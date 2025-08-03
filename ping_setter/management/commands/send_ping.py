import json5
import json
import os
import logging
import subprocess
import requests
import aiohttp
import calendar
from pathlib import Path
from discord import Embed

import discord
import asyncio
from discord.ext import commands
from discord import app_commands
from django.core.management.base import BaseCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from .logging_config import setup_logging
from sqlalchemy import text, create_engine

# Absolute path to config.jsonc inside the container.
CONFIG_PATH = "/opt/ping_setter_hll/config.jsonc"

# Logging setup
logger = setup_logging()

# Load config from config.jsonc
def load_config():
    """
    Try loading config from several known locations, falling back to defaults.
    """
    paths = [
        CONFIG_PATH,
        os.path.join(os.getcwd(), "config.jsonc"),
    ]
    for p in paths:
        try:
            with open(p, "r") as f:
                logger.info(f"Loaded config from: {p}")
                return json5.load(f)
        except FileNotFoundError:
            logger.debug(f"Config not found at: {p}")
    logger.error("Config file not found in any of the expected paths; using default settings.")
    return {
        "DISCORD_TOKEN": "",
        "CHANNEL_ID": None,
	"CHANNEL_ID_STATS": None,
        "API_BASE_URL": "",
        "API_BEARER_TOKEN": "",
        "TIMEZONE": "Australia/Sydney",
        "SCHEDULED_JOB_1_TIME": "0009",
        "SCHEDULED_JOB_1_PING": 500,
        "SCHEDULED_JOB_2_TIME": "1500",
        "SCHEDULED_JOB_2_PING": 320,
        "LOG_DIR": "/opt/ping_setter_hll/logs"
    }
# Load configuration
config = load_config()

# Required settings
DISCORD_TOKEN    = config.get("DISCORD_TOKEN")
CHANNEL_ID       = config.get("CHANNEL_ID")
CHANNEL_ID_STATS = config.get("CHANNEL_ID_stats")
API_BASE_URL     = config.get("API_BASE_URL")
API_BEARER_TOKEN = config.get("API_BEARER_TOKEN")
DB_URL           = config.get("DB_URL")

if not DISCORD_TOKEN or CHANNEL_ID is None or not API_BASE_URL or not API_BEARER_TOKEN:
    raise ValueError("Essential configuration missing in config.jsonc")

# Prepare headers for API calls
HEADERS = {
    "Authorization": f"Bearer {API_BEARER_TOKEN}",
    "Content-Type": "application/json"
}

# Scheduler timezone
tz_name = config.get("TIMEZONE", "Australia/Sydney")
try:
    tz = timezone(tz_name)
except Exception:
    logger.warning(f"Invalid timezone '{tz_name}', defaulting to Australia/Sydney")
    tz = timezone("Australia/Sydney")
scheduler = AsyncIOScheduler(timezone=tz)

# SQLAlchemy DB engine
engine = create_engine(DB_URL)

# Discord bot setup (only slash commands)
intents = discord.Intents.default()
intents.message_content = False
client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

# --- API helper functions ---
# Cache map data
cached_maps = {}

