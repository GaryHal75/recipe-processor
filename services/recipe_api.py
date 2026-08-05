#!/usr/bin/env python3
"""Local recipe API for querying normalized recipe data."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, request, send_file


DEFAULT_DATASET = "structured/recipes.ndjson"
DEFAULT_SOURCE = "."
DEFAULT_OUT = "structured"
DEFAULT_GROCERY_LIST_PATH = "structured/grocery_list.json"
DEFAULT_GROCERY_ARCHIVE_DIR = "structured/grocery_list_archive"
DEFAULT_CUSTOM_INSTRUCTIONS_PATH = "structured/recipe_custom_instructions.md"
DEFAULT_PORT = 8787
API_KEY_ENV = "RECIPE_API_KEY"
BEARER_TOKEN_ENV = "RECIPE_API_BEARER_TOKEN"
HEADER_KEY = "X-API-Key"
AUTHORIZATION_HEADER = "Authorization"
OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.recipes.yaml"
LOCAL_TIMEZONE = ZoneInfo(os.getenv("TZ", "America/New_York"))
ROLE_KEYWORDS = {
    "main": {
        "chicken",
        "beef",
        "steak",
        "pork",
        "salmon",
        "shrimp",
        "fish",
        "tofu",
        "meatball",
        "meatballs",
        "curry",
        "pasta",
        "taco",
        "tacos",
        "bowl",
        "sandwich",
        "burger",
        "soup",
        "stew",
        "pizza",
    },
    "side": {
        "salad",
        "slaw",
        "rice",
        "beans",
        "potatoes",
        "fries",
        "broccoli",
        "asparagus",
        "carrots",
        "vegetables",
        "vegetable",
        "green beans",
        "cauliflower",
        "corn",
        "bread",
        "toast",
        "quinoa",
        "couscous",
    },
    "sauce": {
        "sauce",
        "salsa",
        "crema",
        "aioli",
        "dressing",
        "chutney",
        "pesto",
        "gravy",
        "glaze",
        "vinaigrette",
        "relish",
        "dip",
    },
    "utility": {
        "marinade",
        "rub",
        "seasoning",
        "stock",
        "broth",
        "pickled",
        "pickle",
        "jam",
        "butter",
    },
}
CUISINE_KEYWORDS = {
    "mexican": {"taco", "tacos", "salsa", "crema", "quesadilla", "enchilada", "fajita", "poblano", "chipotle", "cumin"},
    "italian": {"pasta", "parmesan", "mozzarella", "pesto", "marinara", "alfredo", "risotto", "basil"},
    "asian": {"soy", "sesame", "ginger", "miso", "teriyaki", "gochujang", "rice vinegar", "noodle", "noodles"},
    "indian": {"curry", "garam masala", "masala", "turmeric", "naan", "raita"},
    "mediterranean": {"tzatziki", "feta", "hummus", "zaatar", "lemon", "olive", "yogurt"},
    "american": {"bbq", "barbecue", "burger", "slaw", "gravy", "ranch"},
}
PROTEIN_KEYWORDS = {
    "chicken": {"chicken"},
    "beef": {"beef", "steak"},
    "pork": {"pork", "bacon", "ham", "sausage"},
    "seafood": {"shrimp", "salmon", "fish", "cod", "tuna", "crab"},
    "tofu": {"tofu"},
    "vegetarian": {"beans", "lentils", "chickpeas", "mushroom", "cauliflower"},
}
PLATFORM_KEYWORDS = {
    "pasta": {"pasta", "spaghetti", "linguine", "penne", "rigatoni"},
    "taco": {"taco", "tacos", "tortilla"},
    "rice": {"rice", "pilaf"},
    "bowl": {"bowl"},
    "salad": {"salad"},
    "sandwich": {"sandwich", "burger", "bun"},
    "noodle": {"noodle", "noodles", "ramen"},
}
SEARCH_SYNONYMS = {
    "coleslaw": {"slaw", "cole", "coleslaw"},
    "slaw": {"slaw", "cole", "coleslaw"},
    "bbq": {"bbq", "barbecue", "barbeque"},
    "barbecue": {"bbq", "barbecue", "barbeque"},
    "barbeque": {"bbq", "barbecue", "barbeque"},
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_search_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"([0-9])([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])([0-9])", r"\1 \2", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def tokenize(text: str) -> set[str]:
    text = normalize_search_text(text)
    buf = []
    parts = text.split()
    buf.extend(parts)
    for idx in range(len(parts) - 1):
        left = parts[idx]
        right = parts[idx + 1]
        if (left.isdigit() and right.isalpha()) or (left.isalpha() and right.isdigit()):
            buf.append(f"{left}{right}")
    return {w for w in buf if len(w) > 1}


def compact_search_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def expand_search_terms(terms: set[str], enabled: bool) -> set[str]:
    if not enabled:
        return set(terms)
    expanded = set(terms)
    for term in list(terms):
        expanded.update(SEARCH_SYNONYMS.get(term, {term}))
    return expanded


def build_search_document(recipe: dict[str, Any]) -> dict[str, Any]:
    title = recipe.get("title") or ""
    body = " ".join(
        [
            title,
            " ".join(recipe.get("ingredients") or []),
            " ".join(recipe.get("steps") or []),
            " ".join(recipe.get("notes") or []),
            " ".join(
                " ".join(
                    [
                        component.get("title") or "",
                        " ".join(component.get("ingredients") or []),
                        " ".join(component.get("steps") or []),
                        " ".join(component.get("compatibility_tags") or []),
                    ]
                )
                for component in (recipe.get("components") or [])
            ),
        ]
    )
    title_norm = normalize_search_text(title)
    body_norm = normalize_search_text(body)
    return {
        "title_text": title_norm,
        "body_text": body_norm,
        "title_compact": compact_search_text(title),
        "body_compact": compact_search_text(body),
        "title_tokens": tokenize(title),
        "body_tokens": tokenize(body),
    }


def normalize_duplicate_key(text: str) -> str:
    return normalize_search_text(text)


def recipe_source_stem(recipe: dict[str, Any]) -> str:
    source = recipe.get("source") or {}
    file_name = source.get("file_name") or source.get("relative_path") or ""
    if not file_name:
        return ""
    return Path(file_name).stem


def build_duplicate_signals(recipe: dict[str, Any]) -> dict[str, str]:
    return {
        "title_key": normalize_duplicate_key(recipe.get("title") or ""),
        "source_key": normalize_duplicate_key(recipe_source_stem(recipe)),
    }


def recipe_sort_key(recipe: dict[str, Any]) -> tuple[str, str]:
    source = recipe.get("source") or {}
    source_mtime = source.get("mtime_utc") or ""
    ingested_at = recipe.get("ingested_at_utc") or ""
    return (source_mtime, ingested_at)


def phrase_hits(text: str, phrases: set[str]) -> int:
    return sum(1 for phrase in phrases if phrase in text)


def extract_tags(text: str, mapping: dict[str, set[str]]) -> list[str]:
    return sorted(tag for tag, phrases in mapping.items() if phrase_hits(text, phrases))


def collect_recipe_text(recipe: dict[str, Any]) -> str:
    return " ".join(
        [
            (recipe.get("title") or "").lower(),
            " ".join(str(item).lower() for item in (recipe.get("ingredients") or [])),
            " ".join(str(item).lower() for item in (recipe.get("steps") or [])),
            " ".join(str(item).lower() for item in (recipe.get("notes") or [])),
        ]
    )


def infer_recipe_profile(recipe: dict[str, Any]) -> dict[str, Any]:
    stored_profile = recipe.get("profile")
    if isinstance(stored_profile, dict) and stored_profile.get("component_type"):
        return stored_profile

    text = collect_recipe_text(recipe)
    title = (recipe.get("title") or "").lower()
    scores = {role: phrase_hits(text, phrases) for role, phrases in ROLE_KEYWORDS.items()}

    if phrase_hits(title, ROLE_KEYWORDS["sauce"]):
        scores["sauce"] += 3
    if phrase_hits(title, ROLE_KEYWORDS["side"]):
        scores["side"] += 3
    if phrase_hits(title, ROLE_KEYWORDS["utility"]):
        scores["utility"] += 2
    if phrase_hits(title, ROLE_KEYWORDS["main"]):
        scores["main"] += 2

    ingredient_count = len(recipe.get("ingredients") or [])
    step_count = len(recipe.get("steps") or [])
    if ingredient_count <= 8 and step_count <= 4 and max(scores.values(), default=0) == 0:
        scores["utility"] += 1
    if ingredient_count >= 6 and step_count >= 3 and max(scores.values(), default=0) == 0:
        scores["main"] += 1

    component_type = max(scores, key=scores.get)
    if scores[component_type] == 0:
        component_type = "main"

    cuisines = extract_tags(text, CUISINE_KEYWORDS)
    proteins = extract_tags(text, PROTEIN_KEYWORDS)
    platforms = extract_tags(text, PLATFORM_KEYWORDS)

    return {
        "component_type": component_type,
        "role_scores": scores,
        "cuisines": cuisines,
        "proteins": proteins,
        "platforms": platforms,
        "is_pairing_component": component_type in {"side", "sauce", "utility"},
    }


def score_pairing(
    main_recipe: dict[str, Any],
    main_profile: dict[str, Any],
    candidate_recipe: dict[str, Any],
    candidate_profile: dict[str, Any],
) -> dict[str, Any] | None:
    candidate_type = candidate_profile["component_type"]
    if candidate_recipe.get("recipe_id") == main_recipe.get("recipe_id"):
        return None
    if candidate_type not in {"side", "sauce", "utility"}:
        return None

    main_platforms = set(main_profile["platforms"])
    candidate_platforms = set(candidate_profile["platforms"])
    if (
        candidate_type in {"sauce", "utility"}
        and main_platforms
        and candidate_platforms
        and not main_platforms.intersection(candidate_platforms)
    ):
        return None

    score = {"side": 30, "sauce": 28, "utility": 24}[candidate_type]
    reasons: list[str] = [f"{candidate_type} candidate"]

    shared_cuisines = sorted(set(main_profile["cuisines"]).intersection(candidate_profile["cuisines"]))
    if shared_cuisines:
        score += 10 + (len(shared_cuisines) - 1) * 4
        reasons.append(f"shared cuisine: {', '.join(shared_cuisines)}")
    elif not candidate_profile["cuisines"]:
        score += 3
        reasons.append("flexible cuisine")

    shared_platforms = sorted(main_platforms.intersection(candidate_platforms))
    if shared_platforms:
        score += 8
        reasons.append(f"shared format: {', '.join(shared_platforms)}")

    main_proteins = set(main_profile["proteins"])
    candidate_proteins = set(candidate_profile["proteins"])
    shared_proteins = sorted(main_proteins.intersection(candidate_proteins))
    if shared_proteins:
        score += 2
        reasons.append(f"shared protein profile: {', '.join(shared_proteins)}")
    elif candidate_proteins and main_proteins and candidate_type in {"sauce", "utility"}:
        score -= 10
        reasons.append("protein-specific mismatch")

    if score < 20:
        return None

    return {
        "recipe_id": candidate_recipe.get("recipe_id"),
        "title": candidate_recipe.get("title"),
        "component_type": candidate_type,
        "score": score,
        "reasons": reasons,
        "cuisines": candidate_profile["cuisines"],
        "proteins": candidate_profile["proteins"],
        "platforms": candidate_profile["platforms"],
    }


def compose_meal_suggestions(
    main_recipe: dict[str, Any],
    sides: list[dict[str, Any]],
    sauces: list[dict[str, Any]],
    utilities: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    combos: list[dict[str, Any]] = []
    sauce_like = (sauces[:3] + utilities[:3])[:4]
    side_like = sides[:3]

    if not side_like and not sauce_like:
        return combos

    for side in side_like or [None]:
        for sauce in sauce_like or [None]:
            if side is None and sauce is None:
                continue
            title_parts = [main_recipe.get("title") or "Main"]
            score = 0
            if side:
                title_parts.append(side["title"])
                score += int(side["score"])
            if sauce:
                title_parts.append(sauce["title"])
                score += int(sauce["score"])
            combos.append(
                {
                    "title": " + ".join(title_parts),
                    "main_recipe_id": main_recipe.get("recipe_id"),
                    "side_recipe_id": side["recipe_id"] if side else None,
                    "sauce_recipe_id": sauce["recipe_id"] if sauce else None,
                    "score": score,
                }
            )

    combos.sort(key=lambda row: row["score"], reverse=True)
    return combos[:limit]


def normalize_grocery_item(value: str) -> str:
    value = " ".join(value.strip().split())
    return value.casefold()


def grocery_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item.get("added_at_utc") or "", item.get("item") or "")


def archive_sort_key(archive: dict[str, Any]) -> tuple[str, str]:
    return (archive.get("archived_at_utc") or "", archive.get("archive_id") or "")


def local_date_parts(utc_iso: str | None) -> tuple[str | None, str | None]:
    if not utc_iso:
        return None, None
    try:
        dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    local_dt = dt.astimezone(LOCAL_TIMEZONE)
    return local_dt.date().isoformat(), local_dt.strftime("%A")


@dataclass
class RecipeStore:
    dataset_path: Path
    source_dir: Path
    out_dir: Path
    pipeline_script: Path
    lock: threading.RLock = field(default_factory=threading.RLock)
    loaded_at_utc: str | None = None
    recipes: list[dict[str, Any]] = None
    by_id: dict[str, dict[str, Any]] = None
    search_docs: dict[str, dict[str, Any]] = None
    duplicate_info: dict[str, dict[str, Any]] = None
    profiles: dict[str, dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.recipes = []
        self.by_id = {}
        self.search_docs = {}
        self.duplicate_info = {}
        self.profiles = {}

    def load(self) -> int:
        recipes: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        search_docs: dict[str, dict[str, Any]] = {}
        duplicate_info: dict[str, dict[str, Any]] = {}
        profiles: dict[str, dict[str, Any]] = {}
        title_groups: dict[str, set[str]] = {}
        source_groups: dict[str, set[str]] = {}

        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found: {self.dataset_path}")

        with self.dataset_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                rid = payload.get("recipe_id")
                if not rid:
                    continue
                recipes.append(payload)
                by_id[rid] = payload
                profiles[rid] = infer_recipe_profile(payload)
                search_docs[rid] = build_search_document(payload)
                duplicate_signals = build_duplicate_signals(payload)
                title_key = duplicate_signals["title_key"]
                source_key = duplicate_signals["source_key"]
                if title_key:
                    title_groups.setdefault(title_key, set()).add(rid)
                if source_key:
                    source_groups.setdefault(source_key, set()).add(rid)

        for rid, recipe in by_id.items():
            duplicate_signals = build_duplicate_signals(recipe)
            title_matches = sorted(title_groups.get(duplicate_signals["title_key"], set()) - {rid})
            source_matches = sorted(source_groups.get(duplicate_signals["source_key"], set()) - {rid})
            reasons: list[str] = []
            if title_matches:
                reasons.append("title_casefold")
            if source_matches:
                reasons.append("source_file_casefold")
            duplicate_info[rid] = {
                "is_potential_duplicate": bool(reasons),
                "duplicate_candidate_ids": sorted(set(title_matches + source_matches)),
                "duplicate_match_reasons": reasons,
            }

        with self.lock:
            self.recipes = recipes
            self.by_id = by_id
            self.search_docs = search_docs
            self.duplicate_info = duplicate_info
            self.profiles = profiles
            self.loaded_at_utc = now_iso()
        return len(recipes)

    def stats(self) -> dict[str, Any]:
        with self.lock:
            return {
                "recipes": len(self.recipes),
                "loaded_at_utc": self.loaded_at_utc,
                "dataset_path": str(self.dataset_path),
            }

    def list_recipes(self, q: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.recipes[:]
            if q:
                ranked = self._search_rows(q, require_all_terms=False, expand_terms=True)
                rows = [recipe for _, recipe in ranked]
            else:
                rows.sort(key=recipe_sort_key, reverse=True)
            rows = rows[offset : offset + limit]
            return [summarize_recipe(r, duplicate_info=self.duplicate_info.get(r.get("recipe_id"))) for r in rows]

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.by_id.get(recipe_id)

    def get_profile(self, recipe_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.profiles.get(recipe_id)

    def get_duplicate_info(self, recipe_id: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.duplicate_info.get(recipe_id) or {})

    def _search_rows(
        self,
        q: str,
        *,
        require_all_terms: bool = False,
        expand_terms: bool = True,
    ) -> list[tuple[int, dict[str, Any]]]:
        raw_terms = tokenize(q)
        if not raw_terms:
            return []
        required_terms = set(raw_terms)
        terms = expand_search_terms(raw_terms, expand_terms)
        query_norm = normalize_search_text(q)
        query_compact = compact_search_text(q)
        require_overlap = len(required_terms) if require_all_terms else max(1, min(len(required_terms), 2))

        scored: list[tuple[int, str, str, dict[str, Any]]] = []
        with self.lock:
            for recipe in self.recipes:
                rid = recipe["recipe_id"]
                doc = self.search_docs.get(rid) or {}
                body_tokens = doc.get("body_tokens", set())
                title_tokens = doc.get("title_tokens", set())
                required_overlap = required_terms.intersection(body_tokens)
                expanded_overlap = terms.intersection(body_tokens)
                title_overlap = terms.intersection(title_tokens)
                title_text = doc.get("title_text") or ""
                body_text = doc.get("body_text") or ""
                title_compact = doc.get("title_compact") or ""
                body_compact = doc.get("body_compact") or ""
                compact_hit = bool(query_compact and query_compact in body_compact)

                if require_all_terms:
                    if not required_terms.issubset(body_tokens) and not compact_hit:
                        continue
                elif len(expanded_overlap) < require_overlap and not compact_hit:
                    continue

                score = len(required_overlap) * 20
                score += len(expanded_overlap) * 6
                score += len(title_overlap) * 12

                if required_terms and required_terms.issubset(title_tokens):
                    score += 60
                elif required_terms and required_terms.issubset(body_tokens):
                    score += 35

                if query_norm and query_norm in title_text:
                    score += 50
                elif query_norm and query_norm in body_text:
                    score += 24

                if query_compact and query_compact in title_compact:
                    score += 24
                elif query_compact and query_compact in body_compact:
                    score += 12

                if score <= 0:
                    continue
                scored.append((score, *recipe_sort_key(recipe), recipe))

        scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        return [(score, recipe) for score, _, _, recipe in scored]

    def search(
        self,
        q: str,
        limit: int,
        *,
        require_all_terms: bool = False,
        expand_terms: bool = True,
    ) -> list[dict[str, Any]]:
        rows = self._search_rows(q, require_all_terms=require_all_terms, expand_terms=expand_terms)
        return [
            summarize_recipe(
                r,
                score=s,
                duplicate_info=self.duplicate_info.get(r.get("recipe_id")),
            )
            for s, r in rows[:limit]
        ]

    def search_metadata(self, q: str, *, require_all_terms: bool = False, expand_terms: bool = True) -> dict[str, Any]:
        raw_terms = tokenize(q)
        if not raw_terms:
            return {"query_terms": [], "expanded_terms": [], "require_all_terms": require_all_terms}
        expanded = expand_search_terms(raw_terms, expand_terms)
        return {
            "query_terms": sorted(raw_terms),
            "expanded_terms": sorted(expanded),
            "require_all_terms": require_all_terms,
        }

    def generate_pairings(
        self,
        recipe_id: str,
        *,
        side_limit: int = 5,
        sauce_limit: int = 5,
        utility_limit: int = 5,
        combo_limit: int = 8,
    ) -> dict[str, Any] | None:
        with self.lock:
            main_recipe = self.by_id.get(recipe_id)
            main_profile = self.profiles.get(recipe_id)
            recipes = self.recipes[:]
            profiles = dict(self.profiles)

        if not main_recipe or not main_profile:
            return None

        sides: list[dict[str, Any]] = []
        sauces: list[dict[str, Any]] = []
        utilities: list[dict[str, Any]] = []

        for candidate in recipes:
            candidate_id = candidate.get("recipe_id")
            candidate_profile = profiles.get(candidate_id)
            if not candidate_profile:
                continue
            scored = score_pairing(main_recipe, main_profile, candidate, candidate_profile)
            if not scored:
                continue
            if scored["component_type"] == "side":
                sides.append(scored)
            elif scored["component_type"] == "sauce":
                sauces.append(scored)
            else:
                utilities.append(scored)

        sides.sort(key=lambda row: row["score"], reverse=True)
        sauces.sort(key=lambda row: row["score"], reverse=True)
        utilities.sort(key=lambda row: row["score"], reverse=True)

        return {
            "main": summarize_recipe(main_recipe),
            "main_profile": main_profile,
            "recommended_sides": sides[:side_limit],
            "recommended_sauces": sauces[:sauce_limit],
            "recommended_utilities": utilities[:utility_limit],
            "meal_combinations": compose_meal_suggestions(
                main_recipe,
                sides=sides,
                sauces=sauces,
                utilities=utilities,
                limit=combo_limit,
            ),
        }

    def run_ingest(self) -> dict[str, Any]:
        state_path = self.out_dir / ".pipeline_state.json"
        cmd = [
            "python3",
            str(self.pipeline_script),
            "--source",
            str(self.source_dir),
            "--out",
            str(self.out_dir),
            "--state",
            str(state_path),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.source_dir),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        result = {
            "return_code": proc.returncode,
            "stdout": proc.stdout.strip().splitlines()[-15:],
            "stderr": proc.stderr.strip().splitlines()[-15:],
        }
        if proc.returncode != 0:
            return result
        self.load()
        return result


@dataclass
class GroceryListStore:
    file_path: Path
    archive_dir: Path
    lock: threading.Lock = field(default_factory=threading.Lock)
    items: list[dict[str, Any]] = None
    by_normalized: dict[str, dict[str, Any]] = None
    started_at_utc: str | None = None
    updated_at_utc: str | None = None

    def __post_init__(self) -> None:
        self.items = []
        self.by_normalized = {}
        self.load()

    def load(self) -> None:
        payload: dict[str, Any] = {"items": [], "started_at_utc": None, "updated_at_utc": None}
        if self.file_path.exists():
            try:
                payload = json.loads(self.file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {"items": [], "started_at_utc": None, "updated_at_utc": None}

        items = []
        by_normalized = {}
        for row in payload.get("items") or []:
            item_text = (row.get("item") or "").strip()
            if not item_text:
                continue
            normalized = row.get("normalized_item") or normalize_grocery_item(item_text)
            item = {
                "id": row.get("id") or uuid.uuid4().hex,
                "item": item_text,
                "normalized_item": normalized,
                "added_at_utc": row.get("added_at_utc") or now_iso(),
                "source": row.get("source") or None,
                "recipe_id": row.get("recipe_id") or None,
                "recipe_title": row.get("recipe_title") or None,
            }
            items.append(item)
            by_normalized[normalized] = item

        items.sort(key=grocery_sort_key, reverse=True)
        with self.lock:
            self.items = items
            self.by_normalized = by_normalized
            self.started_at_utc = payload.get("started_at_utc")
            self.updated_at_utc = payload.get("updated_at_utc") or now_iso()

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at_utc": self.started_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "items": self.items,
        }
        self.file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def get_items(self) -> dict[str, Any]:
        with self.lock:
            started_local_date, started_local_day = local_date_parts(self.started_at_utc)
            return {
                "count": len(self.items),
                "started_at_utc": self.started_at_utc,
                "started_local_date": started_local_date,
                "started_local_day": started_local_day,
                "updated_at_utc": self.updated_at_utc,
                "items": [summarize_grocery_item(item) for item in self.items],
            }

    def append_items(
        self,
        items: list[str],
        *,
        source: str | None = None,
        recipe_id: str | None = None,
        recipe_title: str | None = None,
    ) -> dict[str, Any]:
        added = 0
        duplicates = 0
        with self.lock:
            if self.started_at_utc is None:
                self.started_at_utc = now_iso()
            for raw in items:
                item_text = " ".join((raw or "").strip().split())
                if not item_text:
                    continue
                normalized = normalize_grocery_item(item_text)
                if normalized in self.by_normalized:
                    duplicates += 1
                    continue
                item = {
                    "id": uuid.uuid4().hex,
                    "item": item_text,
                    "normalized_item": normalized,
                    "added_at_utc": now_iso(),
                    "source": source,
                    "recipe_id": recipe_id,
                    "recipe_title": recipe_title,
                }
                self.items.append(item)
                self.by_normalized[normalized] = item
                added += 1

            self.items.sort(key=grocery_sort_key, reverse=True)
            self.updated_at_utc = now_iso()
            self.save()

            return {
                "ok": True,
                "added": added,
                "duplicates": duplicates,
                "count": len(self.items),
                "updated_at_utc": self.updated_at_utc,
                "items": [summarize_grocery_item(item) for item in self.items],
            }

    def clear(self) -> dict[str, Any]:
        with self.lock:
            self.items = []
            self.by_normalized = {}
            self.started_at_utc = None
            self.updated_at_utc = now_iso()
            self.save()
            return {
                "ok": True,
                "count": 0,
                "started_at_utc": None,
                "started_local_date": None,
                "started_local_day": None,
                "updated_at_utc": self.updated_at_utc,
                "items": [],
            }

    def archive_current(self, name: str | None = None) -> dict[str, Any]:
        with self.lock:
            archive_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
            archive_name = " ".join((name or "").strip().split()) or None
            archived_at = now_iso()
            started_local_date, started_local_day = local_date_parts(self.started_at_utc)
            archive_payload = {
                "archive_id": archive_id,
                "name": archive_name,
                "started_at_utc": self.started_at_utc,
                "started_local_date": started_local_date,
                "started_local_day": started_local_day,
                "archived_at_utc": archived_at,
                "item_count": len(self.items),
                "items": self.items,
            }
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = self.archive_dir / f"{archive_id}.json"
            archive_path.write_text(json.dumps(archive_payload, indent=2, ensure_ascii=True), encoding="utf-8")

            self.items = []
            self.by_normalized = {}
            self.started_at_utc = None
            self.updated_at_utc = archived_at
            self.save()

        return {
            "ok": True,
            "archived": summarize_grocery_archive(archive_payload),
            "current": {
                "count": 0,
                "started_at_utc": None,
                "started_local_date": None,
                "started_local_day": None,
                "updated_at_utc": self.updated_at_utc,
                "items": [],
            },
        }

    def list_archives(self) -> dict[str, Any]:
        archives: list[dict[str, Any]] = []
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.archive_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            archives.append(summarize_grocery_archive(payload))
        archives.sort(key=archive_sort_key, reverse=True)
        return {"count": len(archives), "items": archives}

    def get_archive(self, archive_id: str) -> dict[str, Any] | None:
        path = self.archive_dir / f"{archive_id}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return {
            "archive_id": payload.get("archive_id") or archive_id,
            "name": payload.get("name"),
            "started_at_utc": payload.get("started_at_utc"),
            "started_local_date": payload.get("started_local_date"),
            "started_local_day": payload.get("started_local_day"),
            "archived_at_utc": payload.get("archived_at_utc"),
            "item_count": len(payload.get("items") or []),
            "items": [summarize_grocery_item(item) for item in (payload.get("items") or [])],
        }


def summarize_recipe(
    recipe: dict[str, Any],
    score: int | None = None,
    duplicate_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = recipe.get("source") or {}
    profile = infer_recipe_profile(recipe)
    components = recipe.get("components") or []
    out = {
        "recipe_id": recipe.get("recipe_id"),
        "title": recipe.get("title"),
        "servings": recipe.get("servings"),
        "total_time_text": recipe.get("total_time_text"),
        "ingredient_count": len(recipe.get("ingredients") or []),
        "step_count": len(recipe.get("steps") or []),
        "source_file": source.get("file_name"),
        "source_mtime_utc": source.get("mtime_utc"),
        "ingested_at_utc": recipe.get("ingested_at_utc"),
        "component_type": profile.get("component_type"),
        "component_count": len(components),
        "is_potential_duplicate": False,
        "duplicate_candidate_ids": [],
        "duplicate_match_reasons": [],
    }
    if duplicate_info:
        out["is_potential_duplicate"] = bool(duplicate_info.get("is_potential_duplicate"))
        out["duplicate_candidate_ids"] = list(duplicate_info.get("duplicate_candidate_ids") or [])
        out["duplicate_match_reasons"] = list(duplicate_info.get("duplicate_match_reasons") or [])
    if score is not None:
        out["score"] = score
    return out


def summarize_component(component: dict[str, Any], recipe: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = component.get("profile") or {}
    payload = {
        "component_id": component.get("component_id"),
        "title": component.get("title"),
        "component_type": component.get("component_type") or profile.get("component_type"),
        "ingredient_count": len(component.get("ingredients") or []),
        "step_count": len(component.get("steps") or []),
        "compatibility_tags": component.get("compatibility_tags") or [],
        "cuisine_tags": profile.get("cuisine_tags") or [],
        "protein_tags": profile.get("protein_tags") or [],
        "platform_tags": profile.get("platform_tags") or [],
    }
    if recipe is not None:
        payload["recipe_id"] = recipe.get("recipe_id")
        payload["recipe_title"] = recipe.get("title")
    return payload


def summarize_grocery_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "item": item.get("item"),
        "added_at_utc": item.get("added_at_utc"),
        "source": item.get("source"),
        "recipe_id": item.get("recipe_id"),
        "recipe_title": item.get("recipe_title"),
    }


def summarize_grocery_archive(archive: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_id": archive.get("archive_id"),
        "name": archive.get("name"),
        "started_at_utc": archive.get("started_at_utc"),
        "started_local_date": archive.get("started_local_date"),
        "started_local_day": archive.get("started_local_day"),
        "archived_at_utc": archive.get("archived_at_utc"),
        "item_count": archive.get("item_count", len(archive.get("items") or [])),
    }


def create_app(
    store: RecipeStore,
    grocery_store: GroceryListStore,
    api_key: str | None,
    custom_instructions_path: Path,
    bearer_token: str | None = None,
) -> Flask:
    app = Flask(__name__)
    instructions_lock = threading.Lock()

    def read_custom_instructions() -> dict[str, Any]:
        with instructions_lock:
            if not custom_instructions_path.exists():
                return {"instructions": "", "path": str(custom_instructions_path), "updated_at_utc": None}
            body = custom_instructions_path.read_text(encoding="utf-8")
            updated_at = datetime.fromtimestamp(
                custom_instructions_path.stat().st_mtime,
                tz=UTC,
            ).isoformat().replace("+00:00", "Z")
            return {
                "instructions": body,
                "path": str(custom_instructions_path),
                "updated_at_utc": updated_at,
            }

    def write_custom_instructions(instructions: str) -> dict[str, Any]:
        with instructions_lock:
            custom_instructions_path.parent.mkdir(parents=True, exist_ok=True)
            custom_instructions_path.write_text(instructions, encoding="utf-8")
        return read_custom_instructions()

    @app.before_request
    def enforce_authentication() -> Any:
        if not api_key and not bearer_token:
            return
        if request.path in {"/", "/health", "/openapi.yaml"}:
            return
        if bearer_token:
            supplied_header = request.headers.get(AUTHORIZATION_HEADER, "")
            scheme, _, supplied_token = supplied_header.partition(" ")
            if scheme.lower() == "bearer" and supplied_token and hmac.compare_digest(supplied_token, bearer_token):
                return
        if api_key and hmac.compare_digest(request.headers.get(HEADER_KEY, ""), api_key):
            return
        response = jsonify({"error": "unauthorized", "message": "A valid bearer token is required."})
        response.status_code = 401
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.get("/health")
    def health() -> Any:
        return jsonify({"ok": True, "time_utc": now_iso(), **store.stats()})

    @app.get("/")
    def index() -> Any:
        return jsonify(
            {
                "service": "recipe_processor_api",
                "docs": "/openapi.yaml",
                "health": "/health",
                "list_recipes": "/recipes?limit=25&offset=0",
                "grocery_list": "/grocery-list",
                "search": {"method": "POST", "path": "/search", "body_example": {"q": "harissa shrimp", "limit": 10}},
                "notes": "Most routes require Authorization: Bearer <token> when bearer authentication is enabled.",
            }
        )

    @app.get("/openapi.yaml")
    def openapi_spec() -> Any:
        if not OPENAPI_PATH.exists():
            abort(404, description="openapi.yaml not found")
        return send_file(OPENAPI_PATH, mimetype="application/yaml")

    @app.get("/custom-instructions")
    def get_custom_instructions() -> Any:
        return jsonify({"ok": True, **read_custom_instructions()})

    @app.put("/custom-instructions")
    def put_custom_instructions() -> Any:
        body = request.get_json(silent=True) or {}
        instructions = body.get("instructions")
        if not isinstance(instructions, str):
            abort(400, description="Request JSON must include string field 'instructions'.")
        updated = write_custom_instructions(instructions.strip())
        return jsonify({"ok": True, **updated})

    @app.get("/recipes")
    def list_recipes() -> Any:
        q = (request.args.get("q") or "").strip() or None
        limit = min(max(int(request.args.get("limit", 25)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
        rows = store.list_recipes(q=q, limit=limit, offset=offset)
        return jsonify({"count": len(rows), "items": rows})

    @app.get("/recipes/<recipe_id>")
    def get_recipe(recipe_id: str) -> Any:
        row = store.get_recipe(recipe_id)
        if not row:
            abort(404, description=f"recipe_id not found: {recipe_id}")
        return jsonify(row)

    @app.get("/recipes/<recipe_id>/components")
    def get_recipe_components(recipe_id: str) -> Any:
        row = store.get_recipe(recipe_id)
        if not row:
            abort(404, description=f"recipe_id not found: {recipe_id}")
        components = [summarize_component(component, recipe=row) for component in (row.get("components") or [])]
        return jsonify({"recipe_id": recipe_id, "count": len(components), "items": components})

    @app.get("/recipes/<recipe_id>/pairings")
    def get_recipe_pairings(recipe_id: str) -> Any:
        side_limit = min(max(int(request.args.get("side_limit", 5)), 1), 25)
        sauce_limit = min(max(int(request.args.get("sauce_limit", 5)), 1), 25)
        utility_limit = min(max(int(request.args.get("utility_limit", 5)), 1), 25)
        combo_limit = min(max(int(request.args.get("combo_limit", 8)), 1), 30)
        payload = store.generate_pairings(
            recipe_id,
            side_limit=side_limit,
            sauce_limit=sauce_limit,
            utility_limit=utility_limit,
            combo_limit=combo_limit,
        )
        if not payload:
            abort(404, description=f"recipe_id not found: {recipe_id}")
        return jsonify(payload)

    @app.get("/pairings")
    def list_pairings() -> Any:
        recipe_id = (request.args.get("recipe_id") or "").strip()
        main_limit = min(max(int(request.args.get("limit", 10)), 1), 50)
        side_limit = min(max(int(request.args.get("side_limit", 3)), 1), 10)
        sauce_limit = min(max(int(request.args.get("sauce_limit", 3)), 1), 10)
        utility_limit = min(max(int(request.args.get("utility_limit", 2)), 1), 10)
        combo_limit = min(max(int(request.args.get("combo_limit", 4)), 1), 12)

        if recipe_id:
            payload = store.generate_pairings(
                recipe_id,
                side_limit=side_limit,
                sauce_limit=sauce_limit,
                utility_limit=utility_limit,
                combo_limit=combo_limit,
            )
            if not payload:
                abort(404, description=f"recipe_id not found: {recipe_id}")
            return jsonify(payload)

        mains = []
        with store.lock:
            recipes = store.recipes[:]
            profiles = dict(store.profiles)

        for recipe in recipes:
            recipe_id = recipe.get("recipe_id")
            profile = profiles.get(recipe_id) or {}
            if profile.get("component_type") != "main":
                continue
            pairing = store.generate_pairings(
                recipe_id,
                side_limit=side_limit,
                sauce_limit=sauce_limit,
                utility_limit=utility_limit,
                combo_limit=combo_limit,
            )
            if pairing:
                mains.append(pairing)

        mains.sort(key=lambda row: len(row["meal_combinations"]), reverse=True)
        return jsonify({"count": len(mains[:main_limit]), "items": mains[:main_limit]})

    @app.get("/components")
    def list_components() -> Any:
        component_type = (request.args.get("component_type") or "").strip().lower() or None
        recipe_id = (request.args.get("recipe_id") or "").strip() or None
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)

        if recipe_id:
            recipe = store.get_recipe(recipe_id)
            if not recipe:
                abort(404, description=f"recipe_id not found: {recipe_id}")
            components = [summarize_component(component, recipe=recipe) for component in (recipe.get("components") or [])]
        else:
            with store.lock:
                recipes = store.recipes[:]
            components = []
            for recipe in recipes:
                for component in (recipe.get("components") or []):
                    components.append(summarize_component(component, recipe=recipe))

        if component_type:
            components = [component for component in components if component.get("component_type") == component_type]

        components = components[:limit]
        return jsonify({"count": len(components), "items": components})

    @app.post("/search")
    def search() -> Any:
        body = request.get_json(silent=True) or {}
        q = (body.get("q") or body.get("query") or "").strip()
        if not q:
            abort(400, description="Request JSON must include non-empty 'q'.")
        limit = min(max(int(body.get("limit", 10)), 1), 100)
        require_all_terms = bool(body.get("require_all_terms", False))
        expand_terms = bool(body.get("expand_terms", True))
        rows = store.search(
            q=q,
            limit=limit,
            require_all_terms=require_all_terms,
            expand_terms=expand_terms,
        )
        return jsonify(
            {
                "count": len(rows),
                "items": rows,
                "search": store.search_metadata(
                    q,
                    require_all_terms=require_all_terms,
                    expand_terms=expand_terms,
                ),
            }
        )

    @app.post("/reload")
    def reload_dataset() -> Any:
        count = store.load()
        return jsonify({"ok": True, "recipes": count, "loaded_at_utc": store.loaded_at_utc})

    @app.post("/ingest")
    def ingest() -> Any:
        result = store.run_ingest()
        status = 200 if result["return_code"] == 0 else 500
        return jsonify({"ok": result["return_code"] == 0, **result, **store.stats()}), status

    @app.get("/grocery-list")
    def get_grocery_list() -> Any:
        return jsonify(grocery_store.get_items())

    @app.delete("/grocery-list")
    def clear_grocery_list() -> Any:
        return jsonify(grocery_store.clear())

    @app.get("/grocery-list/archives")
    def list_grocery_archives() -> Any:
        return jsonify(grocery_store.list_archives())

    @app.get("/grocery-list/archives/<archive_id>")
    def get_grocery_archive(archive_id: str) -> Any:
        archive = grocery_store.get_archive(archive_id)
        if not archive:
            abort(404, description=f"archive_id not found: {archive_id}")
        return jsonify(archive)

    @app.post("/grocery-list/archive")
    def archive_grocery_list() -> Any:
        body = request.get_json(silent=True) or {}
        name = body.get("name")
        return jsonify(grocery_store.archive_current(name=name))

    @app.post("/grocery-list/append")
    def append_grocery_list() -> Any:
        body = request.get_json(silent=True) or {}
        freeform_items = [str(item) for item in (body.get("items") or [])]
        recipe_ids = [str(recipe_id) for recipe_id in (body.get("recipe_ids") or [])]
        skip_recipe_ids = {str(recipe_id) for recipe_id in (body.get("skip_recipe_ids") or [])}
        source = (body.get("source") or "chat").strip() or "chat"

        if not freeform_items and not recipe_ids:
            abort(400, description="Request JSON must include non-empty 'items' or 'recipe_ids'.")

        added = 0
        duplicates = 0

        if freeform_items:
            result = grocery_store.append_items(freeform_items, source=source)
            added += result["added"]
            duplicates += result["duplicates"]

        for recipe_id in recipe_ids:
            if recipe_id in skip_recipe_ids:
                continue
            recipe = store.get_recipe(recipe_id)
            if not recipe:
                abort(404, description=f"recipe_id not found: {recipe_id}")
            result = grocery_store.append_items(
                [str(item) for item in (recipe.get("ingredients") or [])],
                source=source,
                recipe_id=recipe_id,
                recipe_title=recipe.get("title"),
            )
            added += result["added"]
            duplicates += result["duplicates"]

        snapshot = grocery_store.get_items()
        return jsonify({"ok": True, "added": added, "duplicates": duplicates, **snapshot})

    return app


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run local recipe API service.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--source", default=DEFAULT_SOURCE)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--grocery-list", default=DEFAULT_GROCERY_LIST_PATH)
    p.add_argument("--grocery-archive-dir", default=DEFAULT_GROCERY_ARCHIVE_DIR)
    p.add_argument("--custom-instructions", default=DEFAULT_CUSTOM_INSTRUCTIONS_PATH)
    p.add_argument("--pipeline-script", default="scripts/recipe_pipeline.py")
    p.add_argument("--api-key", default=None, help=f"Legacy compatibility option; if omitted, reads {API_KEY_ENV}.")
    p.add_argument("--bearer-token", default=None, help=f"If omitted, reads {BEARER_TOKEN_ENV}.")
    return p.parse_args()


def build_store(
    dataset: str,
    source: str,
    out: str,
    pipeline_script: str,
    grocery_list: str,
    grocery_archive_dir: str,
) -> tuple[RecipeStore, GroceryListStore]:
    root = Path.cwd()
    dataset_path = (root / dataset).resolve()
    source_dir = (root / source).resolve()
    out_dir = (root / out).resolve()
    grocery_list_path = (root / grocery_list).expanduser().resolve()
    grocery_archive_path = (root / grocery_archive_dir).expanduser().resolve()
    pipeline_script_path = (root / pipeline_script).resolve()

    if not pipeline_script_path.exists():
        raise FileNotFoundError(f"Missing pipeline script: {pipeline_script_path}")

    store = RecipeStore(
        dataset_path=dataset_path,
        source_dir=source_dir,
        out_dir=out_dir,
        pipeline_script=pipeline_script_path,
    )
    grocery_store = GroceryListStore(file_path=grocery_list_path, archive_dir=grocery_archive_path)
    store.load()
    return store, grocery_store


def app_from_env() -> Flask:
    dataset = os.getenv("RECIPE_DATASET", DEFAULT_DATASET)
    source = os.getenv("RECIPE_SOURCE", DEFAULT_SOURCE)
    out = os.getenv("RECIPE_OUT", DEFAULT_OUT)
    grocery_list = os.getenv("RECIPE_GROCERY_LIST_PATH", DEFAULT_GROCERY_LIST_PATH)
    grocery_archive_dir = os.getenv("RECIPE_GROCERY_ARCHIVE_DIR", DEFAULT_GROCERY_ARCHIVE_DIR)
    custom_instructions = os.getenv("RECIPE_CUSTOM_INSTRUCTIONS_PATH", DEFAULT_CUSTOM_INSTRUCTIONS_PATH)
    pipeline_script = os.getenv("RECIPE_PIPELINE_SCRIPT", "scripts/recipe_pipeline.py")
    api_key = os.getenv(API_KEY_ENV)
    bearer_token = os.getenv(BEARER_TOKEN_ENV)
    store, grocery_store = build_store(
        dataset=dataset,
        source=source,
        out=out,
        pipeline_script=pipeline_script,
        grocery_list=grocery_list,
        grocery_archive_dir=grocery_archive_dir,
    )
    return create_app(
        store=store,
        grocery_store=grocery_store,
        api_key=api_key,
        custom_instructions_path=(Path.cwd() / custom_instructions).expanduser().resolve(),
        bearer_token=bearer_token,
    )


def main() -> int:
    args = parse_args()
    store, grocery_store = build_store(
        dataset=args.dataset,
        source=args.source,
        out=args.out,
        pipeline_script=args.pipeline_script,
        grocery_list=args.grocery_list,
        grocery_archive_dir=args.grocery_archive_dir,
    )
    api_key = args.api_key or os.getenv(API_KEY_ENV)
    bearer_token = args.bearer_token or os.getenv(BEARER_TOKEN_ENV)
    app = create_app(
        store=store,
        grocery_store=grocery_store,
        api_key=api_key,
        custom_instructions_path=(Path.cwd() / args.custom_instructions).expanduser().resolve(),
        bearer_token=bearer_token,
    )
    app.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
