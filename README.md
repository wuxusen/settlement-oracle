# Verifiable Settlement Oracle

**Trust-minimized settlement for prediction markets, built on real sports
results and an on-chain proof anyone can check.**

Prediction markets live and die on one question: *when the event is over, who
decides the outcome, and why should anyone believe them?* A market can be
priced perfectly all the way to the whistle and still blow up at settlement if
the resolution is opaque, disputed, or quietly changed after the fact. This
project is a settlement oracle that removes the "just trust us" from that last
step.

It takes the **authoritative final result** of a real football fixture from
the [TxODDS TxLINE](https://txodds.com) feed, resolves prediction-market
contracts against it with a **deterministic rule**, chains every settlement
into a **tamper-evident audit log**, and **anchors** that log to **Solana**.
Given nothing but a settlement receipt and a transaction signature, anyone can
independently replay the settlement and confirm three things:

1. it was computed from the **real result data**, not an edited copy;
2. the YES/NO resolution follows **mechanically** from that data; and
3. the record has **not been altered** since it was committed on-chain.

If any of those fail, the settlement is rejected. That is the whole point.

This is one of a **three-tool suite** I built on the same real TxLINE data, one
for each stage of a match contract's life: the
[in-play market maker](https://github.com/wuxusen/odds-market-maker) prices a
match *while it is being played*,
[Called It](https://github.com/wuxusen/called-it) is a fan play-along layer on
the same live win-probability line, and this oracle settles the contract *once
it is over*. They share the same TxLINE data plumbing and Solana anchoring.

> Paper / devnet only. No real money changes hands, nothing touches Solana
> mainnet, and the oracle never custodies funds. It decides outcomes; payout
> rails are out of scope.

---

## Why this is the hard part of a prediction market

Pricing is a modelling problem. Settlement is a *trust* problem, and it is
where most disputes actually happen:

- **Whose result is canonical?** Two sources can disagree, especially around
  VAR reversals, own goals, walkovers and abandoned matches.
- **When exactly is it final?** A score that looks final at the 90th minute can
  still move. Settling early on a transient state pays out the wrong side.
- **Was the record changed afterwards?** If the settlement log is a mutable
  database row, "the outcome" is whatever the operator last wrote.

The oracle addresses each directly:

- **One canonical source, one canonical moment.** It settles on the TxLINE
  score feed's explicit `game_finalised` action — TxODDS's own "this result is
  final" signal — and ignores everything after it. The included Portugal v
  Croatia fixture is a real example: the feed briefly showed **2-2** after a
  VAR review, the goal was then discarded back to **2-1**, and only
  `game_finalised` confirmed 2-1. A naive "latest score" oracle settles the
  wrong contract; this one gets it right, and the transient state is right
  there in the evidence bundle for anyone to inspect.
- **A deterministic, replayable rule.** Resolution is a pure function of
  (contract, result). No clock, no network, no discretion. Same inputs, same
  answer, forever.
- **Immutability by construction.** Every settlement is a SHA-256 link in a
  hash chain; the chain head is anchored in a Solana transaction. Rewriting any
  past settlement breaks every hash after it and no longer matches the
  on-chain anchor.

---

## Quickstart

```bash
git clone <this-repo> settlement-oracle && cd settlement-oracle
python3 -m pip install -e .          # core has zero runtime dependencies

# One command: real result -> settle -> chain -> verify (and reject a tamper).
./run_demo.sh                        # Portugal v Croatia, the VAR case
./run_demo.sh 18176123               # Australia v Egypt, a 1-1 draw

python3 -m settlement_oracle.cli list          # the 24 captured real fixtures
```

Everything above runs fully offline against real results captured in
`data/fixtures/`. Sample output:

```
  Fixture     Portugal v Croatia  (id 18179763)
  Source      TxODDS TxLINE devnet — scores feed (game_finalised)
  Final score 2-1  →  outcome HOME
  Finalised   TxLINE 'game_finalised' @ ts 1783041221434 seq 1074

  Settling prediction-market contracts against the result:
    Portugal to win (full time)         [ YES]
    Croatia to win (full time)          [ NO ]
    Match to end in a draw (full time)  [ NO ]
    Over 2.5 total goals (full time)    [ YES]
    Both teams to score (full time)     [ YES]
    Exact score 2-1 (full time)         [ YES]

  Independent verification (trusting nothing the oracle said):
    [PASS] evidence_integrity   evidence bundle hashes to the receipt's digest
    [PASS] settlement_replay    replayed YES for 'Portugal to win (full time)'
    [PASS] receipt_fingerprint  receipt hash reproduces
    [PASS] audit_chain          chain intact and contains this receipt
  Verdict     VERIFIED

  Tamper check (flip the score in the evidence and re-verify):
    [FAIL] evidence_integrity   digest mismatch ...
    [FAIL] settlement_replay    result mismatch: evidence replays 7-1 ...
  Tamper verdict  NOT VERIFIED — a doctored result is rejected.
```

### Anchoring on Solana devnet (optional)

Anchoring is opt-in and off by default; the oracle settles and produces fully
verifiable receipts without it. To also commit the audit-chain head on-chain,
point the oracle at a **devnet** wallet note file and pass `--anchor`:

```bash
export SETTLEMENT_ORACLE_SOLANA_MNEMONIC_FILE=/path/to/devnet-wallet-note.txt
python3 -m pip install -e ".[anchor]"     # solana / solders / bip-utils
./run_demo.sh 18179763 --anchor
```

The wallet is only ever read as a **file path**, never as an env value, so the
seed phrase never lands in `.env`, shell history or a process dump. It pays a
single Memo-transaction fee in worthless devnet SOL and nothing else.

You can independently verify any anchored settlement from just its receipt and
the transaction signature:

```bash
python3 -m settlement_oracle.cli verify receipt.json --tx <SOLANA_TX_SIG>
```

---

## How verification works

`verify_settlement()` re-checks a receipt from scratch, trusting nothing the
producer claimed, in layers of increasing strength:

| Layer | Catches |
|-------|---------|
| **evidence integrity** — re-hash the embedded result bundle | a doctored evidence body |
| **settlement replay** — re-derive the result and re-run the rule | a wrong or invented YES/NO |
| **receipt fingerprint** — recompute the receipt hash | tampering with the receipt's bound fields |
| **audit chain** — recompute the hash chain; require it to contain the receipt | deleting, reordering or inserting settlements |
| **on-chain anchor** — the Solana memo must commit to the ledger head | rewriting history after it was anchored |
| **source re-verification** *(dispute mode)* — re-fetch the result live from TxLINE | a fabricated-but-internally-consistent bundle |

The first three prove a receipt is *internally* honest. A determined forger can
fake all three at once by recomputing the digest, result and hash together — so
the last three bind the receipt to something the forger does not control: a
hash chain, a Solana transaction, and the live upstream feed. **Disputing a
settlement means running this function.** If every applicable layer passes, the
settlement is provably based on real, unaltered data; if any fails, it isn't.

---

## Architecture

```
  TxLINE scores feed          data/fixtures/*.json
  (game_finalised)     ─────▶  result-evidence bundle
                                       │
                                       ▼
  contracts.py  ──rule──▶  engine.py  ──▶  SettlementReceipt  (deterministic hash)
                                       │
                                       ▼
                              ledger.py   (SHA-256 hash chain)
                                       │
                                       ▼
                          anchor/  ──▶  Solana devnet Memo tx
                                       │
                                       ▼
                              verify.py   (replay + chain + on-chain + source)
```

| Module | Responsibility |
|--------|----------------|
| `feeds/txline_results.py` | Pull the authoritative full-time result from the TxLINE scores feed; extract a self-contained evidence bundle (pure, offline-testable parser). |
| `contracts.py` | The prediction-market contract templates and their **deterministic** resolution rules (1X2, double chance, totals, BTTS, exact score). |
| `engine.py` | Settle a contract against evidence; bind it into an immutable, reproducibly-hashed receipt. |
| `ledger.py` | Append-only, hash-chained audit log of receipts and on-chain anchor confirmations. |
| `anchor/` | Commit the ledger head to Solana devnet as a Memo transaction, and verify one from a signature. Best-effort: never on the settlement critical path. |
| `verify.py` | Independent, layered verification / dispute resolution. |
| `oracle.py` | Thin façade wiring settle → record → anchor. |
| `cli.py` | `demo`, `list`, `verify`. |

### Data provenance

`data/fixtures/` holds **real** results for 24 World Cup fixtures, captured
from the live TxLINE devnet feed (`scripts/capture_results.py`). Each bundle
records the fixture identity, the authoritative full-time score, and the raw
`game_finalised` record as evidence — no tokens or secrets. The feed is
TxODDS's own hybrid on/off-chain oracle, which itself publishes a Merkle root
of its data on-chain; the bundles keep the message ids, sequence numbers and
finalisation timestamps needed to re-fetch and cross-check against it.

Refresh them any time:

```bash
export TXLINE_CREDS_FILE=~/.wallets/.txline_creds.json
python3 scripts/capture_results.py
```

---

## Tests

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

Coverage spans the resolution rules (including draws and total-line pushes),
the evidence parser (the VAR-overturn case, post-finalisation edits, refusal to
settle unfinished fixtures), the engine's guardrails (fixture mismatch,
doctored digests), the hash chain (tampering, deletion, reordering), the full
verification stack (including a *self-consistent* forgery caught only by the
chain / anchor / source layers), and the Solana Memo packing and anchor/verify
path against a mocked RPC client. The Solana tests skip cleanly if the optional
`anchor` extras aren't installed.

The on-chain code is exercised against real Solana devnet data too: the same
`verify_anchor` used above confirms a genuine, previously-committed devnet
anchor transaction end-to-end (RPC fetch → memo decode → hash match).

---

## Scope and limitations

- **Devnet / paper only.** Nothing here touches mainnet or real funds, and the
  oracle deliberately does not do payouts — it decides outcomes.
- **1X2 football markets** are implemented end-to-end. The contract model is a
  simple template + rule pair, so new markets are a small, self-contained
  addition rather than a plumbing change.
- **Single authoritative source.** It settles on TxLINE's `game_finalised`
  signal. Multi-source quorum (settle only when *N* independent feeds agree) is
  a natural next layer and the evidence-bundle design already anticipates it.
- **Anchoring is best-effort.** A settlement is valid and verifiable off its
  own receipt the moment it is produced; the on-chain anchor is an additional,
  optional layer of public tamper-evidence.

## License

MIT.
