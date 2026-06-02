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
import json
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


@dataclass
class ArticleSectionBlock:
    title: str
    word_estimate: str = ""
    intent: str = ""
    must_cover: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    children: list[dict] = field(default_factory=list)


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


def _build_briefing_system(style_context: str, seo_brief: str, lang: str = "fr") -> str:
    return _build_system(style_context, seo_brief, lang=lang)


def _build_article_system(style_context: str, lang: str = "fr") -> str:
    lang_rule = _LANG_RULES.get(lang, _LANG_RULES["fr"])
    return f"""{lang_rule}
Tu rédiges uniquement du contenu d'article destiné aux lecteurs finaux.
Tu n'ajoutes JAMAIS de sections de briefing, métadonnées, recommandations SEO ou notes internes.
Tu respectes strictement la section demandée et tu n'inventes pas de nouveaux H2/H3.
Chaque phrase doit apporter une information utile, concrète et non répétitive.

{style_context}
"""


def _build_meta_system(lang: str = "fr") -> str:
    lang_rule = _LANG_RULES.get(lang, _LANG_RULES["fr"])
    return f"""{lang_rule}
Tu produis uniquement des métadonnées et des vérifications structurelles.
Tu ne réécris jamais l'article complet.
Tu respectes strictement le format de sortie demandé.
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
- Ne répète jamais les sources, concurrents, angle différenciant ou notes internes du briefing.
- Évite les formulations anthropomorphiques ou inadaptées aux animaux : un chien ne "mérite pas de savoir" et ne "prospère" pas.
- Adresse les conseils au maître/propriétaire quand il s'agit de compréhension, choix ou décision.
- Évite les répétitions d'idées entre paragraphes et sections.
- Nuance les affirmations métier : évite les positions dogmatiques, alarmistes ou trop catégoriques.
- Adapte la validité du contenu au secteur du client : distingue les faits établis, les recommandations générales et les cas particuliers.
- Si tu cites des chiffres, taux, prix, normes ou comparaisons techniques, précise le contexte de lecture et les limites utiles.
"""


# ── Passe Méta + Révision ──────────────────────────────────────────────────────

