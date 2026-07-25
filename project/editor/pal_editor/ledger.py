"""UI-neutral draft and save-operation ledger.

The ledger records a proposed save edit without knowing anything about the UI
or the save serializer.  It deliberately treats the source save as read-only:
the only filesystem write it performs is creating a new, uniquely named backup.
"""

from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


class BackupPolicy(str, Enum):
    """When the source should be copied before an operation."""

    ALWAYS = "always"
    ASK = "ask"
    OFF = "off"


# A short, sortable timestamp with microseconds to make names useful even
# when several operations are prepared during one second.
BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S-%f"


class LedgerError(ValueError):
    """Raised when a ledger operation would violate its safety rules."""


class BackupApprovalRequired(LedgerError):
    """Raised when the ``ask`` policy has not received explicit approval."""


@dataclass(frozen=True)
class FieldChange:
    """One changed field in a draft."""

    name: str
    before: Any
    after: Any

    @property
    def field(self) -> str:
        """Compatibility alias for callers that call the name ``field``."""

        return self.name


@dataclass(frozen=True)
class DraftEntry:
    """One identity-keyed Pal draft within an editing session.

    Only changed fields are retained.  The immutable source template remains
    on ``PalInstance``; this keeps the aggregate ledger small while allowing
    several Pals to be pending at once.
    """

    instance_id: str
    before_fields: dict[str, Any]
    after_fields: dict[str, Any]
    source_index: int | None = None
    display_context: str = ""

    @property
    def changed_fields(self) -> tuple[str, ...]:
        names = list(dict.fromkeys((*self.before_fields.keys(), *self.after_fields.keys())))
        return tuple(
            name for name in names
            if self.before_fields.get(name) != self.after_fields.get(name)
            or name not in self.before_fields
            or name not in self.after_fields
        )

    @property
    def changes(self) -> tuple[FieldChange, ...]:
        missing = object()
        return tuple(
            FieldChange(
                name,
                copy.deepcopy(self.before_fields.get(name, missing)),
                copy.deepcopy(self.after_fields.get(name, missing)),
            )
            for name in self.changed_fields
        )


def _normalise_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _normalise_policy(value: BackupPolicy | str) -> BackupPolicy:
    if isinstance(value, BackupPolicy):
        return value
    try:
        return BackupPolicy(str(value).lower())
    except ValueError as exc:
        choices = ", ".join(policy.value for policy in BackupPolicy)
        raise LedgerError(f"Unknown backup policy {value!r}; choose {choices}") from exc


def timestamped_backup_name(
    source_path: str | Path,
    timestamp: datetime | None = None,
) -> str:
    """Return a timestamped backup filename for ``source_path``.

    The original suffix is retained and ``.bak`` is appended, so a source
    named ``Level.sav`` becomes ``Level.backup-YYYYMMDD-HHMMSS-ffffff.sav.bak``.
    """

    source = Path(source_path)
    stamp = timestamp or datetime.now()
    return f"{source.stem}.backup-{stamp.strftime(BACKUP_TIMESTAMP_FORMAT)}{source.suffix}.bak"


def timestamped_backup_path(
    source_path: str | Path,
    backup_dir: str | Path | None = None,
    timestamp: datetime | None = None,
) -> Path:
    """Return a timestamped backup path without creating or replacing a file."""

    source = _normalise_path(source_path)
    directory = _normalise_path(backup_dir) if backup_dir is not None else source.parent
    return directory / timestamped_backup_name(source, timestamp)


