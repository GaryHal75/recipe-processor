# Local Recipe API

Standalone API service for your normalized recipe dataset.

## Why separate from your Home Assistant sidecar?

Keep this service separate and call it from your sidecar:
- avoids mixing recipe/search logic into HA weather/time endpoints
- allows independent restart/deploy/versioning
- easier to reuse from multiple local apps

Your sidecar can proxy to this service if you want a single external API surface.

## Environment setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Start the API

From this folder:

```bash
source .venv/bin/activate
python services/recipe_api.py --host 0.0.0.0 --port 8787
```

With API key protection:

```bash
source .venv/bin/activate
export RECIPE_API_KEY="replace-with-local-secret"
python services/recipe_api.py --host 0.0.0.0 --port 8787
```

## Endpoints

- `GET /health`
- `GET /recipes?limit=25&offset=0&q=shrimp`
- `GET /recipes/<recipe_id>`
- `GET /recipes/<recipe_id>/components`
- `GET /recipes/<recipe_id>/pairings?side_limit=5&sauce_limit=5&utility_limit=5&combo_limit=8`
- `POST /search` with JSON body: `{"q":"harissa shrimp", "limit":10}`
- `GET /components?component_type=sauce&limit=100`
- `GET /pairings?limit=10` for pairing suggestions across processed mains
- `GET /pairings?recipe_id=<recipe_id>` as an alternate entry point for one meal
- `POST /reload` (reloads `structured/recipes.ndjson`)
- `POST /ingest` (runs `scripts/recipe_pipeline.py`, then reloads)
- `GET /grocery-list`
- `POST /grocery-list/append` with JSON body like `{"items":["milk","yellow onion"],"recipe_ids":["shrimp_roasted_poblano_cream_pasta"],"skip_recipe_ids":["hello_fresh_recipe_id"],"source":"chat"}`
- `POST /grocery-list/archive` with optional JSON body like `{"name":"Trader Joe's run"}`
- `GET /grocery-list/archives`
- `GET /grocery-list/archives/<archive_id>`
- `DELETE /grocery-list`

The active grocery list now includes `started_at_utc`, `started_local_date`, and `started_local_day` so chat can decide whether the current list is stale and should be archived before starting a new one.

Recipes now persist structured meal metadata from ingest:
- top-level `profile`
- top-level `component_type`
- `components[]` extracted from headers like `For the sauce`, `For the slaw`, `Serve with`, and similar patterns

Recipe summaries now also include a derived or persisted `component_type`, and pairing endpoints use those roles plus cuisine/platform tags to suggest compatible combinations, treating sauces and side dishes as reusable building blocks when they fit the main dish.

If API key is enabled, include header:

`X-API-Key: <your_key>`

## Flask sidecar call example

```python
import requests

RECIPE_API = "http://127.0.0.1:8787"
HEADERS = {"X-API-Key": "replace-with-local-secret"}

def search_recipes(q: str):
    r = requests.post(f"{RECIPE_API}/search", json={"q": q, "limit": 5}, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()
```

## Quick local checks

```bash
curl http://127.0.0.1:8787/health
curl -H "X-API-Key: replace-with-local-secret" "http://127.0.0.1:8787/recipes?limit=3&q=tacos"
curl -H "X-API-Key: replace-with-local-secret" -X POST http://127.0.0.1:8787/search -H "Content-Type: application/json" -d '{"q":"harissa"}'
```
