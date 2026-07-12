import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.config_transaction import ConfigTransactionError, JsonConfigTransaction


class JsonConfigTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_dir = self.root / "configs"
        self.config_dir.mkdir()
        self.backup_dir = self.root / "backups"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_commit_creates_backup_diff_and_validated_json(self):
        config_path = self.config_dir / "one_dragon.json"
        original = {"enabled": False, "targets": ["old"]}
        config_path.write_text(json.dumps(original, ensure_ascii=False, indent=4), encoding="utf-8")

        transaction = JsonConfigTransaction(self.backup_dir)
        updated = {"enabled": True, "targets": ["new"]}
        transaction.stage_json(config_path, updated, "one_dragon")
        result = transaction.commit()

        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), updated)
        self.assertEqual(len(result.changed_files), 1)
        self.assertEqual(len(result.diff_files), 1)
        self.assertIn("-    \"enabled\": false", result.diff_files[0].read_text(encoding="utf-8"))
        self.assertIn("+    \"enabled\": true", result.diff_files[0].read_text(encoding="utf-8"))

        manifest = json.loads((result.backup_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "committed")
        original_backups = list((result.backup_dir / "originals").iterdir())
        self.assertEqual(len(original_backups), 1)
        self.assertEqual(json.loads(original_backups[0].read_text(encoding="utf-8")), original)

    def test_refuses_to_overwrite_file_changed_after_staging(self):
        config_path = self.config_dir / "config.json"
        config_path.write_text('{"value": 1}', encoding="utf-8")

        transaction = JsonConfigTransaction(self.backup_dir)
        transaction.stage_json(config_path, {"value": 2}, "config")
        config_path.write_text('{"value": 99}', encoding="utf-8")

        with self.assertRaises(ConfigTransactionError):
            transaction.commit()
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), {"value": 99})
        self.assertFalse(self.backup_dir.exists())

    def test_readback_failure_rolls_back_all_written_files(self):
        first_path = self.config_dir / "first.json"
        second_path = self.config_dir / "second.json"
        first_original = {"value": "first-original"}
        second_original = {"value": "second-original"}
        first_path.write_text(json.dumps(first_original), encoding="utf-8")
        second_path.write_text(json.dumps(second_original), encoding="utf-8")

        transaction = JsonConfigTransaction(self.backup_dir)
        transaction.stage_json(first_path, {"value": "first-updated"}, "first")
        transaction.stage_json(second_path, {"value": "second-updated"}, "second")

        original_validate = transaction._validate_readback
        validation_calls = 0

        def fail_on_second_readback(update):
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 2:
                raise ConfigTransactionError("simulated readback failure")
            original_validate(update)

        with patch.object(transaction, "_validate_readback", side_effect=fail_on_second_readback):
            with self.assertRaises(ConfigTransactionError):
                transaction.commit()

        self.assertEqual(json.loads(first_path.read_text(encoding="utf-8")), first_original)
        self.assertEqual(json.loads(second_path.read_text(encoding="utf-8")), second_original)
        manifests = list(self.backup_dir.glob("*/manifest.json"))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
