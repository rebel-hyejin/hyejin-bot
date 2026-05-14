"""Jira wiki-markup helpers for the triage comment body.

The bot posts comments via REST v2 (`POST /rest/api/2/issue/{key}/comment`)
which accepts a plain-string `body` in Jira wiki markup. This module
assembles a structured, panel-based layout from a `TriageDraft`.

Panel layout (Atlassian Cloud renders these as titled card blocks):
  📍 Symptom
  📎 Evidence cited
  🎯 Likely layer: <domain>     ← color-coded per layer
  🔬 Next data to collect
  (optional) Suspected duplicates
  (optional) needs_human callout

Pure functions, stdlib only.
"""

from __future__ import annotations

from daeyeon_bot.core.jira_triage.types import (
    EvidenceItem,
    SuspectedDuplicate,
    TriageDraft,
)

# Per-domain panel color. The (border, bg) pairs are chosen to render
# readably on both light and dark Jira themes — the bg is a desaturated
# tint, the border is the strong accent.
_DOMAIN_COLORS: dict[str, tuple[str, str]] = {
    "Driver": ("#3b82f6", "#dbeafe"),  # blue
    "SysFw": ("#f97316", "#fed7aa"),  # orange
    "CpFw": ("#ef4444", "#fecaca"),  # red
    "SysSol": ("#a855f7", "#e9d5ff"),  # purple
    "DevOps": ("#10b981", "#d1fae5"),  # green
    "Connectivity": ("#14b8a6", "#ccfbf1"),  # teal
    "unknown": ("#6b7280", "#e5e7eb"),  # gray
}

_NEUTRAL_BORDER = "#cbd5e1"
_NEUTRAL_BG = "#f8fafc"

# Per-severity badge accent (for the trailing meta table).
_SEVERITY_LABELS: dict[str, str] = {
    "sev1": "🔴 sev1",
    "sev2": "🟠 sev2",
    "sev3": "🟡 sev3",
    "unknown": "⚪ unknown",
}


# ── Primitive helpers ────────────────────────────────────────────────────────


def h3(title: str) -> str:
    """`h3.` heading. Jira wiki: leading `h3. ` on its own line."""
    return f"h3. {title}"


def bullet(text: str) -> str:
    """Single bullet line. Jira wiki: leading `* `."""
    return f"* {text}"


def code(text: str) -> str:
    """Inline `{{...}}` code span. Escapes `}}` if it sneaks in."""
    safe = text.replace("}}", "} }")
    return "{{" + safe + "}}"


def noformat(text: str) -> str:
    """`{noformat}…{noformat}` block. For long quoted log lines."""
    return "{noformat}\n" + text + "\n{noformat}"


def quote(text: str) -> str:
    """`{quote}…{quote}` block. Renders as a callout."""
    return "{quote}" + text + "{quote}"


def bold(text: str) -> str:
    """`*bold*` inline."""
    return f"*{text}*"


def panel(
    *,
    title: str,
    body: str,
    border_color: str = _NEUTRAL_BORDER,
    bg_color: str = _NEUTRAL_BG,
) -> str:
    """Render a Jira `{panel}` block with a titled, colored frame."""
    return (
        f"{{panel:title={title}|borderStyle=solid|borderColor={border_color}"
        f"|titleBGColor={border_color}|bgColor={bg_color}}}\n"
        f"{body}\n"
        "{panel}"
    )


# ── Top-level assembly ──────────────────────────────────────────────────────


