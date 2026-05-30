"""
writer.py
─────────
Moteur de rédaction multi-passes avec Claude Sonnet.

Chaîne de contexte (chaque passe reçoit le résultat de la précédente) :

  Passe 1 → Introduction + angle éditorial
  Passe 2 → Plan H2/H3 enrichi SEO
  Passe 3 → Corps de l'article section par section
  Passe 4 → Méta-title, méta-description, révision finale + CTA

Chaque passe injecte :
  - style_context  (Style Profile JSON → string)
  - seo_brief      (SEO Intelligence → string)
  - previous_output (accumulateur de toutes les passes précédentes)
"""

import logging
from dataclasses import dataclass, field

import anthropic

import config

logger = logging.getLogger(__name__)


# ── Output data structure ──────────────────────────────────────────────────────

@dataclass
class ArticleOutput:
    keyword: str
    introduction: str   = ""
    plan_h2_h3: str     = ""
    body: str           = ""
    meta_title: str     = ""
    meta_description: str = ""
    full_article: str   = ""
    pass_logs: list[str] = field(default_factory=list)


# ── Shared prompt builder ──────────────────────────────────────────────────────

def _build_system(style_context: str, seo_brief: str) -> str:
    return f"""Tu es un rédacteur web expert francophone.
Tu rédiges TOUJOURS en français, en respectant scrupuleusement le Style Profile ci-dessous.
Tu n'ajoutes JAMAIS de contenu générique ni de remplissage.
Chaque phrase doit apporter de la valeur.

{style_context}

{seo_brief}
"""


# ── Pass 1 : Introduction ──────────────────────────────────────────────────────

PASS1_PROMPT = """Rédige l'introduction de l'article pour le mot-clé : **{keyword}**

Contraintes :
- 100 à 150 mots maximum
- Commence par une accroche forte (question, statistique ou affirmation contre-intuitive)
- Pose le problème du lecteur en 1-2 phrases
- Annonce ce que l'article va apporter (sans spoiler les H2)
- Intègre le mot-clé principal de manière naturelle dans les 50 premiers mots
- Respecte scrupuleusement le Style Profile

Ne rédige QUE l'introduction, rien d'autre.
"""


# ── Pass 2 : Plan H2/H3 ────────────────────────────────────────────────────────

PASS2_PROMPT = """Sur la base de l'introduction ci-dessous et du SEO Brief, génère le plan H2/H3 de l'article.

## Introduction rédigée (passe 1)
{pass1_output}

Contraintes du plan :
- 4 à 6 H2 maximum
- Chaque H2 peut avoir 2-3 H3
- Les H2 doivent couvrir les questions PAA prioritaires
- Les H2 doivent intégrer naturellement les mots-clés secondaires
- Format attendu :
  ## H2 : [titre]
    ### H3 : [sous-titre]
    ### H3 : [sous-titre]
  ## H2 : [titre]
  ...
- Respecte les patterns structurels du Style Profile

Retourne UNIQUEMENT le plan formaté, sans commentaire.
"""


# ── Pass 3 : Body ─────────────────────────────────────────────────────────────

PASS3_PROMPT = """Rédige le corps complet de l'article en suivant le plan ci-dessous, section par section.

## Introduction (passe 1)
{pass1_output}

## Plan validé (passe 2)
{pass2_output}

Contraintes de rédaction :
- Objectif global : {target_word_count} mots pour tout l'article (intro incluse)
- Rédige chaque section sous son H2/H3 correspondant
- Pour chaque H2 : 150 à 250 mots
- Intègre les mots-clés secondaires naturellement (pas de keyword stuffing)
- Adresse au moins 3 questions PAA du SEO Brief dans le corps
- Utilise des listes à puces pour les étapes ou comparaisons (max 5 items)
- Ajoute un mini-CTA discret à la fin de 2 sections maximum
- Respecte STRICTEMENT le Style Profile (ton, vocabulaire, POV, phrases interdites)

Retourne le corps de l'article (H2 + H3 + contenu), sans l'introduction.
"""


# ── Pass 4 : Méta + Révision ──────────────────────────────────────────────────

