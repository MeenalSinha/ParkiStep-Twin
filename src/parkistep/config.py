"""Configuration loading, CLI overrides and validation.

Two YAML files are used: a project file (paths, outputs, logging, reports, plots)
and a datasets file (one profile per dataset). Both are merged into `Settings`.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Invalid or missing configuration."""


def load_yaml(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}. Pass an existing file with --config / --project-config.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Configuration file {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file {path} must contain a YAML mapping at the top level.")
    return data


def set_dotted(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = cfg
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def apply_overrides(cfg: dict, overrides: list[str] | None) -> dict:
    """Apply `dotted.key=value` overrides (value parsed as YAML). Returns a new dict."""
    cfg = copy.deepcopy(cfg)
    for item in overrides or []:
        if "=" not in item:
            raise ConfigError(f"Invalid override {item!r}; expected the form dotted.key=value.")
        key, raw = item.split("=", 1)
        set_dotted(cfg, key.strip(), yaml.safe_load(raw))
    return cfg


@dataclass
class Settings:
    project: dict
    dataset: dict
    dataset_key: str
    root: Path

    # ---- paths -------------------------------------------------------
    def _resolve(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (self.root / p)

    @property
    def raw_dir(self) -> Path:
        return self._resolve(self.project["paths"]["data_root"]) / self.dataset["raw_subdir"]

    @property
    def processed_dir(self) -> Path:
        return self._resolve(self.project["paths"]["processed_root"]) / self.dataset_key

    @property
    def reports_dir(self) -> Path:
        return self._resolve(self.project["paths"]["reports_dir"])

    @property
    def figures_dir(self) -> Path:
        return self._resolve(self.project["paths"]["figures_dir"])

    def resolve(self, p: str | Path) -> Path:
        return self._resolve(p)


def _need(cfg: dict, dotted: str, problems: list[str], types=None):
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            problems.append(f"missing required setting `{dotted}`")
            return None
        cur = cur[part]
    if types is not None and not isinstance(cur, types):
        problems.append(f"`{dotted}` must be of type {getattr(types, '__name__', types)}, got {type(cur).__name__}")
        return None
    return cur


_DTYPES = {"int", "float", "str"}
_SEVERITIES = {"error", "warning", "info"}


def validate_project(project: dict) -> None:
    problems: list[str] = []
    for key in ("paths.data_root", "paths.interim_root", "paths.processed_root", "paths.reports_dir",
                "paths.figures_dir", "outputs.format", "outputs.file_stems", "outputs.manifest",
                "reports.markdown", "reports.json", "reports.join_audit", "logging.level"):
        _need(project, key, problems)
    fmt = project.get("outputs", {}).get("format")
    if fmt is not None and fmt not in ("parquet", "csv"):
        problems.append(f"`outputs.format` must be 'parquet' or 'csv', got {fmt!r}")
    if problems:
        raise ConfigError("Invalid project configuration:\n  - " + "\n  - ".join(problems))


def validate_profile(profile: dict, key: str) -> None:
    p = f"datasets.{key}"
    problems: list[str] = []
    for k in ("name", "version", "raw_subdir", "files.sensor.pattern", "files.clinical.pattern",
              "clinical.key", "clinical.fields", "sensor.identifiers", "sensor.group_by",
              "sensor.timestamp.source", "sensor.timestamp.canonical", "sensor.timestamp.unit",
              "sensor.channels.positions", "sensor.channels.axes", "sensor.channels.signals",
              "sensor.channels.source_template", "sensor.channels.canonical_template",
              "sampling", "join.key", "join.how", "join.expected_cardinality",
              "license.name", "attribution"):
        _need(profile, k, problems)
    if problems:
        raise ConfigError(f"Invalid dataset profile `{p}`:\n  - " + "\n  - ".join(problems))

    fields = profile["clinical"]["fields"]
    if not isinstance(fields, dict) or not fields:
        problems.append("`clinical.fields` must be a non-empty mapping canonical_name -> spec")
    else:
        for name, spec in fields.items():
            if not isinstance(spec, dict) or not spec.get("source"):
                problems.append(f"`clinical.fields.{name}.source` must list at least one source column name")
            elif not isinstance(spec["source"], list):
                problems.append(f"`clinical.fields.{name}.source` must be a list")
            if isinstance(spec, dict) and spec.get("dtype", "str") not in _DTYPES:
                problems.append(f"`clinical.fields.{name}.dtype` must be one of {sorted(_DTYPES)}")
            vr = spec.get("valid_range") if isinstance(spec, dict) else None
            if vr is not None and not (isinstance(vr, list) and len(vr) == 2 and vr[0] <= vr[1]):
                problems.append(f"`clinical.fields.{name}.valid_range` must be [min, max]")
    if profile["clinical"]["key"] not in (fields or {}):
        problems.append(f"`clinical.key` ({profile['clinical']['key']!r}) is not defined in `clinical.fields`")
    if profile["join"]["key"] not in (fields or {}):
        problems.append(f"`join.key` ({profile['join']['key']!r}) is not a clinical canonical field")
    if profile["join"]["key"] not in profile["sensor"]["identifiers"]:
        problems.append(f"`join.key` ({profile['join']['key']!r}) is not a sensor identifier in `sensor.identifiers`")
    if profile["join"]["how"] not in ("left", "inner"):
        problems.append("`join.how` must be 'left' or 'inner'")
    if profile["join"]["expected_cardinality"] not in ("many_to_one", "one_to_one"):
        problems.append("`join.expected_cardinality` must be 'many_to_one' or 'one_to_one'")
    for g in profile["sensor"]["group_by"]:
        if g not in profile["sensor"]["identifiers"]:
            problems.append(f"`sensor.group_by` entry {g!r} is not defined in `sensor.identifiers`")
    unit = profile["sensor"]["timestamp"]["unit"]
    kind = profile["sensor"]["timestamp"].get("kind", "numeric")
    if kind not in ("numeric", "datetime"):
        problems.append("`sensor.timestamp.kind` must be 'numeric' or 'datetime'")
    if kind == "numeric" and unit not in ("seconds", "milliseconds", "microseconds"):
        problems.append("`sensor.timestamp.unit` must be seconds, milliseconds or microseconds")
    hz = profile["sampling"].get("expected_hz")
    if hz is not None and not (isinstance(hz, (int, float)) and hz > 0):
        problems.append("`sampling.expected_hz` must be a positive number or null")
    for sect in ("validation",):
        for grp, rules in (profile.get(sect) or {}).items():
            for rk, rv in (rules or {}).items():
                if rk.endswith("_severity") and rv not in _SEVERITIES:
                    problems.append(f"`validation.{grp}.{rk}` must be one of {sorted(_SEVERITIES)}")
    ann = profile["sensor"].get("annotations", {})
    rules = profile["sensor"].get("annotation_rules", {})
    if rules:
        for ref in ("event_label", "severity"):
            if rules.get(ref) and rules[ref] not in ann:
                problems.append(f"`sensor.annotation_rules.{ref}` ({rules[ref]!r}) is not defined in `sensor.annotations`")
    if profile.get("missing_data_policy", "retain") != "retain":
        problems.append("`missing_data_policy` currently supports only 'retain' (no imputation is implemented)")
    if problems:
        raise ConfigError(f"Invalid dataset profile `{p}`:\n  - " + "\n  - ".join(problems))


def load_settings(config_path: str | Path = "configs/datasets.yaml",
                  project_config_path: str | Path | None = None,
                  dataset: str | None = None,
                  project_root: str | Path | None = None,
                  overrides: list[str] | None = None) -> Settings:
    config_path = Path(config_path).resolve()
    if project_root is not None:
        root = Path(project_root).resolve()
    else:
        root = config_path.parent.parent
    if project_config_path is None:
        project_config_path = config_path.parent / "project.yaml"
    project = load_yaml(Path(project_config_path))
    datasets_cfg = load_yaml(config_path)

    # Overrides address either `project.*`-level keys (paths., outputs., ...) or `datasets.<key>.*`.
    merged = {"project": project, "datasets": datasets_cfg.get("datasets", {})}
    ov_project, ov_datasets = [], []
    for item in overrides or []:
        (ov_datasets if item.startswith("datasets.") else ov_project).append(item)
    project = apply_overrides(project, ov_project)
    ds_all = apply_overrides({"datasets": merged["datasets"]}, ov_datasets)["datasets"]

    validate_project(project)
    if not ds_all:
        raise ConfigError(f"{config_path} has no `datasets:` section.")
    key = dataset or project.get("active_dataset")
    if not key:
        raise ConfigError("No dataset selected: pass --dataset or set `active_dataset` in the project config.")
    if key not in ds_all:
        raise ConfigError(f"Dataset profile {key!r} not found in {config_path}. Available: {sorted(ds_all)}")
    profile = ds_all[key]
    validate_profile(profile, key)
    return Settings(project=project, dataset=profile, dataset_key=key, root=root)


def channel_specs(profile: dict) -> list[dict]:
    """Expand the channel template config into explicit channel specs."""
    ch = profile["sensor"]["channels"]
    out = []
    for pos in ch["positions"]:
        for sig, sspec in ch["signals"].items():
            for ax in ch["axes"]:
                fmt = dict(position=pos, signal=sig, axis=ax)
                out.append({
                    "source": ch["source_template"].format(**fmt),
                    "canonical": ch["canonical_template"].format(**fmt),
                    "position": pos, "signal": sig, "axis": ax,
                    "unit": (sspec or {}).get("unit"),
                    "plausible_range": (sspec or {}).get("plausible_range"),
                })
    return out
