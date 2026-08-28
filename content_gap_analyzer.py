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

def _extract_headings(content: str) -> list[str]:
    """Extract H1/H2/H3 headings from markdown content."""
    headings = []
    for line in content.split("\n"):
        m = re.match(r'^(#{1,3})\s+(.+)$', line.strip())
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
        "Une page existante a été analysée ci-dessous.",
        "Le briefing doit COMBLER les lacunes identifiées.",
        "- Renforce les sections présentes mais superficielles.",
        "- Couvre ABSOLUMENT les sujets manquants.",
        "- Ne reproduis pas l'existant : améliore-le et complète-le.",
        "- Le plan de rédaction doit différer de la structure actuelle pour apporter de la valeur ajoutée.",
        "",
        "=== ANALYSE DE LA PAGE EXISTANTE ===",
        f"URL analysée : {analysis.existing_url}",
        f"Volume actuel : ~{analysis.word_count} mots",
    ]

    if analysis.existing_headings:
        lines += ["", "Structure actuelle (H2/H3) :"]
        lines += [f"  - {h}" for h in analysis.existing_headings[:10]]

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
        content = scrape_page(existing_url)
        analysis.existing_content = content
        analysis.word_count       = len(content.split())
        analysis.existing_headings = _extract_headings(content)
        logger.info("[Gap] Scraped %d chars / ~%d words", len(content), analysis.word_count)
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
