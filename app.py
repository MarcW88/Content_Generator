"""
app.py — Backoffice Content Agent
──────────────────────────────────
Lancement : streamlit run app.py
"""

import json
import logging
import os
import re
from datetime import datetime

import streamlit as st

import config
from cost_tracker import estimate_request_cost, format_usd, PassCost

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Content Agent",
    page_icon="✍️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

# ══════════════════════════════════════════════════════════════════════════════
# CSS — light backoffice theme, no sidebar
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
  /* Hide sidebar */
  [data-testid="collapsedControl"] { display: none !important; }
  section[data-testid="stSidebar"]  { display: none !important; }

  /* Base */
  .stApp { background: #f4f5f7; }
  .stApp p, .stApp li, .stApp label { color: #1a1d2e !important; }
  .stApp h1, .stApp h2, .stApp h3 { color: #1a1d2e !important; }

  /* Top nav */
  .nav-bar {
    display: flex; align-items: center; justify-content: space-between;
    background: #ffffff; border-bottom: 1px solid #e2e5ed;
    padding: 0 32px; height: 58px; margin-bottom: 28px;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
  }
  .nav-brand { color: #1a1d2e; font-size: 16px; font-weight: 800; }
  .nav-brand span { color: #4f6ef7; }

  /* KPI cards */
  .kpi-card {
    background: #ffffff; border: 1px solid #e2e5ed; border-radius: 12px;
    padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.05);
  }
  .kpi-label { color: #6b7280; font-size: 11px; font-weight: 700;
               letter-spacing: .08em; text-transform: uppercase; margin-bottom: 8px; }
  .kpi-value { color: #1a1d2e; font-size: 26px; font-weight: 800; line-height: 1; }
  .kpi-sub   { color: #4f6ef7; font-size: 12px; margin-top: 6px; }

  /* Pipeline stepper */
  .pipeline-wrapper {
    background: #ffffff; border: 1px solid #e2e5ed; border-radius: 14px;
    padding: 28px 32px; margin: 20px 0;
    box-shadow: 0 2px 8px rgba(0,0,0,.06);
  }
  .pipeline-title {
    font-size: 13px; font-weight: 700; color: #6b7280;
    text-transform: uppercase; letter-spacing: .08em; margin-bottom: 22px;
  }
  .step-row { display: flex; gap: 12px; align-items: stretch; }
  .step-box {
    flex: 1; background: #f8f9fc; border: 2px solid #e2e5ed;
    border-radius: 10px; padding: 16px 12px; text-align: center;
    transition: all .2s;
  }
  .step-box.running {
    background: #eff3ff; border-color: #4f6ef7;
    box-shadow: 0 0 0 3px rgba(79,110,247,.12);
  }
  .step-box.done    { background: #f0fdf4; border-color: #22c55e; }
  .step-box.error   { background: #fff5f5; border-color: #ef4444; }
  .step-num   { font-size: 11px; font-weight: 700; color: #9ca3af; text-transform: uppercase; margin-bottom: 8px; }
  .step-icon  { font-size: 26px; margin-bottom: 6px; }
  .step-name  { font-size: 13px; font-weight: 700; color: #374151; margin-bottom: 4px; }
  .step-detail{ font-size: 11px; color: #6b7280; min-height: 16px; }
  .step-cost  { font-size: 11px; color: #22c55e; font-weight: 600; margin-top: 4px; }

  /* Cost badge */
  .cost-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 20px;
    padding: 5px 14px; font-size: 13px; font-weight: 700; color: #15803d;
  }

  /* Section header */
  .section-hdr {
    color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; border-bottom: 1px solid #e2e5ed;
    padding-bottom: 8px; margin: 28px 0 18px;
  }

  /* API chips */
  .profile-chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: #f8f9fc; border: 1px solid #e2e5ed; border-radius: 8px;
    padding: 6px 14px; font-size: 13px; color: #374151; margin: 4px;
    font-weight: 500;
  }
  .chip-green { background: #f0fdf4; border-color: #86efac; color: #15803d; }
  .chip-red   { background: #fff5f5; border-color: #fca5a5; color: #dc2626; }

  /* Dataframe + expanders */
  [data-testid="stDataFrame"] { background: #fff; border-radius: 10px; }
  details > summary { color: #1a1d2e !important; font-weight: 600; }

  /* Download buttons */
  div[data-testid="stDownloadButton"] button {
    background: #ffffff !important; border: 1px solid #d1d5db !important;
    color: #374151 !important; font-weight: 600 !important;
  }
  div[data-testid="stDownloadButton"] button:hover {
    background: #f3f4f6 !important;
  }

  /* Primary button */
  .stButton > button[kind="primary"] {
    background: #4f6ef7 !important; border-color: #4f6ef7 !important;
    color: #fff !important; font-weight: 700 !important; border-radius: 8px !important;
  }
  .stButton > button[kind="primary"]:hover { background: #3b55e0 !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Session state init
# ══════════════════════════════════════════════════════════════════════════════
if "page" not in st.session_state:
    st.session_state.page = "dashboard"


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    text = text.lower().strip()
    for src, dst in [("àáâä","a"),("èéêë","e"),("ìíîï","i"),("òóôö","o"),("ùúûü","u"),("ç","c")]:
        for c in src:
            text = text.replace(c, dst)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")

def _count_words(text: str) -> int:
    return len(text.split())

def _load_articles() -> list[dict]:
    out = config.OUTPUT_DIR
    if not os.path.exists(out):
        return []
    result = []
    for f in sorted(os.listdir(out), reverse=True):
        if f.endswith(".json"):
            try:
                with open(os.path.join(out, f), encoding="utf-8") as fh:
                    result.append(json.load(fh))
            except Exception:
                pass
    return result

def _list_style_profiles() -> list[tuple[str, str]]:
    d = config.STYLE_PROFILE_CACHE_DIR
    if not os.path.exists(d):
        return []
    return [(f, os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".json")]

def _kpi(label: str, value: str, sub: str = "") -> str:
    return (
        f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        + (f'<div class="kpi-sub">{sub}</div>' if sub else "")
        + "</div>"
    )

def _nav():
    pages  = [("dashboard","📊 Dashboard"), ("generate","✍️ Générer"),
              ("library","📚 Bibliothèque"), ("settings","⚙️ Paramètres")]
    items  = ""
    for key, label in pages:
        cls = "nav-item active" if st.session_state.page == key else "nav-item"
        items += f'<span class="{cls}" id="nav-{key}">{label}</span>'
    st.markdown(
        f'<div class="nav-bar">'
        f'  <div class="nav-brand">✍️ <span>Content</span>Agent</div>'
        f'  <div class="nav-items">{items}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(len(pages))
    for i, (key, label) in enumerate(pages):
        if cols[i].button(label, key=f"nav_btn_{key}", use_container_width=True,
                          type="primary" if st.session_state.page == key else "secondary"):
            st.session_state.page = key
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# NAV
# ══════════════════════════════════════════════════════════════════════════════
_nav()
page = st.session_state.page


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if page == "dashboard":
    articles = _load_articles()

    total_articles = len(articles)
    total_words    = sum(a.get("word_count", 0) for a in articles)
    total_cost     = sum(a.get("cost", {}).get("total_usd", 0) for a in articles)
    avg_cost       = total_cost / total_articles if total_articles else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_kpi("Articles générés", str(total_articles)), unsafe_allow_html=True)
    c2.markdown(_kpi("Mots rédigés", f"{total_words:,}".replace(",","'")), unsafe_allow_html=True)
    c3.markdown(_kpi("Coût total", format_usd(total_cost), "USD — LLM + SEO"), unsafe_allow_html=True)
    c4.markdown(_kpi("Coût moyen / article", format_usd(avg_cost)), unsafe_allow_html=True)

    if articles:
        st.markdown('<div class="section-hdr">Coût par article (10 derniers)</div>', unsafe_allow_html=True)
        chart_data = {
            a.get("keyword", "?")[:30]: a.get("cost", {}).get("total_usd", 0)
            for a in articles[:10]
        }
        st.bar_chart(chart_data, height=200, color="#58a6ff")

        st.markdown('<div class="section-hdr">Dernières générations</div>', unsafe_allow_html=True)
        rows = []
        for a in articles[:8]:
            cost_d = a.get("cost", {})
            rows.append({
                "Mot-clé":    a.get("keyword","—"),
                "Site":       a.get("site_url","—"),
                "Mots":       a.get("word_count", 0),
                "Coût USD":   format_usd(cost_d.get("total_usd", 0)),
                "Tokens in":  cost_d.get("input_tokens", "—"),
                "Tokens out": cost_d.get("output_tokens", "—"),
                "Date":       a.get("generated_at","")[:16].replace("T"," "),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("Aucun article généré. Va dans **✍️ Générer** pour commencer.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — GÉNÉRER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "generate":
    st.markdown("## ✍️ Nouveau contenu")

    col_a, col_b, col_c = st.columns([2, 2, 1])
    with col_a:
        site_url = st.text_input(
            "Site cible",
            placeholder="https://www.monsite.com",
            key="gen_site_url",
        )
    with col_b:
        keyword = st.text_input(
            "Mot-clé principal",
            placeholder="rénovation cuisine Bruxelles",
            key="gen_keyword",
        )
    with col_c:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        refresh_style = st.checkbox("Forcer rebuild style", value=False)

    # Cost estimate
    if site_url and keyword:
        from tone_analyzer import profile_cache_exists
        cached = profile_cache_exists(site_url)
        est    = estimate_request_cost(style_profile_cached=cached)
        st.markdown(
            f'<div style="margin:8px 0 16px">'
            f'<span class="cost-badge">💰 Estimation : {format_usd(est.total_usd)}'
            f'{"  ·  style profile en cache" if cached else "  ·  inclut analyse tonale"}'
            f'</span></div>',
            unsafe_allow_html=True,
        )

    launch = st.button(
        "⚡ Lancer la génération",
        type="primary",
        disabled=not (site_url.strip() if site_url else False) or not (keyword.strip() if keyword else False),
    )

    if not (site_url and keyword):
        st.markdown(
            '<p style="color:#8b949e;font-size:13px">Remplis le site cible et le mot-clé pour continuer.</p>',
            unsafe_allow_html=True,
        )

    if launch and site_url and keyword:
        if not config.ANTHROPIC_API_KEY:
            st.error("❌ `ANTHROPIC_API_KEY` manquante — configure tes secrets Streamlit.")
            st.stop()

        import json as _json

        # ═══════════════════════════════════════════════════════════════
        # Pipeline stepper — 6 étapes visibles
        # ═══════════════════════════════════════════════════════════════
        STEPS = [
            ("Style",    "🎨", "Analyse du ton"),
            ("SEO",      "📊", "Données SERP"),
            ("Passe 1",  "✏️",  "Introduction"),
            ("Passe 2",  "🗂️",  "Plan H2/H3"),
            ("Passe 3",  "📝", "Corps"),
            ("Passe 4",  "🔍", "Méta + Révision"),
        ]
        states  = ["pending"] * 6   # pending | running | done | error
        details = [""] * 6
        costs   = [0.0] * 6
        running_cost = [0.0]

        stepper_ph  = st.empty()
        progress_bar = st.progress(0)
        cost_ph      = st.empty()
        log_ph       = st.empty()

        def _stepper():
            icon_map = {"pending":"○", "running":"◉", "done":"✓", "error":"✗"}
            css_map  = {"pending":"", "running":" running", "done":" done", "error":" error"}
            boxes = ""
            for i, (short, emoji, name) in enumerate(STEPS):
                st_cls  = css_map[states[i]]
                ic      = icon_map[states[i]]
                cost_ln = f'<div class="step-cost">{format_usd(costs[i])}</div>' if costs[i] > 0 else ""
                boxes  += (
                    f'<div class="step-box{st_cls}">'
                    f'<div class="step-num">{short} {ic}</div>'
                    f'<div class="step-icon">{emoji}</div>'
                    f'<div class="step-name">{name}</div>'
                    f'<div class="step-detail">{details[i]}</div>'
                    f'{cost_ln}</div>'
                )
            stepper_ph.markdown(
                f'<div class="pipeline-wrapper">'
                f'<div class="pipeline-title">Progression de la génération</div>'
                f'<div class="step-row">{boxes}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            cost_ph.markdown(
                f'<div style="text-align:right;margin:-8px 0 12px">'
                f'<span class="cost-badge">💰 Coût en cours : {format_usd(running_cost[0])}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        def _set(i, state, detail="", cost=0.0):
            states[i]  = state
            details[i] = detail
            costs[i]   = cost
            running_cost[0] += cost
            _stepper()

        _stepper()  # render initial state

        # ── Étape 0 — Style profile ───────────────────────────────────────────
        _set(0, "running", "Scraping + Claude Opus…")
        progress_bar.progress(5)
        with log_ph.status("🎨 Étape 1/6 — Analyse du style éditorial", expanded=True) as s_log:
            try:
                from tone_analyzer import build_style_profile, style_profile_to_system_context
                profile_data, sp_in, sp_out = build_style_profile(site_url, force_refresh=refresh_style)
                style_ctx = style_profile_to_system_context(profile_data)
                sp_cost = PassCost(config.CLAUDE_OPUS, sp_in, sp_out).usd if sp_in else 0
                st.write(f"Site analysé : {site_url}")
                st.write(f"{len(profile_data)} attributs extraits {'(depuis cache)' if not sp_in else f'— {sp_in:,} tokens lus'}")
                s_log.update(label="🎨 Style éditorial — ✅ terminé", state="complete", expanded=False)
                _set(0, "done", "Cache" if not sp_in else f"{sp_in:,} tok", sp_cost)
            except Exception as e:
                s_log.update(label="🎨 Style — ❌ erreur", state="error")
                _set(0, "error", str(e)[:30])
                st.error(str(e)); st.stop()

        # ── Étape 1 — SEO intelligence ────────────────────────────────────────
        _set(1, "running", "DataForSEO + GSC…")
        progress_bar.progress(16)
        with log_ph.status("📊 Étape 2/6 — Données SEO", expanded=True) as s_log:
            try:
                from seo_intelligence import gather_seo_intelligence, seo_intel_to_brief
                intel     = gather_seo_intelligence(keyword)
                seo_brief = seo_intel_to_brief(intel)
                st.write(f"Mot-clé : {keyword}")
                st.write(f"{len(intel.serp_top10)} résultats SERP analysés")
                st.write(f"{len(intel.paa_questions)} questions PAA extraites")
                st.write(f"{len(intel.keyword_cluster.secondary)} mots-clés secondaires")
                if intel.cannibalisation_risk:
                    st.warning(f"⚠️ {len(intel.cannibalisation_risk)} risques de cannibalisation détectés")
                s_log.update(label="📊 SEO — ✅ terminé", state="complete", expanded=False)
                _set(1, "done", f"{len(intel.paa_questions)} PAA")
            except Exception as e:
                st.write(f"⚠️ SEO partiel : {e}")
                s_log.update(label="📊 SEO — ⚠️ partiel", state="complete", expanded=False)
                from seo_intelligence import SEOIntelligence, KeywordCluster, seo_intel_to_brief
                intel     = SEOIntelligence(keyword=keyword, keyword_cluster=KeywordCluster(primary=keyword))
                seo_brief = seo_intel_to_brief(intel)
                _set(1, "done", "partiel")

        # ── Passes 1-4 ────────────────────────────────────────────────────────
        from writer import (
            _build_system, _call_claude,
            PASS1_PROMPT, PASS2_PROMPT, PASS3_PROMPT, PASS4_PROMPT,
            ArticleOutput,
        )
        article = ArticleOutput(keyword=keyword, site_url=site_url)
        system  = _build_system(style_ctx, seo_brief)

        PASS_CFG = [
            (2, 35, PASS1_PROMPT.format(keyword=keyword),                          "Étape 3/6 — Introduction",       "intro"),
            (3, 55, None,                                                            "Étape 4/6 — Plan H2/H3",         "plan"),
            (4, 72, None,                                                            "Étape 5/6 — Corps de l'article", "body"),
            (5, 88, None,                                                            "Étape 6/6 — Méta + Révision",    "meta"),
        ]

        for step_i, pct, prompt_tpl, label, role in PASS_CFG:
            pass_n = step_i - 1  # pass number 1-4
            _set(step_i, "running", "Claude Sonnet…")
            progress_bar.progress(pct)

            # Build prompt dynamically for passes 2-4
            if role == "plan":
                prompt_tpl = PASS2_PROMPT.format(pass1_output=article.introduction)
            elif role == "body":
                prompt_tpl = PASS3_PROMPT.format(
                    pass1_output=article.introduction,
                    pass2_output=article.plan_h2_h3,
                    target_word_count=config.TARGET_WORD_COUNT,
                )
            elif role == "meta":
                prompt_tpl = PASS4_PROMPT.format(full_draft=f"{article.introduction}\n\n{article.body}")

            with log_ph.status(f"✍️ {label}", expanded=True) as s_log:
                try:
                    text, in_t, out_t = _call_claude(system, prompt_tpl)
                    p_cost = PassCost(config.CLAUDE_SONNET, in_t, out_t).usd
                    article.cost.passes.append(PassCost(config.CLAUDE_SONNET, in_t, out_t))

                    st.write(f"Tokens envoyés : {in_t:,}   |   Tokens reçus : {out_t:,}")

                    if role == "intro":
                        article.introduction = text
                        wc = _count_words(text)
                        st.write(f"Introduction rédigée — {wc} mots")
                        _set(step_i, "done", f"{wc} mots", p_cost)

                    elif role == "plan":
                        article.plan_h2_h3 = text
                        nb = text.count("##")
                        st.write(f"Plan généré — {nb} sections H2/H3")
                        with st.expander("Voir le plan", expanded=False):
                            st.code(text, language="markdown")
                        _set(step_i, "done", f"{nb} sections", p_cost)

                    elif role == "body":
                        article.body = text
                        wc = _count_words(text)
                        st.write(f"Corps rédigé — {wc} mots")
                        _set(step_i, "done", f"{wc} mots", p_cost)

                    elif role == "meta":
                        clean = text.strip().lstrip("```json").lstrip("```").rstrip("```")
                        p4 = _json.loads(clean)
                        article.meta_title       = p4.get("meta_title", "")
                        article.meta_description = p4.get("meta_description", "")
                        revised                  = p4.get("revised_article", f"{article.introduction}\n\n{article.body}")
                        article.full_article     = f"{revised}\n\n{p4.get('cta_final','')}".strip()
                        st.write(f"Meta title : {article.meta_title}")
                        st.write(f"Meta desc  : {article.meta_description}")
                        _set(step_i, "done", f"{len(article.meta_title)} car.", p_cost)

                    s_log.update(label=f"✅ {label} — terminé", state="complete", expanded=False)

                except Exception as e:
                    s_log.update(label=f"❌ {label} — erreur", state="error")
                    _set(step_i, "error", str(e)[:30])
                    if role != "meta":
                        st.error(str(e)); st.stop()
                    else:
                        article.full_article = f"{article.introduction}\n\n{article.body}"

        article.cost.dataforseo_tasks = 3 if config.DATAFORSEO_LOGIN else 0
        progress_bar.progress(100)
        log_ph.success(f"✅ Article généré — {_count_words(article.full_article)} mots · Coût réel : {format_usd(article.cost.total_usd)}")
        cost_ph.empty()

        # ── Save ──────────────────────────────────────────────────────────────
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        slug = _slugify(keyword)
        ts   = datetime.now().strftime("%Y%m%d_%H%M")
        base = os.path.join(config.OUTPUT_DIR, f"{slug}_{ts}")

        from writer import format_final_output
        md_content = format_final_output(article)
        with open(f"{base}.md", "w", encoding="utf-8") as f:
            f.write(md_content)

        bundle = {
            "keyword":          article.keyword,
            "site_url":         site_url,
            "meta_title":       article.meta_title,
            "meta_description": article.meta_description,
            "plan":             article.plan_h2_h3,
            "full_article":     article.full_article,
            "word_count":       _count_words(article.full_article),
            "pass_logs":        article.pass_logs,
            "generated_at":     datetime.now().isoformat(),
            "cost":             article.cost.to_dict(),
            "seo": {
                "secondary_keywords": intel.keyword_cluster.secondary,
                "paa":                intel.paa_questions,
                "cannibalisations":   [p.url for p in intel.cannibalisation_risk],
            },
        }
        json_content = _json.dumps(bundle, ensure_ascii=False, indent=2)
        with open(f"{base}.json", "w", encoding="utf-8") as f:
            f.write(json_content)

        # ── Results ───────────────────────────────────────────────────────────
        st.markdown('<div class="section-hdr">Résultats</div>', unsafe_allow_html=True)

        r1, r2, r3, r4 = st.columns(4)
        r1.markdown(_kpi("Mots", str(bundle["word_count"])), unsafe_allow_html=True)
        r2.markdown(_kpi("Coût réel", format_usd(article.cost.total_usd)), unsafe_allow_html=True)
        r3.markdown(_kpi("Tokens input", f"{article.cost.total_input_tokens:,}".replace(",","'")), unsafe_allow_html=True)
        r4.markdown(_kpi("Tokens output", f"{article.cost.total_output_tokens:,}".replace(",","'")), unsafe_allow_html=True)

        st.markdown(f"**🏷️ Meta title :** {article.meta_title}")
        st.markdown(f"**📝 Meta description :** {article.meta_description}")

        with st.expander("📐 Plan H2/H3", expanded=False):
            st.code(article.plan_h2_h3, language="markdown")

        with st.expander("📄 Article complet", expanded=True):
            st.markdown(article.full_article)

        if intel.cannibalisation_risk:
            st.warning(f"⚠️ {len(intel.cannibalisation_risk)} page(s) à risque de cannibalisation")
            for p in intel.cannibalisation_risk:
                st.caption(f"• {p.url} — pos. {p.avg_position} sur *{p.top_query}*")

        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Télécharger .md", md_content, f"{slug}_{ts}.md", "text/markdown", use_container_width=True)
        d2.download_button("⬇️ Télécharger .json", json_content, f"{slug}_{ts}.json", "application/json", use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — BIBLIOTHÈQUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "library":
    st.markdown("## 📚 Bibliothèque de contenus")

    articles = _load_articles()
    if not articles:
        st.info("Aucun article généré pour l'instant. Va dans **✍️ Générer** pour commencer.")
    else:
        search = st.text_input("🔍 Rechercher un mot-clé ou site", placeholder="ex: rénovation", label_visibility="collapsed")
        if search:
            articles = [a for a in articles if search.lower() in a.get("keyword","").lower()
                        or search.lower() in a.get("site_url","").lower()]

        st.caption(f"{len(articles)} article(s)")

        for a in articles:
            cost_d  = a.get("cost", {})
            cost_str = format_usd(cost_d.get("total_usd", 0)) if cost_d else "—"
            date_str = a.get("generated_at","")[:16].replace("T"," à ")
            site_str = a.get("site_url","")

            with st.expander(
                f"**{a.get('keyword','—')}** · {a.get('word_count',0)} mots · {cost_str} · {date_str}",
                expanded=False,
            ):
                if site_str:
                    st.caption(f"🌐 Site : `{site_str}`")
                st.markdown(f"**Meta title :** {a.get('meta_title','—')}")
                st.markdown(f"**Meta desc :** {a.get('meta_description','—')}")

                seo = a.get("seo", {})
                if seo.get("paa"):
                    st.markdown("**Questions PAA couvertes :**")
                    for q in seo["paa"][:4]:
                        st.caption(f"• {q}")
                if seo.get("cannibalisations"):
                    st.warning(f"⚠️ {len(seo['cannibalisations'])} risque(s) cannibalisation")

                if cost_d:
                    st.markdown(
                        f'<span class="cost-badge">💰 {cost_str} total — '
                        f'{cost_d.get("input_tokens",0):,} tokens in / {cost_d.get("output_tokens",0):,} out</span>',
                        unsafe_allow_html=True,
                    )

                slug  = _slugify(a.get("keyword","article"))
                fname = [f for f in os.listdir(config.OUTPUT_DIR)
                         if f.startswith(slug) and f.endswith(".md")] if os.path.exists(config.OUTPUT_DIR) else []

                if fname:
                    with open(os.path.join(config.OUTPUT_DIR, fname[0]), encoding="utf-8") as mf:
                        md_c = mf.read()
                    dl1, dl2 = st.columns(2)
                    dl1.download_button("⬇️ .md",   md_c,                          fname[0],               "text/markdown",    key=f"lib_md_{fname[0]}")
                    dl2.download_button("⬇️ .json", json.dumps(a, ensure_ascii=False, indent=2), fname[0].replace(".md",".json"), "application/json", key=f"lib_js_{fname[0]}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — PARAMÈTRES
# ══════════════════════════════════════════════════════════════════════════════
elif page == "settings":
    st.markdown("## ⚙️ Paramètres")

    # ── API Status ────────────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Clés API</div>', unsafe_allow_html=True)

    def _chip(label, ok, detail=""):
        cls = "profile-chip chip-green" if ok else "profile-chip chip-red"
        ico = "✓" if ok else "✗"
        return f'<span class="{cls}">{ico} {label}{(" · " + detail) if detail else ""}</span>'

    chips = (
        _chip("Anthropic", bool(config.ANTHROPIC_API_KEY),
              config.ANTHROPIC_API_KEY[:12]+"…" if config.ANTHROPIC_API_KEY else "manquante")
        + _chip("DataForSEO", bool(config.DATAFORSEO_LOGIN),
                config.DATAFORSEO_LOGIN[:20]+"…" if config.DATAFORSEO_LOGIN else "manquant")
        + _chip("Firecrawl", bool(config.FIRECRAWL_API_KEY),
                "configurée" if config.FIRECRAWL_API_KEY else "fallback BS4")
        + _chip("GSC", os.path.exists(config.GSC_CREDENTIALS_FILE),
                "credentials OK" if os.path.exists(config.GSC_CREDENTIALS_FILE) else "optionnel")
    )
    st.markdown(chips, unsafe_allow_html=True)
    st.caption("Pour modifier les clés : Streamlit Cloud → App settings → Secrets (en prod) ou fichier `.env` (en local)")

    # ── Modèles ───────────────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Modèles & paramètres</div>', unsafe_allow_html=True)
    mc1, mc2, mc3 = st.columns(3)
    mc1.markdown(_kpi("Rédaction (passes 1-4)", config.CLAUDE_SONNET), unsafe_allow_html=True)
    mc2.markdown(_kpi("Tone Analyzer", config.CLAUDE_OPUS), unsafe_allow_html=True)
    mc3.markdown(_kpi("Objectif mots", str(config.TARGET_WORD_COUNT)), unsafe_allow_html=True)

    # ── Style profiles en cache ────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Style profiles en cache</div>', unsafe_allow_html=True)
    profiles = _list_style_profiles()
    if not profiles:
        st.info("Aucun style profile en cache — ils sont créés automatiquement à la première génération pour chaque site.")
    else:
        for fname, fpath in profiles:
            mtime = os.path.getmtime(fpath)
            with open(fpath, encoding="utf-8") as f:
                p = json.load(f)
            with st.expander(f"🎨 `{fname}` — mis à jour le {datetime.fromtimestamp(mtime).strftime('%d/%m/%Y %H:%M')}"):
                col_pa, col_pb = st.columns(2)
                with col_pa:
                    st.markdown("**Tonalité :** " + ", ".join(p.get("tonality",[])))
                    st.markdown("**POV :** " + p.get("pov","—"))
                    st.markdown("**CTA :** " + p.get("cta_style","—"))
                with col_pb:
                    st.markdown("**Patterns :** " + ", ".join(p.get("recurring_patterns",[])[:3]))
                    st.markdown("**Vocab. évité :** " + ", ".join(p.get("avoided_vocabulary",[])[:3]))
                with st.expander("JSON brut"):
                    st.json(p)
                if st.button(f"🗑️ Supprimer ce profil", key=f"del_{fname}"):
                    os.remove(fpath)
                    st.success(f"{fname} supprimé.")
                    st.rerun()

    # ── Coûts de tarification ─────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Grille tarifaire utilisée</div>', unsafe_allow_html=True)
    from cost_tracker import PRICING, DATAFORSEO_COST_PER_TASK
    pricing_rows = [
        {"Modèle": m, "Input (USD/MTok)": f"${v['input']}", "Output (USD/MTok)": f"${v['output']}"}
        for m, v in PRICING.items()
    ]
    pricing_rows.append({"Modèle": "DataForSEO (par tâche)", "Input (USD/MTok)": f"${DATAFORSEO_COST_PER_TASK}", "Output (USD/MTok)": "—"})
    st.dataframe(pricing_rows, use_container_width=True, hide_index=True)
