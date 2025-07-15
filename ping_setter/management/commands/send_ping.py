# Final version of send_ping.py with updated /playerstats command

import logging
import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import create_engine, text
import datetime
import json

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Load config
with open("/opt/ping_setter_hll/config.jsonc", "r") as f:
    config_lines = f.readlines()
config_lines = [line for line in config_lines if not line.strip().startswith("//")]
config = json.loads("".join(config_lines))

DB_URL = config.get("DB_URL")
STATS_CHANNEL_ID = config.get("CHANNEL_ID_stats")

intents = discord.Intents.default()
client = commands.Bot(command_prefix="/", intents=intents)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    try:
        synced = await client.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@client.tree.command(name="playerstats", description="Look up player stats by current name")
@app_commands.describe(player_name="Enter partial player name to search")
async def playerstats(interaction: discord.Interaction, player_name: str):
    if str(interaction.channel_id) != str(STATS_CHANNEL_ID):
        await interaction.response.send_message("This command is only available in the stats channel.", ephemeral=True)
        return

    logger.info(f"/playerstats invoked by {interaction.user} for player name '{player_name}'")

    await interaction.response.defer()

    try:
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            # Search up to 20 names matching the input
            name_query = text("""
                SELECT DISTINCT ON (pn.playersteamid_id) 
                    pn.playersteamid_id, pn.name, ps.kills, ps.deaths, ps.time_seconds, ps.kill_death_ratio
                FROM player_names pn
                LEFT JOIN player_stats ps ON pn.playersteamid_id = ps.playersteamid_id
                WHERE pn.name ILIKE :search_term
                ORDER BY pn.playersteamid_id, pn.last_seen DESC
                LIMIT 20
            """)

            results = conn.execute(name_query, {"search_term": f"%{player_name}%"}).fetchall()

            if not results:
                await interaction.followup.send(f"No players found matching `{player_name}`.")
                return

            lines = []
            for row in results:
                steam_id = row.playersteamid_id
                name = row.name
                kills = row.kills or 0
                deaths = row.deaths or 0
                playtime_min = (row.time_seconds or 0) // 60
                kdr = round(row.kill_death_ratio or 0, 2)

                lines.append(f"**{name}** (ID {steam_id}) - Kills: `{kills}`, Deaths: `{deaths}`, KDR: `{kdr}`, Time: `{playtime_min} min`")

            await interaction.followup.send("\n".join(lines[:10]))

    except Exception as e:
        logger.error(f"Error in /playerstats: {e}", exc_info=True)
        await interaction.followup.send("Something went wrong while fetching stats.")

client.run(config["DISCORD_TOKEN"])
