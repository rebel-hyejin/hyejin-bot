"""Unit tests for the §5 state-machine in `infra.pr_review_state` (T020).

One test per case (1-6) plus the ValueError guard. Uses a real `aiosqlite`
DB in `tmp_path` so the schema constraints are exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from hyejin_bot.infra.pr_review_state import (
    StateRow,
    get_state,
    prune_dormant,
    upsert_observation,
)
from hyejin_bot.infra.storage import apply_migrations, open_db


async def _migrated_db(tmp_path: Path) -> aiosqlite.Connection:
    conn = await open_db(tmp_path / "state.db")
    await apply_migrations(conn)
    return conn


_REPO = "o/r"
_PR = 42
_SHA_A = "a" * 40
_SHA_B = "b" * 40


@pytest.mark.asyncio
async def test_case1_first_observation_inserts_and_emits(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        gen, emit = await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:00:00+00:00",
        )
        await conn.commit()
        assert (gen, emit) == (1, True)
        row = await get_state(conn, _REPO, _PR)
        assert row == StateRow(
            repo=_REPO,
            pr_number=_PR,
            head_sha=_SHA_A,
            request_gen=1,
            in_pending_set=True,
            last_observed_at="2026-05-04T00:00:00+00:00",
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_case2_re_request_after_withdraw_bumps_gen(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        # Set up: PR was observed (case 1), then withdrawn (case 5).
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:00:00+00:00",
        )
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=False,
            head_sha=None,
            now_iso="2026-05-04T00:05:00+00:00",
        )
        await conn.commit()

        # Now author re-requests at the same SHA.
        gen, emit = await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T01:00:00+00:00",
        )
        await conn.commit()
        assert (gen, emit) == (2, True)
        row = await get_state(conn, _REPO, _PR)
        assert row is not None
        assert row.request_gen == 2
        assert row.in_pending_set is True
        assert row.head_sha == _SHA_A
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_case3_new_head_sha_bumps_gen(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:00:00+00:00",
        )
        await conn.commit()

        gen, emit = await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_B,
            now_iso="2026-05-04T00:10:00+00:00",
        )
        await conn.commit()
        assert (gen, emit) == (2, True)
        row = await get_state(conn, _REPO, _PR)
        assert row is not None
        assert row.head_sha == _SHA_B
        assert row.request_gen == 2
        assert row.in_pending_set is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_case4_redundant_observation_only_touches_timestamp(
    tmp_path: Path,
) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:00:00+00:00",
        )
        await conn.commit()

        gen, emit = await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:05:00+00:00",
        )
        await conn.commit()
        assert (gen, emit) == (1, False)
        row = await get_state(conn, _REPO, _PR)
        assert row is not None
        assert row.request_gen == 1
        assert row.in_pending_set is True
        assert row.last_observed_at == "2026-05-04T00:05:00+00:00"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_case5_withdrawal_flips_pending_off(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:00:00+00:00",
        )
        await conn.commit()

        gen, emit = await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=False,
            head_sha=None,
            now_iso="2026-05-04T00:05:00+00:00",
        )
        await conn.commit()
        assert (gen, emit) == (1, False)
        row = await get_state(conn, _REPO, _PR)
        assert row is not None
        assert row.in_pending_set is False
        assert row.request_gen == 1
        assert row.head_sha == _SHA_A  # preserved across the flip
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_case6_dormant_row_no_change(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-05-04T00:00:00+00:00",
        )
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=False,
            head_sha=None,
            now_iso="2026-05-04T00:05:00+00:00",
        )
        await conn.commit()

        gen, emit = await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=_PR,
            observed_now=False,
            head_sha=None,
            now_iso="2026-05-04T00:10:00+00:00",
        )
        await conn.commit()
        assert (gen, emit) == (1, False)
        row = await get_state(conn, _REPO, _PR)
        assert row is not None
        assert row.in_pending_set is False
        assert row.request_gen == 1
        # last_observed_at is NOT updated for case 6 — the row sits truly dormant.
        assert row.last_observed_at == "2026-05-04T00:05:00+00:00"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_observed_now_requires_head_sha(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            await upsert_observation(
                conn,
                repo=_REPO,
                pr_number=_PR,
                observed_now=True,
                head_sha=None,
                now_iso="2026-05-04T00:00:00+00:00",
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_state_returns_none_for_unknown(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        assert await get_state(conn, "x/y", 1) is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_prune_dormant_removes_only_old_dormant_rows(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        # PR 1: dormant + old → pruned.
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=1,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-01-01T00:00:00+00:00",
        )
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=1,
            observed_now=False,
            head_sha=None,
            now_iso="2026-01-02T00:00:00+00:00",
        )
        # PR 2: dormant + recent → kept.
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=2,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-04-30T00:00:00+00:00",
        )
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=2,
            observed_now=False,
            head_sha=None,
            now_iso="2026-05-01T00:00:00+00:00",
        )
        # PR 3: still pending → kept regardless of age.
        await upsert_observation(
            conn,
            repo=_REPO,
            pr_number=3,
            observed_now=True,
            head_sha=_SHA_A,
            now_iso="2026-01-01T00:00:00+00:00",
        )
        await conn.commit()

        # Threshold: older than 2026-04-01 gets pruned.
        deleted = await prune_dormant(conn, older_than_iso="2026-04-01T00:00:00+00:00")
        await conn.commit()
        assert deleted == 1

        assert await get_state(conn, _REPO, 1) is None
        kept_dormant = await get_state(conn, _REPO, 2)
        assert kept_dormant is not None
        assert kept_dormant.in_pending_set is False
        kept_pending = await get_state(conn, _REPO, 3)
        assert kept_pending is not None
        assert kept_pending.in_pending_set is True
    finally:
        await conn.close()
