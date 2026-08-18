"""
tests/test_dcu_api.py

Only tests the pure parsing functions -- _extract_extra_properties and
_normalize_event -- which take a raw payload and transform it, with no
network call involved. The actual HTTP functions (search_category,
get_events) aren't tested here since that would require mocking a real
network call, which is out of scope for this pass.
"""

from dcu_api import _extract_extra_properties, _normalize_event


def _raw_event(**overrides):
    """A minimal but realistic raw Scientia event payload, with sensible
    defaults so each test only needs to override what it's checking."""
    base = {
        "Identity": "event-123",
        "StartDateTime": "2026-03-16T09:00:00Z",
        "EndDateTime": "2026-03-16T10:00:00Z",
        "Name": "CSC1018[2] Logic",
        "Description": "Logic lecture",
        "Location": "GLA.CG12",
        "ExtraProperties": [
            {"Rank": 1, "Value": "CSC1018[2] Logic"},
            {"Rank": 2, "Value": "Dr. Smith"},
            {"Rank": 3, "Value": "1,2,3,4,5"},
        ],
    }
    base.update(overrides)
    return base


# ---------- _extract_extra_properties ----------

def test_extract_extra_properties_full():
    module_name, staff_member, weeks = _extract_extra_properties(_raw_event())
    assert module_name == "CSC1018[2] Logic"
    assert staff_member == "Dr. Smith"
    assert weeks == [1, 2, 3, 4, 5]


def test_extract_extra_properties_missing_returns_none():
    module_name, staff_member, weeks = _extract_extra_properties({"ExtraProperties": []})
    assert module_name is None
    assert staff_member is None
    assert weeks is None


def test_extract_extra_properties_no_key_at_all():
    # Real payloads should always have this key, but the function
    # shouldn't crash if it's ever absent.
    module_name, staff_member, weeks = _extract_extra_properties({})
    assert module_name is None
    assert staff_member is None
    assert weeks is None


# ---------- _normalize_event ----------

def test_normalize_event_basic_fields():
    event = _normalize_event(_raw_event())
    assert event["identity"] == "event-123"
    assert event["start"] == "2026-03-16T09:00:00Z"
    assert event["end"] == "2026-03-16T10:00:00Z"
    assert event["module_name"] == "CSC1018[2] Logic"
    assert event["staff_member"] == "Dr. Smith"


def test_normalize_event_summary_prefers_module_name():
    event = _normalize_event(_raw_event())
    assert event["extras"]["summary"] == "CSC1018[2] Logic"


def test_normalize_event_summary_falls_back_to_description(): 
    event = _normalize_event(_raw_event(ExtraProperties=[]))
    assert event["extras"]["summary"] == "Logic lecture"


def test_normalize_event_summary_falls_back_to_name_when_nothing_else():
    event = _normalize_event(_raw_event(ExtraProperties=[], Description=""))
    assert event["extras"]["summary"] == "CSC1018[2] Logic"  # falls back to Name


def test_normalize_event_missing_location_shows_tbd():
    event = _normalize_event(_raw_event(Location=None))
    assert event["extras"]["location"] == "TBD"


def test_normalize_event_preserves_real_location():
    event = _normalize_event(_raw_event())
    assert event["extras"]["location"] == "GLA.CG12"
