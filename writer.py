"""
writer.py
─────────
Moteur de rédaction multi-passes avec Claude Sonnet.

Chaîne de contexte (chaque passe reçoit le résultat de la précédente) :

  Passe 1 → Introduction + angle éditorial
  Passe 2 → Plan H2/H3 enrichi SEO
  Passe 3 → Corps de l'article section par section
  Passe 4 → Méta-title, méta-description, révision finale + CTA

Chaque passe injecte :
  - style_context  (Style Profile JSON → string)
  - seo_brief      (SEO Intelligence → string)
  - previous_output (accumulateur de toutes les passes précédentes)
"""

import logging
from dataclasses import dataclass, field

import anthropic

import config
from cost_tracker import PassCost, RequestCost

logger = logging.getLogger(__name__)


# ── Output data structure ──────────────────────────────────────────────────────

@dataclass
class ArticleOutput:
    keyword: str
    site_url: str         = ""
    introduction: str     = ""
    plan_h2_h3: str       = ""
    body: str             = ""
    meta_title: str       = ""
    meta_description: str = ""
    full_article: str     = ""
    pass_logs: list[str]  = field(default_factory=list)
    cost: RequestCost     = field(default_factory=RequestCost)


# ── Shared prompt builder ──────────────────────────────────────────────────────

_LANG_RULES = {
    "fr": "Tu es un rédacteur web SEO expert. Tu rédiges TOUJOURS en français.",
    "nl": "Je bent een expert SEO-redacteur. Je schrijft ALTIJD in het Nederlands.",
    "en": "You are an expert SEO writer. You ALWAYS write in English.",
}


def _build_system(style_context: str, seo_brief: str, lang: str = "fr") -> str:
    lang_rule = _LANG_RULES.get(lang, _LANG_RULES["fr"])
    return f"""{lang_rule}
Tu n'ajoutes JAMAIS de contenu générique ni de remplissage.
Chaque phrase doit apporter de la valeur factuelle ou pratique.

{style_context}

{seo_brief}
"""


# ── Passe Briefing ─────────────────────────────────────────────────────────────

BRIEFING_PROMPT = """\
Mot-clé cible : «{keyword}»
Type de page : {page_type}
Site destinataire : {site_url}
Pays / marché cible : {country}

--- INFORMATIONS DE CONTEXTE FOURNIES (si disponibles) ---
{context_doc}
---

Ton rôle :
1. Décris brièvement les activités de {site_url} (2-3 phrases) pour cadrer la demande.

2. Analyse des concurrents dans les SERP locales ({country}) :
   Pour chaque source SERP ci-dessous, identifie :
   - Si la page est commerciale (vente, produit, landing) ou informationnelle (guide, blog)
   - L'angle principal de la page
   - Ce qu'elle couvre bien et ce qui manque
   → Conclus par l'angle différenciant pour {site_url} qui n'est PAS couvert par ces concurrents

3. Les sources SERP locales ({country}) figurent dans les données SEO ci-dessous.
   Utilise-les comme références — elles sont réelles, issues du marché cible.
   Si la liste est vide, identifie toi-même 5 sources pertinentes.

4. En t'appuyant sur ces sources et le contexte, rédige :

   a. Un briefing complet pour une page de type « {page_type} » comprenant :
      - Intention de recherche détectée et ses implications éditoriales
      - Angle différenciant (non couvert par les concurrents analysés)
      - Points clés incontournables à couvrir
      - Recommandations de tonalité et de style adaptées au type de page
      - Longueur cible : 1 200 à 1 800 mots

   b. Un plan de rédaction structuré H2 / H3 où chaque section précise
      l'intention de recherche spécifique à laquelle elle répond
      (ex. : « — intention : comprendre le coût »)

Données SEO disponibles :
{seo_brief}

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides, sans forcer le mot-clé exact
- Langue : français, néerlandais ou anglais selon la langue dominante du site
- Liste les sources utilisées à la fin du briefing (usage interne uniquement)
"""


# ── Passe Article ──────────────────────────────────────────────────────────────

