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

from scanners.twitter import search_recent_posts

from scanners.new_coins import (
    get_latest_token_profiles,
    get_token_details,
    DEFAULT_MIN_LIQUIDITY,
    DEFAULT_MIN_VOLUME,
    DEFAULT_CHAINS
)


# ==================================================
# ENVIRONMENT
# ==================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from Railway Variables"
    )


# ==================================================
# DISCORD SETUP
# ==================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

monitor_lock = asyncio.Lock()

# X is currently out of API credits.
x_api_disabled = False


# ==================================================
# DATABASE SETTINGS
# ==================================================

async def get_min_liquidity():

    value = await get_setting(
        "min_liquidity"
    )

    if value is None:
        return DEFAULT_MIN_LIQUIDITY

    try:
        return float(value)
    except ValueError:
        return DEFAULT_MIN_LIQUIDITY


async def get_min_volume():

    value = await get_setting(
        "min_volume"
    )

    if value is None:
        return DEFAULT_MIN_VOLUME

    try:
        return float(value)
    except ValueError:
        return DEFAULT_MIN_VOLUME


async def get_allowed_chains():

    value = await get_setting(
        "allowed_chains"
    )

    if value is None:
        return set(DEFAULT_CHAINS)

    chains = set()

    for chain in value.split(","):

        chain = chain.strip().lower()

        if chain:
            chains.add(chain)

    if not chains:
        return set(DEFAULT_CHAINS)

    return chains


# ==================================================
# BOT STARTUP
# ==================================================

@bot.event
async def on_ready():

    await init_database()

    # Create default settings if they don't exist.

    if await get_setting("min_liquidity") is None:

        await set_setting(
            "min_liquidity",
            DEFAULT_MIN_LIQUIDITY
        )

    if await get_setting("min_volume") is None:

        await set_setting(
            "min_volume",
            DEFAULT_MIN_VOLUME
        )

    if await get_setting("allowed_chains") is None:

        await set_setting(
            "allowed_chains",
            ",".join(sorted(DEFAULT_CHAINS))
        )

    print("=" * 60)
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("Database initialized")
    print("Crypto Monitor is ONLINE")
    print("=" * 60)

    if not monitor_loop.is_running():
        monitor_loop.start()


# ==================================================
# ALERT CHANNEL
# ==================================================

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


# ==================================================
# X / TWITTER MONITOR
# ==================================================

async def process_twitter():

    global x_api_disabled

    if x_api_disabled:
        return

    accounts = await get_accounts()

    if not accounts:
        return

    channel = await get_alert_channel()

    if channel is None:
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

            error_text = str(error)

            if (
                "402" in error_text
                or "credits_depleted" in error_text
                or "Payment Required" in error_text
            ):

                x_api_disabled = True

                print(
                    "X API credits depleted."
                )

                print(
                    "X monitoring paused."
                )

                return

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

            if await alert_exists(
                alert_id
            ):
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
                title=f"🐦 New post from @{username}",
                description=text[:4000],
                color=discord.Color.blue()
            )

            embed.add_field(
                name="🕐 Posted",
                value=created_at,
                inline=False
            )

            embed.add_field(
                name="🔗 Source",
                value=f"[View post]({url})",
                inline=False
            )

            embed.set_footer(
                text="Crypto Monitor • X Monitor"
            )

            await channel.send(
                embed=embed
            )


# ==================================================
# NEW TOKEN MONITOR
# ==================================================

