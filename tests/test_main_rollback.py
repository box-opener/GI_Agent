import unittest
from pathlib import Path
from unittest.mock import patch

import main
from skills.config_recovery import RecoveryResult


class MainRollbackCommandTests(unittest.TestCase):
    @patch("main.input", return_value="RESTORE")
    @patch("main.restore_transaction")
    @patch("main.load_transaction")
    @patch("main.list_transactions")
    def test_rollback_with_id_restores_without_llm(
        self, list_transactions, load_transaction, restore_transaction, _input
    ):
        transaction_id = "20260712-120000-000000-abcd1234"
        list_transactions.return_value = [
            {"id": transaction_id, "status": "committed", "updates": 1, "created_at": "2026-07-12T12:00:00"}
        ]
        load_transaction.return_value = (
            Path("C:/backups") / transaction_id,
            {"updates": [{"label": "one_dragon", "path": "C:/BetterGI/User/OneDragon/test.json"}]},
        )
        restore_transaction.return_value = RecoveryResult(
            backup_dir=Path("C:/backups/recovery-test"),
            restored_files=(Path("C:/BetterGI/User/OneDragon/test.json"),),
        )

        handled = main.handle_rollback_command(f"rollback {transaction_id}")

        self.assertTrue(handled)
        restore_transaction.assert_called_once_with(main.config.BGI_BACKUP_DIR, transaction_id)

    @patch("main.list_transactions", return_value=[])
    def test_rollback_without_history_is_handled(self, _list_transactions):
        self.assertTrue(main.handle_rollback_command("rollback"))

    def test_non_rollback_input_is_not_intercepted(self):
        self.assertFalse(main.handle_rollback_command("帮我刷天赋材料"))


if __name__ == "__main__":
    unittest.main()
