"""Edge cases: draws, refusal to settle unfinished/postponed fixtures,
multi-fixture ledgers, and boundary totals."""
from __future__ import annotations

import pytest

from settlement_oracle import contracts
from settlement_oracle.engine import SettlementEngine
from settlement_oracle.feeds.txline_results import (
    NotFinalisedError,
    build_evidence_from_snapshot,
)
from settlement_oracle.oracle import SettlementOracle


def _final(fixture_id, p1, p2, p1_home=True):
    rec = {
        "FixtureId": fixture_id, "Ts": 5, "Seq": 5, "Action": "game_finalised",
        "Participant1IsHome": p1_home, "Participant1Id": 11, "Participant2Id": 22,
        "CompetitionId": 72, "StartTime": 1000,
        "Score": {"Participant1": {"Total": {"Goals": p1}},
                  "Participant2": {"Total": {"Goals": p2}}},
    }
    return build_evidence_from_snapshot(fixture_id, [rec], competition_id=72)


def test_draw_makes_both_team_win_contracts_no_and_draw_yes():
    ev = _final(1, 1, 1)
    eng = SettlementEngine()
    assert eng.settle(contracts.team_to_win("h", 1, "HOME"), ev).resolution == "NO"
    assert eng.settle(contracts.team_to_win("a", 1, "AWAY"), ev).resolution == "NO"
    assert eng.settle(contracts.match_draw("d", 1), ev).resolution == "YES"


def test_oracle_refuses_to_settle_a_postponed_fixture():
    """A fixture that was scheduled/kicked off but has no game_finalised (e.g.
    postponed or abandoned mid-game) cannot be settled."""
    snapshot = [
        {"FixtureId": 7, "Ts": 1, "Seq": 1, "Action": "kick_off",
         "Participant1IsHome": True, "Participant1Id": 1, "Participant2Id": 2},
        {"FixtureId": 7, "Ts": 2, "Seq": 2, "Action": "goal",
         "Participant1IsHome": True, "Participant1Id": 1, "Participant2Id": 2,
         "Score": {"Participant1": {"Total": {"Goals": 1}}}},
    ]
    with pytest.raises(NotFinalisedError):
        build_evidence_from_snapshot(7, snapshot)


def test_rescheduled_kickoff_time_does_not_change_the_settlement():
    """If a fixture is replayed at a new StartTime, the result — not the
    schedule — is what settles. Two bundles with different StartTime but the
    same score and ids settle identically."""
    a = _final(9, 2, 0)
    b = dict(_final(9, 2, 0))
    b["start_time"] = a["start_time"] + 86_400_000  # moved a day later
    eng = SettlementEngine()
    ra = eng.settle(contracts.team_to_win("h", 9, "HOME"), a)
    # b's digest recomputes off score/ids/finalised, not start_time -> same
    rb = eng.settle(contracts.team_to_win("h", 9, "HOME"), b)
    assert ra.resolution == rb.resolution == "YES"
    assert ra.evidence_digest == rb.evidence_digest


def test_multi_fixture_ledger_keeps_contracts_isolated():
    oracle = SettlementOracle()
    ev1 = _final(100, 2, 1)   # home win
    ev2 = _final(200, 0, 2)   # away win
    oracle.settle(contracts.team_to_win("100-H", 100, "HOME"), ev1)
    oracle.settle(contracts.team_to_win("200-H", 200, "HOME"), ev2)
    assert oracle.ledger.records[0]["resolution"] == "YES"
    assert oracle.ledger.records[1]["resolution"] == "NO"
    assert oracle.ledger.verify_chain() is True


def test_high_scoring_totals():
    ev = _final(1, 4, 3)  # 7 goals
    eng = SettlementEngine()
    assert eng.settle(contracts.total_over("o", 1, 6.5), ev).resolution == "YES"
    assert eng.settle(contracts.total_under("u", 1, 6.5), ev).resolution == "NO"
    assert eng.settle(contracts.both_teams_to_score("b", 1), ev).resolution == "YES"
