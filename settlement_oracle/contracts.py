"""Deterministic contract resolution rules.

:func:`resolve` is the heart of the oracle: a pure function mapping a
(contract, result) pair to YES / NO / VOID. It has no clock, no I/O and no
randomness, so the same inputs always produce the same resolution — which is
exactly what makes a settlement independently replayable and disputable.

``RULE_ID`` versions the rule set. It is embedded in every receipt; a change
to the resolution logic bumps it, so an old receipt is always re-checkable
against the exact rule that produced it.
"""
from __future__ import annotations

from .types import (
    AWAY,
    DRAW,
    HOME,
    ContractKind,
    MatchResult,
    PredictionContract,
    Resolution,
)

RULE_ID = "settlement-rules/v1"


def _yes_no(condition: bool) -> Resolution:
    return Resolution.YES if condition else Resolution.NO


def resolve(contract: PredictionContract, result: MatchResult) -> Resolution:
    """Resolve ``contract`` against ``result``. Pure and deterministic.

    Raises ``ValueError`` for a malformed contract (unknown kind, missing or
    invalid params) rather than guessing — a settlement oracle must never
    silently invent a resolution.
    """
    kind = contract.kind
    p = contract.params

    if kind is ContractKind.TEAM_WIN:
        side = str(p.get("side", "")).upper()
        if side not in (HOME, AWAY):
            raise ValueError(f"TEAM_WIN requires params.side in HOME/AWAY, got {side!r}")
        return _yes_no(result.outcome == side)

    if kind is ContractKind.MATCH_DRAW:
        return _yes_no(result.outcome == DRAW)

    if kind is ContractKind.DOUBLE_CHANCE:
        cover = {str(o).upper() for o in p.get("cover", [])}
        if not cover or not cover.issubset({HOME, DRAW, AWAY}):
            raise ValueError(f"DOUBLE_CHANCE requires params.cover subset of 1X2, got {cover!r}")
        return _yes_no(result.outcome in cover)

    if kind in (ContractKind.TOTAL_OVER, ContractKind.TOTAL_UNDER):
        if "line" not in p:
            raise ValueError(f"{kind.value} requires params.line")
        line = float(p["line"])
        total = result.total_goals
        if kind is ContractKind.TOTAL_OVER:
            return _yes_no(total > line)
        return _yes_no(total < line)

    if kind is ContractKind.BTTS:
        return _yes_no(result.home_goals > 0 and result.away_goals > 0)

    if kind is ContractKind.EXACT_SCORE:
        if "home" not in p or "away" not in p:
            raise ValueError("EXACT_SCORE requires params.home and params.away")
        return _yes_no(
            result.home_goals == int(p["home"]) and result.away_goals == int(p["away"])
        )

    raise ValueError(f"unknown contract kind: {kind!r}")


# -- ergonomic constructors ------------------------------------------------


def team_to_win(contract_id: str, fixture_id: int, side: str, team: str = "") -> PredictionContract:
    side = side.upper()
    label = team or side
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.TEAM_WIN,
        params={"side": side},
        description=f"{label} to win (full time)",
    )


def match_draw(contract_id: str, fixture_id: int) -> PredictionContract:
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.MATCH_DRAW,
        params={},
        description="Match to end in a draw (full time)",
    )


def double_chance(contract_id: str, fixture_id: int, cover: list) -> PredictionContract:
    cover = [c.upper() for c in cover]
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.DOUBLE_CHANCE,
        params={"cover": cover},
        description=f"Double chance {'/'.join(cover)} (full time)",
    )


def total_over(contract_id: str, fixture_id: int, line: float) -> PredictionContract:
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.TOTAL_OVER,
        params={"line": line},
        description=f"Over {line} total goals (full time)",
    )


def total_under(contract_id: str, fixture_id: int, line: float) -> PredictionContract:
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.TOTAL_UNDER,
        params={"line": line},
        description=f"Under {line} total goals (full time)",
    )


def both_teams_to_score(contract_id: str, fixture_id: int) -> PredictionContract:
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.BTTS,
        params={},
        description="Both teams to score (full time)",
    )


def exact_score(contract_id: str, fixture_id: int, home: int, away: int) -> PredictionContract:
    return PredictionContract(
        contract_id=contract_id,
        fixture_id=fixture_id,
        kind=ContractKind.EXACT_SCORE,
        params={"home": home, "away": away},
        description=f"Exact score {home}-{away} (full time)",
    )
