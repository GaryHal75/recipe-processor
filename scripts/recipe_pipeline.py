#!/usr/bin/env python3
"""Recipe normalization pipeline for local Google Drive folders.

Features:
- Ingest recipe files from a source folder.
- Write one normalized JSON file per recipe.
- Export a combined NDJSON file for retrieval/fine-tuning workflows.
- Optional polling watcher mode to auto-process new/changed files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".docx", ".txt", ".gdoc"}
IGNORE_PREFIXES = (".", "~")
SKIP_FILE_NAMES = {
    "AGENTS.md",
    "README.md",
    "RECIPE_API.md",
    "RECIPE_PIPELINE.md",
    "openapi.yaml",
    "openapi.recipes.yaml",
    "requirements.txt",
}
SKIP_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".ndjson", ".sh"}
PARSER_VERSION = 6
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
COMPONENT_HEADER_HINTS = {
    "sauce": ("sauce", "salsa", "crema", "dressing", "vinaigrette", "aioli", "glaze", "dip", "pesto", "gravy", "chutney"),
    "side": ("slaw", "salad", "rice", "beans", "vegetables", "veggies", "vegetable", "potatoes", "fries", "corn"),
    "utility": ("topping", "garnish", "pickled", "pickle", "marinade", "rub", "seasoning", "crumb", "crunch", "filling"),
    "base": ("base", "grain", "grains", "pasta", "tortillas", "wraps", "bread", "crust"),
}
COMPONENT_HEADER_PREFIXES = ("for", "serve with")
COMPONENT_HEADER_STOPWORDS = {
    "make it your own",
    "variation",
    "variations",
    "tips",
    "notes",
    "optional",
    "optional garnishes",
    "garnish",
    "garnishes",
}
INSTRUCTION_STARTERS = {
    "add",
    "bake",
    "blend",
    "bring",
    "broil",
    "combine",
    "cook",
    "cool",
    "drizzle",
    "fry",
    "heat",
    "let",
    "line",
    "make",
    "mix",
    "place",
    "pour",
    "preheat",
    "press",
    "refrigerate",
    "remove",
    "serve",
    "set",
    "spread",
    "sprinkle",
    "stir",
    "taste",
    "toast",
    "top",
    "transfer",
    "toss",
    "whisk",
}


@dataclass
class ParseResult:
    title: str
    servings: str | None
    total_time: str | None
    ingredients: list[str]
    steps: list[str]
    notes: list[str]


def phrase_hits(text: str, phrases: set[str] | tuple[str, ...]) -> int:
    return sum(1 for phrase in phrases if phrase in text)


def extract_tags(text: str, mapping: dict[str, set[str]]) -> list[str]:
    return sorted(tag for tag, phrases in mapping.items() if phrase_hits(text, phrases))


def collect_text_parts(*parts: str | list[str]) -> str:
    flattened: list[str] = []
    for part in parts:
        if isinstance(part, list):
            flattened.extend(str(item).lower() for item in part)
        else:
            flattened.append(str(part).lower())
    return " ".join(flattened)


def infer_profile(*, title: str, ingredients: list[str], steps: list[str], notes: list[str]) -> dict[str, Any]:
    text = collect_text_parts(title, ingredients, steps, notes)
    lower_title = title.lower()
    scores = {role: phrase_hits(text, phrases) for role, phrases in ROLE_KEYWORDS.items()}

    if phrase_hits(lower_title, ROLE_KEYWORDS["sauce"]):
        scores["sauce"] += 3
    if phrase_hits(lower_title, ROLE_KEYWORDS["side"]):
        scores["side"] += 3
    if phrase_hits(lower_title, ROLE_KEYWORDS["utility"]):
        scores["utility"] += 2
    if phrase_hits(lower_title, ROLE_KEYWORDS["main"]):
        scores["main"] += 2

    ingredient_count = len(ingredients)
    step_count = len(steps)
    if ingredient_count <= 8 and step_count <= 4 and max(scores.values(), default=0) == 0:
        scores["utility"] += 1
    if ingredient_count >= 6 and step_count >= 3 and max(scores.values(), default=0) == 0:
        scores["main"] += 1

    component_type = max(scores, key=scores.get)
    if scores[component_type] == 0:
        component_type = "main"

    return {
        "component_type": component_type,
        "role_scores": scores,
        "cuisine_tags": extract_tags(text, CUISINE_KEYWORDS),
        "protein_tags": extract_tags(text, PROTEIN_KEYWORDS),
        "platform_tags": extract_tags(text, PLATFORM_KEYWORDS),
        "is_pairing_component": component_type in {"side", "sauce", "utility", "base"},
    }


def classify_component_header(header: str) -> str:
    text = header.lower()
    for component_type, hints in COMPONENT_HEADER_HINTS.items():
        if any(hint in text for hint in hints):
            return component_type
    return "component"


def normalize_component_title(recipe_title: str, header: str) -> str:
    header = re.sub(r"^(for|make|prepare|assemble|serve with)\s+", "", header.strip(), flags=re.IGNORECASE)
    header = header.strip(":- ").strip()
    if not header:
        return recipe_title
    header = header.title()
    if recipe_title.lower() in header.lower():
        return header
    return f"{recipe_title} - {header}"


def normalize_header_candidate(line: str) -> str:
    cleaned = line.strip()
    cleaned = cleaned.strip("*_#")
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def looks_like_component_header(line: str) -> bool:
    candidate = normalize_header_candidate(line)
    if not candidate:
        return False

    lowered = candidate.lower().strip(":- ").strip()
    if not lowered:
        return False
    if lowered in COMPONENT_HEADER_STOPWORDS:
        return False
    if not any(lowered.startswith(prefix + " ") for prefix in COMPONENT_HEADER_PREFIXES):
        return False
    if len(lowered.split()) > 6:
        return False
    if lowered.count(".") > 0:
        return False

    words = lowered.replace(":", " ").replace("-", " ").split()
    if len(words) >= 2 and words[0] == "make" and words[1] == "the":
        return False
    if words and words[0] in INSTRUCTION_STARTERS and words[0] not in COMPONENT_HEADER_PREFIXES:
        return False
    return True


def is_ingredient_component_header(line: str) -> bool:
    candidate = normalize_header_candidate(line)
    if not candidate:
        return False
    lowered = candidate.lower().strip(":- ").strip()
    if lowered in COMPONENT_HEADER_STOPWORDS:
        return False
    if len(lowered.split()) > 8:
        return False
    if lowered.startswith("for ") or lowered.startswith("serve with "):
        return classify_component_header(candidate) != "component"
    return False


def parse_components(ingredient_lines: list[str], recipe_title: str) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    current_header: str | None = None
    current_ingredients: list[str] = []

    def flush_component() -> None:
        nonlocal current_header, current_ingredients
        if current_header is None or not current_ingredients:
            current_header = None
            current_ingredients = []
            return

        header_type = classify_component_header(current_header)
        if header_type == "component":
            current_header = None
            current_ingredients = []
            return
        if len(current_ingredients) < 2:
            current_header = None
            current_ingredients = []
            return

        display_title = normalize_component_title(recipe_title, current_header)
        profile_title = re.sub(r"^(for|make|prepare|assemble|serve with)\s+", "", current_header.strip(), flags=re.IGNORECASE)
        profile_title = profile_title.strip(":- ").strip() or display_title
        profile = infer_profile(
            title=profile_title,
            ingredients=current_ingredients,
            steps=[],
            notes=[],
        )
        component_id = slugify(f"{recipe_title}_{current_header}")
        components.append(
            {
                "component_id": component_id,
                "title": display_title,
                "component_type": header_type if profile["component_type"] == "main" else profile["component_type"],
                "ingredients": current_ingredients,
                "steps": [],
                "compatibility_tags": sorted(
                    set(profile["cuisine_tags"] + profile["protein_tags"] + profile["platform_tags"])
                ),
                "profile": profile,
            }
        )
        current_header = None
        current_ingredients = []

    for raw_line in ingredient_lines:
        line = clean_prefix(raw_line)
        if not line:
            continue
        if is_ingredient_component_header(line):
            flush_component()
            current_header = normalize_header_candidate(line)
            continue
        if current_header is not None:
            current_ingredients.append(line)

    flush_component()
    return components


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "recipe"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(IGNORE_PREFIXES):
        return False
    if path.name in SKIP_FILE_NAMES:
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    # Accept extension-less files in addition to known extensions.
    return path.suffix.lower() in SUPPORTED_EXTENSIONS or path.suffix == ""


def read_docx_text(path: Path) -> str:
    cmd = ["textutil", "-convert", "txt", "-stdout", str(path)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback for Linux hosts where macOS textutil is unavailable.
        return read_docx_text_fallback(path)


def read_docx_text_fallback(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except (FileNotFoundError, zipfile.BadZipFile, KeyError) as exc:
        raise RuntimeError(f"Unable to parse DOCX: {path}") from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"Unable to parse DOCX XML: {path}") from exc

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: list[str] = []
    for para in root.findall(".//w:p", ns):
        parts: list[str] = []
        for text in para.findall(".//w:t", ns):
            if text.text:
                parts.append(text.text)
        if parts:
            lines.append("".join(parts))
    return "\n".join(lines)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_gdoc_stub(path: Path) -> tuple[str, str | None]:
    raw = read_text_file(path).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    doc_id = payload.get("doc_id")
    note = "Google Docs stub (.gdoc). Export as .docx for full parsing."
    if doc_id:
        note += f" doc_id={doc_id}"
    return note, doc_id


def split_sections(text: str) -> ParseResult:
    lines = [ln.strip().replace("\u2028", " ") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    if not lines:
        return ParseResult(
            title="Untitled Recipe",
            servings=None,
            total_time=None,
            ingredients=[],
            steps=[],
            notes=[],
        )

    title = lines[0]
    servings = None
    total_time = None

    for ln in lines[1:10]:
        if servings is None:
            m = re.search(r"\bserves?\b\s*[:\-]?\s*(.+)", ln, re.IGNORECASE)
            if m:
                servings = m.group(1).strip()
        if total_time is None:
            m = re.search(r"\btotal\s*time\b\s*[:\-]?\s*(.+)", ln, re.IGNORECASE)
            if m:
                total_time = m.group(1).strip()

    idx_ing = find_section_index(lines, ["ingredients", "for the filling", "for the chicken"])
    idx_instr = find_section_index(lines, ["instructions", "step-by-step", "method", "directions"])

    ingredients: list[str] = []
    steps: list[str] = []
    notes: list[str] = []

    if idx_ing is not None:
        end = idx_instr if idx_instr is not None and idx_instr > idx_ing else len(lines)
        ingredients = normalize_list_items(lines[idx_ing + 1 : end])
    if idx_instr is not None:
        steps = normalize_steps(lines[idx_instr + 1 :])

    if not ingredients and not steps:
        # Fallback: classify bullet lines as ingredients and numbered lines as steps.
        for ln in lines[1:]:
            if re.match(r"^[\-\*\u2022]\s+", ln):
                ingredients.append(clean_prefix(ln))
            elif re.match(r"^\d+[\.\)]\s+", ln):
                steps.append(clean_prefix(ln))
            else:
                notes.append(ln)

    return ParseResult(
        title=title,
        servings=servings,
        total_time=total_time,
        ingredients=ingredients,
        steps=steps,
        notes=notes[:20],
    )


def find_section_index(lines: list[str], keywords: list[str]) -> int | None:
    lowered = [ln.lower() for ln in lines]
    for i, ln in enumerate(lowered):
        for kw in keywords:
            if kw in ln:
                return i
    return None


def clean_prefix(line: str) -> str:
    line = re.sub(r"^[\-\*\u2022]\s*", "", line)
    line = re.sub(r"^\d+[\.\)]\s*", "", line)
    return line.strip()


def normalize_list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in ("instructions", "method", "directions")):
            break
        ln = clean_prefix(ln)
        if ln:
            items.append(ln)
    return items


def normalize_steps(lines: list[str]) -> list[str]:
    steps: list[str] = []
    for ln in lines:
        low = ln.lower()
        if low.startswith("tips") or low.startswith("notes") or low.startswith("variations"):
            break
        ln = clean_prefix(ln)
        if ln:
            steps.append(ln)
    return steps


def parse_file(path: Path, root: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    doc_id = None
    parse_warning = None

    if ext == ".docx":
        text = read_docx_text(path)
    elif ext == ".gdoc":
        text, doc_id = read_gdoc_stub(path)
        parse_warning = "gdoc_stub"
    elif ext == "":
        # Some Google Drive files are OOXML docs without ".docx" extension.
        try:
            text = read_docx_text(path)
        except RuntimeError:
            text = read_text_file(path)
    else:
        text = read_text_file(path)

    parsed = split_sections(text)
    rel_path = str(path.relative_to(root))
    stem = path.stem if path.suffix else path.name
    recipe_id = slugify(stem)
    recipe_profile = infer_profile(
        title=parsed.title,
        ingredients=parsed.ingredients,
        steps=parsed.steps,
        notes=parsed.notes,
    )
    components = parse_components(parsed.ingredients, parsed.title)

    recipe: dict[str, Any] = {
        "recipe_id": recipe_id,
        "title": parsed.title,
        "servings": parsed.servings,
        "total_time_text": parsed.total_time,
        "ingredients": parsed.ingredients,
        "steps": parsed.steps,
        "notes": parsed.notes,
        "profile": recipe_profile,
        "component_type": recipe_profile["component_type"],
        "components": components,
        "source": {
            "file_name": path.name,
            "relative_path": rel_path,
            "file_type": ext or "no_extension",
            "sha256": file_digest(path),
            "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        "ingested_at_utc": now_iso(),
    }
    if doc_id:
        recipe["source"]["gdoc_id"] = doc_id
    if parse_warning:
        recipe["parse_warning"] = parse_warning

    return recipe


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"files": {}}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": {}}


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def write_recipe_json(recipe: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{recipe['recipe_id']}.json"
    out_path.write_text(json.dumps(recipe, indent=2, ensure_ascii=True), encoding="utf-8")
    return out_path


def prune_stale_recipe_jsons(recipes_dir: Path, source_dir: Path) -> int:
    removed = 0
    for jf in sorted(recipes_dir.glob("*.json")):
        try:
            payload = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            jf.unlink(missing_ok=True)
            removed += 1
            continue

        source = payload.get("source") or {}
        relative_path = source.get("relative_path")
        if not relative_path:
            jf.unlink(missing_ok=True)
            removed += 1
            continue

        source_path = source_dir / relative_path
        if not source_path.exists() or not is_candidate(source_path):
            jf.unlink(missing_ok=True)
            removed += 1
    return removed


def rebuild_ndjson(recipes_dir: Path, ndjson_path: Path) -> int:
    count = 0
    with ndjson_path.open("w", encoding="utf-8") as out:
        for jf in sorted(recipes_dir.glob("*.json")):
            try:
                payload = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            out.write(json.dumps(payload, ensure_ascii=True) + "\n")
            count += 1
    return count


def ingest_once(source_dir: Path, out_dir: Path, state_path: Path) -> tuple[int, int]:
    state = load_state(state_path)
    if state.get("parser_version") != PARSER_VERSION:
        known = {}
    else:
        known = state.get("files", {})
    updated = 0
    seen = 0

    recipes_dir = out_dir / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    active_files: dict[str, str] = {}

    for path in sorted(source_dir.iterdir()):
        if not is_candidate(path):
            continue
        seen += 1
        rel = str(path.relative_to(source_dir))
        digest = file_digest(path)
        active_files[rel] = digest
        if known.get(rel) == digest:
            continue

        recipe = parse_file(path, source_dir)
        write_recipe_json(recipe, recipes_dir)
        known[rel] = digest
        updated += 1
        print(f"updated: {rel}")

    stale_keys = sorted(set(known) - set(active_files))
    for rel in stale_keys:
        known.pop(rel, None)

    removed = prune_stale_recipe_jsons(recipes_dir, source_dir)
    state["files"] = active_files
    state["parser_version"] = PARSER_VERSION
    state["last_run_utc"] = now_iso()
    save_state(state_path, state)
    ndjson_count = rebuild_ndjson(recipes_dir, out_dir / "recipes.ndjson")
    print(f"done: scanned={seen} updated={updated} removed={removed} ndjson_records={ndjson_count}")
    return seen, updated


def watch(source_dir: Path, out_dir: Path, state_path: Path, interval_sec: int) -> None:
    print(f"watching {source_dir} every {interval_sec}s")
    while True:
        try:
            ingest_once(source_dir, out_dir, state_path)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"error: {exc}", file=sys.stderr)
        time.sleep(interval_sec)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Normalize recipe files into JSON + NDJSON.")
    p.add_argument("--source", default=".", help="Source folder with recipe files.")
    p.add_argument("--out", default="structured", help="Output folder for normalized data.")
    p.add_argument("--state", default="structured/.pipeline_state.json", help="State file path.")
    p.add_argument("--watch", action="store_true", help="Run continuously in polling mode.")
    p.add_argument("--interval", type=int, default=15, help="Polling interval in seconds for --watch.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    state_path = Path(args.state).expanduser().resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"source folder not found: {source_dir}", file=sys.stderr)
        return 2

    if args.watch:
        watch(source_dir, out_dir, state_path, args.interval)
    else:
        ingest_once(source_dir, out_dir, state_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
