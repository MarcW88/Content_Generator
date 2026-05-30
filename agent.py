"""
agent.py
────────
Cerveau central du content agent.

Usage CLI :
    python agent.py --keyword "rénovation cuisine Bruxelles"
    python agent.py --keyword "parquet chêne massif" --refresh-style

Flux d'exécution :
    1. Build / load Style Profile (tone_analyzer)
    2. Gather SEO Intelligence (seo_intelligence)
    3. Run 4-pass writing pipeline (writer)
    4. Save output to outputs/<slug>.md + outputs/<slug>.json
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime

import config
from tone_analyzer    import build_style_profile, style_profile_to_system_context
from seo_intelligence import gather_seo_intelligence, seo_intel_to_brief
from writer           import run_writing_pipeline, format_final_output

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt= "%H:%M:%S",
)
logger = logging.getLogger("agent")


# ── Utilities ─────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[àáâãäå]", "a", text)
    text = re.sub(r"[èéêë]",   "e", text)
    text = re.sub(r"[ìíîï]",   "i", text)
    text = re.sub(r"[òóôõö]",  "o", text)
    text = re.sub(r"[ùúûü]",   "u", text)
    text = re.sub(r"[ç]",      "c", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _ensure_output_dir() -> str:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    return config.OUTPUT_DIR


def _save_outputs(keyword: str, article, intel, style_profile: dict) -> dict[str, str]:
    """Persist markdown + JSON artefacts. Returns dict of saved paths."""
    out_dir  = _ensure_output_dir()
    slug     = _slugify(keyword)
    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    base     = os.path.join(out_dir, f"{slug}_{ts}")

    # Markdown article
    md_path  = f"{base}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(format_final_output(article))

    # JSON bundle (for Make / n8n consumption)
    json_path = f"{base}.json"
    bundle    = {
        "keyword":          article.keyword,
        "meta_title":       article.meta_title,
        "meta_description": article.meta_description,
        "plan":             article.plan_h2_h3,
        "full_article":     article.full_article,
        "word_count":       len(article.full_article.split()),
        "pass_logs":        article.pass_logs,
        "generated_at":     datetime.now().isoformat(),
        "seo": {
            "secondary_keywords": intel.keyword_cluster.secondary,
            "paa":                intel.paa_questions,
            "cannibalisations":   [p.url for p in intel.cannibalisation_risk],
        },
        "style_profile": style_profile,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    return {"markdown": md_path, "json": json_path}


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run(keyword: str, refresh_style: bool = False) -> dict:
    """
    Full pipeline. Returns the JSON bundle dict.
    Can be called programmatically (from webhook.py or tests).
    """
    logger.info("═" * 60)
    logger.info("Starting content agent for keyword: %s", keyword)
    logger.info("═" * 60)

    # Step 1 — Style Profile
    logger.info("Step 1/3 — Building style profile …")
    style_profile   = build_style_profile(force_refresh=refresh_style)
    style_context   = style_profile_to_system_context(style_profile)
    logger.info("Style profile ready: %d keys", len(style_profile))

    # Step 2 — SEO Intelligence
    logger.info("Step 2/3 — Gathering SEO intelligence …")
    intel           = gather_seo_intelligence(keyword)
    seo_brief       = seo_intel_to_brief(intel)
    logger.info(
        "SEO intel ready: %d PAA, %d H2s, %d cannibalisation risks",
        len(intel.paa_questions),
        len(intel.recommended_h2),
        len(intel.cannibalisation_risk),
    )

    # Step 3 — Writing
    logger.info("Step 3/3 — Running 4-pass writing pipeline …")
    article         = run_writing_pipeline(keyword, style_context, seo_brief)

    # Save
    paths           = _save_outputs(keyword, article, intel, style_profile)
    logger.info("Outputs saved → %s", paths)
    logger.info("═" * 60)
    logger.info("Done. Word count: %d", len(article.full_article.split()))
    logger.info("═" * 60)

    # Return the JSON bundle for programmatic consumption
    with open(paths["json"], encoding="utf-8") as f:
        return json.load(f)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Content agent — génère un article SEO optimisé dans le style du site cible."
    )
    parser.add_argument(
        "--keyword",
        required=True,
        help='Mot-clé principal, ex: "rénovation cuisine Bruxelles"',
    )
    parser.add_argument(
        "--refresh-style",
        action="store_true",
        default=False,
        help="Force le re-scraping du site et la reconstruction du style profile",
    )
    args = parser.parse_args()

    # Basic validation
    missing = []
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not config.DATAFORSEO_LOGIN or not config.DATAFORSEO_PASSWORD:
        missing.append("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD")
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        logger.error("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    result = run(args.keyword, refresh_style=args.refresh_style)
    print(f"\nArticle généré avec succès ({result['word_count']} mots)")
    print(f"Meta title : {result['meta_title']}")
    print(f"Fichiers   : outputs/{_slugify(args.keyword)}_*.md / .json")


if __name__ == "__main__":
    main()
