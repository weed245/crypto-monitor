import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Crypto Monitor is ONLINE")


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Crypto Monitor is online!")


@bot.command()
async def status(ctx):
    embed = discord.Embed(
        title="🟢 Crypto Monitor",
        description="The bot is online.",
    )

    embed.add_field(
        name="X/Twitter Monitor",
        value="⏳ Not configured yet",
        inline=False
    )

    embed.add_field(
        name="New Token Monitor",
        value="⏳ Not configured yet",
        inline=False
    )

    await ctx.send(embed=embed)


bot.run(TOKEN)