async def process_new_tokens():

    channel = await get_alert_channel()

    if channel is None:
        return

    min_liquidity = await get_min_liquidity()

    min_volume = await get_min_volume()

    allowed_chains = await get_allowed_chains()

    try:

        profiles = (
            await get_latest_token_profiles()
        )

        token_details = await get_token_details(
            profiles,
            min_liquidity=min_liquidity,
            min_volume=min_volume,
            allowed_chains=allowed_chains
        )

    except Exception as error:

        print(
            f"Token monitor error: {error}"
        )

        return

    for item in token_details:

        token = item.get(
            "profile",
            {}
        )

        market = item.get(
            "market"
        )

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
            "A newly reported token profile "
            "passed your configured filters."
        )

        if description:

            message += (
                f"\n\n{description[:1000]}"
            )

        fields = [
            (
                "🌐 Network",
                chain_id,
                True
            ),
            (
                "📋 Contract",
                f"`{token_address}`",
                False
            )
        ]

        if market:

            base_token = market.get(
                "baseToken",
                {}
            )

            price = market.get(
                "priceUsd"
            )

            liquidity = (
                market.get(
                    "liquidity",
                    {}
                ).get(
                    "usd"
                )
            )

            volume = (
                market.get(
                    "volume",
                    {}
                ).get(
                    "h24"
                )
            )

            market_name = base_token.get(
                "name",
                "Unknown"
            )

            market_symbol = base_token.get(
                "symbol",
                "Unknown"
            )

            fields.append(
                (
                    "🪙 Token",
                    f"{market_name} ({market_symbol})",
                    True
                )
            )

            if price:

                fields.append(
                    (
                        "💵 Price",
                        f"${price}",
                        True
                    )
                )

            if isinstance(
                liquidity,
                (int, float)
            ):

                fields.append(
                    (
                        "💧 Liquidity",
                        f"${liquidity:,.2f}",
                        True
                    )
                )

            if isinstance(
                volume,
                (int, float)
            ):

                fields.append(
                    (
                        "📊 24h Volume",
                        f"${volume:,.2f}",
                        True
                    )
                )

            dex_name = market.get(
                "dexId"
            )

            if dex_name:

                fields.append(
                    (
                        "🏦 DEX",
                        dex_name,
                        True
                    )
                )

        await send_alert(
            channel=channel,
            title="🆕 New Token Profile Detected",
            description=message,
            alert_type="Token Monitor",
            url=url,
            fields=fields
        )


# ==================================================
# AUTOMATIC MONITORING LOOP
# ==================================================

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


# ==================================================
# PING
# ==================================================

@bot.command()
async def ping(ctx):

    await ctx.send(
        "🏓 Crypto Monitor is online!"
    )


# ==================================================
# STATUS
# ==================================================

