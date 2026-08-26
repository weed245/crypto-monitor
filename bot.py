import asyncio
import os

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from database import (
    init_database,
    add_account,
    remove_account,
    get_accounts,
    alert_exists,
    save_alert,
    set_setting,
    get_setting
)

from alerts import send_alert

from scanners.twitter import (
    search_recent_posts
)

from scanners.new_coins import (
    get_latest_token_profiles
)


# --------------------------------------------------
# ENVIRONMENT
# --------------------------------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from Railway Variables"
    )


# --------------------------------------------------
# DISCORD SETUP
# --------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


monitor_lock = asyncio.Lock()


# --------------------------------------------------
# BOT STARTUP
# --------------------------------------------------

@bot.event
async def on_ready():

    await init_database()

    print("=" * 60)
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("Database initialized")
    print("Crypto Monitor is ONLINE")
    print("=" * 60)

    if not monitor_loop.is_running():
        monitor_loop.start()


# --------------------------------------------------
# ALERT CHANNEL
# --------------------------------------------------

async def get_alert_channel():

    channel_id = await get_setting(
        "alert_channel_id"
    )

    if not channel_id:
        return None

    try:

        return bot.get_channel(
            int(channel_id)
        )

    except (ValueError, TypeError):

        return None


# --------------------------------------------------
# X / TWITTER MONITOR
# --------------------------------------------------

async def process_twitter():

    accounts = await get_accounts()

    if not accounts:
        return

    channel = await get_alert_channel()

    if channel is None:

        print(
            "X monitor: no alert channel configured."
        )

        return

    for username in accounts:

        query = (
            f"from:{username} "
            "-is:retweet "
            "-is:reply"
        )

        try:

            posts = await search_recent_posts(
                query,
                max_results=10
            )

        except Exception as error:

            print(
                f"X API error for @{username}: "
                f"{error}"
            )

            continue

        for post in reversed(posts):

            post_id = post.get("id")

            if not post_id:
                continue

            alert_id = f"x:{post_id}"

            if await alert_exists(alert_id):
                continue

            text = post.get(
                "text",
                ""
            )

            created_at = post.get(
                "created_at",
                "Unknown"
            )

            url = (
                "https://x.com/i/web/status/"
                f"{post_id}"
            )

            saved = await save_alert(
                alert_id=alert_id,
                alert_type="X Monitor",
                title=f"New post from @{username}",
                content=text,
                url=url
            )

            if not saved:
                continue

            embed = discord.Embed(
                title=f"🐦 @{username}",
                description=text[:4000],
                color=discord.Color.blue()
            )

            embed.add_field(
                name="Posted",
                value=created_at,
                inline=False
            )

            embed.add_field(
                name="Source",
                value=f"[View post]({url})",
                inline=False
            )

            embed.set_footer(
                text="Crypto Monitor • X Monitor"
            )

            await channel.send(
                embed=embed
            )


# --------------------------------------------------
# NEW TOKEN MONITOR
# --------------------------------------------------

async def process_new_tokens():

    channel = await get_alert_channel()

    if channel is None:

        print(
            "Token monitor: no alert channel configured."
        )

        return

    try:

        profiles = (
            await get_latest_token_profiles()
        )

    except Exception as error:

        print(
            f"Token monitor error: {error}"
        )

        return

    for token in profiles:

        chain_id = token.get(
            "chainId",
            "unknown"
        )

        token_address = token.get(
            "tokenAddress"
        )

        description = token.get(
            "description"
        )

        url = token.get(
            "url"
        )

        if not token_address:
            continue

        alert_id = (
            f"token:{chain_id}:"
            f"{token_address}"
        )

        if await alert_exists(
            alert_id
        ):
            continue

        saved = await save_alert(
            alert_id=alert_id,
            alert_type="New Token Profile",
            title="New token profile detected",
            content=(
                f"{chain_id} "
                f"{token_address}"
            ),
            url=url or ""
        )

        if not saved:
            continue

        message = (
            f"**Network:** `{chain_id}`\n"
            f"**Contract:** `{token_address}`"
        )

        if description:

            message += (
                f"\n\n{description[:1000]}"
            )

        if url:

            message += (
                f"\n\n[View source]({url})"
            )

        await send_alert(
            channel,
            "🆕 New Token Profile",
            message,
            "Token Monitor",
            url
        )


# --------------------------------------------------
# AUTOMATIC MONITORING LOOP
# --------------------------------------------------

@tasks.loop(minutes=2)
async def monitor_loop():

    async with monitor_lock:

        print(
            "Running monitoring cycle..."
        )

        await process_twitter()

        await process_new_tokens()

        print(
            "Monitoring cycle complete."
        )


@monitor_loop.before_loop
async def before_monitor_loop():

    await bot.wait_until_ready()


# --------------------------------------------------
# BASIC COMMANDS
# --------------------------------------------------

@bot.command()
async def ping(ctx):

    await ctx.send(
        "🏓 Crypto Monitor is online!"
    )


