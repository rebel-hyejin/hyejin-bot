"""GitHub PR review handler (T024).

The single handler that consumes both `gh.review_requested` (auto path) and
`pr.review.manual` (CLI path). Stages, in the exact order required by
`data-model.md` §4:

    (a) load active persona (validation failure → DeadLetter)
    (b) gh.pr_get to refresh head SHA + author + requested reviewers
    (c) self-authored skip
    (d) withdrawn skip (no longer a requested reviewer; or PR closed)
    (e) gh.pr_files → size budget; if exceeded post the templated "too
        large" Summary then Ack + audit `skipped_too_large`
    (f) audit lookup for `(repo, pr, head_sha)`; if found and not force
        → Ack + audit `skipped_already_reviewed`
    (g) call Claude; validate with `ReviewOutput`; retry once on validate
        failure; second failure → DeadLetter
    (h) `_filter_anchors` folds out-of-hunk inline comments into the Summary
    (h.5) `_redact` over Summary + every comment body — any match raises
          PermanentError("redaction would alter posted content")
    (i) prepend supersede header when force-reviewing on top of a prior
        posted review
    (j) gh.post_review (event="COMMENT" — baked-in inside `gh_cli`)
    (k) record_supersede + insert_audit(status='posted') + Ack

`gh_cli` raises `AuthError` / `RateLimitError` / `TransientError` /
`PermanentError`; the dispatcher's exception mapping turns those into halt
/ Retry / DeadLetter without further translation in this module.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast, runtime_checkable

import structlog
from pydantic import ValidationError as PydanticValidationError

from daeyeon_bot.app.config import PrReviewHandlerEntry
from daeyeon_bot.core.errors import (
    PermanentError,
    ValidationError,
)
from daeyeon_bot.core.events import Event
from daeyeon_bot.core.manifest import HandlerManifest
from daeyeon_bot.core.protocols import HandlerContext
from daeyeon_bot.core.results import Ack, HandlerResult
from daeyeon_bot.handlers.pr_review_diff import (
    is_anchor_in_hunk,
    parse_hunk_ranges,
)
from daeyeon_bot.handlers.pr_review_schemas import InlineComment, ReviewOutput
from daeyeon_bot.infra.logging import redact_text
from daeyeon_bot.infra.pr_review_audit import (
    find_latest,
    insert_audit,
    record_supersede,
)
from daeyeon_bot.infra.pr_review_persona import PersonaLoader

_log = structlog.get_logger(__name__)

MANIFEST = HandlerManifest(
    name="pr_review",
    idempotent=True,
    dedup_ttl=timedelta(days=1),
    side_effect_key=None,
    concurrency=1,
    accepts=("gh.review_requested", "pr.review.manual"),
)

# Templated body when the size budget is exceeded (claude-review-output.md §5).
_TOO_LARGE_TEMPLATE = (
    "This PR is too large for an automated review at SHA `{head_sha}`.\n"
    "\n"
    "- Changed files: {n_files} (limit {max_files})\n"
    "- Changed lines: {n_lines} (limit {max_lines})\n"
    "\n"
    "Consider splitting the change into smaller PRs and re-requesting review."
)


@runtime_checkable
class _GhClient(Protocol):
    """The subset of `infra.gh_cli.GhCli` this handler needs."""

    async def pr_get(self, repo: str, pr_number: int) -> dict[str, Any]: ...
    async def pr_files(self, repo: str, pr_number: int) -> list[dict[str, Any]]: ...
    async def post_review(
        self,
        repo: str,
        pr_number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


# Async callable returning `None` when no PAUSE flag is up, raising QuotaError
# otherwise. The container wires this from `app.pause.is_paused`.
PauseGuard = Callable[[], Awaitable[None]]


async def _no_pause() -> None:
    """Default no-op pause guard used when the container hasn't wired one."""
    return None