@bot.command()
async def status(ctx):

    accounts = await get_accounts()

    channel = await get_alert_channel()

    min_liquidity = await get_min_liquidity()

    min_volume = await get_min_volume()

    chains = await get_allowed_chains()

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

    if x_api_disabled:

        embed.add_field(
            name="🐦 X Monitor",
            value="🔴 Paused — X credits depleted",
            inline=True
        )

    else:

        embed.add_field(
            name="🐦 X Monitor",
            value="🟢 Active",
            inline=True
        )

    embed.add_field(
        name="🆕 Token Monitor",
        value="🟢 Active",
        inline=True
    )

    embed.add_field(
        name="💧 Min Liquidity",
        value=f"${min_liquidity:,.0f}",
        inline=True
    )

    embed.add_field(
        name="📊 Min 24h Volume",
        value=f"${min_volume:,.0f}",
        inline=True
    )

    embed.add_field(
        name="🌐 Chains",
        value=", ".join(
            sorted(chains)
        ),
        inline=False
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


# ==================================================
# SET ALERT CHANNEL
# ==================================================

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


# ==================================================
# SET LIQUIDITY
# ==================================================

@bot.command()
@commands.has_permissions(
    manage_guild=True
)
async def setliquidity(
    ctx,
    amount=None
):

    if amount is None:

        await ctx.send(
            "Usage: `!setliquidity 5000`"
        )

        return

    try:

        amount = float(
            amount.replace(
                ",",
                ""
            )
        )

    except ValueError:

        await ctx.send(
            "❌ Enter a valid number."
        )

        return

    if amount < 0:

        await ctx.send(
            "❌ Liquidity cannot be negative."
        )

        return

    await set_setting(
        "min_liquidity",
        amount
    )

    await ctx.send(
        f"💧 Minimum liquidity set to "
        f"**${amount:,.0f}**."
    )


# ==================================================
# SET VOLUME
# ==================================================

@bot.command()
@commands.has_permissions(
    manage_guild=True
)
async def setvolume(
    ctx,
    amount=None
):

    if amount is None:

        await ctx.send(
            "Usage: `!setvolume 2500`"
        )

        return

    try:

        amount = float(
            amount.replace(
                ",",
                ""
            )
        )

    except ValueError:

        await ctx.send(
            "❌ Enter a valid number."
        )

        return

    if amount < 0:

        await ctx.send(
            "❌ Volume cannot be negative."
        )

        return

    await set_setting(
        "min_volume",
        amount
    )

    await ctx.send(
        f"📊 Minimum 24h volume set to "
        f"**${amount:,.0f}**."
    )


# ==================================================
# SHOW FILTERS
# ==================================================

@bot.command()
async def filters(ctx):

    min_liquidity = await get_min_liquidity()

    min_volume = await get_min_volume()

    chains = await get_allowed_chains()

    embed = discord.Embed(
        title="⚙️ Token Filters",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="💧 Minimum Liquidity",
        value=f"${min_liquidity:,.0f}",
        inline=True
    )

    embed.add_field(
        name="📊 Minimum 24h Volume",
        value=f"${min_volume:,.0f}",
        inline=True
    )

    embed.add_field(
        name="🌐 Enabled Chains",
        value=", ".join(
            sorted(chains)
        ),
        inline=False
    )

    await ctx.send(
        embed=embed
    )


# ==================================================
# ENABLE CHAIN
# ==================================================

@bot.command()
@commands.has_permissions(
    manage_guild=True
)
async def enablechain(
    ctx,
    chain=None
):

    if not chain:

        await ctx.send(
            "Usage: `!enablechain solana`"
        )

        return

    chain = chain.lower().strip()

    chains = await get_allowed_chains()

    chains.add(chain)

    await set_setting(
        "allowed_chains",
        ",".join(sorted(chains))
    )

    await ctx.send(
        f"🌐 Enabled chain: **{chain}**"
    )


# ==================================================
# DISABLE CHAIN
# ==================================================

@bot.command()
@commands.has_permissions(
    manage_guild=True
)
async def disablechain(
    ctx,
    chain=None
):

    if not chain:

        await ctx.send(
            "Usage: `!disablechain solana`"
        )

        return

    chain = chain.lower().strip()

    chains = await get_allowed_chains()

    if chain not in chains:

        await ctx.send(
            f"⚠️ **{chain}** isn't enabled."
        )

        return

    chains.remove(chain)

    if not chains:

        await ctx.send(
            "❌ You must keep at least "
            "one chain enabled."
        )

        return

    await set_setting(
        "allowed_chains",
        ",".join(sorted(chains))
    )

    await ctx.send(
        f"🌐 Disabled chain: **{chain}**"
    )


# ==================================================
# SHOW CHAINS
# ==================================================

@bot.command()
async def chains(ctx):

    allowed = await get_allowed_chains()

    embed = discord.Embed(
        title="🌐 Enabled Chains",
        description="\n".join(
            f"• `{chain}`"
            for chain in sorted(allowed)
        ),
        color=discord.Color.blue()
    )

    await ctx.send(
        embed=embed
    )


# ==================================================
# WATCH X ACCOUNT
# ==================================================

@bot.command()
async def watch(
    ctx,
    username=None
):

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
            f"is already being monitored."
        )


# ==================================================
# UNWATCH X ACCOUNT
# ==================================================

@bot.command()
async def unwatch(
    ctx,
    username=None
):

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
            f"wasn't being monitored."
        )


# ==================================================
# LIST ACCOUNTS
# ==================================================

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


# ==================================================
# TEST ALERT
# ==================================================

