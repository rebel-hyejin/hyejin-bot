"""Parsing helpers for jira_triage — T038 tests."""

from __future__ import annotations

from datetime import datetime

from hyejin_bot.handlers.jira_triage_parsing import (
    extract_error_log,
    parse_ssh_url,
    parse_timestamps,
    parse_title,
)

# ── parse_title (FR-008) ──────────────────────────────────────────────────────


def test_parse_title_canonical_format() -> None:
    out = parse_title("regression-test . ssw-giga-02 . TC-0033-Dram_test_with_exception")
    assert out is not None
    assert out.hostname == "ssw-giga-02"
    assert out.tc_name == "TC-0033-Dram_test_with_exception"


def test_parse_title_with_extra_whitespace() -> None:
    out = parse_title("regression-test  .   ssw-giga-02  .  TC-0001-foo")
    assert out is not None
    assert out.hostname == "ssw-giga-02"


def test_parse_title_misformatted_returns_none() -> None:
    assert parse_title("New regression failure") is None
    assert parse_title("regression-test ssw-giga-02 TC-1-x") is None  # missing dots
    assert parse_title("regression-test . ssw-giga-02") is None  # missing TC


def test_parse_title_empty_returns_none() -> None:
    assert parse_title("") is None


def test_parse_title_hostname_with_dot() -> None:
    """Some test hosts are FQDN — hostname regex allows `.` characters."""
    out = parse_title("regression-test . ssw.giga.02 . TC-0001-x")
    assert out is not None
    assert out.hostname == "ssw.giga.02"


# ── parse_timestamps (FR-006) ─────────────────────────────────────────────────


def test_parse_timestamps_space_separator() -> None:
    body = "Start: 2026-05-13 06:54:48.924242\nEnd: 2026-05-13 07:07:38.172125"
    out = parse_timestamps(body)
    assert out is not None
    start, end = out
    assert start == datetime(2026, 5, 13, 6, 54, 48, 924242)
    assert end == datetime(2026, 5, 13, 7, 7, 38, 172125)


def test_parse_timestamps_T_separator() -> None:
    body = "Start 2026-05-13T06:54:48.924242 End 2026-05-13T07:07:38.172125"
    out = parse_timestamps(body)
    assert out is not None


def test_parse_timestamps_low_precision_subsecond() -> None:
    """Some Jira sources truncate microseconds — pad with zeros."""
    body = "Start: 2026-05-13 06:54:48.9 End: 2026-05-13 07:07:38.0"
    out = parse_timestamps(body)
    assert out is not None
    start, _end = out
    assert start.microsecond == 900_000  # 0.9s → 900_000 us


def test_parse_timestamps_missing_returns_none() -> None:
    assert parse_timestamps("no timestamps here") is None
    assert parse_timestamps("Only one: 2026-05-13 06:54:48.0") is None


def test_parse_timestamps_end_before_start_returns_none() -> None:
    """Logical guard — End must be after Start."""
    body = "2026-05-13 07:07:38.172125 then 2026-05-13 06:54:48.924242"
    assert parse_timestamps(body) is None


def test_parse_timestamps_empty_input() -> None:
    assert parse_timestamps("") is None


# ── parse_ssh_url (FR-007) ────────────────────────────────────────────────────


def test_parse_ssh_url_canonical_form() -> None:
    body = (
        "Log dump: ssh://automation@ssw-giga-02:"
        "/mnt/data/logs/regression-test/25746526668-1/ssw-giga-02/"
        "TC-0033-Dram_test_with_exception"
    )
    out = parse_ssh_url(body)
    assert out is not None
    assert out.host == "ssw-giga-02"
    assert out.run_id == "25746526668-1"
    assert "TC-0033" in out.remote_path


def test_parse_ssh_url_missing_returns_none() -> None:
    assert parse_ssh_url("no ssh URL here") is None


def test_parse_ssh_url_extracts_run_id_with_dashes() -> None:
    body = "ssh://automation@h:/mnt/data/logs/regression-test/123-456-789/h/TC-1-x"
    out = parse_ssh_url(body)
    assert out is not None
    assert out.run_id == "123-456-789"


# ── extract_error_log ─────────────────────────────────────────────────────────


def test_extract_error_log_returns_noformat_block() -> None:
    body = "Some prelude\n{noformat}\nstack trace here\nline 2\n{noformat}\nfooter"
    out = extract_error_log(body)
    assert "stack trace here" in out
    assert "line 2" in out
    assert "prelude" not in out


def test_extract_error_log_picks_longest_noformat() -> None:
    body = "{noformat}short{noformat}\nmore\n{noformat}this is a much longer block{noformat}"
    out = extract_error_log(body)
    assert "much longer block" in out


def test_extract_error_log_falls_back_to_body_prefix() -> None:
    body = "no noformat block, just prose explaining the failure"
    out = extract_error_log(body)
    assert out == body


def test_extract_error_log_truncates_to_4k() -> None:
    body = "x" * 5000
    out = extract_error_log(body)
    assert len(out) == 4096


def test_extract_error_log_empty_input() -> None:
    assert extract_error_log("") == ""
