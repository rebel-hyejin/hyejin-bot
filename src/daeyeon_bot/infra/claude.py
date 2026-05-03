"""Claude Agent SDK adapter.

Two implementations of the same `ClaudeSession` shape:
    * `FakeClaudeSession` — scripted responses for tests.
    * `RealClaudeSession` — wraps `claude_agent_sdk.ClaudeSDKClient`. The
      OAuth token is passed to the CLI subprocess via an explicit env
      allowlist (not by inheriting the daemon's environment).

Errors map onto `core.errors`:
    * `CLINotFoundError` / `CLIConnectionError` → `TransientError` (retry).
    * `RateLimitEvent` from the SDK stream → `RateLimitError`.
    * `ProcessError` whose stderr looks auth-related → `AuthError`.
    * Anything else → propagates to the dispatcher's generic catch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import NoReturn, Protocol, runtime_checkable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    RateLimitEvent,
    TextBlock,
)

from daeyeon_bot.core.errors import AuthError, RateLimitError, TransientError

_AUTH_HINTS: tuple[str, ...] = (
    "401",
    "403",
    "unauthorized",
    "invalid_api_key",
    "authentication",
    "oauth",
    "token expired",
    "token revoked",
)


@runtime_checkable
class ClaudeSession(Protocol):
    """The minimal surface a handler uses to talk to Claude."""

    async def __aenter__(self) -> ClaudeSession: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def query(self, prompt: str, *, system: str | None = None) -> str: ...


@dataclass(slots=True)
class FakeClaudeSession:
    """Test double. Returns scripted responses; records calls for assertions.

    Default: echoes the prompt prefixed with `[fake] `. Pass `responses=[...]` to
    play back a sequence; `default` is used after the script is exhausted.
    """

    responses: list[str] = field(default_factory=list[str])
    default: str | None = None
    calls: list[dict[str, str | None]] = field(default_factory=list[dict[str, str | None]])
    closed: bool = False

    async def __aenter__(self) -> FakeClaudeSession:
        self.closed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.closed = True

    async def query(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        if self.responses:
            return self.responses.pop(0)
        if self.default is not None:
            return self.default
        return f"[fake] {prompt}"


class ClaudeSessionFactory(Protocol):
    """Builds a fresh session per handler invocation."""

    def __call__(self) -> ClaudeSession: ...


@dataclass(slots=True)
class FakeFactory:
    session: FakeClaudeSession

    def __call__(self) -> FakeClaudeSession:
        return self.session


@dataclass(slots=True)
class RealClaudeSession:
    """`ClaudeSDKClient` wrapper that fits the `ClaudeSession` protocol.

    The session takes ownership of an SDK client per handler invocation:
    `__aenter__` connects, `query` round-trips a prompt + collects assistant
    text, `__aexit__` disconnects. Errors are translated to `core.errors`
    so the dispatcher can route them to retry / dead-letter / halt.
    """

    oauth_token: str
    model: str | None
    default_system_prompt: str | None
    _client: ClaudeSDKClient | None = field(default=None, init=False)

    async def __aenter__(self) -> RealClaudeSession:
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=self.default_system_prompt,
            env={"CLAUDE_CODE_OAUTH_TOKEN": self.oauth_token},
        )
        client = ClaudeSDKClient(options=options)
        try:
            await client.connect()
        except CLINotFoundError as exc:
            raise TransientError(f"claude CLI not found: {exc}") from exc
        except CLIConnectionError as exc:
            raise TransientError(f"claude CLI connect failed: {exc}") from exc
        self._client = client
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except CLIConnectionError:  # pragma: no cover — best-effort
            return

    async def query(self, prompt: str, *, system: str | None = None) -> str:
        client = self._require_client()
        if system is not None and system != self.default_system_prompt:
            # The current SDK ties system prompts to ClaudeAgentOptions at
            # connect time. Rather than silently ignore an override, surface
            # a transient error so the caller knows to set it up-front.
            raise TransientError(
                "RealClaudeSession does not support per-query system prompts;"
                " configure config.claude.default_system_prompt instead"
            )
        try:
            await client.query(prompt)
            return await _collect_assistant_text(client)
        except ProcessError as exc:
            _raise_process_error(exc)
        except CLIConnectionError as exc:
            raise TransientError(f"claude CLI connection lost: {exc}") from exc

    def _require_client(self) -> ClaudeSDKClient:
        if self._client is None:
            raise TransientError("RealClaudeSession used outside of `async with`")
        return self._client


async def _collect_assistant_text(client: ClaudeSDKClient) -> str:
    """Drain the response stream and concatenate assistant text blocks."""
    parts: list[str] = []
    async for message in client.receive_response():
        if isinstance(message, RateLimitEvent):
            raise RateLimitError(f"claude rate limit: {message}")
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "".join(parts)


def _raise_process_error(exc: ProcessError) -> NoReturn:
    detail = str(exc).lower()
    if any(hint in detail for hint in _AUTH_HINTS):
        raise AuthError(f"claude auth failure: {exc}") from exc
    raise TransientError(f"claude process error: {exc}") from exc


def make_real_factory(
    *, oauth_token: str, model: str | None, default_system_prompt: str | None
) -> Callable[[], RealClaudeSession]:
    """Closure that builds a fresh `RealClaudeSession` per dispatch."""

    def _factory() -> RealClaudeSession:
        return RealClaudeSession(
            oauth_token=oauth_token,
            model=model,
            default_system_prompt=default_system_prompt,
        )

    return _factory


__all__ = [
    "ClaudeSession",
    "ClaudeSessionFactory",
    "FakeClaudeSession",
    "FakeFactory",
    "RealClaudeSession",
    "make_real_factory",
]
