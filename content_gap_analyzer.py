"""
content_gap_analyzer.py
────────────────────────
Scrapes an existing page and identifies content gaps vs. SERP competitors.

Used in "content refresh" mode:
  1. Scrape the existing page (Firecrawl → BS4 fallback)
  2. Compare against SERP top-10 + PAA questions via Claude Opus
  3. Return a ContentGapAnalysis with a formatted gap_brief ready for
     injection as context_doc into generate_chunked_briefing()

Output — ContentGapAnalysis
────────────────────────────
{
  "existing_url":      "https://...",
  "word_count":        843,
  "existing_headings": ["H2 title 1", "H2 title 2", ...],
  "covered_topics":    ["sujets déjà bien couverts"],
  "missing_topics":    ["sujets absents vs concurrents SERP"],
  "weak_sections":     ["sections présentes mais superficielles"],
  "unanswered_paa":    ["questions PAA sans réponse dans la page"],
  "gap_summary":       "résumé des principaux gaps",
  "gap_brief":         "texte formaté prêt pour context_doc"
}
"""

import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

import config
from tone_analyzer import scrape_page

logger = logging.getLogger(__name__)


# ── Data structure ─────────────────────────────────────────────────────────────

@dataclass
class ContentGapAnalysis:
    existing_url: str
    existing_content: str        = ""
    word_count: int              = 0
    existing_headings: list[str] = field(default_factory=list)
    covered_topics: list[str]    = field(default_factory=list)
    missing_topics: list[str]    = field(default_factory=list)
    weak_sections: list[str]     = field(default_factory=list)
    unanswered_paa: list[str]    = field(default_factory=list)
    gap_summary: str             = ""
    gap_brief: str               = ""


# ── Claude prompt ──────────────────────────────────────────────────────────────

