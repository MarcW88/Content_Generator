"""
tone_analyzer.py
────────────────
1. Scrape N pages from the target site with Firecrawl (fallback: BeautifulSoup)
2. Send the corpus to Claude Opus to extract a structured Style Profile JSON
3. Cache the result locally so subsequent runs skip the scraping step

Style Profile schema
──────────────────────────────────────────────────────────────
{
  "tonality": ["expert", "accessible", "direct"],
  "avg_sentence_length": "medium",          // short / medium / long
  "avg_paragraph_length": "3-4 sentences",
  "preferred_vocabulary": ["concret", "chiffres", "bénéfice client", ...],
  "avoided_vocabulary":   ["jargon technique abstrait", "superlatifs vides", ...],
  "recurring_patterns":   ["listes à puces", "questions rhétoriques", "CTA en fin de section"],
  "structural_patterns":  ["H2 = bénéfice, H3 = comment", "intro avec question", ...],
  "pov":                  "nous (marque)",   // ou "vous (lecteur)"
  "cta_style":            "soft — invitation plutôt qu'injonction",
  "forbidden":            ["termes concurrents", "promesses non vérifiables"]
}
"""

import json
import os
import time
import logging
from typing import Optional

import anthropic
import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


def _cache_path(site_url: str) -> str:
    """One cache file per site URL."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", site_url.lower().rstrip("/").replace("https://", "").replace("http://", ""))
    os.makedirs(config.STYLE_PROFILE_CACHE_DIR, exist_ok=True)
    return os.path.join(config.STYLE_PROFILE_CACHE_DIR, f"{slug}.json")


# ── Scrapers ──────────────────────────────────────────────────────────────────

def _scrape_with_firecrawl(url: str) -> str:
    """Returns clean markdown text for a given URL via Firecrawl."""
    endpoint = "https://api.firecrawl.dev/v1/scrape"
    headers  = {
        "Authorization": f"Bearer {config.FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload  = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("markdown", "")


def _scrape_with_bs4(url: str) -> str:
    """Fallback scraper using requests + BeautifulSoup."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ContentAgent/1.0)"}
    resp    = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup    = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def scrape_page(url: str) -> str:
    """Scrape one page — Firecrawl first, BS4 as fallback."""
    if config.FIRECRAWL_API_KEY:
        try:
            text = _scrape_with_firecrawl(url)
            if text.strip():
                return text[: config.SCRAPE_MAX_CHARS]
        except Exception as exc:
            logger.warning("Firecrawl failed for %s: %s — falling back to BS4", url, exc)
    return _scrape_with_bs4(url)[: config.SCRAPE_MAX_CHARS]


