"""PAUSE kill-switch for jira_triage (T053).

Mirrors `test_pr_review_pause.py`: the operator drops PAUSE before the
event lands, the outbox row sits in `pending`, no FakeJira.post_comment
is recorded. Clearing PAUSE drains the row to `acked` + writes the
audit `status='posted'`.

The handler's QuotaError raise path is already verified at the unit
level; this test exercises the full daemon stack honouring it.
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
from daeyeon_bot.infra.jira_client import FieldDiscovery, JiraIdentity
from daeyeon_bot.infra.persona_loader import PersonaLoader
from daeyeon_bot.infra.ssw_bundle import SswBundleClient
from tests.fakes.jira_client import FakeJiraClient
from tests.fakes.loki import FakeLokiClient
from tests.fakes.ssh_logs import FakeSshLogClient
from tests.fakes.ssw_bundle_fixture import build_fixture

pytestmark = pytest.mark.integration


def _bundled_persona_root() -> Path:
    return Path(__file__).resolve().parents[2] / ".claude" / "skills"


def _claude_response() -> str:
    return json.dumps(
        {
            "symptom": "TDR (kernel) — FW abort 증상.",
            "evidence": [
                {
                    "source": "loki.kernel",
                    "quote": "TDR detected",
                    "citation": "2026-05-13T06:55:12Z",
                }
            ],
            "domain": "CpFw",
            "layer_rationale": "kernel TDR는 FW abort의 backtracking 증상.",
            "next_data": ["dmesg 캡처"],
            "severity": "sev2",
            "suspected_duplicates": [],
            "needs_human": False,
        }
    )


async def test_pause_blocks_triage_then_resume_drains(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fixture = build_fixture(tmp_path)
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f"""
[runtime]
state_dir = {str(state_dir)!r}

[logging]
level = "WARNING"
format = "console"

[handlers.echo]
enabled = false

[handlers.jira_triage]
enabled = true
allowed_projects = ["SSWCI"]
persona_skill = "daeyeon-bot-jira-triage"
min_persona_chars = 200
timeout_seconds = 60
ssw_bundle_path = {str(tmp_path / "var" / "ssw-bundle")!r}
allow_external_ssw_bundle = true

[routing]
"jira.triage.manual" = ["jira_triage"]
""".lstrip(),
        encoding="utf-8",
    )

    jira = FakeJiraClient()
    body = (
        "Start: 2026-05-13 06:54:48.924242\n"
        "End: 2026-05-13 07:07:38.172125\n"
        "ssh://automation@ssw-giga-02:"
        "/mnt/data/logs/regression-test/25746526668-1/ssw-giga-02/"
        "TC-0033-Dram_test_with_exception\n"
        "{noformat}\nstack trace\n{noformat}"
    )
    jira.add_issue(
        key="SSWCI-9999",
        summary="regression-test . ssw-giga-02 . TC-0033-Dram_test_with_exception",
        project="SSWCI",
        parent_key="SSWCI-9998",
        description_text=body,
    )
    jira.add_issue(
        key="SSWCI-9998",
        summary="Epic",
        project="SSWCI",
        issuetype_name="Epic",
        custom_fields={
            "customfield_10042": "release/v3.2",
            "customfield_10043": fixture.main_commit,
        },
    )

    loki = FakeLokiClient()
    loki.set_response("kernel", lines=("TDR detected",))
    loki.set_response("fwlog", lines=())
    loki.set_response("smclog", lines=())
    loki.set_response("syslog", lines=())
    ssh = FakeSshLogClient()
    ssh.add_file(
        host="ssw-giga-02",
        remote_path="/mnt/data/logs/regression-test/25746526668-1/ssw-giga-02/TC-0033-Dram_test_with_exception",
        filename="output.xml",
        contents=b"<?xml ?>",
    )

    fake_session = FakeClaudeSession(default=_claude_response())
    factory = FakeFactory(session=fake_session)
    ssw = SswBundleClient(
        clone_path=tmp_path / "var" / "ssw-bundle",
        remote_url=fixture.bundle_remote_url,
        project_root=tmp_path,
    )
    persona_loader = PersonaLoader(skills_root=_bundled_persona_root())

    overrides = ContainerOverrides(
        claude_session_factory=factory,
        jira=jira,
        loki=loki,
        ssh=ssh,
        ssw_bundle=ssw,
        persona_loader=persona_loader,
        jira_identity=JiraIdentity(
            account_id="557058:fake",
            email_address="daeyeon.lee@rebellions.ai",
            display_name="daeyeon",
        ),
        field_discovery=FieldDiscovery(
            branch_field_id="customfield_10042",
            commit_field_id="customfield_10043",
            team_field_id="",
            issuetype_name="Bug",
        ),
        project_root=tmp_path,
    )

    cfg = load(str(config_file))
    db_path = cfg.db_path
    pause_flag = cfg.pause_flag_path

    # Drop PAUSE flag BEFORE booting so the dispatcher's first poll sees it.
    state_dir.mkdir(exist_ok=True)
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

        # Insert event while paused.
        payload = {
            "issue_key": "SSWCI-9999",
            "force": False,
            "comment_seq": "1",
        }
        dedup_seed = "manual-jira-triage|SSWCI-9999|1"
        dedup_key = hashlib.sha256(dedup_seed.encode("utf-8")).hexdigest()
        now = datetime.now(tz=UTC)
        event = make_event(type="jira.triage.manual", payload=payload, created_at=now)
        async with storage.connection(db_path) as conn:
            await storage.apply_migrations(conn)
            await outbox.insert_event(
                conn, event, source="jira_triage_manual", source_dedup_key=dedup_key
            )
            await outbox.enqueue_handler(conn, event_id=event.id, handler="jira_triage", now=now)
            await conn.commit()

        # While paused, the row must NOT advance to acked and Jira post must NOT be called.
        for _ in range(10):
            await asyncio.sleep(0.1)
            assert jira.posted_comments() == [], "comment leaked while PAUSE flag was active"

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

        for _ in range(400):
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

        # Post landed exactly once + audit recorded.
        posted = jira.posted_comments()
        assert len(posted) == 1
        assert posted[0].key == "SSWCI-9999"

        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT status, comment_id FROM jira_triage_audit"
                " WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (event.id,),
            ) as cur:
                audit = await cur.fetchone()
        assert audit is not None
        assert audit["status"] == "posted"
        assert audit["comment_id"] is not None
    finally:
        stop.set()
        await asyncio.wait_for(boot_task, timeout=10.0)
        if pause_mod.is_paused(pause_flag):
            pause_mod.resume(pause_flag)