PASS4_PROMPT = """Tu reçois l'article complet (introduction + corps). Effectue la révision finale.

## Article complet
{full_draft}

Tâches :
1. **Méta-title** : 50-60 caractères, mot-clé principal en début, accrocheur
2. **Méta-description** : 140-160 caractères, bénéfice clair, verbe d'action
3. **Révision** : corrige les incohérences de ton, supprime les répétitions, renforce les transitions
4. **CTA final** : 1 CTA de fin d'article aligné avec le style CTA du Style Profile

Retourne en JSON strictement valide :
{{
  "meta_title": "...",
  "meta_description": "...",
  "revised_article": "article complet révisé ici",
  "cta_final": "..."
}}
"""


# ── Claude caller ──────────────────────────────────────────────────────────────

def _call_claude(system: str, user_prompt: str) -> str:
    """Single Claude Sonnet call. Returns text content."""
    client  = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model      = config.CLAUDE_SONNET,
        max_tokens = config.MAX_TOKENS_PER_PASS,
        system     = system,
        messages   = [{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()


# ── Public API ─────────────────────────────────────────────────────────────────

def run_writing_pipeline(
    keyword: str,
    style_context: str,
    seo_brief: str,
) -> ArticleOutput:
    """
    Execute the full 4-pass writing pipeline.
    Returns a populated ArticleOutput.
    """
    output = ArticleOutput(keyword=keyword)
    system = _build_system(style_context, seo_brief)

    # ── Pass 1 : Introduction ─────────────────────────────────────────────────
    logger.info("[Writer] Passe 1 — Introduction")
    p1_prompt = PASS1_PROMPT.format(keyword=keyword)
    output.introduction = _call_claude(system, p1_prompt)
    output.pass_logs.append(f"PASS1 OK — {len(output.introduction.split())} mots")

    # ── Pass 2 : Plan H2/H3 ──────────────────────────────────────────────────
    logger.info("[Writer] Passe 2 — Plan H2/H3")
    p2_prompt = PASS2_PROMPT.format(pass1_output=output.introduction)
    output.plan_h2_h3 = _call_claude(system, p2_prompt)
    output.pass_logs.append(f"PASS2 OK — {output.plan_h2_h3.count('##')} sections")

    # ── Pass 3 : Corps ────────────────────────────────────────────────────────
    logger.info("[Writer] Passe 3 — Corps de l'article")
    p3_prompt = PASS3_PROMPT.format(
        pass1_output     = output.introduction,
        pass2_output     = output.plan_h2_h3,
        target_word_count= config.TARGET_WORD_COUNT,
    )
    output.body = _call_claude(system, p3_prompt)
    output.pass_logs.append(f"PASS3 OK — {len(output.body.split())} mots")

    # ── Pass 4 : Méta + Révision ──────────────────────────────────────────────
    logger.info("[Writer] Passe 4 — Méta + révision finale")
    full_draft = f"{output.introduction}\n\n{output.body}"
    p4_prompt  = PASS4_PROMPT.format(full_draft=full_draft)
    raw_p4     = _call_claude(system, p4_prompt)

    # Parse JSON response from pass 4
    import json
    raw_p4_clean = raw_p4.strip()
    if raw_p4_clean.startswith("```"):
        raw_p4_clean = raw_p4_clean.split("```")[1]
        if raw_p4_clean.startswith("json"):
            raw_p4_clean = raw_p4_clean[4:]

    try:
        p4_data = json.loads(raw_p4_clean)
        output.meta_title       = p4_data.get("meta_title", "")
        output.meta_description = p4_data.get("meta_description", "")
        revised                 = p4_data.get("revised_article", full_draft)
        cta                     = p4_data.get("cta_final", "")
        output.full_article     = f"{revised}\n\n{cta}".strip()
    except json.JSONDecodeError:
        logger.warning("Pass 4 JSON parse failed — using raw output as full article")
        output.full_article = full_draft

    output.pass_logs.append(
        f"PASS4 OK — méta title: {len(output.meta_title)} chars, "
        f"méta desc: {len(output.meta_description)} chars"
    )

    logger.info("[Writer] Pipeline terminé — %d mots", len(output.full_article.split()))
    return output


def format_final_output(article: ArticleOutput) -> str:
    """Human-readable final deliverable."""
    separator = "─" * 60
    return f"""
{separator}
META TITLE       : {article.meta_title}
META DESCRIPTION : {article.meta_description}
{separator}
PLAN H2/H3
{separator}
{article.plan_h2_h3}

{separator}
ARTICLE COMPLET
{separator}
{article.full_article}

{separator}
LOGS DE PRODUCTION
{separator}
""" + "\n".join(f"  {log}" for log in article.pass_logs)
