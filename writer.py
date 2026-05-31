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
from cost_tracker import PassCost, RequestCost

logger = logging.getLogger(__name__)


# ── Output data structure ──────────────────────────────────────────────────────

@dataclass
class ArticleOutput:
    keyword: str
    site_url: str         = ""
    introduction: str     = ""
    plan_h2_h3: str       = ""
    body: str             = ""
    meta_title: str       = ""
    meta_description: str = ""
    full_article: str     = ""
    pass_logs: list[str]  = field(default_factory=list)
    cost: RequestCost     = field(default_factory=RequestCost)


# ── Shared prompt builder ──────────────────────────────────────────────────────

_LANG_RULES = {
    "fr": "Tu es un rédacteur web SEO expert. Tu rédiges TOUJOURS en français.",
    "nl": "Je bent een expert SEO-redacteur. Je schrijft ALTIJD in het Nederlands.",
    "en": "You are an expert SEO writer. You ALWAYS write in English.",
}


def _build_system(style_context: str, seo_brief: str, lang: str = "fr") -> str:
    lang_rule = _LANG_RULES.get(lang, _LANG_RULES["fr"])
    return f"""{lang_rule}
Tu n'ajoutes JAMAIS de contenu générique ni de remplissage.
Chaque phrase doit apporter de la valeur factuelle ou pratique.

{style_context}

{seo_brief}
"""


# ── Passe Briefing ─────────────────────────────────────────────────────────────

BRIEFING_PROMPT = """\
Mot-clé cible : «{keyword}»
Type de page : {page_type}
Site destinataire : {site_url}
Pays / marché cible : {country}

--- INFORMATIONS DE CONTEXTE FOURNIES (si disponibles) ---
{context_doc}
---

Ton rôle :
1. Décris brièvement les activités de {site_url} (2-3 phrases) pour cadrer la demande.

2. Analyse des concurrents dans les SERP locales ({country}) :
   Pour chaque source SERP ci-dessous, identifie :
   - Si la page est commerciale (vente, produit, landing) ou informationnelle (guide, blog)
   - L'angle principal de la page
   - Ce qu'elle couvre bien et ce qui manque
   → Conclus par l'angle différenciant pour {site_url} qui n'est PAS couvert par ces concurrents

3. Les sources SERP locales ({country}) figurent dans les données SEO ci-dessous.
   Utilise-les comme références — elles sont réelles, issues du marché cible.
   Si la liste est vide, identifie toi-même 5 sources pertinentes.

4. En t'appuyant sur ces sources et le contexte, rédige :

   a. Un briefing complet pour une page de type « {page_type} » comprenant :
      - Intention de recherche détectée et ses implications éditoriales
      - Angle différenciant (non couvert par les concurrents analysés)
      - Points clés incontournables à couvrir
      - Recommandations de tonalité et de style adaptées au type de page
      - Longueur cible : 1 200 à 1 800 mots

   b. Un plan de rédaction structuré H2 / H3 où chaque section précise
      l'intention de recherche spécifique à laquelle elle répond
      (ex. : « — intention : comprendre le coût »)

Données SEO disponibles :
{seo_brief}

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides, sans forcer le mot-clé exact
- Langue : français, néerlandais ou anglais selon la langue dominante du site
- Liste les sources utilisées à la fin du briefing (usage interne uniquement)
"""


# ── Passe Article ──────────────────────────────────────────────────────────────

ARTICLE_PROMPT = """\
Voici le briefing validé à suivre EXACTEMENT :

---BRIEFING---
{briefing}
---FIN BRIEFING---

Ta mission : rédiger l'article complet sur le mot-clé «{keyword}» en respectant
scrupuleusement ce briefing, le plan H2/H3 inclus.

Avant de commencer, répète les sources de référence mentionnées dans le briefing
(pour confirmer que tu utilises les bonnes), puis rédige l'article.

Tu peux t'inspirer de ces sources mais ne les cite PAS dans l'article final.

Principe GEO / AEO obligatoire :
Chaque section (H2 ou H3) doit répondre de manière explicite et complète à une
intention de recherche précise. Commence chaque section par une phrase-réponse directe
(« la réponse courte d'abord »), puis développe. Cette structure facilite la citation
par les moteurs de réponse (ChatGPT, Perplexity, Google SGE).

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot
- Phrases naturelles et fluides, sans sur-optimisation du mot-clé
- Suis le plan H2/H3 du briefing à la lettre
- Respecte le Style Profile du site pour le ton, le vocabulaire et le point de vue
"""


