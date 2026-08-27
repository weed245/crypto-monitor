import os
import asyncio
import logging
import ccxt
import discord
import pandas as pd

from discord.ext import commands, tasks
from dotenv import load_dotenv

# ============================================================
# ⚙️ CONFIGURATION
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY")
EXCHANGE_SECRET_KEY = os.getenv("EXCHANGE_SECRET_KEY")

# Discord channel for automatic FOMO alerts.
# Put your channel ID in .env:
# ALERT_CHANNEL_ID=123456789012345678
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

TIMEFRAME = "1h"

FAST_MA = 9
SLOW_MA = 21

RSI_PERIOD = 14

VOLUME_PERIOD = 20

ATR_PERIOD = 14

# How often automatic scanner runs
SCAN_INTERVAL_MINUTES = 15

# Minimum score before an automatic alert
FOMO_ALERT_SCORE = 75
DROP_ALERT_SCORE = 75

# Coins to scan
SCAN_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "SUI/USDT",
    "LTC/USDT",
    "DOT/USDT",
]

# Existing trade amount
TRADE_AMOUNT = 0.001

# Existing paper-trading risk settings
STOP_LOSS_PERCENT = 0.02
TAKE_PROFIT_PERCENT = 0.04


# ============================================================
# 📝 LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


# ============================================================
# 🤖 DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# 📡 EXCHANGE
# ============================================================

exchange = ccxt.binance({
    "apiKey": EXCHANGE_API_KEY,
    "secret": EXCHANGE_SECRET_KEY,
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})

# Keep your existing sandbox/testnet setup.
exchange.set_sandbox_mode(True)


# ============================================================
# 🧠 SCANNER STATE
# ============================================================

scanner_running = False

last_alerts = {}

current_position = None
entry_price = None


# ============================================================
# 📊 MARKET DATA
# ============================================================

def fetch_market_data(symbol, timeframe=TIMEFRAME, limit=150):
    try:
        bars = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit
        )

        if not bars:
            return None

        df = pd.DataFrame(
            bars,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="ms"
        )

        return df

    except Exception as e:
        logging.error(
            f"{symbol} market-data error: {e}"
        )
        return None


# ============================================================
# 📈 INDICATORS
# ============================================================

