import json
import tempfile
import unittest
from pathlib import Path

from scripts.recipe_pipeline import is_candidate, split_sections
from services.recipe_api import build_store, create_app


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
