# Debugging Log

A chronological record of every error hit and fix applied while setting up
and testing this bot for the first time. Kept for two reasons: to make the
project easy to pick back up later, and because a documented debugging
process is worth showing on a CV in its own right — it demonstrates real
problem-solving, not just a finished result.

Entries are split into **environment/setup issues** (nothing wrong with
the code, just first-time-setup friction) and **actual code bugs** (real
mistakes that were found and fixed).

---

## Environment & setup issues

These weren't bugs in the bot — they were the normal friction of setting
up a Python project on Windows for the first time.

| Issue | Cause | Fix |
|---|---|---|
| `python3 --version` → "Python was not found... Microsoft Store" | Windows registers a stub alias that redirects to the Store | Use `python` instead of `python3` on Windows |
| `source venv/bin/activate` → not recognized | `source` is a Mac/Linux shell command; PowerShell doesn't have it, and Windows venvs use `Scripts\`, not `bin/` | `venv\Scripts\Activate.ps1` |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell's default execution policy blocks local scripts | `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `copy .env.example .env` → "Cannot find path .env.example" | An earlier extraction of the project zip hadn't preserved the hidden `.env.example` file in that folder | Recreated `.env` directly with `Out-File`, then later confirmed the zip's contents included it correctly |
| `copy .env.example env` created a stray `env` file | Typo — missing the leading dot on `.env` | Re-ran with the correct `.env` filename |
| `pip install -r requirements.txt` → "Could not open requirements file" | Working directory didn't yet contain the extracted project files | Downloaded and extracted the full project zip properly, confirmed with `dir` |
| `python bot.py` → `ModuleNotFoundError: No module named 'discord'` | The venv wasn't actually active yet when packages were (or weren't) installed | Re-activated venv, re-ran `pip install -r requirements.txt`, confirmed with `pip list` |

---

## Real code bugs found and fixed

### 1. Missing `tzdata` package on Windows
**Symptom:** `ZoneInfoNotFoundError: No time zone found with key Europe/Dublin`, crashing the whole bot on startup.
**Cause:** Python's `zoneinfo` module relies on the OS having IANA timezone data installed. Linux/Mac ship this by default; Windows doesn't. This was tested in a Linux sandbox during development, so the gap wasn't caught until real Windows testing.
**Fix:** Added `tzdata>=2024.1` to `requirements.txt`.

### 2. Slash commands re-syncing on every reconnect
**Symptom:** No visible error yet, but a latent risk — `bot.tree.sync()` was called inside `on_ready`, which Discord fires on *every* reconnect, not just the first login.
**Cause:** Should only run once at startup.
**Fix:** Moved all one-time startup work (DB init, cog loading, command sync) into `setup_hook()`, which discord.py guarantees runs exactly once.

### 3. Fragile datetime parsing for DCU event times
**Symptom:** Not yet hit live, but found on inspection — `datetime.fromisoformat()` would crash on Python < 3.11 if the API returned a trailing `Z`, and would silently assume the *host machine's* local timezone (not UTC) for any naive datetime, risking wrong displayed class times depending on where the bot runs.
**Fix:** Added `_parse_api_datetime()` in `cogs/dcu.py` that normalizes both the `Z`-suffix case and the naive-datetime case to UTC explicitly. Verified with direct unit tests against all three input shapes (`Z`-suffix, naive, offset-aware).

### 4. `/dcu` commands hanging forever on network failure
**Symptom:** Real bug hit live — `/dcu search` got stuck on "DCU is thinking..." indefinitely.
**Cause:** Error handling only caught a custom `DCUAPIError`, not `asyncio.TimeoutError` or `aiohttp.ClientError`. A network timeout raised an exception type that was never caught, so the interaction was deferred but never received a follow-up response.
**Fix:** Widened exception handling in every DCU command to catch network-level failures specifically, **and** added a cog-wide `cog_app_command_error` fallback so any *future* unhandled error still gets a response instead of a silent hang. Verified by simulating the exact failure (forced a timeout) and confirming a response was sent.

### 5. Crash-on-crash from oversized error messages
**Symptom:** Following bug #4's fix, a *second* error appeared: `discord.errors.HTTPException: 400 Bad Request... Must be 2000 or fewer in length`.
**Cause:** The first bug's error handler tried to show the user the raw API error response — which, in this case, was a full HTML page (DCU's frontend 404 page), far exceeding Discord's 2000-character message limit.
**Fix:** Truncated any raw response text shown to the user to 300 characters in `dcu_api.py`'s request handler.

### 6. Wrong integration target entirely — Redbrick's REST API 404
**Symptom:** `/dcu search` returned a real error (not a hang, thanks to fix #4): a 404 page from a **React Router frontend**, not from the FastAPI backend the code was built against.
**Cause:** The 404 traced back to the frontend's own client-side router reporting "no route matches this URL" — meaning the request never reached Redbrick's backend logic at all. Their live production deployment doesn't currently match the routing structure in their public GitHub source (or their reverse-proxy config differs from what's committed to the repo).
**Fix:** Rather than keep guessing at a third party's deployment, found that Redbrick's own **official Discord bot** (also in their repo) doesn't call their HTTP API at all — it talks directly to DCU's actual Scientia timetabling backend, in-process. Read that library's source to get the real, verified request format (category-type UUIDs, field names, endpoint paths) and rebuilt `dcu_api.py` to call DCU's Scientia backend directly, removing the dependency on Redbrick's web app deployment entirely.
**Confirmed working:** `/dcu search query:Computer Science` returned real DCU programme data (`COMSCI1` through `COMSCI4`, correct names and identities) on the first live test after the rewrite.

---

### 7. `/dcu link` too strict about exact spacing/punctuation
**Symptom:** Real bug hit live — `/dcu search` correctly returned real DCU programmes (e.g. `COMSCI3 (Computer Science-3)`), but `/dcu link` failed with "No programme found" when the name was typed as `COMSCI3(Computer Science-3)` (missing the space before the parenthesis).
**Cause:** The exact-match check compared strings directly (`r["name"].lower() == name.lower()`), with no tolerance for whitespace differences — an easy, common way for a real user to get tripped up copying a name by hand.
**Fix:** Two-part fix: (1) normalized whitespace/case before comparing names, so minor formatting differences no longer cause false negatives; (2) added a more robust alternative — `/dcu link identity:<uuid>` — that looks up a programme by its exact identity via a dedicated Scientia endpoint (`CategoryTypes/Categories/Filter`, distinct from the search endpoint), sidestepping name-matching entirely. Verified the normalization fix directly against the exact strings from the real failure.

### 8. `/dcu today` / `/dcu week` returning empty results
**Symptom:** Not actually a bug — `/dcu today` and `/dcu week` both returned "No classes scheduled" for July 25, 2026.
**Investigation:** Rather than assume this was correct, checked DCU's own academic calendar: it's deep into summer break, and DCU's timetable-information page explicitly states timetables aren't published until roughly a week before semester starts. An empty result was the *expected* answer, not a failure — but that's indistinguishable from an actual bug without a way to check a date known to have real data.
**Resolution:** Added an optional `date` parameter to both commands (`/dcu today date:2026-03-16`) so a real, already-elapsed semester week could be checked instead of waiting until September.
**Confirmed working end-to-end:** `/dcu week date:2026-03-16` returned a fully correct, real DCU Semester 2 timetable — module codes (`CSC1018[2]`, `CSC1022[2]`, etc.), names, time slots, and multiple room locations per class, formatted correctly. This is full confirmation that every stage of the pipeline works correctly against real data: search → link → date-ranged query → Scientia API → event parsing → Discord message formatting.

### 9. Plain-text timetable output was hard to read
**Not a bug, a UX improvement request:** the working `/dcu today`/`/dcu week` output was correct but plain text, unlike the rich embed formatting other Discord bots use (e.g. music bots showing track info in a structured card).
**Change:** Rebuilt the output as Discord `Embed` objects — one per day, with each field representing a time slot. Classes happening simultaneously (e.g. two elective options at the same time) are grouped into one field, each keeping its own location rather than merging locations across different modules. Verified against data shaped like the real Semester 2 response: correct grouping, and well under Discord's 6000-character embed limit (473/6000 used for a 6-class day).

### 10. Linking required typing the full programme name
**Not a bug, a UX improvement request:** `/dcu link` worked, but required typing the full name (`COMSCI2 (Computer Science-2)`) or manually copying a UUID -- tedious for something used every session.
**Change:** Added Discord autocomplete to the `identity` parameter. Typing a short code like `COMSCI2` now shows a live dropdown of matching real programmes to select, with the underlying value being the exact identity (UUID) -- so selecting a suggestion is always unambiguous, even for the two programmes sharing the `COMSCI3` code. Verified directly: simulated a real search result and confirmed the autocomplete callback returns the correct `(display name, identity)` pairs, and that short input (<2 characters) returns nothing without needlessly hitting the API.

### 11. `app_commands.Range` type mismatch when adding the grades feature
**Symptom:** `TypeError: Both min and max in Range must be the same type`, crashing on cog load before the bot even reached Discord.
**Cause:** `app_commands.Range[float, 0, 100]` mixed `int` literals (`0`, `100`) with a `float` type parameter — discord.py requires both bounds to match the declared type exactly.
**Fix:** Changed all bounds to explicit floats (`0.0, 100.0` and `0.5, 60.0`). Caught immediately by actually running the cog rather than just reading the code — a good example of why "looks right" and "runs correctly" aren't the same thing.
**Confirmed working end-to-end:** logged real grades live, deleted and re-added entries, and ran `/grades gpa` against two real modules (72%/5cr, 65%/5cr) -- correctly computed 68.50% weighted average and the 2.1 classification band.

### 12. `/grades` not appearing in Discord after adding the cog
**Symptom:** `cogs/grades.py` existed and compiled fine, but `/grades` never showed up as a command in Discord.
**Cause:** Two separate, compounding issues: (1) `bot.py`'s `INITIAL_COGS` list hadn't been updated to include `"cogs.grades"`, so the cog was never loaded even though the file existed; (2) after fixing that and confirming a successful sync in the terminal log, Discord's client-side command cache still didn't show the new command until the Discord app was fully quit and reopened.
**Fix:** Added `cogs.grades` to `INITIAL_COGS`. Documented the Discord-client-caching behavior as a general troubleshooting step for future feature additions: a successful "Synced N slash commands" log line confirms the *bot* did its job -- if the command still doesn't appear, the next step is restarting the Discord client, not debugging the bot's code further.

### 13. Deleted grade IDs "skip" instead of being reused
**Not a bug:** after deleting grade #1 and adding a new one, the new entry was assigned #2, not a reused #1. This is standard `AUTOINCREMENT` behavior in SQLite -- IDs are never reused once assigned, even after deletion, which is the correct and safer choice (prevents an ID silently referring to a different record than the one originally referenced, e.g. in a support conversation or note-to-self). Confirmed as expected behavior rather than patched as a bug.

### 14. Building the automated test suite
**Not a bug fix, a durability improvement:** added a `pytest` suite (58 tests) covering `time_utils.py`, `database.py`, the pure parsing functions in `dcu_api.py` and `cogs/dcu.py`, and the weighted-average/classification math in `cogs/grades.py`. Several tests formalize checks that had previously only been done manually in this conversation -- e.g. the exact 72%/5cr + 65%/5cr → 68.5% calculation, the three datetime-format edge cases from bug #3, and the simultaneous-class embed grouping from the embed rewrite.
**A real finding along the way:** running the suite surfaced a `DeprecationWarning` on every call to `datetime.utcnow()` (used throughout `time_utils.py` and `database.py`) -- Python has marked it for removal in a future version in favor of timezone-aware `datetime.now(datetime.UTC)`. Not an active bug (everything still works correctly today), but worth fixing before it becomes one. Logged here rather than fixed immediately, since it touches many call sites and deserves its own pass.
**Design choice worth noting for the CV writeup:** deliberately did not attempt to test `bot.py`, the scheduler loop, or any function requiring a live Discord connection or real network call to DCU's API -- those remain manually tested. Mocking a Discord gateway connection or an external HTTP API convincingly is a meaningfully bigger undertaking than unit-testing pure functions, and doing it half-heartedly would produce tests that pass without proving much. Knowing where to draw that line is itself part of testing well.

### 15. Optimization pass: connection reuse, HTTP session reuse, and the datetime fix
**Not a bug fix, a performance/hygiene pass**, prompted by a direct question ("are all codes optimised?") rather than something broken. Went looking systematically rather than guessing, using both `ruff` (broader rule set: PERF, SIM, C4, UP, ASYNC) and manual review for things linting can't catch.

**Finding 1 — a fresh SQLite connection opened per query.** Every single function in `database.py` (18 call sites) opened and closed its own `aiosqlite.connect()` rather than reusing one. Since the scheduler loop calls into this module every 30 seconds indefinitely, this was real recurring overhead, not a one-off cost. Fixed with a module-level shared connection (`init_db()` opens it once, `close_db()` releases it on shutdown), safe because aiosqlite serializes access through its own background thread. Verified directly: confirmed the same connection object is reused across multiple calls, and that `close_db()` actually releases it.

**Finding 2 — a fresh aiohttp session created per DCU API request.** `dcu_api.py`'s `_post()` created a brand-new `aiohttp.ClientSession()` on every call, throwing away connection-pooling/keep-alive benefits every time despite every request hitting the same host. Fixed with a lazily-created shared session, closed on bot shutdown via a new `DiscordBot.close()` override in `bot.py`.

**Finding 3 — actually fixing the `datetime.utcnow()` deprecation** flagged earlier (entry #14) as "known but deferred." This turned out to be more involved than a simple find-and-replace: `time_utils.py`'s `parse_when()` has two branches (relative shorthand and absolute datetime), and only fixing one would have created a *new* inconsistency between them, since these values get stored as ISO strings and compared as plain TEXT in SQL -- a naive-format string and an aware-format string with the same instant don't compare correctly as text. Similarly, `scheduler_loop.py`'s `datetime.combine()` call defaults to producing a naive datetime, which would have crashed (`TypeError: can't compare offset-naive and offset-aware`) the moment it was compared against the now-aware `now` variable, if not given `tzinfo=timezone.utc` explicitly. Fixed all call sites together (`time_utils.py`, `database.py`, `scheduler_loop.py`) to consistently use `datetime.now(timezone.utc)`.
**A real regression caught by the test suite itself:** after this change, 4 tests in `test_time_utils.py` failed -- not because the new code was wrong, but because the *tests* still compared against naive `datetime.utcnow()`. This is exactly what the test suite is for: it caught a real inconsistency immediately, in under a second, rather than that surfacing later as a silent wrong-comparison bug in production. Fixed the tests to match the new (correct) behavior, added one more test explicitly asserting both branches of `parse_when()` agree on being timezone-aware, and reran -- 59 passed.
**Migration note, not a bug:** any existing `bot.db` created before this change has old rows stored in the *naive* timestamp format. Mixing naive- and aware-format ISO strings in the same column would produce subtly wrong `remind_at <= ?` comparisons for those old rows specifically (lexicographic string comparison doesn't handle the format difference correctly). Since `bot.db` is gitignored and documented as "regenerated automatically," the practical fix is simply deleting it once before running the updated code, rather than writing a migration script for what is, at this stage, personal test data.