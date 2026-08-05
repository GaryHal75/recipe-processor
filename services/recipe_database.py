#!/usr/bin/env python3
"""SQLite persistence layer for the recipe dataset migration."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    recipe_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    component_type TEXT,
    profile_json TEXT NOT NULL,
    recipe_json TEXT NOT NULL,
    source_relative_path TEXT,
    source_mtime_utc TEXT,
    ingested_at_utc TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_components (
    component_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL,
    component_type TEXT,
    title TEXT,
    component_json TEXT NOT NULL,
    FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recipes_title ON recipes(title);
CREATE INDEX IF NOT EXISTS idx_recipes_component_type ON recipes(component_type);
CREATE INDEX IF NOT EXISTS idx_components_recipe_id ON recipe_components(recipe_id);
CREATE INDEX IF NOT EXISTS idx_components_type ON recipe_components(component_type);

CREATE VIRTUAL TABLE IF NOT EXISTS recipe_search USING fts5(
    recipe_id UNINDEXED,
    title,
    content
);

CREATE TABLE IF NOT EXISTS database_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grocery_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grocery_archives (
    archive_id TEXT PRIMARY KEY,
    archive_json TEXT NOT NULL,
    archived_at_utc TEXT,
    status TEXT NOT NULL DEFAULT 'archived',
    deleted_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS custom_instructions (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grocery_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    item_id TEXT,
    archive_id TEXT,
    occurred_at_utc TEXT NOT NULL,
    event_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_grocery_events_occurred_at ON grocery_events(occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_grocery_events_type ON grocery_events(event_type);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _recipe_timestamp(recipe: dict[str, Any]) -> str:
    return str(recipe.get("ingested_at_utc") or "")


def _search_content(recipe: dict[str, Any]) -> str:
    fields = [
        recipe.get("title"),
        recipe.get("ingredients"),
        recipe.get("steps"),
        recipe.get("notes"),
        recipe.get("tags"),
        recipe.get("profile"),
        recipe.get("components"),
    ]
    return " ".join(_json(field) for field in fields if field)


class RecipeDatabase:
    """Transactional SQLite store used during the NDJSON migration."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def backup_to(self, destination: Path) -> Path:
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(recipes)")}
            if "source_mtime_utc" not in columns:
                connection.execute("ALTER TABLE recipes ADD COLUMN source_mtime_utc TEXT")
            archive_columns = {row[1] for row in connection.execute("PRAGMA table_info(grocery_archives)")}
            if "status" not in archive_columns:
                connection.execute("ALTER TABLE grocery_archives ADD COLUMN status TEXT NOT NULL DEFAULT 'archived'")
            if "deleted_at_utc" not in archive_columns:
                connection.execute("ALTER TABLE grocery_archives ADD COLUMN deleted_at_utc TEXT")
            connection.execute(
                "INSERT INTO database_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def replace_from_recipes(self, recipes: Iterable[dict[str, Any]]) -> int:
        # RecipeStore keeps the last occurrence for duplicate IDs; mirror that
        # behavior so the database remains compatible with the NDJSON source.
        unique_rows: dict[str, dict[str, Any]] = {}
        for recipe in recipes:
            recipe_id = str(recipe.get("recipe_id") or "").strip()
            if recipe_id:
                unique_rows[recipe_id] = recipe
        rows = list(unique_rows.values())
        with self.connect() as connection:
            connection.execute("BEGIN")
            connection.execute("DELETE FROM recipe_components")
            connection.execute("DELETE FROM recipes")
            connection.execute("DELETE FROM recipe_search")

            for recipe in rows:
                recipe_id = str(recipe["recipe_id"]).strip()
                profile = recipe.get("profile") or {}
                source = recipe.get("source") or {}
                updated_at = _recipe_timestamp(recipe)
                connection.execute(
                    """
                    INSERT INTO recipes(
                        recipe_id, title, component_type, profile_json, recipe_json,
                        source_relative_path, source_mtime_utc, ingested_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recipe_id,
                        str(recipe.get("title") or ""),
                        recipe.get("component_type"),
                        _json(profile),
                        _json(recipe),
                        source.get("relative_path"),
                        source.get("mtime_utc"),
                        recipe.get("ingested_at_utc"),
                        updated_at,
                    ),
                )
                for index, component in enumerate(recipe.get("components") or []):
                    component_id = f"{recipe_id}:{index}"
                    connection.execute(
                        """
                        INSERT INTO recipe_components(
                            component_id, recipe_id, component_type, title, component_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            component_id,
                            recipe_id,
                            component.get("component_type"),
                            component.get("title"),
                            _json(component),
                        ),
                    )
                connection.execute(
                    "INSERT INTO recipe_search(recipe_id, title, content) VALUES (?, ?, ?)",
                    (recipe_id, str(recipe.get("title") or ""), _search_content(recipe)),
                )

            connection.execute(
                "INSERT INTO database_meta(key, value) VALUES('recipe_count', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(len(rows)),),
            )
            connection.commit()
        return len(rows)

    def replace_from_ndjson(self, ndjson_path: Path) -> int:
        recipes: list[dict[str, Any]] = []
        with ndjson_path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid NDJSON at line {line_number}: {ndjson_path}") from exc
                if isinstance(payload, dict):
                    recipes.append(payload)
        return self.replace_from_recipes(recipes)

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            recipe_count = connection.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            component_count = connection.execute("SELECT COUNT(*) FROM recipe_components").fetchone()[0]
            schema_version = connection.execute(
                "SELECT value FROM database_meta WHERE key = 'schema_version'"
            ).fetchone()
        return {
            "path": str(self.path),
            "recipes": recipe_count,
            "components": component_count,
            "schema_version": int(schema_version[0]) if schema_version else None,
        }

    def load_recipes(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT recipe_json FROM recipes ORDER BY source_mtime_utc DESC, ingested_at_utc DESC, recipe_id DESC"
            ).fetchall()
        return [json.loads(row["recipe_json"]) for row in rows]

    def search_recipe_ids(self, terms: Iterable[str]) -> set[str] | None:
        cleaned = [term.replace('"', "") for term in terms if term and term.replace('"', "")]
        if not cleaned:
            return set()
        query = " OR ".join(f'"{term}"' for term in cleaned)
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT recipe_id FROM recipe_search WHERE recipe_search MATCH ?",
                    (query,),
                ).fetchall()
        except sqlite3.OperationalError:
            return None
        return {str(row["recipe_id"]) for row in rows}

    def load_grocery_state(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM grocery_state WHERE key = 'current'"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_grocery_state(self, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO grocery_state(key, value_json) VALUES('current', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (_json(payload),),
            )

    def save_grocery_archive(self, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO grocery_archives(archive_id, archive_json, archived_at_utc, status, deleted_at_utc) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(archive_id) DO UPDATE SET archive_json=excluded.archive_json, "
                "archived_at_utc=excluded.archived_at_utc, status=excluded.status, deleted_at_utc=excluded.deleted_at_utc",
                (
                    payload.get("archive_id"),
                    _json(payload),
                    payload.get("archived_at_utc"),
                    payload.get("status") or "archived",
                    payload.get("deleted_at_utc"),
                ),
            )

    def list_grocery_archives(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        with self.connect() as connection:
            query = "SELECT archive_json, status, deleted_at_utc FROM grocery_archives"
            if not include_deleted:
                query += " WHERE deleted_at_utc IS NULL"
            query += " ORDER BY archived_at_utc DESC, archive_id DESC"
            rows = connection.execute(query).fetchall()
        payloads = []
        for row in rows:
            payload = json.loads(row[0])
            payload["status"] = row[1] or payload.get("status") or "archived"
            payload["deleted_at_utc"] = row[2] or payload.get("deleted_at_utc")
            payloads.append(payload)
        return payloads

    def get_grocery_archive(self, archive_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        with self.connect() as connection:
            query = "SELECT archive_json, status, deleted_at_utc FROM grocery_archives WHERE archive_id = ?"
            if not include_deleted:
                query += " AND deleted_at_utc IS NULL"
            row = connection.execute(query, (archive_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        payload["status"] = row[1] or payload.get("status") or "archived"
        payload["deleted_at_utc"] = row[2] or payload.get("deleted_at_utc")
        return payload

    def soft_delete_grocery_archive(self, archive_id: str, deleted_at_utc: str) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE grocery_archives SET status = 'deleted', deleted_at_utc = ? WHERE archive_id = ? AND deleted_at_utc IS NULL",
                (deleted_at_utc, archive_id),
            )
        return result.rowcount > 0

    def record_grocery_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        item_id: str | None = None,
        archive_id: str | None = None,
        occurred_at_utc: str,
    ) -> str:
        event_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO grocery_events(event_id, event_type, item_id, archive_id, occurred_at_utc, event_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, event_type, item_id, archive_id, occurred_at_utc, _json(payload)),
            )
        return event_id

    def list_grocery_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, event_type, item_id, archive_id, occurred_at_utc, event_json "
                "FROM grocery_events ORDER BY occurred_at_utc DESC, event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        events = []
        for row in rows:
            payload = json.loads(row[5])
            payload.update(
                {
                    "event_id": row[0],
                    "event_type": row[1],
                    "item_id": row[2],
                    "archive_id": row[3],
                    "occurred_at_utc": row[4],
                }
            )
            events.append(payload)
        return events

    def load_custom_instructions(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM custom_instructions WHERE key = 'current'"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_custom_instructions(self, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO custom_instructions(key, value_json) VALUES('current', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (_json(payload),),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import recipe NDJSON into SQLite.")
    parser.add_argument("--ndjson", help="Path to recipes.ndjson.")
    parser.add_argument("--database", required=True, help="Path to the SQLite database.")
    parser.add_argument("--backup", help="Write a consistent SQLite backup to this path.")
    args = parser.parse_args()

    if not args.ndjson and not args.backup:
        parser.error("provide --ndjson to import or --backup to create a backup")

    database = RecipeDatabase(Path(args.database))
    result: dict[str, Any] = {"ok": True}
    if args.ndjson:
        result["imported"] = database.replace_from_ndjson(Path(args.ndjson))
    if args.backup:
        result["backup_path"] = str(database.backup_to(Path(args.backup)))
    print(json.dumps({**result, **database.stats()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
