"""
seo_intelligence.py
───────────────────
Agrège toutes les données SEO nécessaires avant la rédaction :

1. DataForSEO
   - SERP analysis  : top 10 concurrents, titres, métas, word count estimé
   - PAA (People Also Ask) : questions réelles des utilisateurs
   - Keyword clustering : variations sémantiques et mots-clés secondaires

2. Google Search Console (GSC)
   - Pages existantes à ne pas cannibaliser
   - Opportunités : pages en position 4-20 sur des requêtes proches

Output : SEOIntelligence dataclass prête à être consommée par writer.py
"""

import base64
import json
import logging
from dataclasses import dataclass, field

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os

import config

logger = logging.getLogger(__name__)

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class SerpResult:
    rank: int
    url: str
    title: str
    description: str


@dataclass
class KeywordCluster:
    primary: str
    secondary: list[str]  = field(default_factory=list)  # volume > 500
    lsi: list[str]        = field(default_factory=list)  # volume 50-500
    long_tail: list[str]  = field(default_factory=list)  # volume < 50 / questions


@dataclass
class GSCPage:
    url: str
    clicks: int
    impressions: int
    avg_position: float
    top_query: str


@dataclass
class SEOIntelligence:
    keyword: str
    serp_top10: list[SerpResult]        = field(default_factory=list)
    paa_questions: list[str]            = field(default_factory=list)
    keyword_cluster: KeywordCluster     = field(default_factory=lambda: KeywordCluster(""))
    cannibalisation_risk: list[GSCPage] = field(default_factory=list)
    gsc_opportunities: list[GSCPage]    = field(default_factory=list)
    recommended_h2: list[str]           = field(default_factory=list)
    meta_title_examples: list[str]      = field(default_factory=list)
    search_intent: str                  = ""   # Informationnel / Commercial / Transactionnel / Mix
    errors: list[str]                   = field(default_factory=list)


# ── Search intent heuristic ───────────────────────────────────────────────

_INTENT_SIGNALS: dict[str, list[str]] = {
    "Informationnel": [
        "comment", "pourquoi", "qu'est", "guide", "tutoriel", "définition", "comprendre",
        "how", "what", "why", "guide", "tutorial", "definition", "learn", "tips",
        "hoe", "wat", "waarom", "gids",
    ],
    "Commercial": [
        "meilleur", "comparatif", "avis", "top", "classement", "choisir", "alternative",
        "best", "review", "vs", "comparison", "recommend",
        "beste", "vergelijking", "beoordeling",
    ],
    "Transactionnel": [
        "acheter", "prix", "achat", "commander", "tarif", "offre", "promo", "pas cher",
        "buy", "price", "order", "cheap", "discount", "shop",
        "kopen", "prijs", "bestellen", "goedkoop",
    ],
}


def infer_search_intent(keyword: str, titles: list[str]) -> str:
    """Classify search intent from keyword + SERP titles using signal counts."""
    corpus = " ".join([keyword] + titles).lower()
    scores = {intent: sum(1 for w in words if w in corpus)
              for intent, words in _INTENT_SIGNALS.items()}
    top    = max(scores, key=scores.get)
    second = sorted(scores, key=scores.get, reverse=True)[1]
    # Mixed if two intents are equally strong
    if scores[top] > 0 and scores[second] >= scores[top] - 1:
        return f"Mix {top} / {second}"
    return top if scores[top] > 0 else "Informationnel"  # default


# ── DataForSEO helpers ────────────────────────────────────────────────────────

