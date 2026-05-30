"""
Central configuration for the content agent.
Dual-source: Streamlit secrets (cloud) → .env (local) → default.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    """Read from Streamlit secrets first, fall back to env var."""
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, default)


# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY    = _get("ANTHROPIC_API_KEY")
FIRECRAWL_API_KEY    = _get("FIRECRAWL_API_KEY")
DATAFORSEO_LOGIN     = _get("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD  = _get("DATAFORSEO_PASSWORD")
GSC_CREDENTIALS_FILE = _get("GSC_CREDENTIALS_FILE", "gsc_credentials.json")
GSC_SITE_URL         = _get("GSC_SITE_URL")

# ── Style profile cache ────────────────────────────────────────────────────────
# One cache file per site — keyed by slugified URL at runtime (see tone_analyzer)
STYLE_PROFILE_CACHE_DIR = "style_profiles"

# ── Claude models ──────────────────────────────────────────────────────────────
CLAUDE_SONNET = "claude-sonnet-4-5"
CLAUDE_OPUS   = "claude-opus-4-5"

# ── Scraping ───────────────────────────────────────────────────────────────────
SCRAPE_PAGES_COUNT = 12
SCRAPE_MAX_CHARS   = 6_000

# ── SEO ────────────────────────────────────────────────────────────────────────
DATAFORSEO_LANGUAGE = "fr"
DATAFORSEO_LOCATION = 2056
SERP_RESULTS_COUNT  = 10
PAA_MAX             = 10

# ── Writing passes ─────────────────────────────────────────────────────────────
TARGET_WORD_COUNT   = 1500
MAX_TOKENS_PER_PASS = 3000

# ── Webhook ────────────────────────────────────────────────────────────────────
WEBHOOK_HOST   = "0.0.0.0"
WEBHOOK_PORT   = int(_get("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = _get("WEBHOOK_SECRET")

# ── Output ─────────────────────────────────────────────────────────────────────
OUTPUT_DIR = _get("OUTPUT_DIR", "outputs")
