from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from pal_editor.backup_store import BackupSourceIdentity, BackupStore, PruneResult
from pal_editor.direct_save import (
    DirectSaveCoordinator,
    DirectSaveRequest,
    transaction_eligible_for_pruning,
)
from pal_editor.domain import PalTemplate
from pal_editor.safe_save import (
    RecoveryResult,
    SourceConfidence,
    TransactionState,
    fingerprint_file,
)


def _request(tmp_path: Path, template: PalTemplate | None = None) -> DirectSaveRequest:
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    return DirectSaveRequest(
        source_path=source,
        baseline=fingerprint_file(source),
        instance_id="test-instance",
        template=template or PalTemplate(species="PinkCat", nickname="Edited"),
        backup_store=BackupStore(tmp_path / "backups"),
    )


def test_direct_save_success_verifies_backup_and_prunes_only_after_completion(tmp_path: Path):
    request = _request(tmp_path)

    def serializer(source: Path, output: Path, instance_id: str, template: PalTemplate):
        output.write_bytes(b"edited")
        return SimpleNamespace(instance_id=instance_id, changed_fields=("NickName",))

    coordinator = DirectSaveCoordinator(
        serializer=serializer,
        output_validator=lambda path, result, request: True,
    )
    result = coordinator.run(request)

    assert result.success
    assert result.transaction is not None
    assert result.transaction.state is TransactionState.COMPLETED
    assert result.transaction.source_confidence is SourceConfidence.EDITED_SOURCE_VERIFIED
    assert result.refresh_allowed
    assert result.backup_path is not None and result.backup_path.exists()
    assert result.backup_record is not None
    assert result.pruning_result is not None
    assert request.source_path.read_bytes() == b"edited"


def test_direct_save_validation_failure_does_not_create_backup_or_replace_source(tmp_path: Path):
    request = _request(tmp_path, PalTemplate(species="", nickname="Invalid"))
    result = DirectSaveCoordinator().run(request)

    assert not result.success
    assert result.transaction is None
    assert result.validation_report is not None and not result.validation_report.valid
    assert result.primary_failure == "Validation failed"
    assert result.pruning_result is None
    assert request.source_path.read_bytes() == b"original"
    assert not (tmp_path / "backups").exists()


def test_direct_save_failure_preserves_backup_and_never_prunes(tmp_path: Path):
    request = _request(tmp_path)

    def serializer(source: Path, output: Path, instance_id: str, template: PalTemplate):
        raise RuntimeError("serializer failure")

    coordinator = DirectSaveCoordinator(
        serializer=serializer,
        output_validator=lambda path, result, request: True,
    )
    result = coordinator.run(request)

    assert not result.success
    assert result.transaction is not None
    assert result.pruning_result is None
    assert result.transaction.source_confidence is SourceConfidence.NOT_VERIFIED
    assert list((tmp_path / "backups").rglob("*.sav"))
    assert request.source_path.read_bytes() == b"original"


def test_direct_save_pruning_warning_does_not_turn_verified_save_into_failure(
    tmp_path: Path,
):
    request = _request(tmp_path)
    store = request.backup_store
    assert store is not None
    prune_calls: list[Path] = []

    def serializer(source: Path, output: Path, instance_id: str, template: PalTemplate):
        output.write_bytes(b"edited-with-retention-warning")
        return SimpleNamespace(instance_id=instance_id, changed_fields=("NickName",))

    def prune(source: Path) -> PruneResult:
        prune_calls.append(source)
        return PruneResult(
            identity=BackupSourceIdentity.from_path(source),
            retained=(),
            removed=(),
            warnings=("older backup could not be removed",),
        )

    store.prune_verified_backups = prune  # type: ignore[method-assign]
    coordinator = DirectSaveCoordinator(
        serializer=serializer,
        output_validator=lambda path, result, request: True,
    )
    result = coordinator.run(request)

    assert result.success
    assert result.transaction is not None
    assert result.transaction.state is TransactionState.COMPLETED
    assert result.source_confidence is SourceConfidence.EDITED_SOURCE_VERIFIED
    assert prune_calls == [request.source_path]
    assert result.pruning_warning == (
        "Backup retention warning: older backup could not be removed"
    )
    assert request.source_path.read_bytes() == b"edited-with-retention-warning"


@pytest.mark.parametrize(
    ("success", "state", "confidence", "expected"),
    [
        (True, TransactionState.COMPLETED, SourceConfidence.EDITED_SOURCE_VERIFIED, True),
        (False, TransactionState.COMPLETED, SourceConfidence.EDITED_SOURCE_VERIFIED, False),
        (True, TransactionState.FAILED, SourceConfidence.EDITED_SOURCE_VERIFIED, False),
        (True, TransactionState.COMPLETED, SourceConfidence.ORIGINAL_RESTORED, False),
        (True, TransactionState.COMPLETED, SourceConfidence.UNCERTAIN, False),
    ],
)
def test_pruning_gate_requires_verified_completed_success(
    success: bool,
    state: TransactionState,
    confidence: SourceConfidence,
    expected: bool,
):
    result = SimpleNamespace(success=success, state=state, source_confidence=confidence)
    assert transaction_eligible_for_pruning(result) is expected


def test_real_disposable_sav_round_trip_uses_production_parser_and_serializer(tmp_path: Path):
    source_text = os.environ.get("PALADMIN_REAL_SAV", "").strip()
    if not source_text:
        pytest.skip("Set PALADMIN_REAL_SAV to a disposable-test source save")
    source = Path(source_text)
    if not source.is_file():
        pytest.skip(f"PALADMIN_REAL_SAV does not point to a file: {source}")

    from pal_editor.__main__ import inspect
    from pal_editor.domain import PalTemplate

    disposable = tmp_path / "Level.sav"
    shutil.copyfile(source, disposable)
    original_hash = fingerprint_file(source).sha256
    report = inspect(disposable)
    record = report["pals"][0]
    template = PalTemplate.from_record(record, source_build=report["engine_version"])
    template.nickname = "Phase3 Real Gate"
    request = DirectSaveRequest(
        source_path=disposable,
        baseline=fingerprint_file(disposable),
        instance_id=str(record["instance_id"]),
        template=template,
        backup_store=BackupStore(tmp_path / "backups"),
    )

    result = DirectSaveCoordinator().run(request)

    assert result.success
    assert result.transaction is not None
    assert result.transaction.state is TransactionState.COMPLETED
    assert result.source_confidence is SourceConfidence.EDITED_SOURCE_VERIFIED
    reread = inspect(disposable)
    edited = next(pal for pal in reread["pals"] if str(pal["instance_id"]) == str(record["instance_id"]))
    assert edited["nickname"] == "Phase3 Real Gate"
    assert result.backup_path is not None and result.backup_path.is_file()
    assert fingerprint_file(source).sha256 == original_hash
