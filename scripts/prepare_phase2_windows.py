#!/usr/bin/env python
"""Build events, forecasting windows/targets and window features: python scripts/prepare_phase2_windows.py [--config configs/phase2.yaml]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parkistep.forecast.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main("prepare"))
