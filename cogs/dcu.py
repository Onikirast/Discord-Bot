"""
cogs/dcu.py

Lets a user link their DCU programme (course) and view their real,
official DCU class schedule -- pulled live from Redbrick's TimetableSync
API rather than manually entered. Distinct from the generic /timetable
cog, which is for user-defined recurring events (gym, personal habits, etc).
"""

import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import database
import dcu_api

logger = logging.getLogger("dcu_cog")

DUBLIN_TZ = ZoneInfo("Europe/Dublin")


def _parse_api_datetime(value: str) -> datetime.datetime:
    """Parse a datetime string from the TimetableSync API safely.

    Two things `datetime.fromisoformat` can't be trusted with here:
    - A trailing 'Z' (UTC designator) isn't accepted by fromisoformat on
      Python < 3.11.
    - If the API ever returns a naive datetime (no offset), calling
      .astimezone() on it assumes the *host machine's* local timezone,
      not UTC -- which would silently produce wrong times if the bot
      happens to run on a non-UTC server. We explicitly assume UTC
      instead, since that's what the underlying event data is stored as.
    """
    value = value.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


DCU_BLUE = 0x00205B  # DCU's brand navy


def _build_day_embed(course_name: str, day_label: str, day_events: list[dict]) -> discord.Embed:
    """Build one Embed for a single day's classes.

    Events sharing an identical start/end time (e.g. two elective options
    running in parallel) are grouped into a single field, one line each,
    rather than merged into a single combined line -- so each module keeps
    its own location rather than accidentally borrowing another module's.
    """
    embed = discord.Embed(
        title=f"📅 {day_label}",
        color=DCU_BLUE,
    )
    embed.set_footer(text=f"DCU Timetable • {course_name}")

    # Group by exact (start, end) so simultaneous classes share one field
    slots: dict[tuple[str, str], list[dict]] = {}
    for event in day_events:
        start = _parse_api_datetime(event["start"]).astimezone(DUBLIN_TZ)
        end = _parse_api_datetime(event["end"]).astimezone(DUBLIN_TZ)
        key = (start.strftime("%H:%M"), end.strftime("%H:%M"))
        slots.setdefault(key, []).append(event)

    for (start_str, end_str), slot_events in sorted(slots.items()):
        lines = []
        for event in slot_events:
            summary = event.get("extras", {}).get("summary") or event.get("name", "Class")
            location = event.get("extras", {}).get("location") or "TBD"
            lines.append(f"**{summary}**\n📍 {location}")
        embed.add_field(
            name=f"🕐 {start_str} – {end_str}",
            value="\n\n".join(lines),
            inline=False,
        )

    return embed


