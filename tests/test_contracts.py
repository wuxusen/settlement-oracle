"""Deterministic contract resolution rules."""
from __future__ import annotations

import pytest

from settlement_oracle import contracts
from settlement_oracle.contracts import resolve
from settlement_oracle.types import (
    ContractKind,
    MatchResult,
    PredictionContract,
    Resolution,
)


def R(home_goals, away_goals, home="Home", away="Away", fixture_id=1):
    return MatchResult(fixture_id, home, away, home_goals, away_goals)


# -- 1X2 --------------------------------------------------------------------


@pytest.mark.parametrize(
    "hg,ag,side,expected",
    [
        (2, 1, "HOME", Resolution.YES),
        (2, 1, "AWAY", Resolution.NO),
        (0, 3, "AWAY", Resolution.YES),
        (0, 3, "HOME", Resolution.NO),
        (1, 1, "HOME", Resolution.NO),   # draw -> a team-win contract is NO
        (1, 1, "AWAY", Resolution.NO),
    ],
)
def test_team_win(hg, ag, side, expected):
    c = contracts.team_to_win("c", 1, side)
    assert resolve(c, R(hg, ag)) is expected


@pytest.mark.parametrize("hg,ag,expected", [(1, 1, Resolution.YES), (2, 1, Resolution.NO), (0, 0, Resolution.YES)])
def test_match_draw(hg, ag, expected):
    assert resolve(contracts.match_draw("c", 1), R(hg, ag)) is expected


def test_double_chance_home_or_draw():
    c = contracts.double_chance("c", 1, ["HOME", "DRAW"])
    assert resolve(c, R(2, 0)) is Resolution.YES   # home
    assert resolve(c, R(1, 1)) is Resolution.YES   # draw
    assert resolve(c, R(0, 2)) is Resolution.NO    # away


# -- totals -----------------------------------------------------------------


@pytest.mark.parametrize(
    "hg,ag,line,over_expected",
    [
        (2, 1, 2.5, Resolution.YES),   # 3 > 2.5
        (1, 1, 2.5, Resolution.NO),    # 2 < 2.5
        (2, 1, 3.5, Resolution.NO),    # 3 < 3.5
        (2, 2, 3.5, Resolution.YES),   # 4 > 3.5
    ],
)
def test_total_over_and_under_are_complementary_off_the_half_line(hg, ag, line, over_expected):
    over = resolve(contracts.total_over("o", 1, line), R(hg, ag))
    under = resolve(contracts.total_under("u", 1, line), R(hg, ag))
    assert over is over_expected
    # On a .5 line there is no push, so over and under are strict opposites.
    assert under is (Resolution.NO if over is Resolution.YES else Resolution.YES)


def test_integer_total_line_can_push_to_no_on_both_sides():
    # total == line: neither strictly over nor strictly under.
    r = R(1, 1)  # total 2
    assert resolve(contracts.total_over("o", 1, 2), r) is Resolution.NO
    assert resolve(contracts.total_under("u", 1, 2), r) is Resolution.NO


# -- BTTS / exact score -----------------------------------------------------


@pytest.mark.parametrize(
    "hg,ag,expected",
    [(2, 1, Resolution.YES), (2, 0, Resolution.NO), (0, 0, Resolution.NO), (0, 3, Resolution.NO)],
)
def test_btts(hg, ag, expected):
    assert resolve(contracts.both_teams_to_score("c", 1), R(hg, ag)) is expected


def test_exact_score():
    c = contracts.exact_score("c", 1, 2, 1)
    assert resolve(c, R(2, 1)) is Resolution.YES
    assert resolve(c, R(1, 2)) is Resolution.NO
    assert resolve(c, R(2, 0)) is Resolution.NO


# -- validation -------------------------------------------------------------


def test_bad_side_raises():
    c = PredictionContract("c", 1, ContractKind.TEAM_WIN, {"side": "MIDDLE"})
    with pytest.raises(ValueError):
        resolve(c, R(1, 0))


def test_double_chance_bad_cover_raises():
    c = PredictionContract("c", 1, ContractKind.DOUBLE_CHANCE, {"cover": ["FOO"]})
    with pytest.raises(ValueError):
        resolve(c, R(1, 0))


def test_total_requires_line():
    c = PredictionContract("c", 1, ContractKind.TOTAL_OVER, {})
    with pytest.raises(ValueError):
        resolve(c, R(1, 0))


def test_resolution_is_pure_and_repeatable():
    c = contracts.team_to_win("c", 1, "HOME")
    r = R(3, 2)
    assert resolve(c, r) is resolve(c, r) is Resolution.YES
