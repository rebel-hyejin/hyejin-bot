"""Pydantic v2 schema for the JSON Claude must produce per
`specs/002-jira-triage-bot/contracts/claude-triage-output.md` §1.

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
    """Top-level Claude output — see `claude-triage-output.md` §1."""

    model_config = {"extra": "forbid"}

    summary_md: str = Field(min_length=1, max_length=16_000)
    domain: Domain
    severity: Severity
    suspected_duplicates: list[SuspectedDuplicate] = Field(default_factory=list, max_length=5)
    needs_human: bool
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=50)

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
