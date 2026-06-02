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
from writer import (
    _build_article_system, _build_briefing_system, _build_meta_system, _call_claude,
    BRIEFING_PROMPT, ARTICLE_PROMPT, META_PROMPT,
    generate_chunked_briefing, generate_article_by_sections,
    ArticleOutput, format_final_output
)

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

  /* Nav brand label */
  .nav-brand { font-size: 15px; font-weight: 800; letter-spacing: -.3px;
               padding: 8px 0; display: inline-block; }
  .nav-brand span { color: #0f172a; }

  /* Pipeline stepper — custom HTML component only */
  .pipeline-wrapper { border: 1px solid #e5e7eb; border-radius: 10px;
                      padding: 20px 24px; margin: 8px 0 24px; }
  .pipeline-title   { font-size: 11px; font-weight: 600; color: #9ca3af;
                      text-transform: uppercase; letter-spacing: .08em; margin-bottom: 14px; }
  .step-row         { display: flex; gap: 8px; }
  .step-box         { flex: 1; border: 1px solid #e5e7eb; border-radius: 8px;
                      padding: 12px 8px; text-align: center; }
  .step-box.running { border-color: #0f172a; background: #f8fafc;
                      animation: pulse-step 1.8s ease-in-out infinite; }
  @keyframes pulse-step {
    0%,100% { box-shadow: 0 0 0 2px rgba(15,23,42,.08); }
    50%      { box-shadow: 0 0 0 4px rgba(15,23,42,.04); }
  }
  .step-box.done    { border-color: #86efac; background: #f0fdf4; }
  .step-box.error   { border-color: #fca5a5; background: #fef2f2; }
  .step-num    { font-size: 10px; font-weight: 600; color: #d1d5db;
                 text-transform: uppercase; margin-bottom: 6px; }
  .step-box.running .step-num { color: #0f172a; }
  .step-box.done    .step-num { color: #16a34a; }
  .step-box.error   .step-num { color: #dc2626; }
  .step-icon   { font-size: 20px; margin-bottom: 4px; }
  .step-name   { font-size: 12px; font-weight: 600; margin-bottom: 2px; }
  .step-detail { font-size: 11px; color: #9ca3af; min-height: 14px; }
  .step-cost   { font-size: 11px; color: #16a34a; font-weight: 600; margin-top: 2px; }

  /* Cost badge */
  .cost-badge { display: inline-flex; align-items: center; gap: 4px;
                background: #f0fdf4; border: 1px solid #d1fae5;
                border-radius: 20px; padding: 3px 10px;
                font-size: 12px; font-weight: 600; color: #15803d; }

  /* Section header */
  .section-hdr { font-size: 11px; font-weight: 600; color: #9ca3af;
                 letter-spacing: .08em; text-transform: uppercase;
                 border-bottom: 1px solid #f3f4f6; padding-bottom: 6px;
                 margin: 28px 0 16px; }

  /* API status chips */
  .profile-chip { display: inline-flex; align-items: center; gap: 4px;
                  border: 1px solid #e5e7eb; border-radius: 6px;
                  padding: 4px 10px; font-size: 12px; margin: 2px; }
  .chip-green   { background: #f0fdf4; border-color: #bbf7d0; color: #15803d; }
  .chip-red     { background: #fef2f2; border-color: #fecaca; color: #b91c1c; }
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
    pages = [
        ("dashboard", "Dashboard"),
        ("generate",  "Générer"),
        ("library",   "Bibliothèque"),
        ("settings",  "Paramètres"),
    ]
    brand_col, c1, c2, c3, c4 = st.columns([4, 1, 1, 1, 1])
    brand_col.markdown(
        '<div class="nav-brand">Content<span>Agent</span></div>',
        unsafe_allow_html=True,
    )
    for col, (key, label) in zip([c1, c2, c3, c4], pages):
        active = st.session_state.page == key
        if col.button(label, key=f"nav_{key}", use_container_width=True,
                      type="primary" if active else "secondary"):
            st.session_state.page = key
            st.rerun()
    st.markdown('<hr class="nav-divider">', unsafe_allow_html=True)


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
    c1.metric("Articles générés", total_articles)
    c2.metric("Mots rédigés", f"{total_words:,}".replace(",","'"))
    c3.metric("Coût total", format_usd(total_cost))
    c4.metric("Coût moyen / article", format_usd(avg_cost))

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
        st.info("Aucun article généré. Va dans **Générer** pour commencer.")


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
        ("Style",    "—", "Analyse du ton éditorial"),
        ("SEO",      "—", "Données SERP / PAA"),
        ("Briefing", "—", "Briefing & plan de rédaction"),
        ("Article",  "—", "Rédaction de l'article"),
        ("Métas",    "—", "Métas + révision finale"),
    ]
    PROGRESS = [5, 22, 45, 72, 90]

    def _stepper_html(states, details, step_costs):
        icon_map = {"pending": "·", "running": ">", "done": "ok", "error": "!",
                    "waiting": "~"}
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
        st.markdown("## Nouveau contenu")

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            site_url = st.text_input("Site cible", placeholder="https://www.dogchef.com",
                                     key="gen_site_url")
        with col_b:
            keyword = st.text_input("Mot-clé principal",
                                    placeholder="croquettes sans céréales chien",
                                    key="gen_keyword")
        with col_c:
            country = st.text_input("Pays / marché cible",
                                    placeholder="Belgique",
                                    key="gen_country")
        lang_col, type_col = st.columns(2)
        with lang_col:
            article_lang = st.selectbox(
                "Langue de l'article",
                options=[
                    ("fr", "Français"),
                    ("nl", "Néerlandais"),
                    ("en", "Anglais"),
                ],
                format_func=lambda item: item[1],
                key="gen_article_lang",
            )[0]
        with type_col:
            page_type = st.selectbox(
                "Type de page",
                options=[
                    "Article de blog",
                    "Page pilier",
                    "Guide complet",
                    "Landing page",
                    "FAQ / Questions-réponses",
                    "Page catégorie",
                    "Comparatif",
                    "Page produit",
                ],
                key="gen_page_type",
            )

        opt1, opt2 = st.columns(2)
        with opt1:
            manual = st.checkbox(
                "Validation manuelle entre chaque étape",
                value=True,
                help="Si coché, l'agent s'arrête après chaque étape pour relire et valider.",
            )
        with opt2:
            refresh_style = st.checkbox("Forcer rebuild style profile", value=False)

        doc_col, links_col = st.columns(2)
        with doc_col:
            context_file = st.file_uploader(
                "Document de contexte (optionnel) — PDF, Word ou texte",
                type=["pdf", "docx", "txt"],
                key="gen_context_file",
                help="Brief existant, fiche produit, notes internes… son contenu sera injecté dans le briefing."
            )
        with links_col:
            internal_links_file = st.file_uploader(
                "Export Screaming Frog (optionnel) — CSV maillage interne",
                type=["csv"],
                key="gen_internal_links",
                help="Export 'All Inlinks' de Screaming Frog pour générer des suggestions de maillage."
            )

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
            "Lancer la génération",
            type="primary",
            disabled=not bool(site_url and keyword and country),
        )

        if not (site_url and keyword and country):
            st.caption("Remplis le site cible, le mot-clé et le pays pour continuer.")

        if launch and site_url and keyword and country:
            if not config.ANTHROPIC_API_KEY:
                st.error("ANTHROPIC_API_KEY manquante.")
                st.stop()
            # ── Parse uploaded documents ────────────────────────────────────────
            context_text = ""
            if context_file is not None:
                try:
                    fname = context_file.name.lower()
                    if fname.endswith(".txt"):
                        context_text = context_file.read().decode("utf-8", errors="ignore")
                    elif fname.endswith(".pdf"):
                        from pypdf import PdfReader
                        reader = PdfReader(context_file)
                        context_text = "\n".join(p.extract_text() or "" for p in reader.pages)
                    elif fname.endswith(".docx"):
                        from docx import Document as DocxDoc
                        doc = DocxDoc(context_file)
                        context_text = "\n".join(p.text for p in doc.paragraphs)
                    context_text = context_text[:8000]  # cap at 8k chars
                except Exception as exc:
                    st.warning(f"Impossible de lire le document : {exc}")

            internal_links_data = None
            if internal_links_file is not None:
                try:
                    import pandas as _pd
                    internal_links_data = _pd.read_csv(internal_links_file, encoding="utf-8-sig",
                                                       on_bad_lines="skip").head(5000).to_dict("records")
                except Exception as exc:
                    st.warning(f"CSV maillage non lu : {exc}")

            st.session_state.pl = {
                "active":        True,
                "stopped":       False,
                "waiting":       False,
                "manual":        manual,
                "step":          0,      # 0-4 = étape courante, 5 = terminé
                "keyword":       keyword,
                "site_url":      site_url,
                "country":       country,
                "lang":          article_lang,
                "page_type":     page_type,
                "context_doc":   context_text,
                "internal_links_data": internal_links_data,
                "refresh_style": refresh_style,
                # outputs
                "style_ctx":     None,
                "style_profile": None,
                "seo_brief":     None,
                "intel_paa":      [],
                "intel_secondary": [],
                "intel_lsi":       [],
                "intel_longtail":  [],
                "intel_cannib":    [],
                "intel_intent":    "",
                "intel_serp_titles": [],
                "briefing":         None,
                "draft_article":    None,
                "full_article":     None,
                "meta_title":       "",
                "meta_description": "",
                "geo_check":        [],
                "internal_link_suggestions": [],
                "briefing_system_prompt": None,
                "article_system_prompt":  None,
                "meta_system_prompt":     None,
                "lang":             "fr",
                "user_feedback":    "",
                # stepper display
                "states":        ["pending"] * 5,
                "details":       [""] * 5,
                "step_costs":    [0.0] * 5,
                "total_cost":    0.0,
                "pass_costs":    [],
            }
            st.rerun()

    # ── PIPELINE ACTIF ────────────────────────────────────────────────────────
    if pl and pl.get("active"):
        st.markdown(f"## Génération — *{pl['keyword']}*")
        st.caption(f"{pl['site_url']}   ·   {'Validation manuelle activée' if pl['manual'] else 'Mode automatique'}")

        stepper_ph   = st.empty()
        progress_ph  = st.empty()
        cost_ph      = st.empty()
        stepper_ph.markdown(_stepper_html(pl["states"], pl["details"], pl["step_costs"]),
                            unsafe_allow_html=True)
        progress_ph.progress(PROGRESS[min(pl["step"], 4)] if pl["step"] < 5 else 100)
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
                f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;'
                f'padding:14px 20px;margin:8px 0 20px">'
                f'<div style="font-size:13px;font-weight:600;color:#0f172a;margin-bottom:4px">'
                f'Validation requise — {STEPS[step_done][2]}</div>'
                f'<div style="font-size:13px;color:#64748b">'
                f'Relis le résultat ci-dessous puis choisis de continuer ou d\'arrêter.</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Aperçu selon l'étape terminée
            if step_done == 0 and pl["style_profile"]:
                p = pl["style_profile"]
                pages = p.get("_pages_scraped", [])
                scraped_n = p.get("_scraped_count", 0)
                if pages:
                    st.success(f"{scraped_n} page(s) analysée(s) pour construire le style profile")
                    with st.expander(f"Pages scrapées ({scraped_n})"):
                        for u in pages:
                            st.caption(f"• {u}")
                c1, c2 = st.columns(2)
                c1.markdown("**Tonalité :** " + ", ".join(p.get("tonality", [])))
                c1.markdown("**POV :** " + p.get("pov", "—"))
                c2.markdown("**Patterns :** " + ", ".join(p.get("recurring_patterns", [])[:4]))
                c2.markdown("**Vocab. évité :** " + ", ".join(p.get("avoided_vocabulary", [])[:4]))
                with st.expander("Voir le style profile complet"):
                    st.json({k: v for k, v in p.items() if not k.startswith("_")})
            elif step_done == 1:
                intent = pl.get("intel_intent", "")
                if intent:
                    st.info(f"Intention de recherche : **{intent}**")
                sec = pl.get("intel_secondary", [])
                lsi = pl.get("intel_lsi", [])
                lt  = pl.get("intel_longtail", [])
                if sec:
                    st.markdown(f"**Mots-clés secondaires ({len(sec)}) :** " + ", ".join(sec[:10]))
                if lsi:
                    st.markdown(f"**LSI / sémantique ({len(lsi)}) :** " + ", ".join(lsi[:12]))
                if lt:
                    st.markdown(f"**Longue traîne ({len(lt)}) :** " + ", ".join(lt[:8]))
                serp_titles = pl.get("intel_serp_titles", [])
                if serp_titles:
                    with st.expander(f"Sources SERP locales ({len(serp_titles)} pages)"):
                        for i, (title, url) in enumerate(serp_titles, 1):
                            st.markdown(f"**{i}.** {title}  \n`{url}`")
                if pl["intel_paa"]:
                    st.markdown(f"**Questions PAA ({len(pl['intel_paa'])}) :**")
                    for q in pl["intel_paa"][:6]:
                        st.caption(f"• {q}")
                if pl["intel_cannib"]:
                    st.warning(f"{len(pl['intel_cannib'])} risque(s) de cannibalisation")
            elif step_done == 2 and pl["briefing"]:
                wc = _count_words(pl["briefing"])
                st.caption(f"{wc} mots")
                with st.expander("Lire le briefing & plan complet", expanded=True):
                    st.markdown(pl["briefing"])
                st.markdown("**Feedback / adaptations (optionnel)**")
                feedback = st.text_area(
                    "Instructions pour la rédaction (ex: insister sur tel aspect, changer la tonalité, ajouter une section...)",
                    value=pl.get("user_feedback", ""),
                    key=f"feedback_{step_done}",
                    placeholder="Laisse vide si pas d'adaptation nécessaire"
                )
                pl["user_feedback"] = feedback
            elif step_done == 3 and pl["full_article"]:
                wc = _count_words(pl["full_article"])
                st.caption(f"{wc} mots")
                with st.expander("Lire l'article complet", expanded=False):
                    st.markdown(pl["full_article"])
            elif step_done == 4:
                st.markdown(f"**Meta title :** {pl['meta_title']}")
                st.markdown(f"**Meta description :** {pl['meta_description']}")
                geo = pl.get("geo_check", [])
                if geo:
                    with st.expander(f"Vérification GEO ({len(geo)} sections)", expanded=False):
                        for item in geo:
                            ok = "oui" in item.lower()
                            st.caption(f"{'[ok]' if ok else '[!]'} {item}")
                with st.expander("Article révisé complet", expanded=False):
                    st.markdown(pl["full_article"] or "")

            # Boutons de décision
            st.markdown("")
            next_label = "Enregistrer l'article" if step_done == 4 \
                         else f"Valider → {STEPS[step_done + 1][2]}"
            btn_ok, btn_stop = st.columns(2)
            with btn_ok:
                if st.button(next_label, type="primary",
                             use_container_width=True, key=f"val_ok_{step_done}"):
                    pl["waiting"] = False
                    st.rerun()
            with btn_stop:
                if st.button("Arrêter", use_container_width=True,
                             key=f"val_stop_{step_done}"):
                    pl["stopped"] = True
                    pl["active"]  = False
                    st.rerun()

            # ── Revenir à une étape ─────────────────────────────────────────
            if step_done > 0:
                with st.expander("Revenir à une étape précédente et régénérer"):
                    redo_idx = st.selectbox(
                        "Étape à régénérer",
                        options=list(range(step_done + 1)),
                        format_func=lambda i: f"{i+1}. {STEPS[i][2]}",
                        key=f"redo_sel_{step_done}",
                    )
                    st.caption("Les étapes suivantes seront effacées et régénérées.")
                    if st.button("Régénérer à partir de cette étape",
                                 key=f"redo_btn_{step_done}"):
                        # Clear outputs from redo_idx onwards
                        _clear = {
                            0: ["style_ctx", "style_profile", "briefing_system_prompt",
                                "article_system_prompt", "meta_system_prompt"],
                            1: ["seo_brief", "intel_paa", "intel_secondary", "intel_lsi",
                                "intel_longtail", "intel_cannib", "intel_intent", "intel_serp_titles"],
                            2: ["briefing"],
                            3: ["full_article", "draft_article"],
                            4: ["meta_title", "meta_description", "geo_check",
                                "internal_link_suggestions"],
                        }
                        for i in range(redo_idx, 5):
                            pl["states"][i]    = "pending"
                            pl["details"][i]   = ""
                            pl["step_costs"][i] = 0.0
                            for field in _clear.get(i, []):
                                pl[field] = None if pl.get(field) and not isinstance(
                                    pl.get(field), list) else [] if isinstance(
                                    pl.get(field), list) else ""
                        pl["step"]    = redo_idx
                        pl["waiting"] = False
                        pl["stopped"] = False
                        st.rerun()

        # ── Arrêt demandé ────────────────────────────────────────────────────
        if pl["stopped"]:
            st.warning("Génération arrêtée à l'étape **" +
                       STEPS[min(pl["step"], 4)][2] + "**.")
            if pl["total_cost"] > 0:
                st.info(f"Coût consommé : {format_usd(pl['total_cost'])}")
            if st.button("Nouvelle génération", type="primary"):
                st.session_state.pl = None
                st.rerun()

        # ── En attente de validation (reload page / retour arrière) ──────────
        elif pl["waiting"]:
            # Cas où la page est rechargée pendant l'attente
            _show_validation()

        # ── EXÉCUTION de l'étape courante ────────────────────────────────────
        elif pl["step"] < 5:
            s = pl["step"]
            pl["states"][s] = "running"
            pl["details"][s] = "En cours…"
            stepper_ph.markdown(_stepper_html(pl["states"], pl["details"], pl["step_costs"]),
                                unsafe_allow_html=True)

            with st.status(f"{STEPS[s][1]} Étape {s+1}/5 — {STEPS[s][2]}", expanded=True) as status:

                try:
                    if s == 0:
                        from tone_analyzer import (build_style_profile,
                                                   style_profile_to_system_context,
                                                   profile_cache_exists)
                        target_lang = pl.get("lang", "fr")
                        cached = profile_cache_exists(pl["site_url"], target_lang=target_lang)
                        if cached and not pl["refresh_style"]:
                            st.write(f"Style profile en cache pour {pl['site_url']}")
                        else:
                            st.write(f"Scraping de {pl['site_url']} — pages {target_lang.upper()} uniquement si disponibles ...")
                            if not config.FIRECRAWL_API_KEY:
                                st.write("Firecrawl non configuré — fallback BeautifulSoup")
                        profile_data, sp_in, sp_out = build_style_profile(
                            pl["site_url"], force_refresh=pl["refresh_style"], target_lang=target_lang)
                        style_ctx = style_profile_to_system_context(profile_data)
                        sp_cost   = PassCost(config.CLAUDE_OPUS, sp_in, sp_out).usd if sp_in else 0
                        pl["style_profile"] = profile_data
                        pl["style_ctx"]     = style_ctx
                        scraped_count = profile_data.get("_scraped_count", 0)
                        scraped_urls  = profile_data.get("_pages_scraped", [])
                        if sp_in:
                            st.write(f"{scraped_count} page(s) scrappée(s) — {sp_in:,} tokens — langue : {pl['lang']}")
                            for u in scraped_urls:
                                st.caption(u)
                        else:
                            st.write(f"Style profile chargé depuis le cache — langue : {pl['lang']}")
                        if scraped_count > 0 and scraped_count < 5:
                            st.warning(
                                f"Seulement {scraped_count} page(s) scrappée(s). "
                                f"Minimum recommandé : 5. Le profil de ton peut manquer de précision. "
                                f"Cochez 'Rafraîchir le style' et vérifiez que le site est accessible."
                            )
                        detail = "cache" if not sp_in else f"{scraped_count} pages · {sp_in:,} tok"
                        _cost  = sp_cost

                    elif s == 1:
                        from seo_intelligence import gather_seo_intelligence, seo_intel_to_brief

                        if not config.DATAFORSEO_LOGIN:
                            st.warning("DATAFORSEO_LOGIN manquant — données SEO ignorées")

                        intel     = gather_seo_intelligence(pl["keyword"], country=pl.get("country", ""))
                        seo_brief = seo_intel_to_brief(intel)

                        # Affiche les erreurs réelles si présentes
                        for err in intel.errors:
                            st.error(str(err))

                        cl = intel.keyword_cluster
                        st.write(f"{len(intel.serp_top10)} SERP · "
                                 f"{len(intel.paa_questions)} PAA · "
                                 f"{len(cl.secondary)} KW sec. · "
                                 f"{len(cl.lsi)} LSI · "
                                 f"{len(cl.long_tail)} longue traîne")
                        if intel.cannibalisation_risk:
                            st.warning(f"{len(intel.cannibalisation_risk)} risque(s) de cannibalisation détecté(s)")

                        pl["intel_paa"]          = intel.paa_questions
                        pl["intel_secondary"]     = intel.keyword_cluster.secondary
                        pl["intel_lsi"]           = intel.keyword_cluster.lsi
                        pl["intel_longtail"]      = intel.keyword_cluster.long_tail
                        pl["intel_cannib"]        = [p.url for p in intel.cannibalisation_risk]
                        pl["intel_intent"]        = intel.search_intent
                        pl["intel_serp_titles"]   = [(r.title, r.url) for r in intel.serp_top10[:8]]
                        pl["seo_brief"]           = seo_brief
                        if intel.search_intent:
                            st.info(f"Intention de recherche : **{intel.search_intent}**")
                        if pl["intel_serp_titles"]:
                            with st.expander(f"Sources utilisées — marché {pl.get('country', '')}", expanded=True):
                                for i, (title, url) in enumerate(pl["intel_serp_titles"], 1):
                                    st.markdown(f"{i}. [{title}]({url})")
                        total_kw = len(cl.secondary) + len(cl.lsi) + len(cl.long_tail)
                        detail = f"{total_kw} KW · {len(pl['intel_paa'])} PAA" if total_kw else "partiel"
                        _cost  = 0.0

                    elif s in (2, 3, 4):
                        if s == 2:  # Briefing & plan
                            if pl.get("briefing_system_prompt") is None:
                                pl["briefing_system_prompt"] = _build_briefing_system(
                                    pl["style_ctx"] or "", pl["seo_brief"] or "",
                                    lang=pl.get("lang", "fr"))
                            system = pl["briefing_system_prompt"]
                            ctx = pl.get("context_doc") or ""
                            text, in_t, out_t = generate_chunked_briefing(
                                keyword=pl["keyword"],
                                site_url=pl["site_url"],
                                country=pl["country"],
                                page_type=pl.get("page_type", "Article de blog"),
                                context_doc=ctx or "(aucun document fourni)",
                                seo_brief=pl["seo_brief"] or "(aucune donnée SEO disponible)",
                                system=system,
                                lang=pl.get("lang", "fr"),
                            )
                            pl["briefing"] = text
                            wc = _count_words(text)
                            st.write(f"Briefing — {wc} mots (chunked)")
                            detail = f"{wc} mots"

                        elif s == 3:  # Article complet
                            if pl.get("article_system_prompt") is None:
                                pl["article_system_prompt"] = _build_article_system(
                                    pl["style_ctx"] or "", lang=pl.get("lang", "fr"))
                            system = pl["article_system_prompt"]
                            user_feedback = pl.get("user_feedback", "")
                            feedback_block = f"\n\n--- FEEDBACK UTILISATEUR ---\n{user_feedback}\n--- FIN FEEDBACK ---" if user_feedback else ""
                            briefing_with_feedback = pl["briefing"] + feedback_block
                            text, in_t, out_t = generate_article_by_sections(
                                briefing=briefing_with_feedback,
                                system=system,
                            )
                            pl["draft_article"] = text
                            pl["full_article"] = text
                            wc = _count_words(text)
                            st.write(f"Article — {wc} mots (chunked)")
                            detail = f"{wc} mots"

                        elif s == 4:  # Métas + révision
                            if pl.get("meta_system_prompt") is None:
                                pl["meta_system_prompt"] = _build_meta_system(lang=pl.get("lang", "fr"))
                            system = pl["meta_system_prompt"]
                            prompt = META_PROMPT.format(
                                full_article=pl["full_article"] or ""
                            )
                            text, in_t, out_t = _call_claude(system, prompt, max_tokens=6000)

                            import re as _re
                            def _extract(tag: str, nxt: str, src: str) -> str:
                                m = _re.search(
                                    rf"==={tag}===\s*(.*?)\s*==={nxt}===",
                                    src, _re.DOTALL)
                                return m.group(1).strip() if m else ""

                            meta_title       = _extract("META_TITLE",       "META_DESCRIPTION", text)
                            meta_description = _extract("META_DESCRIPTION",  "GEO_CHECK",        text)
                            geo_raw          = _extract("GEO_CHECK",         "CTA_FINAL",        text)
                            cta_final        = _extract("CTA_FINAL",         "END",              text)

                            pl["meta_title"]       = meta_title or pl["meta_title"]
                            pl["meta_description"] = meta_description or pl["meta_description"]
                            pl["geo_check"]        = [l.strip() for l in geo_raw.splitlines() if l.strip()]
                            if cta_final and cta_final not in (pl["full_article"] or ""):
                                pl["full_article"] = f"{pl['full_article']}\n\n{cta_final}".strip()

                            st.write(f"Meta title ({len(pl['meta_title'])} car.) : {pl['meta_title']}")
                            st.write(f"Meta desc. ({len(pl['meta_description'])} car.)")
                            if pl.get("geo_check"):
                                ok = sum(1 for g in pl["geo_check"] if "oui" in g.lower())
                                st.write(f"GEO : {ok}/{len(pl['geo_check'])} sections conformes")
                            detail = f"{len(pl['meta_title'])} car." if pl["meta_title"] else "parsing KO"

                        _cost = PassCost(config.CLAUDE_SONNET, in_t, out_t).usd
                        pl["pass_costs"].append([config.CLAUDE_SONNET, in_t, out_t])
                        st.write(f"Tokens envoyés : {in_t:,}   |   reçus : {out_t:,}")

                    # Mise à jour stepper
                    pl["states"][s]     = "done"
                    pl["details"][s]    = detail
                    pl["step_costs"][s] = _cost
                    pl["total_cost"]   += _cost
                    pl["step"]          = s + 1
                    status.update(label=f"{STEPS[s][2]} — terminé", state="complete", expanded=False)

                except Exception as err:
                    pl["states"][s]  = "error"
                    pl["details"][s] = str(err)[:40]
                    pl["step"]       = s + 1
                    status.update(label=f"{STEPS[s][2]} — erreur", state="error")
                    st.error(str(err))
                    if s not in (1, 4):  # SEO et métas peuvent échouer sans tout stopper
                        pl["stopped"] = True
                        pl["active"]  = False

            # Mise à jour stepper final
            stepper_ph.markdown(_stepper_html(pl["states"], pl["details"], pl["step_costs"]),
                                unsafe_allow_html=True)
            cost_ph.markdown(
                f'<div style="text-align:right;margin:-4px 0 16px">'
                f'<span class="cost-badge">Coût en cours : {format_usd(pl["total_cost"])}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if pl["manual"] and not pl["stopped"] and pl["step"] <= 5:
                # ⚠️ Ne pas appeler st.rerun() ici — afficher la validation
                # directement dans le même run pour éviter qu'elle disparaisse.
                pl["waiting"] = True
                _show_validation()
            elif not pl["stopped"]:
                # Mode automatique : passer directement à l'étape suivante
                st.rerun()

        # ── TOUTES LES ÉTAPES FAITES → Sauvegarde & résultats ────────────────
        elif pl["step"] == 5 and not pl["stopped"]:
            progress_ph.progress(100)

            from cost_tracker import RequestCost

            article              = ArticleOutput(keyword=pl["keyword"], site_url=pl["site_url"])
            article.full_article = pl["full_article"] or ""
            article.meta_title   = pl["meta_title"]
            article.meta_description = pl["meta_description"]
            for model, in_t, out_t in pl["pass_costs"]:
                article.cost.passes.append(PassCost(model, in_t, out_t))
            article.cost.dataforseo_tasks = 3 if config.DATAFORSEO_LOGIN else 0

            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            slug = _slugify(pl["keyword"])
            ts   = datetime.now().strftime("%Y%m%d_%H%M")
            base = os.path.join(config.OUTPUT_DIR, f"{slug}_{ts}")

            # Internal link suggestions
            internal_links_data = pl.get("internal_links_data")
            if internal_links_data and pl["full_article"]:
                try:
                    article_lower = pl["full_article"].lower()
                    suggestions = []
                    seen_urls: set = set()
                    for row in internal_links_data:
                        dest = str(row.get("To") or row.get("destination") or "")
                        anchor = str(row.get("Anchor") or row.get("anchor") or "")
                        title = str(row.get("Title") or row.get("title") or "")
                        if dest in seen_urls or not dest.startswith("http"):
                            continue
                        score = sum(1 for w in (anchor + " " + title).lower().split()
                                   if len(w) > 4 and w in article_lower)
                        if score >= 2:
                            suggestions.append({"url": dest, "anchor": anchor,
                                               "title": title, "score": score})
                            seen_urls.add(dest)
                    suggestions.sort(key=lambda x: x["score"], reverse=True)
                    pl["internal_link_suggestions"] = suggestions[:10]
                except Exception:
                    pass

            md_content = format_final_output(article)
            with open(f"{base}.md", "w", encoding="utf-8") as f:
                f.write(md_content)

            bundle = {
                "keyword":          pl["keyword"],
                "site_url":         pl["site_url"],
                "country":          pl.get("country", ""),
                "meta_title":       pl["meta_title"],
                "meta_description": pl["meta_description"],
                "briefing":         pl["briefing"] or "",
                "draft_article":    pl.get("draft_article") or "",
                "final_article":    pl["full_article"] or "",
                "full_article":     pl["full_article"],
                "revision_applied": bool((pl.get("draft_article") or "") != (pl["full_article"] or "")),
                "word_count":       _count_words(pl["full_article"] or ""),
                "pass_logs":        [],
                "generated_at":     datetime.now().isoformat(),
                "cost":             article.cost.to_dict(),
                "seo": {
                    "secondary_keywords": pl["intel_secondary"],
                    "paa":                pl["intel_paa"],
                    "cannibalisations":   pl["intel_cannib"],
                    "sources_used":       [{"title": title, "url": url} for title, url in pl.get("intel_serp_titles", [])],
                    "internal_links":     pl.get("internal_link_suggestions", []),
                },
            }
            json_content = _json.dumps(bundle, ensure_ascii=False, indent=2)
            with open(f"{base}.json", "w", encoding="utf-8") as f:
                f.write(json_content)

            # HTML export
            import html as _html_mod
            try:
                import markdown as _md_lib
                html_markdown = f"# {pl['meta_title'] or pl['keyword']}\n\n{pl['full_article'] or ''}"
                if pl.get("internal_link_suggestions"):
                    html_markdown += "\n\n## À lire aussi\n"
                    for s in pl["internal_link_suggestions"][:6]:
                        anchor = s.get("anchor") or s.get("title") or s.get("url")
                        html_markdown += f"- [{anchor}]({s.get('url')})\n"
                html_body = _md_lib.markdown(
                    html_markdown,
                    extensions=["extra", "tables", "fenced_code", "sane_lists"],
                )
            except Exception:
                import markdown as _md
                html_body = _md.markdown(f"# {pl['meta_title'] or pl['keyword']}\n\n{pl['full_article'] or ''}", extensions=['extra', 'tables'])
            html_content = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>{_html_mod.escape(pl['meta_title'] or pl['keyword'])}</title>
<meta name="description" content="{_html_mod.escape(pl['meta_description'] or '')}">
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 820px; margin: 40px auto; color: #1a1d2e; line-height: 1.7; padding: 20px; }}
  h1,h2,h3 {{ color: #1a1d2e; }} h1 {{ font-size: 2em; }} h2 {{ font-size: 1.4em; }} h3 {{ font-size: 1.2em; }}
  p {{ margin: 0 0 1em; }} ul,ol {{ padding-left: 1.4em; }} a {{ color: #3b82f6; }}
  blockquote {{ border-left: 4px solid #e5e7eb; padding-left: 1em; margin: 1em 0; color: #6b7280; }}
  code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  pre {{ background: #1f2937; color: #f9fafb; padding: 1em; border-radius: 8px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
</style></head><body>
{html_body}
</body></html>"""
            with open(f"{base}.html", "w", encoding="utf-8") as f:
                f.write(html_content)

            pl["active"] = False
            st.success(f"Article enregistré — {bundle['word_count']} mots · Coût réel : {format_usd(article.cost.total_usd)}")

            st.markdown('<div class="section-hdr">Résultats</div>', unsafe_allow_html=True)
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Mots", bundle["word_count"])
            r2.metric("Coût réel", format_usd(article.cost.total_usd))
            r3.metric("Tokens in", f"{article.cost.total_input_tokens:,}")
            r4.metric("Tokens out", f"{article.cost.total_output_tokens:,}")

            st.markdown(f"**Meta title :** {pl['meta_title']}")
            st.markdown(f"**Meta description :** {pl['meta_description']}")

            if pl["intel_cannib"]:
                st.warning(f"{len(pl['intel_cannib'])} risque(s) de cannibalisation")
                for url in pl["intel_cannib"]:
                    st.caption(f"• {url}")

            # Internal link suggestions block
            if pl.get("internal_link_suggestions"):
                with st.expander(f"Suggestions de maillage interne ({len(pl['internal_link_suggestions'])} liens)", expanded=True):
                    for s in pl["internal_link_suggestions"]:
                        st.markdown(
                            f"**Ancre :** `{s['anchor']}`  \n"
                            f"**URL :** [{s['url']}]({s['url']})  \n"
                            f"**Titre page :** {s['title']}",
                        )
                        st.divider()

            # Article tabs: preview / HTML
            tab_prev, tab_html = st.tabs(["Article (markdown)", "Code HTML (copier-coller)"])
            with tab_prev:
                with st.expander("Briefing éditorial", expanded=False):
                    st.markdown(pl["briefing"] or "")
                if pl.get("intel_serp_titles"):
                    with st.expander(f"Sources utilisées — marché {pl.get('country', '')}", expanded=True):
                        for i, (title, url) in enumerate(pl["intel_serp_titles"], 1):
                            st.markdown(f"{i}. [{title}]({url})")
                st.markdown(pl["full_article"])
            with tab_html:
                st.code(html_content, language="html")
                st.caption("Copie ce code HTML et colle-le dans ton CMS.")

            d1, d2, d3, d4 = st.columns(4)
            d1.download_button("Télécharger .md", md_content, f"{slug}_{ts}.md",
                               "text/markdown", use_container_width=True)
            d2.download_button("Télécharger .json", json_content, f"{slug}_{ts}.json",
                               "application/json", use_container_width=True)
            d3.download_button("Télécharger .html", html_content, f"{slug}_{ts}.html",
                               "text/html", use_container_width=True)
            if d4.button("Nouvelle génération", use_container_width=True):
                st.session_state.pl = None
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — BIBLIOTHÈQUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "library":
    st.markdown("## Bibliothèque de contenus")

    articles = _load_articles()
    if not articles:
        st.info("Aucun article généré pour l'instant. Va dans **Générer** pour commencer.")
    else:
        search = st.text_input("Rechercher un mot-clé ou site", placeholder="ex: rénovation", label_visibility="collapsed")
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
                    st.caption(f"Site : `{site_str}`")
                st.markdown(f"**Meta title :** {a.get('meta_title','—')}")
                st.markdown(f"**Meta desc :** {a.get('meta_description','—')}")

                seo = a.get("seo", {})
                if seo.get("paa"):
                    st.markdown("**Questions PAA couvertes :**")
                    for q in seo["paa"][:4]:
                        st.caption(f"• {q}")
                if seo.get("cannibalisations"):
                    st.warning(f"{len(seo['cannibalisations'])} risque(s) de cannibalisation")

                if cost_d:
                    st.markdown(
                        f'<span class="cost-badge">{cost_str} total — '
                        f'{cost_d.get("input_tokens",0):,} tokens in / {cost_d.get("output_tokens",0):,} out</span>',
                        unsafe_allow_html=True,
                    )

                slug  = _slugify(a.get("keyword","article"))
                fname = [f for f in os.listdir(config.OUTPUT_DIR)
                         if f.startswith(slug) and f.endswith(".md")] if os.path.exists(config.OUTPUT_DIR) else []

                if fname:
                    with open(os.path.join(config.OUTPUT_DIR, fname[0]), encoding="utf-8") as mf:
                        md_c = mf.read()
                    html_name = fname[0].replace(".md", ".html")
                    html_path = os.path.join(config.OUTPUT_DIR, html_name)
                    html_c = ""
                    if os.path.exists(html_path):
                        with open(html_path, encoding="utf-8") as hf:
                            html_c = hf.read()
                    dl1, dl2, dl3 = st.columns(3)
                    dl1.download_button(".md",   md_c,                          fname[0],               "text/markdown",    key=f"lib_md_{fname[0]}")
                    dl2.download_button(".json", json.dumps(a, ensure_ascii=False, indent=2), fname[0].replace(".md",".json"), "application/json", key=f"lib_js_{fname[0]}")
                    dl3.download_button(".html", html_c, html_name, "text/html", key=f"lib_html_{fname[0]}", disabled=not bool(html_c))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — PARAMÈTRES
# ══════════════════════════════════════════════════════════════════════════════
elif page == "settings":
    st.markdown("## Paramètres")

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

    if tc1.button("Tester DataForSEO", use_container_width=True):
        import base64, requests as _req
        login = config.DATAFORSEO_LOGIN.strip()
        pwd   = config.DATAFORSEO_PASSWORD.strip()
        if not login or not pwd:
            st.error("DATAFORSEO_LOGIN ou DATAFORSEO_PASSWORD manquant dans les secrets.")
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
                    st.success(f"DataForSEO OK — {r.status_code}")
                else:
                    st.error(f"{r.status_code} — {r.text[:300]}")
            except Exception as e:
                st.error(f"Erreur réseau : {e}")

    if tc2.button("Tester Anthropic", use_container_width=True):
        try:
            import anthropic as _ant
            c = _ant.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            msg = c.messages.create(
                model=config.CLAUDE_SONNET,
                max_tokens=5,
                messages=[{"role": "user", "content": "hi"}],
            )
            st.success(f"Anthropic OK — {msg.usage.input_tokens} tokens")
        except Exception as e:
            st.error(f"Anthropic : {e}")

    # ── Modèles ───────────────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Modèles & paramètres</div>', unsafe_allow_html=True)
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Rédaction", config.CLAUDE_SONNET)
    mc2.metric("Tone Analyzer", config.CLAUDE_OPUS)
    mc3.metric("Objectif mots", config.TARGET_WORD_COUNT)

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
            with st.expander(f"`{fname}` — mis à jour le {datetime.fromtimestamp(mtime).strftime('%d/%m/%Y %H:%M')}"):
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
                if st.button(f"Supprimer ce profil", key=f"del_{fname}"):
                    os.remove(fpath)
                    st.success(f"{fname} supprimé.")
                    st.rerun()

    # ── Coûts de tarification ─────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Coût estimé par article</div>', unsafe_allow_html=True)

    st.info(
        "**Ces prix sont en USD par million de tokens (MTok)** — pas par article.\n\n"
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