_GAP_SYSTEM = """\
Tu es un expert en audit de contenu SEO.
Tu reçois :
1. Le contenu d'une page existante
2. Les 10 premiers résultats SERP pour le même mot-clé
3. Les questions PAA (People Also Ask)

Ta mission : identifier les content gaps — ce qui manque dans la page existante
par rapport aux standards du marché.

Réponds UNIQUEMENT avec un JSON valide, sans markdown, sans commentaires.
Schéma attendu :
{
  "covered_topics":    ["sujets bien couverts dans la page actuelle"],
  "missing_topics":    ["sujets présents chez les concurrents mais absents de la page"],
  "weak_sections":     ["sections présentes mais superficielles, manquant de profondeur"],
  "unanswered_paa":    ["questions PAA non répondues ou mal répondues"],
  "existing_headings": ["liste des H2/H3 extraits du contenu existant"],
  "gap_summary":       "résumé en 2-3 phrases des principaux gaps à combler"
}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

# Patterns that mark the end of real article content in scraped pages
_FOOTER_PATTERNS = re.compile(
    r'^(Related Articles?|Articles (similaires|connexes)|Lire aussi|'
    r'Vous (pourriez|aimerez) aussi|Articles liés|'
    r'Partager (cet|l.article)|Share this|'
    r'\\\s*\\|Tags\s*:|Catégorie|Filed under)',
    re.IGNORECASE,
)
# Patterns that mark nav/UI noise lines to skip
_NAV_LINE = re.compile(
    r'^(NL|EN|FR|DE|IT|ES|Se connecter|Login|Sign in|'
    r'Mon compte|Panier|Cart|Menu|Navigation|Toggle|'
    r'Table of contents|Table des matières|'
    r'Share this article|Partager cet article|'
    r'\d{1,2}\s+(janvier|février|mars|avril|mai|juin|'
    r'juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}|'
    r'\d+\s+minutes?\s*(de lecture)?$)',
    re.IGNORECASE,
)


def _clean_article_body(content: str) -> str:
    """
    Strip navigation, breadcrumbs, banners, and footer noise from a scraped page.
    Returns just the article body: intro paragraph(s) + H2 sections.
    """
    lines = content.split("\n")
    clean: list[str] = []
    article_started = False

    for line in lines:
        stripped = line.strip()

        # Stop at footer markers
        if _FOOTER_PATTERNS.match(stripped):
            break
        # Stop at Firecrawl artifact lines (e.g. "\ \  \ ")
        if re.match(r'^[\\\s]{0,6}$', stripped) and len(stripped) > 1:
            if not article_started:
                continue
            break

        # H2/H3 headings always belong to article body
        if re.match(r'^#{1,3}\s+', stripped):
            article_started = True
            clean.append(line)
            continue

        # Skip nav-like lines before article starts
        if not article_started:
            # Breadcrumb (contains " > ")
            if ' > ' in stripped and len(stripped) < 120:
                continue
            # Short lines without sentence punctuation = nav items
            if len(stripped) < 55 and not any(c in stripped for c in '.!?,;:'):
                continue
            # Known nav patterns
            if _NAV_LINE.match(stripped):
                continue
            # First real sentence found → article has started
            if len(stripped) > 60 and any(c in stripped for c in '.!?,;'):
                article_started = True

        if article_started:
            clean.append(line)

    return "\n".join(clean).strip()


def _extract_headings(content: str) -> list[str]:
    """Extract H2/H3 headings from markdown content (skip H1)."""
    headings = []
    for line in content.split("\n"):
        m = re.match(r'^(#{2,3})\s+(.+)$', line.strip())
        if m:
            headings.append(m.group(2).strip())
    return headings


# ── Core functions ─────────────────────────────────────────────────────────────

def _analyze_gaps_with_claude(
    existing_content: str,
    serp_top10: list,
    paa_questions: list[str],
    keyword: str,
) -> dict:
    """Send existing page + SERP data to Claude Opus for gap analysis."""
    serp_summary = "\n".join(
        f"{i}. {r.title}\n   URL: {r.url}"
        + (f"\n   {r.description[:120]}" if getattr(r, "description", "") else "")
        for i, r in enumerate(serp_top10[:8], 1)
    )
    paa_summary = "\n".join(f"- {q}" for q in paa_questions[:8]) or "(aucune question PAA disponible)"

    user_msg = (
        f"Mot-clé cible : «{keyword}»\n\n"
        f"=== CONTENU EXISTANT DE LA PAGE ===\n{existing_content[:8000]}\n\n"
        f"=== TOP SERP (concurrents) ===\n{serp_summary}\n\n"
        f"=== QUESTIONS PAA ===\n{paa_summary}\n\n"
        f"Analyse les gaps et retourne le JSON demandé."
    )

    client  = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model      = config.CLAUDE_OPUS,
        max_tokens = 1500,
        system     = _GAP_SYSTEM,
        messages   = [{"role": "user", "content": user_msg}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def _build_gap_brief(analysis: "ContentGapAnalysis") -> str:
    """Format the gap analysis as a context_doc string for briefing injection."""
    lines = [
        "=== INSTRUCTION MODE CONTENT GAP ===",
        "Une page existante est en cours d'optimisation. Elle se positionne déjà en SEO.",
        "OBJECTIF : enrichir cette page, pas la remplacer.",
        "",
        "RÈGLES ABSOLUES pour le plan de rédaction :",
        "- CONSERVE les titres H2 existants EXACTEMENT tels quels dans le plan.",
        "- Insère les nouvelles sections manquantes AUX BONS ENDROITS dans le plan (pas à la fin).",
        "- Pour les sections existantes faibles, enrichis leur contenu (ajoute des sous-sections H3 si nécessaire).",
        "- Couvre ABSOLUMENT les sujets manquants identifiés.",
        "- Le texte existant de chaque section sera conservé verbatim — le plan doit donc garder les titres exacts.",
        "",
        "=== ANALYSE DE LA PAGE EXISTANTE ===",
        f"URL analysée : {analysis.existing_url}",
        f"Volume actuel : ~{analysis.word_count} mots",
    ]

    if analysis.existing_headings:
        lines += ["", "Titres H2/H3 ACTUELS (à reprendre EXACTEMENT dans le plan) :"]
        lines += [f"  - {h}" for h in analysis.existing_headings[:10]]
        lines += ["", "⚠️  Ces titres doivent apparaître TELS QUELS dans le plan de rédaction."]

    lines += ["", f"Résumé des gaps : {analysis.gap_summary or '—'}"]

    if analysis.covered_topics:
        lines += ["", "Sujets déjà couverts (ne pas répéter) :"]
        lines += [f"  - {t}" for t in analysis.covered_topics[:8]]

    if analysis.missing_topics:
        lines += ["", "Sujets MANQUANTS (à couvrir obligatoirement) :"]
        lines += [f"  - {t}" for t in analysis.missing_topics]

    if analysis.weak_sections:
        lines += ["", "Sections faibles (à renforcer) :"]
        lines += [f"  - {t}" for t in analysis.weak_sections]

    if analysis.unanswered_paa:
        lines += ["", "Questions PAA non répondues :"]
        lines += [f"  - {q}" for q in analysis.unanswered_paa]

    return "\n".join(lines)


# ── Merge Plan ─────────────────────────────────────────────────────────────────

_MERGE_PLAN_SYSTEM = """\
Tu es un architecte de contenu SEO expert en optimisation d'articles existants.
Ta mission : produire un plan de fusion précis entre le contenu existant et les nouveaux sujets à couvrir.
Réponds UNIQUEMENT avec un tableau JSON valide, sans markdown, sans commentaires, sans explication.
"""

_MERGE_PLAN_PROMPT = """\
Tu dois décider comment fusionner un article existant avec de nouveaux sujets identifiés par l'analyse SEO.

