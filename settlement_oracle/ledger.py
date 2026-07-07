"""Tamper-evident settlement ledger.

Every settlement receipt is appended as a record carrying a SHA-256 hash
chained to the previous record (git-style). The rolling chain head therefore
transitively commits to the entire settlement history: change any past
receipt and every subsequent hash breaks.

The chain head is what gets anchored to Solana (see :mod:`anchor`). An anchor
confirmation is itself appended as a first-class ``type: "anchor"`` record —
records are immutable once hashed, so an anchor that lands after the fact
cannot be written back into the receipt it commits to; it is a new link that
names the head hash it anchored.

This mirrors how TxODDS itself anchors a Merkle root of its data on-chain:
we anchor a hash-chain head rather than every receipt, at a fraction of the
cost, with the same "verify the whole history from one on-chain commit"
property.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from .types import SettlementReceipt

GENESIS = "0" * 64


class SettlementLedger:
    """Append-only, hash-chained log of settlement receipts and anchors."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._chain_head = GENESIS

    @property
    def chain_head(self) -> str:
        return self._chain_head

    def record_settlement(self, receipt: SettlementReceipt) -> dict:
        """Append a settlement receipt to the chain. Returns the chain record."""
        return self._append(
            {
                "type": "settlement",
                "receipt_hash": receipt.receipt_hash,
                "rule_id": receipt.rule_id,
                "contract_id": receipt.contract["contract_id"],
                "fixture_id": receipt.contract["fixture_id"],
                "kind": receipt.contract["kind"],
                "resolution": receipt.resolution,
                "evidence_digest": receipt.evidence_digest,
                "settled_at": receipt.settled_at,
            }
        )

    def record_anchor(
        self, anchored_hash: str, tx_sig: Optional[str], slot: Optional[int], ts: int
    ) -> dict:
        """Append an on-chain anchor confirmation as its own chain link."""
        return self._append(
            {
                "type": "anchor",
                "anchored_hash": anchored_hash,
                "solana_tx_sig": tx_sig,
                "solana_slot": slot,
                "ts": ts,
            }
        )

    # -- chain internals ---------------------------------------------------

    def _append(self, record: dict) -> dict:
        record = dict(record)
        record["prev_hash"] = self._chain_head
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        record["hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._chain_head = record["hash"]
        self.records.append(record)
        return record

    def verify_chain(self) -> bool:
        """Recompute the whole chain; True iff nothing was tampered with."""
        prev = GENESIS
        for rec in self.records:
            body = {k: v for k, v in rec.items() if k != "hash"}
            if body.get("prev_hash") != prev:
                return False
            payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != rec.get("hash"):
                return False
            prev = rec["hash"]
        return True

    def to_dict(self) -> dict:
        return {"chain_head": self._chain_head, "records": self.records}

    @classmethod
    def from_records(cls, records: list) -> "SettlementLedger":
        """Rebuild a ledger from persisted records (e.g. for verification)."""
        ledger = cls()
        ledger.records = [dict(r) for r in records]
        ledger._chain_head = records[-1]["hash"] if records else GENESIS
        return ledger
