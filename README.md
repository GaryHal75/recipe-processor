# recipe_processor

Local recipe ingestion + API service.

This project converts local recipe documents into structured JSON/NDJSON and serves them over a lightweight Flask API.

## What is in this repo

- `scripts/recipe_pipeline.py`: ingests recipe files and writes normalized JSON/NDJSON.
- `services/recipe_api.py`: local HTTP API for listing/searching recipes and triggering ingestion.
- `requirements.txt`: Python dependencies for the API/runtime environment.
- `RECIPE_PIPELINE.md`: pipeline usage details.
- `RECIPE_API.md`: API usage details.

## What is intentionally NOT committed

This repo is configured to ignore:
- raw recipe docs (`*.docx`, `*.gdoc`, and current extension-less recipe files),
- generated output (`structured/`).

That keeps personal recipe content local while you publish code publicly.

## Quick start

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2. Put recipes in a local folder.

3. Run pipeline:

```bash
python scripts/recipe_pipeline.py --source "." --out "structured"
```

4. Start API:

```bash
python services/recipe_api.py --host 0.0.0.0 --port 8787
```

5. Optional API key:

```bash
export RECIPE_API_KEY="replace-with-local-secret"
python services/recipe_api.py --host 0.0.0.0 --port 8787
```

## Deployment model

- Clone this repo onto your server or Raspberry Pi.
- Create a venv in the repo and install `requirements.txt`.
- Sync or mount your recipe folder separately from this repo, or copy recipe files to a local data directory.
- Run the pipeline on that folder, then run the API service.

Example deployment:

```bash
cd ~/recipe_processor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/recipe_pipeline.py --source "/srv/recipes/raw" --out "/srv/recipes/structured"
python services/recipe_api.py --host 0.0.0.0 --port 8787 --source "/srv/recipes/raw" --out "/srv/recipes/structured" --dataset "/srv/recipes/structured/recipes.ndjson"
```

## Production deployment notes

For a real deployment, keep systemd units, sync credentials, data directories, and refresh scripts outside this public repository. Configure their paths with command-line arguments or environment variables.

Useful local checks:

```bash
systemctl --user status recipe-alt-refresh.timer --no-pager
systemctl --user status recipe-api.service --no-pager
systemctl --user restart recipe-api.service
curl http://127.0.0.1:8787/health
```

On this Pi, LAN URL is:

```text
http://<your-server-ip>:8787
```

Optional API key for LAN access control:

- Edit your local API environment file
- Uncomment/set `RECIPE_API_KEY=...`
- Restart service: `systemctl --user restart recipe-api.service`

## Client dotenv

For sidecars/clients on your LAN, use:

```env
RECIPES_API_BASE_URL=http://127.0.0.1:8787
RECIPES_API_KEY=
```

Template file in repo: `.env.example`