@bot.command()
async def status(ctx):

    accounts = await get_accounts()

    channel = await get_alert_channel()

    embed = discord.Embed(
        title="🟢 Crypto Monitor",
        description="Monitoring system is online.",
        color=discord.Color.green()
    )

    embed.add_field(
        name="🐦 X Accounts",
        value=str(len(accounts)),
        inline=True
    )

    embed.add_field(
        name="🆕 Token Monitor",
        value="🟢 Active",
        inline=True
    )

    if channel:

        embed.add_field(
            name="🚨 Alert Channel",
            value=channel.mention,
            inline=True
        )

    else:

        embed.add_field(
            name="🚨 Alert Channel",
            value="❌ Not configured",
            inline=True
        )

    await ctx.send(
        embed=embed
    )


# --------------------------------------------------
# SET ALERT CHANNEL
# --------------------------------------------------

@bot.command()
@commands.has_permissions(
    manage_guild=True
)
async def setchannel(ctx):

    await set_setting(
        "alert_channel_id",
        str(ctx.channel.id)
    )

    await ctx.send(
        f"🚨 Alerts will now be sent to "
        f"{ctx.channel.mention}"
    )


# --------------------------------------------------
# X ACCOUNT COMMANDS
# --------------------------------------------------

@bot.command()
async def watch(ctx, username=None):

    if not username:

        await ctx.send(
            "Usage: `!watch username`"
        )

        return

    username = (
        username
        .replace("@", "")
        .strip()
        .lower()
    )

    added = await add_account(
        username
    )

    if added:

        await ctx.send(
            f"🐦 Now monitoring "
            f"**@{username}**."
        )

    else:

        await ctx.send(
            f"⚠️ **@{username}** "
            f"is already monitored."
        )


@bot.command()
async def unwatch(ctx, username=None):

    if not username:

        await ctx.send(
            "Usage: `!unwatch username`"
        )

        return

    username = (
        username
        .replace("@", "")
        .strip()
        .lower()
    )

    removed = await remove_account(
        username
    )

    if removed:

        await ctx.send(
            f"🗑️ Stopped monitoring "
            f"**@{username}**."
        )

    else:

        await ctx.send(
            f"⚠️ **@{username}** "
            f"wasn't monitored."
        )


@bot.command()
async def accounts(ctx):

    accounts = await get_accounts()

    if not accounts:

        await ctx.send(
            "📭 No X accounts are being monitored."
        )

        return

    account_list = "\n".join(
        f"🐦 @{account}"
        for account in accounts
    )

    embed = discord.Embed(
        title="🐦 Monitored X Accounts",
        description=account_list,
        color=discord.Color.blue()
    )

    await ctx.send(
        embed=embed
    )


# --------------------------------------------------
# TEST ALERT
# --------------------------------------------------

@bot.command()
async def testalert(ctx):

    await send_alert(
        ctx.channel,
        "🚨 Test Crypto Alert",
        "The Discord alert system is working correctly.",
        "Test Alert"
    )


# --------------------------------------------------
# TEST TOKEN FEED
# --------------------------------------------------

@bot.command()
async def testtokens(ctx):

    try:

        profiles = (
            await get_latest_token_profiles()
        )

        await ctx.send(
            f"🧪 Token feed returned "
            f"**{len(profiles)}** profiles."
        )

    except Exception as error:

        print(error)

        await ctx.send(
            "❌ Token feed test failed. "
            "Check Railway logs."
        )


# --------------------------------------------------
# COMMAND LIST
# --------------------------------------------------

@bot.command(name="commands")
async def commands_list(ctx):

    embed = discord.Embed(
        title="📋 Crypto Monitor Commands",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="!setchannel",
        value=(
            "Set the current channel as "
            "the alert channel."
        ),
        inline=False
    )

    embed.add_field(
        name="!watch username",
        value=(
            "Monitor an X account."
        ),
        inline=False
    )

    embed.add_field(
        name="!unwatch username",
        value=(
            "Stop monitoring an X account."
        ),
        inline=False
    )

    embed.add_field(
        name="!accounts",
        value=(
            "Show monitored X accounts."
        ),
        inline=False
    )

    embed.add_field(
        name="!status",
        value=(
            "Show monitoring status."
        ),
        inline=False
    )

    embed.add_field(
        name="!testalert",
        value=(
            "Test Discord alerts."
        ),
        inline=False
    )

    embed.add_field(
        name="!testtokens",
        value=(
            "Test the new-token feed."
        ),
        inline=False
    )

    await ctx.send(
        embed=embed
    )


# --------------------------------------------------
# ERROR HANDLING
# --------------------------------------------------

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.CommandNotFound
    ):
        return

    if isinstance(
        error,
        commands.MissingPermissions
    ):

        await ctx.send(
            "❌ You need the required "
            "Discord permission for that command."
        )

        return

    print(
        f"Command error: {error}"
    )


# --------------------------------------------------
# START BOT
# --------------------------------------------------

bot.run(TOKEN)
