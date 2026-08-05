# Recipe Database Migration Plan

## Objective

Introduce a local SQLite database as the durable store for the recipe service while preserving the existing NDJSON files during the transition.

The migration should improve queryability and update reliability without disrupting the current ingestion pipeline, API behavior, grocery-list workflows, or local deployment.

## Current status

Phase 1, the initial recipe-read/search portion of Phases 2–4, and the first portion of Phase 5 are complete. The service can create a local SQLite database, import the current NDJSON dataset, preserve duplicate-ID behavior, report synchronization status, read through SQLite when `RECIPE_DATA_SOURCE=sqlite`, use an SQLite FTS5 index to prefilter searches, and synchronize grocery-list, archive, and custom-instruction state. NDJSON and the existing JSON/Markdown files remain transition inputs, fallbacks, and exports while database-backed behavior is validated.

## Design decision

Use SQLite for the first database implementation.

SQLite fits the current deployment because the service is local, single-instance, and file-based. It requires no separate database server and is included with Python. A future PostgreSQL migration can be considered if the service becomes multi-user, distributed, or cloud-hosted.

## Transition principles

- Keep NDJSON available as an import/export format during the migration.
- Store the database in the local generated-data area, for example `structured/recipes.db`.
- Never commit recipe data or the SQLite database to the public repository.
- Preserve current API response shapes wherever possible.
- Make each migration phase independently testable and reversible.
- Prefer rebuilding derived indexes from canonical recipe records rather than maintaining duplicated logic.

## Proposed data model

### `recipes`

One row per normalized recipe.

Suggested fields:

- `recipe_id` — primary key
- `title`
- `servings`
- `total_time_text`
- `profile_json`
- `source_json`
- `source_mtime_utc` — source file modification time used for recency ordering
- `metadata_json`
- `ingested_at_utc`
- `updated_at_utc`

### `recipe_content`

Structured recipe content associated with a recipe.

- `recipe_id`
- `ingredients_json`
- `steps_json`
- `notes_json`
- `tags_json`
- `parse_warning`

Keeping nested content as JSON initially minimizes migration risk while still making recipe records durable in SQLite.

### `recipe_components`

Extracted reusable parts of a recipe.

- `component_id`
- `recipe_id`
- `component_type`
- `title`
- `ingredients_json`
- `steps_json`
- `metadata_json`

Indexes should support lookup by `recipe_id` and `component_type`.

### `recipe_search`

SQLite FTS5 virtual table for recipe search.

Indexed content can include:

- title
- ingredients
- steps
- notes
- tags
- component titles

The existing search behavior should remain the reference behavior during the first database phase. FTS5 can be introduced behind the same API contract and compared against the current results.

### Grocery and assistant state

Move mutable service state into SQLite after recipe reads are stable:

- `grocery_state` (current grocery-list payload)
- `grocery_archives` with status and soft-delete timestamps
- `grocery_events` for append, edit, removal, archive, clear, and soft-delete history
- `custom_instructions`
- `ingestion_state`

This will eventually consolidate the current JSON grocery-list files and custom-instructions file while preserving their API behavior.

## Migration phases

### Phase 1: Schema and importer

- Add a database module responsible for connection setup, schema creation, and transactions.
- Create the initial recipe tables and indexes.
- Add an importer that reads `structured/recipes.ndjson` into SQLite.
- Make imports idempotent using `recipe_id` and update timestamps.
- Add a command to inspect database counts and import status.

Success criteria:

- Existing NDJSON imports without data loss.
- Re-running the importer produces no duplicate recipes.
- The database remains ignored by Git.

### Phase 2: Dual-write ingestion

- Keep writing the existing JSON and NDJSON output.
- After a successful pipeline run, update SQLite in the same application workflow.
- Record ingestion counts and failures.
- Keep NDJSON available for rollback and inspection.

Success criteria:

- JSON, NDJSON, and SQLite contain matching recipe counts.
- Changed and deleted source files are reflected correctly in both formats.
- Partial failures do not silently replace valid database data.

### Phase 3: Database-backed recipe reads

- Add database-backed methods for listing, retrieving, component lookup, and pairing inputs.
- Preserve the current response formats.
- Compare database results with the current in-memory/NDJSON implementation.
- Retain an explicit NDJSON fallback during the initial rollout.

Success criteria:

- Health, recipe, component, pairing, and search endpoints pass regression tests.
- Pagination, limits, filters, and missing-recipe behavior remain unchanged.
- Database-backed responses match the current API contract.

### Phase 4: Database-backed search

- Introduce FTS5 for indexed text search.
- Compare FTS5 results with the existing normalized search behavior.
- Preserve important ranking and related-term behavior where practical.
- Add search regression cases for common recipe queries and component terms.

Success criteria:

- Search remains responsive as the dataset grows.
- Common existing queries return the same or better results.
- Search index rebuilds are repeatable and recoverable.

### Phase 5: Move mutable state

- Migrate grocery-list items and archives into SQLite.
- Migrate custom assistant instructions into SQLite.
- Add editable grocery items, stale-list metadata, and recoverable soft-delete behavior.
- Keep one-time import paths from the existing JSON and Markdown files.
- Continue supporting export or recovery to the current file formats.

Success criteria:

- Grocery append, retrieval, archive, delete, and history endpoints pass regression tests.
- Custom-instruction reads and writes remain atomic.
- Restarting the API does not lose mutable state.

### Phase 6: Make SQLite the source of truth

- Make SQLite the primary read and write store.
- Keep NDJSON export available for backup, portability, and debugging.
- Update deployment documentation and operational checks.
- Remove only redundant code after the fallback period is complete.

## Backup and recovery

- Back up `structured/recipes.db` before schema migrations.
- Keep NDJSON exports as a portable backup format.
- Use SQLite transactions for imports and mutable-state updates.
- Prefer a new database migration over destructive in-place changes.
- Document the commands for rebuilding the database from NDJSON.

## Security and privacy

- Keep the database path under ignored runtime data directories.
- Do not add recipe data, generated JSON, or `.db` files to Git.
- Continue protecting API routes with the bearer token.
- Do not store bearer tokens in the database or generated recipe exports.
- Use filesystem permissions appropriate for the local deployment.

## Testing plan

Add tests for:

- Schema creation on a new database.
- Importing an empty, valid, and partially invalid NDJSON file.
- Idempotent re-imports.
- Updates and deletions from a subsequent ingest.
- Recipe retrieval, pagination, filtering, and missing IDs.
- Component and pairing queries.
- Search ranking and common query regressions.
- Grocery-list transactions and archive behavior.
- Custom-instruction reads and writes.
- Database fallback behavior.
- API bearer-token authentication.

## Rollback plan

At every phase, rollback should be possible by:

1. Stopping the API or ingestion worker.
2. Restoring the previous database backup if needed.
3. Switching the API back to the NDJSON/in-memory implementation.
4. Rebuilding SQLite from the latest valid `recipes.ndjson`.
5. Re-running the API smoke tests.

The migration is complete only when SQLite-backed behavior is validated and the NDJSON fallback or rebuild path remains documented.
