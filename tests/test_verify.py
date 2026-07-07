"""Independent verification and dispute resolution — the heart of the oracle.

Covers the easy tamper (doctored evidence body) *and* the hard one: a fully
self-consistent forged receipt, which only the audit-chain, on-chain-anchor
and source-reverification layers can catch."""
from __future__ import annotations

import copy
from dataclasses import asdict

from settlement_oracle import contracts, verify as verify_mod
from settlement_oracle.engine import SettlementEngine
from settlement_oracle.feeds.txline_results import build_evidence_from_snapshot
from settlement_oracle.ledger import SettlementLedger
from settlement_oracle.types import SettlementReceipt
from settlement_oracle.verify import verify_settlement


def _evidence(fixture_id=100, p1=2, p2=1):
    rec = {
        "FixtureId": fixture_id, "Ts": 5, "Seq": 5, "Action": "game_finalised",
        "Participant1IsHome": True, "Participant1Id": 11, "Participant2Id": 22,
        "CompetitionId": 72, "StartTime": 1000,
        "Score": {"Participant1": {"Total": {"Goals": p1}},
                  "Participant2": {"Total": {"Goals": p2}}},
    }
    return build_evidence_from_snapshot(fixture_id, [rec], competition_id=72)


def _genuine():
    ev = _evidence()
    c = contracts.team_to_win("100-HOME", 100, "HOME")
    receipt = SettlementEngine().settle(c, ev)
    led = SettlementLedger()
    led.record_settlement(receipt)
    return receipt, led


# -- happy path -------------------------------------------------------------


def test_all_offline_layers_pass_for_a_genuine_settlement():
    receipt, led = _genuine()
    report = verify_settlement(receipt, ledger=led)
    assert report.verified is True
    assert report.evidence_integrity
    assert report.settlement_replay
    assert report.receipt_fingerprint
    assert report.audit_chain


def test_accepts_a_dict_receipt():
    receipt, _ = _genuine()
    report = verify_settlement(asdict(receipt))
    assert report.verified is True


# -- easy tamper: doctored evidence body -----------------------------------


def test_doctored_evidence_fails_integrity_and_replay():
    receipt, _ = _genuine()
    forged = asdict(receipt)
    forged["evidence"] = dict(forged["evidence"])
    forged["evidence"]["home_goals"] = 9  # change only the body
    report = verify_settlement(SettlementReceipt(**forged))
    assert report.evidence_integrity is False
    assert report.settlement_replay is False
    assert report.verified is False


# -- hard tamper: fully self-consistent forged receipt ---------------------


def test_self_consistent_forgery_is_caught_by_the_audit_chain():
    """A forger who recomputes the digest, result and receipt hash produces a
    receipt that passes every *self*-check — but it is not in the real
    ledger, so the chain layer rejects it."""
    _, real_led = _genuine()
    # Forge a receipt for a fabricated 5-0 result, internally consistent.
    fake_ev = _evidence(p1=5, p2=0)
    forged = SettlementEngine().settle(contracts.team_to_win("100-HOME", 100, "HOME"), fake_ev)

    self_report = verify_settlement(forged)  # no ledger: only self-checks
    assert self_report.evidence_integrity and self_report.receipt_fingerprint
    assert self_report.settlement_replay  # internally consistent!
    assert self_report.verified  # ...and so it *passes* without external anchors

    # But bound to the real ledger, it is exposed.
    bound = verify_settlement(forged, ledger=real_led)
    assert bound.audit_chain is False
    assert bound.verified is False


# -- source re-verification (dispute mode) ---------------------------------


class _FakeAdapter:
    def __init__(self, home_goals, away_goals):
        self._hg, self._ag = home_goals, away_goals
        self.calls = []

    def fetch_result_evidence(self, fixture_id, p1_name=None, p2_name=None):
        self.calls.append(fixture_id)
        return _evidence(fixture_id=fixture_id, p1=self._hg, p2=self._ag)


def test_source_reverify_passes_when_live_feed_agrees():
    receipt, led = _genuine()
    adapter = _FakeAdapter(2, 1)  # matches the receipt
    report = verify_settlement(receipt, ledger=led, reverify_source=True, results_adapter=adapter)
    assert report.source_reverify is True
    assert report.verified is True
    assert adapter.calls == [100]


def test_source_reverify_exposes_a_forgery_the_chain_would_miss():
    # Forged receipt + a forged ledger that contains it: chain looks fine...
    fake_ev = _evidence(p1=5, p2=0)
    forged = SettlementEngine().settle(contracts.team_to_win("100-HOME", 100, "HOME"), fake_ev)
    forged_led = SettlementLedger()
    forged_led.record_settlement(forged)
    # ...but the live feed still reports the true 2-1, so source reverify fails.
    adapter = _FakeAdapter(2, 1)
    report = verify_settlement(forged, ledger=forged_led, reverify_source=True, results_adapter=adapter)
    assert report.audit_chain is True          # forged ledger is self-consistent
    assert report.source_reverify is False     # but the source disagrees
    assert report.verified is False


# -- on-chain anchor layer (verify_anchor monkeypatched, no network) --------


def test_onchain_anchor_layer_passes(monkeypatch):
    receipt, led = _genuine()
    from settlement_oracle.anchor.solana_anchor import AnchorVerification

    seen = {}

    def fake_verify(tx_sig, expected_hash, **kw):
        seen["tx_sig"] = tx_sig
        seen["expected_hash"] = expected_hash
        return AnchorVerification(verified=True, tx_sig=tx_sig, slot=7,
                                  memo="settlement-oracle:v1:" + expected_hash)

    monkeypatch.setattr(verify_mod, "verify_anchor", fake_verify)
    report = verify_settlement(receipt, ledger=led, tx_sig="TXSIG")
    assert report.onchain_anchor is True
    assert seen["expected_hash"] == led.chain_head  # anchored the ledger head
    assert report.verified is True


def test_onchain_anchor_mismatch_fails(monkeypatch):
    receipt, led = _genuine()
    from settlement_oracle.anchor.solana_anchor import AnchorVerification

    monkeypatch.setattr(
        verify_mod, "verify_anchor",
        lambda tx_sig, expected_hash, **kw: AnchorVerification(
            verified=False, tx_sig=tx_sig, error="no matching memo"),
    )
    report = verify_settlement(receipt, ledger=led, tx_sig="TXSIG")
    assert report.onchain_anchor is False
    assert report.verified is False


def test_tx_sig_without_ledger_is_rejected():
    receipt, _ = _genuine()
    report = verify_settlement(receipt, tx_sig="TXSIG")
    assert report.onchain_anchor is False


# -- robustness -------------------------------------------------------------


def test_verify_never_raises_on_garbage_receipt():
    junk = {
        "receipt_version": 1, "rule_id": "settlement-rules/v1",
        "contract": {"contract_id": "x", "fixture_id": 1, "kind": "TEAM_WIN", "params": {}},
        "evidence": {"fixture_id": 1}, "evidence_digest": "deadbeef",
        "result": {}, "resolution": "YES", "receipt_hash": "nope",
    }
    report = verify_settlement(junk)
    assert report.verified is False
    assert report.errors  # captured, not raised


def test_unknown_rule_id_cannot_be_replayed():
    receipt, led = _genuine()
    forged = asdict(receipt)
    forged["rule_id"] = "settlement-rules/v999"
    report = verify_settlement(SettlementReceipt(**forged))
    assert report.settlement_replay is False
