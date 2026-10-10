"""Private, machine-local inputs for the experiment scripts.

The feasibility experiments were run against one real business and a few of its
reviewers. Which business, which saved response files, and which reviewer were
used is deliberately not part of this repository: saved-response filenames embed
an unkeyed hash of the request (including contributor IDs), and a pinned
reviewer position points at one person.

Those values live in ``local_inputs.json`` at the project root, which is
git-ignored. Copy ``local_inputs.example.json`` (fictional values) and fill in
your own. Every value is validated before use, and a saved-response name can
only refer to a file directly inside ``data/raw/``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "local_inputs.json"
EXAMPLE_NAME = "local_inputs.example.json"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

RAW_FILE_KEYS = (
    "call1_search",
    "call2_reviews_page1",
    "call3_contributor_history",
    "call4_reviews_page2",
    "call5_contributor_history",
    "call6_business_reviews",
)
_TEXT = {"search_query": (3, 120), "seed_business_name": (1, 200)}
_INTEGERS = {"call5_selected_position": (1, 50), "call5_expected_reported_reviews": (1, 100000)}
_DATA_ID = re.compile(r"0x[0-9a-f]{1,32}:0x[0-9a-f]{1,32}")
_RAW_NAME = re.compile(r"[a-z0-9_]{1,80}\.json")


class LocalInputError(Exception):
    """local_inputs.json is missing or invalid. Messages never quote its values."""


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LocalInputError(
            f"{path.name} not found. Copy {EXAMPLE_NAME} to {path.name} in the project root "
            "and fill in your own values."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalInputError(f"{path.name} could not be read as JSON ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise LocalInputError(f"{path.name} must contain a JSON object")
    return data


def text(name: str, path: Path = CONFIG_PATH) -> str:
    """A validated text setting: ``search_query``, ``seed_business_name``, or ``seed_data_id``."""
    value = _load(path).get(name)
    if name == "seed_data_id":
        if not isinstance(value, str) or not _DATA_ID.fullmatch(value):
            raise LocalInputError("'seed_data_id' must look like 0x<hex>:0x<hex>")
        return value
    if name not in _TEXT:
        raise LocalInputError(f"Unknown text setting: {name}")
    low, high = _TEXT[name]
    if not isinstance(value, str) or not low <= len(value.strip()) <= high or re.search(r"[\x00-\x1f\x7f]", value):
        raise LocalInputError(f"'{name}' must be text of {low} to {high} characters")
    return value.strip()


def integer(name: str, path: Path = CONFIG_PATH) -> int:
    """A validated whole-number setting."""
    if name not in _INTEGERS:
        raise LocalInputError(f"Unknown number setting: {name}")
    value = _load(path).get(name)
    low, high = _INTEGERS[name]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise LocalInputError(f"'{name}' must be a whole number from {low} to {high}")
    return value


def raw_file(name: str, path: Path = CONFIG_PATH, raw_dir: Path = RAW_DIR) -> Path:
    """Path of one saved response, restricted to a plain filename inside ``data/raw/``."""
    if name not in RAW_FILE_KEYS:
        raise LocalInputError(f"Unknown saved-response setting: {name}")
    files = _load(path).get("raw_files")
    value = files.get(name) if isinstance(files, dict) else None
    if not isinstance(value, str) or not _RAW_NAME.fullmatch(value):
        raise LocalInputError(f"'raw_files.{name}' must be a plain .json filename inside data/raw/")
    return raw_dir / value
