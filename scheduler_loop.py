"""
scheduler_loop.py

The single background task that powers both reminders and the timetable.
Runs every CHECK_INTERVAL seconds, checks the database for anything due,
and sends it via the appropriate Discord channel.

Design choice: one polling loop instead of scheduling individual
asyncio tasks per-reminder. This is simpler, and since everything is
backed by SQLite, reminders survive a bot restart without extra work
(APScheduler + persistent jobstore would achieve the same thing but
adds a dependency and setup complexity that isn't needed at this scale).
"""

import logging
from datetime import datetime, timedelta

from discord.ext import tasks

import database

logger = logging.getLogger("scheduler")

CHECK_INTERVAL_SECONDS = 30


def setup_scheduler(bot):
    """Attach the background loop to the bot instance and start it."""

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def check_due_items():
        await _check_reminders(bot)
        await _check_timetable(bot)

    @check_due_items.before_loop
    async def before():
        await bot.wait_until_ready()

    check_due_items.start()
    return check_due_items


async def _check_reminders(bot):
    now = datetime.utcnow()
    due = await database.get_due_reminders(now)

    for reminder in due:
        channel = bot.get_channel(reminder["channel_id"])
        user_mention = f"<@{reminder['user_id']}>"
        try:
            if channel:
                await channel.send(f"⏰ {user_mention} reminder: **{reminder['message']}**")
            else:
                # Fall back to DM if the channel can't be resolved (e.g. bot restarted)
                user = await bot.fetch_user(reminder["user_id"])
                await user.send(f"⏰ Reminder: **{reminder['message']}**")
        except Exception:
            logger.exception("Failed to deliver reminder %s", reminder["id"])

        old_time = datetime.fromisoformat(reminder["remind_at"])
        await database.reschedule_or_close_reminder(
            reminder["id"], reminder["recurrence"], old_time
        )


async def _check_timetable(bot):
    now = datetime.utcnow()
    today_index = now.weekday()  # Monday=0 ... Sunday=6
    today_str = now.strftime("%Y-%m-%d")

    entries = await database.get_entries_for_day(today_index)

    for entry in entries:
        start_time = datetime.strptime(entry["start_time"], "%H:%M").time()
        start_dt = datetime.combine(now.date(), start_time)
        notify_at = start_dt - timedelta(minutes=entry["notify_before_minutes"])

        # Fire once we're within the current check window past notify_at,
        # and only if we haven't already notified for this entry today.
        if notify_at <= now < notify_at + timedelta(seconds=CHECK_INTERVAL_SECONDS * 2):
            if await database.was_notified(entry["id"], today_str):
                continue

            channel = bot.get_channel(entry["channel_id"])
            user_mention = f"<@{entry['user_id']}>"
            try:
                if channel:
                    await channel.send(
                        f"📅 {user_mention} upcoming: **{entry['title']}** "
                        f"at {entry['start_time']} today"
                    )
                else:
                    user = await bot.fetch_user(entry["user_id"])
                    await user.send(
                        f"📅 Upcoming: **{entry['title']}** at {entry['start_time']} today"
                    )
            except Exception:
                logger.exception("Failed to deliver timetable notification %s", entry["id"])

            await database.mark_notified(entry["id"], today_str)