# ── Passe Méta + Révision ──────────────────────────────────────────────────────

META_PROMPT = """\
Tu reçois l'article complet ci-dessous. Effectue la révision finale.

## Article
{full_article}

Tâches :
1. Méta-title : 50-60 caractères, mot-clé en début, accrocheur, pas de majuscule à chaque mot
2. Méta-description : 140-160 caractères, bénéfice clair, verbe d'action
3. Révision légère : incohérences de ton, répétitions, transitions
4. CTA final : 1 phrase de CTA de fin d'article aligné avec le style du site
5. Vérification GEO : pour chaque H2/H3, vérifie qu'il commence par une phrase-réponse
   directe (« réponse courte d'abord »). Corrige ceux qui ne respectent pas ce principe.

Retourne EXACTEMENT dans ce format (balises incluses, rien d'autre avant ===META_TITLE===) :

===META_TITLE===
<meta title ici>
===META_DESCRIPTION===
<meta description ici>
===GEO_CHECK===
<une ligne par H2/H3 : Titre section — intention couverte : oui/non>
===CTA_FINAL===
<une phrase CTA>
===REVISED_ARTICLE===
<article complet révisé en markdown>
===END===
"""


# ── Chunked Briefing Prompts ───────────────────────────────────────────────────

BRIEFING_PART1_CONTEXT = """\
Mot-clé cible : «{keyword}»
Type de page : {page_type}
Site destinataire : {site_url}
Pays / marché cible : {country}

--- INFORMATIONS DE CONTEXTE FOURNIES (si disponibles) ---
{context_doc}
---

Ton rôle :
1. Décris brièvement les activités de {site_url} (2-3 phrases) pour cadrer la demande.

2. Analyse des concurrents dans les SERP locales ({country}) :
   Pour chaque source SERP ci-dessous, identifie :
   - Si la page est commerciale (vente, produit, landing) ou informationnelle (guide, blog)
   - L'angle principal de la page
   - Ce qu'elle couvre bien et ce qui manque
   → Conclus par l'angle différenciant pour {site_url} qui n'est PAS couvert par ces concurrents

3. Les sources SERP locales ({country}) figurent dans les données SEO ci-dessous.
   Utilise-les comme références — elles sont réelles, issues du marché cible.
   Si la liste est vide, identifie toi-même 5 sources pertinentes.

Données SEO disponibles :
{seo_brief}

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides
- Langue : français, néerlandais ou anglais selon la langue dominante du site

Retourne UNIQUEMENT la partie suivante du briefing (en markdown) :
## Contexte & Positionnement
[Activité du site]
[Analyse concurrentielle]
[Angle différenciant]
"""

BRIEFING_PART2_INTENTION = """\
Voici le contexte et le positionnement établis précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, définis l'intention de recherche et les points clés.

À générer (en markdown, section ## Intention & Points Clés) :
1. Intention de recherche détectée et ses implications éditoriales
2. Points clés incontournables à couvrir

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## Intention & Points Clés complète.
"""

BRIEFING_PART2_PLAN = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, rédige un plan de rédaction structuré et complet.

