# MCP Adoption: Recipes

## Role

Recipe Processor remains the canonical owner of recipe data, search behavior,
grocery-list state, ingestion, and recipe-specific business rules.

The MCP server in this repository is an adapter. It owns no recipe data and
does not replace the Flask API. It validates MCP arguments, calls the existing
authenticated HTTP API, and returns structured MCP results.

```text
ChatCentralCore
      │ internal MCP stdio
      ▼
recipe_processor/mcp/server.py
      │ authenticated HTTP
      ▼
services/recipe_api.py
      │
      ▼
canonical recipe data
```

## Current status

- [x] MCP adapter lives under `mcp/` in the Recipe Processor repository.
- [x] Adapter reuses the Recipe API bearer-token configuration.
- [x] Tools currently include `search_recipes`, `get_recipe`,
      `get_grocery_list`, and `append_to_grocery_list`.
- [x] ChatCore can select the MCP transport with
      `APPBOT_RECIPE_TRANSPORT=mcp`.
- [x] Direct HTTP remains available with
      `APPBOT_RECIPE_TRANSPORT=http`.
- [x] Live search has been verified against the Pi service.

## Recipe-specific punch list

- [ ] Create a shared contract definition for recipe operations.
- [ ] Compare the MCP schemas with `openapi.recipes.yaml` automatically.
- [ ] Add parity tests for inputs, bounds, response shapes, and errors.
- [ ] Add MCP tools for recipe components and pairing suggestions.
- [ ] Add grocery item update/archive/restore tools only after consequence and
      confirmation behavior is specified.
- [ ] Decide whether custom recipe instructions belong in the first MCP scope.
- [ ] Add a service-level MCP health and diagnostics check.
- [ ] Add structured correlation IDs from ChatCore through the MCP adapter and
      Recipe API.
- [ ] Evaluate migration from the hand-written protocol adapter to the official
      MCP Python SDK.
- [ ] Document service restart, rollback, token rotation, and troubleshooting.

## Contract mapping

| MCP tool | HTTP operation | Consequence | Confirmation |
|---|---|---|---|
| `search_recipes` | `POST /search` | Read | No |
| `get_recipe` | `GET /recipes/{recipe_id}` | Read | No |
| `get_grocery_list` | `GET /grocery-list` | Read | No |
| `append_to_grocery_list` | `POST /grocery-list/append` | Reversible write | Yes in ChatCore |

The MCP adapter should remain thin. New recipe behavior belongs in the Recipe
Processor service and its HTTP/API contract first; then the MCP and ChatCore
surfaces should be updated and parity-tested.
