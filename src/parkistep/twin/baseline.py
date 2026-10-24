"""Descriptive rolling movement baseline (optional, configurable).

What it is: an exponentially weighted mean/variance of a movement statistic (std or RMS of the acceleration/gyro
MAGNITUDE over the forecast window) per sensor position, computed from the patient's own observed samples.
What it is not: a trained personalised model, a disease model, or an input to the Phase 2 forecast.

Update rules (all configurable, see configs/phase3.yaml `baseline`):
  * Only windows that already passed the model's input-quality requirements are summarised (no imputed physiology).
  * Updates happen only when the twin status is in `update_only_when_status` (default READY).
  * Updates are frozen while a warning is pending or active (`freeze_while_warning`), so the baseline cannot slowly
    absorb the movement that precedes or accompanies a warning.
  * A window whose |z| exceeds `skip_if_abs_z_above` (once `min_updates` is reached) is NOT absorbed (outlier safeguard).
  * Event annotations are never available here; nothing is learned from ground truth or from future data.
  * Mean/variance are an exponentially weighted estimate with a bias-corrected start (cumulative mean/variance for the first
    ~1/ewma_alpha updates, exponential forgetting afterwards), so early z-scores are not inflated by an under-estimated variance.
  * Deviation (z) is reported only after `min_updates` updates; before that the baseline is `warming_up`.
"""
from __future__ import annotations

import math

import numpy as np

from ..forecast.features import FeatureSpec
from .state import BaselineState

VAR_FLOOR = 1e-12


class MovementBaseline:
    def __init__(self, cfg: dict, spec: FeatureSpec | None, positions: tuple[str, ...], state: BaselineState | None = None):
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled")) and spec is not None
        self.spec, self.positions = spec, positions
        self.state = state or BaselineState()
        self.state.enabled = self.enabled
        if not self.enabled:
            self.state.status = "disabled"
        elif self.state.status == "disabled":
            self.state.status = "warming_up"
        self.n_skipped_outlier = 0
        if self.enabled:
            self._groups = self._channel_groups()

    def _channel_groups(self) -> dict[str, list[int]]:
        """key -> raw channel indices (x,y,z of one signal at one position)."""
        out = {}
        for pos in self.positions:
            for sig in self.cfg["signals"]:
                idx = [i for i, c in enumerate(self.spec.raw_channels)
                       if self.spec.channel_meta[c]["position"] == pos and self.spec.channel_meta[c]["signal"] == sig]
                if idx:
                    out[f"{pos}_{sig}"] = idx
        return out

    def summarize(self, raw_window: np.ndarray) -> dict[str, float | None]:
        stat = self.cfg["statistic"]
        out: dict[str, float | None] = {}
        for key, idx in self._groups.items():
            seg = raw_window[:, idx]
            mag = np.sqrt(np.sum(seg ** 2, axis=1))
            if not np.isfinite(mag).all():
                out[key] = None                                   # incomplete samples: no value rather than a fabricated one
                continue
            out[key] = float(np.std(mag)) if stat == "std" else float(np.sqrt(np.mean(mag ** 2)))
        return out

    def observe(self, summary: dict, status: str, warning_state: str) -> dict:
        """Compare `summary` with the baseline and (if allowed) absorb it. Returns a small report."""
        if not self.enabled:
            return {"updated": False, "reason": "disabled"}
        cfg, st = self.cfg, self.state
        n_min = int(cfg["min_updates"])
        latest, zs = {}, []
        for k, v in summary.items():
            p = st.positions.get(k)
            z = None
            if v is not None and p and p["n"] >= n_min and p["var"] > VAR_FLOOR:
                z = (v - p["mean"]) / math.sqrt(p["var"])
            latest[k] = {"value": v, "z": z}
            if z is not None:
                zs.append(abs(z))
        st.latest = latest
        reason = None
        if status not in cfg["update_only_when_status"]:
            reason = f"twin status {status} is not in update_only_when_status"
        elif cfg.get("freeze_while_warning") and warning_state in ("pending", "active"):
            reason = f"frozen while a warning is {warning_state}"
        elif cfg.get("skip_if_abs_z_above") is not None and zs and max(zs) > float(cfg["skip_if_abs_z_above"]):
            reason = f"outlier safeguard: max |z| {max(zs):.2f} > {cfg['skip_if_abs_z_above']}"
            self.n_skipped_outlier += 1
        elif any(v is None for v in summary.values()):
            reason = "incomplete window summary"
        if reason:
            if st.status != "warming_up":
                st.status = "frozen"
            return {"updated": False, "reason": reason}
        a0 = float(cfg["ewma_alpha"])
        for k, v in summary.items():
            p = st.positions.get(k)
            if p is None:
                st.positions[k] = {"mean": v, "var": 0.0, "n": 1}
            else:
                a = max(a0, 1.0 / (p["n"] + 1))          # bias-corrected start: exact running mean/variance until n ~ 1/alpha, then forgetting
                diff = v - p["mean"]
                incr = a * diff
                p["mean"] += incr
                p["var"] = (1.0 - a) * (p["var"] + diff * incr)
                p["n"] += 1
        st.n_updates += 1
        st.status = "reporting" if st.n_updates >= n_min else "warming_up"
        return {"updated": True, "reason": None}

    def reset(self):
        n = self.state
        n.n_updates, n.positions, n.latest = 0, {}, {}
        n.status = "warming_up" if self.enabled else "disabled"
