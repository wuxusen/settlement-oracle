"""Settlement engine: receipt construction, determinism, guardrails."""
from __future__ import annotations

import pytest

from settlement_oracle import contracts
from settlement_oracle.engine import FixtureMismatchError, SettlementEngine, compute_receipt_hash
from settlement_oracle.feeds.txline_results import build_evidence_from_snapshot


def _evidence(fixture_id=100, p1=2, p2=1, p1_home=True):
    rec = {
        "FixtureId": fixture_id, "Ts": 5, "Seq": 5, "Action": "game_finalised",
        "Participant1IsHome": p1_home, "Participant1Id": 11, "Participant2Id": 22,
        "CompetitionId": 72, "StartTime": 1000,
        "Score": {"Participant1": {"Total": {"Goals": p1}},
                  "Participant2": {"Total": {"Goals": p2}}},
    }
    return build_evidence_from_snapshot(fixture_id, [rec], competition_id=72)


def test_settle_resolves_and_binds_everything():
    ev = _evidence()
    c = contracts.team_to_win("100-HOME", 100, "HOME")
    receipt = SettlementEngine().settle(c, ev, settled_at=123)
    assert receipt.resolution == "YES"
    assert receipt.result["home_goals"] == 2 and receipt.result["away_goals"] == 1
    assert receipt.evidence_digest == ev["evidence_digest"]
    assert receipt.receipt_hash  # non-empty
    assert receipt.settled_at == 123


def test_receipt_hash_is_deterministic_and_excludes_wall_clock():
    ev = _evidence()
    c = contracts.match_draw("100-DRAW", 100)
    a = SettlementEngine().settle(c, ev, settled_at=1)
    b = SettlementEngine().settle(c, ev, settled_at=999999)
    assert a.receipt_hash == b.receipt_hash  # settled_at is not part of the fingerprint


def test_receipt_hash_matches_standalone_recompute():
    ev = _evidence()
    c = contracts.total_over("100-O25", 100, 2.5)
    r = SettlementEngine().settle(c, ev)
    recomputed = compute_receipt_hash(
        r.receipt_version, r.rule_id, r.contract, r.evidence_digest, r.result, r.resolution
    )
    assert recomputed == r.receipt_hash


def test_fixture_mismatch_is_rejected():
    ev = _evidence(fixture_id=100)
    wrong = contracts.team_to_win("x", 200, "HOME")  # contract for a different fixture
    with pytest.raises(FixtureMismatchError):
        SettlementEngine().settle(wrong, ev)


def test_doctored_evidence_digest_is_rejected():
    ev = _evidence()
    ev = dict(ev)
    ev["home_goals"] = 7           # tamper the body...
    # ...but leave the stated digest pointing at the old contents.
    with pytest.raises(ValueError):
        SettlementEngine().settle(contracts.team_to_win("x", 100, "HOME"), ev)


def test_settle_computes_digest_when_bundle_has_none():
    ev = _evidence()
    ev = dict(ev)
    ev.pop("evidence_digest")  # e.g. a bundle from an older capture
    receipt = SettlementEngine().settle(contracts.match_draw("x", 100), ev)
    assert receipt.evidence_digest  # recomputed
