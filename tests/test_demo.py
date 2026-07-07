"""The CLI demo runs end-to-end offline and returns success, and the tamper
branch it prints actually rejects a doctored receipt."""
from __future__ import annotations

import json

from settlement_oracle.cli import main


def test_demo_runs_and_succeeds(capsys):
    rc = main(["demo", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "VERIFIED" in out
    assert "Portugal v Croatia" in out
    # tamper section must show the rejection
    assert "NOT VERIFIED" in out


def test_demo_on_a_draw_fixture(capsys):
    rc = main(["demo", "--fixture", "18176123", "--no-color"])  # Australia 1-1 Egypt
    out = capsys.readouterr().out
    assert rc == 0
    assert "Australia v Egypt" in out
    assert "Match to end in a draw" in out


def test_demo_writes_artifacts(tmp_path, capsys):
    out_file = tmp_path / "run.json"
    rc = main(["demo", "--no-color", "--out", str(out_file)])
    assert rc == 0
    data = json.loads(out_file.read_text())
    assert data["fixture"]["result"] == "HOME"
    assert data["verification"]["verified"] is True
    assert data["ledger"]["records"]


def test_list_runs(capsys):
    rc = main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Portugal v Croatia" in out


def test_verify_subcommand_on_written_receipt(tmp_path):
    # produce a receipt via the demo artifacts, then verify it standalone
    run = tmp_path / "run.json"
    main(["demo", "--no-color", "--out", str(run)])
    receipt = json.loads(run.read_text())["primary_receipt"]
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    rc = main(["verify", str(receipt_file)])
    assert rc == 0
