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


def _cache_path(site_url: str, target_lang: str = "") -> str:
    """One cache file per site URL."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", site_url.lower().rstrip("/").replace("https://", "").replace("http://", ""))
    lang_suffix = f"-{target_lang.lower()[:2]}" if target_lang else ""
    os.makedirs(config.STYLE_PROFILE_CACHE_DIR, exist_ok=True)
    return os.path.join(config.STYLE_PROFILE_CACHE_DIR, f"{slug}{lang_suffix}.json")


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


def _parse_sitemap_locs(xml_text: str) -> list[str]:
    """Extract all <loc> values from a sitemap or sitemap index."""
    try:
        soup = BeautifulSoup(xml_text, "lxml-xml")
    except Exception:
        soup = BeautifulSoup(xml_text, "html.parser")
    return [t.text.strip() for t in soup.find_all("loc") if t.text.strip()]


def _fetch_sitemap_urls(base: str) -> list[str]:
    """Try robots.txt, common sitemap paths, and sitemap indexes to collect page URLs."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ContentAgent/1.0)"}
    sitemap_urls: list[str] = []

    # 1. robots.txt → Sitemap: directives
    try:
        r = requests.get(base + "/robots.txt", timeout=10, headers=headers)
        if r.ok:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if sm not in sitemap_urls:
                        sitemap_urls.append(sm)
    except Exception:
        pass

    # 2. Common sitemap candidates
    for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
                 "/post-sitemap.xml", "/page-sitemap.xml", "/blog-sitemap.xml"]:
        candidate = base + path
        if candidate not in sitemap_urls:
            sitemap_urls.append(candidate)

    page_urls: list[str] = []
    for sm_url in sitemap_urls:
        try:
            r = requests.get(sm_url, timeout=12, headers=headers)
            if not r.ok or "<loc>" not in r.text:
                continue
            locs = _parse_sitemap_locs(r.text)
            # Sitemap index → recurse one level
            if "<sitemapindex" in r.text.lower() or "<sitemap>" in r.text.lower():
                for child_sm in locs[:8]:
                    try:
                        cr = requests.get(child_sm, timeout=12, headers=headers)
                        if cr.ok and "<loc>" in cr.text:
                            page_urls.extend(_parse_sitemap_locs(cr.text))
                    except Exception:
                        pass
            else:
                page_urls.extend(locs)
            if page_urls:
                logger.info("Sitemap %s — %d URLs", sm_url, len(page_urls))
                break
        except Exception as exc:
            logger.debug("Sitemap %s failed: %s", sm_url, exc)

    return page_urls


def _url_matches_language(url: str, target_lang: str) -> bool:
    if target_lang not in {"fr", "nl", "en"}:
        return True
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url.lower())
    path = f"/{parsed.path.strip('/')}/"
    query_lang = parse_qs(parsed.query).get("lang", []) + parse_qs(parsed.query).get("locale", [])
    if any(value.lower().startswith(target_lang) for value in query_lang):
        return True
    if f"/{target_lang}/" in path or f"-{target_lang}/" in path or f"_{target_lang}/" in path:
        return True
    if target_lang == "fr" and any(segment in path for segment in ["/fr-be/", "/fr-fr/", "/fr/"]):
        return True
    if target_lang == "nl" and any(segment in path for segment in ["/nl-be/", "/nl-nl/", "/nl/"]):
        return True
    if target_lang == "en" and any(segment in path for segment in ["/en-gb/", "/en-us/", "/en/"]):
        return True
    has_lang_marker = any(marker in path for marker in [
        "/fr/", "/fr-be/", "/fr-fr/", "/nl/", "/nl-be/", "/nl-nl/",
        "/en/", "/en-gb/", "/en-us/",
    ])
    return not has_lang_marker


def _filter_urls_by_language(urls: list[str], target_lang: str) -> list[str]:
    if target_lang not in {"fr", "nl", "en"}:
        return urls
    matched = [url for url in urls if _url_matches_language(url, target_lang)]
    return matched if len(matched) >= 3 else urls


def _discover_page_urls(site_url: str, count: int, target_lang: str = "") -> list[str]:
    """
    Discover content pages to scrape.
    Priority: sitemap > robots.txt sitemap > homepage links > common paths.
    Always includes site_url itself.
    """
    urls: list[str] = []
    base = site_url.rstrip("/")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ContentAgent/1.0)"}

    # 1. Sitemap-based discovery
    sitemap_urls = _fetch_sitemap_urls(base)
    content_kws = ["/blog", "/article", "/guide", "/conseil", "/actualit",
                   "/recette", "/ingredient", "/race", "/sante", "/nutrition",
                   "/nieuws", "/advies", "/gezond", "/voeding", "/post"]
    preferred = [u for u in sitemap_urls
                 if any(k in u.lower() for k in content_kws)]
    urls = preferred if len(preferred) >= 3 else sitemap_urls
    urls = _filter_urls_by_language(urls, target_lang)

    # 2. Fallback: parse homepage <a> links
    if len(urls) < count:
        try:
            resp = requests.get(site_url, timeout=15, headers=headers)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/") and len(href) > 1:
                    urls.append(base + href)
                elif href.startswith(base) and href != base + "/":
                    urls.append(href)
            logger.info("Homepage crawl — %d extra links", len(urls))
        except Exception as exc:
            logger.warning("Homepage crawl failed: %s", exc)

    # 3. Common content paths as last resort
    if len(urls) < count:
        for path in ["/blog", "/blog/", "/articles", "/guides", "/recettes",
                     "/nieuws", "/advies", "/about", "/a-propos"]:
            urls.append(base + path)

    # 4. Always include homepage
    if site_url not in urls:
        urls.insert(0, site_url)

    # deduplicate + filter to same domain + limit
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
Ta mission : produire un Style Profile JSON précis et actionnable.

Règle critique : Détecte la langue dominante du corpus (fr / nl / en).
Écris TOUTES les valeurs textuelles du JSON dans cette même langue détectée.
Ne mélange JAMAIS les langues dans les valeurs JSON.

Retourne UNIQUEMENT un objet JSON valide, sans markdown, sans commentaires.
Schéma attendu (respecte exactement ces clés) :
{
  "detected_language": "fr",
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
                    "Here is the corpus of texts from the website. "
                    "Detect the language and produce the Style Profile JSON in that language.\n\n"
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
    target_lang: str = "",
) -> tuple[dict, int, int]:
    """
    Return (style_profile_dict, input_tokens, output_tokens) for the given site URL.
    Uses a per-site cache file — tokens are 0 when loaded from cache.
    """
    target_lang = (target_lang or "").lower()[:2]
    cache = _cache_path(site_url, target_lang)

    if not force_refresh and os.path.exists(cache):
        logger.info("Loading cached style profile from %s", cache)
        with open(cache) as f:
            return json.load(f), 0, 0

    logger.info("Building style profile for %s …", site_url)
    urls = _discover_page_urls(site_url, config.SCRAPE_PAGES_COUNT, target_lang=target_lang)
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
    profile["_target_language"] = target_lang

    with open(cache, "w") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    logger.info("Style profile saved to %s", cache)

    return profile, in_t, out_t


def profile_cache_exists(site_url: str, target_lang: str = "") -> bool:
    """True if a cached style profile exists for this site."""
    return os.path.exists(_cache_path(site_url, (target_lang or "").lower()[:2]))


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
