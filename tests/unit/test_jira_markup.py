"""Jira wiki-markup helpers — T020 tests (panel-based layout)."""

from __future__ import annotations

from daeyeon_bot.core.jira_triage.types import (
    EvidenceItem,
    SuspectedDuplicate,
    TriageDraft,
)
from daeyeon_bot.infra.jira_markup import (
    bold,
    build_comment,
    bullet,
    code,
    duplicate_bullet,
    evidence_bullet,
    h3,
    noformat,
    panel,
    quote,
    supersede_header_text,
)


def test_h3_emits_prefix() -> None:
    assert h3("Symptom") == "h3. Symptom"


def test_bullet_emits_prefix() -> None:
    assert bullet("a thing") == "* a thing"


def test_code_wraps_double_brace() -> None:
    assert code("FW HALT") == "{{FW HALT}}"


def test_code_escapes_closing_brace() -> None:
    """`}}` inside the body would close the span early; we space-break it."""
    out = code("see }} that")
    # `{{see } } that}}` — only the outer `{{` / `}}` should remain.
    assert out == "{{see } } that}}"


def test_noformat_wraps_with_newlines() -> None:
    out = noformat("line1\nline2")
    assert out.startswith("{noformat}\n")
    assert out.endswith("\n{noformat}")


def test_quote_inline_wrap() -> None:
    assert quote("hi") == "{quote}hi{quote}"


def test_bold_wraps_with_stars() -> None:
    assert bold("x") == "*x*"


def test_panel_renders_with_colors() -> None:
    out = panel(
        title="📍 Symptom",
        body="something failed",
        border_color="#3b82f6",
        bg_color="#dbeafe",
    )
    assert out.startswith("{panel:title=📍 Symptom|borderStyle=solid|borderColor=#3b82f6")
    assert "titleBGColor=#3b82f6" in out
    assert "bgColor=#dbeafe" in out
    assert "something failed" in out
    assert out.endswith("{panel}")


# ── build_comment integration ────────────────────────────────────────────────


def _draft(
    *,
    symptom: str = "rblnWaitJob TIMEDOUT 후 KMD TDR; root는 FW.",
    domain: str = "CpFw",
    layer_rationale: str = "err_code=0x10007이 0x1xxxx 범위로 CpFw page fault.",
    next_data: tuple[str, ...] = ("FW abort dump 캡처", "rblntrace로 재현"),
    duplicates: tuple[SuspectedDuplicate, ...] = (),
    needs_human: bool = False,
    evidence: tuple[EvidenceItem, ...] = (
        EvidenceItem(
            source="loki.kernel",
            quote="rbln_drv: TDR detected",
            citation="2026-05-13T06:55:12Z",
        ),
    ),
) -> TriageDraft:
    return TriageDraft(
        symptom=symptom,
        evidence=evidence,
        domain=domain,  # type: ignore[arg-type]
        layer_rationale=layer_rationale,
        next_data=next_data,
        severity="sev2",
        suspected_duplicates=duplicates,
        needs_human=needs_human,
    )


def test_build_comment_includes_all_required_panels() -> None:
    out = build_comment(_draft())
    assert "📍 Symptom" in out
    assert "📎 Evidence cited" in out
    assert "🎯 Likely layer: CpFw" in out
    assert "🔬 Next data to collect" in out
    # No supersede header, no duplicates panel, no needs_human callout.
    assert "Suspected duplicates" not in out
    assert "needs_human=true" not in out


def test_build_comment_uses_domain_color() -> None:
    out = build_comment(_draft(domain="Driver"))
    # Driver = blue (#3b82f6)
    assert "borderColor=#3b82f6" in out
    out2 = build_comment(_draft(domain="SysFw"))
    # SysFw = orange (#f97316)
    assert "borderColor=#f97316" in out2


def test_build_comment_unknown_domain_uses_neutral_color() -> None:
    out = build_comment(_draft(domain="unknown"))
    assert "borderColor=#6b7280" in out  # gray


def test_build_comment_with_supersede_header() -> None:
    out = build_comment(_draft(), supersede_header=supersede_header_text("14:30:11 UTC"))
    assert out.startswith(
        "{quote}Updated triage (supersedes earlier bot comment posted at 14:30:11 UTC).{quote}"
    )
    assert "📍 Symptom" in out


def test_build_comment_with_duplicates() -> None:
    dups = (
        SuspectedDuplicate(key="SSWCI-1234", basis="same TC + same err_code"),
        SuspectedDuplicate(key="SSWCI-5678", basis="adjacent host history"),
    )
    out = build_comment(_draft(duplicates=dups))
    assert "🔁 Suspected duplicates (best-effort, NOT verified)" in out
    assert "*SSWCI-1234*" in out
    assert "*SSWCI-5678*" in out


def test_build_comment_needs_human_quote_appended() -> None:
    out = build_comment(_draft(needs_human=True))
    assert "{quote}⚠️ needs_human=true" in out


def test_build_comment_includes_metadata_footer() -> None:
    out = build_comment(_draft())
    assert "||severity||domain||needs_human||" in out
    assert "|🟠 sev2|CpFw|false|" in out


def test_build_comment_ends_with_newline() -> None:
    out = build_comment(_draft())
    assert out.endswith("\n")


def test_build_comment_korean_prose_passes_through() -> None:
    """Korean prose in structured fields is preserved verbatim (SC-012)."""
    out = build_comment(_draft(symptom="rblnWaitJob TIMEDOUT 후 다음 잡 제출 실패."))
    assert "rblnWaitJob TIMEDOUT 후" in out


def test_build_comment_renders_next_data_as_bullets() -> None:
    out = build_comment(_draft(next_data=("step one", "step two", "step three")))
    assert "* step one" in out
    assert "* step two" in out
    assert "* step three" in out


def test_build_comment_empty_evidence_shows_placeholder() -> None:
    out = build_comment(_draft(evidence=(), domain="unknown"))
    assert "_(no evidence cited" in out


def test_build_comment_empty_next_data_shows_placeholder() -> None:
    out = build_comment(_draft(next_data=()))
    assert "_(no follow-up actions suggested)_" in out


# ── evidence_bullet / duplicate_bullet helpers ───────────────────────────────


def test_evidence_bullet_short_quote_uses_inline_code_and_bold_source() -> None:
    item = EvidenceItem(source="ssh.dmesg", quote="atom_halt status: 6", citation="ssh.dmesg:1247")
    out = evidence_bullet(item)
    assert out == "* *ssh.dmesg* @ ssh.dmesg:1247 — {{atom_halt status: 6}}"


def test_evidence_bullet_long_quote_uses_noformat() -> None:
    long_quote = "x" * 250
    item = EvidenceItem(source="ssh.dmesg", quote=long_quote, citation="ssh.dmesg:99")
    out = evidence_bullet(item)
    assert "{noformat}" in out


def test_evidence_bullet_multiline_quote_uses_noformat() -> None:
    item = EvidenceItem(source="ssh.dmesg", quote="line1\nline2", citation="ssh.dmesg:42")
    out = evidence_bullet(item)
    assert "{noformat}" in out
    assert "line1" in out


def test_duplicate_bullet_renders_bold_key() -> None:
    dup = SuspectedDuplicate(key="SSWCI-99", basis="same TC")
    assert duplicate_bullet(dup) == "* *SSWCI-99* — same TC"
