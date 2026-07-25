"""GUI-independent coordination for the v1.2b direct-Save workflow.

This module is the single production path that may combine the accepted
``SafeSaveTransaction`` primitive with the Phase 2 ``BackupStore``.  It does
not know about Qt, widgets, or navigation.  The GUI supplies a validated
``PalTemplate`` and consumes the structured result returned here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .__main__ import inspect
from .backup_store import BackupRecord, BackupStore, PruneResult
from .domain import PalTemplate
from .operations import BatchEdit, BatchEditResult, EditResult, edit_save_copy_batch
from .safe_save import (
    BackupProof,
    RecoveryResult,
    SafeSaveTransaction,
    SourceConfidence,
    SourceFingerprint,
    TransactionRequest,
    TransactionResult,
    TransactionState,
)
from .validation import ValidationReport, validate_template


Serializer = Callable[..., EditResult | BatchEditResult]
Inspector = Callable[[Path], dict]
OutputValidator = Callable[[Path, EditResult | None, "DirectSaveRequest"], object]


@dataclass(frozen=True)
class DirectSaveRequest:
    """All data required to replace one already-loaded source safely.

    ``instance_id`` and ``template`` remain as compatibility fields for the
    Phase 3 one-Pal API.  New callers should provide the immutable ``edits``
    tuple so the coordinator performs one transaction for the whole batch.
    """

    source_path: Path
    baseline: SourceFingerprint
    instance_id: str | None = None
    template: PalTemplate | None = None
    edits: tuple[BatchEdit, ...] = ()
    backup_store: BackupStore | None = None

    def normalized_edits(self) -> tuple[BatchEdit, ...]:
        if self.edits:
            return tuple(self.edits)
        if self.instance_id and self.template is not None:
            return (BatchEdit(str(self.instance_id), self.template),)
        raise ValueError("A direct-save request requires one or more identity-keyed edits")


@dataclass(frozen=True)
class DirectSaveResult:
    """A complete, UI-neutral account of a direct-Save attempt."""

    success: bool
    transaction: TransactionResult | None
    validation_report: ValidationReport | None
    backup_path: Path | None
    backup_record: BackupRecord | None
    pruning_result: PruneResult | None
    pruning_warning: str | None
    primary_failure: str | None
    cleanup_failure: str | None
    recovery_result: RecoveryResult
    source_confidence: SourceConfidence
    refresh_allowed: bool


def transaction_eligible_for_pruning(result: TransactionResult) -> bool:
    """Return whether retention may run after this exact transaction result."""

    return bool(
        result.success is True
        and result.state is TransactionState.COMPLETED
        and result.source_confidence is SourceConfidence.EDITED_SOURCE_VERIFIED
    )


class DirectSaveCoordinator:
    """Coordinate validation, backup-gated replacement, verification, and pruning."""

    def __init__(
        self,
        *,
        transaction_factory: Callable[[], SafeSaveTransaction] = SafeSaveTransaction,
        serializer: Serializer = edit_save_copy_batch,
        inspector: Inspector = inspect,
        output_validator: OutputValidator | None = None,
    ) -> None:
        self.transaction_factory = transaction_factory
        self.serializer = serializer
        self.inspector = inspector
        self.output_validator = output_validator

    def run(self, request: DirectSaveRequest) -> DirectSaveResult:
        """Run one direct save and return its outcome without touching the GUI."""

        backup_store = request.backup_store or BackupStore()
        try:
            edits = request.normalized_edits()
        except Exception as exc:
            return DirectSaveResult(
                success=False,
                transaction=None,
                validation_report=ValidationReport(),
                backup_path=None,
                backup_record=None,
                pruning_result=None,
                pruning_warning=None,
                primary_failure=str(exc),
                cleanup_failure=None,
                recovery_result=RecoveryResult.NOT_REQUIRED,
                source_confidence=SourceConfidence.NOT_VERIFIED,
                refresh_allowed=False,
            )
        report = validate_edit_batch(edits)
        if not report.valid:
            return DirectSaveResult(
                success=False,
                transaction=None,
                validation_report=report,
                backup_path=None,
                backup_record=None,
                pruning_result=None,
                pruning_warning=None,
                primary_failure="Validation failed",
                cleanup_failure=None,
                recovery_result=RecoveryResult.NOT_REQUIRED,
                source_confidence=SourceConfidence.NOT_VERIFIED,
                refresh_allowed=False,
            )

        edit_result: EditResult | BatchEditResult | None = None

        def serialize_output(temporary_path: Path) -> None:
            nonlocal edit_result
            # Reuse the existing parser and serializer.  No backup is created
            # here because SafeSaveTransaction obtains the verified proof.
            if request.edits or self.serializer is edit_save_copy_batch:
                edit_result = self.serializer(request.source_path, temporary_path, edits)
            else:
                # Preserve existing Phase 3 test and extension compatibility
                # for callers that still supply one legacy edit.
                edit_result = self.serializer(
                    request.source_path,
                    temporary_path,
                    edits[0].instance_id,
                    edits[0].template,
                )

        def validate_output(path: Path) -> object:
            if self.output_validator is not None:
                return self.output_validator(path, edit_result, request)
            return self._validate_serialized_output(path, edit_result, request)

        transaction_request = TransactionRequest(
            source_path=request.source_path,
            baseline=request.baseline,
            backup_provider=backup_store,
            serialize_output=serialize_output,
            validate_output=validate_output,
        )
        try:
            transaction = self.transaction_factory().run(transaction_request)
        except Exception as exc:  # Keep coordinator failures structured at the UI boundary.
            return DirectSaveResult(
                success=False,
                transaction=None,
                validation_report=report,
                backup_path=None,
                backup_record=None,
                pruning_result=None,
                pruning_warning=None,
                primary_failure=str(exc),
                cleanup_failure=None,
                recovery_result=RecoveryResult.UNCERTAIN,
                source_confidence=SourceConfidence.UNCERTAIN,
                refresh_allowed=False,
            )

        proof = transaction.backup_proof
        backup_path = proof.backup_path if proof is not None else None
        backup_record = self._backup_record(backup_store, proof)
        pruning_result: PruneResult | None = None
        pruning_warning: str | None = None
        if transaction_eligible_for_pruning(transaction):
            try:
                pruning_result = backup_store.prune_verified_backups(request.source_path)
                warnings = list(pruning_result.warnings)
                if pruning_result.cleanup_error:
                    warnings.append(pruning_result.cleanup_error)
                if warnings:
                    pruning_warning = "Backup retention warning: " + "; ".join(warnings)
            except Exception as exc:  # Retention warning must not undo a verified save.
                pruning_warning = f"Backup retention warning: {exc}"

        return DirectSaveResult(
            success=transaction.success,
            transaction=transaction,
            validation_report=report,
            backup_path=backup_path,
            backup_record=backup_record,
            pruning_result=pruning_result,
            pruning_warning=pruning_warning,
            primary_failure=transaction.error_message,
            cleanup_failure=transaction.cleanup_error,
            recovery_result=transaction.recovery_result,
            source_confidence=transaction.source_confidence,
            refresh_allowed=transaction_eligible_for_pruning(transaction),
        )

    def _validate_serialized_output(
        self,
        path: Path,
        edit_result: EditResult | BatchEditResult | None,
        request: DirectSaveRequest,
    ) -> dict:
        report = self.inspector(path)
        if not isinstance(report, dict) or not isinstance(report.get("pals"), list):
            raise ValueError("The serialized save did not produce a readable Palworld report")
        if edit_result is None:
            raise ValueError("The serializer returned no edit result")
        results = (
            edit_result.results
            if isinstance(edit_result, BatchEditResult)
            else (edit_result,)
        )
        edits = request.normalized_edits()
        if len(results) != len(edits):
            raise ValueError("The serializer did not return one result for every edited Pal")
        mismatches: list[str] = []
        for edit, result in zip(edits, results):
            record = next(
                (
                    pal
                    for pal in report["pals"]
                    if str(pal.get("instance_id")) == str(edit.instance_id)
                ),
                None,
            )
            if record is None:
                raise ValueError(
                    f"The serialized save did not retain edited Pal {edit.instance_id}"
                )
            checks = {
                "CharacterID": ("species", edit.template.species),
                "NickName": ("nickname", edit.template.nickname),
                "Gender": ("gender", edit.template.gender),
                "Level": ("level", edit.template.level),
                "Rank": ("rank", edit.template.rank),
                "Exp": ("xp", edit.template.xp),
                "Hp": ("hp", edit.template.hp),
                "Talent_HP": ("iv_hp", edit.template.iv_hp),
                "Talent_Shot": ("iv_attack", edit.template.iv_attack),
                "Talent_Defense": ("iv_defense", edit.template.iv_defense),
                "EquipWaza": ("active_skills", edit.template.active_skills),
                "PassiveSkillList": ("passives", edit.template.passive_skills),
            }
            for field_name in result.changed_fields:
                if field_name not in checks:
                    continue
                record_name, expected = checks[field_name]
                actual = record.get(record_name)
                if actual != expected:
                    mismatches.append(
                        f"{edit.instance_id} {field_name}: expected {expected!r}, reloaded {actual!r}"
                    )
        if mismatches:
            raise ValueError("Serialized output verification failed: " + "; ".join(mismatches))
        return report

    @staticmethod
    def _backup_record(store: BackupStore, proof: BackupProof | None) -> BackupRecord | None:
        if proof is None:
            return None
        metadata_path = proof.metadata.get("metadata_path")
        if not metadata_path:
            return None
        try:
            return store.verify_record(metadata_path)
        except Exception:
            return None


def validate_edit_batch(edits: tuple[BatchEdit, ...] | list[BatchEdit]) -> ValidationReport:
    """Validate every pending template and retain Pal context on each issue."""

    report = ValidationReport()
    normalized = tuple(edits)
    identities = [str(edit.instance_id).strip() for edit in normalized]
    if any(not identity for identity in identities):
        report.add("error", "missing_identity", "A pending Pal is missing its stable identity", "instance_id")
    if len(set(identities)) != len(identities):
        report.add("error", "duplicate_identity", "The pending batch contains duplicate Pal identities", "instance_id")
    for edit in normalized:
        identity = str(edit.instance_id).strip() or "unknown identity"
        context = edit.display_context or identity
        template_report = validate_template(edit.template, mode=edit.template.validation_mode)  # type: ignore[arg-type]
        for issue in template_report.issues:
            prefix = f"{context} ({identity}): " if context != identity else f"{identity}: "
            report.add(issue.severity, issue.code, prefix + issue.message, issue.field)
    return report
