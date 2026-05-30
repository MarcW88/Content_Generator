"""
Central configuration for the content agent.
All tunable parameters live here — never hardcode in modules.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
FIRECRAWL_API_KEY   = os.getenv("FIRECRAWL_API_KEY", "")
DATAFORSEO_LOGIN    = os.getenv("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD", "")
GSC_CREDENTIALS_FILE = os.getenv("GSC_CREDENTIALS_FILE", "gsc_credentials.json")
GSC_SITE_URL        = os.getenv("GSC_SITE_URL", "")   # e.g. "https://www.dedecker.be/"

# ── Target site ───────────────────────────────────────────────────────────────
TARGET_SITE_URL     = os.getenv("TARGET_SITE_URL", "https://www.dedecker.be")
STYLE_PROFILE_CACHE = os.getenv("STYLE_PROFILE_CACHE", "style_profile.json")

# ── Claude models ─────────────────────────────────────────────────────────────
CLAUDE_SONNET = "claude-sonnet-4-5"   # passes 1-4 : rédaction
CLAUDE_OPUS   = "claude-opus-4-5"     # tone analyzer : analyse nuancée

# ── Scraping ──────────────────────────────────────────────────────────────────
SCRAPE_PAGES_COUNT  = 8       # nb de pages à scraper pour le style profile
SCRAPE_MAX_CHARS    = 6_000   # chars max par page envoyés à Claude

# ── SEO ───────────────────────────────────────────────────────────────────────
DATAFORSEO_LANGUAGE = "fr"
DATAFORSEO_LOCATION = 2056    # Belgique
SERP_RESULTS_COUNT  = 10
PAA_MAX             = 10      # "People Also Ask" max items

# ── Writing passes ────────────────────────────────────────────────────────────
TARGET_WORD_COUNT   = 1500    # objectif mots pour l'article complet
MAX_TOKENS_PER_PASS = 3000

# ── Webhook server ────────────────────────────────────────────────────────────
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", 8080))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")   # optionnel — vérifie le header X-Secret

# ── Output ────────────────────────────────────────────────────────────────────
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")