def build_comment(triage: TriageDraft, *, supersede_header: str | None = None) -> str:
    """Assemble the structured panel-based comment body.

    Sections, in order:
      1. (optional) supersede header in `{quote}…{quote}`
      2. 📍 Symptom panel
      3. 📎 Evidence cited panel (bullet list)
      4. 🎯 Likely layer: <domain> panel (color-coded per domain)
      5. 🔬 Next data to collect panel (bullet list)
      6. (optional) Suspected duplicates panel
      7. (optional) needs_human callout
      8. Metadata footer table (severity / domain / persona origin)
    """
    parts: list[str] = []

    if supersede_header:
        parts.append(quote(supersede_header))
        parts.append("")

    # 1) Symptom
    parts.append(
        panel(
            title="📍 Symptom",
            body=triage.symptom.rstrip(),
        )
    )
    parts.append("")

    # 2) Evidence
    parts.append(
        panel(
            title="📎 Evidence cited",
            body=_render_evidence_list(triage.evidence),
        )
    )
    parts.append("")

    # 3) Likely layer — color per domain
    border, bg = _DOMAIN_COLORS.get(triage.domain, (_NEUTRAL_BORDER, _NEUTRAL_BG))
    parts.append(
        panel(
            title=f"🎯 Likely layer: {triage.domain}",
            body=triage.layer_rationale.rstrip(),
            border_color=border,
            bg_color=bg,
        )
    )
    parts.append("")

    # 4) Next data
    parts.append(
        panel(
            title="🔬 Next data to collect",
            body=_render_next_data(triage.next_data),
        )
    )
    parts.append("")

    # 5) Suspected duplicates (optional)
    if triage.suspected_duplicates:
        parts.append(
            panel(
                title="🔁 Suspected duplicates (best-effort, NOT verified)",
                body=_render_duplicates(triage.suspected_duplicates),
            )
        )
        parts.append("")

    # 6) needs_human callout
    if triage.needs_human:
        parts.append(quote("⚠️ needs_human=true — operator review required."))
        parts.append("")

    # 7) Metadata footer — Jira renders `||header||header||` as a table.
    parts.append("----")
    parts.append("||severity||domain||needs_human||")
    parts.append(
        f"|{_SEVERITY_LABELS.get(triage.severity, triage.severity)}"
        f"|{triage.domain}"
        f"|{'true' if triage.needs_human else 'false'}|"
    )

    return "\n".join(parts) + "\n"


def supersede_header_text(prior_posted_at_hhmmss_utc: str) -> str:
    """Standard supersede-header text used as the leading `{quote}` block."""
    return (
        f"Updated triage (supersedes earlier bot comment posted at {prior_posted_at_hhmmss_utc})."
    )


# ── Section renderers ───────────────────────────────────────────────────────


def _render_evidence_list(evidence: tuple[EvidenceItem, ...]) -> str:
    if not evidence:
        return bullet("_(no evidence cited — see needs_human flag)_")
    lines: list[str] = []
    for item in evidence:
        lines.append(evidence_bullet(item))
    return "\n".join(lines)


def _render_next_data(items: tuple[str, ...]) -> str:
    if not items:
        return bullet("_(no follow-up actions suggested)_")
    return "\n".join(bullet(item) for item in items)


def _render_duplicates(dups: tuple[SuspectedDuplicate, ...]) -> str:
    return "\n".join(duplicate_bullet(d) for d in dups)


def evidence_bullet(item: EvidenceItem) -> str:
    """Format: `* *<source>* @ <citation> — {{<quote>}}`.

    Long quotes (>200 chars) or multi-line quotes use `{noformat}` to
    avoid escape headaches.
    """
    head = f"{bold(item.source)} @ {item.citation}"
    if len(item.quote) > 200 or "\n" in item.quote:
        return f"{bullet(head + ' —')}\n{noformat(item.quote)}"
    return bullet(f"{head} — {code(item.quote)}")


def duplicate_bullet(dup: SuspectedDuplicate) -> str:
    return bullet(f"{bold(dup.key)} — {dup.basis}")


__all__ = [
    "bold",
    "build_comment",
    "bullet",
    "code",
    "duplicate_bullet",
    "evidence_bullet",
    "h3",
    "noformat",
    "panel",
    "quote",
    "supersede_header_text",
]
