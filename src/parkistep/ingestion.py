"""Source-aware ingestion layer.

`SourceAdapter` is the extension point for other datasets/formats. The bundled
`TabularFilesAdapter` reads delimited/parquet files described by the dataset profile and
returns *raw* tables (original column names untouched) plus provenance and issues.
Schema mapping and validation happen in `validation.py`.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .acquisition import sha256_file
from .config import Settings
from .issues import Issues

log = logging.getLogger(__name__)


@dataclass
class RawTable:
    kind: str                 # 'clinical' | 'sensor'
    path: Path
    df: pd.DataFrame
    sha256: str
    size_bytes: int


@dataclass
class Discovery:
    found: dict[str, Path] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)   # kind -> pattern searched
    issues: Issues = field(default_factory=Issues)

    @property
    def complete(self) -> bool:
        return not self.missing


class SourceAdapter(ABC):
    """Interface every dataset adapter implements."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def discover(self) -> Discovery: ...

    @abstractmethod
    def load(self, kind: str, path: Path) -> tuple[RawTable | None, Issues]: ...


class TabularFilesAdapter(SourceAdapter):
    SUPPORTED = ("csv", "tsv", "parquet")

    def discover(self) -> Discovery:
        d = Discovery()
        raw = self.settings.raw_dir
        for kind, spec in self.settings.dataset["files"].items():
            pattern = spec["pattern"]
            matches = sorted(raw.glob(pattern)) if raw.is_dir() else []
            matches = [m for m in matches if m.is_file()]
            if not matches:
                d.missing[kind] = pattern
                d.issues.add("error", "FILE_MISSING",
                             f"No file matching '{pattern}' for the {kind} table in {raw}.",
                             location=f"datasets.{self.settings.dataset_key}.files.{kind}.pattern",
                             action="Run scripts/acquire_data.py, place the file there, or fix the pattern in the profile.")
            elif len(matches) > 1:
                d.issues.add("error", "FILE_AMBIGUOUS",
                             f"Pattern '{pattern}' for the {kind} table matches {len(matches)} files: "
                             f"{[m.name for m in matches]}.",
                             location=f"datasets.{self.settings.dataset_key}.files.{kind}.pattern",
                             action="Make the pattern specific enough to match exactly one file.")
                d.missing[kind] = pattern
            else:
                d.found[kind] = matches[0]
        return d

    def load(self, kind: str, path: Path) -> tuple[RawTable | None, Issues]:
        issues = Issues()
        spec = self.settings.dataset["files"][kind]
        fmt = spec.get("format", "csv")
        loc = f"datasets.{self.settings.dataset_key}.files.{kind}"
        if fmt not in self.SUPPORTED:
            issues.add("error", "FORMAT_UNSUPPORTED", f"Unsupported file format {fmt!r} for the {kind} table.",
                       location=loc + ".format", action=f"Use one of {self.SUPPORTED} or add an adapter.")
            return None, issues
        try:
            if fmt == "parquet":
                df = pd.read_parquet(path)
            else:
                df = pd.read_csv(path, sep="\t" if fmt == "tsv" else spec.get("sep", ","),
                                 encoding=spec.get("encoding", "utf-8"), na_values=spec.get("na_values"),
                                 low_memory=False)
        except FileNotFoundError:
            issues.add("error", "FILE_MISSING", f"{path} does not exist.", location=str(path),
                       action="Acquire the dataset first.")
            return None, issues
        except UnicodeDecodeError as exc:
            issues.add("error", "ENCODING_ERROR", f"{path.name} is not valid {spec.get('encoding', 'utf-8')}: {exc}",
                       location=loc + ".encoding", action="Set the correct `encoding` for this file in the profile.")
            return None, issues
        except pd.errors.EmptyDataError:
            issues.add("error", "FILE_EMPTY", f"{path.name} is empty.", location=str(path),
                       action="Re-acquire the file; it may be an incomplete download.")
            return None, issues
        except (pd.errors.ParserError, ValueError, OSError) as exc:
            issues.add("error", "FILE_UNREADABLE", f"{path.name} could not be parsed as {fmt}: {exc}",
                       location=str(path), action="Check the file format/delimiter or re-download the file.")
            return None, issues
        if df.shape[0] == 0:
            issues.add("error", "NO_ROWS", f"{path.name} has a header but no data rows.", location=str(path),
                       action="Re-acquire the file.")
            return None, issues
        log.info("Loaded %s table: %s rows x %s columns from %s", kind, f"{len(df):,}", df.shape[1], path.name)
        return RawTable(kind, path, df, sha256_file(path), path.stat().st_size), issues


ADAPTERS = {"tabular_files": TabularFilesAdapter}


def get_adapter(settings: Settings) -> SourceAdapter:
    name = settings.dataset.get("adapter", "tabular_files")
    if name not in ADAPTERS:
        from .config import ConfigError
        raise ConfigError(f"Unknown adapter {name!r} in profile `{settings.dataset_key}`. Available: {sorted(ADAPTERS)}. "
                          "Implement parkistep.ingestion.SourceAdapter and register it in ADAPTERS.")
    return ADAPTERS[name](settings)
