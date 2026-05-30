"""
app.py — Interface Streamlit du Content Agent
─────────────────────────────────────────────
Lancement : streamlit run app.py
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from io import StringIO

import streamlit as st

import config

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Content Agent",
    page_icon="✍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Logging → Streamlit ───────────────────────────────────────────────────────
log_stream = StringIO()
logging.basicConfig(stream=log_stream, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .stApp { background: #0f1117; }
    .metric-card {
        background: #1e2130;
        border: 1px solid #2d3250;
        border-radius: 12px;
        padding: 16px 20px;
        margin: 4px 0;
    }
    .metric-card .label { color: #8892b0; font-size: 12px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }
    .metric-card .value { color: #cdd6f4; font-size: 22px; font-weight: 700; margin-top: 4px; }
    .pass-badge {
        display: inline-block;
        background: #313244;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 12px;
        font-weight: 600;
        color: #89b4fa;
        margin-right: 6px;
    }
    .section-title {
        color: #89b4fa;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        margin-bottom: 8px;
        border-bottom: 1px solid #2d3250;
        padding-bottom: 6px;
    }
    div[data-testid="stDownloadButton"] button {
        background: #89b4fa !important;
        color: #1e1e2e !important;
        font-weight: 700 !important;
        border: none !important;
    }
    div[data-testid="stDownloadButton"] button:hover {
        background: #b4befe !important;
    }
    .stTextInput > div > div > input {
        background: #1e2130 !important;
        border: 1px solid #2d3250 !important;
        color: #cdd6f4 !important;
    }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api_status(label: str, value: str) -> str:
    ok  = "🟢" if value else "🔴"
    val = f"`{value[:8]}…`" if value else "non configurée"
    return f"{ok} **{label}** — {val}"


def _load_style_profile() -> dict | None:
    if os.path.exists(config.STYLE_PROFILE_CACHE):
        with open(config.STYLE_PROFILE_CACHE) as f:
            return json.load(f)
    return None


def _slugify(text: str) -> str:
    text = text.lower().strip()
    for src, dst in [("àáâä","a"),("èéêë","e"),("ìíîï","i"),("òóôö","o"),("ùúûü","u"),("ç","c")]:
        for c in src:
            text = text.replace(c, dst)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _format_md(article) -> str:
    from writer import format_final_output
    return format_final_output(article)


def _count_words(text: str) -> int:
    return len(text.split())


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ✍️ Content Agent")
    st.markdown("---")

    st.markdown('<div class="section-title">Statut des APIs</div>', unsafe_allow_html=True)
    st.markdown(_api_status("Anthropic", config.ANTHROPIC_API_KEY))
    st.markdown(_api_status("DataForSEO", config.DATAFORSEO_LOGIN))
    st.markdown(_api_status("Firecrawl", config.FIRECRAWL_API_KEY))
    gsc_ok = os.path.exists(config.GSC_CREDENTIALS_FILE)
    st.markdown(f"{'🟢' if gsc_ok else '🟡'} **GSC** — {'credentials OK' if gsc_ok else 'optionnel'}")

    st.markdown("---")
    st.markdown('<div class="section-title">Style Profile</div>', unsafe_allow_html=True)

    profile = _load_style_profile()
    if profile:
        st.success("✅ En cache — prêt à l'emploi")
        mtime = os.path.getmtime(config.STYLE_PROFILE_CACHE)
        st.caption(f"Généré le {datetime.fromtimestamp(mtime).strftime('%d/%m/%Y à %H:%M')}")
        if st.button("🔄 Reconstruire le style profile", use_container_width=True):
            st.session_state["force_refresh"] = True
            st.rerun()
    else:
        st.warning("⚠️ Aucun cache — sera créé au premier lancement")
        st.caption(f"Site cible : `{config.TARGET_SITE_URL}`")

    st.markdown("---")
    st.markdown('<div class="section-title">Paramètres</div>', unsafe_allow_html=True)
    st.caption(f"Modèle rédaction : `{config.CLAUDE_SONNET}`")
    st.caption(f"Modèle tone : `{config.CLAUDE_OPUS}`")
    st.caption(f"Objectif : `{config.TARGET_WORD_COUNT}` mots")
    st.caption(f"Site : `{config.TARGET_SITE_URL}`")


# ── Main area ─────────────────────────────────────────────────────────────────

st.markdown("# ✍️ Content Agent")
st.markdown("Génère un article SEO optimisé dans le style éditorial du site cible, en 4 passes.")

tab_generate, tab_profile, tab_history = st.tabs(
    ["🚀 Générer", "🎨 Style Profile", "📂 Historique"]
)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Générer
# ════════════════════════════════════════════════════════════════════════════════

with tab_generate:
    col_input, col_options = st.columns([3, 1])

    with col_input:
        keyword = st.text_input(
            "Mot-clé principal",
            placeholder="ex : rénovation cuisine Bruxelles",
            label_visibility="collapsed",
        )

    with col_options:
        force_refresh = st.session_state.pop("force_refresh", False)
        refresh_cb = st.checkbox("Reconstruire style profile", value=force_refresh)

    launch = st.button(
        "⚡ Générer l'article",
        type="primary",
        use_container_width=True,
        disabled=not keyword.strip(),
    )

    if not keyword.strip():
        st.info("👆 Saisis un mot-clé pour commencer.")

    # ── Pipeline ──────────────────────────────────────────────────────────────
    if launch and keyword.strip():
        st.markdown("---")

        # Check mandatory keys
        missing = []
        if not config.ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
        if not config.DATAFORSEO_LOGIN:
            missing.append("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD")
        if missing:
            st.error(f"❌ Clés API manquantes : `{', '.join(missing)}` — configure le fichier `.env`")
            st.stop()

        # ── Progress layout ───────────────────────────────────────────────────
        progress_bar   = st.progress(0, text="Initialisation…")
        col1, col2, col3, col4 = st.columns(4)
        pass_containers = {
            1: col1.empty(),
            2: col2.empty(),
            3: col3.empty(),
            4: col4.empty(),
        }

        def render_pass(n: int, state: str, detail: str = ""):
            icons   = {"pending": "⬜", "running": "🔄", "done": "✅", "error": "❌"}
            colors  = {"pending": "#555", "running": "#89b4fa", "done": "#a6e3a1", "error": "#f38ba8"}
            icon    = icons.get(state, "⬜")
            color   = colors.get(state, "#555")
            labels  = {1: "Introduction", 2: "Plan H2/H3", 3: "Corps", 4: "Méta + Révision"}
            pass_containers[n].markdown(
                f"""<div class="metric-card">
                    <div class="label">Passe {n}</div>
                    <div class="value" style="font-size:16px; color:{color};">{icon} {labels[n]}</div>
                    <div style="color:#555; font-size:11px; margin-top:4px;">{detail}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        for i in range(1, 5):
            render_pass(i, "pending")

        result_placeholder = st.empty()
        log_placeholder    = st.empty()

        # ── Step 1 : Style Profile ────────────────────────────────────────────
        progress_bar.progress(5, text="🎨 Construction du style profile…")

        with st.status("🎨 Style Profile", expanded=False) as style_status:
            try:
                from tone_analyzer import build_style_profile, style_profile_to_system_context
                style_profile_data = build_style_profile(force_refresh=refresh_cb)
                style_context      = style_profile_to_system_context(style_profile_data)
                st.write(f"✅ Style profile prêt — {len(style_profile_data)} attributs")
                style_status.update(label="🎨 Style Profile — ✅", state="complete")
            except Exception as e:
                st.write(f"❌ Erreur : {e}")
                style_status.update(label="🎨 Style Profile — ❌", state="error")
                st.error(f"Impossible de construire le style profile : {e}")
                st.stop()

        # ── Step 2 : SEO Intelligence ─────────────────────────────────────────
        progress_bar.progress(20, text="📊 Collecte des données SEO…")

        with st.status("📊 SEO Intelligence", expanded=False) as seo_status:
            try:
                from seo_intelligence import gather_seo_intelligence, seo_intel_to_brief
                intel     = gather_seo_intelligence(keyword)
                seo_brief = seo_intel_to_brief(intel)
                st.write(f"✅ {len(intel.serp_top10)} résultats SERP")
                st.write(f"✅ {len(intel.paa_questions)} questions PAA")
                st.write(f"✅ {len(intel.keyword_cluster.secondary)} mots-clés secondaires")
                if intel.cannibalisation_risk:
                    st.write(f"⚠️ {len(intel.cannibalisation_risk)} risques de cannibalisation détectés")
                seo_status.update(label="📊 SEO Intelligence — ✅", state="complete")
            except Exception as e:
                st.write(f"⚠️ SEO partiel : {e}")
                seo_status.update(label="📊 SEO Intelligence — ⚠️ partiel", state="complete")
                from seo_intelligence import SEOIntelligence, KeywordCluster, seo_intel_to_brief
                intel = SEOIntelligence(keyword=keyword, keyword_cluster=KeywordCluster(primary=keyword))
                seo_brief = seo_intel_to_brief(intel)

        # ── Step 3 : Writing passes ───────────────────────────────────────────
        import anthropic as _anthropic

        client = _anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        from writer import (
            _build_system, _call_claude,
            PASS1_PROMPT, PASS2_PROMPT, PASS3_PROMPT, PASS4_PROMPT,
            ArticleOutput,
        )
        import json as _json

        article = ArticleOutput(keyword=keyword)
        system  = _build_system(style_context, seo_brief)

        # Pass 1
        render_pass(1, "running", "Rédaction en cours…")
        progress_bar.progress(35, text="✍️ Passe 1 — Introduction…")
        try:
            article.introduction = _call_claude(system, PASS1_PROMPT.format(keyword=keyword))
            w1 = _count_words(article.introduction)
            render_pass(1, "done", f"{w1} mots")
            article.pass_logs.append(f"PASS1 OK — {w1} mots")
        except Exception as e:
            render_pass(1, "error", str(e)[:40])
            st.error(f"Passe 1 échouée : {e}")
            st.stop()

        # Pass 2
        render_pass(2, "running", "Structuration en cours…")
        progress_bar.progress(52, text="🗂️ Passe 2 — Plan H2/H3…")
        try:
            article.plan_h2_h3 = _call_claude(
                system, PASS2_PROMPT.format(pass1_output=article.introduction)
            )
            sections = article.plan_h2_h3.count("##")
            render_pass(2, "done", f"{sections} sections")
            article.pass_logs.append(f"PASS2 OK — {sections} sections")
        except Exception as e:
            render_pass(2, "error", str(e)[:40])
            st.error(f"Passe 2 échouée : {e}")
            st.stop()

        # Pass 3
        render_pass(3, "running", "Rédaction en cours…")
        progress_bar.progress(68, text="📝 Passe 3 — Corps de l'article…")
        try:
            article.body = _call_claude(
                system,
                PASS3_PROMPT.format(
                    pass1_output      = article.introduction,
                    pass2_output      = article.plan_h2_h3,
                    target_word_count = config.TARGET_WORD_COUNT,
                ),
            )
            w3 = _count_words(article.body)
            render_pass(3, "done", f"{w3} mots")
            article.pass_logs.append(f"PASS3 OK — {w3} mots")
        except Exception as e:
            render_pass(3, "error", str(e)[:40])
            st.error(f"Passe 3 échouée : {e}")
            st.stop()

        # Pass 4
        render_pass(4, "running", "Révision en cours…")
        progress_bar.progress(85, text="🔍 Passe 4 — Méta + révision finale…")
        full_draft = f"{article.introduction}\n\n{article.body}"
        try:
            raw_p4 = _call_claude(system, PASS4_PROMPT.format(full_draft=full_draft))
            raw_p4_clean = raw_p4.strip().lstrip("```json").lstrip("```").rstrip("```")
            p4_data             = _json.loads(raw_p4_clean)
            article.meta_title       = p4_data.get("meta_title", "")
            article.meta_description = p4_data.get("meta_description", "")
            revised                  = p4_data.get("revised_article", full_draft)
            cta                      = p4_data.get("cta_final", "")
            article.full_article     = f"{revised}\n\n{cta}".strip()
            render_pass(4, "done", f"{len(article.meta_title)} car. titre")
            article.pass_logs.append("PASS4 OK")
        except Exception as e:
            render_pass(4, "error", str(e)[:40])
            article.full_article = full_draft
            article.pass_logs.append(f"PASS4 partiel : {e}")

        progress_bar.progress(100, text="✅ Article généré !")

        # ── Save outputs ──────────────────────────────────────────────────────
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        slug = _slugify(keyword)
        ts   = datetime.now().strftime("%Y%m%d_%H%M")
        base = os.path.join(config.OUTPUT_DIR, f"{slug}_{ts}")

        md_content = _format_md(article)
        with open(f"{base}.md", "w", encoding="utf-8") as f:
            f.write(md_content)

        bundle = {
            "keyword": article.keyword,
            "meta_title": article.meta_title,
            "meta_description": article.meta_description,
            "plan": article.plan_h2_h3,
            "full_article": article.full_article,
            "word_count": _count_words(article.full_article),
            "pass_logs": article.pass_logs,
            "generated_at": datetime.now().isoformat(),
            "seo": {
                "secondary_keywords": intel.keyword_cluster.secondary,
                "paa": intel.paa_questions,
                "cannibalisations": [p.url for p in intel.cannibalisation_risk],
            },
        }
        json_content = _json.dumps(bundle, ensure_ascii=False, indent=2)
        with open(f"{base}.json", "w", encoding="utf-8") as f:
            f.write(json_content)

        st.session_state["last_article"] = bundle
        st.session_state["last_md"]      = md_content
        st.session_state["last_json"]    = json_content

        # ── Results display ───────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("## 📄 Résultats")

        mc1, mc2, mc3 = st.columns(3)
        mc1.markdown(
            f'<div class="metric-card"><div class="label">Mots</div>'
            f'<div class="value">{bundle["word_count"]}</div></div>',
            unsafe_allow_html=True,
        )
        mc2.markdown(
            f'<div class="metric-card"><div class="label">Méta-title</div>'
            f'<div class="value" style="font-size:14px;">{article.meta_title or "—"}</div></div>',
            unsafe_allow_html=True,
        )
        mc3.markdown(
            f'<div class="metric-card"><div class="label">Méta-description</div>'
            f'<div class="value" style="font-size:12px;">{article.meta_description or "—"}</div></div>',
            unsafe_allow_html=True,
        )

        st.markdown("### Plan H2/H3")
        st.code(article.plan_h2_h3, language="markdown")

        st.markdown("### Article complet")
        st.markdown(article.full_article)

        st.markdown("---")
        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "⬇️ Télécharger .md",
                data      = md_content,
                file_name = f"{slug}_{ts}.md",
                mime      = "text/markdown",
                use_container_width=True,
            )
        with dl2:
            st.download_button(
                "⬇️ Télécharger .json",
                data      = json_content,
                file_name = f"{slug}_{ts}.json",
                mime      = "application/json",
                use_container_width=True,
            )

        if intel.cannibalisation_risk:
            st.markdown("---")
            st.warning(f"⚠️ **{len(intel.cannibalisation_risk)} pages existantes à risque de cannibalisation**")
            for p in intel.cannibalisation_risk:
                st.markdown(f"- `{p.url}` — position {p.avg_position} sur *{p.top_query}*")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Style Profile
