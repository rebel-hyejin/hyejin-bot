"""Persona hot-reload for jira_triage (T052).

Edits the persona's SKILL.md between two triage runs, bumps mtime, and
verifies the second triage's audit row records the NEW `persona_mtime_ns`
(distinct from the first). This proves the PersonaLoader's stat-on-each-
review cache invalidation works end-to-end through the daemon stack.

We use a tmp_path-local skills_root so we can mutate the file without
touching the repo-bundled default. Two distinct `force=true` events are
fired so neither hits `skipped_already_triaged`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


_PERSONA_V1_BODY = (
    "# Stage 1\nThis persona only analyzes the Run Snapshot.\n"
    "Symptom: cite. Evidence: cite. Likely layer: cite. Next data: cite.\n"
    "Domain ENUMs: Driver / SysFw / CpFw / SysSol / DevOps / Connectivity.\n"
    "Stage 1 only. Stage 2 deferred to PR-4.\n"
    "Output strictly JSON. PERSONA_V1\n" * 3
)
_PERSONA_V2_BODY = (
    "# Stage 1\nUpdated persona — adds new triage rule.\n"
    "Symptom: cite. Evidence: cite. Likely layer: cite. Next data: cite.\n"
    "Domain ENUMs: Driver / SysFw / CpFw / SysSol / DevOps / Connectivity.\n"
    "Stage 1 only. Stage 2 deferred to PR-4.\n"
    "New rule: always prefer hardware over software when ambiguous. PERSONA_V2\n" * 3
)


def _materialize_persona(root: Path, body: str) -> Path:
    skill_dir = root / "daeyeon-bot-jira-triage"
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: daeyeon-bot-jira-triage\ndescription: test persona.\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def _claude_response() -> str:
    return json.dumps(
        {
            "summary_md": (
                "h3. Symptom\nTDR.\n\nh3. Evidence cited\n- loki.kernel — `TDR detected`\n\n"
                "h3. Likely layer\n*CpFw*\n\nh3. Next data to collect\n- dmesg"
            ),
            "domain": "CpFw",
            "severity": "sev2",
            "suspected_duplicates": [],
            "needs_human": False,
            "evidence": [
                {
                    "source": "loki.kernel",
                    "quote": "TDR detected",
                    "citation": "2026-05-13T06:55:12Z",
                }
            ],
        }
    )


async def _enqueue_manual(
    *,
    db_path: Path,
    issue_key: str,
    comment_seq: str,
    force: bool,
) -> str:
    payload = {"issue_key": issue_key, "force": force, "comment_seq": comment_seq}
    dedup_key = hashlib.sha256(f"manual-jira-triage|{issue_key}|{comment_seq}".encode()).hexdigest()
    now = datetime.now(tz=UTC)
    event = make_event(type="jira.triage.manual", payload=payload, created_at=now)
    async with storage.connection(db_path) as conn:
        await storage.apply_migrations(conn)
        await outbox.insert_event(
            conn, event, source="jira_triage_manual", source_dedup_key=dedup_key
        )
        await outbox.enqueue_handler(conn, event_id=event.id, handler="jira_triage", now=now)
        await conn.commit()
    return event.id


async def _wait_until_acked(*, db_path: Path, event_id: str) -> bool:
    for _ in range(400):
        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT status FROM outbox WHERE event_id = ?", (event_id,)
            ) as cur:
                row = await cur.fetchone()
        if row is not None and row["status"] == "acked":
            return True
        await asyncio.sleep(0.05)
    return False


async def test_persona_mtime_change_picked_up_on_next_triage(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fixture = build_fixture(tmp_path)
    skills_root = tmp_path / "skills"

    persona_path = _materialize_persona(skills_root, _PERSONA_V1_BODY)

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
min_persona_chars = 100
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
        "{noformat}\nstack\n{noformat}"
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

    # Same scripted Claude response for both runs — the persona body changes
    # but we don't need a different Claude output to verify the mtime bump
    # was observed. (The system_prompt sent to the FakeClaudeSession would
    # differ, but FakeClaudeSession doesn't assert on it here.)
    fake_session = FakeClaudeSession(default=_claude_response())
    factory = FakeFactory(session=fake_session)

    ssw = SswBundleClient(
        clone_path=tmp_path / "var" / "ssw-bundle",
        remote_url=fixture.bundle_remote_url,
        project_root=tmp_path,
    )
    persona_loader = PersonaLoader(skills_root=skills_root)

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

        # 1st triage with persona v1.
        event1_id = await _enqueue_manual(
            db_path=db_path,
            issue_key="SSWCI-9999",
            comment_seq="manual_111",
            force=True,
        )
        assert await _wait_until_acked(db_path=db_path, event_id=event1_id)

        # Capture the first triage's recorded mtime_ns.
        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT persona_mtime_ns FROM jira_triage_audit"
                " WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (event1_id,),
            ) as cur:
                row1 = await cur.fetchone()
        assert row1 is not None
        v1_mtime_ns = int(row1["persona_mtime_ns"])
        assert v1_mtime_ns > 0

        # Bump the persona file. Use ns-precision `os.utime(..., ns=...)` so
        # we don't lose mtime resolution to float64 round-trip.
        persona_path.write_text(
            f"---\nname: daeyeon-bot-jira-triage\ndescription: edited.\n---\n\n{_PERSONA_V2_BODY}",
            encoding="utf-8",
        )
        bumped = v1_mtime_ns + 5_000_000_000  # +5s in ns
        os.utime(persona_path, ns=(bumped, bumped))

        # 2nd triage with persona v2 (force=true with distinct comment_seq so
        # we don't hit `skipped_already_triaged`).
        event2_id = await _enqueue_manual(
            db_path=db_path,
            issue_key="SSWCI-9999",
            comment_seq="manual_222",
            force=True,
        )
        assert await _wait_until_acked(db_path=db_path, event_id=event2_id)

        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT persona_mtime_ns FROM jira_triage_audit"
                " WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (event2_id,),
            ) as cur:
                row2 = await cur.fetchone()
        assert row2 is not None
        v2_mtime_ns = int(row2["persona_mtime_ns"])

        # The persona loader saw the new mtime and re-read.
        assert v2_mtime_ns != v1_mtime_ns
        assert v2_mtime_ns > v1_mtime_ns

        # Two posted comments (one per force triage).
        assert len(jira.posted_comments()) == 2
    finally:
        stop.set()
        await asyncio.wait_for(boot_task, timeout=10.0)