def _discover_page_urls(site_url: str, count: int) -> list[str]:
    """
    Quick sitemap-based discovery. Falls back to crawling the homepage.
    Always includes site_url itself as a last resort.
    """
    urls: list[str] = []
    base = site_url.rstrip("/")

    # 1. Try sitemap
    sitemap_candidates = [
        base + "/sitemap.xml",
        base + "/sitemap_index.xml",
    ]
    for sitemap_url in sitemap_candidates:
        try:
            resp = requests.get(sitemap_url, timeout=15,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.ok and "<loc>" in resp.text:
                # Use html.parser as xml fallback in case lxml-xml is unavailable
                try:
                    soup = BeautifulSoup(resp.text, "lxml-xml")
                except Exception:
                    soup = BeautifulSoup(resp.text, "html.parser")
                locs = [t.text.strip() for t in soup.find_all("loc")]
                preferred = [u for u in locs
                             if any(k in u for k in ["/blog", "/article", "/guide", "/conseil", "/actualite"])]
                urls = preferred if preferred else locs
                logger.info("Sitemap OK — %d URLs found", len(urls))
                break
        except Exception as exc:
            logger.warning("Sitemap %s failed: %s", sitemap_url, exc)

    # 2. Fallback: parse homepage links
    if not urls:
        try:
            resp = requests.get(site_url, timeout=15,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/") and len(href) > 1:
                    urls.append(base + href)
                elif href.startswith(base) and href != base:
                    urls.append(href)
            logger.info("Homepage crawl — %d links found", len(urls))
        except Exception as exc:
            logger.warning("Homepage crawl failed: %s", exc)

    # 3. Always include the base URL itself as a last resort
    if site_url not in urls:
        urls.insert(0, site_url)

    # deduplicate + limit
    seen, result = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
        if len(result) >= count:
            break
    return result


# ── Style Profile extractor ───────────────────────────────────────────────────

TONE_SYSTEM_PROMPT = """Tu es un expert en analyse linguistique et éditoriale.
Tu reçois un corpus de textes extraits d'un site web.
Ta mission : produire un Style Profile JSON précis et actionnable qui permettra
à un autre LLM de reproduire fidèlement le ton éditorial de ce site.

Retourne UNIQUEMENT un objet JSON valide, sans markdown, sans commentaires.
Schema attendu (respecte exactement ces clés) :
{
  "tonality": [],
  "avg_sentence_length": "",
  "avg_paragraph_length": "",
  "preferred_vocabulary": [],
  "avoided_vocabulary": [],
  "recurring_patterns": [],
  "structural_patterns": [],
  "pov": "",
  "cta_style": "",
  "forbidden": []
}"""


def extract_style_profile(corpus: str) -> tuple[dict, int, int]:
    """Send corpus to Claude Opus and return structured Style Profile dict."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=config.CLAUDE_OPUS,
        max_tokens=1024,
        system=TONE_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Voici le corpus de textes du site. Analyse et retourne le Style Profile JSON.\n\n"
                    f"---CORPUS---\n{corpus}"
                ),
            }
        ],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    in_tok  = message.usage.input_tokens
    out_tok = message.usage.output_tokens
    return json.loads(raw), in_tok, out_tok


# ── Public API ────────────────────────────────────────────────────────────────

def build_style_profile(
    site_url: str,
    force_refresh: bool = False,
) -> tuple[dict, int, int]:
    """
    Return (style_profile_dict, input_tokens, output_tokens) for the given site URL.
    Uses a per-site cache file — tokens are 0 when loaded from cache.
    """
    cache = _cache_path(site_url)

    if not force_refresh and os.path.exists(cache):
        logger.info("Loading cached style profile from %s", cache)
        with open(cache) as f:
            return json.load(f), 0, 0

    logger.info("Building style profile for %s …", site_url)
    urls = _discover_page_urls(site_url, config.SCRAPE_PAGES_COUNT)
    logger.info("Discovered %d pages to scrape", len(urls))

    pages_text: list[str] = []
    errors: list[str] = []
    for url in urls:
        try:
            text = scrape_page(url)
            if text.strip():
                pages_text.append(f"=== {url} ===\n{text}")
                logger.info("Scraped %s (%d chars)", url, len(text))
            else:
                logger.warning("Empty content at %s", url)
            time.sleep(0.5)
        except Exception as exc:
            msg = f"{url}: {exc}"
            logger.warning("Skipping %s", msg)
            errors.append(msg)

    if not pages_text:
        detail = "\n".join(errors[:5]) if errors else "Aucune URL trouvée."
        raise RuntimeError(
            f"Aucune page n'a pu être scrappée pour '{site_url}'.\n"
            f"Vérifie que l'URL est correcte et accessible.\n"
            f"Détails ({len(errors)} erreur(s)) :\n{detail}"
        )

    corpus          = "\n\n".join(pages_text)[:40_000]
    profile, in_t, out_t = extract_style_profile(corpus)

    profile["_pages_scraped"] = urls[:len(pages_text)]
    profile["_scraped_count"] = len(pages_text)

    with open(cache, "w") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    logger.info("Style profile saved to %s", cache)

    return profile, in_t, out_t


def profile_cache_exists(site_url: str) -> bool:
    """True if a cached style profile exists for this site."""
    return os.path.exists(_cache_path(site_url))


def style_profile_to_system_context(profile: dict) -> str:
    """
    Convert the Style Profile dict to a compact system-prompt string
    ready to be injected into every writing pass.
    """
    return (
        "## Style Profile — consignes éditorielles strictes\n"
        f"Tonalité : {', '.join(profile.get('tonality', []))}\n"
        f"Longueur des phrases : {profile.get('avg_sentence_length', '')}\n"
        f"Longueur des paragraphes : {profile.get('avg_paragraph_length', '')}\n"
        f"Vocabulaire privilégié : {', '.join(profile.get('preferred_vocabulary', []))}\n"
        f"Vocabulaire à éviter : {', '.join(profile.get('avoided_vocabulary', []))}\n"
        f"Patterns récurrents : {', '.join(profile.get('recurring_patterns', []))}\n"
        f"Structure des titres : {', '.join(profile.get('structural_patterns', []))}\n"
        f"Point de vue : {profile.get('pov', '')}\n"
        f"Style CTA : {profile.get('cta_style', '')}\n"
        f"Absolument interdit : {', '.join(profile.get('forbidden', []))}\n"
    )
