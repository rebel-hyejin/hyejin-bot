"""Slack Web API adapter for one-off DM/channel notifications.

Used by the pr_review handler to push LGTM-eligible PRs to the operator's
DM channel (`D08GP012483`) so the operator can manually verify + click
APPROVE on GitHub. The bot itself never APPROVEs — the persona's
verdict=APPROVE only emits a COMMENT review on GitHub and a one-line
nudge over here.

Design points:
* Fire-and-forget: a Slack failure must never block (or fail) a PR
  review post. The handler wraps calls in best-effort catch and logs.
* No background queue: the daemon already has the outbox + dispatcher
  retries for the GitHub side; Slack is treated as a side-effect
  ack only. If hyejin needs durable Slack delivery later, the right
  fix is to move LGTM-eligible into its own outbox handler — for now,
  inline send keeps the change small.
* Token / channel come from the secrets provider (Vault path
  `secret/bots/hyejin-bot` already carries `SLACK_BOT_TOKEN` and
  `SLACK_CHANNEL`); the daemon never holds either in plaintext on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from hyejin_bot.core.errors import AuthError, TransientError

_log = structlog.get_logger(__name__)

_SLACK_API = "https://slack.com/api"


@runtime_checkable
class SlackClient(Protocol):
    """Minimal Slack surface the handler uses."""

    async def post_message(self, *, channel: str, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class HttpSlackClient:
    """Real Slack client over `chat.postMessage`.

    The bot token authorizes a single workspace. `channel` accepts an
    operator DM ID (`D...`), a public channel ID (`C...`), or a name
    (`#channel-name`). For hyejin-bot we target the operator's DM.
    """

    bot_token: str
    timeout_s: float = 5.0
    http_client: httpx.AsyncClient | None = field(default=None)

    async def post_message(self, *, channel: str, text: str) -> None:
        client = self.http_client or httpx.AsyncClient(timeout=self.timeout_s)
        owns_client = self.http_client is None
        try:
            try:
                resp = await client.post(
                    f"{_SLACK_API}/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {self.bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json={"channel": channel, "text": text},
                )
            except httpx.HTTPError as exc:
                raise TransientError(f"slack: chat.postMessage transport ({exc})") from exc
            payload: dict[str, Any]
            try:
                payload = resp.json()
            except ValueError as exc:
                raise TransientError(
                    f"slack: chat.postMessage non-json response http={resp.status_code}"
                ) from exc
            if resp.status_code != 200 or not payload.get("ok", False):
                err = str(payload.get("error", f"http {resp.status_code}"))
                # `invalid_auth` / `token_revoked` / `account_inactive` are
                # the non-retryable shape; everything else (rate_limited,
                # internal_error, network blips) goes through as transient.
                if err in {"invalid_auth", "token_revoked", "account_inactive", "not_authed"}:
                    raise AuthError(f"slack: {err}")
                raise TransientError(f"slack: chat.postMessage error: {err}")
        finally:
            if owns_client:
                await client.aclose()


@dataclass(slots=True)
class FakeSlackClient:
    """Test double — records calls; raises if configured to."""

    raise_on_post: BaseException | None = None
    calls: list[dict[str, str]] = field(default_factory=list[dict[str, str]])

    async def post_message(self, *, channel: str, text: str) -> None:
        self.calls.append({"channel": channel, "text": text})
        if self.raise_on_post is not None:
            raise self.raise_on_post


__all__ = ["FakeSlackClient", "HttpSlackClient", "SlackClient"]