# ════════════════════════════════════════════════════════════════════════════════

with tab_profile:
    st.markdown("## 🎨 Style Profile — Ton éditorial")
    profile = _load_style_profile()

    if not profile:
        st.info(
            "Aucun style profile en cache. Lance une première génération "
            "ou clique sur **Reconstruire** dans la sidebar."
        )
    else:
        mtime = os.path.getmtime(config.STYLE_PROFILE_CACHE)
        st.caption(f"Généré le {datetime.fromtimestamp(mtime).strftime('%d/%m/%Y à %H:%M')}"
                   f" depuis `{config.TARGET_SITE_URL}`")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Tonalité**")
            for t in profile.get("tonality", []):
                st.markdown(f"- {t}")

            st.markdown("**Vocabulaire privilégié**")
            for v in profile.get("preferred_vocabulary", []):
                st.markdown(f"- {v}")

            st.markdown("**Point de vue**")
            st.info(profile.get("pov", "—"))

        with col_b:
            st.markdown("**Patterns récurrents**")
            for p in profile.get("recurring_patterns", []):
                st.markdown(f"- {p}")

            st.markdown("**Vocabulaire à éviter**")
            for v in profile.get("avoided_vocabulary", []):
                st.markdown(f"- ~~{v}~~")

            st.markdown("**Style CTA**")
            st.info(profile.get("cta_style", "—"))

        with st.expander("🔍 JSON brut du style profile"):
            st.json(profile)

        col_rebuild, _ = st.columns([1, 2])
        with col_rebuild:
            if st.button("🔄 Reconstruire depuis le site", use_container_width=True):
                with st.spinner("Scraping + analyse en cours…"):
                    try:
                        from tone_analyzer import build_style_profile
                        new_profile = build_style_profile(force_refresh=True)
                        st.success("Style profile reconstruit !")
                        st.json(new_profile)
                    except Exception as e:
                        st.error(f"Erreur : {e}")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Historique
