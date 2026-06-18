"""Pydantic v2 schema for the JSON Claude must produce.

Structured layout (no free-form prose) — the persona fills typed fields
and the handler assembles a Jira-native, panel-based comment from them.
This keeps every triage comment visually consistent and lets the
operator tune layout without re-prompting Claude.

`extra="forbid"` is the load-bearing knob — a hallucinated key like
`"approve": true` must NOT slip past validation. FR-017 is enforced
structurally via `@model_validator`: a `domain != "unknown"` conclusion
without any evidence is rejected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Domain = Literal["Driver", "SysFw", "CpFw", "SysSol", "DevOps", "Connectivity", "unknown"]
Severity = Literal["sev1", "sev2", "sev3", "unknown"]

EvidenceSource = Literal[
    "loki.fwlog",
    "loki.smclog",
    "loki.kernel",
    "loki.syslog",
    "ssh.output_xml",
    "ssh.dmesg",
    "ssh.console",
    "test_code",
    "product_code",
    "ticket.error_log",
]


class EvidenceItem(BaseModel):
    """One citation — pinpoints a quoted line in a specific source."""

    model_config = {"extra": "forbid"}

    source: EvidenceSource
    quote: str = Field(min_length=1, max_length=2000)
    citation: str = Field(min_length=1, max_length=512)


class SuspectedDuplicate(BaseModel):
    """One best-effort suspected duplicate — verified by the operator, NOT the bot."""

    model_config = {"extra": "forbid"}

    key: str = Field(pattern=r"^[A-Z]+-\d+$")
    basis: str = Field(min_length=1, max_length=512)


class TriageOutput(BaseModel):
    """Structured Claude output — handler assembles the actual comment body."""

    model_config = {"extra": "forbid"}

    # One-sentence summary of what failed. Korean prose, English technical
    # terms preserved (e.g. "rblnWaitJob TIMEDOUT" stays English).
    symptom: str = Field(min_length=1, max_length=500)

    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=50)

    domain: Domain
    # One-sentence justification for the chosen domain. Cites the
    # strongest evidence lines that point at this layer.
    layer_rationale: str = Field(min_length=1, max_length=500)

    # Bullet list of concrete next-step suggestions (commands, files to
    # collect, hosts to re-run on). Each item is one short imperative.
    next_data: list[str] = Field(default_factory=list, max_length=10)

    severity: Severity

    suspected_duplicates: list[SuspectedDuplicate] = Field(default_factory=list, max_length=5)
    needs_human: bool

    @model_validator(mode="after")
    def evidence_required_when_concluded(self) -> TriageOutput:
        """FR-017: never diagnose without cited evidence."""
        if self.domain != "unknown" and not self.evidence:
            raise ValueError(
                "evidence list is required when domain is concluded (FR-017);"
                " set domain='unknown' + needs_human=true to opt out"
            )
        return self


__all__ = [
    "Domain",
    "EvidenceItem",
    "EvidenceSource",
    "Severity",
    "SuspectedDuplicate",
    "TriageOutput",
]
