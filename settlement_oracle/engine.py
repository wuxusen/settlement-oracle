"""The settlement engine: turn (contract + result evidence) into an
immutable, hashed :class:`SettlementReceipt`.

The engine is deliberately thin — all the judgement lives in the pure
:func:`settlement_oracle.contracts.resolve` rule. The engine's job is to:

1. sanity-check that the evidence is for the contract's fixture,
2. re-derive the match result from the evidence,
3. apply the deterministic rule to get YES/NO,
4. bind it all into a receipt with a reproducible fingerprint hash.

Because the fingerprint hashes only the *deterministic* settlement facts
(contract + evidence digest + resolution + rule id), two independent runs of
the engine on the same inputs produce byte-identical ``receipt_hash`` values.
That reproducibility is what makes the settlement verifiable.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from .contracts import RULE_ID, resolve
from .feeds.txline_results import canonical_digest
from .types import MatchResult, PredictionContract, Resolution, SettlementReceipt

RECEIPT_VERSION = 1


class FixtureMismatchError(ValueError):
    """Evidence bundle is for a different fixture than the contract."""


def compute_receipt_hash(
    receipt_version: int,
    rule_id: str,
    contract: dict,
    evidence_digest: str,
    result: dict,
    resolution: str,
) -> str:
    """Deterministic SHA-256 fingerprint of a settlement.

    Note what is *excluded*: ``settled_at`` (wall clock) and the full evidence
    body. The digest already commits to the evidence; the wall clock is not a
    settlement fact. This keeps the hash reproducible by anyone replaying the
    settlement from the contract and evidence alone.
    """
    body = {
        "receipt_version": receipt_version,
        "rule_id": rule_id,
        "contract": contract,
        "evidence_digest": evidence_digest,
        "result": result,
        "resolution": resolution,
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result_dict(result: MatchResult) -> dict:
    return {
        "fixture_id": result.fixture_id,
        "home": result.home,
        "away": result.away,
        "home_goals": result.home_goals,
        "away_goals": result.away_goals,
        "total_goals": result.total_goals,
        "outcome": result.outcome,
    }


class SettlementEngine:
    """Settles prediction-market contracts against result evidence."""

    def __init__(self, rule_id: str = RULE_ID, receipt_version: int = RECEIPT_VERSION) -> None:
        self.rule_id = rule_id
        self.receipt_version = receipt_version

    def settle(
        self,
        contract: PredictionContract,
        evidence: dict,
        settled_at: Optional[int] = None,
    ) -> SettlementReceipt:
        """Settle ``contract`` against a result-evidence bundle.

        Raises :class:`FixtureMismatchError` if the evidence is for another
        fixture — a settlement oracle must never resolve a contract against
        the wrong match.
        """
        if int(evidence["fixture_id"]) != int(contract.fixture_id):
            raise FixtureMismatchError(
                f"contract fixture {contract.fixture_id} != evidence fixture {evidence['fixture_id']}"
            )

        # Recompute the evidence digest from the bundle itself; if the bundle
        # carries one, it must agree (guards against a doctored bundle whose
        # stated digest no longer matches its contents).
        digest = canonical_digest(evidence)
        stated = evidence.get("evidence_digest")
        if stated is not None and stated != digest:
            raise ValueError(
                "evidence bundle digest mismatch — bundle contents were altered"
            )

        result = MatchResult.from_evidence(evidence)
        resolution: Resolution = resolve(contract, result)

        contract_d = contract.to_dict()
        result_d = _result_dict(result)
        receipt_hash = compute_receipt_hash(
            self.receipt_version, self.rule_id, contract_d, digest, result_d, resolution.value
        )
        return SettlementReceipt(
            receipt_version=self.receipt_version,
            rule_id=self.rule_id,
            contract=contract_d,
            evidence=evidence,
            evidence_digest=digest,
            result=result_d,
            resolution=resolution.value,
            settled_at=settled_at if settled_at is not None else int(time.time() * 1000),
            receipt_hash=receipt_hash,
        )
