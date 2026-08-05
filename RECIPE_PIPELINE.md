# Recipe JSON Pipeline

This pipeline converts recipe files in this folder into:
- `structured/recipes/*.json` (one JSON file per recipe)
- `structured/recipes.ndjson` (combined newline-delimited JSON)

It supports:
- `.docx` (parsed with macOS `textutil`)
- `.txt`, `.md`
- Extension-less text files
- `.gdoc` Google Docs stubs (records doc id with a parse warning)

## One-time ingest

```bash
python3 scripts/recipe_pipeline.py --source "." --out "structured"
```

## Continuous watcher (polling)

```bash
python3 scripts/recipe_pipeline.py --source "." --out "structured" --watch --interval 15
```

The watcher checks for new/changed files and updates only those recipes.

## Output schema (high level)

Each recipe JSON includes:
- `recipe_id`
- `title`
- `servings`
- `total_time_text`
- `ingredients[]`
- `steps[]`
- `notes[]`
- `source` metadata (file path/type/hash/mtime)
- `ingested_at_utc`
- optional `parse_warning` for `.gdoc`

## Notes

- `.gdoc` files in Google Drive are pointer files, not the document body. Export those docs as `.docx` if you want full parsing.
- This parser is intentionally lightweight. You can iterate later with stricter ingredient parsing (qty/unit/item) and tag extraction.