META_PROMPT = """\
Tu reçois l'article complet ci-dessous. Génère les métas et vérifie la structure.

## Article
{full_article}

Tâches :
1. Méta-title : 50-60 caractères, mot-clé en début, accrocheur, pas de majuscule à chaque mot
2. Méta-description : 140-160 caractères, bénéfice clair, verbe d'action
3. CTA final : 1 phrase de CTA de fin d'article aligné avec le style du site
4. Vérification GEO : pour chaque H2/H3, vérifie qu'il commence par une phrase-réponse
   directe (« réponse courte d'abord »). Signale les sections non conformes, sans réécrire l'article.

Interdictions absolues :
- Ne réécris PAS l'article complet.
- Ne modifie PAS la structure H2/H3.
- Ne génère PAS de version révisée de l'article.
- Ne retourne AUCUN contenu d'article, sauf le CTA final.

Retourne EXACTEMENT dans ce format (balises incluses, rien d'autre avant ===META_TITLE===) :

===META_TITLE===
<meta title ici>
===META_DESCRIPTION===
<meta description ici>
===GEO_CHECK===
<une ligne par H2/H3 : Titre section — intention couverte : oui/non>
===CTA_FINAL===
<une phrase CTA>
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
   Si la liste est vide, signale que les sources locales manquent au lieu d'inventer des sources.
   N'utilise pas de sources, statistiques, prix, réglementations ou habitudes propres à un autre pays que {country}.

Données SEO disponibles :
{seo_brief}

Règles absolues :
- Pas d'emoji dans le texte
- Pas de majuscule à chaque mot des titres
- Phrases naturelles et fluides
- Langue : français, néerlandais ou anglais selon la langue dominante du site
- Marché : toutes les recommandations doivent être adaptées à {country}, sans extrapolation depuis la France si {country} n'est pas la France

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

Termine par un bloc JSON machine-readable exactement sous cette forme :
```json
{{
  "target_total_words": [1200, 1800],
  "sections": [
    {{
      "title": "Titre H2",
      "intent": "intention spécifique",
      "target_words": [250, 350],
      "must_cover": ["point obligatoire"],
      "avoid": ["sujet réservé à une autre section"],
      "children": [
        {{
          "title": "Titre H3",
          "intent": "intention spécifique",
          "target_words": [100, 160],
          "must_cover": ["point obligatoire"],
          "avoid": []
        }}
      ]
    }}
  ]
}}
```

Retourne UNIQUEMENT la section ## Plan de Rédaction complète, avec le plan lisible puis le bloc JSON.
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
- Évite les formulations anthropomorphiques ou inadaptées aux animaux : un chien ne "mérite pas de savoir" et ne "prospère" pas.
- Adresse les conseils au maître/propriétaire quand il s'agit de compréhension, choix ou décision.
- Ne répète pas deux fois la même idée ou le même exemple dans cette section.

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
        # Limit to 300 mots (reduced from 500 for efficiency)
        words = summary.split()
        if len(words) > 300:
            summary = ' '.join(words[:300]) + "..."
        return summary
    else:
        # Fallback: simple truncation of briefing
        words = briefing.split()
        return ' '.join(words[:400]) + "..."


def extract_style_rules(briefing: str) -> str:
    """Return strict style rules for article generation without editorial briefing content."""
    import re
    lines = briefing.split('\n')
    style_rules = []
    in_style_section = False
    allowed_keywords = [
        'phrase', 'phrases', 'paragraphe', 'paragraphes', 'voix active',
        'jargon', 'liste', 'listes', 'ton ', 'ton:', 'vocabulaire', 'style',
        'gras', 'markdown', 'lisible', 'naturel', 'fluide',
    ]
    forbidden_keywords = [
        'mots-clés', 'mots clés', 'maillage', 'meta title', 'meta description',
        'angle', 'intention', 'points clés', 'seo', 'source', 'serp',
        'longueur cible', 'répartition', 'éléments visuels',
    ]

    for line in lines:
        header_match = re.match(r'^##\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            section_name = header_match.group(1).lower()
            in_style_section = any(kw in section_name for kw in ['tonalité', 'style'])
        elif in_style_section and line.strip():
            clean_line = line.strip()
            lower_line = clean_line.lower()
            if any(kw in lower_line for kw in forbidden_keywords):
                continue
            if (clean_line.startswith('-') or clean_line.startswith('•')) and any(
                kw in lower_line for kw in allowed_keywords
            ):
                style_rules.append(clean_line)
            elif len(clean_line) <= 120 and any(kw in lower_line for kw in allowed_keywords):
                style_rules.append(line.strip())

    default_rules = """# Style Rules
