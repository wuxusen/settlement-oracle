"""Precompute the web demo's data from the real settlement engine.

Runs the actual settlement_oracle package over every captured fixture, settles a
slate of prediction-market contracts, chains them into an audit ledger, and
writes the authoritative results (evidence, resolutions, receipt hashes, ledger
records, chain head) to docs/data.js as `window.ORACLE_DATA`.

The web page recomputes every one of these hashes in the browser with the Web
Crypto API and checks they reproduce — so the numbers here are the ground truth
the client independently re-derives, not values the page trusts blindly.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from settlement_oracle import contracts
from settlement_oracle.ledger import SettlementLedger
from settlement_oracle.oracle import SettlementOracle
from settlement_oracle.store import list_fixtures, load_evidence

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.js"

# A fixed settled_at base so the ledger records (which hash settled_at) are
# reproducible across runs. Metadata only; excluded from the receipt hash.
SETTLED_AT_BASE = 1751500000000

# The real, previously-committed Solana devnet anchor produced by the sister
# project (odds-market-maker) with the *identical* SolanaAnchor code and Memo
# Program. Presented on the page as live, independently-verifiable proof that
# the shared on-chain anchoring path works end-to-end. Not fabricated; open the
# explorer link and decode the Memo yourself.
SISTER_ANCHOR = {
    "tx_sig": "a1HPa9V9k8Q8ckFJoNLckSjCC7yNRsEUU8NSsfpnj6UfTxuWtpPDn4rpGLZegmMifLak7zwADh9LNB5s598D2MG",
    "slot": 473814127,
    "anchored_head": "3bec697170a5369a1395e79d4462d50f898d89850d5f5a5afca403cd44b05b48",
    "memo_prefix": "odds-mm-audit-head:v1:",
    "project": "odds-market-maker",
    "cluster": "devnet",
}

# The memo prefix this oracle uses when it anchors its own ledger head.
ORACLE_MEMO_PREFIX = "settlement-oracle:v1:"


def build_slate(fixture_id: int, ev: dict) -> list:
    """The representative market slate, matching the CLI demo."""
    home, away = ev.get("home", "HOME"), ev.get("away", "AWAY")
    return [
        contracts.team_to_win(f"{fixture_id}-HOME", fixture_id, "HOME", home),
        contracts.team_to_win(f"{fixture_id}-AWAY", fixture_id, "AWAY", away),
        contracts.match_draw(f"{fixture_id}-DRAW", fixture_id),
        contracts.double_chance(f"{fixture_id}-1X", fixture_id, ["HOME", "DRAW"]),
        contracts.total_over(f"{fixture_id}-O25", fixture_id, 2.5),
        contracts.total_under(f"{fixture_id}-U25", fixture_id, 2.5),
        contracts.both_teams_to_score(f"{fixture_id}-BTTS", fixture_id),
        contracts.exact_score(
            f"{fixture_id}-CS", fixture_id, ev["home_goals"], ev["away_goals"]
        ),
    ]


def compact_evidence(ev: dict) -> dict:
    """The evidence fields the client needs: the exact ones the digest commits
    to, plus display fields and the finalised record (kept small)."""
    fr = ev.get("finalised_record", {}) or {}
    return {
        "fixture_id": ev["fixture_id"],
        "competition_id": ev.get("competition_id"),
        "participant1_id": ev.get("participant1_id"),
        "participant2_id": ev.get("participant2_id"),
        "participant1_is_home": ev["participant1_is_home"],
        "home": ev.get("home"),
        "away": ev.get("away"),
        "home_goals": ev["home_goals"],
        "away_goals": ev["away_goals"],
        "result": ev.get("result"),
        "start_time": ev.get("start_time"),
        "finalised_ts": ev["finalised_ts"],
        "finalised_seq": ev["finalised_seq"],
        "source": ev["source"],
        "finalised_action": fr.get("Action"),
    }


def build_fixture(entry: dict) -> dict:
    fid = int(entry["fixture_id"])
    ev = load_evidence(fid)
    oracle = SettlementOracle(ledger=SettlementLedger())

    slate_out = []
    result_dict = None
    for i, contract in enumerate(build_slate(fid, ev)):
        outcome = oracle.settle(contract, ev, settled_at=SETTLED_AT_BASE + i)
        r = outcome.receipt
        result_dict = r.result  # identical for every contract on this fixture
        slate_out.append(
            {
                "contract": r.contract,
                "resolution": r.resolution,
                "receipt_version": r.receipt_version,
                "rule_id": r.rule_id,
                "receipt_hash": r.receipt_hash,
                "settled_at": r.settled_at,
            }
        )

    return {
        "fixture_id": fid,
        "label": entry.get("label", f"{ev.get('home')} v {ev.get('away')}"),
        "home": ev.get("home"),
        "away": ev.get("away"),
        "home_goals": ev["home_goals"],
        "away_goals": ev["away_goals"],
        "total_goals": ev["home_goals"] + ev["away_goals"],
        "outcome": ev.get("result"),
        "score": f"{ev['home_goals']}-{ev['away_goals']}",
        "competition_id": ev.get("competition_id"),
        "source": ev["source"],
        "finalised_action": (ev.get("finalised_record", {}) or {}).get("Action"),
        "finalised_ts": ev["finalised_ts"],
        "finalised_seq": ev["finalised_seq"],
        "start_time": ev.get("start_time"),
        "evidence": compact_evidence(ev),
        "evidence_digest": ev["evidence_digest"],
        "result": result_dict,
        "rule_id": slate_out[0]["rule_id"],
        "receipt_version": slate_out[0]["receipt_version"],
        "slate": slate_out,
        "ledger": oracle.ledger.to_dict(),
    }


def main() -> None:
    entries = list_fixtures()
    fixtures = [build_fixture(e) for e in entries]

    # A couple of narrative flags for the UI.
    for fx in fixtures:
        fx["is_var_case"] = fx["fixture_id"] == 18179763  # Portugal v Croatia

    data = {
        "generated_from": "settlement_oracle package · real TxODDS TxLINE devnet fixtures",
        "n_fixtures": len(fixtures),
        "memo_prefix": ORACLE_MEMO_PREFIX,
        "sister_anchor": SISTER_ANCHOR,
        "default_fixture_id": 18179763,
        "fixtures": fixtures,
    }

    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    OUT.write_text("window.ORACLE_DATA = " + blob + ";\n", encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT}  ({size_kb:.1f} KB, {len(fixtures)} fixtures)")


if __name__ == "__main__":
    main()
