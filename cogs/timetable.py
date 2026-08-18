"""
cogs/timetable.py

Slash commands for a recurring weekly timetable (e.g. classes, gym
sessions, standing meetings). Distinct from reminders: timetable
entries repeat every week on the same day/time by design.
"""

import discord
from discord import app_commands
from discord.ext import commands

import database
from time_utils import parse_hhmm, parse_day, TimeParseError, DAY_NAMES


class Timetable(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    timetable_group = app_commands.Group(name="timetable", description="Manage your weekly timetable")

    @timetable_group.command(name="add", description="Add a recurring weekly entry")
    @app_commands.describe(
        day="Day of the week, e.g. 'monday'",
        time="24h time, e.g. '09:30'",
        title="What is this entry for?",
        notify_before="Minutes before start to notify (default 10)",
    )
    async def add(self, interaction: discord.Interaction, day: str, time: str,
                  title: str, notify_before: int = 10):
        try:
            day_index = parse_day(day)
            time_str = parse_hhmm(time)
        except TimeParseError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        entry_id = await database.add_timetable_entry(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            day_of_week=day_index,
            start_time=time_str,
            title=title,
            notify_before_minutes=notify_before,
        )

        await interaction.response.send_message(
            f"✅ Added #{entry_id}: **{title}** every {DAY_NAMES[day_index].title()} at {time_str} "
            f"(notify {notify_before}m before)."
        )

    @timetable_group.command(name="list", description="List your weekly timetable")
    async def list_entries(self, interaction: discord.Interaction):
        entries = await database.list_timetable(interaction.user.id)
        if not entries:
            await interaction.response.send_message("Your timetable is empty.", ephemeral=True)
            return

        lines = [
            f"**#{e['id']}** — {DAY_NAMES[e['day_of_week']].title()} {e['start_time']} — {e['title']}"
            for e in entries
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @timetable_group.command(name="delete", description="Delete a timetable entry by ID")
    async def delete(self, interaction: discord.Interaction, entry_id: int):
        success = await database.delete_timetable_entry(interaction.user.id, entry_id)
        if success:
            await interaction.response.send_message(f"🗑️ Deleted entry #{entry_id}.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Couldn't find that entry (check the ID with /timetable list).", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Timetable(bot))