@dataclass
class OperationLedger:
    """A pending draft and its eventual copy-out operation metadata.

    ``before_fields`` and ``after_fields`` contain only fields whose values
    differ.  The ledger does not edit ``target_path``; a save backend can use
    the recorded metadata when it writes a separate target.
    """

    source_path: str | Path
    target_path: str | Path | None = None
    backup_policy: BackupPolicy | str = BackupPolicy.ALWAYS
    before_fields: dict[str, Any] = field(default_factory=dict)
    after_fields: dict[str, Any] = field(default_factory=dict)
    validation_messages: list[str] = field(default_factory=list)
    operation_status: str = "idle"
    operation_message: str = ""
    drafts: dict[str, DraftEntry] = field(default_factory=dict)
    _dirty: bool = field(default=False, init=False, repr=False)
    _backup_path: Path | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.source_path = _normalise_path(self.source_path)
        if self.target_path is not None:
            self.target_path = _normalise_path(self.target_path)
        self.backup_policy = _normalise_policy(self.backup_policy)

        self.before_fields = copy.deepcopy(dict(self.before_fields))
        self.after_fields = copy.deepcopy(dict(self.after_fields))
        self.validation_messages = self._message_texts(self.validation_messages)
        self.operation_status = str(self.operation_status or "idle")
        self.operation_message = str(self.operation_message or "")
        self.drafts = {
            str(identity): entry
            for identity, entry in dict(self.drafts).items()
            if isinstance(entry, DraftEntry) and str(identity).strip()
        }
        self._validate_target()
        self._dirty = bool(self._changed_field_names())

    def _validate_target(self) -> None:
        if self.target_path is not None and self.source_path == self.target_path:
            raise LedgerError("Source and target must be different files; the source is read-only")

    @staticmethod
    def _message_texts(messages: Iterable[Any]) -> list[str]:
        result: list[str] = []
        for message in messages:
            text = getattr(message, "message", message)
            text = str(text)
            if text and text not in result:
                result.append(text)
        return result

    def _changed_field_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for name in (*self.before_fields.keys(), *self.after_fields.keys()):
            if name not in names:
                names.append(name)
        return tuple(
            name
            for name in names
            if self.before_fields.get(name) != self.after_fields.get(name)
            or name not in self.before_fields
            or name not in self.after_fields
        )

    @property
    def dirty(self) -> bool:
        """Whether the ledger contains a draft that has not been cleared."""

        return self._dirty or bool(self.drafts)

    @property
    def changed_fields(self) -> tuple[str, ...]:
        """Names of changed fields in first-seen order."""

        names = list(self._changed_field_names())
        for entry in self.drafts.values():
            for name in entry.changed_fields:
                if name not in names:
                    names.append(name)
        return tuple(names)

    @property
    def changes(self) -> tuple[FieldChange, ...]:
        """Structured before/after values for each changed field."""

        result = list(self._legacy_changes())
        for entry in self.drafts.values():
            result.extend(entry.changes)
        return tuple(result)

    def _legacy_changes(self) -> tuple[FieldChange, ...]:
        missing = object()
        return tuple(
            FieldChange(
                name,
                copy.deepcopy(self.before_fields.get(name, missing)),
                copy.deepcopy(self.after_fields.get(name, missing)),
            )
            for name in self._changed_field_names()
        )

    @property
    def pending_entries(self) -> tuple[DraftEntry, ...]:
        """Pending Pal drafts in insertion order."""

        return tuple(self.drafts.values())

    @property
    def pending_pal_count(self) -> int:
        return len(self.drafts) + (1 if self._changed_field_names() else 0)

    @property
    def total_changed_field_count(self) -> int:
        return len(self._changed_field_names()) + sum(
            len(entry.changed_fields) for entry in self.drafts.values()
        )

    def draft_for(self, instance_id: str) -> DraftEntry | None:
        return self.drafts.get(str(instance_id))

    def record_pal_draft(
        self,
        instance_id: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        *,
        source_index: int | None = None,
        display_context: str = "",
    ) -> DraftEntry | None:
        """Add, update, or remove one stable-identity keyed Pal draft."""

        identity = str(instance_id).strip()
        if not identity:
            raise LedgerError("A Pal draft requires a stable instance identity")
        names = list(dict.fromkeys((*before.keys(), *after.keys())))
        before_fields = {
            name: copy.deepcopy(before[name])
            for name in names
            if name in before and (name not in after or before[name] != after[name])
        }
        after_fields = {
            name: copy.deepcopy(after[name])
            for name in names
            if name in after and (name not in before or before[name] != after[name])
        }
        if not before_fields and not after_fields:
            self.drafts.pop(identity, None)
            return None
        entry = DraftEntry(
            identity,
            before_fields,
            after_fields,
            source_index=source_index,
            display_context=str(display_context or ""),
        )
        self.drafts[identity] = entry
        return entry

    def remove_pal_draft(self, instance_id: str) -> None:
        self.drafts.pop(str(instance_id), None)

    def clear_drafts(self) -> None:
        """Clear the complete aggregate draft after success or global revert."""

        self.drafts.clear()
        self.before_fields.clear()
        self.after_fields.clear()
        self._dirty = False
        self.clear_validation_messages()

    def changes_by_pal(self) -> tuple[DraftEntry, ...]:
        """Return identity-keyed entries for batch review and serialization."""

        return self.pending_entries

    @property
    def backup_path(self) -> Path | None:
        """The backup created by the latest successful backup operation."""

        return self._backup_path

    def set_target_path(self, target_path: str | Path | None) -> None:
        """Set a separate output path, rejecting an attempted source overwrite."""

        self.target_path = _normalise_path(target_path) if target_path is not None else None
        self._validate_target()

    def record_changes(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> tuple[FieldChange, ...]:
        """Replace the draft with the actual differences between two mappings."""

        names = list(dict.fromkeys((*before.keys(), *after.keys())))
        self.before_fields = {
            name: copy.deepcopy(before[name])
            for name in names
            if name in before and (name not in after or before[name] != after[name])
        }
        self.after_fields = {
            name: copy.deepcopy(after[name])
            for name in names
            if name in after and (name not in before or before[name] != after[name])
        }
        self._dirty = bool(self.changed_fields)
        return self.changes

    # A draft-oriented name reads naturally at UI boundaries.
    record_draft = record_changes

    def set_field(self, name: str, before: Any, after: Any) -> None:
        """Add or replace one draft field, removing it when it is unchanged."""

        if not str(name).strip():
            raise LedgerError("A changed field needs a non-empty name")
        if before == after:
            self.before_fields.pop(name, None)
            self.after_fields.pop(name, None)
        else:
            self.before_fields[name] = copy.deepcopy(before)
            self.after_fields[name] = copy.deepcopy(after)
        self._dirty = bool(self.changed_fields)

    add_change = set_field

    def set_validation_messages(self, messages: Iterable[Any]) -> None:
        """Replace validation text while preserving its first-seen order."""

        self.validation_messages = self._message_texts(messages)

    def add_validation_message(self, message: Any) -> None:
        """Add a validation message, accepting strings or issue-like objects."""

        texts = self._message_texts((message,))
        for text in texts:
            if text not in self.validation_messages:
                self.validation_messages.append(text)

    def clear_validation_messages(self) -> None:
        self.validation_messages.clear()

    def record_operation(self, status: str, message: str = "") -> None:
        """Record the latest save outcome without changing draft cleanliness."""

        status_text = str(status).strip()
        if not status_text:
            raise LedgerError("An operation status needs a non-empty value")
        self.operation_status = status_text
        self.operation_message = str(message)

    def mark_clean(self) -> None:
        """Mark the current draft as acknowledged without changing its diff."""

        # Preserve the historical audit-diff behavior for the legacy single
        # draft fields while clearing the identity-keyed pending collection.
        # Production multi-Pal success creates a fresh ledger only after the
        # accepted source reload, so it does not rely on this compatibility API.
        self.drafts.clear()
        self._dirty = False

    def should_backup(self, approved: bool = False) -> bool:
        """Return whether policy permits a backup for this operation."""

        if self.backup_policy is BackupPolicy.ALWAYS:
            return True
        if self.backup_policy is BackupPolicy.ASK:
            return approved
        return False

    def new_backup_path(
        self,
        *,
        backup_dir: str | Path | None = None,
        timestamp: datetime | None = None,
    ) -> Path:
        """Choose a non-existing timestamped backup path without creating it."""

        candidate = timestamped_backup_path(self.source_path, backup_dir, timestamp)
        counter = 1
        while candidate.exists():
            candidate = candidate.with_name(
                f"{candidate.stem}-{counter}{candidate.suffix}"
            )
            counter += 1
        return candidate

    def create_backup(
        self,
        *,
        approved: bool = False,
        backup_dir: str | Path | None = None,
        timestamp: datetime | None = None,
    ) -> Path | None:
        """Copy the source to a new backup when the policy allows it.

        ``ask`` requires ``approved=True``.  ``off`` returns ``None``.  The
        destination is opened exclusively, so an existing backup is never
        overwritten.  This method intentionally has no cleanup or deletion
        behavior.
        """

        if self.backup_policy is BackupPolicy.OFF:
            return None
        if self.backup_policy is BackupPolicy.ASK and not approved:
            raise BackupApprovalRequired("Backup approval is required by the 'ask' policy")
        if not self.source_path.is_file():
            raise LedgerError(f"Source save does not exist: {self.source_path}")

        destination = self.new_backup_path(backup_dir=backup_dir, timestamp=timestamp)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.source_path.open("rb") as source, destination.open("xb") as backup:
            shutil.copyfileobj(source, backup)

        self._backup_path = destination
        return destination


# Readable aliases for callers that prefer the shorter names.
BackupMode = BackupPolicy
DraftLedger = OperationLedger


__all__ = [
    "BACKUP_TIMESTAMP_FORMAT",
    "BackupApprovalRequired",
    "BackupMode",
    "BackupPolicy",
    "DraftLedger",
    "DraftEntry",
    "FieldChange",
    "LedgerError",
    "OperationLedger",
    "timestamped_backup_name",
    "timestamped_backup_path",
]
