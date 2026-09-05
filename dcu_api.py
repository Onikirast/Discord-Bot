"""
dcu_api.py

Direct client for DCU's Scientia Enterprise Timetabler "Public" API --
the actual system that generates DCU's official timetable data.

Why this, instead of Redbrick's timetable.redbrick.dcu.ie REST wrapper:
live testing hit a 404 there that traced back to their *frontend's own
router*, not their FastAPI backend -- meaning their production deployment
doesn't currently match what's in their public GitHub source (or their
reverse-proxy config differs from the one committed to the repo). Rather
than depend on a third party's deployment staying in sync with their
source, this talks directly to the same DCU-owned Scientia endpoint that
Redbrick's own official Discord bot uses internally -- confirmed by
reading https://github.com/novanai/timetable-sync-api/blob/main/timetable/api.py,
which is the library that bot imports directly (not over HTTP).

This endpoint is public and needs no real authentication -- just an
`Authorization: Anonymous` header. The category-type UUIDs and payload
field names below are taken directly from that source, not guessed.

One honest caveat: like the Redbrick API, this hasn't been tested against
a live network from the environment this bot was scaffolded in. It's
built against a verified, working reference implementation rather than
guesswork, but you should still smoke-test it for real.
"""

import datetime
import logging
from typing import Any, Literal

import aiohttp

logger = logging.getLogger("dcu_api")

BASE_URL = "https://scientia-eu-v4-api-d1-03.azurewebsites.net/api/Public"
INSTITUTION_IDENTITY = "a1fdee6b-68eb-47b8-b2ac-a4c60c8e6177"  # DCU's identity in Scientia

CategoryType = Literal["course", "module", "location"]

# These UUIDs are Scientia's own internal category-type identifiers for
# DCU, not something we chose -- taken verbatim from timetable-sync-api.
CATEGORY_TYPE_IDS: dict[CategoryType, str] = {
    "course": "241e4d36-60e0-49f8-b27e-99416745d98d",  # Programmes of Study
    "module": "525fe79b-73c3-4b5c-8186-83c652b3adcc",
    "location": "1e042cb1-547d-41d4-ae93-a1f2c3d34538",
}

HEADERS = {
    "Authorization": "Anonymous",
    "Content-Type": "application/json",
    "User-Agent": "PersonalDiscordBot/1.0 (student CV project; not affiliated with DCU/Redbrick)",
}


class DCUAPIError(Exception):
    """Raised when the Scientia API returns an error response."""


