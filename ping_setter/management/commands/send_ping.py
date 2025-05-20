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
