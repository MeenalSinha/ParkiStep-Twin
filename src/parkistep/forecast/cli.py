"""Command-line entry for Phase 2: prepare | train | evaluate."""
from __future__ import annotations

import argparse
import logging
import sys

from ..config import ConfigError
from .prepare import Phase2Error
from .settings import load_phase2


def main(command: str, argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=f"phase2 {command}")
    ap.add_argument("--config", default="configs/phase2.yaml")
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--set", action="append", default=[], metavar="key=value", help="override a phase2 setting, e.g. windowing.stride_s=0.5")
    ap.add_argument("--force", action="store_true", help="rebuild cached windows / retrain")
    ap.add_argument("--log-level", default=None)
    args = ap.parse_args(argv)
    try:
        s = load_phase2(args.config, args.project_root, args.set)
    except (ConfigError, FileNotFoundError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=(args.log_level or s.cfg.get("logging", {}).get("level", "INFO")),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        from . import experiment as ex
        if command == "prepare":
            ex.prepare(s, force=args.force)
        elif command == "train":
            ex.train(s, force=args.force)
        elif command == "evaluate":
            ex.evaluate(s)
        else:
            raise ValueError(command)
    except (Phase2Error, ConfigError) as e:
        print(f"Phase 2 error: {e}", file=sys.stderr)
        return 1
    return 0