@bot.command()
async def testalert(ctx):

    await send_alert(
        channel=ctx.channel,
        title="🚨 Test Crypto Alert",
        description=(
            "The Discord alert system "
            "is working correctly."
        ),
        alert_type="Test Alert"
    )


# ==================================================
# TEST TOKEN FEED
# ==================================================

@bot.command()
async def testtokens(ctx):

    try:

        profiles = (
            await get_latest_token_profiles()
        )

        min_liquidity = await get_min_liquidity()

        min_volume = await get_min_volume()

        chains = await get_allowed_chains()

        details = await get_token_details(
            profiles,
            min_liquidity=min_liquidity,
            min_volume=min_volume,
            allowed_chains=chains
        )

        market_count = sum(
            1
            for item in details
            if item.get("market")
        )

        await ctx.send(
            f"🧪 Token feed test complete.\n\n"
            f"Profiles received: **{len(profiles)}**\n"
            f"Passed filters: **{len(details)}**\n"
            f"Market data found: **{market_count}**"
        )

    except Exception as error:

        print(
            f"Token feed test error: {error}"
        )

        await ctx.send(
            "❌ Token feed test failed. "
            "Check Railway logs."
        )


# ==================================================
# X SEARCH
# ==================================================

@bot.command()
async def xsearch(
    ctx,
    *,
    query=None
):

    global x_api_disabled

    if not query:

        await ctx.send(
            "Usage: `!xsearch bitcoin`"
        )

        return

    if x_api_disabled:

        await ctx.send(
            "🔴 X monitoring is paused "
            "because X API credits are depleted."
        )

        return

    try:

        posts = await search_recent_posts(
            query,
            max_results=10
        )

        if not posts:

            await ctx.send(
                "🔎 No recent X posts found."
            )

            return

        for post in posts[:5]:

            text = post.get(
                "text",
                ""
            )

            post_id = post.get(
                "id"
            )

            url = (
                "https://x.com/i/web/status/"
                f"{post_id}"
            )

            embed = discord.Embed(
                title="🐦 X Post",
                description=text[:4000],
                color=discord.Color.blue()
            )

            embed.add_field(
                name="🔗 Source",
                value=f"[View post]({url})",
                inline=False
            )

            await ctx.send(
                embed=embed
            )

    except Exception as error:

        error_text = str(error)

        if (
            "402" in error_text
            or "credits_depleted" in error_text
            or "Payment Required" in error_text
        ):

            x_api_disabled = True

            await ctx.send(
                "🔴 X API credits are depleted. "
                "X monitoring has been paused."
            )

            return

        print(
            f"Manual X search error: {error}"
        )

        await ctx.send(
            "❌ X search failed. "
            "Check Railway logs."
        )


# ==================================================
# COMMAND LIST
# ==================================================

@bot.command(name="commands")
async def commands_list(ctx):

    embed = discord.Embed(
        title="📋 Crypto Monitor Commands",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="⚙️ Settings",
        value=(
            "`!setchannel`\n"
            "`!setliquidity 5000`\n"
            "`!setvolume 2500`\n"
            "`!filters`"
        ),
        inline=False
    )

    embed.add_field(
        name="🌐 Chains",
        value=(
            "`!enablechain solana`\n"
            "`!disablechain polygon`\n"
            "`!chains`"
        ),
        inline=False
    )

    embed.add_field(
        name="🐦 X Monitoring",
        value=(
            "`!watch username`\n"
            "`!unwatch username`\n"
            "`!accounts`\n"
            "`!xsearch bitcoin`"
        ),
        inline=False
    )

    embed.add_field(
        name="🧪 Testing",
        value=(
            "`!testalert`\n"
            "`!testtokens`\n"
            "`!status`\n"
            "`!ping`"
        ),
        inline=False
    )

    await ctx.send(
        embed=embed
    )


# ==================================================
# ERROR HANDLING
# ==================================================

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
            "❌ You don't have permission "
            "to use that command."
        )

        return

    print(
        f"Command error: {error}"
    )


# ==================================================
# START BOT
# ==================================================

bot.run(TOKEN)
