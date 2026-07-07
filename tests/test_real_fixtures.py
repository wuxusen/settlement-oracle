"""End-to-end settlement over the committed real TxLINE World Cup results.

Every bundle is settled and independently verified; a handful of spot-checks
pin the expected resolution for named fixtures so a bad capture can't silently
pass."""
from __future__ import annotations

import pytest

from settlement_oracle import contracts
from settlement_oracle.engine import SettlementEngine
from settlement_oracle.oracle import SettlementOracle
from settlement_oracle.store import list_fixtures, load_evidence
from settlement_oracle.verify import verify_settlement

FIXTURES = list_fixtures()
IDS = [f["fixture_id"] for f in FIXTURES]

# Known results captured from the live feed (see data/fixtures/_index.json).
SPOT_CHECKS = {
    18179763: ("Portugal", "Croatia", 2, 1, "HOME"),
    18176123: ("Australia", "Egypt", 1, 1, "DRAW"),
    18188721: ("Paraguay", "France", 0, 1, "AWAY"),
    18185036: ("Canada", "Morocco", 0, 3, "AWAY"),
    17588326: ("Algeria", "Austria", 3, 3, "DRAW"),
}


def test_there_are_fixtures():
    assert len(FIXTURES) >= 20


@pytest.mark.parametrize("fixture_id", IDS)
def test_every_bundle_settles_and_verifies(fixture_id):
    ev = load_evidence(fixture_id)
    oracle = SettlementOracle()
    # Settle the winning-outcome contract for this fixture.
    outcome_side = {"HOME": "HOME", "AWAY": "AWAY"}.get(ev["result"])
    if outcome_side:
        c = contracts.team_to_win(f"{fixture_id}-W", fixture_id, outcome_side)
        expected = "YES"
    else:  # draw
        c = contracts.match_draw(f"{fixture_id}-D", fixture_id)
        expected = "YES"
    out = oracle.settle(c, ev)
    assert out.receipt.resolution == expected
    report = verify_settlement(out.receipt, ledger=oracle.ledger)
    assert report.verified is True


@pytest.mark.parametrize("fixture_id,expected", list(SPOT_CHECKS.items()))
def test_named_fixture_results(fixture_id, expected):
    home, away, hg, ag, result = expected
    ev = load_evidence(fixture_id)
    assert ev["home"] == home and ev["away"] == away
    assert (ev["home_goals"], ev["away_goals"]) == (hg, ag)
    assert ev["result"] == result

    eng = SettlementEngine()
    # team-to-win for the actual winner is YES iff not a draw
    for side in ("HOME", "AWAY"):
        r = eng.settle(contracts.team_to_win(f"{fixture_id}-{side}", fixture_id, side), ev)
        assert r.resolution == ("YES" if result == side else "NO")
    # exact score is always YES against its own result
    cs = eng.settle(contracts.exact_score(f"{fixture_id}-CS", fixture_id, hg, ag), ev)
    assert cs.resolution == "YES"


def test_whole_slate_over_all_fixtures_is_one_verifiable_chain():
    """Settle every fixture's winner into one ledger and verify the chain."""
    oracle = SettlementOracle()
    for f in FIXTURES:
        fid = f["fixture_id"]
        ev = load_evidence(fid)
        side = ev["result"]
        c = (contracts.match_draw(f"{fid}-D", fid) if side == "DRAW"
             else contracts.team_to_win(f"{fid}-W", fid, side))
        oracle.settle(c, ev)
    assert len(oracle.ledger.records) == len(FIXTURES)
    assert oracle.ledger.verify_chain() is True