class DCUTimetable(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    dcu_group = app_commands.Group(name="dcu", description="DCU official timetable integration")

    @dcu_group.command(name="search", description="Search for your DCU programme/course")
    @app_commands.describe(query="e.g. 'Computer Science' or a course code like 'COMSCI1'")
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            results = await dcu_api.search_category("course", query)
        except (dcu_api.DCUAPIError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.exception("DCU search failed")
            await interaction.followup.send(
                f"⚠️ Couldn't reach the DCU timetable API (it may be slow or down): {e}"
            )
            return

        if not results:
            await interaction.followup.send("No matching programmes found. Try a different search.")
            return

        lines = [f"**{r['name']}** — `{r['identity']}`" for r in results[:10]]
        await interaction.followup.send(
            "Matching programmes:\n" + "\n".join(lines) +
            "\n\nUse `/dcu link <name>` with the exact name shown above to link it."
        )

    @dcu_group.command(name="link", description="Link your DCU programme to your account")
    @app_commands.describe(
        identity="Start typing a course code (e.g. COMSCI2) and pick from the dropdown -- easiest option",
        name="Alternative: full programme name if not using the identity dropdown, e.g. 'COMSCI1 (Computer Science-1)'",
    )
    async def link(self, interaction: discord.Interaction, identity: str = None, name: str = None):
        await interaction.response.defer(ephemeral=True)

        if not name and not identity:
            await interaction.followup.send("Provide either `name` or `identity` from `/dcu search`.")
            return

        # Linking by identity is the most robust path -- no name-matching
        # involved at all, so it can't be tripped up by spacing/punctuation
        # differences like "COMSCI3(Computer Science-3)" vs the real
        # "COMSCI3 (Computer Science-3)".
        if identity:
            try:
                match = await dcu_api.get_category_item_by_identity("course", identity)
            except (dcu_api.DCUAPIError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.exception("DCU link identity lookup failed")
                await interaction.followup.send(
                    f"⚠️ Couldn't reach the DCU timetable API (it may be slow or down): {e}"
                )
                return
            if not match:
                await interaction.followup.send(
                    f"No programme found with identity `{identity}`. "
                    "Double-check it was copied exactly from `/dcu search`."
                )
                return
        else:
            try:
                results = await dcu_api.search_category("course", name)
            except (dcu_api.DCUAPIError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.exception("DCU link search failed")
                await interaction.followup.send(
                    f"⚠️ Couldn't reach the DCU timetable API (it may be slow or down): {e}"
                )
                return

            def _normalize(s: str) -> str:
                # Collapses repeated/missing whitespace and case differences,
                # e.g. "COMSCI3(Computer Science-3)" vs "COMSCI3 (Computer Science-3)".
                return " ".join(s.split()).lower().replace(" (", "(")

            exact = [r for r in results if _normalize(r["name"]) == _normalize(name)]
            if not exact:
                if results:
                    options = ", ".join(r["name"] for r in results[:5])
                    await interaction.followup.send(
                        f"No exact match for '{name}'. Did you mean: {options}?\n"
                        "Tip: if two results share a code (e.g. two 'COMSCI3' entries), "
                        "use `/dcu link identity:<uuid>` instead to avoid ambiguity."
                    )
                else:
                    await interaction.followup.send(f"No programme found matching '{name}'.")
                return
            if len(exact) > 1:
                options = "\n".join(f"{r['name']} — `{r['identity']}`" for r in exact)
                await interaction.followup.send(
                    f"Multiple programmes share that name. Use `/dcu link identity:<uuid>` "
                    f"with one of these:\n{options}"
                )
                return
            match = exact[0]
        await database.set_dcu_link(
            user_id=interaction.user.id,
            course_name=match["name"],
            course_description=None,
            course_identity=str(match["identity"]),
        )
        await interaction.followup.send(f"✅ Linked your account to **{match['name']}**.")

    @link.autocomplete("identity")
    async def link_identity_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Populates the dropdown as the user types, e.g. typing 'COMSCI2'
        shows 'COMSCI2 (Computer Science-2)' to select -- the value stored
        is the identity (UUID), not the display text, so selecting a
        suggestion is always unambiguous even when two programmes share
        a code (like the two 'COMSCI3' entries).
        """
        if len(current) < 2:
            return []
        try:
            results = await dcu_api.search_category("course", current)
        except (dcu_api.DCUAPIError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("DCU autocomplete search failed")
            return []
        # Discord caps autocomplete choices at 25 and each choice's name at 100 chars.
        return [
            app_commands.Choice(name=r["name"][:100], value=str(r["identity"]))
            for r in results[:25]
        ]

    @dcu_group.command(name="unlink", description="Remove your linked DCU programme")
    async def unlink(self, interaction: discord.Interaction):
        removed = await database.delete_dcu_link(interaction.user.id)
        msg = "🗑️ Unlinked your DCU programme." if removed else "You don't have a linked programme."
        await interaction.response.send_message(msg, ephemeral=True)

    @dcu_group.command(name="today", description="Show today's DCU classes for your linked programme")
    @app_commands.describe(date="Optional: check a specific date instead of today, format YYYY-MM-DD")
    async def today(self, interaction: discord.Interaction, date: str = None):
        await self._show_range(interaction, days=0, date_str=date)

    @dcu_group.command(name="week", description="Show a week's DCU classes for your linked programme")
    @app_commands.describe(date="Optional: any date in the target week, format YYYY-MM-DD (defaults to this week)")
    async def week(self, interaction: discord.Interaction, date: str = None):
        await self._show_range(interaction, days=6, date_str=date)

    async def _show_range(self, interaction: discord.Interaction, days: int, date_str: str | None = None):
        await interaction.response.defer(ephemeral=True)

        link = await database.get_dcu_link(interaction.user.id)
        if not link:
            await interaction.followup.send(
                "You haven't linked a DCU programme yet. Use `/dcu search` then `/dcu link`."
            )
            return

        if date_str:
            try:
                anchor_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                await interaction.followup.send(
                    f"Couldn't parse '{date_str}' -- use YYYY-MM-DD, e.g. 2026-03-16."
                )
                return
            now = datetime.datetime.combine(anchor_date, datetime.time(0, 0), tzinfo=DUBLIN_TZ)
        else:
            now = datetime.datetime.now(DUBLIN_TZ)

        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=days + 1)

        try:
            events = await dcu_api.get_events(
                category_type="course", item_identity=link["course_identity"],
                start=start, end=end,
            )
        except (dcu_api.DCUAPIError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.exception("DCU get_events failed")
            await interaction.followup.send(
                f"⚠️ Couldn't fetch your timetable (the DCU API may be slow or down): {e}"
            )
            return

        if not events:
            await interaction.followup.send(
                f"No classes scheduled for **{link['course_name']}** in this period. 🎉"
            )
            return

        events.sort(key=lambda e: e["start"])

        # Group by day for readability
        by_day: dict[str, list[dict]] = {}
        for event in events:
            day_key = _parse_api_datetime(event["start"]).astimezone(DUBLIN_TZ).strftime("%A %d %b")
            by_day.setdefault(day_key, []).append(event)

        embeds = [
            _build_day_embed(link["course_name"], day_label, day_events)
            for day_label, day_events in by_day.items()
        ]

        # Discord allows up to 10 embeds per message, and a week is at
        # most 7 days -- but batch regardless, as a safety net against
        # unexpectedly dense schedules. Every batch goes through
        # followup.send (not channel.send) so it stays ephemeral,
        # consistent with the rest of this cog's commands.
        for i in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[i:i + 10])

    @dcu_group.command(name="calendar", description="Get a link to generate a subscribable calendar")
    async def calendar(self, interaction: discord.Interaction):
        link = await database.get_dcu_link(interaction.user.id)
        if not link:
            await interaction.response.send_message(
                "You haven't linked a DCU programme yet. Use `/dcu search` then `/dcu link`.",
                ephemeral=True,
            )
            return

        url = dcu_api.calendar_subscription_url("course", link["course_identity"])
        await interaction.response.send_message(
            f"📅 For **{link['course_name']}**, generate a subscribable Google/Apple/Outlook "
            f"calendar link here: {url}\n\n"
            f"Search for **{link['course_name']}** on that page under \"Courses\" to get your link.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Guaranteed fallback for any error not already caught inside a command.

        Without this, an unhandled exception after interaction.response.defer()
        leaves Discord showing "thinking..." forever, since no follow-up ever
        gets sent (this is exactly what happened before the network-error
        handling above was added). This makes sure that can't happen again for
        *any* future bug in this cog, not just the ones we've already caught.
        """
        logger.exception("Unhandled error in /dcu command", exc_info=error)
        message = "⚠️ Something went wrong talking to the DCU timetable service. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            # Interaction token may have expired (>15 min) -- nothing more we can do.
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(DCUTimetable(bot))
