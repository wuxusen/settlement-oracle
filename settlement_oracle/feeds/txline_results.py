"""TxLINE results adapter — authoritative full-time match results.

Where the *odds* feed (used by the sister market-maker project) answers
"what is the price right now", this adapter answers the only question a
settlement oracle cares about: **what was the final, confirmed result?**

It reads the TxODDS TxLINE ``scores`` feed. The score feed is a sequence of
timestamped, sequence-numbered *action* records (goals, cards, VAR reviews,
status changes...). The terminal, authoritative record is the one whose
``Action == "game_finalised"`` — TxODDS's explicit "this result is final"
signal. Everything before it (including transient VAR states that get
overturned) is provisional and must not be settled on.

Real example baked into the test data: Portugal v Croatia briefly showed
2-2 after a VAR review, the goal was then ``action_discarded`` back to 2-1,
and only ``game_finalised`` confirmed the 2-1 result. A naive oracle that
settled on the latest score would have paid out the wrong contract; this one
settles on ``game_finalised`` and gets it right.

The parsing (:func:`build_evidence_from_snapshot`) is a pure function and is
unit-tested entirely offline against the committed evidence bundles. The
authenticated transport (:class:`TxLineResultsAdapter`) reads credentials
from a local creds file, never from source, exactly like the sister project.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Optional

API_ORIGIN_DEVNET = "https://txline-dev.txodds.com"
DEVNET_BASE = f"{API_ORIGIN_DEVNET}/api"
GUEST_START_PATH = "/auth/guest/start"

ENV_CREDS_FILE = "TXLINE_CREDS_FILE"
DEFAULT_CREDS_PATH = os.path.expanduser("~/.wallets/.txline_creds.json")

# The score-feed action that marks a fixture as authoritatively finished.
FINALISED_ACTION = "game_finalised"

# Canonical prediction-market outcomes for a 1X2 (match-result) fixture.
HOME, DRAW, AWAY = "HOME", "DRAW", "AWAY"


def load_credentials(path: Optional[str] = None) -> dict:
    """Read the TxLINE creds JSON (guest JWT + activated API token).

    Resolution order: explicit ``path`` -> ``$TXLINE_CREDS_FILE`` ->
    :data:`DEFAULT_CREDS_PATH`. The secret values are never logged here.
    """
    resolved = path or os.environ.get(ENV_CREDS_FILE) or DEFAULT_CREDS_PATH
    with open(resolved, "r", encoding="utf-8") as fh:
        creds = json.load(fh)
    if not creds.get("jwt") or not creds.get("apiToken"):
        raise ValueError(f"creds file {resolved!r} missing jwt/apiToken")
    return creds


def _goals(record: dict, side: str) -> Optional[int]:
    """Total goals for ``Participant1``/``Participant2`` in one score record,
    or ``None`` if the record carries no total for that side."""
    try:
        total = record["Score"][side]["Total"]
    except (KeyError, TypeError):
        return None
    goals = total.get("Goals")
    return int(goals) if goals is not None else None


def canonical_digest(evidence: dict) -> str:
    """SHA-256 over the settlement-relevant fields of an evidence bundle.

    Deterministic and independent of key order / capture timestamp, so two
    independent captures of the same finalised fixture produce the same
    digest. This is what the oracle's receipt commits to, and what
    :func:`verify_settlement` recomputes.
    """
    core = {
        "fixture_id": evidence["fixture_id"],
        "competition_id": evidence.get("competition_id"),
        "participant1_id": evidence.get("participant1_id"),
        "participant2_id": evidence.get("participant2_id"),
        "participant1_is_home": evidence["participant1_is_home"],
        "home_goals": evidence["home_goals"],
        "away_goals": evidence["away_goals"],
        "finalised_ts": evidence["finalised_ts"],
        "finalised_seq": evidence["finalised_seq"],
        "source": evidence["source"],
    }
    payload = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_evidence_from_snapshot(
    fixture_id: int,
    snapshot: list,
    competition_id: Optional[int] = None,
    p1_name: Optional[str] = None,
    p2_name: Optional[str] = None,
) -> dict:
    """Extract an authoritative result-evidence bundle from a raw scores
    snapshot (the JSON array returned by ``/api/scores/snapshot/{id}``).

    Raises :class:`NotFinalisedError` if the fixture has not reached a
    ``game_finalised`` record — the oracle must refuse to settle those, which
    is exactly how postponed / in-progress / rescheduled fixtures are handled.

    Pure function: no network, no clock, no globals. Deterministic given the
    same snapshot.
    """
    records = [r for r in snapshot if int(r.get("FixtureId", -1)) == int(fixture_id)]
    if not records:
        raise ValueError(f"snapshot has no records for fixture {fixture_id}")

    ordered = sorted(records, key=lambda r: (int(r.get("Ts", 0)), int(r.get("Seq", 0))))
    finalised = [r for r in ordered if str(r.get("Action")) == FINALISED_ACTION]
    if not finalised:
        raise NotFinalisedError(
            f"fixture {fixture_id} has no '{FINALISED_ACTION}' record — not settleable"
        )
    final_rec = finalised[-1]
    cutoff = (int(final_rec.get("Ts", 0)), int(final_rec.get("Seq", 0)))

    # Authoritative goals: the last known Total.Goals for each side at or
    # before finalisation. A side that never records a Goals total finished
    # on zero. This deliberately ignores any post-finalisation records.
    p1_goals = p2_goals = 0
    for r in ordered:
        if (int(r.get("Ts", 0)), int(r.get("Seq", 0))) > cutoff:
            break
        g1 = _goals(r, "Participant1")
        g2 = _goals(r, "Participant2")
        if g1 is not None:
            p1_goals = g1
        if g2 is not None:
            p2_goals = g2

    p1_is_home = bool(final_rec.get("Participant1IsHome", True))
    home_goals, away_goals = (p1_goals, p2_goals) if p1_is_home else (p2_goals, p1_goals)
    if home_goals > away_goals:
        result = HOME
    elif away_goals > home_goals:
        result = AWAY
    else:
        result = DRAW

    home_name = (p1_name if p1_is_home else p2_name)
    away_name = (p2_name if p1_is_home else p1_name)

    evidence = {
        "fixture_id": int(fixture_id),
        "competition_id": competition_id if competition_id is not None
        else final_rec.get("CompetitionId"),
        "source": "TxODDS TxLINE devnet — scores feed (game_finalised)",
        "participant1_id": final_rec.get("Participant1Id"),
        "participant2_id": final_rec.get("Participant2Id"),
        "participant1_is_home": p1_is_home,
        "home": home_name or f"participant_{final_rec.get('Participant1Id' if p1_is_home else 'Participant2Id')}",
        "away": away_name or f"participant_{final_rec.get('Participant2Id' if p1_is_home else 'Participant1Id')}",
        "start_time": final_rec.get("StartTime"),
        "home_goals": int(home_goals),
        "away_goals": int(away_goals),
        "result": result,
        "finalised_ts": int(final_rec.get("Ts", 0)),
        "finalised_seq": int(final_rec.get("Seq", 0)),
        "finalised_record": {
            "Action": final_rec.get("Action"),
            "Ts": final_rec.get("Ts"),
            "Seq": final_rec.get("Seq"),
            "StatusId": final_rec.get("StatusId"),
            "Confirmed": final_rec.get("Confirmed"),
            "Score": final_rec.get("Score"),
        },
    }
    evidence["evidence_digest"] = canonical_digest(evidence)
    return evidence


class NotFinalisedError(RuntimeError):
    """Raised when a fixture has not reached ``game_finalised`` — the oracle
    refuses to settle it (postponed / rescheduled / still in play)."""


class TxLineResultsAdapter:
    """Authenticated adapter that fetches result evidence from live TxLINE.

    Transport only — all parsing lives in :func:`build_evidence_from_snapshot`
    so it stays offline-testable. Use the committed bundles in
    ``data/fixtures/`` for the demo and tests; use this to refresh them.
    """

    def __init__(
        self,
        jwt: str,
        api_token: str,
        base_url: str = DEVNET_BASE,
        competition_id: int = 72,
    ) -> None:
        self._jwt = jwt
        self._api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.competition_id = competition_id

    @classmethod
    def from_creds_file(cls, path: Optional[str] = None, competition_id: int = 72):
        creds = load_credentials(path)
        return cls(
            jwt=creds["jwt"],
            api_token=creds["apiToken"],
            base_url=creds.get("apiBaseUrl", DEVNET_BASE),
            competition_id=competition_id,
        )

    @property
    def _origin(self) -> str:
        return self.base_url[: -len("/api")] if self.base_url.endswith("/api") else self.base_url

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._jwt}",
            "X-Api-Token": self._api_token,
            "Accept": "application/json",
        }

    def _renew_jwt(self) -> None:
        req = urllib.request.Request(self._origin + GUEST_START_PATH, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            self._jwt = json.loads(resp.read().decode("utf-8"))["token"]

    def _get(self, path: str, _retried: bool = False):
        req = urllib.request.Request(self.base_url + path, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and not _retried:
                self._renew_jwt()
                return self._get(path, _retried=True)
            raise

    def fetch_scores_snapshot(self, fixture_id: int) -> list:
        data = self._get(f"/scores/snapshot/{fixture_id}")
        return data if isinstance(data, list) else [data]

    def fetch_result_evidence(
        self, fixture_id: int, p1_name: Optional[str] = None, p2_name: Optional[str] = None
    ) -> dict:
        snapshot = self.fetch_scores_snapshot(fixture_id)
        return build_evidence_from_snapshot(
            fixture_id, snapshot, competition_id=self.competition_id,
            p1_name=p1_name, p2_name=p2_name,
        )
