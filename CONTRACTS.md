# daeyeon-bot — Core Contracts

> Authoritative reference for the **stable interfaces** between trigger, dispatcher, handler, and storage. Extracted from `docs/PLAN.md` §3. Implementation may evolve; **these contracts may not break without an explicit migration plan**.

## 1. Delivery semantics

- **At-least-once.** Every handler MUST tolerate being invoked more than once for the same `Event`.
- A handler declares idempotency at compile time via `HandlerManifest.idempotent` and a `dedup_ttl`. The dispatcher honors the manifest by writing `dedup_keys` keyed on `(event_id, handler, attempt_epoch)` after a successful `Ack`.
- A handler with `idempotent = False` that ends in `interrupted` is **NOT** retried automatically. It moves to `dead_letter` and is only resumed via `daeyeon-bot ops replay`.
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
- Operator-controlled kill switch: presence of `~/.daeyeon-bot/PAUSE` blocks all Claude calls **before** rate-limiter check.
- `daeyeon-bot lifecycle pause` / `resume` is the only sanctioned way to toggle PAUSE.

## 6. Replay

- `daeyeon-bot ops replay <event_id> [--handler X] [--confirm]`
- Dry-run by default; `--confirm` is required to actually re-emit.
- Increments `events.attempt_epoch`; the dedup key now changes (`event_id:handler:attempt_epoch`) so the previous Ack does not block the new one.
- Side-effect dedup is the handler's responsibility via `side_effect_key`.

## 7. Schema versioning

- DDL migrations live in `infra/db/migrations/NNN_*.sql`. Linear, additive, never rewritten in place.
- `meta.schema_version` is the only source of truth. `daeyeon-bot ops migrate` applies pending migrations under a transaction.
- Event payloads: `Event.schema_version` lets handlers reject old payloads or run lazy migrators in `core/events.py:migrate(event)`.

---

*Last updated: 2026-05-03 (Phase 0 scaffolding).*
