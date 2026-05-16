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

from daeyeon_bot.handlers.pr_review_schemas import InlineComment


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
) -> str:
    """Render the snapshot the way `contracts/claude-review-output.md` §2 specs.

    When `prior_reviews` is non-empty, a `Prior reviews (most recent first)`
    section is inserted before `Changed files` so the persona can produce
    Resolved / Still open / New buckets per the SKILL `Re-review` mode.
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
        f"Prior reviews ({len(prior_reviews)} most recent, by daeyeon-bot):",
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


__all__ = ["inline_to_api", "render_user_message"]
