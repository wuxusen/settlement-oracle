"""The AnchorSink contract — no optional dependencies, always runs."""
from __future__ import annotations

from settlement_oracle.anchor import AnchorResult, NullAnchor


def test_null_anchor_never_reports_success():
    result = NullAnchor().anchor("ab" * 32)
    assert isinstance(result, AnchorResult)
    assert result.ok is False
    assert result.tx_sig is None


def test_null_anchor_echoes_head_hash():
    h = "cd" * 32
    assert NullAnchor().anchor(h).head_hash == h