_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """Return the shared session, creating it on first use.

    aiohttp's own docs recommend one ClientSession per application
    lifetime rather than one per request -- a session holds a connection
    pool that enables keep-alive reuse to the same host, which matters
    here since every single call in this module hits the same Scientia
    endpoint. Creating a fresh session per call (the previous approach)
    throws that reuse away every time.
    """
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    """Close the shared session. Call this on bot shutdown to avoid an
    'Unclosed client session' warning."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def _post(path: str, params: dict[str, str] | None = None,
                 json_data: dict[str, Any] | None = None) -> Any:
    url = f"{BASE_URL}/{path}"
    session = _get_session()
    async with session.post(
        url, params=params, json=json_data, headers=HEADERS,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            text = (await resp.text())[:300]  # truncate: never dump a full error page
            raise DCUAPIError(f"Request failed ({resp.status}): {text}")
        return await resp.json()


async def search_category(category_type: CategoryType, query: str) -> list[dict[str, Any]]:
    """Search courses/modules/locations by name or code.

    Returns a list of {"name": str, "identity": str}.
    """
    type_id = CATEGORY_TYPE_IDS[category_type]
    data = await _post(
        f"CategoryTypes/{type_id}/Categories/FilterWithCache/{INSTITUTION_IDENTITY}",
        params={"pageNumber": "1", "query": query.strip()},
    )
    return [
        {"name": item["Name"], "identity": item["Identity"]}
        for item in data.get("Results", [])
    ]


async def get_category_item_by_identity(category_type: CategoryType, identity: str) -> dict[str, Any] | None:
    """Look up a single course/module/location by its exact identity (UUID).

    Unlike search_category, this doesn't do a text search -- it's an exact
    lookup, so it's the reliable way to resolve an identity a user pasted
    in directly (e.g. copied from a prior /dcu search result), rather than
    re-running a name-based search which can't match on a UUID at all.

    Returns {"name": str, "identity": str} or None if not found. Note: this
    endpoint returns a bare list, not the {"Results": [...]} wrapper that
    search_category's endpoint uses -- different shape, verified from the
    reference implementation's fetch_category_item().
    """
    type_id = CATEGORY_TYPE_IDS[category_type]
    data = await _post(
        f"CategoryTypes/Categories/Filter/{INSTITUTION_IDENTITY}",
        json_data={
            "CategoryTypesWithIdentities": [
                {"CategoryTypeIdentity": type_id, "CategoryIdentities": [identity]}
            ]
        },
    )
    if not data:
        return None
    item = data[0]
    return {"name": item["Name"], "identity": item["Identity"]}


def _extract_extra_properties(payload: dict[str, Any]) -> tuple[str | None, str | None, list[int] | None]:
    """Pull module name / staff member / weeks out of an event's ExtraProperties.

    Scientia ranks these 1/2/3 respectively rather than naming the fields
    directly -- this mapping is taken from the reference implementation,
    not guessed.
    """
    module_name = staff_member = None
    weeks: list[int] | None = None
    for item in payload.get("ExtraProperties", []):
        rank = item.get("Rank")
        if rank == 1:
            module_name = item.get("Value")
        elif rank == 2:
            staff_member = item.get("Value")
        elif rank == 3 and item.get("Value"):
            try:
                weeks = [int(w.strip()) for w in item["Value"].split(",") if w.strip().isdigit()]
            except ValueError:
                weeks = None
    return module_name, staff_member, weeks


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Scientia event payload into the simpler shape our cog expects."""
    module_name, staff_member, weeks = _extract_extra_properties(raw)
    location = raw.get("Location")
    description = (raw.get("Description") or "").strip() or None
    name = raw.get("Name", "")

    summary = module_name or description or name or "Class"

    return {
        "identity": raw.get("Identity"),
        "start": raw.get("StartDateTime"),
        "end": raw.get("EndDateTime"),
        "name": name,
        "description": description,
        "module_name": module_name,
        "staff_member": staff_member,
        "weeks": weeks,
        "extras": {
            "summary": summary,
            "location": location or "TBD",
        },
    }


async def get_events(
    category_type: CategoryType,
    item_identity: str,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[dict[str, Any]]:
    """Fetch events for a single course/module/location identity within a date range.

    Returns a list of normalized event dicts (see _normalize_event), sorted
    by start time isn't guaranteed -- sort in the caller if needed.
    """
    type_id = CATEGORY_TYPE_IDS[category_type]

    start_utc = start.astimezone(datetime.timezone.utc)
    end_utc = end.astimezone(datetime.timezone.utc)

    data = await _post(
        f"CategoryTypes/Categories/Events/Filter/{INSTITUTION_IDENTITY}",
        params={
            # Matches the exact string format used by the reference
            # implementation -- a naive-looking ISO string with a literal
            # 'Z' appended, rather than a true tz-aware ISO offset.
            "startRange": f"{start_utc.strftime('%Y-%m-%dT%H:%M:%S')}Z",
            "endRange": f"{end_utc.strftime('%Y-%m-%dT%H:%M:%S')}Z",
        },
        json_data={
            "ViewOptions": {
                "Days": [{"DayOfWeek": d} for d in range(1, 7)],
            },
            "CategoryTypesWithIdentities": [
                {
                    "CategoryTypeIdentity": type_id,
                    "CategoryIdentities": [item_identity],
                }
            ],
        },
    )

    events: list[dict[str, Any]] = []
    for timetable in data.get("CategoryEvents", []):
        for raw_event in timetable.get("Results", []):
            events.append(_normalize_event(raw_event))
    return events


def calendar_subscription_url(category_type: CategoryType, item_identity: str) -> str:
    """No native calendar-subscription endpoint exists for this direct Scientia
    client (unlike Redbrick's wrapper, which generates .ics files). Point
    users at Redbrick's own generator page instead, which remains a valid
    way to get a subscribable link even though its underlying API had
    issues in our testing -- the /generator page itself is a normal,
    working part of the site.
    """
    return "https://timetable.redbrick.dcu.ie/generator"
