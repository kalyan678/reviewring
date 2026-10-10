"""Offline tests for reviewring.local_inputs and the fictional example file.

Every value used here is fictional. Nothing touches the network.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reviewring import local_inputs  # noqa: E402
from reviewring.local_inputs import LocalInputError, integer, raw_file, text  # noqa: E402

EXAMPLE = PROJECT_ROOT / "local_inputs.example.json"


class LocalInputsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "local_inputs.json"
        self.raw_dir = Path(self._tmp.name) / "raw"
        self.config = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, **changes: object) -> None:
        self.path.write_text(json.dumps({**self.config, **changes}), encoding="utf-8")

    def test_example_file_is_valid_and_fictional(self):
        self.write()
        self.assertEqual(text("search_query", self.path), "bakeries in Exampleville")
        self.assertEqual(text("seed_data_id", self.path), "0x0000000000000001:0x0000000000000002")
        self.assertEqual(integer("call5_selected_position", self.path), 2)
        for key in local_inputs.RAW_FILE_KEYS:
            path = raw_file(key, self.path, self.raw_dir)
            self.assertEqual(path.parent, self.raw_dir)
            self.assertRegex(path.name, r"_0{15}\d\.json$")

    def test_the_real_file_is_git_ignored_and_absent_from_the_repository(self):
        self.assertIn("local_inputs.json", (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").split())
        self.assertFalse((PROJECT_ROOT / "local_inputs.json").exists())

    def test_missing_file_explains_what_to_do(self):
        with self.assertRaisesRegex(LocalInputError, "Copy local_inputs.example.json"):
            text("search_query", self.path)

    def test_unreadable_file_fails_without_quoting_it(self):
        for content in ("{not json", "[]"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(LocalInputError) as ctx:
                    text("search_query", self.path)
                self.assertNotIn("not json", str(ctx.exception))

    def test_saved_response_names_cannot_leave_the_raw_directory(self):
        for name in ("../secret.json", "..\\secret.json", "/etc/passwd", "C:/x.json", "sub/x.json", "x.txt", "", "X.JSON", 5, None):
            with self.subTest(name=name):
                self.write(raw_files={**self.config["raw_files"], "call2_reviews_page1": name})
                with self.assertRaisesRegex(LocalInputError, "plain .json filename") as ctx:
                    raw_file("call2_reviews_page1", self.path, self.raw_dir)
                if isinstance(name, str) and name:
                    self.assertNotIn(name, str(ctx.exception))
        self.write(raw_files="not-an-object")
        with self.assertRaises(LocalInputError):
            raw_file("call2_reviews_page1", self.path, self.raw_dir)

    def test_values_are_validated_and_never_echoed(self):
        cases = [
            (text, "seed_data_id", "not-an-id"),
            (text, "seed_data_id", 12345),
            (text, "search_query", "ab"),
            (text, "search_query", "x" * 121),
            (text, "search_query", "line\nbreak"),
            (text, "seed_business_name", ""),
            (integer, "call5_selected_position", 0),
            (integer, "call5_selected_position", 51),
            (integer, "call5_selected_position", "2"),
            (integer, "call5_selected_position", True),
            (integer, "call5_expected_reported_reviews", -1),
        ]
        for read, key, value in cases:
            with self.subTest(key=key, value=value):
                self.write(**{key: value})
                with self.assertRaises(LocalInputError) as ctx:
                    read(key, self.path)
                if isinstance(value, str) and len(value) > 3:
                    self.assertNotIn(value, str(ctx.exception))

    def test_unknown_settings_are_rejected(self):
        self.write()
        with self.assertRaises(LocalInputError):
            text("api_key", self.path)
        with self.assertRaises(LocalInputError):
            integer("search_query", self.path)
        with self.assertRaises(LocalInputError):
            raw_file("call9", self.path, self.raw_dir)


class ExperimentScriptsNeedLocalInputsTests(unittest.TestCase):
    """Without local_inputs.json the scripts stop before any request."""

    def test_scripts_hold_no_private_input(self):
        for script in sorted((PROJECT_ROOT / "experiments").glob("*.py")):
            with self.subTest(script=script.name):
                source = script.read_text(encoding="utf-8")
                self.assertNotRegex(source, r"google_maps[a-z_]*_[0-9a-f]{16}\.json")
                self.assertNotRegex(source, r"0x[0-9a-f]{8,}:0x[0-9a-f]{8,}")
                self.assertIn("local_inputs", source)


if __name__ == "__main__":
    unittest.main()
