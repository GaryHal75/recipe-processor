# Recipes Entity Editing Implementation

## Scope

This enhancement stays inside the existing `recipe_processor` Recipes service
and its `mcp/server.py` adapter. It does not create a Grocery MCP server or a
second persistence layer.

## Existing architecture retained

- `services/recipe_api.py` owns the Flask API, recipe search/retrieval, and
  grocery-list persistence.
- `services/recipe_database.py` remains the optional SQLite transition store;
  NDJSON/file-backed serving behavior is unchanged.
- `mcp/server.py` remains a thin authenticated HTTP adapter.
- `DocumentService` remains the owner of staged recipe DOCX drafts, guarded
  draft edits, explicit Save approval, version history, and restore.
- Existing recipe creation, retrieval, grocery append/read, archive, and
  single-item update/remove routes remain available.

## Implemented capability

The Recipes API now exposes:

- `POST /grocery-list/change-sets/preview`
- `POST /grocery-list/change-sets/commit`

The MCP adapter exposes the matching `preview_grocery_change_set` and
`commit_grocery_change_set` tools.

The selection snapshot contains the active parent (`active`), entity type
(`grocery_item`), selected stable item IDs, and the grocery-list revision.
Empty selections are rejected. Every proposed change must target a selected
ID, and the backend revalidates each ID against authoritative storage.

Supported grocery fields are `item`, `description`, `quantity`, `unit`,
`category`, `notes`, and `purchased`. A single change set can assign different
fields to different selected items. Unspecified fields and unselected items
are preserved. Existing item IDs are never replaced during edits.

## Preview, approval, and persistence

Preview validates the exact selection, revision, target IDs, expected prior
values, and supported fields. It returns a separate before/after entry for
each affected entity and a single-use `preview_id`; it does not persist data.

Commit requires `approved: true` and consumes only that exact preview. Remove
operations additionally require `confirm_destructive: true`. Validation of all
targets occurs before mutation, and the complete change set is applied under
the grocery-store lock and saved once. A revision conflict rejects the whole
operation.

After commit, the service reads the authoritative grocery list and returns a
structured verification result with the new revision, changed IDs, and a
present-or-removed target check. The service does not report confirmed success
when the revision or expected values are stale.

## Concurrency and limits

The grocery list now carries a stable opaque `revision` value. Legacy lists
without one use their existing update timestamp until the next mutation. Each
mutation advances the revision. The preview binds both the revision and
expected field values, protecting against changes between selection, preview,
and commit. Change sets are capped at 100 selected IDs/operations.

## UI and ID handoff

The current repository has no separate recipe/grocery frontend. The reliable
handoff is therefore the structured MCP/API request: the assistant must send
the selected stable IDs and captured revision explicitly. Visual position
numbers are not accepted as identifiers. If a future interactive surface can
emit these fields, it can use the same contract without a new frontend.

## Recipe workflow boundary

Recipe draft editing already uses `DocumentService` guarded operations with
expected values, staged DOCX previews, explicit Save, version archiving, and
restore. This enhancement preserves that workflow rather than writing around
it. The Recipes MCP adapter does not claim to commit canonical recipe DOCX
changes itself.

## Tests

Executed on fiveberry:

```text
.venv/bin/python -m unittest tests.test_regressions -v
Ran 9 tests ... OK
```

New coverage verifies independent multi-item edits, preservation of an
unselected item, stale revision rejection, and removal gating. Existing recipe
pipeline, SQLite migration, archive, append, read, and single-item regression
tests also pass.

## Known limitations and deployment

- Pending previews are process-local and single-use; a service restart expires
  them. This is intentional until a shared session store is required.
- The current interactive UI cannot be changed in this repository because it
  is not present here; callers must transmit the selection snapshot explicitly.
- Canonical recipe DOCX editing remains in DocumentService and its existing
  MCP tools.
- Deploy by restarting the existing Recipe Processor API and MCP processes on
  fiveberry from this feature branch after review. No production deployment or
  live household mutation was performed by this change.
