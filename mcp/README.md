# Recipe MCP adapter

This folder contains the MCP adapter for the existing Recipe Processor. The
adapter owns no recipe data and does not replace the Flask API. It validates
MCP tool arguments, calls the authenticated local API on `127.0.0.1:8787`,
and returns structured MCP results.

## Tools

- `search_recipes` — read-only search of the canonical recipe dataset.
- `get_recipe` — read-only retrieval by recipe ID.
- `get_grocery_list` — read-only retrieval of the active grocery list.
- `append_to_grocery_list` — writes through to the canonical grocery list.

The adapter loads the existing Recipe Processor environment file at
`/home/garygnu/.config/recipe_processor/api.env` by default, so it can reuse
the API bearer token without copying or storing credentials in this folder.
Override with `RECIPES_MCP_ENV_FILE` when needed.

## Test locally on the Pi

```bash
cd /home/garygnu/recipe_processor
printf '%s\\n' \\
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \\
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \\
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_recipes","arguments":{"query":"chicken","limit":2}}}' \\
  | .venv/bin/python mcp/server.py
```

The adapter is currently launched on demand by an MCP host. It is not yet
registered with ChatCentralCore; that should be a deliberate next step after
we decide whether ChatCore should consume MCP internally or expose this as an
external Sudogary capability.
