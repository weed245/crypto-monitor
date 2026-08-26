import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    init_database,
    add_account,
    remove_account,
    get_accounts
)

from alerts import send_alert


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from Railway Variables"
    )


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    await init_database()

    print("=" * 50)
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("Database initialized")
    print("Crypto Monitor is ONLINE")
    print("=" * 50)


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Crypto Monitor is online!")


@bot.command()
async def status(ctx):
    accounts = await get_accounts()

    embed = discord.Embed(
        title="🟢 Crypto Monitor",
        description="Monitoring system is online.",
        color=discord.Color.green()
    )

    embed.add_field(
        name="🐦 Monitored X Accounts",
        value=str(len(accounts)),
        inline=True
    )

    embed.add_field(
        name="🆕 New Token Monitor",
        value="⏳ Coming next",
        inline=True
    )

    embed.add_field(
        name="💾 Database",
        value="🟢 Connected",
        inline=True
    )

    await ctx.send(embed=embed)


@bot.command()
async def watch(ctx, username=None):
    if not username:
        await ctx.send(
            "Usage: `!watch username`"
        )
        return

    username = username.replace("@", "").strip()

    added = await add_account(username)

    if added:
        await ctx.send(
            f"🐦 Now monitoring **@{username}**."
        )
    else:
        await ctx.send(
            f"⚠️ **@{username}** is already being monitored."
        )


@bot.command()
async def unwatch(ctx, username=None):
    if not username:
        await ctx.send(
            "Usage: `!unwatch username`"
        )
        return

    username = username.replace("@", "").strip()

    removed = await remove_account(username)

    if removed:
        await ctx.send(
            f"🗑️ Stopped monitoring **@{username}**."
        )
    else:
        await ctx.send(
            f"⚠️ **@{username}** wasn't being monitored."
        )


@bot.command()
async def accounts(ctx):
    accounts = await get_accounts()

    if not accounts:
        await ctx.send(
            "📭 No X accounts are being monitored yet."
        )
        return

    account_list = "\n".join(
        f"🐦 @{account}"
        for account in accounts
    )

    embed = discord.Embed(
        title="🐦 Monitored Accounts",
        description=account_list,
        color=discord.Color.blue()
    )

    await ctx.send(embed=embed)


@bot.command()
async def testalert(ctx):
    await send_alert(
        ctx.channel,
        "🚨 Test Crypto Alert",
        "The Discord alert system is working correctly.",
        "Test Alert"
    )


@bot.command(name="commands")
async def commands_list(ctx):
    embed = discord.Embed(
        title="📋 Crypto Monitor Commands",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="!ping",
        value="Check if the bot is responding.",
        inline=False
    )

    embed.add_field(
        name="!status",
        value="Show monitor status.",
        inline=False
    )

    embed.add_field(
        name="!watch username",
        value="Add an X account to the monitoring list.",
        inline=False
    )

    embed.add_field(
        name="!unwatch username",
        value="Remove an X account.",
        inline=False
    )

    embed.add_field(
        name="!accounts",
        value="Show monitored X accounts.",
        inline=False
    )

    embed.add_field(
        name="!testalert",
        value="Send a test Discord alert.",
        inline=False
    )

    await ctx.send(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    print(f"Command error: {error}")


bot.run(TOKEN)
