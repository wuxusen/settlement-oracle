"""Result-evidence extraction from the TxLINE scores feed.

The parsing is a pure function, so these run fully offline against synthetic
snapshots plus the committed real bundles. The star case is the VAR overturn:
a transient score that must NOT be settled on."""
from __future__ import annotations

import pytest

from settlement_oracle.feeds.txline_results import (
    NotFinalisedError,
    build_evidence_from_snapshot,
    canonical_digest,
)


def _rec(ts, seq, action, p1=None, p2=None, p1_home=True, **kw):
    """Build one score-feed record with optional Participant Total goals."""
    r = {
        "FixtureId": 100,
        "Ts": ts,
        "Seq": seq,
        "Action": action,
        "Participant1IsHome": p1_home,
        "Participant1Id": 11,
        "Participant2Id": 22,
        "CompetitionId": 72,
        "StartTime": 1_000,
    }
    score = {}
    if p1 is not None:
        score["Participant1"] = {"Total": {"Goals": p1}}
    if p2 is not None:
        score["Participant2"] = {"Total": {"Goals": p2}}
    if score:
        r["Score"] = score
    r.update(kw)
    return r


def test_var_overturn_settles_on_finalised_not_transient():
    """Home leads 2-1, VAR briefly shows 2-2, the goal is discarded, and
    game_finalised confirms 2-1. The oracle must extract 2-1."""
    snapshot = [
        _rec(1, 1, "goal", p1=1, p2=0),
        _rec(2, 2, "goal", p1=2, p2=0),
        _rec(3, 3, "goal", p1=2, p2=1),
        _rec(4, 4, "var", p2=2),               # transient VAR: shows 2-2
        _rec(5, 5, "action_discarded", p2=1),  # overturned back to 2-1
        _rec(6, 6, "game_finalised", p1=2, p2=1),
    ]
    ev = build_evidence_from_snapshot(100, snapshot, competition_id=72)
    assert (ev["home_goals"], ev["away_goals"]) == (2, 1)
    assert ev["result"] == "HOME"
    assert ev["finalised_seq"] == 6


def test_records_after_finalisation_are_ignored():
    snapshot = [
        _rec(1, 1, "goal", p1=1, p2=0),
        _rec(5, 5, "game_finalised", p1=1, p2=0),
        _rec(9, 9, "correction", p1=9, p2=9),  # bogus post-finalisation edit
    ]
    ev = build_evidence_from_snapshot(100, snapshot)
    assert (ev["home_goals"], ev["away_goals"]) == (1, 0)


def test_not_finalised_refuses():
    snapshot = [_rec(1, 1, "goal", p1=1, p2=0), _rec(2, 2, "kick_off")]
    with pytest.raises(NotFinalisedError):
        build_evidence_from_snapshot(100, snapshot)


def test_away_home_mapping_when_participant1_is_away():
    snapshot = [_rec(5, 5, "game_finalised", p1=0, p2=3, p1_home=False)]
    ev = build_evidence_from_snapshot(100, snapshot)
    # p1 (away) scored 0, p2 (home) scored 3 -> home 3, away 0
    assert (ev["home_goals"], ev["away_goals"]) == (3, 0)
    assert ev["result"] == "HOME"


def test_scoreless_draw_defaults_missing_goals_to_zero():
    snapshot = [_rec(5, 5, "game_finalised")]  # no Score at all -> 0-0
    ev = build_evidence_from_snapshot(100, snapshot)
    assert (ev["home_goals"], ev["away_goals"]) == (0, 0)
    assert ev["result"] == "DRAW"


def test_ignores_records_for_other_fixtures():
    other = _rec(4, 4, "game_finalised", p1=5, p2=5)
    other["FixtureId"] = 999
    snapshot = [other, _rec(5, 5, "game_finalised", p1=2, p2=1)]
    ev = build_evidence_from_snapshot(100, snapshot)
    assert (ev["home_goals"], ev["away_goals"]) == (2, 1)


def test_digest_is_stable_and_ignores_capture_noise():
    snapshot = [_rec(5, 5, "game_finalised", p1=2, p2=1)]
    ev = build_evidence_from_snapshot(100, snapshot)
    # A second, independent build with cosmetic extras must digest identically.
    ev2 = dict(ev)
    ev2["captured_at"] = "some-other-time"
    ev2["home"] = "Renamed FC"
    assert canonical_digest(ev) == canonical_digest(ev2) == ev["evidence_digest"]


def test_digest_changes_if_score_changes():
    a = build_evidence_from_snapshot(100, [_rec(5, 5, "game_finalised", p1=2, p2=1)])
    b = build_evidence_from_snapshot(100, [_rec(5, 5, "game_finalised", p1=3, p2=1)])
    assert a["evidence_digest"] != b["evidence_digest"]
