"""Command-line interface for the settlement oracle.

    settlement-oracle demo     # run the whole pipeline on one real fixture
    settlement-oracle list     # list the captured real fixtures
    settlement-oracle verify   # independently verify a saved receipt

The ``demo`` subcommand is the headline: it takes a real, finished World Cup
fixture, settles a slate of prediction-market contracts against the
authoritative TxLINE result, records them into a hash-chained audit ledger,
anchors the ledger head to Solana devnet (if a wallet is configured), then
*independently re-verifies* every settlement — and finally shows that a
tampered evidence bundle fails verification.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from dataclasses import asdict

from . import __version__, contracts
from .anchor.base import NullAnchor
from .ledger import SettlementLedger
from .oracle import SettlementOracle
from .store import list_fixtures, load_evidence
from .types import SettlementReceipt
from .verify import verify_settlement

DEFAULT_FIXTURE = 18179763  # Portugal v Croatia (2-1, with a real VAR overturn)

# ANSI helpers (no-op when not a TTY).
def _c(code: str, s: str, enabled: bool) -> str:
    return f"\033[{code}m{s}\033[0m" if enabled else s


def _build_slate(fixture_id: int, ev: dict) -> list:
    """A representative slate of contracts for one fixture."""
    home, away = ev.get("home", "HOME"), ev.get("away", "AWAY")
    return [
        contracts.team_to_win(f"{fixture_id}-HOME", fixture_id, "HOME", home),
        contracts.team_to_win(f"{fixture_id}-AWAY", fixture_id, "AWAY", away),
        contracts.match_draw(f"{fixture_id}-DRAW", fixture_id),
        contracts.double_chance(f"{fixture_id}-1X", fixture_id, ["HOME", "DRAW"]),
        contracts.total_over(f"{fixture_id}-O25", fixture_id, 2.5),
        contracts.total_under(f"{fixture_id}-U25", fixture_id, 2.5),
        contracts.both_teams_to_score(f"{fixture_id}-BTTS", fixture_id),
        contracts.exact_score(
            f"{fixture_id}-CS", fixture_id, ev["home_goals"], ev["away_goals"]
        ),
    ]


def cmd_list(args) -> int:
    fixtures = list_fixtures()
    print(f"{len(fixtures)} captured real World Cup fixtures (TxODDS TxLINE devnet):\n")
    print(f"  {'fixture_id':>10}  {'match':<30} {'score':>6}  result")
    print("  " + "-" * 58)
    for f in fixtures:
        print(f"  {f['fixture_id']:>10}  {f['label']:<30} {f['score']:>6}  {f['result']}")
    return 0


def cmd_verify(args) -> int:
    payload = json.loads(open(args.receipt, "r", encoding="utf-8").read())
    receipt = SettlementReceipt(**payload)
    report = verify_settlement(receipt, tx_sig=args.tx, rpc_url=args.rpc)
    print(json.dumps(report.as_dict(), indent=2))
    return 0 if report.verified else 1


def cmd_demo(args) -> int:
    color = sys.stdout.isatty() and not args.no_color
    bold = lambda s: _c("1", s, color)
    green = lambda s: _c("32", s, color)
    red = lambda s: _c("31", s, color)
    cyan = lambda s: _c("36", s, color)
    dim = lambda s: _c("2", s, color)

    fixture_id = args.fixture
    ev = load_evidence(fixture_id)
    home, away = ev["home"], ev["away"]
    score = f"{ev['home_goals']}-{ev['away_goals']}"

    print(bold("\n═══ Verifiable Settlement Oracle — live demo ═══\n"))
    print(f"  Fixture     {cyan(f'{home} v {away}')}  (id {fixture_id})")
    print(f"  Source      {ev['source']}")
    print(f"  Final score {bold(score)}  →  outcome {bold(ev['result'])}")
    print(f"  Finalised   TxLINE '{ev['finalised_record']['Action']}' "
          f"@ ts {ev['finalised_ts']} seq {ev['finalised_seq']}")
    print(f"  Evidence #  {dim(ev['evidence_digest'])}")

    # --- Anchor sink -----------------------------------------------------
    anchor_sink = NullAnchor()
    anchoring = False
    if args.anchor:
        try:
            from .anchor.solana_anchor import SolanaAnchor
            rpc = args.rpc or os.environ.get(
                "SETTLEMENT_ORACLE_SOLANA_RPC_URL", "https://api.devnet.solana.com"
            )
            anchor_sink = SolanaAnchor.from_env(rpc_url=rpc)
            anchoring = True
            print(f"  Anchoring   {green('ENABLED')} → Solana devnet  (payer {anchor_sink.pubkey})")
        except Exception as exc:  # noqa: BLE001
            print(f"  Anchoring   {red('unavailable')} ({exc}); running offline")
    else:
        print(f"  Anchoring   {dim('offline (pass --anchor with a devnet wallet to anchor on-chain)')}")

    oracle = SettlementOracle(anchor_sink=anchor_sink, ledger=SettlementLedger())

    # --- Settle the slate ------------------------------------------------
    print(bold("\n  Settling prediction-market contracts against the result:\n"))
    print(f"    {'contract':<34} {'resolution'}")
    print("    " + "-" * 46)
    outcomes = []
    for contract in _build_slate(fixture_id, ev):
        outcome = oracle.settle(contract, ev)
        outcomes.append(outcome)
        res = outcome.receipt.resolution
        badge = green(" YES") if res == "YES" else (red(" NO ") if res == "NO" else " VOID")
        print(f"    {contract.description:<34} [{badge}]")

    print(f"\n  Ledger      {len(oracle.ledger.records)} receipts chained; "
          f"head {cyan(oracle.ledger.chain_head)}")

    # --- Anchor the ledger head on-chain ---------------------------------
    anchor_tx = None
    if anchoring:
        res, _rec = oracle.anchor_head()
        if res.ok:
            anchor_tx = res.tx_sig
            print(f"  Anchored    {green('on-chain')}  tx {cyan(anchor_tx)}  (slot {res.slot})")
            print(f"              {dim('https://explorer.solana.com/tx/' + str(anchor_tx) + '?cluster=devnet')}")
        else:
            print(f"  Anchored    {red('failed')} ({res.error}); ledger still fully verifiable offline")

    # --- Independent verification ---------------------------------------
    print(bold("\n  Independent verification (trusting nothing the oracle said):\n"))
    primary = outcomes[0]  # home team to win
    report = verify_settlement(
        primary.receipt,
        ledger=oracle.ledger,
        tx_sig=anchor_tx,
        rpc_url=args.rpc,
    )
    for check in report.checks:
        mark = green("PASS") if check["ok"] else red("FAIL")
        print(f"    [{mark}] {check['check']:<20} {dim(check['detail'])}")
    verdict = green("VERIFIED") if report.verified else red("NOT VERIFIED")
    print(f"\n  Verdict     {bold(verdict)} — "
          f"'{primary.receipt.contract['description']}' → {primary.receipt.resolution}")

    # --- Tamper demonstration -------------------------------------------
    print(bold("\n  Tamper check (flip the score in the evidence and re-verify):\n"))
    forged = copy.deepcopy(asdict(primary.receipt))
    forged["evidence"] = dict(forged["evidence"])
    forged["evidence"]["home_goals"] = ev["home_goals"] + 5  # fabricate a blowout
    tampered = verify_settlement(SettlementReceipt(**forged))
    for check in tampered.checks:
        mark = green("PASS") if check["ok"] else red("FAIL")
        print(f"    [{mark}] {check['check']:<20} {dim(check['detail'])}")
    print(f"\n  Tamper verdict  {red('NOT VERIFIED') if not tampered.verified else green('VERIFIED')}"
          f" — a doctored result is rejected. {green('This is the point.')}\n")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "fixture": {"id": fixture_id, "home": home, "away": away, "score": score,
                                "result": ev["result"]},
                    "ledger": oracle.ledger.to_dict(),
                    "anchor_tx": anchor_tx,
                    "primary_receipt": asdict(primary.receipt),
                    "verification": report.as_dict(),
                },
                fh, indent=2,
            )
        print(f"  Wrote run artifacts to {args.out}\n")
    return 0 if report.verified else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="settlement-oracle", description=__doc__)
    p.add_argument("--version", action="version", version=f"settlement-oracle {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the full settle→anchor→verify pipeline on one fixture")
    d.add_argument("--fixture", type=int, default=DEFAULT_FIXTURE, help="TxLINE fixture id")
    d.add_argument("--anchor", action="store_true", help="anchor on Solana devnet (needs a wallet)")
    d.add_argument("--rpc", default=None, help="Solana RPC url override")
    d.add_argument("--out", default=None, help="write run artifacts (ledger, receipt, verification) to a JSON file")
    d.add_argument("--no-color", action="store_true")
    d.set_defaults(func=cmd_demo)

    l = sub.add_parser("list", help="list captured real fixtures")
    l.set_defaults(func=cmd_list)

    v = sub.add_parser("verify", help="independently verify a saved settlement receipt")
    v.add_argument("receipt", help="path to a receipt JSON file")
    v.add_argument("--tx", default=None, help="Solana anchor tx signature to check against")
    v.add_argument("--rpc", default=None, help="Solana RPC url override")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