ARTICLE_PROMPT = """\
Voici le briefing validé à suivre EXACTEMENT :

---BRIEFING---
{briefing}
---FIN BRIEFING---

Ta mission : rédiger l'article complet sur le mot-clé «{keyword}» en respectant
scrupuleusement ce briefing, le plan H2/H3 inclus.

Avant de commencer, répète les sources de référence mentionnées dans le briefing
(pour confirmer que tu utilises les bonnes), puis rédige l'article.

Tu peux t'inspirer de ces sources mais ne les cite PAS dans l'article final.

Principe GEO / AEO obligatoire :
Chaque section (H2 ou H3) doit répondre de manière explicite et complète à une
intention de recherche précise. Commence chaque section par une phrase-réponse directe
(« la réponse courte d'abord »), puis développe. Cette structure facilite la citation
par les moteurs de réponse (ChatGPT, Perplexity, Google SGE).

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot
- Phrases naturelles et fluides, sans sur-optimisation du mot-clé
- Suis le plan H2/H3 du briefing à la lettre
- Respecte le Style Profile du site pour le ton, le vocabulaire et le point de vue
"""


# ── Passe Méta + Révision ──────────────────────────────────────────────────────

META_PROMPT = """\
Tu reçois l'article complet ci-dessous. Effectue la révision finale.

## Article
{full_article}

Tâches :
1. Méta-title : 50-60 caractères, mot-clé en début, accrocheur, pas de majuscule à chaque mot
2. Méta-description : 140-160 caractères, bénéfice clair, verbe d'action
3. Révision légère : incohérences de ton, répétitions, transitions
4. CTA final : 1 phrase de CTA de fin d'article aligné avec le style du site
5. Vérification GEO : pour chaque H2/H3, vérifie qu'il commence par une phrase-réponse
   directe (« réponse courte d'abord »). Corrige ceux qui ne respectent pas ce principe.

Retourne EXACTEMENT dans ce format (balises incluses, rien d'autre avant ===META_TITLE===) :

===META_TITLE===
<meta title ici>
===META_DESCRIPTION===
<meta description ici>
===GEO_CHECK===
<une ligne par H2/H3 : Titre section — intention couverte : oui/non>
===CTA_FINAL===
<une phrase CTA>
===REVISED_ARTICLE===
<article complet révisé en markdown>
===END===
"""


# ── Chunked Briefing Prompts ───────────────────────────────────────────────────

BRIEFING_PART1_CONTEXT = """\
Mot-clé cible : «{keyword}»
Type de page : {page_type}
Site destinataire : {site_url}
Pays / marché cible : {country}

--- INFORMATIONS DE CONTEXTE FOURNIES (si disponibles) ---
{context_doc}
---

Ton rôle :
1. Décris brièvement les activités de {site_url} (2-3 phrases) pour cadrer la demande.

2. Analyse des concurrents dans les SERP locales ({country}) :
   Pour chaque source SERP ci-dessous, identifie :
   - Si la page est commerciale (vente, produit, landing) ou informationnelle (guide, blog)
   - L'angle principal de la page
   - Ce qu'elle couvre bien et ce qui manque
   → Conclus par l'angle différenciant pour {site_url} qui n'est PAS couvert par ces concurrents

3. Les sources SERP locales ({country}) figurent dans les données SEO ci-dessous.
   Utilise-les comme références — elles sont réelles, issues du marché cible.
   Si la liste est vide, identifie toi-même 5 sources pertinentes.

Données SEO disponibles :
{seo_brief}

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides
- Langue : français, néerlandais ou anglais selon la langue dominante du site

Retourne UNIQUEMENT la partie suivante du briefing (en markdown) :
## Contexte & Positionnement
[Activité du site]
[Analyse concurrentielle]
[Angle différenciant]
"""