def calculate_indicators(df):

    if df is None or len(df) < 60:
        return None

    df = df.copy()

    # -------------------------
    # Moving averages
    # -------------------------

    df["fast_ma"] = (
        df["close"]
        .rolling(FAST_MA)
        .mean()
    )

    df["slow_ma"] = (
        df["close"]
        .rolling(SLOW_MA)
        .mean()
    )

    # -------------------------
    # RSI - Wilder style
    # -------------------------

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / RSI_PERIOD,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / RSI_PERIOD,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # -------------------------
    # MACD
    # -------------------------

    ema12 = (
        df["close"]
        .ewm(span=12, adjust=False)
        .mean()
    )

    ema26 = (
        df["close"]
        .ewm(span=26, adjust=False)
        .mean()
    )

    df["macd"] = ema12 - ema26

    df["macd_signal"] = (
        df["macd"]
        .ewm(span=9, adjust=False)
        .mean()
    )

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # -------------------------
    # Volume
    # -------------------------

    df["avg_volume"] = (
        df["volume"]
        .rolling(VOLUME_PERIOD)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["avg_volume"]
    )

    # -------------------------
    # ATR
    # -------------------------

    previous_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]

    tr2 = (
        df["high"] -
        previous_close
    ).abs()

    tr3 = (
        df["low"] -
        previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["atr"] = (
        true_range
        .rolling(ATR_PERIOD)
        .mean()
    )

    # -------------------------
    # Momentum
    # -------------------------

    df["momentum_1h"] = (
        df["close"].pct_change(1) * 100
    )

    df["momentum_4h"] = (
        df["close"].pct_change(4) * 100
    )

    df["momentum_24h"] = (
        df["close"].pct_change(24) * 100
    )

    return df


# ============================================================
# 🧠 FOMO / DROP AI-STYLE SCORING
# ============================================================

def analyze_symbol(symbol):

    df = fetch_market_data(symbol)

    if df is None:
        return None

    df = calculate_indicators(df)

    if df is None:
        return None

    # IMPORTANT:
    # -2 = most recently CLOSED candle
    # -1 = currently forming candle
    candle = df.iloc[-2]
    previous = df.iloc[-3]

    price = float(candle["close"])

    fomo_score = 0
    drop_score = 0

    reasons_up = []
    reasons_down = []

    # ========================================================
    # MA TREND
    # ========================================================

    if candle["fast_ma"] > candle["slow_ma"]:
        fomo_score += 20
        reasons_up.append("9 MA above 21 MA")

    if candle["fast_ma"] < candle["slow_ma"]:
        drop_score += 20
        reasons_down.append("9 MA below 21 MA")

    # ========================================================
    # MA CROSS
    # ========================================================

    bullish_cross = (
        previous["fast_ma"] <= previous["slow_ma"]
        and
        candle["fast_ma"] > candle["slow_ma"]
    )

    bearish_cross = (
        previous["fast_ma"] >= previous["slow_ma"]
        and
        candle["fast_ma"] < candle["slow_ma"]
    )

    if bullish_cross:
        fomo_score += 25
        reasons_up.append("Bullish MA crossover")

    if bearish_cross:
        drop_score += 25
        reasons_down.append("Bearish MA crossover")

    # ========================================================
    # RSI
    # ========================================================

    rsi = float(candle["rsi"])

    if 50 <= rsi < 70:
        fomo_score += 15
        reasons_up.append(f"RSI bullish ({rsi:.1f})")

    elif rsi >= 70:
        # Very high RSI can indicate FOMO but also overheating.
        fomo_score += 10
        reasons_up.append(f"RSI overheated ({rsi:.1f})")

        drop_score += 5
        reasons_down.append("RSI potentially overheated")

    elif rsi < 40:
        drop_score += 15
        reasons_down.append(f"Weak RSI ({rsi:.1f})")

    # ========================================================
    # MACD
    # ========================================================

    macd = float(candle["macd"])
    macd_signal = float(candle["macd_signal"])

    if macd > macd_signal:
        fomo_score += 15
        reasons_up.append("MACD bullish")

    else:
        drop_score += 15
        reasons_down.append("MACD bearish")

    # ========================================================
    # VOLUME
    # ========================================================

    volume_ratio = float(candle["volume_ratio"])

    if volume_ratio >= 1.5:
        fomo_score += 15
        drop_score += 10

        reasons_up.append(
            f"Volume spike {volume_ratio:.1f}x"
        )

        reasons_down.append(
            f"High volume {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 1.2:

        fomo_score += 8

        reasons_up.append(
            f"Volume {volume_ratio:.1f}x average"
        )

    # ========================================================
    # MOMENTUM
    # ========================================================

    momentum_1h = float(
        candle["momentum_1h"]
    )

    momentum_4h = float(
        candle["momentum_4h"]
    )

    if momentum_1h > 0.5:
        fomo_score += 8
        reasons_up.append(
            f"1h momentum +{momentum_1h:.2f}%"
        )

    elif momentum_1h < -0.5:
        drop_score += 8
        reasons_down.append(
            f"1h momentum {momentum_1h:.2f}%"
        )

    if momentum_4h > 1:
        fomo_score += 7
        reasons_up.append(
            f"4h momentum +{momentum_4h:.2f}%"
        )

    elif momentum_4h < -1:
        drop_score += 7
        reasons_down.append(
            f"4h momentum {momentum_4h:.2f}%"
        )

    # ========================================================
    # PRICE CHANGE
    # ========================================================

    candle_open = float(candle["open"])

    candle_change = (
        (price - candle_open) /
        candle_open
    ) * 100

    if candle_change > 1:
        fomo_score += 5
        reasons_up.append(
            f"Candle +{candle_change:.2f}%"
        )

    elif candle_change < -1:
        drop_score += 5
        reasons_down.append(
            f"Candle {candle_change:.2f}%"
        )

    # ========================================================
    # LIMIT SCORES
    # ========================================================

    fomo_score = min(100, max(0, fomo_score))
    drop_score = min(100, max(0, drop_score))

    # ========================================================
    # DIRECTION
    # ========================================================

    if fomo_score >= drop_score:
        direction = "PUMP WATCH"
    else:
        direction = "DROP WATCH"

    return {
        "symbol": symbol,
        "price": price,
        "fomo_score": fomo_score,
        "drop_score": drop_score,
        "direction": direction,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "momentum_1h": momentum_1h,
        "momentum_4h": momentum_4h,
        "ma_fast": float(candle["fast_ma"]),
        "ma_slow": float(candle["slow_ma"]),
        "macd": macd,
        "macd_signal": macd_signal,
        "reasons_up": reasons_up,
        "reasons_down": reasons_down
    }


# ============================================================
# 📊 SCAN ALL COINS
# ============================================================

def scan_market():

    results = []

    for symbol in SCAN_SYMBOLS:

        try:

            result = analyze_symbol(symbol)

            if result:
                results.append(result)

        except Exception as e:

            logging.error(
                f"Scanner error for {symbol}: {e}"
            )

    return results


# ============================================================
# 💬 FORMAT ALERT
# ============================================================

def create_signal_embed(result):

    symbol = result["symbol"]

    fomo = result["fomo_score"]
    drop = result["drop_score"]

    if fomo >= drop:

        embed = discord.Embed(
            title="🚀 FOMO AI ALERT",
            description=(
                f"**{symbol}**\n"
                f"Potential upward momentum detected."
            ),
            color=discord.Color.green()
        )

        embed.add_field(
            name="🔥 FOMO Score",
            value=f"**{fomo}/100**",
            inline=True
        )

        embed.add_field(
            name="📉 Drop Score",
            value=f"{drop}/100",
            inline=True
        )

        reasons = result["reasons_up"]

    else:

        embed = discord.Embed(
            title="📉 DROP AI ALERT",
            description=(
                f"**{symbol}**\n"
                f"Potential downward pressure detected."
            ),
            color=discord.Color.red()
        )

        embed.add_field(
            name="📈 FOMO Score",
            value=f"{fomo}/100",
            inline=True
        )

        embed.add_field(
            name="🔻 Drop Score",
            value=f"**{drop}/100**",
            inline=True
        )

        reasons = result["reasons_down"]

    embed.add_field(
        name="💰 Price",
        value=f"${result['price']:,.4f}",
        inline=False
    )

    embed.add_field(
        name="📊 RSI",
        value=f"{result['rsi']:.1f}",
        inline=True
    )

    embed.add_field(
        name="📊 Volume",
        value=f"{result['volume_ratio']:.2f}x avg",
        inline=True
    )

    embed.add_field(
        name="⚡ Momentum",
        value=(
            f"1h: {result['momentum_1h']:+.2f}%\n"
            f"4h: {result['momentum_4h']:+.2f}%"
        ),
        inline=True
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons[:6]
    )

    if not reason_text:
        reason_text = "No major confirmation."

    embed.add_field(
        name="🧠 Why?",
        value=reason_text,
        inline=False
    )

    embed.set_footer(
        text="Market analysis only • Not a guaranteed prediction"
    )

    return embed


# ============================================================
# 📢 SEND ALERT
# ============================================================

async def send_signal(result):

    if ALERT_CHANNEL_ID == 0:
        logging.warning(
            "ALERT_CHANNEL_ID is not configured."
        )
        return

    channel = bot.get_channel(ALERT_CHANNEL_ID)

    if channel is None:
        logging.warning(
            "Could not find alert channel."
        )
        return

    embed = create_signal_embed(result)

    await channel.send(
        embed=embed
    )


# ============================================================
# ⏱️ AUTOMATIC FOMO WATCHER
# ============================================================

@tasks.loop(minutes=SCAN_INTERVAL_MINUTES)
async def fomo_watcher():

    global last_alerts

    logging.info(
        "🔎 Running automatic FOMO scan..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    if not results:
        logging.warning(
            "No scanner results."
        )
        return

    # Sort strongest signals
    strongest_fomo = sorted(
        results,
        key=lambda x: x["fomo_score"],
        reverse=True
    )

    strongest_drops = sorted(
        results,
        key=lambda x: x["drop_score"],
        reverse=True
    )

    # Top FOMO signal
    if strongest_fomo:

        result = strongest_fomo[0]

        if result["fomo_score"] >= FOMO_ALERT_SCORE:

            key = (
                result["symbol"],
                "FOMO"
            )

            if key not in last_alerts:

                await send_signal(result)

                last_alerts[key] = True

    # Top drop signal
    if strongest_drops:

        result = strongest_drops[0]

        if result["drop_score"] >= DROP_ALERT_SCORE:

            key = (
                result["symbol"],
                "DROP"
            )

            if key not in last_alerts:

                await send_signal(result)

                last_alerts[key] = True

    # Reset alert memory when signals weaken
    for key in list(last_alerts):

        symbol, signal_type = key

        matching = next(
            (
                x for x in results
                if x["symbol"] == symbol
            ),
            None
        )

        if matching is None:
            continue

        if signal_type == "FOMO":
            if matching["fomo_score"] < 60:
                del last_alerts[key]

        elif signal_type == "DROP":
            if matching["drop_score"] < 60:
                del last_alerts[key]

    logging.info(
        f"Scan complete: {len(results)} coins analyzed."
    )


# ============================================================
# 🤖 BOT READY
# ============================================================

@bot.event
async def on_ready():

    logging.info(
        f"✅ Logged in as {bot.user}"
    )

    try:

        await asyncio.to_thread(
            exchange.load_markets
        )

        logging.info(
            "📊 Binance connection successful."
        )

    except Exception as e:

        logging.error(
            f"Exchange connection error: {e}"
        )

    if not fomo_watcher.is_running():

        fomo_watcher.start()

        logging.info(
            "🔎 Automatic FOMO watcher started."
        )


# ============================================================
# 💰 EXISTING !price COMMAND
# ============================================================

@bot.command(name="price")
async def get_price(
    ctx,
    symbol: str = "BTC/USDT"
):

    try:

        ticker = await asyncio.to_thread(
            exchange.fetch_ticker,
            symbol
        )

        price = ticker["last"]

        await ctx.send(
            f"📈 Current **{symbol}** price: "
            f"`${price:,.2f}`"
        )

    except Exception as e:

        await ctx.send(
            f"❌ Error fetching price: `{e}`"
        )


# ============================================================
# 💵 EXISTING !buy COMMAND
# ============================================================

@bot.command(name="buy")
async def buy_bitcoin(
    ctx,
    amount: float
):

    symbol = "BTC/USDT"

    try:

        await ctx.send(
            f"🔄 Processing paper buy order "
            f"for {amount} BTC..."
        )

        order = await asyncio.to_thread(
            exchange.create_market_buy_order,
            symbol,
            amount
        )

        embed = discord.Embed(
            title="✅ Order Executed Successfully",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Symbol",
            value=order["symbol"],
            inline=True
        )

        embed.add_field(
            name="Type",
            value=order["type"].upper(),
            inline=True
        )

        embed.add_field(
            name="Amount",
            value=f"{order['amount']} BTC",
            inline=True
        )

        embed.add_field(
            name="Status",
            value=order["status"].upper(),
            inline=False
        )

        await ctx.send(
            embed=embed
        )

    except Exception as e:

        await ctx.send(
            f"❌ Order Failed: `{e}`"
        )


# ============================================================
# 💸 EXISTING !sell COMMAND
# ============================================================

@bot.command(name="sell")
async def sell_bitcoin(
    ctx,
    amount: float
):

    symbol = "BTC/USDT"

    try:

        await ctx.send(
            f"🔄 Processing paper sell order "
            f"for {amount} BTC..."
        )

        order = await asyncio.to_thread(
            exchange.create_market_sell_order,
            symbol,
            amount
        )

        embed = discord.Embed(
            title="✅ Order Executed Successfully",
            color=discord.Color.red()
        )

        embed.add_field(
            name="Symbol",
            value=order["symbol"],
            inline=True
        )

        embed.add_field(
            name="Type",
            value=order["type"].upper(),
            inline=True
        )

        embed.add_field(
            name="Amount",
            value=f"{order['amount']} BTC",
            inline=True
        )

        embed.add_field(
            name="Status",
            value=order["status"].upper(),
            inline=False
        )

        await ctx.send(
            embed=embed
        )

    except Exception as e:

        await ctx.send(
            f"❌ Order Failed: `{e}`"
        )


# ============================================================
# 💰 EXISTING !balance COMMAND
# ============================================================

@bot.command(name="balance")
async def get_balance(ctx):

    try:

        balance = await asyncio.to_thread(
            exchange.fetch_balance
        )

        btc_free = (
            balance
            .get("BTC", {})
            .get("free", 0.0)
        )

        usdt_free = (
            balance
            .get("USDT", {})
            .get("free", 0.0)
        )

        await ctx.send(
            "💰 **Your Paper Wallet Balance:**\n"
            f"• **USDT:** ${usdt_free:,.2f}\n"
            f"• **BTC:** {btc_free} BTC"
        )

    except Exception as e:

        await ctx.send(
            f"❌ Error fetching balance: `{e}`"
        )


# ============================================================
# 🚀 !fomo COMMAND
# ============================================================

@bot.command(name="fomo")
async def fomo_command(ctx):

    await ctx.send(
        "🔎 Scanning the market for the "
        "strongest FOMO setups..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    results = sorted(
        results,
        key=lambda x: x["fomo_score"],
        reverse=True
    )

    results = results[:5]

    if not results:

        await ctx.send(
            "❌ No market data available."
        )
        return

    embed = discord.Embed(
        title="🚀 FOMO AI — Top Setups",
        color=discord.Color.green()
    )

    for result in results:

        embed.add_field(
            name=(
                f"{result['symbol']} — "
                f"{result['fomo_score']}/100"
            ),
            value=(
                f"💰 ${result['price']:,.4f}\n"
                f"RSI: {result['rsi']:.1f} | "
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"Momentum: "
                f"{result['momentum_1h']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# 📉 !drops COMMAND
# ============================================================

@bot.command(name="drops")
async def drops_command(ctx):

    await ctx.send(
        "🔎 Scanning for coins showing "
        "downward pressure..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    results = sorted(
        results,
        key=lambda x: x["drop_score"],
        reverse=True
    )

    results = results[:5]

    if not results:

        await ctx.send(
            "❌ No market data available."
        )
        return

    embed = discord.Embed(
        title="📉 DROP AI — Highest Risk",
        color=discord.Color.red()
    )

    for result in results:

        embed.add_field(
            name=(
                f"{result['symbol']} — "
                f"{result['drop_score']}/100"
            ),
            value=(
                f"💰 ${result['price']:,.4f}\n"
                f"RSI: {result['rsi']:.1f} | "
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"Momentum: "
                f"{result['momentum_1h']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# 🔎 !scan COMMAND
# ============================================================

@bot.command(name="scan")
async def scan_command(ctx):

    await ctx.send(
        "🔎 Running complete FOMO + DROP scan..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    if not results:

        await ctx.send(
            "❌ Scanner returned no results."
        )
        return

    best_fomo = max(
        results,
        key=lambda x: x["fomo_score"]
    )

    best_drop = max(
        results,
        key=lambda x: x["drop_score"]
    )

    embed = discord.Embed(
        title="🧠 FOMO AI Market Scan",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🚀 Strongest FOMO",
        value=(
            f"**{best_fomo['symbol']}**\n"
            f"Score: **{best_fomo['fomo_score']}/100**\n"
            f"Price: ${best_fomo['price']:,.4f}"
        ),
        inline=False
    )

    embed.add_field(
        name="📉 Strongest Drop Risk",
        value=(
            f"**{best_drop['symbol']}**\n"
            f"Score: **{best_drop['drop_score']}/100**\n"
            f"Price: ${best_drop['price']:,.4f}"
        ),
        inline=False
    )

    embed.add_field(
        name="📊 Coins Scanned",
        value=str(len(results)),
        inline=True
    )

    embed.add_field(
        name="⏱️ Timeframe",
        value=TIMEFRAME,
        inline=True
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# ▶️ !watch COMMAND
# ============================================================

@bot.command(name="watch")
async def watch_command(ctx):

    global scanner_running

    scanner_running = True

    if not fomo_watcher.is_running():
        fomo_watcher.start()

    await ctx.send(
        "🟢 **FOMO AI watcher started.**\n"
        f"Scanning every {SCAN_INTERVAL_MINUTES} minutes."
    )


# ============================================================
# ⏹️ !stopwatch COMMAND
# ============================================================

@bot.command(name="stopwatch")
async def stopwatch_command(ctx):

    global scanner_running

    scanner_running = False

    if fomo_watcher.is_running():
        fomo_watcher.cancel()

    await ctx.send(
        "🔴 **FOMO AI watcher stopped.**"
    )


# ============================================================
# 🧪 !testsignal COMMAND
# ============================================================

@bot.command(name="testsignal")
async def test_signal(ctx):

    result = analyze_symbol(
        "BTC/USDT"
    )

    if result is None:

        await ctx.send(
            "❌ Could not analyze BTC."
        )
        return

    embed = create_signal_embed(
        result
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# 🚀 START BOT
# ============================================================

if __name__ == "__main__":

    if not DISCORD_TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN is missing from .env"
        )

    bot.run(
        DISCORD_TOKEN
    )
