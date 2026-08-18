"""
tests/test_dcu_cog.py

Tests _parse_api_datetime and _build_day_embed from cogs/dcu.py.

_parse_api_datetime formalizes the exact three input shapes that were
manually verified earlier in the project (Z-suffix, naive, and
offset-aware datetimes) after a real bug was found in the original
version. _build_day_embed formalizes the manual check that simultaneous
classes group into one field while keeping their own separate locations.
"""

import datetime

from cogs.dcu import _parse_api_datetime, _build_day_embed


# ---------- _parse_api_datetime ----------

def test_parse_api_datetime_z_suffix():
    # This exact input crashed on Python < 3.11 before the fix.
    result = _parse_api_datetime("2026-07-23T10:00:00Z")
    assert result.tzinfo is not None
    assert result.utcoffset() == datetime.timedelta(0)


def test_parse_api_datetime_naive_assumes_utc():
    # Before the fix, a naive datetime would silently be treated as the
    # host machine's local timezone instead of UTC.
    result = _parse_api_datetime("2026-07-23T10:00:00")
    assert result.tzinfo == datetime.timezone.utc


def test_parse_api_datetime_offset_aware_preserved():
    result = _parse_api_datetime("2026-07-23T10:00:00+01:00")
    assert result.utcoffset() == datetime.timedelta(hours=1)


def test_parse_api_datetime_all_three_forms_agree_on_same_instant():
    # A UTC 10:00 and a +01:00 11:00 are the same real moment in time.
    utc_version = _parse_api_datetime("2026-07-23T10:00:00Z")
    offset_version = _parse_api_datetime("2026-07-23T11:00:00+01:00")
    assert utc_version == offset_version


# ---------- _build_day_embed ----------

def _event(start, end, summary, location):
    return {
        "start": f"2026-03-16T{start}:00Z",
        "end": f"2026-03-16T{end}:00Z",
        "extras": {"summary": summary, "location": location},
        "name": summary,
    }


def test_build_day_embed_basic_fields():
    events = [_event("09:00", "10:00", "CSC1018[2] Logic", "GLA.CG12")]
    embed = _build_day_embed("COMSCI2", "Monday 16 Mar", events)
    assert "Monday 16 Mar" in embed.title
    assert "COMSCI2" in embed.footer.text
    assert len(embed.fields) == 1
    assert "09:00" in embed.fields[0].name
    assert "10:00" in embed.fields[0].name


def test_build_day_embed_groups_simultaneous_classes_into_one_field():
    events = [
        _event("10:00", "11:00", "CSC1022[2] Databases", "GLA.LG25"),
        _event("10:00", "11:00", "CSC1005[2] Data Science", "GLA.L129"),
    ]
    embed = _build_day_embed("COMSCI2", "Monday", events)
    assert len(embed.fields) == 1
    # Each module keeps its own location rather than merging locations together.
    assert "GLA.LG25" in embed.fields[0].value
    assert "GLA.L129" in embed.fields[0].value
    assert "CSC1022[2]" in embed.fields[0].value
    assert "CSC1005[2]" in embed.fields[0].value


def test_build_day_embed_separate_times_produce_separate_fields():
    events = [
        _event("09:00", "10:00", "Module A", "Room A"),
        _event("13:00", "14:00", "Module B", "Room B"),
    ]
    embed = _build_day_embed("COMSCI2", "Monday", events)
    assert len(embed.fields) == 2