## SECTIONS DE L'ARTICLE EXISTANT
{existing_sections_summary}

## ANALYSE DES GAPS SEO
- Résumé : {gap_summary}
- Sujets manquants : {missing_topics}
- Sections faibles : {weak_sections}
- Questions PAA sans réponse : {unanswered_paa}

## PLAN DE RÉDACTION CIBLE (briefing)
{briefing_plan}

## RÈGLES DE FUSION
Produis une liste ordonnée de sections représentant l'article FINAL optimal.
Pour chaque section, attribue une action :
  - KEEP    : section existante bien couverte, garder verbatim
  - EXPAND  : section existante faible, garder le texte + ajouter des points manquants
  - REWRITE : section existante à réécrire en intégrant nouveaux éléments
  - INSERT  : nouvelle section absente de l'article actuel
  - MOVE    : section existante à déplacer à cette position

RÈGLES ABSOLUES :
1. L'ordre des sections doit être logique (général → particulier → complémentaire).
2. Les sections primaires (apprentissages de base, méthode) ne doivent PAS être interrompues par des sujets secondaires (cours, socialisation avancée, etc.).
3. Toutes les sections existantes doivent apparaître dans le plan (action KEEP/EXPAND/REWRITE/MOVE).
4. Les nouvelles sections reçoivent INSERT, placées à la position logique dans le parcours lecteur.
5. Si deux sections couvrent le même sujet, utilise MERGE (indique existing_heading des deux).

