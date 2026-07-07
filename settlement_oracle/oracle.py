"""High-level façade wiring the pieces into one settlement flow.

    result evidence  ->  settle (engine)  ->  record (ledger)  ->  anchor (Solana)

Anchoring is optional and best-effort: with a :class:`NullAnchor` (the
default) the oracle still settles and records receipts into a verifiable hash
chain — you just don't get the extra on-chain tamper-evidence layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .anchor.base import AnchorResult, AnchorSink, NullAnchor
from .engine import SettlementEngine
from .ledger import SettlementLedger
from .types import PredictionContract, SettlementReceipt


@dataclass
class SettlementOutcome:
    """One settle-record-anchor cycle's artifacts."""

    receipt: SettlementReceipt
    ledger_record: dict
    anchor: Optional[AnchorResult] = None
    anchor_record: Optional[dict] = None


class SettlementOracle:
    """Settles contracts, records them to a hash-chained ledger, and anchors
    the ledger head to Solana."""

    def __init__(
        self,
        anchor_sink: Optional[AnchorSink] = None,
        ledger: Optional[SettlementLedger] = None,
        engine: Optional[SettlementEngine] = None,
    ) -> None:
        self.anchor_sink = anchor_sink or NullAnchor()
        self.ledger = ledger or SettlementLedger()
        self.engine = engine or SettlementEngine()

    def settle(
        self,
        contract: PredictionContract,
        evidence: dict,
        anchor: bool = False,
        settled_at: Optional[int] = None,
    ) -> SettlementOutcome:
        """Settle one contract; append to the ledger; optionally anchor the
        new chain head on-chain."""
        receipt = self.engine.settle(contract, evidence, settled_at=settled_at)
        record = self.ledger.record_settlement(receipt)
        outcome = SettlementOutcome(receipt=receipt, ledger_record=record)
        if anchor:
            outcome.anchor, outcome.anchor_record = self._anchor_head()
        return outcome

    def anchor_head(self) -> tuple:
        """Anchor the current ledger head on-chain and append a confirmation
        record. Returns (AnchorResult, anchor_ledger_record | None)."""
        return self._anchor_head()

    def _anchor_head(self):
        head = self.ledger.chain_head
        res = self.anchor_sink.anchor(head)
        rec = None
        if res.ok:
            rec = self.ledger.record_anchor(
                anchored_hash=head, tx_sig=res.tx_sig, slot=res.slot,
                ts=int(time.time() * 1000),
            )
        return res, rec
