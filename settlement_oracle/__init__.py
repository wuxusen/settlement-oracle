"""Verifiable Settlement Oracle.

A settlement oracle for prediction markets: it takes the authoritative,
final result of a real sporting fixture from the TxODDS TxLINE feed,
deterministically resolves prediction-market contracts against it, hashes
the settlement together with its evidence into a tamper-evident audit chain,
and anchors that chain to the Solana blockchain (devnet). Anyone can then
independently replay the settlement and verify against the on-chain anchor
that it was based on real data and has not been altered.

Sister project to the ``odds-market-maker`` in-play market maker: same real
TxLINE feed, same Solana anchoring, different job. The market maker prices
during a match; this oracle settles it after full time.

Paper / devnet only. No real money, no mainnet.
"""

__version__ = "0.1.0"
