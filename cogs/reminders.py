"""
cogs/reminders.py

Slash commands for creating, listing, and deleting reminders.
"""

import discord
from discord import app_commands
from discord.ext import commands

import database
from time_utils import parse_when, TimeParseError


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    reminder_group = app_commands.Group(name="remind", description="Manage reminders")

    @reminder_group.command(name="add", description="Set a reminder")
    @app_commands.describe(
        when="e.g. '30m', '2h', '1d', or '2026-07-25 14:30'",
        message="What should I remind you about?",
        recurrence="How often this reminder repeats",
    )
    @app_commands.choices(recurrence=[
        app_commands.Choice(name="Once", value="once"),
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
    ])
    async def add(self, interaction: discord.Interaction, when: str, message: str,
                  recurrence: app_commands.Choice[str] = None):
        try:
            remind_at = parse_when(when)
        except TimeParseError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        recurrence_value = recurrence.value if recurrence else "once"

        reminder_id = await database.add_reminder(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            message=message,
            remind_at=remind_at,
            recurrence=recurrence_value,
        )

        await interaction.response.send_message(
            f"✅ Reminder #{reminder_id} set for **{remind_at.strftime('%Y-%m-%d %H:%M UTC')}** "
            f"({recurrence_value})."
        )

    @reminder_group.command(name="list", description="List your pending reminders")
    async def list_reminders(self, interaction: discord.Interaction):
        reminders = await database.list_reminders(interaction.user.id)
        if not reminders:
            await interaction.response.send_message("You have no pending reminders.", ephemeral=True)
            return

        lines = [
            f"**#{r['id']}** — {r['message']} — {r['remind_at']} UTC ({r['recurrence']})"
            for r in reminders
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @reminder_group.command(name="delete", description="Delete a reminder by ID")
    async def delete(self, interaction: discord.Interaction, reminder_id: int):
        success = await database.delete_reminder(interaction.user.id, reminder_id)
        if success:
            await interaction.response.send_message(f"🗑️ Deleted reminder #{reminder_id}.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Couldn't find that reminder (check the ID with /remind list).", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
