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

BRIEFING_PART2_INTENTION = """\
Voici le contexte et le positionnement établis précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, définis l'intention de recherche et les points clés.

À générer (en markdown, section ## Intention & Points Clés) :
1. Intention de recherche détectée et ses implications éditoriales
2. Points clés incontournables à couvrir

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## Intention & Points Clés complète.
"""

BRIEFING_PART2_PLAN = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, rédige un plan de rédaction structuré et complet.

À générer (en markdown, section ## Plan de Rédaction) :
Plan de rédaction structuré H2 / H3 où chaque section précise :
- L'intention de recherche spécifique à laquelle elle répond (ex. : « — intention : comprendre le coût »)
- Le nombre de mots estimé pour cette section (ex. : « — mots estimés : 250-300 »)

Structure flexible :
- Adapte le plan au sujet et à l'intention de recherche
- Inclue les sections pertinentes selon le contexte (pas de structure imposée)
- Le plan doit être complet et couvrir tous les aspects nécessaires
- Pas de CTA final dans le plan
- Le total des mots estimés pour toutes les sections doit se situer entre 1200 et 1800 mots

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides
- Le plan doit être généré en une seule fois (complet)

Retourne UNIQUEMENT la section ## Plan de Rédaction complète.
"""

BRIEFING_PART2_TONALITY = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, définis les recommandations de tonalité et de style.

À générer (en markdown, section ## Tonalité & Style) :
1. Recommandations de tonalité et de style adaptées au type de page
2. Longueur cible : 1 200 à 1 800 mots

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## Tonalité & Style complète.
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

IMPORTANT : Suis TOUTES les consignes du briefing (tonalité, style, positionnement)
mais ton OUTPUT ne doit contenir QUE le contenu de l'article basé sur le plan de rédaction.
N'inclus PAS les sections du briefing (CTA, angle différenciant, etc.) dans ta réponse.

RESPECTE LA LONGUEUR CIBLE spécifiée dans le briefing (généralement 1200-1800 mots pour l'article complet).
Pour cette section, vise STRICTEMENT {word_estimate} mots comme indiqué dans le plan de rédaction.
Ne répète PAS les informations déjà couvertes dans les sections précédentes.
Sois concis et va droit au but sans développements superflus.
Chaque phrase doit apporter de l'information utile, pas de remplissage.

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
- OUTPUT : uniquement le contenu de l'article, pas de sections du briefing

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

    # Part 2a: Intention & Points Clés
    logger.info("[ChunkedBriefing] Part 2a — Intention & Points Clés")
    p2a = BRIEFING_PART2_INTENTION.format(context_summary=summary1)
    part2a, in2a, out2a = _call_claude(system, p2a, max_tokens=2000)
    parts.append(part2a)
    total_in += in2a
    total_out += out2a

    # Summary for next call
    summary2a = _build_context_summary(part1 + "\n\n" + part2a, max_words=120)

    # Part 2b: Plan de Rédaction (single call, 12000 tokens for completeness)
    logger.info("[ChunkedBriefing] Part 2b — Plan de Rédaction")
    p2b = BRIEFING_PART2_PLAN.format(context_summary=summary2a)
    part2b, in2b, out2b = _call_claude(system, p2b, max_tokens=12000)
    parts.append(part2b)
    total_in += in2b
    total_out += out2b

    # Summary for next call
    summary2b = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b, max_words=150)

    # Part 2c: Tonalité & Style
    logger.info("[ChunkedBriefing] Part 2c — Tonalité & Style")
    p2c = BRIEFING_PART2_TONALITY.format(context_summary=summary2b)
    part2c, in2c, out2c = _call_claude(system, p2c, max_tokens=2000)
    parts.append(part2c)
    total_in += in2c
    total_out += out2c

    # Summary for next call
    summary2 = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b + "\n\n" + part2c, max_words=180)

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
    summary3a = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b + "\n\n" + part2c + "\n\n" + part3a, max_words=150)

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
    summary3b = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b + "\n\n" + part2c + "\n\n" + part3a + "\n\n" + part3b, max_words=180)

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


def extract_h2_sections(briefing: str) -> list[tuple[str, str]]:
    """Extract H2 section titles and word estimates from briefing markdown.
    Returns list of tuples (title, word_estimate) where word_estimate is the estimated word count.
    """
    import re
    sections = []
    lines = briefing.split('\n')
    current_h2 = None
    
    for line in lines:
        h2_match = re.match(r'^##\s+(.+)$', line)
        if h2_match:
            current_h2 = h2_match.group(1).strip()
            sections.append((current_h2, ""))  # Default empty word estimate
        elif current_h2 and 'mots estimés' in line.lower():
            # Extract word estimate from line like "— mots estimés : 250-300"
            word_match = re.search(r'(\d+[-–]?\d*)\s*mots?', line, re.IGNORECASE)
            if word_match:
                sections[-1] = (current_h2, word_match.group(1))
    
    # If no word estimates found, return just titles with empty estimates
    if not any(word_est for _, word_est in sections):
        h2_pattern = re.compile(r'^##\s+(.+)$', re.MULTILINE)
        matches = h2_pattern.findall(briefing)
        return [(m.strip(), "") for m in matches if m.strip()]
    
    return sections


def filter_briefing_for_content(briefing: str) -> str:
    """Filter briefing to remove only SEO/technical sections, keep content and style sections for model context."""
    import re
    lines = briefing.split('\n')
    filtered = []
    skip_section = False
    skip_keywords = ['seo', 'maillage', 'métas', 'checklist', 'mots-clés']
    for line in lines:
        header_match = re.match(r'^##\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            header_text = header_match.group(1).lower()
            if any(kw in header_text for kw in skip_keywords):
                skip_section = True
                continue
            else:
                skip_section = False
        if not skip_section:
            filtered.append(line)
    return '\n'.join(filtered)


def generate_article_by_sections(
    briefing: str,
    system: str,
    h2_sections: list[tuple[str, str]] | None = None,
) -> tuple[str, int, int]:
    """
    Generate article section by section to avoid token limits.
    If h2_sections is None, extract them from briefing.
    Uses 5+ calls with continuation logic.
    Returns (full_article, total_input_tokens, total_output_tokens).
    """
    # Filter briefing to remove technical/SEO parts
    filtered_briefing = filter_briefing_for_content(briefing)

    if h2_sections is None:
        h2_sections = extract_h2_sections(filtered_briefing)
        logger.info("[ChunkedArticle] Extracted %d H2 sections from briefing", len(h2_sections))

    if not h2_sections:
        logger.warning("[ChunkedArticle] No H2 sections found, falling back to single call")
        return _call_claude(system, ARTICLE_PROMPT.format(briefing=filtered_briefing, keyword=""), max_tokens=6000)

    # Ensure minimum 5 calls by splitting sections if needed
    min_calls = 5
    if len(h2_sections) < min_calls:
        # Split sections into smaller chunks
        chunks_per_section = (min_calls + len(h2_sections) - 1) // len(h2_sections)
        section_chunks = []
        for h2, word_est in h2_sections:
            for i in range(chunks_per_section):
                section_chunks.append((h2, word_est, i, chunks_per_section))
        logger.info("[ChunkedArticle] Splitting %d sections into %d chunks for minimum 5 calls", len(h2_sections), len(section_chunks))
    else:
        section_chunks = [(h2, word_est, 0, 1) for h2, word_est in h2_sections]

    sections = []
    total_in = 0
    total_out = 0
    continuation = ""
    seen_headers = set()

    for idx, (h2, word_est, chunk_idx, total_chunks) in enumerate(section_chunks, 1):
        logger.info("[ChunkedArticle] Chunk %d/%d — %s (part %d/%d) — words: %s", idx, len(section_chunks), h2, chunk_idx + 1, total_chunks, word_est or "N/A")

        if total_chunks > 1:
            section_spec = f"## {h2}\nRédige la partie {chunk_idx + 1}/{total_chunks} de cette section avec ses sous-parties H3 si nécessaire."
        else:
            section_spec = f"## {h2}\nRédige cette section complète avec ses sous-parties H3 si nécessaire."

        prompt = ARTICLE_SECTION_PROMPT.format(
            briefing=filtered_briefing,
            section_spec=section_spec,
            word_estimate=word_est or "200-300",
        )
        if continuation:
            prompt = f"{continuation}\n\n{prompt}"

        section, in_t, out_t = _call_claude(system, prompt, max_tokens=4000)

        # Post-process: remove duplicate headers
        if idx > 1:
            import re
            lines = section.split('\n')
            filtered_lines = []
            for line in lines:
                header_match = re.match(r'^(#{1,3})\s+(.+)$', line)
                if header_match:
                    header_text = header_match.group(2).strip().lower()
                    if header_text in seen_headers:
                        continue
                    seen_headers.add(header_text)
                filtered_lines.append(line)
            section = '\n'.join(filtered_lines)
        else:
            import re
            for match in re.finditer(r'^(#{1,3})\s+(.+)$', section, re.MULTILINE):
                seen_headers.add(match.group(2).strip().lower())

        sections.append(section)
        total_in += in_t
        total_out += out_t

        # Prepare continuation instruction
        continuation = f"CONTINUE from previous output. DO NOT repeat headers already written. Continue directly with the next content.\n\nPrevious output ended with:\n{section[-500:]}"

    full_article = "\n\n".join(sections)
    logger.info("[ChunkedArticle] Complete — %d chunks, %d total tokens", len(section_chunks), total_in + total_out)
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
    text = message.content[0].text.strip() if message.content else ""
    in_tokens = message.usage.input_tokens if hasattr(message.usage, 'input_tokens') else 0
    out_tokens = message.usage.output_tokens if hasattr(message.usage, 'output_tokens') else 0
    return (text, in_tokens, out_tokens)


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
