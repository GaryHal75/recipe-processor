import json
import tempfile
import unittest
from pathlib import Path

from scripts.recipe_pipeline import is_candidate, split_sections
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