def _dfs_request(endpoint: str, payload: list[dict]) -> dict:
    """Generic DataForSEO POST call with Basic Auth."""
    credentials = base64.b64encode(
        f"{config.DATAFORSEO_LOGIN}:{config.DATAFORSEO_PASSWORD}".encode()
    ).decode()
    resp = requests.post(
        f"https://api.dataforseo.com/v3/{endpoint}",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_serp(keyword: str) -> tuple[list[SerpResult], list[str]]:
    """
    Returns (top10 organic results, PAA questions) for the keyword.
    Uses DataForSEO SERP Live endpoint.
    """
    payload = [
        {
            "keyword": keyword,
            "language_code": config.DATAFORSEO_LANGUAGE,
            "location_code": config.DATAFORSEO_LOCATION,
            "device": "desktop",
            "depth": config.SERP_RESULTS_COUNT,
        }
    ]
    data    = _dfs_request("serp/google/organic/live/advanced", payload)
    task    = data.get("tasks", [{}])[0]
    result  = (task.get("result") or [{}])[0]
    items   = result.get("items", [])

    organic: list[SerpResult] = []
    paa:     list[str]        = []

    for item in items:
        itype = item.get("type", "")
        if itype == "organic":
            organic.append(
                SerpResult(
                    rank        = item.get("rank_absolute", 0),
                    url         = item.get("url", ""),
                    title       = item.get("title", ""),
                    description = item.get("description", ""),
                )
            )
        elif itype == "people_also_ask":
            for q in item.get("items", []):
                if len(paa) < config.PAA_MAX:
                    paa.append(q.get("title", ""))

    return organic[: config.SERP_RESULTS_COUNT], paa


def _parse_kw_items(items: list[dict], cluster: KeywordCluster,
                    seen: set[str], limit: int = 40) -> None:
    """
    Classify keyword items into secondary / lsi / long_tail based on search volume.
    Handles both flat {search_volume} and nested {keyword_info: {search_volume}} formats.
    Modifies cluster in-place. `seen` prevents duplicates across calls.
    """
    count = 0
    for item in items:
        if count >= limit:
            break
        kw  = (item.get("keyword") or "").strip()
        # keywords_for_keywords wraps volume inside keyword_info
        kw_info = item.get("keyword_info") or {}
        vol = (kw_info.get("search_volume")
               or item.get("search_volume")
               or 0)
        if not kw or kw.lower() == cluster.primary.lower() or kw in seen:
            continue
        seen.add(kw)
        count += 1
        if vol >= 100:
            cluster.secondary.append(kw)
        elif vol >= 20:
            cluster.lsi.append(kw)
        else:
            cluster.long_tail.append(kw)


def fetch_keyword_cluster(keyword: str) -> KeywordCluster:
    """
    Builds a rich semantic cluster by combining two DataForSEO endpoints:
    1. keywords_for_keywords  — variations sémantiques directes
    2. related_keywords       — champ sémantique élargi (entités, synonymes)
    Falls back gracefully if either endpoint is unavailable.
    """
    cluster = KeywordCluster(primary=keyword)
    seen: set[str] = set()

    # ── Source 1 : keywords_for_keywords ──────────────────────────────────────
    try:
        data  = _dfs_request(
            "dataforseo_labs/google/keywords_for_keywords/live",
            [{"keywords": [keyword],
              "language_code": config.DATAFORSEO_LANGUAGE,
              "location_code": config.DATAFORSEO_LOCATION,
              "limit": 50}],
        )
        items = (
            data.get("tasks", [{}])[0]
                .get("result", [{}])[0]
                .get("items", [])
        )
        _parse_kw_items(items, cluster, seen, limit=40)
        logger.info("[SEO] keywords_for_keywords — %d items parsed", len(items))
    except Exception as exc:
        logger.warning("keywords_for_keywords failed: %s", exc)

    # ── Source 2 : related_keywords ───────────────────────────────────────────
    try:
        data  = _dfs_request(
            "dataforseo_labs/google/related_keywords/live",
            [{"keyword": keyword,
              "language_code": config.DATAFORSEO_LANGUAGE,
              "location_code": config.DATAFORSEO_LOCATION,
              "limit": 50,
              "depth": 2}],
        )
        items = (
            data.get("tasks", [{}])[0]
                .get("result", [{}])[0]
                .get("items", [])
        )
        # related_keywords wraps each item in {keyword_data: {...}}
        flat = []
        for it in items:
            kd = it.get("keyword_data") or it
            flat.append({"keyword": kd.get("keyword", ""),
                         "search_volume": (kd.get("keyword_info") or {}).get("search_volume", 0)})
        _parse_kw_items(flat, cluster, seen, limit=40)
        logger.info("[SEO] related_keywords — %d items parsed", len(items))
    except Exception as exc:
        logger.warning("related_keywords failed: %s", exc)

    # ── Fallback : Claude semantic expansion when DFS yields < 5 keywords ──────
    total = len(cluster.secondary) + len(cluster.lsi) + len(cluster.long_tail)
    if total < 5 and config.ANTHROPIC_API_KEY:
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=config.CLAUDE_SONNET,
                max_tokens=400,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Donne-moi 15 variantes sémantiques du mot-clé '{keyword}' "
                        f"(synonymes, expressions longue traîne, questions, entités liées). "
                        f"Réponds UNIQUEMENT avec une liste de mots-clés séparés par des virgules, "
                        f"sans numérotation ni explication. Langue : même que le mot-clé."
                    ),
                }],
            )
            raw = msg.content[0].text.strip()
            for kw in [k.strip() for k in raw.split(",") if k.strip()]:
                if kw.lower() != keyword.lower() and kw not in seen:
                    seen.add(kw)
                    cluster.lsi.append(kw)
            logger.info("[SEO] Claude fallback keywords — %d ajoutés", len(cluster.lsi))
        except Exception as exc:
            logger.warning("Claude keyword fallback failed: %s", exc)

    return cluster