- Phrases de 10-20 mots, une idée par phrase
- Paragraphes de 2-4 phrases maximum
- Voix active : "Votre chien a besoin de..." plutôt que "Des protéines sont nécessaires..."
- Éviter le jargon non expliqué
- Pas de langage marketing agressif
- Listes à puces pour énumérations (3-6 points max)
- Éviter les formulations anthropomorphiques ou inadaptées aux animaux : un chien ne "mérite pas de savoir" et ne "prospère" pas.
- Adresser les conseils au maître/propriétaire quand il s'agit de compréhension, choix ou décision.
- Éviter les répétitions d'idées, d'exemples et de formulations entre paragraphes.
- Ton expert mais accessible : pédagogique, rassurant, bienveillant, sans jargon inutile.
- Éviter les formulations dogmatiques, extrêmes ou alarmistes ; préférer les nuances factuelles.
- Adapter les affirmations au secteur du client : santé, nutrition, finance, droit, immobilier, technique, SaaS, e-commerce, etc.
- Distinguer clairement les faits établis, les recommandations générales, les hypothèses et les cas particuliers.
- Ne pas transformer une recommandation contextuelle en vérité absolue.
- Si le sujet implique des chiffres, prix, taux, normes, performances ou comparaisons techniques, préciser le contexte, les limites et les critères de comparaison.
- Pour les sujets sensibles ou réglementés, rester prudent : pas de promesse absolue, pas de garantie excessive, pas de conseil médical/juridique/financier personnalisé.
- Ne jamais inclure de sections de briefing, notes SEO, métas, maillage ou recommandations internes"""

    if style_rules:
        style_text = '\n'.join(style_rules[:8])
        return f"{default_rules}\n{style_text}"
    return default_rules


def extract_writing_plan(briefing: str) -> str:
    """Extract the Plan de Rédaction section from briefing.
    Returns ONLY H2/H3 titles with word estimates, stripping all editorial context.
    """
    import re
    lines = briefing.split('\n')
    plan_lines = []
    in_plan_section = False

    for line in lines:
        header_match = re.match(r'^(#{2,3})\s+(.+)$', line, re.IGNORECASE)
        if header_match:
            section_name = header_match.group(2).lower()
            if 'plan' in section_name and 'rédaction' in section_name:
                in_plan_section = True
                plan_lines.append(line)
            elif in_plan_section:
                # We've reached the next section, stop
                break
        elif in_plan_section:
            # Only keep lines that look like word estimates or brief descriptions
            # Skip long editorial paragraphs
            if len(line.strip()) < 100 or 'mots estimés' in line.lower() or 'intention' in line.lower():
                plan_lines.append(line)

    if plan_lines:
        return '\n'.join(plan_lines)
    else:
        return ""


def _strip_briefing_leakage(text: str) -> str:
    """Remove lines that look like briefing metadata leaking into article output."""
    import re
    bad_patterns = [
        r"^\*\*Mots-clés",
        r"^\*\*Recommandations",
        r"^\*\*Angle",
        r"^\*\*Points clés",
        r"^\*\*Intention",
        r"^\*\*Tonalité",
        r"^\*\*Style",
        r"^Meta (Title|Description)",
        r"^#+\s+(SEO|Métas|Maillage|Checklist|Contexte & Positionnement|Intention & Points)",
        r"---\s*(BRIEFING|FIN BRIEFING)",
    ]
    lines = text.split('\n')
    cleaned = []
    skip_block = False
    for line in lines:
        # Skip known briefing section headers and their content
        if any(re.match(p, line.strip(), re.IGNORECASE) for p in bad_patterns):
            skip_block = True
            continue
        # Resume at next ## heading that isn't in bad_patterns
        if skip_block and re.match(r'^##\s+', line) and not any(
            re.match(p, line.strip(), re.IGNORECASE) for p in bad_patterns
        ):
            skip_block = False
        if not skip_block:
            cleaned.append(line)
    return '\n'.join(cleaned).strip()


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
    """Extract H2 and H3 section titles with word estimates from the Plan de Rédaction only.
    Returns list of tuples (title, level, word_estimate) where level is 'H2' or 'H3'.
    """
    import re
    sections = []
    lines = briefing.split('\n')
    current_h2 = None
    in_plan_section = False
    forbidden_titles = [
        'contexte', 'positionnement', 'intention', 'points clés', 'plan de rédaction',
        'tonalité', 'style', 'seo', 'technique', 'métas', 'metas', 'sources',
        'checklist', 'mots-clés', 'mots clés', 'maillage', 'feedback utilisateur',
    ]

    for line in lines:
        h2_match = re.match(r'^##\s+(.+)$', line)
        h3_match = re.match(r'^###\s+(.+)$', line)

        if h2_match:
            title = h2_match.group(1).strip()
            title_lower = title.lower()
            if 'plan' in title_lower and 'rédaction' in title_lower:
                in_plan_section = True
                current_h2 = None
                continue
            if in_plan_section and any(kw in title_lower for kw in forbidden_titles):
                break
            if in_plan_section:
                current_h2 = title
                sections.append((current_h2, 'H2', ""))
            continue

        if h3_match and in_plan_section and current_h2:
            title = h3_match.group(1).strip()
            title_lower = title.lower()
            if not any(kw in title_lower for kw in forbidden_titles):
                sections.append((title, 'H3', ""))
            continue

        if in_plan_section and 'mots estimés' in line.lower():
            # Extract word estimate from line like "— mots estimés : 250-300"
            word_match = re.search(r'(\d+[-–]?\d*)\s*mots?', line, re.IGNORECASE)
            if word_match:
                # Associate with the most recent section (H2 or H3)
                if sections:
                    title, level, _ = sections[-1]
                    sections[-1] = (title, level, word_match.group(1))

    return sections


def _words_range_to_estimate(value) -> str:
    if isinstance(value, list) and value:
        nums = [int(v) for v in value if isinstance(v, int) or str(v).isdigit()]
        if len(nums) >= 2:
            return f"{nums[0]}-{nums[1]}"
        if len(nums) == 1:
            return str(nums[0])
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return ""


def extract_article_blocks(briefing: str) -> list[ArticleSectionBlock]:
    """Extract a hierarchical H2/H3 article plan, preferring JSON and falling back to markdown."""
    import re
    json_candidates = re.findall(r'```json\s*(.*?)\s*```', briefing, re.DOTALL | re.IGNORECASE)
    for candidate in reversed(json_candidates):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        blocks = []
        for section in data.get("sections", []):
            if not isinstance(section, dict) or not section.get("title"):
                continue
            children = []
            for child in section.get("children", []) or []:
                if isinstance(child, dict) and child.get("title"):
                    children.append({
                        "title": str(child.get("title", "")).strip(),
                        "intent": str(child.get("intent", "")).strip(),
                        "word_estimate": _words_range_to_estimate(child.get("target_words")),
                        "must_cover": child.get("must_cover", []) if isinstance(child.get("must_cover", []), list) else [],
                        "avoid": child.get("avoid", []) if isinstance(child.get("avoid", []), list) else [],
                    })
            blocks.append(ArticleSectionBlock(
                title=str(section.get("title", "")).strip(),
                word_estimate=_words_range_to_estimate(section.get("target_words")),
                intent=str(section.get("intent", "")).strip(),
                must_cover=section.get("must_cover", []) if isinstance(section.get("must_cover", []), list) else [],
                avoid=section.get("avoid", []) if isinstance(section.get("avoid", []), list) else [],
                children=children,
            ))
        if blocks:
            return blocks

    blocks = []
    current_block = None
    for title, level, word_est in extract_h2_sections(briefing):
        if level == "H2":
            current_block = ArticleSectionBlock(title=title, word_estimate=word_est)
            blocks.append(current_block)
        elif level == "H3" and current_block:
            current_block.children.append({
                "title": title,
                "intent": "",
                "word_estimate": word_est,
                "must_cover": [],
                "avoid": [],
            })
    return blocks


def _parse_word_estimate_bounds(word_estimate: str, default_min: int = 200, default_max: int = 300) -> tuple[int, int]:
    import re
    if not word_estimate:
        return default_min, default_max
    match = re.search(r'(\d+)[-–]?(\d+)?', word_estimate)
    if not match:
        return default_min, default_max
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    return min(low, high), max(low, high)


def _count_text_words(text: str) -> int:
    import re
    return len(re.findall(r"\b[\wÀ-ÖØ-öø-ÿ'-]+\b", text))


def _looks_truncated(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped[-1] not in ".!?»)”]":
        return True
    last_words = stripped.split()[-4:]
    if last_words and last_words[-1].lower().strip(" ,;:") in {"de", "du", "des", "à", "au", "aux", "et", "ou", "pour", "avec", "sans", "dans", "sur", "par"}:
        return True
    if stripped.count("(") > stripped.count(")") or stripped.count("[") > stripped.count("]"):
        return True
    return False


def _section_validation_issues(text: str, min_words: int, max_words: int) -> list[str]:
    issues = []
    wc = _count_text_words(text)
    if wc < int(min_words * 0.75):
        issues.append(f"too_short:{wc}/{min_words}")
    if wc > int(max_words * 1.6):
        issues.append(f"too_long:{wc}/{max_words}")
    if _looks_truncated(text):
        issues.append("looks_truncated")
    if any(marker in text.lower() for marker in ["meta title", "meta description", "mots-clés à intégrer", "recommandations de maillage"]):
        issues.append("briefing_leakage")
    lower_text = text.lower()
    if any(marker in lower_text for marker in ["mérite de savoir", "mérite aussi de savoir", "ne peut tout simplement pas prospérer"]):
        issues.append("animal_wording")
    if any(marker in lower_text for marker in ["toujours", "jamais", "sans aucun risque", "garanti à 100%", "source principale d'énergie"]):
        issues.append("too_categorical_claim")
    return issues


def _repair_article_block(
    system: str,
    style_rules: str,
    block_title: str,
    section_text: str,
    target_words: str,
    issues: list[str],
) -> tuple[str, int, int]:
    prompt = f"""{style_rules}