BRIEFING_PART2_STRUCTURE = """\
Voici le contexte et le positionnement établis précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, rédige la structure détaillée de la page :

1. Intention de recherche détectée et ses implications éditoriales

2. Points clés incontournables à couvrir

3. Plan de rédaction structuré H2 / H3 où chaque section précise
   l'intention de recherche spécifique à laquelle elle répond
   (ex. : « — intention : comprendre le coût »)

4. Recommandations de tonalité et de style adaptées au type de page

5. Longueur cible : 1 200 à 1 800 mots

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la partie suivante du briefing (en markdown) :
## Structure & Guidelines
[Intention de recherche]
[Points clés]
[Plan H2/H3 avec intentions]
[Tonalité & style]
[Longueur cible]
"""

BRIEFING_PART3_SEO_KEYWORDS = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
Complète le briefing avec les spécifications SEO détaillées (mots-clés et maillage).

Données SEO disponibles :
{seo_brief}

À générer (en markdown, section ## SEO & Technique) :
1. Mots-clés secondaires à intégrer (priorisés par pertinence)
2. Recommandations de maillage interne (si applicable)

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## SEO & Technique (mots-clés et maillage uniquement).
"""

BRIEFING_PART3_SEO_METAS = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
Complète le briefing avec les spécifications SEO détaillées (métas).

Données SEO disponibles :
{seo_brief}

À générer (en markdown, section ## Métas) :
1. Suggestions de méta-title (50-60 caractères)
2. Suggestions de méta-description (140-160 caractères)

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## Métas complète.
"""

BRIEFING_PART3_SEO_FAQ = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
Complète le briefing avec les spécifications SEO détaillées (FAQ et technique).

Données SEO disponibles :
{seo_brief}

À générer (en markdown, section ## FAQ & Technique) :
1. Questions fréquentes (FAQ) à intégrer dans la page
2. Checklist technique (si applicable)

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## FAQ & Technique complète.
"""


# ── Article Section Generation Prompts ───────────────────────────────────────────

ARTICLE_SECTION_PROMPT = """\
Voici le briefing complet à suivre :

---BRIEFING---
{briefing}
---FIN BRIEFING---

Ta mission : rédiger UNIQUEMENT la section demandée ci-dessous, en respectant
scrupuleusement le briefing.

Section à rédiger :
{section_spec}

Principe GEO / AEO obligatoire :
Cette section doit répondre de manière explicite et complète à une
intention de recherche précise. Commence par une phrase-réponse directe
(« la réponse courte d'abord »), puis développe.

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot
- Phrases naturelles et fluides, sans sur-optimisation du mot-clé
- Respecte le Style Profile du site pour le ton, le vocabulaire et le point de vue
- Ne rédige PAS les autres sections de la page

Retourne UNIQUEMENT le contenu de cette section en markdown.
"""


# ── Helper Functions ─────────────────────────────────────────────────────────────

def _build_context_summary(text: str, max_words: int = 150) -> str:
    """Build a concise summary of context text for token efficiency."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def generate_chunked_briefing(
    keyword: str,
    site_url: str,
    country: str,
    page_type: str,
    context_doc: str,
    seo_brief: str,
    system: str,
    lang: str = "fr",
) -> tuple[str, int, int]:
    """
    Generate briefing in 3 chunked calls to avoid token limits.
    Returns (full_briefing, total_input_tokens, total_output_tokens).
    """
    parts = []
    total_in, total_out = 0, 0

    # Part 1: Context & Positionnement
    logger.info("[ChunkedBriefing] Part 1 — Context & Positionnement")
    p1 = BRIEFING_PART1_CONTEXT.format(
        keyword=keyword,
        site_url=site_url,
        country=country,
        page_type=page_type,
        context_doc=context_doc or "(aucun document fourni)",
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part1, in1, out1 = _call_claude(system, p1, max_tokens=2000)
    parts.append(part1)
    total_in += in1
    total_out += out1

    # Summary for next calls
    summary1 = _build_context_summary(part1, max_words=100)

    # Part 2: Structure & Guidelines
    logger.info("[ChunkedBriefing] Part 2 — Structure & Guidelines")
    p2 = BRIEFING_PART2_STRUCTURE.format(context_summary=summary1)
    part2, in2, out2 = _call_claude(system, p2, max_tokens=2000)
    parts.append(part2)
    total_in += in2
    total_out += out2

    # Summary for next call
    summary2 = _build_context_summary(part1 + "\n\n" + part2, max_words=120)

    # Part 3a: SEO Keywords & Maillage
    logger.info("[ChunkedBriefing] Part 3a — SEO Keywords & Maillage")
    p3a = BRIEFING_PART3_SEO_KEYWORDS.format(
        context_summary=summary2,
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part3a, in3a, out3a = _call_claude(system, p3a, max_tokens=2000)
    parts.append(part3a)
    total_in += in3a
    total_out += out3a

    # Summary for next call
    summary3a = _build_context_summary(part1 + "\n\n" + part2 + "\n\n" + part3a, max_words=150)

    # Part 3b: SEO Metas
    logger.info("[ChunkedBriefing] Part 3b — SEO Metas")
    p3b = BRIEFING_PART3_SEO_METAS.format(
        context_summary=summary3a,
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part3b, in3b, out3b = _call_claude(system, p3b, max_tokens=2000)
    parts.append(part3b)
    total_in += in3b
    total_out += out3b

    # Summary for next call
    summary3b = _build_context_summary(part1 + "\n\n" + part2 + "\n\n" + part3a + "\n\n" + part3b, max_words=180)

    # Part 3c: FAQ & Technique
    logger.info("[ChunkedBriefing] Part 3c — FAQ & Technique")
    p3c = BRIEFING_PART3_SEO_FAQ.format(
        context_summary=summary3b,
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part3c, in3c, out3c = _call_claude(system, p3c, max_tokens=2000)
    parts.append(part3c)
    total_in += in3c
    total_out += out3c

    full_briefing = "\n\n".join(parts)
    logger.info("[ChunkedBriefing] Complete — %d total tokens", total_in + total_out)
    return full_briefing, total_in, total_out


def extract_h2_sections(briefing: str) -> list[str]:
    """Extract H2 section titles from briefing markdown."""
    import re
    h2_pattern = re.compile(r'^##\s+(.+)$', re.MULTILINE)
    matches = h2_pattern.findall(briefing)
    return [m.strip() for m in matches if m.strip()]


def generate_article_by_sections(
    briefing: str,
    system: str,
    h2_sections: list[str] | None = None,
) -> tuple[str, int, int]:
    """
    Generate article section by section to avoid token limits.
    If h2_sections is None, extract them from briefing.
    Returns (full_article, total_input_tokens, total_output_tokens).
    """
    if h2_sections is None:
        h2_sections = extract_h2_sections(briefing)
        logger.info("[ChunkedArticle] Extracted %d H2 sections from briefing", len(h2_sections))

    if not h2_sections:
        logger.warning("[ChunkedArticle] No H2 sections found, falling back to single call")
        return _call_claude(system, ARTICLE_PROMPT.format(briefing=briefing, keyword=""), max_tokens=6000)

    sections = []
    total_in, total_out = 0, 0

    for i, h2 in enumerate(h2_sections, 1):
        logger.info("[ChunkedArticle] Section %d/%d — %s", i, len(h2_sections), h2)
        section_spec = f"## {h2}\nRédige cette section complète avec ses sous-parties H3 si nécessaire."
        prompt = ARTICLE_SECTION_PROMPT.format(
            briefing=briefing,
            section_spec=section_spec,
        )
        section, in_t, out_t = _call_claude(system, prompt, max_tokens=2000)
        sections.append(section)
        total_in += in_t
        total_out += out_t

    full_article = "\n\n".join(sections)
    logger.info("[ChunkedArticle] Complete — %d sections, %d total tokens", len(h2_sections), total_in + total_out)
    return full_article, total_in, total_out


# ── Claude caller ──────────────────────────────────────────────────────────────

def _call_claude(system: str, user_prompt: str,
                 max_tokens: int | None = None) -> tuple[str, int, int]:
    """Single Claude Sonnet call. Returns (text, input_tokens, output_tokens)."""
    client  = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model      = config.CLAUDE_SONNET,
        max_tokens = max_tokens or config.MAX_TOKENS_PER_PASS,
        system     = system,
        messages   = [{"role": "user", "content": user_prompt}],
    )
    return (
        message.content[0].text.strip(),
        message.usage.input_tokens,
        message.usage.output_tokens,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def run_writing_pipeline(
    keyword: str,
    style_context: str,
    seo_brief: str,
) -> ArticleOutput:
    """
    Legacy 4-pass pipeline (kept for reference).
    Returns a populated ArticleOutput.
    """
    output = ArticleOutput(keyword=keyword)
    system = _build_system(style_context, seo_brief)

    # ── Pass 1 : Introduction ─────────────────────────────────────────────────
    logger.info("[Writer] Passe 1 — Introduction")
    p1_prompt = PASS1_PROMPT.format(keyword=keyword)
    output.introduction, in1, out1 = _call_claude(system, p1_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in1, out1))
    output.pass_logs.append(f"PASS1 OK — {len(output.introduction.split())} mots")

    # ── Pass 2 : Plan H2/H3 ──────────────────────────────────────────────────
    logger.info("[Writer] Passe 2 — Plan H2/H3")
    p2_prompt = PASS2_PROMPT.format(pass1_output=output.introduction)
    output.plan_h2_h3, in2, out2 = _call_claude(system, p2_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in2, out2))
    output.pass_logs.append(f"PASS2 OK — {output.plan_h2_h3.count('##')} sections")

    # ── Pass 3 : Corps ────────────────────────────────────────────────────────
    logger.info("[Writer] Passe 3 — Corps de l'article")
    p3_prompt = PASS3_PROMPT.format(
        pass1_output     = output.introduction,
        pass2_output     = output.plan_h2_h3,
        target_word_count= config.TARGET_WORD_COUNT,
    )
    output.body, in3, out3 = _call_claude(system, p3_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in3, out3))
    output.pass_logs.append(f"PASS3 OK — {len(output.body.split())} mots")

    # ── Pass 4 : Méta + Révision ──────────────────────────────────────────────
    logger.info("[Writer] Passe 4 — Méta + révision finale")
    full_draft = f"{output.introduction}\n\n{output.body}"
    p4_prompt  = PASS4_PROMPT.format(full_draft=full_draft)
    raw_p4, in4, out4 = _call_claude(system, p4_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in4, out4))

    # Parse JSON response from pass 4
    import json
    raw_p4_clean = raw_p4.strip()
    if raw_p4_clean.startswith("```"):
        raw_p4_clean = raw_p4_clean.split("```")[1]
        if raw_p4_clean.startswith("json"):
            raw_p4_clean = raw_p4_clean[4:]

    try:
        p4_data = json.loads(raw_p4_clean)
        output.meta_title       = p4_data.get("meta_title", "")
        output.meta_description = p4_data.get("meta_description", "")
        revised                 = p4_data.get("revised_article", full_draft)
        cta                     = p4_data.get("cta_final", "")
        output.full_article     = f"{revised}\n\n{cta}".strip()
    except json.JSONDecodeError:
        logger.warning("Pass 4 JSON parse failed — using raw output as full article")
        output.full_article = full_draft

    output.pass_logs.append(
        f"PASS4 OK — méta title: {len(output.meta_title)} chars, "
        f"méta desc: {len(output.meta_description)} chars"
    )

    logger.info("[Writer] Pipeline terminé — %d mots", len(output.full_article.split()))
    return output


def format_final_output(article: ArticleOutput) -> str:
    """Human-readable final deliverable."""
    separator = "─" * 60
    return f"""
{separator}
META TITLE       : {article.meta_title}
META DESCRIPTION : {article.meta_description}
{separator}
PLAN H2/H3
{separator}
{article.plan_h2_h3}

{separator}
ARTICLE COMPLET
{separator}
{article.full_article}

{separator}
LOGS DE PRODUCTION
{separator}
""" + "\n".join(f"  {log}" for log in article.pass_logs)