# Connection factory that yields an aiosqlite connection for one-shot writes.
# The handler's `audit_writer` callable is wired by the container; tests
# pass a small wrapper that uses the same `tmp_path` connection.
AuditWriter = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class PrReviewHandler:
    """Consumes `gh.review_requested` and `pr.review.manual` events."""

    manifest: HandlerManifest
    gh: _GhClient
    persona_loader: PersonaLoader
    config: PrReviewHandlerEntry
    github_username: str
    db: Any  # aiosqlite.Connection — Any to avoid circular type stub on tests.
    pause_guard: PauseGuard = _no_pause

    async def handle(  # noqa: PLR0915 — sequential gates; decomposing further hurts readability
        self, event: Event, ctx: HandlerContext
    ) -> HandlerResult:
        # PAUSE check first so even the size-budget short-circuit honors it.
        await self.pause_guard()

        repo, pr_number, payload_head_sha, request_gen, force = _parse_payload(event)
        now = ctx.clock.now()

        # ── (a) Persona ────────────────────────────────────────────────────
        skill_name = self.config.persona_skill or ""
        try:
            persona = self.persona_loader.load(skill_name, min_chars=self.config.min_persona_chars)
        except ValidationError as exc:
            # Persona load failed — record the *configured* skill name so the
            # operator can correlate the failure with which persona file was
            # active. `persona_mtime_ns` is unknown at this point.
            await self._write_audit(
                event_id=event.id,
                repo=repo,
                pr_number=pr_number,
                head_sha=payload_head_sha,
                request_gen=request_gen,
                status="failed",
                persona_skill=skill_name or None,
                error=str(exc),
                created_at=now,
            )
            raise

        # ── (b) Refresh PR metadata ────────────────────────────────────────
        pr = await self.gh.pr_get(repo, pr_number)
        current_head = _read_head_sha(pr)
        author_login = _read_author(pr)
        requested_logins = _read_requested_logins(pr)
        pr_state = str(pr.get("state", "open"))

        # Use the *current* head SHA going forward — payload's was a snapshot.
        head_sha = current_head or payload_head_sha

        audit_kwargs: dict[str, Any] = {
            "event_id": event.id,
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "request_gen": request_gen,
            "persona_skill": persona.name,
            "persona_mtime_ns": persona.mtime_ns,
        }

        # ── (c) Self-authored skip ─────────────────────────────────────────
        if author_login and author_login == self.github_username:
            await self._write_audit(
                **audit_kwargs,
                status="skipped_self_authored",
                created_at=now,
            )
            _log.info(
                "pr_review.skipped_self_authored",
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
            )
            return Ack()

        # ── (d) Withdrawn skip ─────────────────────────────────────────────
        # Manual force re-runs honor the request even when the PR is no longer
        # in the requested-reviewers list; auto runs always require it.
        if not force:
            withdrawn = pr_state != "open" or self.github_username not in requested_logins
            if withdrawn:
                await self._write_audit(
                    **audit_kwargs,
                    status="skipped_withdrawn",
                    created_at=now,
                )
                _log.info(
                    "pr_review.skipped_withdrawn",
                    repo=repo,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    state=pr_state,
                )
                return Ack()

        # ── (e) Size budget ────────────────────────────────────────────────
        files_raw = await self.gh.pr_files(repo, pr_number)
        files = [_normalize_file(f) for f in files_raw]
        n_files = len(files)
        n_lines = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files)
        budget = self.config.size_budget
        if n_files > budget.max_files or n_lines > budget.max_lines:
            await self.pause_guard()
            summary = _TOO_LARGE_TEMPLATE.format(
                head_sha=head_sha,
                n_files=n_files,
                max_files=budget.max_files,
                n_lines=n_lines,
                max_lines=budget.max_lines,
            )
            posted = await self.gh.post_review(
                repo,
                pr_number,
                commit_id=head_sha,
                body=summary,
                comments=[],
            )
            await self._write_audit(
                **audit_kwargs,
                status="skipped_too_large",
                review_id=_read_review_id(posted),
                submitted_at=_read_submitted_at(posted),
                summary_chars=len(summary),
                inline_comment_count=0,
                created_at=now,
            )
            _log.info(
                "pr_review.skipped_too_large",
                repo=repo,
                pr_number=pr_number,
                n_files=n_files,
                n_lines=n_lines,
            )
            return Ack()

        # ── (f) Already-reviewed short-circuit ─────────────────────────────
        prior = await find_latest(self.db, repo, pr_number, head_sha)
        already_posted = prior is not None and prior.status == "posted"
        if already_posted and not force:
            await self._write_audit(
                **audit_kwargs,
                status="skipped_already_reviewed",
                created_at=now,
            )
            _log.info(
                "pr_review.skipped_already_reviewed",
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
                prior_review_id=prior.review_id if prior else None,
            )
            return Ack()

        # ── (g) Call Claude with validate-once-retry-once ──────────────────
        snapshot_text = _render_user_message(
            repo=repo,
            pr_number=pr_number,
            title=str(pr.get("title", "")),
            body=str(pr.get("body") or ""),
            author_login=author_login,
            head_sha=head_sha,
            files=files,
        )
        system_prompt = persona.body
        await self.pause_guard()
        review = await self._call_claude_with_retry(
            ctx=ctx,
            system_prompt=system_prompt,
            user_message=snapshot_text,
        )

        # ── (h) Filter inline anchors ──────────────────────────────────────
        kept, folded = _filter_anchors(review.comments, files)
        summary = review.summary
        if folded:
            summary = _append_folded_bullets(summary, folded)

        # ── (h.5) Redaction guard ──────────────────────────────────────────
        _enforce_redaction(summary, kept)

        # ── (i) Force-supersede header ─────────────────────────────────────
        is_force_supersede = force and already_posted and prior is not None
        if is_force_supersede and prior is not None and prior.submitted_at is not None:
            header = (
                f"Updated review for SHA {head_sha}"
                f" (supersedes earlier bot review posted at"
                f" {prior.submitted_at.strftime('%H:%M:%S UTC')})"
            )
            summary = header + "\n\n" + summary

        # ── (j) Post the review ────────────────────────────────────────────
        await self.pause_guard()
        posted = await self.gh.post_review(
            repo,
            pr_number,
            commit_id=head_sha,
            body=summary,
            comments=[_inline_to_api(c) for c in kept],
        )
        new_review_id = _read_review_id(posted)
        new_submitted_at = _read_submitted_at(posted)

        # ── (k) Audit: supersede or insert ─────────────────────────────────
        if is_force_supersede and prior is not None and new_submitted_at is not None:
            await record_supersede(
                self.db,
                prior.id,
                new_review_id=new_review_id or 0,
                new_submitted_at=new_submitted_at,
            )
            await self.db.commit()
        else:
            await self._write_audit(
                **audit_kwargs,
                status="posted",
                review_id=new_review_id,
                submitted_at=new_submitted_at,
                summary_chars=len(summary),
                inline_comment_count=len(kept),
                created_at=now,
            )

        _log.info(
            "pr_review.posted",
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            review_id=new_review_id,
            inline_count=len(kept),
            superseded=is_force_supersede,
        )
        return Ack()

    # ── helpers ────────────────────────────────────────────────────────────

    async def _write_audit(self, **kwargs: Any) -> None:
        await insert_audit(self.db, **kwargs)
        await self.db.commit()

    async def _call_claude_with_retry(
        self,
        *,
        ctx: HandlerContext,
        system_prompt: str,
        user_message: str,
    ) -> ReviewOutput:
        last_error: str | None = None
        for attempt in (0, 1):
            session = ctx.claude_session_factory()
            async with session as s:  # type: ignore[attr-defined]
                response_obj = await s.query(  # type: ignore[attr-defined]
                    user_message
                    if last_error is None
                    else (
                        user_message
                        + "\n\n---\nYour previous response failed validation: "
                        + last_error
                        + "\nReturn ONLY the corrected JSON object."
                    ),
                    system=system_prompt,
                )
            response = cast("str", response_obj)
            try:
                parsed = json.loads(_strip_code_fence(response))
            except (TypeError, ValueError) as exc:
                last_error = f"JSON parse: {exc}"
                if attempt == 1:
                    raise PermanentError(
                        f"claude returned malformed review (parse): {exc}"
                    ) from exc
                continue
            try:
                return ReviewOutput.model_validate(parsed)
            except PydanticValidationError as exc:
                last_error = exc.errors().__repr__()
                if attempt == 1:
                    raise PermanentError("claude returned malformed review (schema)") from exc
                continue
        raise PermanentError("unreachable: claude retry loop fell through")


