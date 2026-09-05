"""
tests/test_time_utils.py

time_utils.py has no database or Discord dependency, so these tests
need no fixtures -- just call the functions directly and assert on
what comes back.
"""

from datetime import datetime, timedelta, timezone

import pytest

from time_utils import parse_when, parse_hhmm, parse_day, TimeParseError


# ---------- parse_when: relative shorthand ----------

def test_parse_when_hours():
    result = parse_when("2h")
    expected = datetime.now(timezone.utc) + timedelta(hours=2)
    # Allow a couple of seconds of slack for the time the test itself takes to run.
    assert abs((result - expected).total_seconds()) < 3


def test_parse_when_minutes():
    result = parse_when("30m")
    expected = datetime.now(timezone.utc) + timedelta(minutes=30)
    assert abs((result - expected).total_seconds()) < 3


def test_parse_when_combined_days_hours():
    result = parse_when("1d12h")
    expected = datetime.now(timezone.utc) + timedelta(days=1, hours=12)
    assert abs((result - expected).total_seconds()) < 3


def test_parse_when_zero_duration_rejected():
    # "0m" parses as a valid shorthand shape but a zero-length duration
    # isn't a sensible reminder -- should raise, not silently succeed.
    with pytest.raises(TimeParseError):
        parse_when("0m")


# ---------- parse_when: absolute datetimes ----------

def test_parse_when_absolute():
    result = parse_when("2026-08-01 14:30")
    assert result == datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc)


def test_parse_when_absolute_is_timezone_aware():
    # Both branches of parse_when must agree on being timezone-aware --
    # a mismatch here would silently corrupt comparisons once stored.
    result = parse_when("2026-08-01 14:30")
    assert result.tzinfo == timezone.utc


def test_parse_when_garbage_input_rejected():
    with pytest.raises(TimeParseError):
        parse_when("not a time at all")


# ---------- parse_hhmm ----------

def test_parse_hhmm_valid():
    assert parse_hhmm("09:30") == "09:30"


def test_parse_hhmm_normalizes_missing_leading_zero():
    # strptime is lenient about a missing leading zero on input; the
    # function should still normalize the *output* to two-digit form.
    assert parse_hhmm("9:5") == "09:05"


def test_parse_hhmm_rejects_out_of_range_hour():
    with pytest.raises(TimeParseError):
        parse_hhmm("25:00")


def test_parse_hhmm_rejects_garbage():
    with pytest.raises(TimeParseError):
        parse_hhmm("not a time")


# ---------- parse_day ----------

def test_parse_day_valid():
    assert parse_day("monday") == 0
    assert parse_day("sunday") == 6


def test_parse_day_case_and_whitespace_insensitive():
    assert parse_day("  Wednesday  ") == 2


def test_parse_day_rejects_invalid_name():
    with pytest.raises(TimeParseError):
        parse_day("someday")