La section suivante est incomplète ou non conforme.

Titre H2 : {block_title}
Mots cibles : {target_words}
Problèmes détectés : {', '.join(issues)}

Section actuelle :
{section_text}

Réécris UNIQUEMENT cette section complète.
Garde le même titre H2 et les mêmes sous-titres H3 s'ils existent.
Ne génère aucune note de briefing, aucun méta contenu, aucun commentaire.
Corrige les formulations anthropomorphiques ou inadaptées si le sujet concerne des animaux.
Nuance les affirmations métier trop catégoriques.
Clarifie les chiffres, taux, prix, normes ou comparaisons techniques en précisant le contexte et les limites utiles.
Termine par une phrase complète.
"""
    max_tokens = max(1200, calculate_max_tokens_from_word_estimate(target_words) + 600)
    return _call_claude(system, prompt, max_tokens=min(max_tokens, 3000))


def _continue_article_block(
    system: str,
    style_rules: str,
    block_title: str,
    section_text: str,
) -> tuple[str, int, int]:
    prompt = f"""{style_rules}

La section suivante est coupée en fin de texte.

Titre H2 : {block_title}

Fin actuelle de la section :
{section_text[-900:]}

Continue UNIQUEMENT la fin de cette section.
Ne répète pas le début.
N'ajoute pas de nouveau H2.
Termine par une phrase complète et conclusive.
Retourne uniquement le texte à ajouter.
"""
    return _call_claude(system, prompt, max_tokens=900)


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
    If h2_sections is None, extract a hierarchical plan from briefing.
    Uses structured H2 blocks and repairs incomplete sections.
    Returns (full_article, total_input_tokens, total_output_tokens).
    """
    # Extract compact style rules only
    style_rules = extract_style_rules(briefing)

    if h2_sections:
        blocks = []
        current_block = None
        for title, level, word_est in h2_sections:
            if level == "H2":
                current_block = ArticleSectionBlock(title=title, word_estimate=word_est)
                blocks.append(current_block)
            elif level == "H3" and current_block:
                current_block.children.append({
                    "title": title,
                    "intent": "",
                    "word_estimate": word_est,
                    "must_cover": [],
                    "avoid": [],
                })
    else:
        blocks = extract_article_blocks(briefing)
        logger.info("[ChunkedArticle] Extracted %d H2 blocks from briefing", len(blocks))

    if not blocks:
        logger.warning("[ChunkedArticle] No article blocks found. Using safe single-call fallback.")
        safe_prompt = f"""{style_rules}

Le plan structuré n'a pas pu être extrait automatiquement.
Rédige l'article final à partir du briefing ci-dessous, mais sans jamais reprendre les sources,
concurrents, notes internes, angle différenciant, métas ou recommandations SEO.

Briefing :
{briefing}

Règles strictes :
- Retourne uniquement l'article final en markdown.
- Ne commence pas par une liste de sources ou de concurrents.
- Ne génère pas de notes de briefing.
- Termine par une phrase complète.
"""
        text, in_t, out_t = _call_claude(system, safe_prompt, max_tokens=6000)
        text = _strip_briefing_leakage(text)
        if _looks_truncated(text):
            addition, cont_in, cont_out = _continue_article_block(system, style_rules, "Article", text)
            text = f"{text.rstrip()} {addition.strip()}".strip()
            in_t += cont_in
            out_t += cont_out
        return text, in_t, out_t

    sections = []
    total_in = 0
    total_out = 0
    covered_summary = []
    outline = "\n".join(f"{i}. {block.title}" for i, block in enumerate(blocks, 1))

    for idx, block in enumerate(blocks, 1):
        child_specs = []
        child_word_estimates = []
        for child in block.children:
            child_specs.append(
                f"- ### {child['title']} — intention : {child.get('intent') or 'non précisée'} — mots : {child.get('word_estimate') or '100-180'}"
            )
            child_word_estimates.append(child.get("word_estimate") or "")
        children_block = "\n".join(child_specs) if child_specs else "Aucun H3 imposé."

        min_words, max_words = _parse_word_estimate_bounds(block.word_estimate, 250, 380)
        for child_est in child_word_estimates:
            c_min, c_max = _parse_word_estimate_bounds(child_est, 100, 160)
            min_words += c_min
            max_words += c_max
        target_words = f"{min_words}-{max_words}"
        max_tokens = min(max(calculate_max_tokens_from_word_estimate(target_words) + 700, 1200), 3000)
        logger.info("[ChunkedArticle] H2 block %d/%d — %s — words: %s — max_tokens: %d",
                    idx, len(blocks), block.title, target_words, max_tokens)

        must_cover = "\n".join(f"- {item}" for item in block.must_cover) or "- Aucun point obligatoire explicite."
        avoid = "\n".join(f"- {item}" for item in block.avoid) or "- Ne répète pas les sections précédentes."
        covered = "\n".join(covered_summary[-5:]) or "Aucune section encore rédigée."
        prompt = f"""{style_rules}

---
Plan global de l'article :
{outline}

Sections déjà couvertes :
{covered}

Bloc H2 à rédiger :
## {block.title}
Intention : {block.intent or "répondre clairement à l'intention de cette section"}
Mots cibles pour ce bloc complet : {target_words}

Sous-sections H3 à inclure dans ce bloc :
{children_block}

Points obligatoires :
{must_cover}

À éviter :
{avoid}

Règles strictes :
- Rédige UNIQUEMENT du texte destiné aux lecteurs finaux (style article de blog / guide).
- INTERDIT d'inclure : titres de sections du briefing, bullets "Mots-clés à intégrer",
  "Recommandations de maillage", "Meta Title", "Angle différenciant", "Points clés", etc.
- Commence directement par le titre "## {block.title}".
- Inclus uniquement les H3 listés ci-dessus, s'il y en a.
- Ne répète PAS ce qui est déjà couvert dans les sections précédentes.
- Ne répète PAS la même idée, le même exemple ou la même formule à l'intérieur du bloc.
- Utilise des termes précis et adaptés : pour un animal, évite les verbes anthropomorphiques ou inadaptés comme "mériter de savoir" ou "prospérer".
- Adresse les actions de choix, compréhension et décision au maître/propriétaire, pas au chien.
- Reste nuancé sur les sujets métier : pas de propos alarmiste, dogmatique ou trop catégorique.
- Adapte les affirmations au secteur du client et signale les cas particuliers si une règle dépend du contexte.
- Si tu cites des chiffres, taux, prix, performances, normes ou comparaisons techniques, précise les critères et limites de comparaison.
- Pour les sujets sensibles ou réglementés, ne donne pas de conseil personnalisé et évite toute promesse absolue.
- Vise {target_words} mots pour tout le bloc.
- Structure GEO : commence par une phrase-réponse directe, puis développe.
- Termine par une phrase complète.

Retourne UNIQUEMENT ce bloc H2 complet en markdown.
"""
        section, in_t, out_t = _call_claude(system, prompt, max_tokens=max_tokens)

        section = _strip_briefing_leakage(section)
        if not section.lstrip().startswith("##"):
            section = f"## {block.title}\n\n{section}"
        issues = _section_validation_issues(section, min_words, max_words)
        if issues:
            logger.warning("[ChunkedArticle] Repairing block '%s' because: %s", block.title, ", ".join(issues))
            repaired, repair_in, repair_out = _repair_article_block(
                system=system,
                style_rules=style_rules,
                block_title=block.title,
                section_text=section,
                target_words=target_words,
                issues=issues,
            )
            repaired = _strip_briefing_leakage(repaired)
            if repaired.lstrip().startswith("##"):
                section = repaired
            else:
                section = f"## {block.title}\n\n{repaired}"
            in_t += repair_in
            out_t += repair_out
            if _looks_truncated(section):
                logger.warning("[ChunkedArticle] Continuing still-truncated block '%s'", block.title)
                addition, cont_in, cont_out = _continue_article_block(
                    system=system,
                    style_rules=style_rules,
                    block_title=block.title,
                    section_text=section,
                )
                section = f"{section.rstrip()} {addition.strip()}".strip()
                in_t += cont_in
                out_t += cont_out

        sections.append(section)
        total_in += in_t
        total_out += out_t
        section_words = _count_text_words(section)
        covered_summary.append(f"- {block.title} : {section_words} mots rédigés")

    full_article = "\n\n".join(sections)
    logger.info("[ChunkedArticle] Complete — %d H2 blocks, %d total tokens", len(sections), total_in + total_out)
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
    stop_reason = getattr(message, "stop_reason", "")
    if stop_reason == "max_tokens":
        logger.warning("[Claude] Output stopped because max_tokens=%s was reached", max_tokens or config.MAX_TOKENS_PER_PASS)
    return (text, in_tokens, out_tokens)


# ── Public API ─────────────────────────────────────────────────────────────────

def run_writing_pipeline(
    keyword: str,
    style_context: str,
    seo_brief: str,
) -> ArticleOutput:
    """
    LEGACY 4-pass pipeline.
    Utilisé uniquement par agent.py (CLI) pour l'instant.
    La version Streamlit utilise generate_chunked_briefing + generate_article_by_sections.
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
