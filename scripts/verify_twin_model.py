#!/usr/bin/env python
"""Thin wrapper: python scripts/verify_twin_model.py [options]  (see --help)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parkistep.twin.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main("verify-model"))
