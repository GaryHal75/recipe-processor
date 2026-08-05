# Recipe Processor

Recipe Processor is a local recipe knowledge service for chatbot and home-automation integrations.

It turns recipe documents into a searchable structured dataset, then exposes that dataset through a Flask API. The API supports recipe search, meal-component discovery, pairing suggestions, grocery-list workflows, dataset refreshes, and runtime custom instructions for a recipe assistant.

## Highlights

- Ingest recipe documents into normalized JSON and NDJSON.
- Parse `.docx`, `.txt`, `.md`, extension-less text files, and Google Docs stub files.
- Extract ingredients, steps, notes, servings, timing, tags, profiles, and recipe components such as sauces, slaws, and sides.
- Search recipes with normalized terms, phrase matching, and related-term expansion.
- Suggest compatible components and complete-meal pairings.
- Maintain a persistent grocery list with append, archive, and retrieval operations.
- Edit or remove individual grocery items, detect stale carts, and retain cleared history in SQLite.
- Reload the dataset or trigger ingestion through the API.
- Store custom recipe-assistant instructions independently from source code.
- Synchronize the normalized dataset into a local SQLite database during the persistence migration.
- Build an SQLite FTS5 search index while preserving the existing search ranking behavior.
- Synchronize grocery lists, archives, and custom assistant instructions into SQLite during the migration.
- Run locally, on a server, or as a backend for another chatbot or sidecar service.

## How it works

```text
Recipe documents
      │
      ▼
recipe_pipeline.py
      │
      ├── structured/recipes/*.json
      └── structured/recipes.ndjson
              │
              ▼
       recipe_api.py
              │
              ├── Search and recipe retrieval
              ├── Pairing recommendations
              ├── Grocery-list workflows
              └── Chatbot integration
```

The ingestion pipeline can run once or in polling-watch mode. The API loads the generated NDJSON dataset into an in-memory search index and can refresh it after new recipes are ingested.

During the database migration, the API keeps NDJSON as its serving source and synchronizes a local `structured/recipes.db` copy. This database is runtime data and is not required to be present in a fresh clone.

## Quick start

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Ingest recipes

Place recipe files in a source directory, then run:

```bash
python scripts/recipe_pipeline.py \
  --source ./recipes \
  --out ./structured
```

The pipeline writes one JSON file per recipe and a combined `structured/recipes.ndjson` file.

To keep the dataset updated while files change:

```bash
python scripts/recipe_pipeline.py \
  --source ./recipes \
  --out ./structured \
  --watch \
  --interval 15
```

### 3. Start the API

```bash
python services/recipe_api.py \
  --host 127.0.0.1 \
  --port 8787 \
  --source ./recipes \
  --out ./structured \
  --dataset ./structured/recipes.ndjson
```

The API is available at `http://127.0.0.1:8787`.

## API examples

Check service health:

```bash
curl http://127.0.0.1:8787/health
```

Search recipes:

```bash
curl -X POST http://127.0.0.1:8787/search \
  -H "Content-Type: application/json" \
  -d '{"q":"harissa shrimp", "limit":5}'
```

List recipes:

```bash
curl "http://127.0.0.1:8787/recipes?limit=10&q=pasta"
```

Retrieve components and pairing suggestions:

```bash
curl "http://127.0.0.1:8787/recipes/<recipe_id>/components"
curl "http://127.0.0.1:8787/recipes/<recipe_id>/pairings"
curl "http://127.0.0.1:8787/pairings?limit=10"
```

Append items to the grocery list:

```bash
curl -X POST http://127.0.0.1:8787/grocery-list/append \
  -H "Content-Type: application/json" \
  -d '{
    "items": ["milk", "yellow onion"],
    "recipe_ids": ["example_recipe_id"],
    "source": "chat"
  }'
```

Refresh the in-memory dataset after ingestion:

```bash
curl -X POST http://127.0.0.1:8787/reload
```

The complete API contract is available in [`openapi.recipes.yaml`](openapi.recipes.yaml).

## API capabilities

| Area | Endpoints | Purpose |
| --- | --- | --- |
| Service | `/health`, `/` | Health and service metadata |
| Recipes | `/recipes`, `/recipes/{recipe_id}` | Browse and retrieve recipes |
| Search | `POST /search` | Search normalized recipe content |
| Components | `/components`, `/recipes/{recipe_id}/components` | Find sauces, sides, slaws, and other extracted components |
| Pairings | `/pairings`, `/recipes/{recipe_id}/pairings` | Recommend compatible meal combinations |
| Grocery list | `/grocery-list/*` | Add, retrieve, archive, and clear grocery lists |
| Dataset | `POST /reload`, `POST /ingest` | Refresh or rebuild the searchable dataset |
| Assistant config | `/custom-instructions` | Read or update recipe-assistant instructions |

## Data model

Each normalized recipe can include:

- `recipe_id` and `title`
- servings and total-time text
- normalized ingredients, steps, and notes
- source metadata and ingestion timestamps
- inferred profile, cuisine, platform, and component type
- extracted components such as sauces, sides, slaws, and toppings
- parser warnings when a source file needs review

The generated dataset is designed to be useful both to the API and to an external chatbot that needs concise recipe summaries and structured actions.

## Configuration

The API can be configured with command-line arguments or environment variables. `.env.example` provides a client configuration template.

The database path is configured with `RECIPE_DATABASE_PATH` or `--database`. The default `RECIPE_DATA_SOURCE=ndjson` keeps the file-backed stores as the serving source while synchronizing SQLite. Set `RECIPE_DATA_SOURCE=sqlite` to use database-backed recipe reads, search, grocery lists, archives, and custom instructions; the transition continues writing the existing files for fallback and export. Grocery freshness defaults to 7 days and can be changed with `RECIPE_GROCERY_STALE_DAYS`.

To import an existing NDJSON export directly:

```bash
python -m services.recipe_database \
  --ndjson structured/recipes.ndjson \
  --database structured/recipes.db
```

Optional bearer-token protection:

```bash
export RECIPE_API_BEARER_TOKEN="your-generated-token"
python services/recipe_api.py --host 127.0.0.1 --port 8787
```

Generate a token locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Clients send it with the standard `Authorization` header:

```bash
curl http://127.0.0.1:8787/recipes?limit=5 \
  -H "Authorization: Bearer $RECIPE_API_BEARER_TOKEN"
```

The root, health, and OpenAPI-documentation routes remain available without authentication. All recipe, search, pairing, grocery-list, ingestion, reload, and custom-instruction routes require a valid bearer token when authentication is configured.

`X-API-Key` remains supported as a legacy compatibility header. Runtime paths can also be configured for the source directory, output directory, dataset, grocery list, archives, and custom instructions file.

## Project structure

```text
.
├── scripts/
│   └── recipe_pipeline.py     # Document ingestion and normalization
├── services/
│   ├── recipe_api.py          # Flask API and search service
│   └── recipe_database.py     # SQLite schema and NDJSON importer
├── openapi.recipes.yaml       # OpenAPI description
├── RECIPE_PIPELINE.md         # Pipeline details
├── RECIPE_API.md              # API reference and integration notes
├── requirements.txt
├── tests/
└── .env.example
```

## Documentation

- [`RECIPE_PIPELINE.md`](RECIPE_PIPELINE.md) — supported inputs, parsing behavior, watcher mode, and output schema.
- [`RECIPE_API.md`](RECIPE_API.md) — endpoint details and client integration examples.
- [`openapi.recipes.yaml`](openapi.recipes.yaml) — machine-readable API contract.
