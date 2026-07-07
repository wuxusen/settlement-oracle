"""Loading committed result-evidence bundles from ``data/fixtures/``.

These are the real, captured TxLINE World Cup results the demo and tests run
against, so everything works offline and deterministically. Refresh them with
``scripts/capture_results.py`` against the live feed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def data_dir() -> Path:
    return _DATA_DIR


def load_evidence(fixture_id: int, data_dir: Optional[Path] = None) -> dict:
    """Load one fixture's result-evidence bundle."""
    d = data_dir or _DATA_DIR
    path = Path(d) / f"{fixture_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_fixtures(data_dir: Optional[Path] = None) -> list:
    """Index of captured fixtures: [{fixture_id, label, score, result}]."""
    d = Path(data_dir or _DATA_DIR)
    index = d / "_index.json"
    if index.exists():
        return json.loads(index.read_text(encoding="utf-8")).get("captured", [])
    out = []
    for p in sorted(d.glob("*.json")):
        if p.name.startswith("_"):
            continue
        ev = json.loads(p.read_text(encoding="utf-8"))
        out.append(
            {
                "fixture_id": ev["fixture_id"],
                "label": f"{ev.get('home')} v {ev.get('away')}",
                "score": f"{ev['home_goals']}-{ev['away_goals']}",
                "result": ev["result"],
            }
        )
    return out
