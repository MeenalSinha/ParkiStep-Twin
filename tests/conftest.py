import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


import logging

import pytest


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    root.handlers[:] = before
