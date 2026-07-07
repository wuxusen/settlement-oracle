"""Independent settlement verification — the oracle's dispute-resolution core.

``verify_settlement`` takes a settlement receipt (and, optionally, the ledger
it lives in and the Solana anchor transaction) and re-checks it from scratch,
trusting nothing the producer said. It answers, in layers:

1. **Evidence integrity** — does the embedded evidence still hash to the
   digest the receipt claims? (Catches a doctored evidence bundle.)
2. **Settlement replay** — re-deriving the match result from the evidence and
   re-running the deterministic rule reproduces the exact YES/NO the receipt
   asserts. (Catches a wrong or invented resolution.)
3. **Receipt fingerprint** — recomputing the receipt hash matches. (Catches
   any tampering with the receipt's bound fields.)
4. **Audit chain** — the ledger's hash chain is intact and actually contains
   this receipt. (Catches deletion/reordering/insertion of settlements.)
5. **On-chain anchor** — the Solana transaction's memo commits to the ledger
   head. (Catches after-the-fact rewriting of anchored history.)
6. **Source re-verification** (optional, the strongest) — re-fetch the result
   straight from the live TxLINE feed and confirm it still yields the same
   outcome. (Catches a fabricated-but-internally-consistent evidence bundle.)

A dispute is resolved by whoever runs this: if any layer fails, the
settlement is not valid; if they all pass, the settlement is provably based
on real, unaltered data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .anchor.solana_anchor import verify_anchor
from .contracts import RULE_ID, resolve
from .engine import compute_receipt_hash
from .feeds.txline_results import canonical_digest
from .types import MatchResult, PredictionContract, SettlementReceipt

# Rule sets the verifier knows how to replay, keyed by the rule_id a receipt
# was produced under. Adding a new rule version registers it here so old
# receipts remain replayable against the exact rule that settled them.
_RULE_REGISTRY = {RULE_ID: resolve}


@dataclass
class VerificationReport:
    """Structured result of :func:`verify_settlement`. ``verified`` is the AND
    of every check that was actually run."""

    verified: bool = False
    evidence_integrity: Optional[bool] = None
    settlement_replay: Optional[bool] = None
    receipt_fingerprint: Optional[bool] = None
    audit_chain: Optional[bool] = None
    onchain_anchor: Optional[bool] = None
    source_reverify: Optional[bool] = None
    checks: list = field(default_factory=list)  # [(name, ok, detail)]
    errors: list = field(default_factory=list)

    def _add(self, name: str, ok: Optional[bool], detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})

    def as_dict(self) -> dict:
        return {
            "verified": self.verified,
            "checks": self.checks,
            "errors": self.errors,
        }


def _as_receipt(receipt) -> SettlementReceipt:
    if isinstance(receipt, SettlementReceipt):
        return receipt
    return SettlementReceipt(**receipt)


def verify_settlement(
    receipt,
    ledger=None,
    tx_sig: Optional[str] = None,
    rpc_url: Optional[str] = None,
    reverify_source: bool = False,
    results_adapter=None,
) -> VerificationReport:
    """Independently verify a settlement. Never raises — every failure is
    captured as a failed check so a verifier always gets a full report.

    Parameters
    ----------
    receipt : SettlementReceipt | dict
        The settlement to verify.
    ledger : SettlementLedger | None
        If given, its hash chain is verified and required to contain the
        receipt.
    tx_sig : str | None
        If given, the Solana anchor transaction whose memo must commit to the
        ledger's chain head (requires ``ledger``).
    rpc_url : str | None
        Solana RPC endpoint for anchor verification (defaults to devnet).
    reverify_source : bool
        If True, re-fetch the result from the live TxLINE feed via
        ``results_adapter`` and confirm it matches the receipt.
    results_adapter : TxLineResultsAdapter | None
        Live results source for ``reverify_source``.
    """
    report = VerificationReport()
    r = _as_receipt(receipt)

    # 1. Evidence integrity ------------------------------------------------
    try:
        recomputed_digest = canonical_digest(r.evidence)
        ok = recomputed_digest == r.evidence_digest
        report.evidence_integrity = ok
        report._add(
            "evidence_integrity", ok,
            "evidence bundle hashes to the receipt's digest" if ok
            else f"digest mismatch: {recomputed_digest} != {r.evidence_digest}",
        )
    except Exception as exc:  # noqa: BLE001
        report.evidence_integrity = False
        report.errors.append(f"evidence_integrity: {type(exc).__name__}: {exc}")
        report._add("evidence_integrity", False, str(exc))

    # 2. Settlement replay -------------------------------------------------
    try:
        rule = _RULE_REGISTRY.get(r.rule_id)
        if rule is None:
            report.settlement_replay = False
            report._add("settlement_replay", False, f"unknown rule_id {r.rule_id!r}")
        else:
            contract = PredictionContract.from_dict(r.contract)
            result = MatchResult.from_evidence(r.evidence)
            replay_res = rule(contract, result)
            result_matches = _result_dict_matches(r.result, result)
            ok = replay_res.value == r.resolution and result_matches
            report.settlement_replay = ok
            if ok:
                detail = f"replayed {replay_res.value} for '{contract.description}'"
            elif not result_matches:
                detail = (
                    f"result mismatch: evidence replays "
                    f"{result.home_goals}-{result.away_goals} ({result.outcome}), "
                    f"receipt claims {r.result.get('home_goals')}-{r.result.get('away_goals')} "
                    f"({r.result.get('outcome')})"
                )
            else:
                detail = f"resolution mismatch: replay {replay_res.value} != receipt {r.resolution}"
            report._add("settlement_replay", ok, detail)
    except Exception as exc:  # noqa: BLE001
        report.settlement_replay = False
        report.errors.append(f"settlement_replay: {type(exc).__name__}: {exc}")
        report._add("settlement_replay", False, str(exc))

    # 3. Receipt fingerprint ----------------------------------------------
    try:
        recomputed = compute_receipt_hash(
            r.receipt_version, r.rule_id, r.contract, r.evidence_digest, r.result, r.resolution
        )
        ok = recomputed == r.receipt_hash
        report.receipt_fingerprint = ok
        report._add(
            "receipt_fingerprint", ok,
            "receipt hash reproduces" if ok else f"hash mismatch: {recomputed} != {r.receipt_hash}",
        )
    except Exception as exc:  # noqa: BLE001
        report.receipt_fingerprint = False
        report.errors.append(f"receipt_fingerprint: {type(exc).__name__}: {exc}")
        report._add("receipt_fingerprint", False, str(exc))

    # 4. Audit chain -------------------------------------------------------
    if ledger is not None:
        try:
            chain_ok = ledger.verify_chain()
            contains = any(
                rec.get("type") == "settlement" and rec.get("receipt_hash") == r.receipt_hash
                for rec in ledger.records
            )
            ok = chain_ok and contains
            report.audit_chain = ok
            report._add(
                "audit_chain", ok,
                "chain intact and contains this receipt" if ok
                else f"chain_intact={chain_ok} contains_receipt={contains}",
            )
        except Exception as exc:  # noqa: BLE001
            report.audit_chain = False
            report.errors.append(f"audit_chain: {type(exc).__name__}: {exc}")
            report._add("audit_chain", False, str(exc))

    # 5. On-chain anchor ---------------------------------------------------
    if tx_sig is not None:
        try:
            if ledger is None:
                report.onchain_anchor = False
                report._add("onchain_anchor", False, "tx_sig given but no ledger to bind it to")
            else:
                kwargs = {"rpc_url": rpc_url} if rpc_url else {}
                v = verify_anchor(tx_sig, ledger.chain_head, **kwargs)
                report.onchain_anchor = v.verified
                report._add(
                    "onchain_anchor", v.verified,
                    f"memo commits to chain head (slot {v.slot})" if v.verified
                    else f"anchor mismatch: {v.error or v.memo}",
                )
        except Exception as exc:  # noqa: BLE001
            report.onchain_anchor = False
            report.errors.append(f"onchain_anchor: {type(exc).__name__}: {exc}")
            report._add("onchain_anchor", False, str(exc))

    # 6. Source re-verification -------------------------------------------
    if reverify_source and results_adapter is not None:
        try:
            fresh = results_adapter.fetch_result_evidence(int(r.contract["fixture_id"]))
            fresh_result = MatchResult.from_evidence(fresh)
            same_score = (
                fresh_result.home_goals == r.result["home_goals"]
                and fresh_result.away_goals == r.result["away_goals"]
            )
            report.source_reverify = same_score
            report._add(
                "source_reverify", same_score,
                f"live TxLINE still reports {fresh_result.home_goals}-{fresh_result.away_goals}"
                if same_score else
                f"live TxLINE reports {fresh_result.home_goals}-{fresh_result.away_goals}, "
                f"receipt says {r.result['home_goals']}-{r.result['away_goals']}",
            )
        except Exception as exc:  # noqa: BLE001
            report.source_reverify = False
            report.errors.append(f"source_reverify: {type(exc).__name__}: {exc}")
            report._add("source_reverify", False, str(exc))

    ran = [c["ok"] for c in report.checks if c["ok"] is not None]
    report.verified = bool(ran) and all(ran)
    return report


def _result_dict_matches(claimed: dict, result: MatchResult) -> bool:
    return (
        int(claimed.get("home_goals")) == result.home_goals
        and int(claimed.get("away_goals")) == result.away_goals
        and claimed.get("outcome") == result.outcome
    )
