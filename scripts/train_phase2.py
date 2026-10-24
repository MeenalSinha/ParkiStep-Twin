#!/usr/bin/env python
"""Train and cross-validate Phase 2 models (participant-grouped): python scripts/train_phase2.py [--config configs/phase2.yaml]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parkistep.forecast.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main("train"))
