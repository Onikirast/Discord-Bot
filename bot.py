"""
bot.py

Entry point. Loads config, initialises the database, registers cogs,
starts the background scheduler loop, and syncs slash commands.
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

import database
import dcu_api
from scheduler_loop import setup_scheduler

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

intents = discord.Intents.default()
intents.message_content = False  # not needed; we use slash commands only

INITIAL_COGS = ["cogs.reminders", "cogs.timetable", "cogs.dcu", "cogs.grades"]


class DiscordBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Runs exactly once, after login but before connecting to the
        # gateway -- the correct place for one-time startup work like
        # loading cogs and syncing the command tree. Doing this in
        # on_ready instead would re-run it on every reconnect.
        await database.init_db()

        for cog in INITIAL_COGS:
            await self.load_extension(cog)

        setup_scheduler(self)

        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
        except Exception:
            logger.exception("Failed to sync slash commands")

    async def close(self) -> None:
        # Runs on shutdown (Ctrl+C, or bot.close()). Releases the shared
        # DB connection and HTTP session cleanly instead of leaving them
        # to be garbage-collected, which would otherwise print an
        # "Unclosed client session" warning for the aiohttp session.
        await database.close_db()
        await dcu_api.close_session()
        await super().close()


bot = DiscordBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (id: {bot.user.id})")


async def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN not set. Copy .env.example to .env and add your bot token."
        )

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
