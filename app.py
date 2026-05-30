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
# CSS — full backoffice, no sidebar
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
  /* Hide sidebar toggle + sidebar entirely */
  [data-testid="collapsedControl"] { display: none !important; }
  section[data-testid="stSidebar"]  { display: none !important; }

  .stApp { background: #0d1117; }

  /* Top nav bar */
  .nav-bar {
    display: flex; align-items: center; justify-content: space-between;
    background: #161b22; border-bottom: 1px solid #21262d;
    padding: 0 32px; height: 56px; margin-bottom: 28px;
  }
  .nav-brand { color: #e6edf3; font-size: 16px; font-weight: 700; letter-spacing: -0.3px; }
  .nav-brand span { color: #58a6ff; }
  .nav-items { display: flex; gap: 4px; }
  .nav-item {
    color: #8b949e; font-size: 13px; font-weight: 500;
    padding: 6px 14px; border-radius: 6px; cursor: pointer;
    border: 1px solid transparent; transition: all 0.15s;
    text-decoration: none;
  }
  .nav-item:hover  { color: #e6edf3; background: #21262d; }
  .nav-item.active { color: #e6edf3; background: #21262d; border-color: #30363d; }

  /* Cards */
  .kpi-card {
    background: #161b22; border: 1px solid #21262d; border-radius: 10px;
    padding: 20px 24px;
  }
  .kpi-label { color: #8b949e; font-size: 11px; font-weight: 600;
               letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 8px; }
  .kpi-value { color: #e6edf3; font-size: 26px; font-weight: 700; line-height: 1; }
  .kpi-sub   { color: #58a6ff; font-size: 12px; margin-top: 6px; }

  /* Pass cards */
  .pass-card {
    background: #161b22; border: 1px solid #21262d; border-radius: 8px;
    padding: 14px 16px; text-align: center;
  }
  .pass-label { color: #8b949e; font-size: 11px; font-weight: 600; text-transform: uppercase; }
  .pass-status { font-size: 22px; margin: 6px 0; }
  .pass-detail { color: #8b949e; font-size: 11px; }

  /* Article card in library */
  .article-card {
    background: #161b22; border: 1px solid #21262d; border-radius: 10px;
    padding: 18px 22px; margin-bottom: 12px;
    transition: border-color 0.15s;
  }
  .article-card:hover { border-color: #58a6ff; }
  .article-title { color: #e6edf3; font-size: 15px; font-weight: 600; margin-bottom: 6px; }
  .article-meta  { color: #8b949e; font-size: 12px; }
  .tag {
    display: inline-block; background: #21262d; border-radius: 20px;
    padding: 2px 10px; font-size: 11px; color: #58a6ff; margin-right: 6px;
  }

  /* Cost badge */
  .cost-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: #0d1117; border: 1px solid #30363d; border-radius: 20px;
    padding: 4px 12px; font-size: 12px; color: #3fb950;
  }

  /* Inputs */
  .stTextInput > div > div > input, .stTextArea textarea {
    background: #161b22 !important; border: 1px solid #30363d !important;
    color: #e6edf3 !important; border-radius: 6px !important;
  }
  .stSelectbox > div > div { background: #161b22 !important; border-color: #30363d !important; }

  /* Buttons */
  .stButton > button[kind="primary"] {
    background: #238636 !important; border-color: #2ea043 !important;
    color: #fff !important; font-weight: 600 !important;
  }
  .stButton > button[kind="primary"]:hover { background: #2ea043 !important; }
  div[data-testid="stDownloadButton"] button {
    background: #21262d !important; border: 1px solid #30363d !important;
    color: #e6edf3 !important; font-weight: 500 !important;
  }

  /* Section header */
  .section-hdr {
    color: #8b949e; font-size: 11px; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; border-bottom: 1px solid #21262d;
    padding-bottom: 8px; margin: 24px 0 16px;
  }

  /* Profile chip */
  .profile-chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: #161b22; border: 1px solid #21262d; border-radius: 6px;
    padding: 4px 10px; font-size: 12px; color: #e6edf3; margin: 3px;
  }
  .chip-green { border-color: #3fb950; color: #3fb950; }
  .chip-red   { border-color: #f85149; color: #f85149; }
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

        # ── Progress ──────────────────────────────────────────────────────────
        progress_bar    = st.progress(0, text="Initialisation…")
        cost_placeholder = st.empty()
        c1b, c2b, c3b, c4b = st.columns(4)
        containers = {1: c1b.empty(), 2: c2b.empty(), 3: c3b.empty(), 4: c4b.empty()}
        labels = {1: "Introduction", 2: "Plan H2/H3", 3: "Corps", 4: "Méta + Révision"}
        icons  = {"pending":"⬜", "running":"🔄", "done":"✅", "error":"❌"}
        colors = {"pending":"#8b949e","running":"#58a6ff","done":"#3fb950","error":"#f85149"}

        running_cost = [0.0]

        def render_pass_card(n, state, detail="", cost_delta=0.0):
            running_cost[0] += cost_delta
            containers[n].markdown(
                f'<div class="pass-card">'
                f'<div class="pass-label">Passe {n}</div>'
                f'<div class="pass-status">{icons[state]}</div>'
                f'<div style="color:{colors[state]};font-size:13px;font-weight:600">{labels[n]}</div>'
                f'<div class="pass-detail">{detail}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            cost_placeholder.markdown(
                f'<div style="text-align:right;margin-bottom:8px">'
                f'<span class="cost-badge">💰 Coût en cours : {format_usd(running_cost[0])}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        for i in range(1, 5):
            render_pass_card(i, "pending")

        # ── Style profile ─────────────────────────────────────────────────────
        progress_bar.progress(5, text="🎨 Style profile…")
        with st.status("🎨 Analyse du style éditorial", expanded=False) as s1:
            try:
                from tone_analyzer import build_style_profile, style_profile_to_system_context
                profile_data, sp_in, sp_out = build_style_profile(site_url, force_refresh=refresh_style)
                style_ctx = style_profile_to_system_context(profile_data)
                if sp_in:
                    running_cost[0] += PassCost(config.CLAUDE_OPUS, sp_in, sp_out).usd
                st.write(f"✅ {len(profile_data)} attributs extraits {'(cache)' if not sp_in else '(nouveau)'}")
                s1.update(label="🎨 Style profile — ✅", state="complete")
            except Exception as e:
                s1.update(label=f"🎨 Style profile — ❌", state="error")
                st.error(str(e)); st.stop()

        # ── SEO intelligence ──────────────────────────────────────────────────
        progress_bar.progress(18, text="📊 SEO intelligence…")
        with st.status("📊 Données SEO", expanded=False) as s2:
            try:
                from seo_intelligence import gather_seo_intelligence, seo_intel_to_brief
                intel     = gather_seo_intelligence(keyword)
                seo_brief = seo_intel_to_brief(intel)
                st.write(f"✅ {len(intel.serp_top10)} SERP · {len(intel.paa_questions)} PAA · "
                         f"{len(intel.keyword_cluster.secondary)} KW secondaires")
                if intel.cannibalisation_risk:
                    st.write(f"⚠️ {len(intel.cannibalisation_risk)} risques cannibalisation")
                s2.update(label="📊 SEO — ✅", state="complete")
            except Exception as e:
                st.write(f"⚠️ SEO partiel : {e}")
                s2.update(label="📊 SEO — ⚠️ partiel", state="complete")
                from seo_intelligence import SEOIntelligence, KeywordCluster, seo_intel_to_brief
                intel     = SEOIntelligence(keyword=keyword, keyword_cluster=KeywordCluster(primary=keyword))
                seo_brief = seo_intel_to_brief(intel)

        # ── Writing 4 passes ──────────────────────────────────────────────────
        from writer import (
            _build_system, _call_claude,
            PASS1_PROMPT, PASS2_PROMPT, PASS3_PROMPT, PASS4_PROMPT,
            ArticleOutput,
        )
        article        = ArticleOutput(keyword=keyword, site_url=site_url)
        system         = _build_system(style_ctx, seo_brief)

        def _run_pass(n, prompt, pct, msg):
            render_pass_card(n, "running")
            progress_bar.progress(pct, text=msg)
            text, in_t, out_t = _call_claude(system, prompt)
            delta = PassCost(config.CLAUDE_SONNET, in_t, out_t).usd
            article.cost.passes.append(PassCost(config.CLAUDE_SONNET, in_t, out_t))
            return text, delta

        try:
            text, d = _run_pass(1, PASS1_PROMPT.format(keyword=keyword), 35, "✍️ Passe 1…")
            article.introduction = text
            render_pass_card(1, "done", f"{_count_words(text)} mots", d)
        except Exception as e:
            render_pass_card(1, "error", str(e)[:35]); st.stop()

        try:
            text, d = _run_pass(2, PASS2_PROMPT.format(pass1_output=article.introduction), 52, "🗂️ Passe 2…")
            article.plan_h2_h3 = text
            render_pass_card(2, "done", f"{text.count('##')} sections", d)
        except Exception as e:
            render_pass_card(2, "error", str(e)[:35]); st.stop()

        try:
            text, d = _run_pass(3, PASS3_PROMPT.format(
                pass1_output=article.introduction,
                pass2_output=article.plan_h2_h3,
                target_word_count=config.TARGET_WORD_COUNT), 68, "📝 Passe 3…")
            article.body = text
            render_pass_card(3, "done", f"{_count_words(text)} mots", d)
        except Exception as e:
            render_pass_card(3, "error", str(e)[:35]); st.stop()

        full_draft = f"{article.introduction}\n\n{article.body}"
        try:
            raw, d = _run_pass(4, PASS4_PROMPT.format(full_draft=full_draft), 85, "🔍 Passe 4…")
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```")
            p4    = _json.loads(clean)
            article.meta_title       = p4.get("meta_title", "")
            article.meta_description = p4.get("meta_description", "")
            revised                  = p4.get("revised_article", full_draft)
            article.full_article     = f"{revised}\n\n{p4.get('cta_final','')}".strip()
            render_pass_card(4, "done", f"{len(article.meta_title)} car.", d)
        except Exception as e:
            render_pass_card(4, "error", str(e)[:35])
            article.full_article = full_draft

        article.cost.dataforseo_tasks = 3 if config.DATAFORSEO_LOGIN else 0
        progress_bar.progress(100, text="✅ Terminé !")

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
