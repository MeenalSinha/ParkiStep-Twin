"""Structured validation issues (error / warning / info) shared by all modules."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

ERROR, WARNING, INFO = "error", "warning", "info"
_VALID = (ERROR, WARNING, INFO)


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    location: str = ""      # file or configuration entry involved
    action: str = ""        # what the user should do next
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in _VALID:
            raise ValueError(f"invalid severity {self.severity!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        s = f"[{self.severity.upper()}] {self.code}: {self.message}"
        if self.location:
            s += f" (at {self.location})"
        if self.action:
            s += f" -> {self.action}"
        return s


class Issues(list):
    """A list of Issue with convenience helpers."""

    def add(self, severity: str, code: str, message: str, location: str = "",
            action: str = "", **details) -> Issue:
        issue = Issue(severity, code, message, location, action, details)
        self.append(issue)
        return issue

    @property
    def errors(self):
        return [i for i in self if i.severity == ERROR]

    @property
    def warnings(self):
        return [i for i in self if i.severity == WARNING]

    @property
    def has_errors(self) -> bool:
        return any(i.severity == ERROR for i in self)

    def to_dicts(self) -> list[dict]:
        return [i.to_dict() for i in self]


class PipelineError(Exception):
    """Raised when processing cannot continue; carries the underlying issues."""

    def __init__(self, message: str, issues: list[Issue] | None = None):
        super().__init__(message)
        self.issues = list(issues or [])

    def __str__(self):
        base = super().__str__()
        if self.issues:
            base += "\n" + "\n".join(f"  - {i}" for i in self.issues)
        return base


def severity_from_config(value: str, default: str = WARNING) -> str:
    return value if value in _VALID else default
