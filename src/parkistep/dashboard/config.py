"""Phase 4 configuration: loading and validation. Presentation/interaction settings only (see configs/phase4.yaml)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError, apply_overrides, load_yaml

THEME_ROLES = ("background", "surface", "text", "muted", "border", "primary", "neutral", "ok", "caution", "alert",
               "unavailable", "historical", "annotation")
REQUIRED_SECTIONS = ("app", "paths", "navigation", "selection", "model_selection", "replay", "plots", "tables", "display",
                     "features", "theme", "status_styles", "text")


@dataclass
class Phase4Settings:
    cfg: dict
    root: Path
    config_path: Path

    def __getitem__(self, k):
        return self.cfg[k]

    def resolve(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.cfg, sort_keys=True, default=str).encode()).hexdigest()

    @property
    def phase3_config_path(self) -> Path:
        return self.resolve(self.cfg["paths"]["phase3_config"])

    def style(self, group: str, key) -> dict:
        """Label/role/help for a status value; unknown values get a neutral, explicit style instead of an exception."""
        d = self.cfg["status_styles"].get(group, {}).get(str(key))
        return dict(d) if d else {"label": str(key), "role": "neutral", "help": ""}

    def color(self, role: str) -> str:
        return self.cfg["theme"]["colors"].get(role, self.cfg["theme"]["colors"]["neutral"])


def _validate(cfg: dict) -> None:
    p: list[str] = []
    for k in REQUIRED_SECTIONS:
        if not isinstance(cfg.get(k), dict) and not (k == "navigation" and isinstance(cfg.get(k), list)):
            p.append(f"missing required section `phase4.{k}`")
    if p:
        raise ConfigError("Invalid Phase 4 configuration: " + "; ".join(p))

    def num(path, positive=True, integer=False):
        cur = cfg
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, bool) or not isinstance(cur, (int, float)):
            p.append(f"phase4.{path} must be a number, got {cur!r}")
        elif positive and cur <= 0:
            p.append(f"phase4.{path} must be > 0, got {cur}")
        elif integer and int(cur) != cur:
            p.append(f"phase4.{path} must be an integer, got {cur}")

    for path in ("replay.refresh_interval_s", "replay.default_speed", "plots.history_s", "plots.height_px", "plots.gap_factor"):
        num(path)
    for path in ("replay.max_observations_per_tick", "replay.keep_completed_sessions", "replay.max_stored_observations",
                 "plots.max_points_per_trace", "plots.forecast_history_points", "tables.max_rows", "tables.recent_forecasts"):
        num(path, integer=True)
    sp = cfg["replay"].get("speed_choices")
    if not isinstance(sp, list) or not sp or any(isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0 for x in sp):
        p.append("phase4.replay.speed_choices must be a non-empty list of positive numbers")
    elif cfg["replay"].get("default_speed") not in sp and not cfg["replay"].get("allow_max_speed"):
        p.append("phase4.replay.default_speed must be one of replay.speed_choices")
    nav = cfg["navigation"]
    if not isinstance(nav, list) or not nav or any(not isinstance(n, dict) or "key" not in n or "title" not in n for n in nav):
        p.append("phase4.navigation must be a list of {key, title}")
    if cfg["selection"].get("default_patient_policy") not in ("phase3_selection", "first", "none"):
        p.append("phase4.selection.default_patient_policy must be phase3_selection | first | none")
    if cfg["selection"].get("default_recording_policy") not in ("phase3_selection", "first", "longest", "none"):
        p.append("phase4.selection.default_recording_policy must be phase3_selection | first | longest | none")
    if cfg["model_selection"].get("default") != "phase3":
        p.append("phase4.model_selection.default must be 'phase3' (the model configured in phase3.yaml)")
    th = cfg["theme"].get("colors", {})
    miss = [r for r in THEME_ROLES if r not in th]
    if miss:
        p.append(f"phase4.theme.colors lacks {miss}")
    for grp in ("twin", "forecast", "warning", "replay"):
        for k, v in (cfg["status_styles"].get(grp) or {}).items():
            if v.get("role") not in th:
                p.append(f"phase4.status_styles.{grp}.{k}.role {v.get('role')!r} is not a theme colour role")
    if cfg["app"].get("layout") not in ("wide", "centered"):
        p.append("phase4.app.layout must be 'wide' or 'centered'")
    if p:
        raise ConfigError("Invalid Phase 4 configuration:\n  - " + "\n  - ".join(p))


def _reject_unknown_overrides(base: dict, overrides: list[str]) -> None:
    """A mistyped override key used to be accepted silently and ignored. Every override must name a setting that exists."""
    bad = []
    for item in overrides:
        key = item.split("=", 1)[0].strip()
        parts = key.split(".")[1:]                      # drop the leading `phase4`
        cur = base
        for i, part in enumerate(parts):
            if not isinstance(cur, dict) or part not in cur:
                bad.append(key)
                break
            cur = cur[part]
    if bad:
        raise ConfigError("Unknown Phase 4 setting(s) in override: " + ", ".join(bad)
                          + ". Overrides can only change settings that exist in the configuration file (check for typos).")


def load_phase4(config_path: str | Path = "configs/phase4.yaml", project_root: str | Path | None = None,
                overrides: list[str] | None = None) -> Phase4Settings:
    config_path = Path(config_path).resolve()
    root = Path(project_root).resolve() if project_root else config_path.parent.parent
    raw = load_yaml(config_path)
    if "phase4" not in raw:
        raise ConfigError(f"{config_path} has no `phase4:` section.")
    ov = [o if o.lstrip().startswith("phase4.") else "phase4." + o.lstrip() for o in (overrides or [])]
    _reject_unknown_overrides(raw["phase4"], ov)
    cfg = apply_overrides({"phase4": raw["phase4"]}, ov)["phase4"]
    _validate(cfg)
    return Phase4Settings(cfg=cfg, root=root, config_path=config_path)


def parse_override_env(value: str | None) -> list[str]:
    """`PARKISTEP_PHASE4_SET="a.b=1;c.d=x"` -> ['a.b=1', 'c.d=x'] (the dashboard has no command line of its own)."""
    return [x.strip() for x in (value or "").split(";") if x.strip()]
