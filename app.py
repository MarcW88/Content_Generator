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
# PAGE — GÉNÉRER  (machine à états — validation manuelle optionnelle)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "generate":
    import json as _json

    # ── Session state ─────────────────────────────────────────────────────────
    if "pl" not in st.session_state:
        st.session_state.pl = None
    pl = st.session_state.pl

    STEPS = [
        ("Style",   "🎨", "Analyse du ton éditorial"),
        ("SEO",     "📊", "Données SERP / PAA"),
        ("Passe 1", "✏️",  "Introduction"),
        ("Passe 2", "🗂️",  "Plan H2/H3"),
        ("Passe 3", "📝", "Corps de l'article"),
        ("Passe 4", "🔍", "Méta + Révision"),
    ]
    PROGRESS = [5, 16, 35, 55, 72, 88]

    def _stepper_html(states, details, step_costs):
        icon_map = {"pending": "○", "running": "◉", "done": "✓", "error": "✗",
                    "waiting": "⏸"}
        css_map  = {"pending": "", "running": " running", "done": " done",
                    "error": " error", "waiting": " running"}
        boxes = ""
        for i, (short, emoji, name) in enumerate(STEPS):
            ic     = icon_map[states[i]]
            cls    = css_map[states[i]]
            cost_s = f'<div class="step-cost">{format_usd(step_costs[i])}</div>' \
                     if step_costs[i] > 0 else ""
            boxes += (
                f'<div class="step-box{cls}">'
                f'<div class="step-num">{short} {ic}</div>'
                f'<div class="step-icon">{emoji}</div>'
                f'<div class="step-name">{name}</div>'
                f'<div class="step-detail">{details[i]}</div>'
                f'{cost_s}</div>'
            )
        return (
            f'<div class="pipeline-wrapper">'
            f'<div class="pipeline-title">Progression de la génération</div>'
            f'<div class="step-row">{boxes}</div>'
            f'</div>'
        )

    # ── FORM — aucun pipeline actif ───────────────────────────────────────────
    if not pl or not pl.get("active"):
        st.markdown("## ✍️ Nouveau contenu")

        col_a, col_b = st.columns(2)
        with col_a:
            site_url = st.text_input("Site cible", placeholder="https://www.monsite.com",
                                     key="gen_site_url")
        with col_b:
            keyword = st.text_input("Mot-clé principal",
                                    placeholder="rénovation cuisine Bruxelles",
                                    key="gen_keyword")

        opt1, opt2 = st.columns(2)
        with opt1:
            manual = st.checkbox(
                "✋ Validation manuelle entre chaque étape",
                value=True,
                help="Si coché, l'agent s'arrête après chaque étape pour que tu puisses relire et valider avant de continuer.",
            )
        with opt2:
            refresh_style = st.checkbox("🔄 Forcer rebuild style profile", value=False)

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
            disabled=not bool(site_url and keyword),
        )

        if not (site_url and keyword):
            st.caption("Remplis le site cible et le mot-clé pour continuer.")

        if launch and site_url and keyword:
            if not config.ANTHROPIC_API_KEY:
                st.error("❌ `ANTHROPIC_API_KEY` manquante.")
                st.stop()
            st.session_state.pl = {
                "active":        True,
                "stopped":       False,
                "waiting":       False,  # en attente de validation manuelle
                "manual":        manual,
                "step":          0,      # 0-5 = étape courante, 6 = terminé
                "keyword":       keyword,
                "site_url":      site_url,
                "refresh_style": refresh_style,
                # outputs
                "style_ctx":     None,
                "style_profile": None,
                "seo_brief":     None,
                "intel_paa":     [],
                "intel_secondary": [],
                "intel_cannib":  [],
                "introduction":  None,
                "plan":          None,
                "body":          None,
                "full_article":  None,
                "meta_title":    "",
                "meta_description": "",
                "system_prompt": None,
                # stepper display
                "states":        ["pending"] * 6,
                "details":       [""] * 6,
                "step_costs":    [0.0] * 6,
                "total_cost":    0.0,
                # pass costs for final export
                "pass_costs":    [],  # list of [model, in_t, out_t]
            }
            st.rerun()

    # ── PIPELINE ACTIF ────────────────────────────────────────────────────────
    if pl and pl.get("active"):
        st.markdown(f"## ✍️ Génération — *{pl['keyword']}*")
        st.caption(f"🌐 {pl['site_url']}   ·   {'✋ Validation manuelle activée' if pl['manual'] else '⚡ Mode automatique'}")

        stepper_ph   = st.empty()
        progress_ph  = st.empty()
        cost_ph      = st.empty()
        stepper_ph.markdown(_stepper_html(pl["states"], pl["details"], pl["step_costs"]),
                            unsafe_allow_html=True)
        progress_ph.progress(PROGRESS[min(pl["step"], 5)] if pl["step"] < 6 else 100)
        cost_ph.markdown(
            f'<div style="text-align:right;margin:-4px 0 16px">'
            f'<span class="cost-badge">💰 Coût en cours : {format_usd(pl["total_cost"])}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ─────────────────────────────────────────────────────────────────────
        # Helper : UI de validation (appelé depuis 2 endroits)
        # ─────────────────────────────────────────────────────────────────────
        def _show_validation():
            step_done = pl["step"] - 1
            st.divider()
            st.markdown(
                f'<div style="background:#eff3ff;border:2px solid #4f6ef7;border-radius:12px;'
                f'padding:18px 24px;margin:8px 0 20px">'
                f'<div style="font-size:14px;font-weight:700;color:#4f6ef7;margin-bottom:4px">'
                f'⏸ Validation requise — {STEPS[step_done][2]}</div>'
                f'<div style="font-size:13px;color:#374151">'
                f'Relis le résultat ci-dessous puis choisis de continuer ou d\'arrêter.</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Aperçu selon l'étape terminée
            if step_done == 0 and pl["style_profile"]:
                p = pl["style_profile"]
                c1, c2 = st.columns(2)
                c1.markdown("**Tonalité :** " + ", ".join(p.get("tonality", [])))
                c1.markdown("**POV :** " + p.get("pov", "—"))
                c2.markdown("**Patterns :** " + ", ".join(p.get("recurring_patterns", [])[:4]))
                c2.markdown("**Vocab. évité :** " + ", ".join(p.get("avoided_vocabulary", [])[:4]))
                with st.expander("Voir le style profile complet"):
                    st.json(p)
            elif step_done == 1:
                if pl["intel_paa"]:
                    st.markdown("**Questions PAA extraites :**")
                    for q in pl["intel_paa"][:6]:
                        st.caption(f"• {q}")
                if pl["intel_cannib"]:
                    st.warning(f"⚠️ {len(pl['intel_cannib'])} risque(s) cannibalisation")
            elif step_done == 2 and pl["introduction"]:
                with st.expander("Lire l'introduction", expanded=True):
                    st.markdown(pl["introduction"])
            elif step_done == 3 and pl["plan"]:
                st.code(pl["plan"], language="markdown")
            elif step_done == 4 and pl["body"]:
                st.caption(f"{_count_words(pl['body'])} mots")
                with st.expander("Lire le corps de l'article", expanded=False):
                    st.markdown(pl["body"])
            elif step_done == 5:
                st.markdown(f"**🏷️ Meta title :** {pl['meta_title']}")
                st.markdown(f"**📝 Meta description :** {pl['meta_description']}")
                with st.expander("Article révisé complet", expanded=False):
                    st.markdown(pl["full_article"] or "")

            # Boutons de décision
            st.markdown("")
            next_label = "Enregistrer l'article ✅" if step_done == 5 \
                         else f"✅ Valider → {STEPS[step_done + 1][2]}"
            btn_ok, btn_stop = st.columns(2)
            with btn_ok:
                if st.button(next_label, type="primary",
                             use_container_width=True, key=f"val_ok_{step_done}"):
                    pl["waiting"] = False
                    st.rerun()
            with btn_stop:
                if st.button("⛔ Arrêter", use_container_width=True,
                             key=f"val_stop_{step_done}"):
                    pl["stopped"] = True
                    pl["active"]  = False
                    st.rerun()

        # ── Arrêt demandé ────────────────────────────────────────────────────
        if pl["stopped"]:
            st.warning("⛔ Génération arrêtée à l'étape **" +
                       STEPS[min(pl["step"], 5)][2] + "**.")
            if pl["total_cost"] > 0:
                st.info(f"💰 Coût consommé : {format_usd(pl['total_cost'])}")
            if st.button("↩️ Nouvelle génération", type="primary"):
                st.session_state.pl = None
                st.rerun()

        # ── En attente de validation (reload page / retour arrière) ──────────
        elif pl["waiting"]:
            # Cas où la page est rechargée pendant l'attente
            _show_validation()

        # ── EXÉCUTION de l'étape courante ────────────────────────────────────
        elif pl["step"] < 6:
            s = pl["step"]
            pl["states"][s] = "running"
            pl["details"][s] = "En cours…"
            stepper_ph.markdown(_stepper_html(pl["states"], pl["details"], pl["step_costs"]),
                                unsafe_allow_html=True)

            with st.status(f"{STEPS[s][1]} Étape {s+1}/6 — {STEPS[s][2]}", expanded=True) as status:

                try:
                    if s == 0:
                        from tone_analyzer import (build_style_profile,
                                                   style_profile_to_system_context,
                                                   profile_cache_exists)
                        cached = profile_cache_exists(pl["site_url"])
                        if cached and not pl["refresh_style"]:
                            st.write(f"✅ Style profile en cache pour {pl['site_url']}")
                        else:
                            st.write(f"🌐 Scraping de {pl['site_url']} …")
                            if config.FIRECRAWL_API_KEY:
                                st.write("🔑 Firecrawl API détectée")
                            else:
                                st.write("⚠️ Firecrawl non configuré — fallback BeautifulSoup")
                        profile_data, sp_in, sp_out = build_style_profile(
                            pl["site_url"], force_refresh=pl["refresh_style"])
                        style_ctx = style_profile_to_system_context(profile_data)
                        sp_cost   = PassCost(config.CLAUDE_OPUS, sp_in, sp_out).usd if sp_in else 0
                        pl["style_profile"] = profile_data
                        pl["style_ctx"]     = style_ctx
                        st.write(f"✅ {len(profile_data)} attributs extraits "
                                 f"{'(cache)' if not sp_in else f'— {sp_in:,} tokens input'}")
                        detail = "cache" if not sp_in else f"{sp_in:,} tok"
                        _cost  = sp_cost

                    elif s == 1:
                        from seo_intelligence import gather_seo_intelligence, seo_intel_to_brief

                        if config.DATAFORSEO_LOGIN:
                            st.write(f"🔑 DataForSEO login : `{config.DATAFORSEO_LOGIN[:20]}…`")
                        else:
                            st.warning("⚠️ DATAFORSEO_LOGIN manquant — données SEO ignorées")

                        intel     = gather_seo_intelligence(pl["keyword"])
                        seo_brief = seo_intel_to_brief(intel)

                        # Affiche les erreurs réelles si présentes
                        for err in intel.errors:
                            st.error(f"❌ {err}")

                        st.write(f"{len(intel.serp_top10)} SERP · "
                                 f"{len(intel.paa_questions)} PAA · "
                                 f"{len(intel.keyword_cluster.secondary)} KW sec.")
                        if intel.cannibalisation_risk:
                            st.warning(f"⚠️ {len(intel.cannibalisation_risk)} risques cannibalisation")

                        pl["intel_paa"]       = intel.paa_questions
                        pl["intel_secondary"] = intel.keyword_cluster.secondary
                        pl["intel_cannib"]    = [p.url for p in intel.cannibalisation_risk]
                        pl["seo_brief"]       = seo_brief
                        detail = f"{len(pl['intel_paa'])} PAA" if pl["intel_paa"] else "⚠️ partiel"
                        _cost  = 0.0

                    elif s in (2, 3, 4, 5):
                        from writer import (
                            _build_system, _call_claude,
                            PASS1_PROMPT, PASS2_PROMPT, PASS3_PROMPT, PASS4_PROMPT,
                        )
                        if pl["system_prompt"] is None:
                            pl["system_prompt"] = _build_system(pl["style_ctx"], pl["seo_brief"])
                        system = pl["system_prompt"]

                        if s == 2:
                            prompt = PASS1_PROMPT.format(keyword=pl["keyword"])
                        elif s == 3:
                            prompt = PASS2_PROMPT.format(pass1_output=pl["introduction"])
                        elif s == 4:
                            prompt = PASS3_PROMPT.format(
                                pass1_output=pl["introduction"],
                                pass2_output=pl["plan"],
                                target_word_count=config.TARGET_WORD_COUNT,
                            )
                        else:
                            prompt = PASS4_PROMPT.format(
                                full_draft=f"{pl['introduction']}\n\n{pl['body']}")

                        text, in_t, out_t = _call_claude(system, prompt)
                        _cost = PassCost(config.CLAUDE_SONNET, in_t, out_t).usd
                        pl["pass_costs"].append([config.CLAUDE_SONNET, in_t, out_t])
                        st.write(f"Tokens envoyés : {in_t:,}   |   reçus : {out_t:,}")

                        if s == 2:
                            pl["introduction"] = text
                            wc = _count_words(text)
                            st.write(f"Introduction — {wc} mots")
                            detail = f"{wc} mots"
                        elif s == 3:
                            pl["plan"] = text
                            nb = text.count("##")
                            st.write(f"Plan — {nb} sections")
                            detail = f"{nb} sections"
                        elif s == 4:
                            pl["body"] = text
                            wc = _count_words(text)
                            st.write(f"Corps — {wc} mots")
                            detail = f"{wc} mots"
                        elif s == 5:
                            clean = text.strip().lstrip("```json").lstrip("```").rstrip("```")
                            try:
                                p4 = _json.loads(clean)
                                pl["meta_title"]       = p4.get("meta_title", "")
                                pl["meta_description"] = p4.get("meta_description", "")
                                revised = p4.get("revised_article",
                                                 f"{pl['introduction']}\n\n{pl['body']}")
                                pl["full_article"] = f"{revised}\n\n{p4.get('cta_final','')}".strip()
                            except Exception:
                                pl["full_article"] = f"{pl['introduction']}\n\n{pl['body']}"
                            st.write(f"Meta title : {pl['meta_title']}")
                            detail = f"{len(pl['meta_title'])} car."

                    # Mise à jour stepper
                    pl["states"][s]     = "done"
                    pl["details"][s]    = detail
                    pl["step_costs"][s] = _cost
                    pl["total_cost"]   += _cost
                    pl["step"]          = s + 1
                    status.update(label=f"✅ {STEPS[s][2]} — terminé", state="complete", expanded=False)

                except Exception as err:
                    pl["states"][s]  = "error"
                    pl["details"][s] = str(err)[:40]
                    pl["step"]       = s + 1
                    status.update(label=f"❌ {STEPS[s][2]} — erreur", state="error")
                    st.error(str(err))
                    if s not in (1, 5):
                        pl["stopped"] = True
                        pl["active"]  = False

            # Mise à jour stepper final
            stepper_ph.markdown(_stepper_html(pl["states"], pl["details"], pl["step_costs"]),
                                unsafe_allow_html=True)
            cost_ph.markdown(
                f'<div style="text-align:right;margin:-4px 0 16px">'
                f'<span class="cost-badge">💰 Coût en cours : {format_usd(pl["total_cost"])}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if pl["manual"] and not pl["stopped"] and pl["step"] <= 6:
                # ⚠️ Ne pas appeler st.rerun() ici — afficher la validation
                # directement dans le même run pour éviter qu'elle disparaisse.
                pl["waiting"] = True
                _show_validation()
            elif not pl["stopped"]:
                # Mode automatique : passer directement à l'étape suivante
                st.rerun()

        # ── TOUTES LES ÉTAPES FAITES → Sauvegarde & résultats ────────────────
        elif pl["step"] == 6 and not pl["stopped"]:
            progress_ph.progress(100)

            from writer import ArticleOutput, format_final_output
            from cost_tracker import RequestCost

            article              = ArticleOutput(keyword=pl["keyword"], site_url=pl["site_url"])
            article.introduction = pl["introduction"] or ""
            article.plan_h2_h3   = pl["plan"] or ""
            article.body         = pl["body"] or ""
            article.full_article = pl["full_article"] or f"{article.introduction}\n\n{article.body}"
            article.meta_title   = pl["meta_title"]
            article.meta_description = pl["meta_description"]
            for model, in_t, out_t in pl["pass_costs"]:
                article.cost.passes.append(PassCost(model, in_t, out_t))
            article.cost.dataforseo_tasks = 3 if config.DATAFORSEO_LOGIN else 0

            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            slug = _slugify(pl["keyword"])
            ts   = datetime.now().strftime("%Y%m%d_%H%M")
            base = os.path.join(config.OUTPUT_DIR, f"{slug}_{ts}")

            md_content = format_final_output(article)
            with open(f"{base}.md", "w", encoding="utf-8") as f:
                f.write(md_content)

            bundle = {
                "keyword":          pl["keyword"],
                "site_url":         pl["site_url"],
                "meta_title":       pl["meta_title"],
                "meta_description": pl["meta_description"],
                "plan":             pl["plan"],
                "full_article":     pl["full_article"],
                "word_count":       _count_words(pl["full_article"] or ""),
                "pass_logs":        [],
                "generated_at":     datetime.now().isoformat(),
                "cost":             article.cost.to_dict(),
                "seo": {
                    "secondary_keywords": pl["intel_secondary"],
                    "paa":                pl["intel_paa"],
                    "cannibalisations":   pl["intel_cannib"],
                },
            }
            json_content = _json.dumps(bundle, ensure_ascii=False, indent=2)
            with open(f"{base}.json", "w", encoding="utf-8") as f:
                f.write(json_content)

            pl["active"] = False
            st.success(f"✅ Article enregistré — {bundle['word_count']} mots · Coût réel : {format_usd(article.cost.total_usd)}")

            st.markdown('<div class="section-hdr">Résultats</div>', unsafe_allow_html=True)
            r1, r2, r3, r4 = st.columns(4)
            r1.markdown(_kpi("Mots", str(bundle["word_count"])), unsafe_allow_html=True)
            r2.markdown(_kpi("Coût réel", format_usd(article.cost.total_usd)), unsafe_allow_html=True)
            r3.markdown(_kpi("Tokens in", f"{article.cost.total_input_tokens:,}"), unsafe_allow_html=True)
            r4.markdown(_kpi("Tokens out", f"{article.cost.total_output_tokens:,}"), unsafe_allow_html=True)

            st.markdown(f"**🏷️ Meta title :** {pl['meta_title']}")
            st.markdown(f"**📝 Meta description :** {pl['meta_description']}")

            if pl["intel_cannib"]:
                st.warning(f"⚠️ {len(pl['intel_cannib'])} risque(s) de cannibalisation")
                for url in pl["intel_cannib"]:
                    st.caption(f"• {url}")

            with st.expander("📐 Plan H2/H3", expanded=False):
                st.code(pl["plan"], language="markdown")
            with st.expander("📄 Article complet", expanded=True):
                st.markdown(pl["full_article"])

            d1, d2, d3 = st.columns(3)
            d1.download_button("⬇️ .md", md_content, f"{slug}_{ts}.md",
                               "text/markdown", use_container_width=True)
            d2.download_button("⬇️ .json", json_content, f"{slug}_{ts}.json",
                               "application/json", use_container_width=True)
            if d3.button("✍️ Nouvelle génération", use_container_width=True):
                st.session_state.pl = None
                st.rerun()


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

    # ── Test live des APIs ─────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Test des connexions API</div>', unsafe_allow_html=True)
    tc1, tc2 = st.columns(2)

    if tc1.button("🧪 Tester DataForSEO", use_container_width=True):
        import base64, requests as _req
        login = config.DATAFORSEO_LOGIN.strip()
        pwd   = config.DATAFORSEO_PASSWORD.strip()
        if not login or not pwd:
            st.error("❌ DATAFORSEO_LOGIN ou DATAFORSEO_PASSWORD manquant dans les secrets.")
        else:
            encoded = base64.b64encode(f"{login}:{pwd}".encode()).decode()
            st.code(f"Login    : {login}\n"
                    f"Password : {pwd[:4]}{'*' * (len(pwd)-4)}\n"
                    f"Base64   : {encoded[:30]}…")
            try:
                r = _req.post(
                    "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
                    headers={"Authorization": f"Basic {encoded}",
                             "Content-Type": "application/json"},
                    json=[{"keyword": "test", "language_code": "fr",
                           "location_code": 2056, "device": "desktop", "depth": 1}],
                    timeout=15,
                )
                if r.status_code == 200:
                    st.success(f"✅ DataForSEO OK — {r.status_code}")
                else:
                    st.error(f"❌ {r.status_code} — {r.text[:300]}")
            except Exception as e:
                st.error(f"❌ Erreur réseau : {e}")

    if tc2.button("🧪 Tester Anthropic", use_container_width=True):
        try:
            import anthropic as _ant
            c = _ant.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            msg = c.messages.create(
                model=config.CLAUDE_SONNET,
                max_tokens=5,
                messages=[{"role": "user", "content": "hi"}],
            )
            st.success(f"✅ Anthropic OK — {msg.usage.input_tokens} tokens")
        except Exception as e:
            st.error(f"❌ Anthropic : {e}")

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
    st.markdown('<div class="section-hdr">Coût estimé par article</div>', unsafe_allow_html=True)

    st.info(
        "💡 **Ces prix sont en USD par million de tokens (MTok)** — pas par article.\n\n"
        "Un article de ~1 500 mots consomme environ **5 000 à 12 000 tokens** en tout. "
        "Le coût réel tourne entre **$0.10 et $0.35 par article**, selon que le style profile "
        "est déjà en cache ou non.\n\n"
        "Claude Opus n'est utilisé qu'**une seule fois** pour analyser le style du site (puis mis en cache). "
        "Les 4 passes de rédaction utilisent Claude Sonnet (3× moins cher)."
    )

    from cost_tracker import PRICING, DATAFORSEO_COST_PER_TASK, ESTIMATE

    # Coût estimé par poste
    cost_rows = [
        {
            "Poste": "Style profile — Claude Opus (1ère fois seulement, puis cache)",
            "Modèle": "claude-opus-4-5",
            "Tokens estimés": "~12 400 in / 400 out",
            "Coût estimé": format_usd(ESTIMATE["tone_analyzer"].usd),
        },
        {
            "Poste": "Passe 1 — Introduction",
            "Modèle": "claude-sonnet-4-5",
            "Tokens estimés": "~800 in / 200 out",
            "Coût estimé": format_usd(ESTIMATE["pass1"].usd),
        },
        {
            "Poste": "Passe 2 — Plan H2/H3",
            "Modèle": "claude-sonnet-4-5",
            "Tokens estimés": "~1 200 in / 500 out",
            "Coût estimé": format_usd(ESTIMATE["pass2"].usd),
        },
        {
            "Poste": "Passe 3 — Corps de l'article",
            "Modèle": "claude-sonnet-4-5",
            "Tokens estimés": "~2 500 in / 2 000 out",
            "Coût estimé": format_usd(ESTIMATE["pass3"].usd),
        },
        {
            "Poste": "Passe 4 — Méta + Révision",
            "Modèle": "claude-sonnet-4-5",
            "Tokens estimés": "~4 000 in / 2 500 out",
            "Coût estimé": format_usd(ESTIMATE["pass4"].usd),
        },
        {
            "Poste": "DataForSEO (SERP + PAA + clustering)",
            "Modèle": "—",
            "Tokens estimés": "3 tâches × $0.0025",
            "Coût estimé": format_usd(3 * DATAFORSEO_COST_PER_TASK),
        },
    ]

    passes_cost = sum(ESTIMATE[k].usd for k in ("pass1","pass2","pass3","pass4"))
    total_first = ESTIMATE["tone_analyzer"].usd + passes_cost + 3 * DATAFORSEO_COST_PER_TASK
    total_cached = passes_cost + 3 * DATAFORSEO_COST_PER_TASK

    st.dataframe(cost_rows, use_container_width=True, hide_index=True)

    t1, t2 = st.columns(2)
    t1.markdown(
        f'<div class="kpi-card">'
        f'<div class="kpi-label">1er article (style à créer)</div>'
        f'<div class="kpi-value">{format_usd(total_first)}</div>'
        f'<div class="kpi-sub">inclut analyse tonale Claude Opus</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    t2.markdown(
        f'<div class="kpi-card">'
        f'<div class="kpi-label">Articles suivants (style en cache)</div>'
        f'<div class="kpi-value">{format_usd(total_cached)}</div>'
        f'<div class="kpi-sub">4 passes Sonnet + DataForSEO uniquement</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-hdr">Tarifs Anthropic bruts (USD / million tokens)</div>', unsafe_allow_html=True)
    pricing_rows = [
        {"Modèle": m, "Input (USD/MTok)": f"${v['input']}", "Output (USD/MTok)": f"${v['output']}",
         "Explication": "4 passes rédaction" if "sonnet" in m else "Analyse style (1× puis cache)"}
        for m, v in PRICING.items()
    ]
    st.dataframe(pricing_rows, use_container_width=True, hide_index=True)
