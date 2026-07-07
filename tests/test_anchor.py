"""Solana devnet anchoring: sink contract, Memo packing, and the anchor +
verify path against a mocked RPC client — no real network traffic.

The Solana-specific tests are skipped (not failed) if the optional
``solders``/``bip_utils`` dependencies aren't installed; the sink-contract
test always runs."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

# -- Solana-specific (needs solders/bip_utils) ------------------------------
# The dependency-free AnchorSink contract test lives in test_anchor_contract.py
# so it runs even where the optional Solana stack is absent.

pytest.importorskip("solders", reason="Solana Memo packing needs solders")
pytest.importorskip("bip_utils", reason="key derivation needs bip_utils")

from settlement_oracle.anchor.solana_anchor import (  # noqa: E402
    ENV_MNEMONIC_FILE,
    MEMO_PREFIX,
    MEMO_PROGRAM_ID,
    SolanaAnchor,
    build_anchor_transaction,
    build_memo_instruction,
    keypair_from_mnemonic,
    verify_anchor,
)

# Well-known public BIP-39 test vector — NOT a real/funded wallet.
TEST_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
TEST_MNEMONIC_EXPECTED_PUBKEY = "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"


def test_memo_prefix_is_settlement_specific():
    assert MEMO_PREFIX == "settlement-oracle:v1:"


def test_memo_instruction_targets_memo_program():
    from solders.keypair import Keypair

    kp = Keypair()
    ix = build_memo_instruction(kp.pubkey(), b"hello")
    assert str(ix.program_id) == MEMO_PROGRAM_ID
    assert bytes(ix.data) == b"hello"
    assert ix.accounts[0].is_signer is True


def test_anchor_transaction_is_deterministic_given_fixed_blockhash():
    from solders.hash import Hash
    from solders.keypair import Keypair

    kp, bh = Keypair(), Hash.default()
    head = hashlib.sha256(b"chain-head").hexdigest()
    ix = build_memo_instruction(kp.pubkey(), (MEMO_PREFIX + head).encode())
    assert bytes(build_anchor_transaction(ix, kp, bh)) == bytes(build_anchor_transaction(ix, kp, bh))


def test_keypair_from_mnemonic_matches_known_vector():
    assert str(keypair_from_mnemonic(TEST_MNEMONIC).pubkey()) == TEST_MNEMONIC_EXPECTED_PUBKEY


def test_load_mnemonic_from_file_ignores_other_lines(tmp_path):
    from settlement_oracle.anchor.solana_anchor import _load_mnemonic_from_file

    note = tmp_path / "wallet.txt"
    note.write_text(f"label: my devnet wallet\naddress: Xyz\nmnemonic: {TEST_MNEMONIC}\n")
    assert _load_mnemonic_from_file(str(note)) == TEST_MNEMONIC


class _FakeAsyncClient:
    """Duck-typed stand-in for solana.rpc.async_api.AsyncClient. No sockets."""

    def __init__(self, blockhash, sig, slot=None, fail_send=False, fail_confirm=False, tx_for_get=None):
        self._blockhash = blockhash
        self._sig = sig
        self._slot = slot
        self._fail_send = fail_send
        self._fail_confirm = fail_confirm
        self._tx_for_get = tx_for_get
        self.closed = False
        self.sent = []

    async def get_latest_blockhash(self, commitment=None):
        return SimpleNamespace(value=SimpleNamespace(blockhash=self._blockhash))

    async def send_raw_transaction(self, txn, opts=None):
        self.sent.append(txn)
        if self._fail_send:
            raise ConnectionError("devnet RPC unreachable")
        return SimpleNamespace(value=self._sig)

    async def confirm_transaction(self, tx_sig, commitment=None):
        if self._fail_confirm:
            raise TimeoutError("confirmation timed out")
        return SimpleNamespace(value=[SimpleNamespace(slot=self._slot)])

    async def get_transaction(self, tx_sig, encoding="base64", commitment=None, max_supported_transaction_version=None):
        if self._tx_for_get is None:
            return SimpleNamespace(value=None)
        return SimpleNamespace(value=SimpleNamespace(
            transaction=SimpleNamespace(transaction=self._tx_for_get), slot=self._slot))

    async def close(self):
        self.closed = True


def _kp_bh():
    from solders.hash import Hash
    from solders.keypair import Keypair

    return Keypair(), Hash.default()


def test_anchor_success_returns_sig_and_slot():
    from solders.signature import Signature

    kp, bh = _kp_bh()
    sig = Signature.default()
    client = _FakeAsyncClient(blockhash=bh, sig=sig, slot=42)
    result = SolanaAnchor(kp, client=client).anchor("cd" * 32)
    assert result.ok is True
    assert result.tx_sig == str(sig)
    assert result.slot == 42
    assert len(client.sent) == 1


def test_anchor_send_failure_degrades_without_raising():
    from solders.signature import Signature

    kp, bh = _kp_bh()
    client = _FakeAsyncClient(blockhash=bh, sig=Signature.default(), fail_send=True)
    result = SolanaAnchor(kp, client=client).anchor("ef" * 32)
    assert result.ok is False
    assert result.tx_sig is None
    assert result.error


def test_anchor_confirm_timeout_still_returns_signature():
    from solders.signature import Signature

    kp, bh = _kp_bh()
    sig = Signature.default()
    client = _FakeAsyncClient(blockhash=bh, sig=sig, fail_confirm=True)
    result = SolanaAnchor(kp, client=client).anchor("11" * 32)
    assert result.ok is True
    assert result.tx_sig == str(sig)
    assert result.slot is None


def test_verify_anchor_true_for_matching_memo():
    from solders.signature import Signature

    kp, bh = _kp_bh()
    head = hashlib.sha256(b"chain-head").hexdigest()
    ix = build_memo_instruction(kp.pubkey(), (MEMO_PREFIX + head).encode())
    tx = build_anchor_transaction(ix, kp, bh)
    client = _FakeAsyncClient(blockhash=bh, sig=Signature.default(), slot=7, tx_for_get=tx)
    v = verify_anchor(str(Signature.default()), head, client=client)
    assert v.verified is True
    assert v.slot == 7
    assert v.memo == MEMO_PREFIX + head


def test_verify_anchor_false_for_mismatched_hash():
    from solders.signature import Signature

    kp, bh = _kp_bh()
    head = hashlib.sha256(b"real").hexdigest()
    other = hashlib.sha256(b"tampered").hexdigest()
    ix = build_memo_instruction(kp.pubkey(), (MEMO_PREFIX + head).encode())
    tx = build_anchor_transaction(ix, kp, bh)
    client = _FakeAsyncClient(blockhash=bh, sig=Signature.default(), tx_for_get=tx)
    assert verify_anchor(str(Signature.default()), other, client=client).verified is False


def test_verify_anchor_handles_transaction_not_found():
    from solders.signature import Signature

    client = _FakeAsyncClient(blockhash=None, sig=None, tx_for_get=None)
    v = verify_anchor(str(Signature.default()), "ab" * 32, client=client)
    assert v.verified is False and v.error


def test_verify_anchor_never_raises_on_rpc_error():
    from solders.signature import Signature

    class _Exploding(_FakeAsyncClient):
        async def get_transaction(self, *a, **kw):
            raise ConnectionError("rpc down")

    v = verify_anchor(str(Signature.default()), "ab" * 32, client=_Exploding(None, None))
    assert v.verified is False
    assert "rpc down" in (v.error or "")


def test_verify_anchor_never_raises_on_malformed_signature():
    v = verify_anchor("not-a-signature", "ab" * 32, client=_FakeAsyncClient(None, None))
    assert v.verified is False and v.error


def test_from_env_requires_mnemonic_file(monkeypatch):
    monkeypatch.delenv(ENV_MNEMONIC_FILE, raising=False)
    with pytest.raises(RuntimeError):
        SolanaAnchor.from_env()
