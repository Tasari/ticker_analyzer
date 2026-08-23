from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ticker_analyzer.access_control import (
    AccessConfigError,
    create_access_config,
    load_access_config,
    verify_password,
    write_access_config,
)


class AccessControlTest(unittest.TestCase):
    def test_password_is_hashed_and_verified_without_plaintext_storage(self):
        password = "a-correct-test-password"
        payload = create_access_config(password, salt=b"0123456789abcdef")

        self.assertNotIn(password, json.dumps(payload))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_access_config(path)

        self.assertTrue(verify_password(password, config))
        self.assertFalse(verify_password("an-incorrect-password", config))

    def test_writer_replaces_config_and_rejects_short_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            write_access_config("first-valid-password", path)
            first = path.read_text(encoding="utf-8")
            write_access_config("second-valid-password", path)
            second = path.read_text(encoding="utf-8")

            self.assertNotEqual(first, second)
            self.assertTrue(verify_password("second-valid-password", load_access_config(path)))
        with self.assertRaisesRegex(ValueError, "at least 12"):
            create_access_config("too-short")

    def test_loader_fails_closed_for_missing_or_unsafe_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            with self.assertRaisesRegex(AccessConfigError, "could not be loaded"):
                load_access_config(path)
            path.write_text('{"version": 1, "algorithm": "plain"}', encoding="utf-8")
            with self.assertRaisesRegex(AccessConfigError, "Unsupported"):
                load_access_config(path)


if __name__ == "__main__":
    unittest.main()
