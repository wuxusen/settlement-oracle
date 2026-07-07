"""Ensure the package is importable when running ``pytest`` from the repo root
without an editable install."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
