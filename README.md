# Discord Reminders & Timetable Bot

![Tests](https://github.com/Onikirast/Discord-Bot/actions/workflows/tests.yml/badge.svg)

A personal Discord bot for reminders and a recurring weekly timetable,
built with `discord.py` and a lightweight async SQLite backend.

## Features

- **Reminders** — one-off or recurring (daily/weekly), supports relative
  ("2h", "30m", "1d12h") or absolute ("2026-07-25 14:30") time input.
- **Timetable** — recurring weekly entries (e.g. classes, gym sessions)
  with a configurable "notify X minutes before" alert.
- Reminders and timetable notifications survive bot restarts — everything
  is backed by SQLite, and a single background loop (`discord.ext.tasks`)
  polls for due items every 30 seconds rather than relying on in-memory
  timers.

## Setup

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add your bot token
# (.env is already git-ignored — never commit your real token)

python bot.py
```

You'll need a bot application at https://discord.com/developers/applications
with the "applications.commands" scope enabled when generating the invite URL.

## Commands

| Command | Description |
|---|---|
| `/remind add when message [recurrence]` | Set a reminder |
| `/remind list` | List your pending reminders |
| `/remind delete reminder_id` | Delete a reminder |
| `/timetable add day time title [notify_before]` | Add a manual weekly entry |
| `/timetable list` | List your manual timetable |
| `/timetable delete entry_id` | Delete an entry |
| `/dcu search query` | Search DCU programmes by name/code |
| `/dcu link name` | Link your DCU programme to your account |
| `/dcu unlink` | Remove your linked programme |
| `/dcu today` / `/dcu week` | Show your real DCU classes (live, official) |
| `/dcu calendar` | Get a self-updating calendar subscription link |
| `/grades add` | Log a grade you received (self-reported), with module autocomplete |
| `/grades list` | List your logged grades |
| `/grades gpa` | Weighted average + Irish honours classification |
| `/grades delete grade_id` | Delete a logged grade |

## DCU official timetable integration

`/dcu` pulls your **real, official DCU class schedule** directly from
**DCU's Scientia Enterprise Timetabler** ("Public" API) — the actual
system that generates DCU's timetable data.

**How this evolved:** the first version of this integration went through
Redbrick's [TimetableSync](https://timetable.redbrick.dcu.ie) REST wrapper
(source: [novanai/timetable-sync](https://github.com/novanai/timetable-sync)).
Live testing hit a 404 that traced back to their **frontend's own router**,
not their FastAPI backend — meaning their production deployment doesn't
currently match what's in their public GitHub source (or their reverse-proxy
setup differs from what's committed to the repo).

Rather than depend on a third party's live deployment staying in sync with
their source, `dcu_api.py` now talks **directly** to the same DCU-owned
Scientia endpoint that Redbrick's own official Discord bot uses internally
— confirmed by reading
[timetable-sync-api](https://github.com/novanai/timetable-sync-api)'s
`api.py`, which is the library that bot imports in-process (not over HTTP).
That endpoint is public and needs no real authentication, just an
`Authorization: Anonymous` header. The category-type UUIDs and payload
field names in `dcu_api.py` are taken directly from that verified,
working source — not guessed.

**Confirmed working end-to-end** (see `DEBUGGING_LOG.md` for the full testing story): `/dcu search`, `/dcu link`, and `/dcu week` have all been run live against real DCU data, returning correctly parsed module codes, times, and locations for a real Semester 2 timetable. If `/dcu today` or `/dcu week` return no results during the summer, that's expected — DCU's own site notes timetables aren't published until roughly a week before semester starts. Both commands accept an optional `date:YYYY-MM-DD` parameter to check any specific date, past or future, rather than only right now.

## Grades tracker

`/grades` is a **self-reported** grade tracker with a credit-weighted
average and Irish honours degree classification (First, 2.1, 2.2, Third).

This deliberately does **not** log into DCU to pull official grades. That
approach was considered and rejected: it would mean storing DCU login
credentials in the bot (a serious security liability if the bot's server
or `.env` file were ever compromised), is likely to break against
multi-factor authentication, probably falls foul of DCU's acceptable-use
policy for automated access, and — as a CV project — reads as a red flag
to a technical reviewer rather than an impressive feature.

Instead, you type in your own grade after you've already checked it
yourself, with the module name/code autocompleted from the same live DCU
module search `/dcu link` uses (so you're not retyping codes from memory,
but no grade data is ever pulled from anywhere but you).

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

58 tests, running in about a second, covering:
- `time_utils.py` — relative/absolute time parsing, day/time validation
- `database.py` — full CRUD for reminders (including recurrence rescheduling), timetable, DCU links, and grades, each test running against a fresh temporary SQLite file (never your real `bot.db`)
- `dcu_api.py` — the raw-payload-to-normalized-event parsing logic
- `cogs/dcu.py` — the datetime-parsing fix and embed-grouping logic from earlier bugs (see `DEBUGGING_LOG.md`)
- `cogs/grades.py` — the weighted-average and classification math, including the exact real-world case verified manually earlier (72%/5cr + 65%/5cr → 68.5%, 2.1)

Deliberately not covered: anything requiring a live Discord connection or a real network call to DCU's API — those stay manually tested, since mocking them thoroughly is a bigger undertaking than this pass covers.

## Design decisions (for the CV writeup)

- **Polling loop over per-item timers**: a single `tasks.loop` checks the
  database every 30s for due reminders/timetable entries. This trades a
  small amount of latency for a much simpler mental model and free
  persistence across restarts, versus scheduling individual `asyncio`
  tasks that would be lost on crash/restart.
- **UTC storage, local display**: all times are stored in UTC in SQLite;
  conversion to a user's local time is a display-layer concern. Avoids a
  whole class of timezone bugs.
- **Single `recurrence` column**: reminders use one table with a
  `once | daily | weekly` column instead of separate tables, keeping the
  "find due items" query path uniform.
- **Notification dedup log**: `timetable_notifications` records
  `(entry_id, date)` pairs so a restart mid-day doesn't cause duplicate
  pings for the same entry.
- **Shared connection/session over per-call creation**: a single SQLite
  connection and a single `aiohttp` session are opened once at startup
  and reused for every query/request, rather than opening a fresh one
  per call. Real recurring cost given the scheduler polls every 30s
  indefinitely -- see `DEBUGGING_LOG.md` entry #15 for the details and
  what actually broke while fixing it.
- **Timezone-aware timestamps throughout**: `datetime.utcnow()` (deprecated
  by Python) has been replaced with `datetime.now(timezone.utc)`
  consistently across every call site that produces a stored timestamp.
  **If you have an existing `bot.db` from before this change, delete it**
  -- old rows are in a naive timestamp format that doesn't compare
  correctly against new aware-format rows in the same column. `bot.db`
  is gitignored and regenerates automatically, so this is a one-time
  `rm bot.db` rather than a migration to write.

## Possible extensions

- Per-user timezone support via `zoneinfo`
- `/mood`, `/habit` cogs (see project roadmap)
- Weekly digest DM summarising the past week's reminders/timetable
- Dockerfile + deployment to Railway/Fly.io for 24/7 uptime