# ════════════════════════════════════════════════════════════════════════════════

with tab_history:
    st.markdown("## 📂 Articles générés")

    out_dir = config.OUTPUT_DIR
    if not os.path.exists(out_dir):
        st.info("Aucun article généré pour l'instant.")
    else:
        json_files = sorted(
            [f for f in os.listdir(out_dir) if f.endswith(".json")],
            reverse=True,
        )
        if not json_files:
            st.info("Aucun article généré pour l'instant.")
        else:
            for jf in json_files:
                path = os.path.join(out_dir, jf)
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)

                with st.expander(
                    f"**{data.get('keyword', jf)}** — {data.get('word_count', '?')} mots"
                    f" · {data.get('generated_at', '')[:16].replace('T', ' à ')}"
                ):
                    st.markdown(f"**Meta title :** {data.get('meta_title', '—')}")
                    st.markdown(f"**Meta desc :** {data.get('meta_description', '—')}")

                    md_path = path.replace(".json", ".md")
                    if os.path.exists(md_path):
                        with open(md_path, encoding="utf-8") as mf:
                            md_content = mf.read()
                        c1, c2 = st.columns(2)
                        with c1:
                            st.download_button(
                                "⬇️ .md",
                                data=md_content,
                                file_name=jf.replace(".json", ".md"),
                                mime="text/markdown",
                                key=f"dl_md_{jf}",
                            )
                        with c2:
                            st.download_button(
                                "⬇️ .json",
                                data=json.dumps(data, ensure_ascii=False, indent=2),
                                file_name=jf,
                                mime="application/json",
                                key=f"dl_json_{jf}",
                            )
