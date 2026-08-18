"""
cogs/grades.py

A self-reported grade tracker: the user manually logs their own grade per
module (as they received it themselves), and the bot computes a
credit-weighted average plus the corresponding Irish honours degree
classification band.

Deliberately NOT connected to any DCU login or official records system --
see the project's DEBUGGING_LOG.md / conversation history for why that
was ruled out (credential storage risk, MFA incompatibility, and likely
against DCU's acceptable-use policy). This only ever stores what the user
types in themselves.

The `module` field reuses the same live DCU module search used by
/dcu link's autocomplete, purely so users don't have to remember or
retype exact module codes -- it does not pull grade data from anywhere.
"""

import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import database
import dcu_api

logger = logging.getLogger("grades_cog")


def _classification(average: float) -> str:
    """Map a percentage average to the standard Irish honours degree bands."""
    if average >= 70:
        return "First Class Honours (1.1)"
    if average >= 60:
        return "Second Class Honours, Grade 1 (2.1)"
    if average >= 50:
        return "Second Class Honours, Grade 2 (2.2)"
    if average >= 40:
        return "Third Class Honours"
    return "Below honours threshold"


def _weighted_average(grades: list[dict]) -> float | None:
    total_credits = sum(g["credits"] for g in grades)
    if total_credits == 0:
        return None
    weighted_sum = sum(g["grade"] * g["credits"] for g in grades)
    return weighted_sum / total_credits


class Grades(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    grades_group = app_commands.Group(name="grades", description="Track your own module grades")

    @grades_group.command(name="add", description="Log a grade you received for a module")
    @app_commands.describe(
        module="Start typing a module code (e.g. CSC1018) and pick from the dropdown",
        grade="Your percentage mark for this module, 0-100",
        credits="ECTS credits for this module (default 5)",
        semester="Optional label, e.g. 'Year 2 Semester 2', to group results later",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        module: str,
        grade: app_commands.Range[float, 0.0, 100.0],
        credits: app_commands.Range[float, 0.5, 60.0] = 5.0,
        semester: str = None,
    ):
        grade_id = await database.add_grade(
            user_id=interaction.user.id,
            module_name=module,
            grade=grade,
            credits=credits,
            semester=semester,
        )
        semester_note = f" ({semester})" if semester else ""
        await interaction.response.send_message(
            f"✅ Logged #{grade_id}: **{module}** — {grade}% ({credits} credits){semester_note}",
            ephemeral=True,
        )

    @add.autocomplete("module")
    async def module_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Same pattern as /dcu link's identity autocomplete, but here the
        *name itself* is stored (not an identity/UUID) -- this is just a
        convenience so users don't have to remember exact module codes,
        not a link to any live record.
        """
        if len(current) < 2:
            return []
        try:
            results = await dcu_api.search_category("module", current)
        except (dcu_api.DCUAPIError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("DCU module autocomplete search failed")
            return []
        return [
            app_commands.Choice(name=r["name"][:100], value=r["name"][:100])
            for r in results[:25]
        ]

    @grades_group.command(name="list", description="List your logged grades")
    @app_commands.describe(semester="Optional: only show grades logged under this semester label")
    async def list_grades(self, interaction: discord.Interaction, semester: str = None):
        grades = await database.list_grades(interaction.user.id, semester=semester)
        if not grades:
            await interaction.response.send_message("No grades logged yet.", ephemeral=True)
            return

        lines = [
            f"**#{g['id']}** — {g['module_name']} — {g['grade']}% ({g['credits']} credits)"
            + (f" [{g['semester']}]" if g["semester"] else "")
            for g in grades
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @grades_group.command(name="gpa", description="Show your credit-weighted average and classification")
    @app_commands.describe(semester="Optional: only include grades logged under this semester label")
    async def gpa(self, interaction: discord.Interaction, semester: str = None):
        grades = await database.list_grades(interaction.user.id, semester=semester)
        if not grades:
            await interaction.response.send_message("No grades logged yet.", ephemeral=True)
            return

        average = _weighted_average(grades)
        total_credits = sum(g["credits"] for g in grades)
        classification = _classification(average)

        embed = discord.Embed(
            title="📊 Grade Summary" + (f" — {semester}" if semester else ""),
            color=0x00205B,
        )
        embed.add_field(name="Weighted Average", value=f"{average:.2f}%", inline=True)
        embed.add_field(name="Classification", value=classification, inline=True)
        embed.add_field(name="Total Credits", value=f"{total_credits:g}", inline=True)
        embed.set_footer(text=f"Based on {len(grades)} logged module(s) — self-reported, not official")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @grades_group.command(name="delete", description="Delete a logged grade by ID")
    async def delete(self, interaction: discord.Interaction, grade_id: int):
        removed = await database.delete_grade(interaction.user.id, grade_id)
        msg = f"🗑️ Deleted grade #{grade_id}." if removed else "Couldn't find that grade (check the ID with `/grades list`)."
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Grades(bot))
