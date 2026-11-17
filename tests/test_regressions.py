import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.recipe_pipeline import is_candidate, parse_file, split_sections
from services.recipe_api import build_store, create_app
from services.recipe_database import RecipeDatabase


class PipelineRegressionTests(unittest.TestCase):
    def test_markdown_files_are_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.md"
            path.write_text("# Sample Recipe\n", encoding="utf-8")
            self.assertTrue(is_candidate(path))

    def test_steps_heading_is_parsed_after_ingredients(self) -> None:
        result = split_sections(
            """Harissa Chicken

            Ingredients
            - chicken thighs

            Steps
            1. Roast the chicken.
            2. Serve hot.
            """
        )
        self.assertEqual(result.ingredients, ["chicken thighs"])
        self.assertEqual(result.steps, ["Roast the chicken.", "Serve hot."])

    def test_metadata_accepts_servings_and_separate_values(self) -> None:
        result = split_sections(
            """Mac and Cheese

            Servings
            4 generous servings
            Total Time
            About 45 minutes

            Ingredients
            - 1 pound pasta

            Instructions
            1. Cook the pasta.
            """
        )
        self.assertEqual(result.servings, "4 generous servings")
        self.assertEqual(result.total_time, "About 45 minutes")

    def test_two_column_docx_table_is_parsed_sequentially(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>Two Column Pasta</w:t></w:r></w:p>
            <w:p><w:r><w:t>Servings: </w:t><w:t>2 adults</w:t></w:r></w:p>
            <w:tbl><w:tr>
              <w:tc>
                <w:p><w:r><w:t>Ingredients</w:t></w:r></w:p>
                <w:p><w:r><w:t>• </w:t><w:t>1 pound pasta</w:t></w:r></w:p>
              </w:tc>
              <w:tc>
                <w:p><w:r><w:t>Instructions</w:t></w:r></w:p>
                <w:p><w:r><w:t>1. Boil the pasta.</w:t></w:r></w:p>
                <w:p><w:r><w:t>Instructions - continued</w:t></w:r></w:p>
                <w:p><w:r><w:t>2. Drain and serve.</w:t></w:r></w:p>
              </w:tc>
            </w:tr></w:tbl>
            <w:sectPr/>
          </w:body>
        </w:document>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "two-column.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            recipe = parse_file(path, Path(temp_dir))

        self.assertEqual(recipe["title"], "Two Column Pasta")
        self.assertEqual(recipe["servings"], "2 adults")
        self.assertEqual(recipe["ingredients"], ["1 pound pasta"])
        self.assertEqual(recipe["steps"], ["Boil the pasta.", "Drain and serve."])


class ApiValidationRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        dataset = root / "structured" / "recipes.ndjson"
        dataset.parent.mkdir(parents=True)
        dataset.write_text(
            json.dumps(
                {
                    "recipe_id": "audit_recipe",
                    "title": "Audit Recipe",
                    "ingredients": ["one onion"],
                    "steps": ["Cook the onion."],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        source = root / "source"
        source.mkdir()
        store, grocery_store = build_store(
            dataset=str(dataset),
            source=str(source),
            out=str(root / "structured"),
            pipeline_script=str(Path.cwd() / "scripts/recipe_pipeline.py"),
            grocery_list=str(root / "grocery.json"),
            grocery_archive_dir=str(root / "archives"),
        )
        app = create_app(
            store=store,
            grocery_store=grocery_store,
            api_key=None,
            custom_instructions_path=root / "instructions.md",
            bearer_token="test-token",
        )
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_invalid_numeric_parameters_return_bad_request(self) -> None:
        auth = {"Authorization": "Bearer test-token"}
        self.assertEqual(self.client.get("/recipes?limit=abc", headers=auth).status_code, 400)
        self.assertEqual(self.client.get("/pairings?limit=abc", headers=auth).status_code, 400)
        self.assertEqual(
            self.client.post("/search", json={"q": "onion", "limit": "abc"}, headers=auth).status_code,
            400,
        )

    def test_selection_scoped_grocery_change_set_previews_commits_and_verifies(self) -> None:
        auth = {"Authorization": "Bearer test-token"}
        self.assertEqual(
            self.client.post(
                "/grocery-list/append",
                json={"items": ["salmon", "gold potatoes", "onions", "celery"]},
                headers=auth,
            ).status_code,
            200,
        )
        snapshot = self.client.get("/grocery-list", headers=auth).get_json()
        ids = {row["item"]: row["id"] for row in snapshot["items"]}
        selected = [ids["salmon"], ids["gold potatoes"], ids["onions"]]
        preview = self.client.post(
            "/grocery-list/change-sets/preview",
            json={
                "selection": {"entity_ids": selected, "expected_revision": snapshot["revision"]},
                "changes": [
                    {"entity_id": ids["salmon"], "expected": {"item": "salmon"}, "fields": {"quantity": 1.5, "unit": "lb"}},
                    {"entity_id": ids["gold potatoes"], "expected": {"item": "gold potatoes"}, "fields": {"item": "red potatoes"}},
                    {"entity_id": ids["onions"], "expected": {"item": "onions"}, "fields": {"quantity": 2, "unit": "bags", "description": "pearl onions"}},
                ],
            },
            headers=auth,
        )
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertEqual(self.client.get("/grocery-list", headers=auth).get_json()["revision"], snapshot["revision"])
        committed = self.client.post(
            "/grocery-list/change-sets/commit",
            json={"preview_id": preview.get_json()["preview_id"], "approved": True},
            headers=auth,
        )
        self.assertEqual(committed.status_code, 200, committed.get_json())
        result = {row["id"]: row for row in committed.get_json()["result"]["items"]}
        self.assertEqual(result[ids["salmon"]]["quantity"], 1.5)
        self.assertEqual(result[ids["salmon"]]["unit"], "lb")
        self.assertEqual(result[ids["gold potatoes"]]["item"], "red potatoes")
        self.assertEqual(result[ids["onions"]]["description"], "pearl onions")
        self.assertEqual(result[ids["celery"]]["item"], "celery")

    def test_change_set_rejects_stale_revision_and_requires_destructive_confirmation(self) -> None:
        auth = {"Authorization": "Bearer test-token"}
        self.client.post("/grocery-list/append", json={"items": ["milk", "eggs"]}, headers=auth)
        snapshot = self.client.get("/grocery-list", headers=auth).get_json()
        milk_id = next(row["id"] for row in snapshot["items"] if row["item"] == "milk")
        preview = self.client.post(
            "/grocery-list/change-sets/preview",
            json={"selection": {"entity_ids": [milk_id], "expected_revision": snapshot["revision"]}, "changes": [{"entity_id": milk_id, "operation": "remove", "expected": {"item": "milk"}}]},
            headers=auth,
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self.client.post("/grocery-list/append", json={"items": ["bread"]}, headers=auth).status_code, 200)
        stale_commit = self.client.post(
            "/grocery-list/change-sets/commit",
            json={"preview_id": preview.get_json()["preview_id"], "approved": True, "confirm_destructive": True},
            headers=auth,
        )
        self.assertEqual(stale_commit.status_code, 409)


class DatabaseMigrationTests(unittest.TestCase):
    def test_recipe_import_is_repeatable_and_replaces_removed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = RecipeDatabase(Path(temp_dir) / "recipes.db")
            first = [
                {
                    "recipe_id": "one",
                    "title": "One",
                    "source": {"mtime_utc": "2026-08-02T00:00:00Z"},
                    "components": [{"title": "Sauce"}],
                },
                {
                    "recipe_id": "two",
                    "title": "Two",
                    "source": {"mtime_utc": "2026-08-01T00:00:00Z"},
                    "components": [],
                },
            ]
            self.assertEqual(database.replace_from_recipes(first), 2)
            self.assertEqual(database.stats()["recipes"], 2)
            backup_path = Path(temp_dir) / "backup" / "recipes.db"
            database.backup_to(backup_path)
            backup = RecipeDatabase(backup_path)
            self.assertEqual(backup.stats()["recipes"], 2)
            self.assertEqual(backup.stats()["components"], 1)
            self.assertEqual(database.stats()["components"], 1)
            self.assertEqual(database.search_recipe_ids(["sauce"]), {"one"})
            self.assertEqual(database.load_recipes()[0]["recipe_id"], "one")

            self.assertEqual(database.replace_from_recipes(first), 2)
            self.assertEqual(database.stats()["recipes"], 2)

            self.assertEqual(database.replace_from_recipes(first[:1]), 1)
            self.assertEqual(database.stats()["recipes"], 1)
            self.assertEqual(database.stats()["components"], 1)

    def test_store_can_read_through_sqlite_during_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            structured = root / "structured"
            structured.mkdir()
            ndjson = structured / "recipes.ndjson"
            ndjson.write_text(
                json.dumps({"recipe_id": "sqlite_recipe", "title": "SQLite Recipe"}) + "\n",
                encoding="utf-8",
            )
            database_path = structured / "recipes.db"
            database = RecipeDatabase(database_path)
            database.replace_from_ndjson(ndjson)
            source = root / "source"
            source.mkdir()
            store, _ = build_store(
                dataset=str(ndjson),
                source=str(source),
                out=str(structured),
                pipeline_script=str(Path.cwd() / "scripts/recipe_pipeline.py"),
                grocery_list=str(root / "grocery.json"),
                grocery_archive_dir=str(root / "archives"),
                database=str(database_path),
                data_source="sqlite",
            )
            self.assertEqual(store.data_source, "sqlite")
            self.assertEqual(store.list_recipes(None, 10, 0)[0]["recipe_id"], "sqlite_recipe")
            app = create_app(
                store=store,
                grocery_store=_,
                api_key=None,
                custom_instructions_path=root / "instructions.md",
                bearer_token="test-token",
            )
            client = app.test_client()
            auth = {"Authorization": "Bearer test-token"}
            self.assertEqual(
                client.post("/grocery-list/append", json={"items": ["onion"]}, headers=auth).status_code,
                200,
            )
            grocery = client.get("/grocery-list", headers=auth).get_json()
            self.assertEqual(grocery["count"], 1)
            self.assertIn("is_stale", grocery)
            self.assertFalse(
                client.post("/grocery-list/append", json={"items": ["onion"]}, headers=auth)
                .get_json()["stale_before_append"]
            )
            old_payload = database.load_grocery_state()
            old_payload["started_at_utc"] = "2026-07-22T12:00:00Z"
            database.save_grocery_state(old_payload)
            stale_append = client.post("/grocery-list/append", json={"items": ["onion"]}, headers=auth)
            self.assertEqual(stale_append.status_code, 200)
            self.assertTrue(stale_append.get_json()["stale_before_append"])
            self.assertEqual(stale_append.get_json()["existing_cart_started_local_date"], "2026-07-22")
            self.assertEqual(stale_append.get_json()["existing_cart_started_local_day"], "Wednesday")
            rejected = client.post(
                "/grocery-list/append",
                json={"items": ["milk"], "recipe_ids": ["missing-recipe"]},
                headers=auth,
            )
            self.assertEqual(rejected.status_code, 404)
            self.assertEqual(client.get("/grocery-list", headers=auth).get_json()["count"], 1)
            item_id = grocery["items"][0]["id"]
            updated = client.patch(
                f"/grocery-list/items/{item_id}",
                json={"quantity": 2, "unit": "lb", "purchased": True},
                headers=auth,
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.get_json()["item"]["quantity"], 2)
            self.assertEqual(client.delete(f"/grocery-list/items/{item_id}", headers=auth).status_code, 200)
            self.assertEqual(client.get("/grocery-list", headers=auth).get_json()["count"], 0)
            self.assertEqual(
                client.post("/grocery-list/append", json={"items": ["onion"]}, headers=auth).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/grocery-list/archive",
                    json={"name": "Test archive"},
                    headers=auth,
                ).status_code,
                200,
            )
            _, second_grocery_store = build_store(
                dataset=str(ndjson),
                source=str(source),
                out=str(structured),
                pipeline_script=str(Path.cwd() / "scripts/recipe_pipeline.py"),
                grocery_list=str(root / "grocery.json"),
                grocery_archive_dir=str(root / "archives"),
                database=str(database_path),
                data_source="sqlite",
            )
            self.assertEqual(second_grocery_store.get_items()["count"], 0)
            archives = client.get("/grocery-list/archives", headers=auth).get_json()
            self.assertEqual(archives["count"], 1)
            archive_id = archives["items"][0]["archive_id"]
            self.assertEqual(client.delete(f"/grocery-list/archives/{archive_id}", headers=auth).status_code, 200)
            self.assertEqual(client.get("/grocery-list/archives", headers=auth).get_json()["count"], 0)
            deleted = client.get(
                f"/grocery-list/archives/{archive_id}?include_deleted=true",
                headers=auth,
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.get_json()["status"], "deleted")
            self.assertEqual(
                client.post("/grocery-list/append", json={"items": ["garlic"]}, headers=auth).status_code,
                200,
            )
            self.assertEqual(
                client.post("/grocery-list/archive", json={"name": "Restorable"}, headers=auth).status_code,
                200,
            )
            restorable = client.get("/grocery-list/archives", headers=auth).get_json()["items"][0]["archive_id"]
            restored = client.post(f"/grocery-list/archives/{restorable}/restore", headers=auth)
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.get_json()["count"], 1)
            self.assertGreaterEqual(len(database.list_grocery_events()), 5)
            self.assertEqual(
                client.put(
                    "/custom-instructions",
                    json={"instructions": "Use concise guidance."},
                    headers=auth,
                ).status_code,
                200,
            )
            self.assertEqual(
                client.get("/custom-instructions", headers=auth).get_json()["instructions"],
                "Use concise guidance.",
            )