Format JSON requis (tableau d'objets) :
[
  {{
    "position": 1,
    "heading": "Titre H2 exact de la section dans l'article final",
    "action": "KEEP|EXPAND|REWRITE|INSERT|MOVE",
    "existing_heading": "Titre H2 exact dans l'article actuel, ou null si nouvelle section",
    "missing_points": ["point manquant à ajouter", "autre point"],
    "word_target": "150-200"
  }}
]
"""


def generate_merge_plan(
    existing_content: str,
    analysis: "ContentGapAnalysis",
    briefing_plan_text: str,
) -> list[dict]:
    """
    Generate a structured merge plan deciding what to do with each section.

    Returns an ordered list of section dicts with action, existing_heading,
    missing_points and word_target. Returns [] if Claude is unavailable.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.warning("[MergePlan] ANTHROPIC_API_KEY missing — skipping")
        return []

    # Build existing sections summary
    from writer import _split_content_by_h2  # local import to avoid circular at module level
    sections = _split_content_by_h2(existing_content)

    existing_summary_lines = []
    for title, text in sections.items():
        if title == "_intro":
            continue
        word_count = len(text.split())
        preview = text.replace("\n", " ")[:120].strip()
        existing_summary_lines.append(
            f"  [{word_count} mots] ## {title}\n    → {preview}..."
        )
    existing_summary = "\n".join(existing_summary_lines) or "Aucune section H2 détectée."

    safe_briefing = (briefing_plan_text[:3000] if briefing_plan_text else "—").replace("{", "{{").replace("}", "}}")
    prompt = _MERGE_PLAN_PROMPT.format(
        existing_sections_summary = existing_summary,
        gap_summary    = analysis.gap_summary or "—",
        missing_topics = ", ".join(analysis.missing_topics[:10]) or "—",
        weak_sections  = ", ".join(analysis.weak_sections[:8]) or "—",
        unanswered_paa = ", ".join(analysis.unanswered_paa[:6]) or "—",
        briefing_plan  = safe_briefing,
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model      = config.CLAUDE_SONNET,
            max_tokens = 2000,
            system     = _MERGE_PLAN_SYSTEM,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip optional markdown fences
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        plan: list[dict] = json.loads(raw)
        logger.info("[MergePlan] Generated %d sections", len(plan))
        return plan
    except Exception as exc:
        logger.error("[MergePlan] Failed: %s", exc, exc_info=True)
        return []


# ── Public API ─────────────────────────────────────────────────────────────────

def build_content_gap_analysis(
    existing_url: str,
    serp_top10: list,
    paa_questions: list[str],
    keyword: str,
) -> ContentGapAnalysis:
    """
    Main entry point for content refresh mode.
    Scrapes existing_url, compares against SERP + PAA via Claude Opus,
    and returns a ContentGapAnalysis with a formatted gap_brief.
    """
    analysis = ContentGapAnalysis(existing_url=existing_url)

    # Step 1 — Scrape existing page
    logger.info("[Gap] Scraping existing page: %s", existing_url)
    try:
        raw_content = scrape_page(existing_url)
        content = _clean_article_body(raw_content)
        if not content:
            logger.warning("[Gap] Clean body empty — falling back to raw content")
            content = raw_content
        analysis.existing_content = content
        analysis.word_count       = len(content.split())
        analysis.existing_headings = _extract_headings(content)
        logger.info("[Gap] Scraped %d chars / ~%d words (cleaned from %d raw chars)",
                    len(content), analysis.word_count, len(raw_content))
    except Exception as exc:
        logger.error("[Gap] Failed to scrape %s: %s", existing_url, exc)
        return analysis

    if not config.ANTHROPIC_API_KEY:
        logger.warning("[Gap] ANTHROPIC_API_KEY missing — skipping Claude gap analysis")
        analysis.gap_brief = _build_gap_brief(analysis)
        return analysis

    if not serp_top10 and not paa_questions:
        logger.warning("[Gap] No SERP data available — gap analysis will be limited")

    # Step 2 — Analyze gaps via Claude Opus
    try:
        gaps = _analyze_gaps_with_claude(
            existing_content = analysis.existing_content,
            serp_top10       = serp_top10,
            paa_questions    = paa_questions,
            keyword          = keyword,
        )
        analysis.covered_topics = gaps.get("covered_topics", [])
        analysis.missing_topics = gaps.get("missing_topics", [])
        analysis.weak_sections  = gaps.get("weak_sections",  [])
        analysis.unanswered_paa = gaps.get("unanswered_paa", [])
        analysis.gap_summary    = gaps.get("gap_summary",    "")
        # Prefer Claude-extracted headings if scraper found none
        if not analysis.existing_headings:
            analysis.existing_headings = gaps.get("existing_headings", [])
        logger.info(
            "[Gap] Analysis complete — %d missing topics, %d weak sections",
            len(analysis.missing_topics), len(analysis.weak_sections),
        )
    except Exception as exc:
        logger.error("[Gap] Claude gap analysis failed: %s", exc)

    # Step 3 — Build formatted brief
    analysis.gap_brief = _build_gap_brief(analysis)
    return analysis