def fetch_and_cache_maps() -> bool:
    """
    Fetches map data from the API and caches warfare maps, excluding maps with 'Night' in the name.
    
    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        r = requests.get(f"{API_BASE_URL}/api/get_maps", headers=HEADERS)
        r.raise_for_status()
        maps = r.json().get("result", [])

        # Filter out maps that have 'Night' in the pretty_name
        warfare_maps = {
            map_data["id"]: map_data["pretty_name"]
            for map_data in maps
            if map_data.get("game_mode") == "warfare"
            and map_data.get("id")
            and map_data.get("pretty_name")
            and "night" not in map_data["pretty_name"].lower()  # Exclude maps with 'Night' in the name
        }

        cached_maps.clear()
        cached_maps.update(warfare_maps)
        logger.info(f"Successfully cached {len(warfare_maps)} warfare maps.")
        return True
    except Exception as e:
        logger.error(f"Failed to fetch and cache maps: {e}")
        return False

def get_max_ping_autokick() -> int | None:
    try:
        r = requests.get(f"{API_BASE_URL}/api/get_server_settings", headers=HEADERS)
        r.raise_for_status()
        return r.json().get("result", {}).get("max_ping_autokick")
    except Exception as e:
        logger.error(f"Failed to fetch max ping: {e}")
        return None

def set_max_ping_autokick(ping: int) -> bool:
    try:
        r = requests.post(
            f"{API_BASE_URL}/api/set_max_ping_autokick",
            headers=HEADERS,
            json={"max_ms": ping}
        )
        return r.ok
    except Exception as e:
        logger.error(f"Failed to set max ping: {e}")
        return False

def get_recent_bans(limit=5):
    try:
        r = requests.get(f"{API_BASE_URL}/api/get_bans", headers=HEADERS)
        r.raise_for_status()
        bans = r.json().get("result", [])
        return list(reversed([b for b in bans if b.get("type") == "temp" and b.get("player_id")]))[:limit]
    except Exception as e:
        logger.error(f"Failed to fetch bans: {e}")
        return []

def get_player_name(player_id: str) -> str:
    try:
        r = requests.get(
            f"{API_BASE_URL}/api/get_player_profile",
            headers=HEADERS,
            params={"player_id": player_id}
        )
        r.raise_for_status()
        names = r.json().get("result", {}).get("names", [])
        if names:
            names.sort(key=lambda n: n.get("last_seen", ""), reverse=True)
            return names[0].get("name", "Unknown")
        return "Unknown"
    except Exception as e:
        logger.error(f"Failed to fetch player profile: {e}")
        return "Unknown"

def unban_player(player_id: str) -> bool:
    try:
        r = requests.post(f"{API_BASE_URL}/api/unban", headers=HEADERS, json={"player_id": player_id})
        return r.ok
    except Exception as e:
        logger.error(f"Failed to unban player: {e}")
        return False

# --- Scheduled job function ---
async def scheduled_ping_job(job_id: str, time_str: str, ping: int):
    h, m = time_str[:2], time_str[2:]
    if set_max_ping_autokick(ping):
        logger.info(f"[{job_id}] Set max ping to {ping}ms at {h}:{m}")
        ch = client.get_channel(CHANNEL_ID)
        if ch:
            await ch.send(f"🔄 [{job_id}] Max ping autokick set to `{ping}` ms (Scheduled {h}:{m})")
    else:
        logger.warning(f"[{job_id}] Failed to set max ping {ping}")   

# --- Reschedule helper ---
def reschedule_job(job_id: str, time_str: str, ping: int):
    try:
        hour, minute = int(time_str[:2]), int(time_str[2:])
        scheduler.add_job(
            scheduled_ping_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=job_id,
            args=[job_id, time_str, ping],
            replace_existing=True
        )

        job_config_map = {
            "set_ping_job_1": ("SCHEDULED_JOB_1_TIME", "SCHEDULED_JOB_1_PING"),
            "set_ping_job_2": ("SCHEDULED_JOB_2_TIME", "SCHEDULED_JOB_2_PING")
        }
        job_time_key, job_ping_key = job_config_map.get(job_id, (None, None))
        if job_time_key and job_ping_key:
            config[job_time_key] = time_str
            config[job_ping_key] = ping
            with open(CONFIG_PATH, "w") as f:
                json5.dump(config, f, indent=4)
            logger.info(f"[{job_id}] Rescheduled to {time_str} with ping {ping}")
        else:
            logger.warning(f"Job ID '{job_id}' is not recognized for config update")

    except Exception as e:
        logger.error(f"Failed to reschedule {job_id}: {e}")

# --- Slash commands ---
from datetime import datetime
import discord.ui

@tree.command(name="banplayer", description="Ban a live player by name prefix")
@app_commands.describe(name_prefix="Start of the player name")
async def banplayer(interaction: discord.Interaction, name_prefix: str):
    logger.info(f"[/banplayer] Requested by {interaction.user} (ID: {interaction.user.id}) - prefix: {name_prefix}")
    await interaction.response.defer(ephemeral=True)

    try:
        r = requests.get(f"{API_BASE_URL}/api/get_live_scoreboard", headers=HEADERS)
        r.raise_for_status()
        stats = r.json().get("result", {}).get("stats", [])
    except Exception as e:
        logger.error(f"Failed to fetch scoreboard: {e}")
        await interaction.followup.send("❌ Error fetching live scoreboard.")
        return

    filtered = [
        (p["player"], p["player_id"]) for p in stats
        if p.get("player", "").lower().startswith(name_prefix.lower())
    ]

    if not filtered:
        await interaction.followup.send("⚠️ No players found with that prefix.")
        return

    if len(filtered) > 25:
        await interaction.followup.send("⚠️ Too many matches. Please narrow your prefix.")
        return

    class PlayerDropdown(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label=name, value=pid) for name, pid in filtered
            ]
            super().__init__(placeholder="Select a player to ban", min_values=1, max_values=1, options=options)

        async def callback(self, interaction_select: discord.Interaction):
            player_id = self.values[0]
            player_name = next((n for n, pid in filtered if pid == player_id), "Unknown")

            class ReasonModal(discord.ui.Modal, title=f"Ban Reason for {player_name}"):
                reason = discord.ui.TextInput(label="Reason", placeholder="Enter reason for ban", required=True)

                async def on_submit(self, modal_interaction: discord.Interaction):
                    payload = {
                        "player_id": player_id,
                        "blacklist_id": 0,
                        "reason": self.reason.value,
                        "expires_at": "2033-01-01T00:00:00",
                        "admin_name": "discordBot"
                    }

                    try:
                        r = requests.post(f"{API_BASE_URL}/api/add_blacklist_record", headers=HEADERS, json=payload)
                        r.raise_for_status()

                        await modal_interaction.response.send_message(
                            f"✅ Successfully banned `{player_name}` for reason: '{self.reason.value}'.",
                            ephemeral=True
                        )

                        logger.info(f"{interaction.user.name} (ID: {interaction.user.id}) banned {player_name} (ID: {player_id}) for '{self.reason.value}'")

                        channel = await client.fetch_channel(CHANNEL_ID)
                        if channel:
                            await channel.send(f"👮 `{interaction.user.name}` banned `{player_name}` for reason: '{self.reason.value}'")

                    except Exception as e:
                        logger.error(f"Ban failed for player_id {player_id}: {e}")
                        await modal_interaction.response.send_message("❌ Failed to ban player.", ephemeral=True)

            await interaction_select.response.send_modal(ReasonModal())

    class PlayerView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.add_item(PlayerDropdown())

    await interaction.followup.send("Select the player to ban:", view=PlayerView())


@tree.command(name="curping", description="Show current max ping autokick")
async def curping(interaction: discord.Interaction):
    logger.info(f"[/curping] Requested by {interaction.user} (ID: {interaction.user.id})")
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message(f"📡 Current max ping autokick is `{ping}` ms.")
    else:
        await interaction.response.send_message("⚠️ Could not fetch current ping.")

@tree.command(name="setping", description="Set max ping autokick")
@app_commands.describe(ping="Ping in ms")
async def setping(interaction: discord.Interaction, ping: int):
    logger.info(f"[/setping] Requested by {interaction.user} (ID: {interaction.user.id}) with ping {ping}")
    if ping <= 0 or ping > 10000:
        return await interaction.response.send_message("⚠️ Ping must be between 1 and 10000 ms.")
    if set_max_ping_autokick(ping):
        await interaction.response.send_message(f"✅ Set max ping autokick to `{ping}` ms.")
    else:
        await interaction.response.send_message("❌ Failed to set ping.")

@tree.command(name="curscheduledtime", description="Show scheduled jobs and pings")
async def curscheduledtime(interaction: discord.Interaction):
    logger.info(f"[/curscheduledtime] Requested by {interaction.user} (ID: {interaction.user.id})")
    t1, p1 = config.get("SCHEDULED_JOB_1_TIME"), config.get("SCHEDULED_JOB_1_PING")
    t2, p2 = config.get("SCHEDULED_JOB_2_TIME"), config.get("SCHEDULED_JOB_2_PING")
    msg = (f"🕒 Job 1: {t1[:2]}:{t1[2:]} @ {p1}ms\n"
           f"🕒 Job 2: {t2[:2]}:{t2[2:]} @ {p2}ms")
    await interaction.response.send_message(msg)

@tree.command(name="setscheduledtime", description="Set scheduled job time and ping")
@app_commands.describe(job="Job number (1 or 2)", time="Time HHMM", ping="Ping in ms")
async def setscheduledtime(interaction: discord.Interaction, job: int, time: str, ping: int):
    logger.info(f"[/setscheduledtime] Requested by {interaction.user} (ID: {interaction.user.id}) - Job {job}, Time {time}, Ping {ping}")
    if not time.isdigit() or len(time) != 4:
        return await interaction.response.send_message("⚠️ Invalid time format. Use HHMM.")
    hour, minute = int(time[:2]), int(time[2:])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return await interaction.response.send_message("⚠️ Invalid time value.")
    if job == 1:
        reschedule_job("set_ping_job_1", time, ping)
        await interaction.response.send_message(f"✅ Job 1 rescheduled to `{hour:02d}:{minute:02d}` @ {ping}ms.")
    elif job == 2:
        reschedule_job("set_ping_job_2", time, ping)
        await interaction.response.send_message(f"✅ Job 2 rescheduled to `{hour:02d}:{minute:02d}` @ {ping}ms.")
    else:
        await interaction.response.send_message("⚠️ Invalid job number (1 or 2).")

@tree.command(name="bans", description="Show last 5 temp bans")
async def bans(interaction: discord.Interaction):
    logger.info(f"[/bans] Requested by {interaction.user} (ID: {interaction.user.id})")
    
    # Fetch recent bans
    data = get_recent_bans()
    
    # Ensure that data is in list format and filter only temp bans with valid player_id
    temp_bans = [
        b for b in data if b.get("type") == "temp" and b.get("player_id") is not None
    ]
    
    # Check if there are any temp bans
    if not temp_bans:
        return await interaction.response.send_message("⚠️ No temp bans found.")
    
    # Create list of temp bans (we only want the last 5)
    lines = []
    for i, b in enumerate(temp_bans[:5]):  # Only show the last 5 bans
        player_name = get_player_name(b['player_id'])  # Assuming this function works properly
        lines.append(f"`{i + 1}` - {player_name} (ID: `{b['player_id']}`)")

    # Send the list of temp bans
    await interaction.response.send_message("**Last 5 Temp Bans:**\n" + "\n".join(lines))

@tree.command(name="unban", description="Unban player by ban number from the last /bans list")
@app_commands.describe(index="Ban number from the /bans list (1-5)")
async def unban(interaction: discord.Interaction, index: int):
    logger.info(f"[/unban] Requested by {interaction.user} (ID: {interaction.user.id}), index: {index}")
 
    data = get_recent_bans()
    if not data:
        await interaction.response.send_message("⚠️ No bans to unban.")
        logger.info(f"User `{interaction.user.name}` attempted to unban a player, but no bans were found.")
        return

    if 1 <= index <= len(data):
        player_id = data[index - 1]["player_id"]
        name = get_player_name(player_id)
        success = unban_player(player_id)

        if success:
            await interaction.response.send_message(f"✅ Unbanned `{name}` (ID: `{player_id}`)")
            logger.info(f"User `{interaction.user.name}` successfully unbanned player `{name}` (ID: `{player_id}`)")
        else:
            await interaction.response.send_message("❌ Failed to unban player.")
            logger.error(f"User `{interaction.user.name}` failed to unban player `{name}` (ID: `{player_id}`).")
    else:
        await interaction.response.send_message("⚠️ Invalid ban index.")
        logger.warning(f"User `{interaction.user.name}` provided an invalid index `{index}` when attempting to unban a player.")


@tree.command(name="online", description="Check if bot and API are running")
async def online(interaction: discord.Interaction):
    logger.info(f"[/online] Requested by {interaction.user} (ID: {interaction.user.id})")
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message(f"🟢 Bot and API are online! Current max ping autokick: `{ping}` ms.")
    else:
        await interaction.response.send_message("🟢 Bot is online, but failed to reach API.")

@tree.command(name="help", description="Show this help message")
async def help_command(interaction: discord.Interaction):
    logger.info(f"[/help] Requested by {interaction.user} (ID: {interaction.user.id})")
    msg = (
        "📘 **Getting Started:**\n"
        "Welcome to the HLL command tool!\n\n"
        "📜 **List of Commands:**\n"
        "/banplayer - Ban a live player, input start of name hit enter, select player, then a reason to show the player this is for 8 YEARS\n"
	"/bantemp- Ban a live player, input start of name hit enter, select player, then how many HOURS and a reason to show the player\n"
        "/bans - Show recent bans\n"
        "/unban - Unban a player from recent bans\n"
        "/curping - Show current max ping autokick value\n"
        "/setping - Set max ping autokick value (in ms)\n"
        "/curscheduledtime - Show current scheduled job times and ping values\n"
        "/setscheduledtime <job> <time> <ping> - Set scheduled job time and ping\n"
        
	"/showvips - Display a paginated list of temporary VIPs and how long they have left\n"
        "/online - Check if bot and API are running\n\n"
        "/help - Show this help message"
    )
    await interaction.response.send_message(msg)



@tree.command(name="bantemp", description="Temporarily ban a live player by name prefix")
@app_commands.describe(name_prefix="Start of the player name")
async def bantemp(interaction: discord.Interaction, name_prefix: str):
    logger.info(f"[/bantemp] Requested by {interaction.user} (ID: {interaction.user.id}) - prefix: {name_prefix}")
    await interaction.response.defer(ephemeral=True)

    try:
        r = requests.get(f"{API_BASE_URL}/api/get_live_scoreboard", headers=HEADERS)
        r.raise_for_status()
        stats = r.json().get("result", {}).get("stats", [])
    except Exception as e:
        logger.error(f"Failed to fetch scoreboard: {e}")
        await interaction.followup.send("❌ Error fetching live scoreboard.")
        return

    filtered = [
        (p["player"], p["player_id"]) for p in stats
        if p.get("player", "").lower().startswith(name_prefix.lower())
    ]

    if not filtered:
        await interaction.followup.send("⚠️ No players found with that prefix.")
        return

    if len(filtered) > 25:
        await interaction.followup.send("⚠️ Too many matches. Please narrow your prefix.")
        return

    class PlayerDropdown(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label=name, value=pid) for name, pid in filtered
            ]
            super().__init__(placeholder="Select a player to temp-ban", min_values=1, max_values=1, options=options)

        async def callback(self, interaction_select: discord.Interaction):
            player_id = self.values[0]
            player_name = next((n for n, pid in filtered if pid == player_id), "Unknown")

            class DurationModal(discord.ui.Modal, title=f"Temp Ban for {player_name}"):
                reason = discord.ui.TextInput(label="Reason", placeholder="Enter reason", required=True)
                duration = discord.ui.TextInput(label="Duration (hours)", placeholder="e.g. 2", required=True)

                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        hours = int(str(self.duration.value).strip())
                        if hours <= 0 or hours > 720:
                            raise ValueError("Invalid hours")

                        payload = {
                            "player_id": player_id,
                            "player_name": player_name,
                            "duration_hours": hours,
                            "reason": self.reason.value,
                            "by": "discordBot"
                        }

                        r = requests.post(f"{API_BASE_URL}/api/temp_ban", headers=HEADERS, json=payload)
                        r.raise_for_status()

                        await modal_interaction.response.send_message(
                            f"⏳ Temporarily banned `{player_name}` for {hours}h. Reason: '{self.reason.value}'",
                            ephemeral=True
                        )

                        channel = await client.fetch_channel(CHANNEL_ID)
                        if channel:
                            await channel.send(f"⛔ `{interaction.user.name}` temp-banned `{player_name}` ({hours}h) for: '{self.reason.value}'")

                        logger.info(f"{interaction.user.name} temp-banned {player_name} ({player_id}) for {hours}h, reason: {self.reason.value}")
                    except Exception as e:
                        logger.error(f"Temp ban failed: {e}")
                        await modal_interaction.response.send_message("❌ Failed to temp-ban player.", ephemeral=True)

            await interaction_select.response.send_modal(DurationModal())

    class PlayerView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.add_item(PlayerDropdown())

    await interaction.followup.send("Select the player to temp-ban:", view=PlayerView())

@tree.command(name="showvips", description="Show all temporary VIPs by time remaining")
@app_commands.checks.cooldown(1, 3600.0)
async def show_vips(interaction: discord.Interaction):
    # Restrict to #general only
    if interaction.channel.id != CHANNEL_ID_VIPstats:
        await interaction.response.send_message(
            "⚠️ You can only run this command in #general.",
            ephemeral=True
        )
        return

    logger.info(f"[/showvips] Requested by {interaction.user} (ID: {interaction.user.id})")
    await interaction.response.defer(ephemeral=True)

    vip_url = f"{API_BASE_URL}/api/get_vip_ids"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(vip_url, headers=HEADERS) as resp:
                data = await resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch VIP data: {e}")
        return await interaction.followup.send("❌ Error fetching VIP data.", ephemeral=True)

    from datetime import timezone as dt_timezone
    now = datetime.now(dt_timezone.utc)
    vip_entries = []

    for entry in data.get("result", []):
        vip_exp = entry.get("vip_expiration")
        if vip_exp == "3000-01-01T00:00:00+00:00":
            continue  # skip permanent VIPs
        try:
            expires_at = datetime.fromisoformat(vip_exp)
            if expires_at > now:
                delta = expires_at - now
                name = entry["name"].replace(" - CRCON Seed VIP", "")
                vip_entries.append((name, delta))
        except Exception:
            continue

    if not vip_entries:
        await interaction.followup.send("⚠️ No temporary VIPs found.", ephemeral=True)
        return

    # Sort by longest remaining time
    vip_entries.sort(key=lambda x: x[1], reverse=True)

    def format_duration(delta):
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes = remainder // 60
        return f"{days}d {hours}h {minutes}m"

    def format_line(name, delta):
        return f"⏰ {name} → `{format_duration(delta)}`"

    pages = []
    per_page = 20
    for i in range(0, len(vip_entries), per_page):
        chunk = vip_entries[i:i+per_page]
        description = "\n".join(format_line(name, delta) for name, delta in chunk)
        embed = discord.Embed(
            title="🧾 Temporary VIPs (Longest to Shortest)",
            description=description,
            color=discord.Color.teal()
        )
        embed.set_footer(text=f"{i + 1}–{min(i + per_page, len(vip_entries))} of {len(vip_entries)}")
        pages.append(embed)

    # Log public message in #general
    await interaction.channel.send(f"👀 {interaction.user.display_name} reviewed VIPs")

    if len(pages) == 1:
        await interaction.followup.send(embed=pages[0], ephemeral=True)
    else:
        class Paginator(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.page = 0
                self.message = None  # Will hold the message object

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return True  # ✅ Allow anyone to interact

            @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
            async def back(self, interaction_: discord.Interaction, _):
                if self.page > 0:
                    self.page -= 1
                    await interaction_.response.edit_message(embed=pages[self.page], view=self)

            @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
            async def forward(self, interaction_: discord.Interaction, _):
                if self.page < len(pages) - 1:
                    self.page += 1
                    await interaction_.response.edit_message(embed=pages[self.page], view=self)

        view = Paginator()
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
        view.message = await interaction.original_response()


import calendar
from discord import Embed

@tree.command(name="playerstats", description="Show all-time stats for a player by name")
@app_commands.describe(player_name="All or part of the player's name")
async def playerstats(interaction: discord.Interaction, player_name: str):
    logger.info(f"[/playerstats] Requested by {interaction.user} (ID: {interaction.user.id}), search: {player_name}")

      # Restrict to stats channel only
    if interaction.channel.id != CHANNEL_ID_STATS:
        await interaction.response.send_message(
            "This command can only be used in the stats channel.", ephemeral=True
        )
        return

    await interaction.response.defer()  # not ephemeral

    # Search for players
    with engine.connect() as conn:
        query = text("""
            SELECT name, playersteamid_id
            FROM (
                SELECT DISTINCT ON (pn.playersteamid_id)
                    pn.playersteamid_id,
                    pn.name,
                    pn.last_seen
                FROM player_names pn
                WHERE pn.name ILIKE :search
                ORDER BY pn.playersteamid_id, pn.last_seen DESC
            ) sub
            ORDER BY sub.last_seen DESC
            LIMIT 20
        """)
        results = conn.execute(query, {"search": f"{player_name}%"}).fetchall()

    if not results:
        await interaction.followup.send("No matching players found.")
        return


    # Combine steam_id and name into value so we keep the exact selected name
    options = [
        discord.SelectOption(
            label=row.name[:100],
            value=f"{row.playersteamid_id}|{row.name[:100]}"
        )
        for row in results
    ]

    class PlayerSelect(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.select = discord.ui.Select(
                placeholder="Select a player",
                options=options,
                min_values=1,
                max_values=1,
            )
            self.select.callback = self.select_callback
            self.add_item(self.select)

        async def select_callback(self, select_interaction: discord.Interaction):
            steam_id_str, player_display_name = self.select.values[0].split("|")
            steam_id = int(steam_id_str)

            with engine.connect() as conn:
                # All-time stats
                all_time_query = text("""
                    SELECT
                        COUNT(*) AS matches_played,
                        SUM(kills) AS total_kills,
                        SUM(deaths) AS total_deaths,
                        MAX(kills_streak) AS best_kill_streak,
                        AVG(kill_death_ratio) AS avg_kdr,
                        SUM(time_seconds) AS total_time_seconds
                    FROM player_stats
                    WHERE playersteamid_id = :steam_id
                """)
                all_time = conn.execute(all_time_query, {"steam_id": steam_id}).fetchone()

                # Last 6 months
                monthly_query = text("""
                    SELECT
                        TO_CHAR(m.start, 'YYYY-MM') AS month,
                        COUNT(*) AS matches,
                        SUM(kills) AS kills,
                        SUM(deaths) AS deaths,
                        MAX(kills_streak) AS best_kill_streak,
                        AVG(kill_death_ratio) AS avg_kdr,
                        SUM(time_seconds) AS time_seconds
                    FROM player_stats ps
                    JOIN map_history m ON ps.map_id = m.id
                    WHERE ps.playersteamid_id = :steam_id
                    GROUP BY month
                    ORDER BY month DESC
                    LIMIT 6
                """)
                recent_rows = conn.execute(monthly_query, {"steam_id": steam_id}).fetchall()

            # Compute stats
            total_kills = all_time.total_kills or 0
            total_deaths = all_time.total_deaths or 0
            matches_played = all_time.matches_played or 0
            total_seconds = all_time.total_time_seconds or 0
            all_time_kdr = total_kills / total_deaths if total_deaths else 0
            best_kill_streak = all_time.best_kill_streak or 0
            total_hours = total_seconds // 3600
            total_minutes = (total_seconds % 3600) // 60

            def format_month_row(row):
                kdr = f"{row.avg_kdr:.2f}" if row.avg_kdr else "0.00"
                hours = int((row.time_seconds or 0) / 3600)
                short_month = calendar.month_abbr[int(row.month[-2:])]
                return f"📆 {short_month} — {row.kills} / {row.deaths} / {row.matches} / {kdr} 🎯 {row.best_kill_streak} 🕒 {hours}h"

            recent_kills = sum(r.kills for r in recent_rows)
            recent_deaths = sum(r.deaths for r in recent_rows)
            recent_matches = sum(r.matches for r in recent_rows)
            recent_seconds = sum(r.time_seconds for r in recent_rows)

            monthly_lines = [format_month_row(r) for r in recent_rows]

            earlier_kills = total_kills - recent_kills
            earlier_deaths = total_deaths - recent_deaths
            earlier_games = matches_played - recent_matches
            earlier_kdr = earlier_kills / earlier_deaths if earlier_deaths else 0
            earlier_hours = (total_seconds - recent_seconds) // 3600

            requester = select_interaction.user.display_name

            embed = Embed(
                title=f"📊 {requester} requested stats for `{player_display_name}`",
                color=discord.Color.blurple()
            )

            embed.add_field(
                name="🏅 All-Time Totals",
                value=(
                    f"• Games: **{matches_played}**\n"
                    f"• Kills / Deaths: **{total_kills} / {total_deaths}**\n"
                    f"• K/D Ratio: **{all_time_kdr:.2f}**\n"
                    f"• Best Kill Streak: **{best_kill_streak}**\n"
                    f"• Time Played: **{total_hours}h {total_minutes}m**"
                ),
                inline=False
            )

            embed.add_field(
                name="🗓️ Last 6 Months (K/D/G/R 🎯 🕒)",
                value="\n".join(monthly_lines),
                inline=False
            )

            embed.add_field(
                name="📦 Earlier",
                value=(
                    f"{earlier_kills} / {earlier_deaths} / {earlier_games} / "
                    f"{earlier_kdr:.2f} 🎯 {best_kill_streak} 🕒 {earlier_hours}h"
                ),
                inline=False
            )

            await select_interaction.response.send_message(embed=embed)

            logger.info(
                f"/playerstats used by {requester} ({select_interaction.user.id}) "
                f"for Steam ID {steam_id}"
            )

    await interaction.followup.send("Select a player:", view=PlayerSelect())

# --- Bot startup ---

@client.event
async def on_ready():
    logger.info(f"Bot logged in as {client.user} (ID: {client.user.id})")

    try:
        await tree.sync()
        logger.info(f"Synced slash commands for {client.user} (ID: {client.user.id})")

        # Fetch and cache maps ONCE at startup
        fetch_and_cache_maps()
        logger.info("Fetched and cached map data.")

        # Reschedule jobs
        reschedule_job("set_ping_job_1", config.get("SCHEDULED_JOB_1_TIME"), config.get("SCHEDULED_JOB_1_PING"))
        reschedule_job("set_ping_job_2", config.get("SCHEDULED_JOB_2_TIME"), config.get("SCHEDULED_JOB_2_PING"))

        if not scheduler.running:
            scheduler.start()
            logger.info("Scheduler started and jobs scheduled.")

        # Notify in channel.. Edit 3 lines below to be quiet
        channel = await client.fetch_channel(CHANNEL_ID)
        await channel.send("🟢 Bot is online!")
        logger.info("Sent online notification to the channel.")

    except Exception as e:
        logger.exception("Error during on_ready sequence")  # This logs full traceback


# --- Django management command ---

class Command(BaseCommand):
    help = "Starts the Discord Ping Bot"

    def handle(self, *args, **options):
        logger.info("Starting Discord client...")
        client.run(DISCORD_TOKEN)
