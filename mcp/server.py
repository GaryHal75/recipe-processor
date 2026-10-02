#!/usr/bin/env python3
"""MCP adapter for the existing Recipe Processor HTTP API.

This process owns no recipe data. It validates MCP arguments, calls the
authenticated local Recipe API, and returns structured MCP tool results.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SERVER_INFO = {"name": "sudogary-recipes-mcp", "version": "0.1.0"}
DEFAULT_BASE_URL = "http://127.0.0.1:8787"
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class AdapterError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def load_env_file(path: str) -> None:
    """Load only simple KEY=value entries when the host did not inject them."""
    try:
        with open(path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except FileNotFoundError:
        pass


load_env_file(os.getenv("RECIPES_MCP_ENV_FILE", "/home/garygnu/.config/recipe_processor/api.env"))
BASE_URL = os.getenv("RECIPES_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
TOKEN = os.getenv("RECIPES_API_BEARER_TOKEN") or os.getenv("RECIPE_API_BEARER_TOKEN")
TIMEOUT = float(os.getenv("RECIPES_MCP_TIMEOUT_SECONDS", "15"))


TOOLS = [
    {
        "name": "search_recipes",
        "description": "Search the canonical Recipe Processor dataset by free text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_recipe",
        "description": "Retrieve one canonical recipe by its public recipe ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"recipe_id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,160}$"}},
            "required": ["recipe_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_grocery_list",
        "description": "Get the current canonical grocery list.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "append_to_grocery_list",
        "description": "Add normalized grocery items to the canonical grocery list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 160}, "minItems": 1, "maxItems": 50},
                "recipe_ids": {"type": "array", "items": {"type": "string", "maxLength": 160}, "maxItems": 50},
                "source": {"type": "string", "maxLength": 80, "default": "mcp"},
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
]


def json_result(value: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}],
        "structuredContent": value,
    }


def api_request(path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = {"message": exc.reason}
        code = "unauthorized" if exc.code in (401, 403) else "not_found" if exc.code == 404 else "api_error"
        raise AdapterError(code, detail.get("message", f"Recipe API returned HTTP {exc.code}"), exc.code) from exc
    except (URLError, TimeoutError) as exc:
        raise AdapterError("upstream_unavailable", "Recipe API is unavailable", 503) from exc
    except json.JSONDecodeError as exc:
        raise AdapterError("invalid_upstream_response", "Recipe API returned invalid JSON", 502) from exc


def arguments_object(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise AdapterError("invalid_request", "arguments must be a JSON object", 400)
    return arguments


def call_tool(name: str, raw_arguments: Any) -> Any:
    arguments = arguments_object(raw_arguments)
    allowed = {
        "search_recipes": {"query", "limit"},
        "get_recipe": {"recipe_id"},
        "get_grocery_list": set(),
        "append_to_grocery_list": {"items", "recipe_ids", "source"},
    }
    if name not in allowed:
        raise AdapterError("unknown_tool", f"unknown tool: {name}", 404)
    unexpected = set(arguments) - allowed[name]
    if unexpected:
        raise AdapterError("invalid_request", f"unexpected argument(s): {', '.join(sorted(unexpected))}", 400)

    if name == "search_recipes":
        query = arguments.get("query")
        limit = arguments.get("limit", 10)
        if not isinstance(query, str) or not query.strip() or len(query) > 240:
            raise AdapterError("invalid_request", "query must be a non-empty string of at most 240 characters", 400)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
            raise AdapterError("invalid_request", "limit must be an integer from 1 to 25", 400)
        return api_request(f"/search?{urlencode({'q': query.strip(), 'limit': limit})}", "POST", {"q": query.strip(), "limit": limit})

    if name == "get_recipe":
        recipe_id = arguments.get("recipe_id")
        if not isinstance(recipe_id, str) or not ID_PATTERN.fullmatch(recipe_id):
            raise AdapterError("invalid_request", "recipe_id contains invalid characters or length", 400)
        return api_request(f"/recipes/{quote(recipe_id, safe='')}")

    if name == "get_grocery_list":
        return api_request("/grocery-list")

    items = arguments.get("items")
    recipe_ids = arguments.get("recipe_ids", [])
    source = arguments.get("source", "mcp")
    if not isinstance(items, list) or not 1 <= len(items) <= 50 or any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in items):
        raise AdapterError("invalid_request", "items must contain 1 to 50 non-empty short strings", 400)
    if not isinstance(recipe_ids, list) or len(recipe_ids) > 50 or any(not isinstance(item, str) or len(item) > 160 for item in recipe_ids):
        raise AdapterError("invalid_request", "recipe_ids must be an array of at most 50 short strings", 400)
    if not isinstance(source, str) or not source.strip() or len(source) > 80:
        raise AdapterError("invalid_request", "source must be a non-empty string of at most 80 characters", 400)
    return api_request("/grocery-list/append", "POST", {"items": [item.strip() for item in items], "recipe_ids": recipe_ids, "source": source.strip()})


def error_response(request_id: Any, error: AdapterError) -> dict[str, Any]:
    value = {"error": error.code, "message": str(error)}
    if error.status is not None:
        value["status"] = error.status
    return {"jsonrpc": "2.0", "id": request_id, "result": {"isError": True, **json_result(value)}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": json_result(call_tool(params.get("name"), params.get("arguments")))}
        except AdapterError as exc:
            return error_response(request_id, exc)
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} ready (stdio)", file=sys.stderr, flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle(request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except (json.JSONDecodeError, TypeError):
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}), flush=True)


if __name__ == "__main__":
    main()
