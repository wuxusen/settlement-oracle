#!/usr/bin/env python3
"""Capture authoritative final results for World Cup fixtures from the live
TxLINE scores feed, and write one self-contained evidence bundle per fixture
into ``data/fixtures/``.

Each bundle records exactly the data the settlement oracle needs to resolve a
prediction-market contract, plus enough provenance for anyone to re-fetch and
re-check it against TxLINE:

* the fixture identity (id, competition, participants, kickoff),
* the *authoritative* full-time score, taken from the ``game_finalised``
  score-feed action (the terminal, confirmed settlement signal), and
* the raw finalisation record(s) as evidence.

This is a one-off / refreshable capture tool. The oracle, its tests and the
demo run entirely off the committed bundles, so nothing here is on the
settlement critical path and no credentials are ever written into the output.

Usage:
    TXLINE_CREDS_FILE=~/.wallets/.txline_creds.json python3 scripts/capture_results.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from settlement_oracle.feeds.txline_results import (  # noqa: E402
    build_evidence_from_snapshot,
    load_credentials,
)

COMPETITION_ID = 72  # FIFA World Cup on TxLINE devnet
OUT_DIR = REPO / "data" / "fixtures"

# The 24 real World Cup fixtures captured for the sister market-maker project;
# we reuse the same fixture ids so both projects reference the same matches.
FIXTURE_IDS = [
    18188721, 18185036, 18179549, 18175918, 18176123, 18179552,
    18179763, 18179551, 18172379, 18179550, 18179764, 18179759,
    18175397, 18175981, 18175983, 18172280, 18172469, 18167317,
    17926704, 17588245, 17588325, 17588326, 17588391, 17588402,
]


def _http_get_json(base: str, headers: dict, path: str):
    req = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _build_name_map(base: str, headers: dict) -> dict:
    """participant_id -> team name, gathered from live fixtures snapshots."""
    names: dict[int, str] = {}
    now_day = int(_dt.datetime.now(_dt.timezone.utc).timestamp() // 86400)
    for ed in range(now_day + 1, now_day - 40, -1):
        try:
            fixtures = _http_get_json(
                base, headers, f"/fixtures/snapshot?competitionId={COMPETITION_ID}&startEpochDay={ed}"
            )
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
        for f in fixtures or []:
            if f.get("Participant1Id") and f.get("Participant1"):
                names[int(f["Participant1Id"])] = f["Participant1"]
            if f.get("Participant2Id") and f.get("Participant2"):
                names[int(f["Participant2Id"])] = f["Participant2"]
    return names


def main() -> int:
    creds = load_credentials()
    base = creds["apiBaseUrl"].rstrip("/")
    headers = {
        "Authorization": f"Bearer {creds['jwt']}",
        "X-Api-Token": creds["apiToken"],
        "Accept": "application/json",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name_map = _build_name_map(base, headers)
    print(f"  resolved {len(name_map)} team names from fixtures feed")
    captured, skipped = [], []
    for fid in FIXTURE_IDS:
        try:
            snapshot = _http_get_json(base, headers, f"/scores/snapshot/{fid}")
            # peek participant ids so we can attach human-readable team names
            peek = next((r for r in snapshot if r.get("Participant1Id")), {})
            p1_name = name_map.get(int(peek.get("Participant1Id", 0)))
            p2_name = name_map.get(int(peek.get("Participant2Id", 0)))
            evidence = build_evidence_from_snapshot(
                fid, snapshot, competition_id=COMPETITION_ID,
                p1_name=p1_name, p2_name=p2_name,
            )
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
            skipped.append({"fixture_id": fid, "reason": f"{type(exc).__name__}: {exc}"})
            print(f"  skip {fid}: {exc}")
            continue
        out = OUT_DIR / f"{fid}.json"
        out.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
        label = f"{evidence['home']} v {evidence['away']}"
        score = f"{evidence['home_goals']}-{evidence['away_goals']}"
        captured.append({"fixture_id": fid, "label": label, "score": score,
                         "result": evidence["result"]})
        print(f"  ok   {fid}  {label:<28} {score}  {evidence['result']}")
    index = {
        "captured_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "TxODDS TxLINE devnet — FIFA World Cup free tier",
        "competition_id": COMPETITION_ID,
        "n_captured": len(captured),
        "n_skipped": len(skipped),
        "captured": captured,
        "skipped": skipped,
    }
    (OUT_DIR / "_index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"captured {len(captured)} / {len(FIXTURE_IDS)} fixtures -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
