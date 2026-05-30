# Content Agent

Agent de rédaction SEO automatisé qui génère des articles dans le ton éditorial exact du site cible.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        agent.py                             │
│                   (orchestrateur central)                   │
└──────────┬────────────────┬────────────────┬────────────────┘
           │                │                │
    ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
    │tone_analyzer│  │seo_intel    │  │  writer     │
    │             │  │             │  │             │
    │ Firecrawl   │  │ DataForSEO  │  │ Pass 1 Intro│
    │ +           │  │ SERP + PAA  │  │ Pass 2 Plan │
    │ Claude Opus │  │ +           │  │ Pass 3 Body │
    │ → JSON      │  │ GSC         │  │ Pass 4 Meta │
    │ style profile│  │ → SEO brief │  │ (Sonnet)    │
    └─────────────┘  └─────────────┘  └─────────────┘
           │                │                │
           └────────────────┴────────────────┘
                            │
                    ┌───────▼───────┐
                    │  outputs/     │
                    │  slug.md      │
                    │  slug.json    │
                    └───────────────┘
```

## Installation

```bash
cd ~/Desktop/content-agent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Remplis les clés API dans .env
```

## Configuration Google Search Console

1. Va sur [Google Cloud Console](https://console.cloud.google.com)
2. Crée un projet → Active l'API **Search Console**
3. Crée des identifiants OAuth2 (type : **Desktop App**)
4. Télécharge le JSON → renomme-le `gsc_credentials.json` dans le dossier
5. Au premier lancement, une fenêtre browser s'ouvre pour l'auth

## Utilisation

### CLI

```bash
# Génère un article (style profile mis en cache automatiquement)
python agent.py --keyword "rénovation cuisine Bruxelles"

# Force le re-scraping du site pour reconstruire le style profile
python agent.py --keyword "parquet chêne massif" --refresh-style
```

### Webhook (Make / n8n)

```bash
# Démarre le serveur webhook
python webhook.py
```

**Endpoint** : `POST http://localhost:8080/generate`

```json
{
  "keyword": "rénovation cuisine Bruxelles",
  "refresh_style": false
}
```

**Health check** : `GET http://localhost:8080/health`

### Depuis Make (Integromat)

1. Module **Webhook** → écoute l'événement Notion (formulaire keyword)
2. Module **HTTP Make a Request** → `POST localhost:8080/generate` (ou ngrok URL)
3. Module **Notion Create Page** → injecte `meta_title`, `meta_description`, `full_article`

### Depuis n8n

1. Nœud **Webhook** trigger
2. Nœud **HTTP Request** → POST `/generate`
3. Nœud **Notion** → crée la page avec le contenu

## Flux de génération

| Passe | Modèle | Input | Output |
|-------|--------|-------|--------|
| 1 | Claude Sonnet | keyword + style profile + SEO brief | Introduction (100-150 mots) |
| 2 | Claude Sonnet | Passe 1 + style profile + SEO brief | Plan H2/H3 enrichi |
| 3 | Claude Sonnet | Passe 1 + Passe 2 + style profile + SEO brief | Corps complet |
| 4 | Claude Sonnet | Article complet | Méta-title, méta-desc, révision + CTA |

Le **Style Profile** (généré par Claude Opus) est injecté dans chaque passe comme contexte système.

## Outputs

Chaque génération produit dans `outputs/` :

- `<slug>_<timestamp>.md` — article formaté lisible
- `<slug>_<timestamp>.json` — bundle complet pour intégration CMS

```json
{
  "keyword": "...",
  "meta_title": "...",
  "meta_description": "...",
  "plan": "...",
  "full_article": "...",
  "word_count": 1523,
  "seo": { "secondary_keywords": [], "paa": [], "cannibalisations": [] },
  "style_profile": { ... }
}
```

## Variables d'environnement

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Clé API Anthropic |
| `DATAFORSEO_LOGIN` | ✅ | Email DataForSEO |
| `DATAFORSEO_PASSWORD` | ✅ | Mot de passe DataForSEO |
| `FIRECRAWL_API_KEY` | Recommandé | Clé Firecrawl (fallback BS4 si absent) |
| `GSC_CREDENTIALS_FILE` | Optionnel | Fichier credentials GSC |
| `GSC_SITE_URL` | Optionnel | URL du site dans GSC |
| `TARGET_SITE_URL` | ✅ | Site à analyser pour le style profile |
| `WEBHOOK_SECRET` | Optionnel | Token de sécurité webhook |