# ── module-level helpers ──────────────────────────────────────────────────


def _parse_payload(event: Event) -> tuple[str, int, str, str, bool]:
    payload = event.payload
    repo = str(payload.get("repo", ""))
    pr_number_raw = payload.get("pr_number")
    head_sha = str(payload.get("head_sha", ""))
    request_gen = str(payload.get("request_gen", "0"))
    force = bool(payload.get("force", False))
    if not repo or pr_number_raw is None or not head_sha:
        raise ValidationError("pr_review payload must include repo, pr_number, head_sha")
    return (repo, int(pr_number_raw), head_sha, request_gen, force)


def _read_head_sha(pr: dict[str, Any]) -> str | None:
    head = pr.get("head")
    if isinstance(head, dict):
        sha = head.get("sha")  # type: ignore[union-attr]
        if isinstance(sha, str):
            return sha
    return None


def _read_author(pr: dict[str, Any]) -> str:
    user = pr.get("user")
    if isinstance(user, dict):
        login = user.get("login")  # type: ignore[union-attr]
        if isinstance(login, str):
            return login
    return ""


def _read_requested_logins(pr: dict[str, Any]) -> tuple[str, ...]:
    raw = pr.get("requested_reviewers", [])
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for entry in raw:  # type: ignore[assignment]
        if isinstance(entry, dict):
            login = entry.get("login")  # type: ignore[union-attr]
            if isinstance(login, str):
                out.append(login)
    return tuple(out)


