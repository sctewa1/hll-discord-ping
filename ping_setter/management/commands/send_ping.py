import os
import logging
import requests
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from django.core.management.base import BaseCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))
API_BASE_URL = os.getenv('API_BASE_URL')
API_BEARER_TOKEN = os.getenv('API_BEARER_TOKEN')

# Logging
logging.basicConfig(filename='logs/discord_bot.log', level=logging.INFO)
logger = logging.getLogger(__name__)

# Discord bot setup
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()
tree = client.tree

HEADERS = {
    "Authorization": f"Bearer {API_BEARER_TOKEN}",
    "Content-Type": "application/json"
}

# API helper functions
def get_max_ping_autokick() -> int | None:
    try:
        response = requests.get(f"{API_BASE_URL}/api/get_server_settings", headers=HEADERS)
        response.raise_for_status()
        return response.json().get("result", {}).get("max_ping_autokick")
    except Exception as e:
        logger.error(f"Failed to fetch max ping: {e}")
        return None

def set_max_ping_autokick(ping: int) -> bool:
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/set_max_ping_autokick",
            headers=HEADERS,
            json={"max_ms": ping}
        )
        return response.ok
    except Exception as e:
        logger.error(f"Failed to set max ping: {e}")
        return False

def get_recent_bans(limit=5):
    try:
        response = requests.get(f"{API_BASE_URL}/api/get_bans", headers=HEADERS)
        response.raise_for_status()
        bans = response.json().get("result", [])
        filtered = [b for b in bans if b.get("type") == "temp" and b.get("player_id")]
        return list(reversed(filtered[-limit:]))
    except Exception as e:
        logger.error(f"Failed to fetch bans: {e}")
        return []

def get_player_name(player_id: str) -> str:
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/get_player_profile",
            headers=HEADERS,
            params={"player_id": player_id},
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        names = result.get("names", [])

        if names:
            names.sort(key=lambda n: n.get("last_seen", ""), reverse=True)
            return names[0].get("name", "Unknown")
        return "Unknown"
    except Exception as e:
        logger.error(f"Failed to fetch player profile: {e}")
        return "Unknown"

def unban_player(player_id: str) -> bool:
    try:
        response = requests.post(f"{API_BASE_URL}/api/unban", headers=HEADERS, json={"player_id": player_id})
        return response.ok
    except Exception as e:
        logger.error(f"Failed to unban player: {e}")
        return False

# Discord bot events and commands
@client.event
async def on_ready():
    await tree.sync()
    logger.info("🔔 Bot has started and is now online!")
    print("🔔 Bot has started and is now online!")
    # Send a message to the designated channel to announce the bot is online
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🟢 Bot is now online and ready to go!")

@tree.command(name="curping", description="Show current max ping kick setting")
async def curping(interaction: discord.Interaction):
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message(f"📡 Current max ping autokick is set to: `{ping}` ms.")
    else:
        await interaction.response.send_message("⚠️ Could not fetch the current max ping value.")

@tree.command(name="setping", description="Set new max ping kick value")
@app_commands.describe(ping="The new max ping value (in ms)")
async def setping(interaction: discord.Interaction, ping: int):
    if set_max_ping_autokick(ping):
        await interaction.response.send_message(f"✅ Max ping autokick set to `{ping}` ms.")
    else:
        await interaction.response.send_message("⚠️ Failed to set max ping autokick.")

@tree.command(name="bans", description="List last 5 bans")
async def bans(interaction: discord.Interaction):
    data = get_recent_bans()
    if not data:
        await interaction.response.send_message("⚠️ No bans found.")
        return

    msg = "**Last 5 Bans:**\n"
    for idx, ban in enumerate(data, 1):
        name = get_player_name(ban.get("player_id"))
        msg += f"`{idx}` - {name} (ID: `{ban.get('player_id')}`)\n"

    await interaction.response.send_message(msg)

@tree.command(name="unban", description="Unban player by ban number from the last /bans list")
@app_commands.describe(index="Ban number from the /bans list (1-5)")
async def unban(interaction: discord.Interaction, index: int):
    data = get_recent_bans()
    if not data:
        await interaction.response.send_message("⚠️ No bans to unban.")
        return

    if 1 <= index <= len(data):
        player_id = data[index - 1]["player_id"]
        if unban_player(player_id):
            name = get_player_name(player_id)
            await interaction.response.send_message(f"✅ Unbanned `{name}` (ID: `{player_id}`)")
        else:
            await interaction.response.send_message("❌ Failed to unban player.")
    else:
        await interaction.response.send_message("⚠️ Invalid ban index.")

@tree.command(name="online", description="Check if bot is online and API is reachable")
async def online(interaction: discord.Interaction):
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message("✅ Bot is online and API is reachable.")
    else:
        await interaction.response.send_message("⚠️ Bot is online but failed to reach API.")

@tree.command(name="help", description="Show help message")
async def help_command(interaction: discord.Interaction):
    msg = (
        "📘 **Getting Started:**\n"
        "Welcome to the HLL command tool!\n\n"
        "📜 **List of Commands:**\n"
        "/bans - Show recent bans\n"
        "/unban - Unban a player from recent bans\n"
        "/curping - Show current max ping kick\n"
        "/setping - Set max ping kick\n"
        "/online - Check if bot and API are running\n"
        "/help - Show this help message"
    )
    await interaction.response.send_message(msg)

# Django command entry point
class Command(BaseCommand):
    help = "Starts the Discord bot"

    def handle(self, *args, **options):
        # Scheduled ping update tasks
        @scheduler.scheduled_job(CronTrigger(hour=0, minute=1))
        async def set_ping_to_500():
            if set_max_ping_autokick(500):
                logger.info("Scheduled: Set max ping to 500ms 🕐 (00:01)")
                channel = client.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send("🕐 Max ping autokick set to `500` ms (Scheduled 00:01)")
            else:
                logger.warning("Scheduled: Failed to set max ping to 500ms")

        @scheduler.scheduled_job(CronTrigger(hour=15, minute=0))
        async def set_ping_to_320():
            if set_max_ping_autokick(320):
                logger.info("Scheduled: Set max ping to 320ms 🕒 (15:00)")
                channel = client.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send("🕒 Max ping autokick set to `320` ms (Scheduled 15:00)")
            else:
                logger.warning("Scheduled: Failed to set max ping to 320ms")

        client.run(BOT_TOKEN)