# ── Google Search Console helpers ─────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _get_gsc_service():
    """Returns an authenticated GSC service object (OAuth2)."""
    creds = None
    token_file = "gsc_token.json"

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(
                config.GSC_CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("searchconsole", "v1", credentials=creds)


def fetch_gsc_data(keyword: str) -> tuple[list[GSCPage], list[GSCPage]]:
    """
    Returns (cannibalisation_risks, opportunities).
    cannibalisation_risks : pages déjà classées sur ce keyword ou proches (position ≤ 3)
    opportunities         : pages entre position 4 et 20 sur des requêtes proches
    """
    if not config.GSC_SITE_URL or not os.path.exists(config.GSC_CREDENTIALS_FILE):
        logger.warning("GSC not configured — skipping GSC analysis")
        return [], []

    try:
        service = _get_gsc_service()
        body    = {
            "startDate": "2024-01-01",
            "endDate":   "2025-12-31",
            "dimensions": ["query", "page"],
            "dimensionFilterGroups": [
                {
                    "filters": [
                        {
                            "dimension": "query",
                            "operator": "contains",
                            "expression": keyword.split()[0],   # first word fuzzy match
                        }
                    ]
                }
            ],
            "rowLimit": 50,
        }
        response = (
            service.searchanalytics()
            .query(siteUrl=config.GSC_SITE_URL, body=body)
            .execute()
        )
        rows = response.get("rows", [])

        risks: list[GSCPage]   = []
        opps:  list[GSCPage]   = []
        seen_pages:  set[str]  = set()

        for row in rows:
            keys     = row.get("keys", ["", ""])
            query    = keys[0] if len(keys) > 0 else ""
            page     = keys[1] if len(keys) > 1 else ""
            position = row.get("position", 99)

            if page in seen_pages:
                continue
            seen_pages.add(page)

            gsc_page = GSCPage(
                url          = page,
                clicks       = int(row.get("clicks", 0)),
                impressions  = int(row.get("impressions", 0)),
                avg_position = round(position, 1),
                top_query    = query,
            )
            if position <= 3:
                risks.append(gsc_page)
            elif position <= 20:
                opps.append(gsc_page)

        return risks, opps

    except Exception as exc:
        logger.warning("GSC fetch failed: %s", exc)
        return [], []


# ── Recommended H2s ──────────────────────────────────────────────────────────

def _build_recommended_h2s(
    paa: list[str],
    cluster: KeywordCluster,
    serp: list[SerpResult],
) -> list[str]:
    """
    Derives a list of recommended H2 angles from PAA + competitor titles + cluster.
    These feed directly into the writer's plan.
    """
    h2s: list[str] = []

    # PAA questions make excellent H2s
    for q in paa[:5]:
        h2s.append(q)

    # Secondary keywords that aren't already covered
    for kw in cluster.secondary[:5]:
        candidate = kw.capitalize()
        if candidate not in h2s:
            h2s.append(candidate)

    # Extract unique title patterns from competitors
    for result in serp[:5]:
        title = result.title.split("|")[0].split("–")[0].strip()
        if title and title not in h2s:
            h2s.append(title)

    return h2s[:8]


# ── Public API ────────────────────────────────────────────────────────────────

def gather_seo_intelligence(keyword: str) -> SEOIntelligence:
    """
    Main entry point. Returns a fully populated SEOIntelligence object.
    Individual sub-fetches fail gracefully so a missing API key
    doesn't block the whole pipeline.
    """
    intel = SEOIntelligence(keyword=keyword)

    if not config.DATAFORSEO_LOGIN or not config.DATAFORSEO_PASSWORD:
        intel.errors.append("DATAFORSEO_LOGIN ou DATAFORSEO_PASSWORD manquant dans les secrets")
        intel.keyword_cluster = KeywordCluster(primary=keyword)
        return intel

    logger.info("[SEO] Fetching SERP + PAA for: %s", keyword)
    try:
        intel.serp_top10, intel.paa_questions = fetch_serp(keyword)
        intel.meta_title_examples = [r.title for r in intel.serp_top10[:3]]
    except Exception as exc:
        msg = f"SERP/PAA : {exc}"
        logger.warning(msg)
        intel.errors.append(msg)

    logger.info("[SEO] Fetching keyword cluster …")
    try:
        intel.keyword_cluster = fetch_keyword_cluster(keyword)
    except Exception as exc:
        msg = f"Keyword cluster : {exc}"
        logger.warning(msg)
        intel.errors.append(msg)
        intel.keyword_cluster = KeywordCluster(primary=keyword)

    logger.info("[SEO] Fetching GSC data …")
    intel.cannibalisation_risk, intel.gsc_opportunities = fetch_gsc_data(keyword)

    intel.recommended_h2 = _build_recommended_h2s(
        intel.paa_questions, intel.keyword_cluster, intel.serp_top10
    )

    intel.search_intent = infer_search_intent(
        keyword, [r.title for r in intel.serp_top10]
    )
    logger.info("[SEO] Intent détecté : %s", intel.search_intent)

    return intel


def seo_intel_to_brief(intel: SEOIntelligence) -> str:
    """
    Serialises SEOIntelligence to a rich brief for the writer.
    Includes SERP sources (country-local), intent, semantic clusters and PAA.
    """
    cl = intel.keyword_cluster
    lines = [
        f"## SEO Brief — mot-clé cible : {intel.keyword}",
        f"Intention de recherche détectée : **{intel.search_intent or 'Informationnel'}**",
        "",
    ]

    # ─ Sources SERP locales (vraies pages qui rankent) ────────────────────────────
    if intel.serp_top10:
        lines.append("### Sources SERP locales — pages qui rankent sur ce marché")
        lines.append("(Utilise ces URLs comme références principales dans le briefing)")
        for i, r in enumerate(intel.serp_top10[:8], 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   URL : {r.url}")
            if r.description:
                lines.append(f"   Extrait : {r.description[:120]}…")
        lines.append("")

    # ─ Champ sémantique ─────────────────────────────────────────────────────────
    lines += [
        "### Mots-clés secondaires (volume ≥ 500)",
        ", ".join(cl.secondary[:12]) or "—",
        "",
        "### LSI / sémantique élargie (volume 50-500)",
        ", ".join(cl.lsi[:15]) or "—",
        "",
        "### Longue traîne",
        ", ".join(cl.long_tail[:10]) or "—",
        "",
        "### Questions PAA (intention de recherche locale)",
    ]
    for q in intel.paa_questions[:8]:
        lines.append(f"- {q}")

    if intel.recommended_h2:
        lines += ["", "### H2 suggérés"]
        for h in intel.recommended_h2:
            lines.append(f"- {h}")

    if intel.cannibalisation_risk:
        lines += [
            "",
            "### Risque de cannibalisation — ne pas dupliquer",
        ]
        for p in intel.cannibalisation_risk:
            lines.append(f"- {p.url} (position {p.avg_position}, requête: {p.top_query})")

    if intel.gsc_opportunities:
        lines += ["", "### Opportunités GSC (lier si pertinent)"]
        for p in intel.gsc_opportunities[:3]:
            lines.append(f"- {p.url} (position {p.avg_position})")

    return "\n".join(lines)