def _normalize_file(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": str(raw.get("filename") or raw.get("path") or ""),
        "additions": int(raw.get("additions") or 0),
        "deletions": int(raw.get("deletions") or 0),
        "status": str(raw.get("status") or "modified"),
        "patch": raw.get("patch") if isinstance(raw.get("patch"), str) else None,
    }


def _read_review_id(posted: dict[str, Any]) -> int | None:
    raw = posted.get("id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _read_submitted_at(posted: dict[str, Any]) -> datetime | None:
    raw = posted.get("submitted_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # GitHub returns "Z"-suffixed ISO; Python 3.12 fromisoformat handles it.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _strip_code_fence(text: str) -> str:
    """Tolerate Claude wrapping its JSON in ```json … ``` despite the prompt."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop first line (``` or ```json) and trailing ``` if present.
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _filter_anchors(
    comments: list[InlineComment],
    files: list[dict[str, Any]],
) -> tuple[list[InlineComment], list[InlineComment]]:
    """Split `comments` into (kept, folded). Folded ones go into Summary bullets."""
    by_path: dict[str, list[tuple[int, int]]] = {}
    for f in files:
        patch = f.get("patch")
        path = str(f.get("filename", ""))
        if not path or not isinstance(patch, str):
            continue
        by_path[path] = parse_hunk_ranges(patch)
    kept: list[InlineComment] = []
    folded: list[InlineComment] = []
    for comment in comments:
        hunks = by_path.get(comment.path)
        if hunks is None:
            folded.append(comment)
            continue
        if is_anchor_in_hunk(comment.line, comment.start_line, hunks):
            kept.append(comment)
        else:
            folded.append(comment)
    return (kept, folded)


def _append_folded_bullets(summary: str, folded: list[InlineComment]) -> str:
    bullets = "\n".join(f"- [{c.path} near L{c.line}] {c.body}" for c in folded)
    if not bullets:
        return summary
    if summary.endswith("\n"):
        return summary + "\n" + bullets
    return summary + "\n\n" + bullets


def _enforce_redaction(summary: str, comments: list[InlineComment]) -> None:
    """Raise PermanentError if redaction would mutate any posted text (FR-015)."""
    if redact_text(summary) != summary:
        raise PermanentError("redaction would alter posted content (summary)")
    for comment in comments:
        if redact_text(comment.body) != comment.body:
            raise PermanentError(
                f"redaction would alter posted content (comment on {comment.path})"
            )


def _inline_to_api(comment: InlineComment) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": comment.path,
        "line": comment.line,
        "side": comment.side,
        "body": comment.body,
    }
    if comment.start_line is not None:
        payload["start_line"] = comment.start_line
        payload["start_side"] = comment.side
    return payload


def _render_user_message(
    *,
    repo: str,
    pr_number: int,
    title: str,
    body: str,
    author_login: str,
    head_sha: str,
    files: list[dict[str, Any]],
) -> str:
    """Render the snapshot the way `contracts/claude-review-output.md` §2 specs."""
    additions = sum(int(f.get("additions") or 0) for f in files)
    deletions = sum(int(f.get("deletions") or 0) for f in files)
    parts: list[str] = [
        f"Repository: {repo}",
        f"PR #{pr_number}: {title}",
        f"Author: @{author_login}",
        f"Head commit SHA: {head_sha}",
        "",
        "PR description:",
        "---",
        body,
        "---",
        "",
        f"Changed files ({len(files)}, +{additions} / -{deletions} lines):",
        "",
    ]
    for f in files:
        path = f.get("filename")
        status = f.get("status")
        adds = f.get("additions")
        dels = f.get("deletions")
        parts.append(f"### {path}  (status: {status}, +{adds}/-{dels})")
        patch = f.get("patch")
        if isinstance(patch, str):
            parts.append("```diff")
            parts.append(patch)
            parts.append("```")
        else:
            parts.append("(binary or oversized — diff omitted)")
        parts.append("")
    return "\n".join(parts)


# Avoid unused-import lint when type stubs aren't strict in this build.
_AUDIT_WRITER: AuditWriter | None = None
__all__ = ["MANIFEST", "PrReviewHandler"]
