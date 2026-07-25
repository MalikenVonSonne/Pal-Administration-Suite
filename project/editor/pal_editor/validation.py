"""Conservative validation for portable Pal templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .domain import PalTemplate

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    field: str | None = None


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, code: str, message: str, field: str | None = None) -> None:
        self.issues.append(ValidationIssue(severity, code, message, field))


def _bounded(
    report: ValidationReport,
    value: int | None,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value is None:
        return
    if not isinstance(value, int):
        report.add("error", "not_integer", f"{name} must be an integer", name)
    elif not minimum <= value <= maximum:
        report.add(
            "error",
            "out_of_range",
            f"{name} must be between {minimum} and {maximum}",
            name,
        )


def validate_template(template: PalTemplate, *, mode: Literal["legal", "advanced"] = "legal") -> ValidationReport:
    """Validate only rules we can defend; unknown game fields remain untouched."""
    report = ValidationReport()

    if not template.species.strip():
        report.add("error", "missing_species", "A Pal species is required", "species")

    _bounded(report, template.level, name="level", minimum=1, maximum=80)
    _bounded(report, template.iv_hp, name="iv_hp", minimum=0, maximum=100)
    _bounded(report, template.iv_attack, name="iv_attack", minimum=0, maximum=100)
    _bounded(report, template.iv_defense, name="iv_defense", minimum=0, maximum=100)
    _bounded(report, template.rank, name="rank", minimum=0, maximum=5)

    if len(template.passive_skills) > 4:
        severity: Severity = "warning" if mode == "advanced" else "error"
        report.add(
            severity,
            "too_many_passives",
            "Standard Pals have at most four passive skills",
            "passive_skills",
        )

    if len(set(template.passive_skills)) != len(template.passive_skills):
        report.add(
            "warning",
            "duplicate_passive",
            "The passive list contains duplicates",
            "passive_skills",
        )

    if len(set(template.active_skills)) != len(template.active_skills):
        report.add(
            "warning",
            "duplicate_active_skill",
            "The active-skill list contains duplicates",
            "active_skills",
        )

    if len(template.nickname) > 32:
        report.add(
            "warning",
            "long_nickname",
            "The nickname may exceed the in-game display limit",
            "nickname",
        )

    return report
