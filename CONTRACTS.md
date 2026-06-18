# hyejin-bot — Core Contracts

> Authoritative reference for the **stable interfaces** between trigger, dispatcher, handler, and storage. Extracted from `docs/PLAN.md` §3. Implementation may evolve; **these contracts may not break without an explicit migration plan**.

## 1. Delivery semantics

- **At-least-once.** Every handler MUST tolerate being invoked more than once for the same `Event`.
- A handler declares idempotency at compile time via `HandlerManifest.idempotent` and a `dedup_ttl`. The dispatcher honors the manifest by writing `dedup_keys` keyed on `(event_id, handler, attempt_epoch)` after a successful `Ack`.
- A handler with `idempotent = False` that ends in `interrupted` is **NOT** retried automatically. It moves to `dead_letter` and is only resumed via `hyejin-bot ops replay`.
- A handler with a non-empty `side_effect_key` is responsible for using that key to dedupe with the **external** system (Slack `client_msg_id`, GitHub idempotency keys, etc.).

## 2. HandlerResult (sum type)

```
HandlerResult = Ack
              | Retry(after_s: float)
              | DeadLetter(reason: str)
```

| Variant | Outbox status | Side effects |
|---|---|---|
| `Ack` | `acked` | `dedup_keys` row inserted with TTL |
| `Retry(after_s)` | `retry`, `next_attempt_at = now + after_s` | none |
| `DeadLetter(reason)` | `dead_letter` | none — **operator action required** |

Exceptions raised by the handler are translated by the dispatcher:

| Raised | Mapped to |
|---|---|
| `TransientError` | `Retry(default_backoff)` |
| `RateLimitError` | `Retry(rate_limit_backoff)` |
| `AuthError` | dispatcher halt (exit 78), no settle |
| `PermanentError` or unclassified `Exception` | `DeadLetter(repr(exc))` |

## 3. Manifest

```python
@dataclass(frozen=True, slots=True)
class HandlerManifest:
    name: str                       # unique, kebab-case
    idempotent: bool
    dedup_ttl: timedelta
    side_effect_key: str | None
    concurrency: int                # asyncio.Semaphore upper bound
    accepts: list[str]              # event.type filter

@dataclass(frozen=True, slots=True)
class TriggerManifest:
    name: str
    source: str                     # written into outbox.source
    retryable_at_source: bool
```

Every `triggers/<name>.py` and `handlers/<name>.py` MUST expose a module-level `MANIFEST: TriggerManifest | HandlerManifest`. The container reads it; no decoration magic.

## 4. Errors

```
core.errors.BotError
├── TransientError
│   └── RateLimitError
├── PermanentError
│   ├── ValidationError
│   └── ConfigError
├── AuthError              # daemon-wide impact
└── QuotaError             # local rate limiter rejection
```

Unclassified exceptions are treated as `PermanentError`.

## 5. Quota / rate limit / kill switch

- **Atomic** SQL UPDATE on `ratelimit_buckets`: `UPDATE … SET tokens = tokens - 1 WHERE name=? AND tokens >= 1`. No read-modify-write in app code.
- Refill in the same UPDATE: `MAX(capacity, tokens + (now - last_refill) * rate)`.
- Operator-controlled kill switch: presence of `~/.hyejin-bot/PAUSE` blocks all Claude calls **before** rate-limiter check.
- `hyejin-bot lifecycle pause` / `resume` is the only sanctioned way to toggle PAUSE.

## 6. Replay

- `hyejin-bot ops replay <event_id> [--handler X] [--confirm]`
- Dry-run by default; `--confirm` is required to actually re-emit.
- Increments `events.attempt_epoch`; the dedup key now changes (`event_id:handler:attempt_epoch`) so the previous Ack does not block the new one.
- Side-effect dedup is the handler's responsibility via `side_effect_key`.

## 7. Schema versioning

- DDL migrations live in `infra/db/migrations/NNN_*.sql`. Linear, additive, never rewritten in place.
- `meta.schema_version` is the only source of truth. `hyejin-bot ops migrate` applies pending migrations under a transaction.
- Event payloads: `Event.schema_version` lets handlers reject old payloads or run lazy migrators in `core/events.py:migrate(event)`.

## 8. Security boundaries

The bot is a single-tenant daemon running as the operator on a trusted host. The threat model assumes operator-controlled secrets at rest (Keychain / 0600 file) and operator-owned credentials in flight (GitHub `gh` CLI auth, Jira API token, SSH credentials). Explicit boundaries:

- **OAuth token never lands in `os.environ`** after startup unless `--insecure-env` is set; provider order is Keychain → 0600 file → env. See `infra/secrets.py`.
- **Log sink redaction** scrubs Slack, AWS, JWT, Anthropic OAuth, GitHub PAT, and high-entropy strings (≥4.5 bits/char on ≥24-char strings) via the structlog processor in `infra/logging.py`. Posted PR comment bodies and Jira comment bodies get a separate, stricter pass in the respective handlers (`_enforce_redaction`).
- **GitHub access** flows through the operator's local `gh` CLI subprocess — no PAT in config files; auth is delegated.
- **Jira access** uses `JIRA_USER` + `JIRA_API_TOKEN` (httpx basic auth). Both via the secrets provider.
- **SSH access** to `automation@<ssw-lab-host>` for the regression log dump uses `known_hosts=None` (host-key verification disabled). The lab network is treated as trusted: hosts are re-imaged regularly so fingerprints churn, and the bot's SFTP path is read-only. Fork-PR runners do NOT have access; if that boundary ever changes, re-enable verification with a custom asyncssh callback that pins the lab CA. Decision documented in `infra/ssh_logs.py:_fetch`.

---

*Last updated: 2026-05-16 (Phases 0–8 implemented; contracts stable, §8 added for jira_triage SSH boundary).*