À générer (en markdown, section ## Plan de Rédaction) :
Plan de rédaction structuré H2 / H3 où chaque section précise :
- L'intention de recherche spécifique à laquelle elle répond (ex. : « — intention : comprendre le coût »)
- Le nombre de mots estimé pour cette section (ex. : « — mots estimés : 250-300 »)

Structure flexible :
- Adapte le plan au sujet et à l'intention de recherche
- Inclue les sections pertinentes selon le contexte (pas de structure imposée)
- Le plan doit être complet et couvrir tous les aspects nécessaires
- Pas de CTA final dans le plan
- Le total des mots estimés pour toutes les sections doit se situer entre 1200 et 1800 mots
- Termine OBLIGATOIREMENT par une ligne "Total estimé : X-Y mots" pour indiquer la complétude

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides
- Le plan doit être généré en une seule fois (complet)

Retourne UNIQUEMENT la section ## Plan de Rédaction complète.
"""

BRIEFING_PART2_PLAN_CONTINUATION = """\
Voici le plan de rédaction commencé précédemment (incomplet) :

{previous_content}

Ton rôle :
Continue le plan de rédaction à partir de là où il s'est arrêté.

Instructions :
- Ne répète PAS les sections déjà présentes
- Continue avec les sections H2 manquantes
- Chaque section doit préciser l'intention et le nombre de mots estimé
- Termine OBLIGATOIREMENT par une ligne "Total estimé : X-Y mots"
- Le total des mots estimés pour toutes les sections doit se situer entre 1200 et 1800 mots

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la suite du plan de rédaction (sans répéter le début).
"""

BRIEFING_PART2_TONALITY = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
En t'appuyant sur ce contexte, définis les recommandations de tonalité et de style.

À générer (en markdown, section ## Tonalité & Style) :
1. Recommandations de tonalité et de style adaptées au type de page
2. Longueur cible : 1 200 à 1 800 mots

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## Tonalité & Style complète.
"""

BRIEFING_PART3_SEO_KEYWORDS = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
Complète le briefing avec les spécifications SEO détaillées (mots-clés et maillage).

Données SEO disponibles :
{seo_brief}

À générer (en markdown, section ## SEO & Technique) :
1. Mots-clés secondaires à intégrer (priorisés par pertinence)
2. Recommandations de maillage interne (si applicable)

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## SEO & Technique (mots-clés et maillage uniquement).
"""

BRIEFING_PART3_SEO_METAS = """\
Voici le résumé du briefing établi précédemment :

{context_summary}

Ton rôle :
Complète le briefing avec les spécifications SEO détaillées (métas).

Données SEO disponibles :
{seo_brief}

À générer (en markdown, section ## Métas) :
1. Suggestions de méta-title (50-60 caractères)
2. Suggestions de méta-description (140-160 caractères)

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides

Retourne UNIQUEMENT la section ## Métas complète.
"""


# ── Article Section Generation Prompts ───────────────────────────────────────────

ARTICLE_SECTION_PROMPT = """\
Voici le briefing complet à suivre :

---BRIEFING---
{briefing}
---FIN BRIEFING---

Ta mission : rédiger UNIQUEMENT la section demandée ci-dessous, en respectant
scrupuleusement le briefing et le plan de rédaction.

Section à rédiger :
{section_spec}

CRITIQUE : Suis STRICTEMENT le plan de rédaction fourni dans le briefing.
- Ne crée PAS de sections H2 ou H3 qui ne sont PAS dans le plan
- N'ajoute PAS de sous-sections non prévues
- Ne saute PAS de sections prévues dans le plan
- Le plan de rédaction est la SEULE structure autorisée pour l'article

IMPORTANT : Suis TOUTES les consignes du briefing (tonalité, style, positionnement)
mais ton OUTPUT ne doit contenir QUE le contenu de l'article basé sur le plan de rédaction.
N'inclus PAS les sections du briefing (CTA, angle différenciant, etc.) dans ta réponse.

RESPECTE LA LONGUEUR CIBLE spécifiée dans le briefing (généralement 1200-1800 mots pour l'article complet).
Pour cette section, vise STRICTEMENT {word_estimate} mots comme indiqué dans le plan de rédaction.
Ne répète PAS les informations déjà couvertes dans les sections précédentes.
Sois concis et va droit au but sans développements superflus.
Chaque phrase doit apporter de l'information utile, pas de remplissage.

Principe GEO / AEO obligatoire :
Cette section doit répondre de manière explicite et complète à une
intention de recherche précise. Commence par une phrase-réponse directe
(« la réponse courte d'abord »), puis développe.

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot
- Phrases naturelles et fluides, sans sur-optimisation du mot-clé
- Respecte le Style Profile du site pour le ton, le vocabulaire et le point de vue
- Ne rédige PAS les autres sections de la page
- OUTPUT : uniquement le contenu de l'article, pas de sections du briefing
- NE DÉVIE PAS du plan de rédaction : structure fixe, pas d'ajouts, pas d'omissions

Retourne UNIQUEMENT le contenu de cette section en markdown.
"""


# ── Helper Functions ─────────────────────────────────────────────────────────────

def _build_context_summary(text: str, max_words: int = 150) -> str:
    """Build a concise summary of context text for token efficiency."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def build_article_context(briefing: str) -> str:
    """Build a structured summary of the briefing for article generation (300-500 words).
    Extracts key sections: Contexte, Angle, Intention, Tonalité.
    """
    import re
    lines = briefing.split('\n')
    context_parts = []
    current_section = None
    section_content = []

    for line in lines:
        header_match = re.match(r'^##\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            # Save previous section if relevant
            if current_section and section_content:
                content = '\n'.join(section_content).strip()
                if content and len(content.split()) > 10:  # Only keep non-empty sections
                    section_name = current_section.lower()
                    # Keep only relevant sections
                    if any(kw in section_name for kw in ['contexte', 'angle', 'intention', 'tonalité', 'style']):
                        context_parts.append(f"## {current_section}\n{content}")
            # Start new section
            current_section = header_match.group(1).strip()
            section_content = []
        else:
            section_content.append(line)

    # Don't forget last section
    if current_section and section_content:
        content = '\n'.join(section_content).strip()
        if content and len(content.split()) > 10:
            section_name = current_section.lower()
            if any(kw in section_name for kw in ['contexte', 'angle', 'intention', 'tonalité', 'style']):
                context_parts.append(f"## {current_section}\n{content}")

    # Build summary
    if context_parts:
        summary = '\n\n'.join(context_parts)
        # Limit to 500 words
        words = summary.split()
        if len(words) > 500:
            summary = ' '.join(words[:500]) + "..."
        return summary
    else:
        # Fallback: simple truncation of briefing
        words = briefing.split()
        return ' '.join(words[:400]) + "..."


def extract_style_rules(briefing: str) -> str:
    """Extract style rules from briefing into a compact block (10-15 lines).
    Focuses on sentence length, paragraph structure, tone, and formatting.
    """
    import re
    lines = briefing.split('\n')
    style_rules = []
    in_style_section = False

    for line in lines:
        header_match = re.match(r'^##\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            section_name = header_match.group(1).lower()
            in_style_section = any(kw in section_name for kw in ['tonalité', 'style'])
            if in_style_section:
                style_rules.append(f"# Style Rules")
        elif in_style_section and line.strip():
            # Extract key style indicators
            if any(kw in line.lower() for kw in ['phrase', 'paragraphe', 'ton', 'voix', 'longueur', 'structure', 'liste', 'gras']):
                style_rules.append(line.strip())
            elif line.startswith('-') or line.startswith('•'):
                style_rules.append(line.strip())

    if style_rules:
        # Limit to 15 lines
        style_text = '\n'.join(style_rules[:15])
        return style_text
    else:
        # Default style rules
        return """# Style Rules
- Phrases de 10-20 mots, une idée par phrase
- Paragraphes de 2-4 phrases maximum
- Voix active : "Votre chien a besoin de..." plutôt que "Des protéines sont nécessaires..."
- Éviter le jargon non expliqué
- Pas de langage marketing agressif
- Listes à puces pour énumérations (3-6 points max)"""


def extract_writing_plan(briefing: str) -> str:
    """Extract the Plan de Rédaction section from briefing.
    Returns the complete plan section as a string.
    """
    import re
    lines = briefing.split('\n')
    plan_lines = []
    in_plan_section = False

    for line in lines:
        header_match = re.match(r'^##\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            section_name = header_match.group(1).lower()
            if 'plan' in section_name and 'rédaction' in section_name:
                in_plan_section = True
                plan_lines.append(line)
            elif in_plan_section:
                # We've reached the next section, stop
                break
        elif in_plan_section:
            plan_lines.append(line)

    if plan_lines:
        return '\n'.join(plan_lines)
    else:
        return ""


def generate_chunked_briefing(
    keyword: str,
    site_url: str,
    country: str,
    page_type: str,
    context_doc: str,
    seo_brief: str,
    system: str,
    lang: str = "fr",
) -> tuple[str, int, int]:
    """
    Generate briefing in 3 chunked calls to avoid token limits.
    Returns (full_briefing, total_input_tokens, total_output_tokens).
    """
    parts = []
    total_in, total_out = 0, 0

    # Part 1: Context & Positionnement
    logger.info("[ChunkedBriefing] Part 1 — Context & Positionnement")
    p1 = BRIEFING_PART1_CONTEXT.format(
        keyword=keyword,
        site_url=site_url,
        country=country,
        page_type=page_type,
        context_doc=context_doc or "(aucun document fourni)",
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part1, in1, out1 = _call_claude(system, p1, max_tokens=2000)
    parts.append(part1)
    total_in += in1
    total_out += out1

    # Summary for next calls
    summary1 = _build_context_summary(part1, max_words=100)

    # Part 2a: Intention & Points Clés
    logger.info("[ChunkedBriefing] Part 2a — Intention & Points Clés")
    p2a = BRIEFING_PART2_INTENTION.format(context_summary=summary1)
    part2a, in2a, out2a = _call_claude(system, p2a, max_tokens=2000)
    parts.append(part2a)
    total_in += in2a
    total_out += out2a

    # Summary for next call
    summary2a = _build_context_summary(part1 + "\n\n" + part2a, max_words=120)

    # Part 2b: Plan de Rédaction (with continuation loop for completeness)
    logger.info("[ChunkedBriefing] Part 2b — Plan de Rédaction")
    p2b = BRIEFING_PART2_PLAN.format(context_summary=summary2a)
    part2b, in2b, out2b = _call_claude(system, p2b, max_tokens=12000)
    
    # Check if plan is complete (look for "Total estimé" line)
    max_continuations = 3
    continuation_count = 0
    while "Total estimé" not in part2b and continuation_count < max_continuations:
        continuation_count += 1
        logger.info("[ChunkedBriefing] Plan de Rédaction incomplete, continuation %d/%d", continuation_count, max_continuations)
        p2b_cont = BRIEFING_PART2_PLAN_CONTINUATION.format(previous_content=part2b)
        continuation_part, in_cont, out_cont = _call_claude(system, p2b_cont, max_tokens=4000)
        part2b += "\n\n" + continuation_part
        in2b += in_cont
        out2b += out_cont
    
    parts.append(part2b)
    total_in += in2b
    total_out += out2b

    # Summary for next call
    summary2b = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b, max_words=150)

    # Part 2c: Tonalité & Style
    logger.info("[ChunkedBriefing] Part 2c — Tonalité & Style")
    p2c = BRIEFING_PART2_TONALITY.format(context_summary=summary2b)
    part2c, in2c, out2c = _call_claude(system, p2c, max_tokens=2000)
    parts.append(part2c)
    total_in += in2c
    total_out += out2c

    # Summary for next call
    summary2 = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b + "\n\n" + part2c, max_words=180)

    # Part 3a: SEO Keywords & Maillage
    logger.info("[ChunkedBriefing] Part 3a — SEO Keywords & Maillage")
    p3a = BRIEFING_PART3_SEO_KEYWORDS.format(
        context_summary=summary2,
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part3a, in3a, out3a = _call_claude(system, p3a, max_tokens=2000)
    parts.append(part3a)
    total_in += in3a
    total_out += out3a

    # Summary for next call
    summary3a = _build_context_summary(part1 + "\n\n" + part2a + "\n\n" + part2b + "\n\n" + part2c + "\n\n" + part3a, max_words=150)

    # Part 3b: SEO Metas
    logger.info("[ChunkedBriefing] Part 3b — SEO Metas")
    p3b = BRIEFING_PART3_SEO_METAS.format(
        context_summary=summary3a,
        seo_brief=seo_brief or "(aucune donnée SEO disponible)",
    )
    part3b, in3b, out3b = _call_claude(system, p3b, max_tokens=2000)
    parts.append(part3b)
    total_in += in3b
    total_out += out3b

    full_briefing = "\n\n".join(parts)
    logger.info("[ChunkedBriefing] Complete — %d total tokens", total_in + total_out)
    return full_briefing, total_in, total_out


def extract_h2_sections(briefing: str) -> list[tuple[str, str, str]]:
    """Extract H2 and H3 section titles with word estimates from briefing markdown.
    Returns list of tuples (title, level, word_estimate) where level is 'H2' or 'H3'.
    """
    import re
    sections = []
    lines = briefing.split('\n')
    current_h2 = None
    current_h3 = None

    for line in lines:
        h2_match = re.match(r'^##\s+(.+)$', line)
        h3_match = re.match(r'^###\s+(.+)$', line)
        
        if h2_match:
            current_h2 = h2_match.group(1).strip()
            current_h3 = None
            sections.append((current_h2, 'H2', ""))  # Default empty word estimate
        elif h3_match and current_h2:
            current_h3 = h3_match.group(1).strip()
            sections.append((current_h3, 'H3', ""))  # Default empty word estimate
        elif 'mots estimés' in line.lower():
            # Extract word estimate from line like "— mots estimés : 250-300"
            word_match = re.search(r'(\d+[-–]?\d*)\s*mots?', line, re.IGNORECASE)
            if word_match:
                # Associate with the most recent section (H2 or H3)
                if sections:
                    title, level, _ = sections[-1]
                    sections[-1] = (title, level, word_match.group(1))

    # If no word estimates found, return just titles with empty estimates
    if not any(word_est for _, _, word_est in sections):
        h2_pattern = re.compile(r'^##\s+(.+)$', re.MULTILINE)
        h3_pattern = re.compile(r'^###\s+(.+)$', re.MULTILINE)
        h2_matches = h2_pattern.findall(briefing)
        h3_matches = h3_pattern.findall(briefing)
        result = [(m.strip(), 'H2', "") for m in h2_matches if m.strip()]
        result.extend([(m.strip(), 'H3', "") for m in h3_matches if m.strip()])
        return result

    return sections


def calculate_max_tokens_from_word_estimate(word_estimate: str) -> int:
    """Calculate max_tokens from word estimate string (e.g., '250-300' or '200').
    Uses 1 word ≈ 1.5 tokens ratio with 30% buffer for markdown formatting.
    """
    import re
    if not word_estimate:
        return 800  # Default fallback

    # Extract the maximum word count from estimate (e.g., "250-300" → 300)
    match = re.search(r'(\d+)[-–]?(\d+)?', word_estimate)
    if not match:
        return 800

    if match.group(2):  # Range like "250-300"
        max_words = int(match.group(2))
    else:  # Single number like "300"
        max_words = int(match.group(1))

    # Convert words to tokens (1 word ≈ 1.5 tokens) + 30% buffer
    max_tokens = int(max_words * 1.5 * 1.3)

    # Ensure minimum of 500 tokens and maximum of 2000
    return max(500, min(max_tokens, 2000))


def filter_briefing_for_content(briefing: str) -> str:
    """Filter briefing to remove only SEO/technical sections, keep content and style sections for model context."""
    import re
    lines = briefing.split('\n')
    filtered = []
    skip_section = False
    skip_keywords = ['seo', 'maillage', 'métas', 'checklist', 'mots-clés', 'faq']
    for line in lines:
        header_match = re.match(r'^##\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            header_text = header_match.group(1).lower()
            if any(kw in header_text for kw in skip_keywords):
                skip_section = True
                continue
            else:
                skip_section = False
        if not skip_section:
            filtered.append(line)
    return '\n'.join(filtered)


def generate_article_by_sections(
    briefing: str,
    system: str,
    h2_sections: list[tuple[str, str, str]] | None = None,
) -> tuple[str, int, int]:
    """
    Generate article section by section to avoid token limits.
    If h2_sections is None, extract them from briefing.
    Uses structured context and treats H2/H3 independently.
    Returns (full_article, total_input_tokens, total_output_tokens).
    """
    # Filter briefing to remove SEO/maillage/métas sections before building context
    clean_brief = filter_briefing_for_content(briefing)
    
    # Build structured context (300-500 words) from clean briefing
    article_context = build_article_context(clean_brief)
    
    # Extract compact style rules
    style_rules = extract_style_rules(briefing)

    if h2_sections is None:
        h2_sections = extract_h2_sections(briefing)
        logger.info("[ChunkedArticle] Extracted %d sections (H2/H3) from briefing", len(h2_sections))

    if not h2_sections:
        logger.warning("[ChunkedArticle] No sections found, falling back to single call")
        return _call_claude(system, ARTICLE_PROMPT.format(briefing=article_context, keyword=""), max_tokens=6000)

    sections = []
    total_in = 0
    total_out = 0
    continuation = ""
    seen_headers = set()

    for idx, (title, level, word_est) in enumerate(h2_sections, 1):
        # Calculate max_tokens based on word estimate
        max_tokens = calculate_max_tokens_from_word_estimate(word_est)
        logger.info("[ChunkedArticle] Section %d/%d — %s (%s) — words: %s — max_tokens: %d", 
                    idx, len(h2_sections), title, level, word_est or "N/A", max_tokens)

        # Build section spec with proper heading level
        if level == 'H2':
            section_spec = f"Section : {title}\nRédige cette section complète avec ses sous-parties H3 si nécessaire. NE commence PAS par ## {title}, rédige directement le contenu."
        else:
            section_spec = f"Sous-section : {title}\nRédige cette sous-section. NE commence PAS par ### {title}, rédige directement le contenu."

        # Build compact prompt with structured context
        prompt = f"""{article_context}

{style_rules}

Section à rédiger :
{section_spec}

IMPORTANT :
- Rédige UNIQUEMENT du texte d'article destiné aux lecteurs.
- N'inclus AUCUN élément de briefing (Contexte & Positionnement, Intention & Points clés, SEO & Technique, Mots-clés, Maillage, Métas, etc.).
- Ne mentionne PAS les blocs "Recommandations de style", "Mots-clés à intégrer", "Recommandations de maillage", "Meta Title", "Meta Description", etc.
- Ton texte doit être un article lisible pour le grand public, pas un briefing pour rédacteur.
- Pour cette section, vise STRICTEMENT {word_est or "200-300"} mots.
- Ne répète PAS les informations déjà couvertes dans les sections précédentes.
- NE commence PAS par le titre (## ou ###), rédige directement le contenu du paragraphe.

Retourne UNIQUEMENT le contenu de cette section en markdown (sans le titre).
"""
        if continuation:
            prompt = f"{continuation}\n\n{prompt}"

        section, in_t, out_t = _call_claude(system, prompt, max_tokens=max_tokens)

        # Add header to section for consistency
        if level == 'H2':
            section = f"## {title}\n\n{section}"
        else:
            section = f"### {title}\n\n{section}"

        # Post-process: remove duplicate headers
        if idx > 1:
            import re
            lines = section.split('\n')
            filtered_lines = []
            for line in lines:
                header_match = re.match(r'^(#{1,3})\s+(.+)$', line)
                if header_match:
                    header_text = header_match.group(2).strip().lower()
                    if header_text in seen_headers:
                        continue
                    seen_headers.add(header_text)
                filtered_lines.append(line)
            section = '\n'.join(filtered_lines)
        else:
            import re
            for match in re.finditer(r'^(#{1,3})\s+(.+)$', section, re.MULTILINE):
                seen_headers.add(match.group(2).strip().lower())

        sections.append(section)
        total_in += in_t
        total_out += out_t

        # Prepare continuation instruction
        continuation = f"CONTINUE from previous output. DO NOT repeat headers already written. Continue directly with the next content.\n\nPrevious output ended with:\n{section[-500:]}"

    full_article = "\n\n".join(sections)
    logger.info("[ChunkedArticle] Complete — %d chunks, %d total tokens", len(sections), total_in + total_out)
    return full_article, total_in, total_out


# ── Claude caller ──────────────────────────────────────────────────────────────

def _call_claude(system: str, user_prompt: str,
                 max_tokens: int | None = None) -> tuple[str, int, int]:
    """Single Claude Sonnet call. Returns (text, input_tokens, output_tokens)."""
    client  = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model      = config.CLAUDE_SONNET,
        max_tokens = max_tokens or config.MAX_TOKENS_PER_PASS,
        system     = system,
        messages   = [{"role": "user", "content": user_prompt}],
    )
    text = message.content[0].text.strip() if message.content else ""
    in_tokens = message.usage.input_tokens if hasattr(message.usage, 'input_tokens') else 0
    out_tokens = message.usage.output_tokens if hasattr(message.usage, 'output_tokens') else 0
    return (text, in_tokens, out_tokens)


# ── Public API ─────────────────────────────────────────────────────────────────

def run_writing_pipeline(
    keyword: str,
    style_context: str,
    seo_brief: str,
) -> ArticleOutput:
    """
    Legacy 4-pass pipeline (kept for reference).
    Returns a populated ArticleOutput.
    """
    output = ArticleOutput(keyword=keyword)
    system = _build_system(style_context, seo_brief)

    # ── Pass 1 : Introduction ─────────────────────────────────────────────────
    logger.info("[Writer] Passe 1 — Introduction")
    p1_prompt = PASS1_PROMPT.format(keyword=keyword)
    output.introduction, in1, out1 = _call_claude(system, p1_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in1, out1))
    output.pass_logs.append(f"PASS1 OK — {len(output.introduction.split())} mots")

    # ── Pass 2 : Plan H2/H3 ──────────────────────────────────────────────────
    logger.info("[Writer] Passe 2 — Plan H2/H3")
    p2_prompt = PASS2_PROMPT.format(pass1_output=output.introduction)
    output.plan_h2_h3, in2, out2 = _call_claude(system, p2_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in2, out2))
    output.pass_logs.append(f"PASS2 OK — {output.plan_h2_h3.count('##')} sections")

    # ── Pass 3 : Corps ────────────────────────────────────────────────────────
    logger.info("[Writer] Passe 3 — Corps de l'article")
    p3_prompt = PASS3_PROMPT.format(
        pass1_output     = output.introduction,
        pass2_output     = output.plan_h2_h3,
        target_word_count= config.TARGET_WORD_COUNT,
    )
    output.body, in3, out3 = _call_claude(system, p3_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in3, out3))
    output.pass_logs.append(f"PASS3 OK — {len(output.body.split())} mots")

    # ── Pass 4 : Méta + Révision ──────────────────────────────────────────────
    logger.info("[Writer] Passe 4 — Méta + révision finale")
    full_draft = f"{output.introduction}\n\n{output.body}"
    p4_prompt  = PASS4_PROMPT.format(full_draft=full_draft)
    raw_p4, in4, out4 = _call_claude(system, p4_prompt)
    output.cost.passes.append(PassCost(config.CLAUDE_SONNET, in4, out4))

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
