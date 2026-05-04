"""Async wrapper around the operator's local `gh` CLI.

All GitHub access flows through this module. Auth is delegated to `gh` —
the daemon stores no GitHub token of its own. The 5 endpoints exposed here
are the entire GitHub surface (`contracts/github-api-surface.md`).

Error mapping (per `contracts/github-api-surface.md` §"Auth & rate-limit"):
    HTTP 401 / auth failure  → AuthError       (daemon halts, exit 78)
    HTTP 403 + rate headers  → RateLimitError  (Retry with rate-limit backoff)
    HTTP 422 on POST         → PermanentError  (DeadLetter; local validator bug)
    HTTP 404 on GET          → PermanentError  (PR not found / no access)
    Other 5xx / timeout      → TransientError  (Retry default backoff)
    HTTP 200 on probe         → success (parsed JSON returned)

No retries inside the wrapper; the dispatcher handles them.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from daeyeon_bot.core.errors import (
    AuthError,
    PermanentError,
    RateLimitError,
    TransientError,
)

_DEFAULT_TIMEOUT = 30.0
_REVIEW_EVENT: Literal["COMMENT"] = "COMMENT"

# stderr patterns. `gh` writes "HTTP <code>" or "gh: <msg> (HTTP <code>)" depending
# on the subcommand; cover both. Auth-failure phrasing varies across `gh` versions.
_HTTP_CODE_RE = re.compile(r"HTTP\s+(\d{3})")
_AUTH_PHRASES = (
    "authentication failed",
    "authentication required",
    "bad credentials",
    "could not refresh",
    "must authenticate",
    "no logged-in account",
    "token has not been granted",
)
_RATE_LIMIT_PHRASES = (
    "api rate limit exceeded",
    "x-ratelimit-remaining: 0",
)


@dataclass(frozen=True, slots=True)
class _GhResult:
    """Outcome of one `gh` subprocess invocation."""

    returncode: int
    stdout: bytes
    stderr: bytes


class GhCli:
    """Thin async wrapper around `gh api` for the 5 GitHub endpoints we use."""

    def __init__(self, *, timeout_seconds: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout_seconds

    # ── Public surface ────────────────────────────────────────────────────

    async def auth_status(self) -> None:
        """Probe `gh auth status`. Raises AuthError if not logged in."""
        result = await self._run("auth", "status")
        if result.returncode != 0:
            raise AuthError("gh auth status failed: " + _safe_decode(result.stderr).strip())

    async def auth_user(self) -> str:
        """Return the authenticated user's `login`. One call at boot."""
        payload = await self._api("GET", "/user")
        login = payload.get("login")
        if not isinstance(login, str) or not login:
            raise PermanentError("gh api /user returned no login")
        return login

    async def search_review_requested(self, username: str) -> list[dict[str, Any]]:
        """Search open PRs awaiting review by `username`.

        Returns the flattened `items` list from `GET /search/issues`.
        """
        if not username:
            raise PermanentError("github.username is empty; cannot build search query")
        query = f"is:open is:pr review-requested:{username} archived:false"
        payload = await self._api(
            "GET",
            "/search/issues",
            extra=("-f", f"q={query}", "-f", "per_page=100"),
            paginate=True,
        )
        if isinstance(payload, dict):
            items_raw = payload.get("items", [])
            if isinstance(items_raw, list):
                return [item for item in items_raw if isinstance(item, dict)]
        return []

    async def pr_get(self, repo: str, pr_number: int) -> dict[str, Any]:
        """Fetch one PR's metadata via `GET /repos/{repo}/pulls/{n}`."""
        payload = await self._api("GET", f"/repos/{repo}/pulls/{pr_number}")
        if not isinstance(payload, dict):
            raise PermanentError("gh pr_get returned non-object")
        return payload

    async def pr_files(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        """Fetch the changed-files list via `GET /repos/{repo}/pulls/{n}/files`."""
        payload = await self._api(
            "GET",
            f"/repos/{repo}/pulls/{pr_number}/files",
            extra=("-f", "per_page=100"),
            paginate=True,
        )
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    async def post_review(
        self,
        repo: str,
        pr_number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Post one review object. `event` is always "COMMENT" (FR-010a)."""
        request: dict[str, Any] = {
            "commit_id": commit_id,
            "event": _REVIEW_EVENT,
            "body": body,
            "comments": comments,
        }
        payload = await self._api(
            "POST",
            f"/repos/{repo}/pulls/{pr_number}/reviews",
            stdin_json=request,
        )
        if not isinstance(payload, dict):
            raise PermanentError("gh post review returned non-object")
        return payload

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _api(
        self,
        method: str,
        path: str,
        *,
        extra: tuple[str, ...] = (),
        paginate: bool = False,
        stdin_json: dict[str, Any] | None = None,
    ) -> Any:
        args: list[str] = ["api", "-X", method]
        if paginate:
            args.append("--paginate")
        if stdin_json is not None:
            args.extend(["--input", "-"])
        args.append(path)
        args.extend(extra)

        stdin_bytes = json.dumps(stdin_json).encode("utf-8") if stdin_json is not None else None
        result = await self._run(*args, stdin=stdin_bytes)
        if result.returncode != 0:
            self._raise_error(method, path, result)
        text = _safe_decode(result.stdout).strip()
        if not text:
            return {}
        if paginate and not text.startswith("["):
            # `gh api --paginate` concatenates JSON arrays as `[...]\n[...]`.
            # When the endpoint already returns an object with `items` (e.g.
            # /search/issues), gh emits multiple objects; merge them.
            return _merge_paginated_objects(text)
        if paginate and text.startswith("["):
            return _merge_paginated_arrays(text)
        return json.loads(text)

    async def _run(self, *args: str, stdin: bytes | None = None) -> _GhResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                *args,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise PermanentError(f"gh CLI not found on PATH: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=self._timeout)
        except TimeoutError as exc:
            with _suppress():
                proc.kill()
            raise TransientError(f"gh {args[0]} timed out after {self._timeout}s") from exc
        return _GhResult(returncode=proc.returncode or 0, stdout=stdout, stderr=stderr)

    def _raise_error(self, method: str, path: str, result: _GhResult) -> None:
        stderr = _safe_decode(result.stderr)
        lower = stderr.lower()

        if any(p in lower for p in _AUTH_PHRASES):
            raise AuthError(f"gh {method} {path}: auth failure: {stderr.strip()}")

        match = _HTTP_CODE_RE.search(stderr)
        code = int(match.group(1)) if match else None

        if code == 401:
            raise AuthError(f"gh {method} {path}: HTTP 401: {stderr.strip()}")
        if code == 403:
            if any(p in lower for p in _RATE_LIMIT_PHRASES):
                raise RateLimitError(f"gh {method} {path}: rate-limited: {stderr.strip()}")
            raise PermanentError(f"gh {method} {path}: HTTP 403: {stderr.strip()}")
        if code == 404 and method == "GET":
            raise PermanentError(f"gh {method} {path}: HTTP 404: PR not found or no access")
        if code == 422 and method == "POST":
            raise PermanentError(f"gh {method} {path}: HTTP 422: {stderr.strip()}")
        if code is not None and 500 <= code < 600:
            raise TransientError(f"gh {method} {path}: HTTP {code}: {stderr.strip()}")

        # No identifiable HTTP code — treat as transient if exit code is non-zero.
        raise TransientError(f"gh {method} {path}: exit {result.returncode}: {stderr.strip()}")


# ── Module helpers ────────────────────────────────────────────────────────


def _safe_decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace")


def _merge_paginated_arrays(text: str) -> list[Any]:
    """`gh --paginate` on array endpoints emits `[..]\\n[..]\\n...`."""
    out: list[Any] = []
    for chunk in _split_json_chunks(text):
        loaded: Any = json.loads(chunk)
        if isinstance(loaded, list):
            out.extend(loaded)  # type: ignore[arg-type]
        else:
            out.append(loaded)
    return out


def _merge_paginated_objects(text: str) -> dict[str, Any]:
    """`gh --paginate` on `/search/issues` emits one JSON object per page.

    Concatenate `items` lists; preserve `total_count` from the first page.
    """
    merged: dict[str, Any] = {}
    items: list[Any] = []
    for chunk in _split_json_chunks(text):
        loaded: Any = json.loads(chunk)
        if not isinstance(loaded, dict):
            continue
        if not merged:
            merged = {str(k): v for k, v in loaded.items() if k != "items"}  # type: ignore[misc]
        page_items: Any = loaded.get("items", [])
        if isinstance(page_items, list):
            items.extend(page_items)  # type: ignore[arg-type]
    merged["items"] = items
    return merged


def _split_json_chunks(text: str) -> list[str]:
    """Split a `gh --paginate` blob into individual JSON documents."""
    chunks: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "[{":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                chunks.append(text[start : i + 1])
    if not chunks and text.strip():
        chunks.append(text.strip())
    return chunks


class _suppress:
    """tiny contextlib.suppress(BaseException) without the import."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_a: object) -> bool:
        return True


__all__ = ["GhCli"]
