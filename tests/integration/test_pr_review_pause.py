"""PAUSE kill-switch end-to-end (T040, US4).

When the operator drops the PAUSE flag:
    * the dispatcher stops claiming new outbox rows, and
    * any handler invocation that races the flag must call its `pause_guard`,
      which raises `QuotaError`. The dispatcher maps `QuotaError` to a `Retry`
      so the row resumes once PAUSE is cleared.

This integration test exercises the first arm: PAUSE flag set before the
event lands → outbox row sits in `pending`, no `gh.post_review` recorded.
Cleared PAUSE → dispatcher claims, handler posts, audit row written with
`status='posted'`.

The handler's QuotaError raise path is already verified at the unit level
in `tests/unit/test_pr_review_handler.py`; here we verify the full daemon
stack honours it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daeyeon_bot.app import pause as pause_mod
from daeyeon_bot.app.config import load
from daeyeon_bot.app.container import ContainerOverrides
from daeyeon_bot.app.lifecycle import BootOptions, boot
from daeyeon_bot.core.events import make_event
from daeyeon_bot.infra import outbox, storage
from daeyeon_bot.infra.claude import FakeClaudeSession, FakeFactory
from daeyeon_bot.infra.pr_review_persona import PersonaLoader
from tests.fakes.gh_cli import FakeGh
from tests.fakes.pr_persona import materialize_persona

pytestmark = pytest.mark.integration


_PERSONA_BODY = (
    "You are a careful Python reviewer. Always look for off-by-one bugs.\n"
    "Always identify the head SHA in the first sentence of the Summary.\n"
    "Speak plainly and propose concrete fixes when you spot a problem."
)


_PATCH_HUNK = "@@ -1,3 +1,5 @@\n ctx\n+added line one\n+added line two\n ctx\n ctx\n"


def _claude_response(head_sha: str) -> str:
    return json.dumps(
        {
            "verdict": "PASS",
            "summary": (
                f"Reviewed at SHA {head_sha}. Looks fine, with one nit on the second added line."
            ),
            "comments": [
                {
                    "path": "lib/foo.py",
                    "line": 3,
                    "side": "RIGHT",
                    "body": "Consider naming this constant.",
                }
            ],
        }
    )


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    materialize_persona(root, "pr-reviewer", _PERSONA_BODY)
    return root


@pytest.fixture
def config_file(tmp_path: Path, state_dir: Path) -> Path:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"""
[runtime]
state_dir = {str(state_dir)!r}

[logging]
level = "WARNING"
format = "console"

[github]
username = "daeyeon-lee"

[handlers.echo]
enabled = false

[handlers.pr_review]
enabled = true
persona_skill = "pr-reviewer"
min_persona_chars = 50

[routing]
"pr.review.manual" = ["pr_review"]
""".lstrip(),
        encoding="utf-8",
    )
    return cfg_path


async def test_pause_blocks_review_then_resume_drains(
    config_file: Path,
    state_dir: Path,
    skills_root: Path,
) -> None:
    head_sha = "feedface1234feedface"
    repo = "octo/cat"
    pr_number = 7

    fake_gh = FakeGh(user_login="daeyeon-lee")
    fake_gh.add_pr(
        repo,
        pr_number,
        head_sha=head_sha,
        author="alice",
        requested=("daeyeon-lee",),
        files=[{"filename": "lib/foo.py", "patch": _PATCH_HUNK, "changes": 4}],
    )
    fake_session = FakeClaudeSession(default=_claude_response(head_sha))
    factory = FakeFactory(session=fake_session)
    persona_loader = PersonaLoader(skills_root=skills_root)

    overrides = ContainerOverrides(
        claude_session_factory=factory,
        gh=fake_gh,
        persona_loader=persona_loader,
        github_username="daeyeon-lee",
    )

    cfg = load(str(config_file))
    db_path = cfg.db_path
    pause_flag = cfg.pause_flag_path

    # Drop the PAUSE flag BEFORE booting so the dispatcher's first poll sees it.
    pause_mod.pause(pause_flag)
    assert pause_mod.is_paused(pause_flag)

    stop = asyncio.Event()
    boot_task = asyncio.create_task(
        boot(
            BootOptions(
                config_path=str(config_file),
                overrides=overrides,
                install_signal_handlers=False,
                external_stop_event=stop,
            )
        )
    )

    try:
        for _ in range(50):
            if db_path.exists():
                break
            await asyncio.sleep(0.05)
        assert db_path.exists()

        # Insert event + outbox row while paused.
        payload = {
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "request_gen": "0",
            "force": False,
        }
        dedup_seed = f"manual-pr-review|{repo}#{pr_number}@{head_sha}|0|False"
        dedup_key = hashlib.sha256(dedup_seed.encode("utf-8")).hexdigest()
        now = datetime.now(tz=UTC)
        event = make_event(type="pr.review.manual", payload=payload, created_at=now)
        async with storage.connection(db_path) as conn:
            await storage.apply_migrations(conn)
            await outbox.insert_event(
                conn, event, source="pr_review_manual", source_dedup_key=dedup_key
            )
            await outbox.enqueue_handler(conn, event_id=event.id, handler="pr_review", now=now)
            await conn.commit()

        # Give the dispatcher poll loop a few cycles. Row must NOT advance
        # to 'acked' and FakeGh must NOT see a post.
        for _ in range(10):
            await asyncio.sleep(0.1)
            assert fake_gh.posted_reviews() == [], "post leaked while PAUSE flag was active"

        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT status FROM outbox WHERE event_id = ?", (event.id,)
            ) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert row["status"] in {"pending", "retry"}, (
            f"expected paused row to sit in pending/retry, got {row['status']!r}"
        )

        # Clear PAUSE; dispatcher should claim on the next poll cycle.
        pause_mod.resume(pause_flag)
        assert not pause_mod.is_paused(pause_flag)

        for _ in range(200):
            async with storage.connection(db_path) as conn:
                async with conn.execute(
                    "SELECT status FROM outbox WHERE event_id = ?", (event.id,)
                ) as cur:
                    row = await cur.fetchone()
            if row is not None and row["status"] == "acked":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("outbox row never reached 'acked' after resume")

        # Post landed exactly once and matches the expected SHA.
        posted = fake_gh.posted_reviews()
        assert len(posted) == 1
        assert posted[0]["commit_id"] == head_sha
        assert head_sha in posted[0]["body"]

        # Audit row recorded.
        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT status, review_id FROM pr_review_audit"
                " WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (event.id,),
            ) as cur:
                audit = await cur.fetchone()
        assert audit is not None
        assert audit["status"] == "posted"
        assert audit["review_id"] is not None
    finally:
        stop.set()
        await asyncio.wait_for(boot_task, timeout=10.0)
        # Best-effort cleanup of the pause flag.
        if pause_mod.is_paused(pause_flag):
            pause_mod.resume(pause_flag)
