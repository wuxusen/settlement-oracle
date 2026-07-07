"""Core domain types for the settlement oracle.

Everything here is a plain, hashable dataclass or enum. The settlement rule
that maps a (contract, result) pair to YES/NO lives in :mod:`contracts` and
is a pure function, so a settlement can be replayed byte-for-byte by anyone.
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Optional

# Canonical 1X2 outcomes.
HOME, DRAW, AWAY = "HOME", "DRAW", "AWAY"
OUTCOMES = (HOME, DRAW, AWAY)


class Resolution(str, enum.Enum):
    """How a binary prediction-market contract resolves against a result."""

    YES = "YES"
    NO = "NO"
    VOID = "VOID"  # contract not applicable (e.g. abandoned fixture)


class ContractKind(str, enum.Enum):
    """The prediction-market contract templates the oracle can settle.

    Each kind has a fixed, deterministic resolution rule (see
    :func:`settlement_oracle.contracts.resolve`). Adding a market means adding
    a kind and a rule branch — never touching the settlement plumbing.
    """

    TEAM_WIN = "TEAM_WIN"            # params: {"side": "HOME"|"AWAY"}
    MATCH_DRAW = "MATCH_DRAW"        # params: {}
    DOUBLE_CHANCE = "DOUBLE_CHANCE"  # params: {"cover": ["HOME","DRAW"]}
    TOTAL_OVER = "TOTAL_OVER"        # params: {"line": 2.5}
    TOTAL_UNDER = "TOTAL_UNDER"      # params: {"line": 2.5}
    BTTS = "BTTS"                    # params: {}  both teams to score
    EXACT_SCORE = "EXACT_SCORE"      # params: {"home": 2, "away": 1}


@dataclass(frozen=True)
class MatchResult:
    """The settled facts of a fixture, derived from result evidence."""

    fixture_id: int
    home: str
    away: str
    home_goals: int
    away_goals: int

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    @property
    def outcome(self) -> str:
        if self.home_goals > self.away_goals:
            return HOME
        if self.away_goals > self.home_goals:
            return AWAY
        return DRAW

    @classmethod
    def from_evidence(cls, evidence: dict) -> "MatchResult":
        return cls(
            fixture_id=int(evidence["fixture_id"]),
            home=str(evidence.get("home", "HOME")),
            away=str(evidence.get("away", "AWAY")),
            home_goals=int(evidence["home_goals"]),
            away_goals=int(evidence["away_goals"]),
        )


@dataclass(frozen=True)
class PredictionContract:
    """A binary prediction-market contract to be settled on one fixture.

    ``params`` carries the kind-specific configuration (which team, which
    total line, which exact score). The contract is fully self-describing, so
    a receipt that embeds it can be re-settled with no external lookup.
    """

    contract_id: str
    fixture_id: int
    kind: ContractKind
    params: dict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PredictionContract":
        return cls(
            contract_id=str(d["contract_id"]),
            fixture_id=int(d["fixture_id"]),
            kind=ContractKind(d["kind"]),
            params=dict(d.get("params", {})),
            description=str(d.get("description", "")),
        )


@dataclass(frozen=True)
class SettlementReceipt:
    """The immutable output of settling one contract.

    Self-contained: it embeds the contract, the underlying evidence bundle and
    its digest, the derived result, and the resolution. ``receipt_hash`` is a
    deterministic fingerprint over the settlement facts (contract +
    evidence digest + resolution + rule), so :func:`verify_settlement` can
    recompute it from nothing but this object.
    """

    receipt_version: int
    rule_id: str
    contract: dict           # PredictionContract.to_dict()
    evidence: dict           # the full result-evidence bundle
    evidence_digest: str     # sha256 over evidence's settlement-relevant fields
    result: dict             # {fixture_id, home, away, home_goals, away_goals, outcome}
    resolution: str          # Resolution value
    settled_at: Optional[int] = None  # wall-clock ms; metadata, NOT hashed
    receipt_hash: str = ""   # filled in by the engine
