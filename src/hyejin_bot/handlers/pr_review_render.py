"""Rendering helpers for the `pr_review` handler.

Two pure functions that don't depend on handler state:

- `render_user_message` — builds the diff snapshot prompt the way
  `contracts/claude-review-output.md` §2 specifies.
- `inline_to_api` — converts a validated `InlineComment` into the
  GitHub Reviews API payload (single-line vs multi-line anchor).

Split out of `pr_review.py` to keep that file under the 800-line soft
limit and to isolate prompt-shape changes from handler control flow.
"""

from __future__ import annotations

from typing import Any

from hyejin_bot.handlers.pr_review_schemas import InlineComment


def inline_to_api(comment: InlineComment) -> dict[str, Any]:
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


# Per-prior-review body cap — bigger reviews get truncated so the
# user message stays under the model's effective context budget.
_PRIOR_BODY_CAP_CHARS = 2000


def render_user_message(
    *,
    repo: str,
    pr_number: int,
    title: str,
    body: str,
    author_login: str,
    head_sha: str,
    files: list[dict[str, Any]],
    prior_reviews: list[dict[str, Any]] | None = None,
    other_comments: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Render the snapshot the way `contracts/claude-review-output.md` §2 specs.

    When `prior_reviews` is non-empty, a `Prior reviews (most recent first)`
    section is inserted before `Changed files` so the persona can produce
    Resolved / Still open / New buckets per the SKILL `Re-review` mode.

    When `other_comments` is non-empty (one of the three buckets has any
    entry), an `Existing PR comments by other actors` section follows.
    The persona uses it for trait #9 dedup: skip a new finding when an
    existing comment already covers the same file:line + rule + open
    thread, and emit `[CONFIRM]` / `[REFINE]` reply text instead.
    """
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
    ]

    if prior_reviews:
        parts.extend(_render_prior_reviews_section(prior_reviews))

    if other_comments and any(other_comments.values()):
        parts.extend(_render_other_comments_section(other_comments))

    parts.append(f"Changed files ({len(files)}, +{additions} / -{deletions} lines):")
    parts.append("")
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


def _render_prior_reviews_section(prior_reviews: list[dict[str, Any]]) -> list[str]:
    """Emit a `Prior reviews` section listing each review's body + inline comments.

    Reviews are rendered most-recent-first. Each review body is truncated
    to `_PRIOR_BODY_CAP_CHARS`; truncation is signaled with a literal
    `... [truncated]` marker so the persona knows not to treat absence as
    evidence the prior didn't say something.
    """
    out: list[str] = [
        f"Prior reviews ({len(prior_reviews)} most recent, by hyejin-bot):",
        "---",
    ]
    for i, r in enumerate(prior_reviews, start=1):
        submitted = str(r.get("submitted_at", ""))
        commit = str(r.get("commit_id", ""))[:8]
        state = str(r.get("state", ""))
        body = str(r.get("body") or "")
        if len(body) > _PRIOR_BODY_CAP_CHARS:
            body = body[:_PRIOR_BODY_CAP_CHARS] + "\n... [truncated]"
        out.append(f"### Prior #{i} — submitted {submitted} on {commit} (state={state})")
        out.append(body)
        inlines = r.get("inline_comments")
        if isinstance(inlines, list) and inlines:
            out.append("")
            out.append("Inline comments on this prior review:")
            for c in inlines:
                if not isinstance(c, dict):
                    continue
                path = c.get("path")
                line = c.get("line") or c.get("original_line")
                raw_body = c.get("body")
                body_str: str = raw_body if isinstance(raw_body, str) else ""
                cbody = body_str.replace("\n", " ⏎ ")
                if len(cbody) > 400:
                    cbody = cbody[:400] + "..."
                out.append(f"- {path}:{line} — {cbody}")
        out.append("")
    out.append("---")
    out.append("")
    return out


def _render_other_comments_section(
    other_comments: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Emit the cross-actor comment bundle the persona uses for trait-#9 dedup.

    Compact one-line-per-comment formatting — the LLM only needs author,
    body, and (for review_comments) file:line to match candidates against
    its own pending findings. Bodies are truncated to `_OTHER_BODY_CAP`
    so we don't burn context on long quoted CI logs.

    Three buckets, each optional. We emit even when one is empty so the
    header is stable and the persona doesn't fish for a missing block.
    """
    review_comments = other_comments.get("review_comments") or []
    issue_comments = other_comments.get("issue_comments") or []
    reviews = other_comments.get("pull_request_reviews") or []
    out: list[str] = [
        "Existing PR comments by other actors (humans / Copilot / other bots):",
        f"  review_comments: {len(review_comments)}, "
        f"issue_comments: {len(issue_comments)}, "
        f"pull_request_reviews: {len(reviews)}",
        "---",
    ]
    if review_comments:
        out.append("### review_comments (inline, anchored to file:line)")
        for c in review_comments:
            out.append(_format_review_comment_line(c))
        out.append("")
    if issue_comments:
        out.append("### issue_comments (PR-body level, no file:line)")
        for c in issue_comments:
            out.append(_format_issue_comment_line(c))
        out.append("")
    if reviews:
        out.append("### pull_request_reviews (top-level review bodies)")
        for r in reviews:
            out.append(_format_review_top_line(r))
        out.append("")
    out.append("---")
    out.append("")
    return out


_OTHER_BODY_CAP = 240


def _format_review_comment_line(c: dict[str, Any]) -> str:
    user = c.get("user")
    login = (
        user.get("login") if isinstance(user, dict) and isinstance(user.get("login"), str) else "?"
    )
    path = c.get("path")
    line = c.get("line") or c.get("original_line")
    raw = c.get("body") if isinstance(c.get("body"), str) else ""
    body = str(raw).replace("\n", " ⏎ ")
    if len(body) > _OTHER_BODY_CAP:
        body = body[: _OTHER_BODY_CAP] + "..."
    url = c.get("html_url") or ""
    return f"- @{login} on {path}:{line} — {body}  <{url}>"


def _format_issue_comment_line(c: dict[str, Any]) -> str:
    user = c.get("user")
    login = (
        user.get("login") if isinstance(user, dict) and isinstance(user.get("login"), str) else "?"
    )
    raw = c.get("body") if isinstance(c.get("body"), str) else ""
    body = str(raw).replace("\n", " ⏎ ")
    if len(body) > _OTHER_BODY_CAP:
        body = body[: _OTHER_BODY_CAP] + "..."
    url = c.get("html_url") or ""
    return f"- @{login} — {body}  <{url}>"


def _format_review_top_line(r: dict[str, Any]) -> str:
    user = r.get("user")
    login = (
        user.get("login") if isinstance(user, dict) and isinstance(user.get("login"), str) else "?"
    )
    state = r.get("state") or "?"
    raw = r.get("body") if isinstance(r.get("body"), str) else ""
    body = str(raw).replace("\n", " ⏎ ")
    if len(body) > _OTHER_BODY_CAP:
        body = body[: _OTHER_BODY_CAP] + "..."
    url = r.get("html_url") or ""
    return f"- @{login} ({state}) — {body}  <{url}>"


__all__ = ["inline_to_api", "render_user_message"]
