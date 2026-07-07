"""AnchorSink: pluggable on-chain (or no-op) commitment for the settlement
ledger's tamper-evident hash chain.

Anchoring is external I/O, and external I/O fails — RPC nodes rate-limit,
devnet faucets run dry, connections time out. The ``AnchorSink.anchor()``
contract therefore *never* raises for expected failure modes: it always
returns an :class:`AnchorResult`. Settling a contract and anchoring the
result are decoupled: a settlement is fully valid and verifiable off its own
receipt hash the moment it is produced; the on-chain anchor is an additional,
best-effort layer of public tamper-evidence, not a precondition.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AnchorResult:
    """Outcome of one attempt to commit a hash to an :class:`AnchorSink`."""

    ok: bool
    head_hash: str
    tx_sig: Optional[str] = None
    slot: Optional[int] = None
    error: Optional[str] = None


class AnchorSink(abc.ABC):
    """Something that can durably, publicly commit to a hash."""

    @abc.abstractmethod
    def anchor(self, head_hash: str) -> AnchorResult:
        """Commit ``head_hash`` (hex SHA-256). Must not raise."""


class NullAnchor(AnchorSink):
    """Anchoring disabled/unconfigured. Always reports failure (not fatal) so
    a caller never mistakes "off" for "confirmed on-chain" — settlements are
    still produced and verifiable off their receipt hashes; they just are not
    additionally anchored on-chain."""

    def anchor(self, head_hash: str) -> AnchorResult:
        return AnchorResult(ok=False, head_hash=head_hash, error="anchoring disabled (NullAnchor)")
