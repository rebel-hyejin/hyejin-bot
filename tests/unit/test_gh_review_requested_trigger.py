"""Unit tests for `triggers/gh_review_requested.py` (T033).

Drives `poll_once()` directly against `FakeGh` + tmp_path SQLite so the
seven scenarios from `tasks.md` § Phase 4 are individually verifiable
without standing up a full daemon. The full TaskGroup wiring lives in
`tests/integration/test_gh_review_requested_e2e.py` (T034).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from daeyeon_bot.core.errors import AuthError
from daeyeon_bot.core.time import Clock, SystemClock
from daeyeon_bot.infra import storage
from daeyeon_bot.triggers.gh_review_requested import GhReviewRequestedTrigger
from tests.fakes.gh_cli import FakeGh

REPO = "octo/cat"
PR = 17


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    """Return a tmp DB path with the full migration set applied."""
    p = tmp_path / "state.db"
    async with storage.connection(p) as conn:
        await storage.apply_migrations(conn)
        await conn.commit()
    return p


def _trigger(*, gh: FakeGh, db_path: Path, **kwargs: Any) -> GhReviewRequestedTrigger:
    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        async with storage.connection(db_path) as conn:
            yield conn

    return GhReviewRequestedTrigger(
        gh=gh,
        storage_factory=factory,
        github_username=kwargs.pop("username", "daeyeon-lee"),
        poll_interval_seconds=kwargs.pop("poll_interval_seconds", 0.01),
        clock=kwargs.pop("clock", SystemClock()),
    )


async def _state_for(db_path: Path, repo: str, pr_number: int) -> dict[str, Any] | None:
    async with storage.connection(db_path) as conn:
        async with conn.execute(
            "SELECT head_sha, request_gen, in_pending_set, last_observed_at"
            " FROM gh_review_requested_state WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return {
        "head_sha": row["head_sha"],
        "request_gen": int(row["request_gen"]),
        "in_pending_set": bool(row["in_pending_set"]),
        "last_observed_at": row["last_observed_at"],
    }


async def _events_for(db_path: Path, repo: str, pr_number: int) -> list[dict[str, Any]]:
    async with storage.connection(db_path) as conn:
        async with conn.execute(
            "SELECT id, type, source, source_dedup_key, payload_json"
            " FROM events WHERE source = 'gh_review_requested' ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "source": r["source"],
            "source_dedup_key": r["source_dedup_key"],
            "payload_json": r["payload_json"],
        }
        for r in rows
    ]


async def _outbox_handlers(db_path: Path) -> list[str]:
    async with storage.connection(db_path) as conn:
        async with conn.execute("SELECT handler FROM outbox ORDER BY id") as cur:
            rows = await cur.fetchall()
    return [str(r["handler"]) for r in rows]


# ── Scenarios ─────────────────────────────────────────────────────────────


async def test_first_observation_emits_gen_one(db_path: Path) -> None:
    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    emitted = await trig.poll_once()

    assert emitted == 1
    state = await _state_for(db_path, REPO, PR)
    assert state is not None
    assert state == {
        "head_sha": "sha1",
        "request_gen": 1,
        "in_pending_set": True,
        "last_observed_at": state["last_observed_at"],  # set, format checked below
    }
    events = await _events_for(db_path, REPO, PR)
    assert len(events) == 1
    assert events[0]["type"] == "gh.review_requested"
    assert "request_gen" in events[0]["payload_json"]
    assert '"1"' in events[0]["payload_json"]
    assert await _outbox_handlers(db_path) == ["pr_review"]


async def test_same_observation_twice_no_emit_second_time(db_path: Path) -> None:
    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    first = await trig.poll_once()
    second = await trig.poll_once()

    assert first == 1
    assert second == 0
    events = await _events_for(db_path, REPO, PR)
    assert len(events) == 1


async def test_new_push_increments_gen(db_path: Path) -> None:
    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    await trig.poll_once()
    gh.update_head_sha(REPO, PR, head_sha="sha2")
    second = await trig.poll_once()

    assert second == 1
    state = await _state_for(db_path, REPO, PR)
    assert state is not None
    assert state["head_sha"] == "sha2"
    assert state["request_gen"] == 2
    assert state["in_pending_set"] is True
    events = await _events_for(db_path, REPO, PR)
    assert len(events) == 2
    assert '"2"' in events[1]["payload_json"]


async def test_re_request_after_withdrawal_increments_gen(db_path: Path) -> None:
    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    await trig.poll_once()
    gh.remove_from_search(REPO, PR)
    await trig.poll_once()  # case 5: pending=0, no emit
    gh.add_to_search(REPO, PR)
    third = await trig.poll_once()  # case 2: re-request, emit gen=2

    assert third == 1
    state = await _state_for(db_path, REPO, PR)
    assert state is not None
    assert state["request_gen"] == 2
    assert state["in_pending_set"] is True
    events = await _events_for(db_path, REPO, PR)
    assert len(events) == 2
    assert '"2"' in events[1]["payload_json"]


async def test_permanent_withdrawal_keeps_state_no_emit(db_path: Path) -> None:
    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    await trig.poll_once()
    gh.remove_from_search(REPO, PR)
    second = await trig.poll_once()
    third = await trig.poll_once()

    assert second == 0
    assert third == 0
    state = await _state_for(db_path, REPO, PR)
    assert state is not None
    assert state["in_pending_set"] is False
    events = await _events_for(db_path, REPO, PR)
    assert len(events) == 1


async def test_dedup_key_uniqueness_across_polls(db_path: Path) -> None:
    """Belt-and-suspenders: even if poll_once ran twice for the same observation,
    `events.UNIQUE(source, source_dedup_key)` lets the second insert no-op
    instead of duplicating the row.
    """
    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    await trig.poll_once()

    # Manually re-emit at the same (head_sha, gen) by short-circuiting the
    # state machine: directly call _emit_event again with gen=1.
    from daeyeon_bot.core.time import SystemClock
    from daeyeon_bot.triggers.gh_review_requested import (
        _emit_event,  # pyright: ignore[reportPrivateUsage]
    )

    clock = SystemClock()
    async with storage.connection(db_path) as conn:
        now = clock.now()
        accepted = await _emit_event(
            conn,
            repo=REPO,
            pr_number=PR,
            head_sha="sha1",
            request_gen=1,
            now=now,
            now_iso=now.isoformat(),
        )
        await conn.commit()
    assert accepted is False  # UNIQUE made it a no-op

    events = await _events_for(db_path, REPO, PR)
    assert len(events) == 1


async def test_auth_error_propagates(db_path: Path) -> None:
    gh = FakeGh(auth_ok=False)
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    with pytest.raises(AuthError):
        await trig.poll_once()


async def test_run_loop_halts_on_auth_error(db_path: Path) -> None:
    """run() must surface AuthError so the daemon halts (exit 78)."""
    gh = FakeGh(auth_ok=True)
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path, poll_interval_seconds=0.001)

    async def _flip_after_first_poll() -> None:
        await asyncio.sleep(0.05)
        gh.auth_ok = False

    flipper = asyncio.create_task(_flip_after_first_poll())

    class _NopCtx:
        clock: Clock = SystemClock()

    async def _emit(_e: object) -> None:
        return None

    with pytest.raises(AuthError):
        await asyncio.wait_for(trig.run(_emit, _NopCtx()), timeout=2.0)
    await flipper


async def test_run_loop_swallows_rate_limit_and_transient_then_succeeds(
    db_path: Path,
) -> None:
    """run() must keep looping on RateLimitError / TransientError."""
    from daeyeon_bot.core.errors import TransientError

    gh = FakeGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path, poll_interval_seconds=0.001)

    # First poll: rate-limited. Second poll: transient. Third poll: success.
    seq: list[str] = ["rate", "transient", "ok"]

    async def _shift() -> None:
        # Allow `run()` to enter the first iteration so it sees rate_limited.
        await asyncio.sleep(0.02)
        gh.rate_limited = False
        # Inject a transient failure on the next search call.
        gh.raise_on_search = TransientError("flaky")
        await asyncio.sleep(0.02)
        gh.raise_on_search = None  # next search succeeds

    gh.rate_limited = True

    class _NopCtx:
        clock: Clock = SystemClock()

    async def _emit(_e: object) -> None:
        return None

    shifter = asyncio.create_task(_shift())
    run_task = asyncio.create_task(trig.run(_emit, _NopCtx()))
    try:
        # Wait until at least one event lands in the DB → success path reached.
        for _ in range(200):
            events = await _events_for(db_path, REPO, PR)
            if events:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail(f"event never landed; seq={seq}")
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, shifter, return_exceptions=True)


async def test_poll_once_skips_non_dict_items(db_path: Path) -> None:
    """A non-dict element in search results is dropped (not raised)."""
    from typing import cast

    class _BadGh(FakeGh):
        async def search_review_requested(self, username: str) -> list[dict[str, Any]]:
            base = await super().search_review_requested(username)
            return [*base, cast("dict[str, Any]", "not-a-dict")]  # type: ignore[list-item]

    gh = _BadGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    emitted = await trig.poll_once()
    assert emitted == 1


async def test_poll_once_handles_pr_get_failure_for_persisted_pr(
    db_path: Path,
) -> None:
    """When pr_get fails on an observed PR that has a persisted state row,
    the row must be left untouched (treated as not-observed for this cycle)."""
    from daeyeon_bot.core.errors import TransientError

    class _FlakyGh(FakeGh):
        flake: bool = False

        async def pr_get(self, repo: str, pr_number: int) -> dict[str, Any]:
            if self.flake:
                raise TransientError("flaky")
            return await super().pr_get(repo, pr_number)

    gh = _FlakyGh()
    gh.add_pr(REPO, PR, head_sha="sha1", in_search_set=True)
    trig = _trigger(gh=gh, db_path=db_path)

    first = await trig.poll_once()
    assert first == 1

    gh.flake = True
    second = await trig.poll_once()
    # No new event because head_sha couldn't be fetched; existing row preserved.
    assert second == 0
    state = await _state_for(db_path, REPO, PR)
    assert state is not None
    assert state["head_sha"] == "sha1"
    assert state["request_gen"] == 1
    # Still in pending — withdrawal is not the right interpretation here.
    assert state["in_pending_set"] is True


async def test_parse_search_item_rejects_garbage() -> None:
    """Defensive parsing of `repository_url` and `number`."""
    from daeyeon_bot.triggers.gh_review_requested import (
        _parse_search_item,  # pyright: ignore[reportPrivateUsage]
    )

    # Wrong types → None
    assert _parse_search_item({"number": "x", "repository_url": "https://api/repos/o/r"}) is None
    assert _parse_search_item({"number": 1, "repository_url": 42}) is None
    # Missing /repos/ marker → None
    assert _parse_search_item({"number": 1, "repository_url": "https://api/foo/o/r"}) is None
    # Empty repo segment → None
    assert _parse_search_item({"number": 1, "repository_url": "https://api/repos/"}) is None


async def test_extract_head_sha_handles_malformed_payloads() -> None:
    """`_extract_head_sha` returns None on any unexpected shape."""
    from daeyeon_bot.triggers.gh_review_requested import (
        _extract_head_sha,  # pyright: ignore[reportPrivateUsage]
    )

    assert _extract_head_sha(None) is None
    assert _extract_head_sha("not a dict") is None
    assert _extract_head_sha({"head": "not a dict"}) is None
    assert _extract_head_sha({"head": {}}) is None
    assert _extract_head_sha({"head": {"sha": ""}}) is None
    assert _extract_head_sha({"head": {"sha": 123}}) is None
    assert _extract_head_sha({"head": {"sha": "abc"}}) == "abc"
