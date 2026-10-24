"""Application bootstrap: configuration, the cached base service, one forked service per browser session, error screens.

Streamlit reruns the script on every interaction, so nothing here creates a twin: twins live in the service's replay sessions
(`DashboardService.create_session`), which are created only by an explicit user action.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
try:                                                                  # works with `pip install -e .` or a plain checkout
    import parkistep  # noqa: F401
except ImportError:                                                   # pragma: no cover
    sys.path.insert(0, str(_ROOT / "src"))

from parkistep.config import ConfigError                                   # noqa: E402
from parkistep.dashboard.config import Phase4Settings, load_phase4, parse_override_env   # noqa: E402
from parkistep.dashboard.service import DashboardService                    # noqa: E402

ENV_CONFIG = "PARKISTEP_PHASE4_CONFIG"
ENV_SET = "PARKISTEP_PHASE4_SET"
ENV_P3_SET = "PARKISTEP_PHASE3_SET"
ENV_ROOT = "PARKISTEP_PROJECT_ROOT"


@dataclass
class Context:
    s4: Phase4Settings
    svc: DashboardService

    def session(self):
        return self.svc.current_session()


def _config_path() -> str:
    return os.environ.get(ENV_CONFIG, str(_ROOT / "configs" / "phase4.yaml"))


@st.cache_resource(show_spinner="Loading dataset, model and evaluation reports ...")
def _base_service(config_path: str, root: str | None, overrides: tuple, p3_overrides: tuple) -> DashboardService:
    s4 = load_phase4(config_path, root, list(overrides))
    _setup_logging(s4)
    return DashboardService(s4, phase3_overrides=list(p3_overrides)).warm_up()


def _setup_logging(s4: Phase4Settings) -> None:
    cfg = s4["app"]
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.get("log_file"):
        p = s4.resolve(cfg["log_file"])
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(p))
    logging.basicConfig(level=cfg.get("log_level", "INFO"), handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def load_settings_or_stop() -> Phase4Settings:
    """Used before set_page_config: validates configuration and shows an actionable error instead of a traceback."""
    try:
        return load_phase4(_config_path(), os.environ.get(ENV_ROOT), parse_override_env(os.environ.get(ENV_SET)))
    except (ConfigError, FileNotFoundError) as e:
        st.set_page_config(page_title="ParkiStep Twin - configuration error")
        st.error("The dashboard configuration is invalid and the application cannot start.")
        st.code(str(e))
        st.caption(f"Configuration file: {_config_path()}. Fix it (or unset {ENV_CONFIG} / {ENV_SET}) and reload.")
        st.stop()
        raise


def get_context() -> Context:
    try:
        base = _base_service(_config_path(), os.environ.get(ENV_ROOT), tuple(parse_override_env(os.environ.get(ENV_SET))),
                             tuple(parse_override_env(os.environ.get(ENV_P3_SET))))
    except (ConfigError, FileNotFoundError) as e:
        st.error("The Phase 3 / Phase 2 configuration could not be loaded.")
        st.code(str(e))
        st.stop()
        raise
    if "_svc" not in st.session_state:
        st.session_state["_svc"] = base.fork()                     # per browser session: its own replay sessions and twins
    return Context(s4=base.s4, svc=st.session_state["_svc"])
