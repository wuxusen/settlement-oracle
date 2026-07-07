"""Tamper-evident settlement ledger (hash chain)."""
from __future__ import annotations

from settlement_oracle import contracts
from settlement_oracle.engine import SettlementEngine
from settlement_oracle.feeds.txline_results import build_evidence_from_snapshot
from settlement_oracle.ledger import GENESIS, SettlementLedger


def _receipt(fixture_id=100, p1=2, p2=1, kind="home"):
    rec = {
        "FixtureId": fixture_id, "Ts": 5, "Seq": 5, "Action": "game_finalised",
        "Participant1IsHome": True, "Participant1Id": 11, "Participant2Id": 22,
        "CompetitionId": 72, "StartTime": 1000,
        "Score": {"Participant1": {"Total": {"Goals": p1}},
                  "Participant2": {"Total": {"Goals": p2}}},
    }
    ev = build_evidence_from_snapshot(fixture_id, [rec], competition_id=72)
    c = (contracts.team_to_win(f"{fixture_id}-H", fixture_id, "HOME")
         if kind == "home" else contracts.match_draw(f"{fixture_id}-D", fixture_id))
    return SettlementEngine().settle(c, ev)


def test_empty_ledger_head_is_genesis():
    assert SettlementLedger().chain_head == GENESIS


def test_chain_extends_and_verifies():
    led = SettlementLedger()
    led.record_settlement(_receipt(100))
    led.record_settlement(_receipt(101))
    assert len(led.records) == 2
    assert led.chain_head != GENESIS
    assert led.verify_chain() is True


def test_each_record_links_to_previous():
    led = SettlementLedger()
    r0 = led.record_settlement(_receipt(100))
    r1 = led.record_settlement(_receipt(101))
    assert r0["prev_hash"] == GENESIS
    assert r1["prev_hash"] == r0["hash"]
    assert led.chain_head == r1["hash"]


def test_tampering_with_a_record_breaks_the_chain():
    led = SettlementLedger()
    led.record_settlement(_receipt(100))
    led.record_settlement(_receipt(101))
    led.records[0]["resolution"] = "NO"  # flip a past settlement
    assert led.verify_chain() is False


def test_deleting_a_record_breaks_the_chain():
    led = SettlementLedger()
    led.record_settlement(_receipt(100))
    led.record_settlement(_receipt(101))
    led.record_settlement(_receipt(102))
    del led.records[1]
    assert led.verify_chain() is False


def test_anchor_record_is_chained_and_verifiable():
    led = SettlementLedger()
    led.record_settlement(_receipt(100))
    head = led.chain_head
    rec = led.record_anchor(anchored_hash=head, tx_sig="sig123", slot=42, ts=9)
    assert rec["type"] == "anchor"
    assert rec["anchored_hash"] == head
    assert rec["prev_hash"] == head
    assert led.chain_head == rec["hash"]
    assert led.verify_chain() is True


def test_tampering_with_anchor_record_breaks_chain():
    led = SettlementLedger()
    led.record_settlement(_receipt(100))
    led.record_anchor(anchored_hash=led.chain_head, tx_sig="sig", slot=1, ts=0)
    led.records[-1]["solana_slot"] = 999999
    assert led.verify_chain() is False


def test_from_records_round_trip():
    led = SettlementLedger()
    led.record_settlement(_receipt(100))
    led.record_settlement(_receipt(101))
    rebuilt = SettlementLedger.from_records(led.records)
    assert rebuilt.chain_head == led.chain_head
    assert rebuilt.verify_chain() is True
