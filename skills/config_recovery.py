"""Manual recovery commands for BetterGI configuration transactions."""

from __future__ import annotations

import argparse
import difflib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from skills.config_transaction import ConfigTransactionError, JsonConfigTransaction


RECOVERABLE_STATUSES = {"committed", "manual_restore_committed"}


@dataclass(frozen=True)
class RecoveryResult:
    backup_dir: Path
    restored_files: tuple[Path, ...]


@dataclass
class _RestoreEntry:
    label: str
    path: Path
    before_bytes: bytes | None
    restore_bytes: bytes | None
    existed_before: bool


def list_transactions(backup_root: str | Path) -> list[dict[str, Any]]:
    """Return transaction summaries, newest first, while skipping unrelated directories."""
    root = Path(backup_root)
    if not root.is_dir():
        return []

    transactions = []
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            transactions.append(
                {
                    "id": manifest_path.parent.name,
                    "status": manifest.get("status", "unknown"),
                    "created_at": manifest.get("created_at", "unknown"),
                    "updates": len(manifest.get("updates", [])),
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(transactions, key=lambda item: item["id"], reverse=True)


def load_transaction(backup_root: str | Path, transaction_id: str) -> tuple[Path, dict[str, Any]]:
    """Load one transaction while preventing traversal outside the configured backup root."""
    root = Path(backup_root).resolve()
    if not transaction_id or Path(transaction_id).name != transaction_id:
        raise ConfigTransactionError("Invalid transaction ID")

    transaction_dir = (root / transaction_id).resolve()
    if transaction_dir.parent != root or not transaction_dir.is_dir():
        raise ConfigTransactionError(f"Transaction not found: {transaction_id}")

    manifest_path = transaction_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigTransactionError(f"Transaction manifest is invalid: {manifest_path}") from exc
    return transaction_dir, manifest


def restore_transaction(backup_root: str | Path, transaction_id: str) -> RecoveryResult:
    """Restore all files to their state before a committed transaction.

    A new transaction-style backup of the *current* state is created first, so the
    manual recovery itself can be reversed by restoring that new recovery record.
    """
    root = Path(backup_root)
    source_dir, manifest = load_transaction(root, transaction_id)
    if manifest.get("status") not in RECOVERABLE_STATUSES:
        raise ConfigTransactionError(
            f"Transaction {transaction_id} has status '{manifest.get('status')}' and cannot be restored"
        )

    entries = _build_restore_entries(source_dir, manifest)
    recovery_dir = _create_recovery_dir(root)
    _write_current_snapshot(recovery_dir, entries, source_dir)

    applied: list[_RestoreEntry] = []
    try:
        for entry in entries:
            applied.append(entry)
            _restore_bytes(entry.path, entry.restore_bytes)
            _validate_bytes(entry.path, entry.restore_bytes)
    except Exception as exc:
        rollback_errors = _rollback_recovery(applied)
        _write_recovery_manifest(recovery_dir, entries, "manual_restore_rolled_back", str(exc), rollback_errors, source_dir)
        detail = f"Manual restore failed and was rolled back: {exc}"
        if rollback_errors:
            detail += f"; rollback errors: {'; '.join(rollback_errors)}"
        raise ConfigTransactionError(detail) from exc

    _write_recovery_manifest(recovery_dir, entries, "manual_restore_committed", None, [], source_dir)
    return RecoveryResult(recovery_dir, tuple(entry.path for entry in entries))


def _build_restore_entries(source_dir: Path, manifest: dict[str, Any]) -> list[_RestoreEntry]:
    updates = manifest.get("updates")
    if not isinstance(updates, list) or not updates:
        raise ConfigTransactionError("Transaction has no recoverable updates")

    entries = []
    for index, update in enumerate(updates, start=1):
        if not isinstance(update, dict) or not isinstance(update.get("path"), str):
            raise ConfigTransactionError("Transaction manifest contains an invalid update")
        label = str(update.get("label", f"update_{index}"))
        path = Path(update["path"])
        if not path.is_absolute():
            raise ConfigTransactionError(f"Transaction contains a non-absolute path: {path}")

        original_path = source_dir / "originals" / _backup_filename(index, label, path)
        existed_before = bool(update.get("existed_before"))
        if existed_before:
            try:
                restore_bytes = original_path.read_bytes()
                json.loads(restore_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfigTransactionError(f"Original backup is invalid: {original_path}") from exc
        else:
            marker = original_path.with_suffix(original_path.suffix + ".missing")
            if not marker.is_file():
                raise ConfigTransactionError(f"Missing-file marker is absent: {marker}")
            restore_bytes = None

        before_bytes = path.read_bytes() if path.exists() else None
        entries.append(_RestoreEntry(label, path, before_bytes, restore_bytes, existed_before))
    return entries


def _create_recovery_dir(root: Path) -> Path:
    name = "recovery-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8]
    recovery_dir = root / name
    (recovery_dir / "originals").mkdir(parents=True, exist_ok=False)
    (recovery_dir / "diffs").mkdir()
    return recovery_dir


def _write_current_snapshot(recovery_dir: Path, entries: list[_RestoreEntry], source_dir: Path) -> None:
    for index, entry in enumerate(entries, start=1):
        snapshot_path = recovery_dir / "originals" / _backup_filename(index, entry.label, entry.path)
        if entry.before_bytes is None:
            snapshot_path.with_suffix(snapshot_path.suffix + ".missing").write_text(
                "File did not exist before this manual recovery.\n", encoding="utf-8"
            )
        else:
            snapshot_path.write_bytes(entry.before_bytes)

        before_text = "" if entry.before_bytes is None else _with_newline(entry.before_bytes.decode("utf-8"))
        after_text = "" if entry.restore_bytes is None else _with_newline(entry.restore_bytes.decode("utf-8"))
        diff = "".join(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"{entry.path.name} (before manual restore)",
                tofile=f"{entry.path.name} (restored)",
            )
        )
        (recovery_dir / "diffs" / f"{_backup_filename(index, entry.label, entry.path)}.diff").write_text(
            diff, encoding="utf-8"
        )

    _write_recovery_manifest(recovery_dir, entries, "manual_restore_staged", None, [], source_dir)


def _write_recovery_manifest(
    recovery_dir: Path,
    entries: list[_RestoreEntry],
    status: str,
    error: str | None,
    rollback_errors: list[str],
    source_dir: Path,
) -> None:
    manifest = {
        "status": status,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "restored_from": str(source_dir),
        "updates": [
            {
                "label": entry.label,
                "path": str(entry.path),
                "existed_before": entry.before_bytes is not None,
                "diff": str(Path("diffs") / f"{_backup_filename(index, entry.label, entry.path)}.diff"),
            }
            for index, entry in enumerate(entries, start=1)
        ],
        "error": error,
        "rollback_errors": rollback_errors,
    }
    JsonConfigTransaction._write_bytes_atomically(
        recovery_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )


def _restore_bytes(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        JsonConfigTransaction._write_bytes_atomically(path, content)


def _validate_bytes(path: Path, expected: bytes | None) -> None:
    if expected is None:
        if path.exists():
            raise ConfigTransactionError(f"Deleted file still exists after restore: {path}")
        return
    actual = path.read_bytes()
    if actual != expected:
        raise ConfigTransactionError(f"Readback bytes do not match backup: {path}")
    try:
        json.loads(actual.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigTransactionError(f"Restored content is not valid JSON: {path}") from exc


def _rollback_recovery(applied: list[_RestoreEntry]) -> list[str]:
    errors = []
    for entry in reversed(applied):
        try:
            _restore_bytes(entry.path, entry.before_bytes)
        except OSError as exc:
            errors.append(f"{entry.path}: {exc}")
    return errors


def _backup_filename(index: int, label: str, path: Path) -> str:
    return f"{index:02d}_{JsonConfigTransaction._safe_label(label)}_{path.name}"


def _with_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and manually restore BetterGI configuration transactions.")
    parser.add_argument("--backup-root", default=config.BGI_BACKUP_DIR, help="Backup root directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List configuration transactions")
    show_parser = subparsers.add_parser("show", help="Show one transaction")
    show_parser.add_argument("transaction_id")
    restore_parser = subparsers.add_parser("restore", help="Restore files to before one transaction")
    restore_parser.add_argument("transaction_id")
    restore_parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")
    args = parser.parse_args()

    if args.command == "list":
        for item in list_transactions(args.backup_root):
            print(f"{item['id']}  {item['status']}  files={item['updates']}  {item['created_at']}")
        return

    transaction_dir, manifest = load_transaction(args.backup_root, args.transaction_id)
    if args.command == "show":
        print(json.dumps({"id": transaction_dir.name, **manifest}, ensure_ascii=False, indent=2))
        return

    if not args.yes:
        answer = input(
            f"This will overwrite {len(manifest.get('updates', []))} file(s) with the state before "
            f"{transaction_dir.name}. Type RESTORE to continue: "
        )
        if answer != "RESTORE":
            print("Manual restore cancelled.")
            return
    result = restore_transaction(args.backup_root, args.transaction_id)
    print(f"Manual restore completed: {', '.join(path.name for path in result.restored_files)}")
    print(f"Recovery backup and Diff: {result.backup_dir}")


if __name__ == "__main__":
    main()
