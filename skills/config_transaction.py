"""Atomic, auditable updates for BetterGI JSON configuration files."""

from __future__ import annotations

import difflib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class ConfigTransactionError(RuntimeError):
    """Raised when a configuration transaction cannot be safely completed."""


@dataclass
class _StagedJsonUpdate:
    label: str
    path: Path
    before_bytes: bytes | None
    after_data: Any
    after_bytes: bytes
    diff_text: str


@dataclass(frozen=True)
class TransactionResult:
    backup_dir: Path
    changed_files: tuple[Path, ...]
    diff_files: tuple[Path, ...]


class JsonConfigTransaction:
    """Stage JSON changes, then commit all of them with backup and rollback support."""

    def __init__(self, backup_root: str | Path) -> None:
        self.backup_root = Path(backup_root)
        self._updates: list[_StagedJsonUpdate] = []

    def stage_json(self, path: str | Path, data: Any, label: str) -> None:
        """Capture a JSON file's current bytes and stage its replacement content."""
        target = Path(path)
        if not target.parent.is_dir():
            raise ConfigTransactionError(f"Configuration directory does not exist: {target.parent}")
        if any(update.path == target for update in self._updates):
            raise ConfigTransactionError(f"Configuration file staged twice: {target}")

        before_bytes = target.read_bytes() if target.exists() else None
        if before_bytes is not None:
            try:
                json.loads(before_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfigTransactionError(f"Existing JSON is invalid: {target}") from exc

        try:
            after_text = json.dumps(data, ensure_ascii=False, indent=4) + "\n"
        except (TypeError, ValueError) as exc:
            raise ConfigTransactionError(f"Updated value cannot be serialized as JSON: {target}") from exc

        before_text = "" if before_bytes is None else before_bytes.decode("utf-8")
        if before_text and not before_text.endswith("\n"):
            before_text += "\n"
        diff_text = "".join(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"{target.name} (before)",
                tofile=f"{target.name} (after)",
            )
        )
        self._updates.append(
            _StagedJsonUpdate(
                label=label,
                path=target,
                before_bytes=before_bytes,
                after_data=data,
                after_bytes=after_text.encode("utf-8"),
                diff_text=diff_text,
            )
        )

    def commit(self) -> TransactionResult:
        """Write every staged update atomically, or restore all files on failure."""
        if not self._updates:
            raise ConfigTransactionError("No JSON configuration updates were staged")

        self._verify_sources_unchanged()
        backup_dir = self._create_backup_dir()
        self._write_audit_artifacts(backup_dir)

        applied: list[_StagedJsonUpdate] = []
        try:
            for update in self._updates:
                # Include the file before writing so a failed readback is rolled back too.
                applied.append(update)
                self._write_bytes_atomically(update.path, update.after_bytes)
                self._validate_readback(update)
        except Exception as exc:
            rollback_errors = self._rollback(applied)
            self._write_manifest(backup_dir, "rolled_back", str(exc), rollback_errors)
            detail = f"Configuration transaction failed and was rolled back: {exc}"
            if rollback_errors:
                detail += f"; rollback errors: {'; '.join(rollback_errors)}"
            raise ConfigTransactionError(detail) from exc

        self._write_manifest(backup_dir, "committed", None, [])
        diff_files = tuple(sorted((backup_dir / "diffs").glob("*.diff")))
        return TransactionResult(
            backup_dir=backup_dir,
            changed_files=tuple(update.path for update in self._updates),
            diff_files=diff_files,
        )

    def _verify_sources_unchanged(self) -> None:
        for update in self._updates:
            current_bytes = update.path.read_bytes() if update.path.exists() else None
            if current_bytes != update.before_bytes:
                raise ConfigTransactionError(
                    f"Configuration changed after staging; refusing to overwrite: {update.path}"
                )

    def _create_backup_dir(self) -> Path:
        transaction_name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8]
        backup_dir = self.backup_root / transaction_name
        (backup_dir / "originals").mkdir(parents=True, exist_ok=False)
        (backup_dir / "diffs").mkdir()
        return backup_dir

    def _write_audit_artifacts(self, backup_dir: Path) -> None:
        originals_dir = backup_dir / "originals"
        diffs_dir = backup_dir / "diffs"
        for index, update in enumerate(self._updates, start=1):
            prefix = f"{index:02d}_{self._safe_label(update.label)}_{update.path.name}"
            original_path = originals_dir / prefix
            if update.before_bytes is None:
                original_path.with_suffix(original_path.suffix + ".missing").write_text(
                    "File did not exist before this transaction.\n", encoding="utf-8"
                )
            else:
                original_path.write_bytes(update.before_bytes)
            (diffs_dir / f"{prefix}.diff").write_text(update.diff_text, encoding="utf-8")

        self._write_manifest(backup_dir, "staged", None, [])

    def _write_manifest(
        self, backup_dir: Path, status: str, error: str | None, rollback_errors: list[str]
    ) -> None:
        manifest = {
            "status": status,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updates": [
                {
                    "label": update.label,
                    "path": str(update.path),
                    "existed_before": update.before_bytes is not None,
                    "diff": str(Path("diffs") / f"{index:02d}_{self._safe_label(update.label)}_{update.path.name}.diff"),
                }
                for index, update in enumerate(self._updates, start=1)
            ],
            "error": error,
            "rollback_errors": rollback_errors,
        }
        self._write_bytes_atomically(
            backup_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        )

    def _validate_readback(self, update: _StagedJsonUpdate) -> None:
        try:
            actual_data = json.loads(update.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigTransactionError(f"Readback validation failed: {update.path}") from exc
        if actual_data != update.after_data:
            raise ConfigTransactionError(f"Readback data does not match staged data: {update.path}")

    def _rollback(self, applied: list[_StagedJsonUpdate]) -> list[str]:
        errors: list[str] = []
        for update in reversed(applied):
            try:
                if update.before_bytes is None:
                    update.path.unlink(missing_ok=True)
                else:
                    self._write_bytes_atomically(update.path, update.before_bytes)
            except OSError as exc:
                errors.append(f"{update.path}: {exc}")
        return errors

    @staticmethod
    def _write_bytes_atomically(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp_file:
                temp_path = temp_file.name
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _safe_label(label: str) -> str:
        return "".join(char if char.isalnum() or char in "-_" else "_" for char in label)
