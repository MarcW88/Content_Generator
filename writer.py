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

def _build_system(style_context: str, seo_brief: str) -> str:
    return f"""Tu es un rédacteur web expert francophone.
Tu rédiges TOUJOURS en français, en respectant scrupuleusement le Style Profile ci-dessous.
Tu n'ajoutes JAMAIS de contenu générique ni de remplissage.
Chaque phrase doit apporter de la valeur.

{style_context}

{seo_brief}
"""


# ── Passe Briefing ─────────────────────────────────────────────────────────────

BRIEFING_PROMPT = """\
Mot-clé cible : «{keyword}»
Site destinataire : {site_url}
Pays / marché cible : {country}

Ton rôle :
1. Décris brièvement les activités de {site_url} (2-3 phrases) pour cadrer la demande.

2. Les sources SERP locales ({country}) figurent dans les données SEO ci-dessous
   (section « Sources SERP locales »). Utilise ces pages comme références principales
   — elles sont réelles, issues du marché cible, et non générées.
   Si la liste est vide, identifie toi-même 5 sources pertinentes.

3. En t'appuyant sur ces sources, rédige :

   a. Un briefing complet comprenant :
      - Intention de recherche détectée et ses implications éditoriales
      - Angle différenciant pour {site_url} par rapport aux pages qui rankent
      - Points clés incontournables à couvrir
      - Recommandations de tonalité et de style
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
4. CTA final : 1 CTA de fin d'article aligné avec le style du site
5. Vérification GEO : pour chaque H2/H3, vérifie qu'il commence par une phrase-réponse
   directe (« réponse courte d'abord »). Corrige ceux qui ne respectent pas ce principe.

Retourne en JSON strictement valide :
{{
  "meta_title": "...",
  "meta_description": "...",
  "revised_article": "article complet révisé ici",
  "cta_final": "...",
  "geo_check": ["H2 titre — intention couverte : oui/non", ...]
}}
"""


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
