"""Claude Agent SDK adapter.

Phase 0: stub. Phase 1 introduces a fake `ClaudeSession` for tests; Phase 4
wires the real SDK with explicit env allowlist for the subprocess.
"""

from __future__ import annotations


async def open_session() -> None:
    raise NotImplementedError(
        "Phase 1 (fake) / Phase 4 (real): AsyncContextManager wrapping claude_agent_sdk"
    )
