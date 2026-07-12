import json
import tempfile
import unittest
from pathlib import Path

from skills.config_recovery import list_transactions, restore_transaction
from skills.config_transaction import JsonConfigTransaction


class ConfigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_dir = self.root / "configs"
        self.config_dir.mkdir()
        self.backup_dir = self.root / "backups"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manual_restore_recovers_original_and_can_be_reversed(self):
        config_path = self.config_dir / "config.json"
        original_bytes = b'{"mode":"original"}'
        config_path.write_bytes(original_bytes)

        transaction = JsonConfigTransaction(self.backup_dir)
        transaction.stage_json(config_path, {"mode": "changed"}, "config")
        committed = transaction.commit()
        changed_bytes = config_path.read_bytes()

        restored = restore_transaction(self.backup_dir, committed.backup_dir.name)
        self.assertEqual(config_path.read_bytes(), original_bytes)
        recovery_manifest = json.loads((restored.backup_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(recovery_manifest["status"], "manual_restore_committed")

        restore_transaction(self.backup_dir, restored.backup_dir.name)
        self.assertEqual(config_path.read_bytes(), changed_bytes)

    def test_manual_restore_removes_file_created_by_transaction(self):
        config_path = self.config_dir / "created.json"
        transaction = JsonConfigTransaction(self.backup_dir)
        transaction.stage_json(config_path, {"created": True}, "created")
        committed = transaction.commit()
        self.assertTrue(config_path.exists())

        restore_transaction(self.backup_dir, committed.backup_dir.name)
        self.assertFalse(config_path.exists())

    def test_list_transactions_includes_commits_and_manual_recoveries(self):
        config_path = self.config_dir / "config.json"
        config_path.write_text('{"value": 1}', encoding="utf-8")
        transaction = JsonConfigTransaction(self.backup_dir)
        transaction.stage_json(config_path, {"value": 2}, "config")
        committed = transaction.commit()
        restore_transaction(self.backup_dir, committed.backup_dir.name)

        statuses = {item["status"] for item in list_transactions(self.backup_dir)}
        self.assertIn("committed", statuses)
        self.assertIn("manual_restore_committed", statuses)


if __name__ == "__main__":
    unittest.main()
