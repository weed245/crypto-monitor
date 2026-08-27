import os
import asyncio
import logging

import ccxt
import discord
import pandas as pd

from discord.ext import commands, tasks
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY")
EXCHANGE_SECRET_KEY = os.getenv("EXCHANGE_SECRET_KEY")

ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

TIMEFRAME = "1h"

FAST_MA = 9
SLOW_MA = 21
RSI_PERIOD = 14
VOLUME_PERIOD = 20
ATR_PERIOD = 14

SCAN_INTERVAL_MINUTES = 15

FOMO_ALERT_SCORE = 75
DROP_ALERT_SCORE = 75

TRADE_AMOUNT = 0.001

STOP_LOSS_PERCENT = 0.02
TAKE_PROFIT_PERCENT = 0.04


# ============================================================
# COINS TO SCAN
# ============================================================

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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# BINANCE
# ============================================================

exchange = ccxt.binance({
    "apiKey": EXCHANGE_API_KEY,
    "secret": EXCHANGE_SECRET_KEY,
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})

# KEEP PAPER/TESTNET MODE
exchange.set_sandbox_mode(True)


# ============================================================
# SCANNER STATE
# ============================================================

last_alerts = {}


# ============================================================
# MARKET DATA
# ============================================================

def fetch_market_data(
    symbol,
    timeframe=TIMEFRAME,
    limit=150
):
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
            f"{symbol}: market data error: {e}"
        )
        return None


# ============================================================
# INDICATORS
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
    # RSI
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

    # -------------------------
    # Candle percentage
    # -------------------------

    df["candle_change"] = (
        (
            df["close"] -
            df["open"]
        )
        /
        df["open"]
    ) * 100

    return df


# ============================================================
# FOMO / DROP ANALYSIS
# ============================================================

def analyze_symbol(symbol):

    df = fetch_market_data(symbol)

    if df is None:
        return None

    df = calculate_indicators(df)

    if df is None:
        return None

    # -2 = last completed candle
    # -1 = currently forming candle
    candle = df.iloc[-2]
    previous = df.iloc[-3]

    price = float(candle["close"])

    fomo_score = 0
    drop_score = 0

    up_reasons = []
    down_reasons = []

    # ========================================================
    # MA TREND
    # ========================================================

    if candle["fast_ma"] > candle["slow_ma"]:
        fomo_score += 15
        up_reasons.append(
            "9 MA is above 21 MA"
        )

    elif candle["fast_ma"] < candle["slow_ma"]:
        drop_score += 15
        down_reasons.append(
            "9 MA is below 21 MA"
        )

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
        up_reasons.append(
            "Bullish MA crossover"
        )

    if bearish_cross:
        drop_score += 25
        down_reasons.append(
            "Bearish MA crossover"
        )

    # ========================================================
    # RSI
    # ========================================================

    rsi = float(candle["rsi"])

    if 50 <= rsi < 70:
        fomo_score += 15
        up_reasons.append(
            f"RSI bullish ({rsi:.1f})"
        )

    elif rsi >= 70:
        fomo_score += 8
        drop_score += 5

        up_reasons.append(
            f"RSI overheated ({rsi:.1f})"
        )

        down_reasons.append(
            f"RSI potentially overheated ({rsi:.1f})"
        )

    elif rsi < 40:
        drop_score += 15
        down_reasons.append(
            f"Weak RSI ({rsi:.1f})"
        )

    # ========================================================
    # MACD
    # ========================================================

    macd = float(candle["macd"])
    macd_signal = float(candle["macd_signal"])

    if macd > macd_signal:
        fomo_score += 15
        up_reasons.append(
            "MACD bullish"
        )

    else:
        drop_score += 15
        down_reasons.append(
            "MACD bearish"
        )

    # ========================================================
    # VOLUME
    # ========================================================

    volume_ratio = float(
        candle["volume_ratio"]
    )

    if volume_ratio >= 2.0:

        fomo_score += 15
        drop_score += 10

        up_reasons.append(
            f"Major volume spike ({volume_ratio:.1f}x)"
        )

        down_reasons.append(
            f"Major volume spike ({volume_ratio:.1f}x)"
        )

    elif volume_ratio >= 1.5:

        fomo_score += 12
        drop_score += 7

        up_reasons.append(
            f"Strong volume ({volume_ratio:.1f}x)"
        )

    elif volume_ratio >= 1.2:

        fomo_score += 7

        up_reasons.append(
            f"Above-average volume ({volume_ratio:.1f}x)"
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

    momentum_24h = float(
        candle["momentum_24h"]
    )

    if momentum_1h > 0.5:
        fomo_score += 8
        up_reasons.append(
            f"1h momentum +{momentum_1h:.2f}%"
        )

    elif momentum_1h < -0.5:
        drop_score += 8
        down_reasons.append(
            f"1h momentum {momentum_1h:.2f}%"
        )

    if momentum_4h > 1:
        fomo_score += 7
        up_reasons.append(
            f"4h momentum +{momentum_4h:.2f}%"
        )

    elif momentum_4h < -1:
        drop_score += 7
        down_reasons.append(
            f"4h momentum {momentum_4h:.2f}%"
        )

    if momentum_24h > 3:
        fomo_score += 5
        up_reasons.append(
            f"24h momentum +{momentum_24h:.2f}%"
        )

    elif momentum_24h < -3:
        drop_score += 5
        down_reasons.append(
            f"24h momentum {momentum_24h:.2f}%"
        )

    # ========================================================
    # CANDLE STRENGTH
    # ========================================================

    candle_change = float(
        candle["candle_change"]
    )

    if candle_change > 1:
        fomo_score += 5
        up_reasons.append(
            f"Strong bullish candle +{candle_change:.2f}%"
        )

    elif candle_change < -1:
        drop_score += 5
        down_reasons.append(
            f"Strong bearish candle {candle_change:.2f}%"
        )

    # ========================================================
    # VOLATILITY
    # ========================================================

    atr = float(candle["atr"])

    atr_percent = (
        atr / price
    ) * 100

    if atr_percent >= 3:
        fomo_score += 5
        drop_score += 5

        up_reasons.append(
            f"High volatility ({atr_percent:.1f}%)"
        )

        down_reasons.append(
            f"High volatility ({atr_percent:.1f}%)"
        )

    # ========================================================
    # FINAL SCORES
    # ========================================================

    fomo_score = max(
        0,
        min(100, fomo_score)
    )

    drop_score = max(
        0,
        min(100, drop_score)
    )

    if fomo_score > drop_score:
        direction = "PUMP WATCH"
    elif drop_score > fomo_score:
        direction = "DROP WATCH"
    else:
        direction = "NEUTRAL"

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
        "momentum_24h": momentum_24h,

        "atr_percent": atr_percent,

        "ma_fast": float(candle["fast_ma"]),
        "ma_slow": float(candle["slow_ma"]),

        "macd": macd,
        "macd_signal": macd_signal,

        "up_reasons": up_reasons,
        "down_reasons": down_reasons
    }


# ============================================================
# SCAN MARKET
# ============================================================

def scan_market():

    results = []

    for symbol in SCAN_SYMBOLS:

        result = analyze_symbol(symbol)

        if result:
            results.append(result)

    return results


# ============================================================
# SIGNAL EMBED
# ============================================================

def create_signal_embed(result):

    fomo = result["fomo_score"]
    drop = result["drop_score"]

    if fomo >= drop:

        title = "🚀 FOMO AI ALERT"

        description = (
            f"**{result['symbol']}**\n"
            "Potential upward momentum detected."
        )

        color = discord.Color.green()

        reasons = result["up_reasons"]

    else:

        title = "📉 DROP AI ALERT"

        description = (
            f"**{result['symbol']}**\n"
            "Potential downward pressure detected."
        )

        color = discord.Color.red()

        reasons = result["down_reasons"]

    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )

    embed.add_field(
        name="💰 Price",
        value=f"${result['price']:,.6f}",
        inline=False
    )

    embed.add_field(
        name="🚀 FOMO Score",
        value=f"{fomo}/100",
        inline=True
    )

    embed.add_field(
        name="📉 Drop Score",
        value=f"{drop}/100",
        inline=True
    )

    embed.add_field(
        name="📊 RSI",
        value=f"{result['rsi']:.1f}",
        inline=True
    )

    embed.add_field(
        name="📊 Volume",
        value=f"{result['volume_ratio']:.2f}x",
        inline=True
    )

    embed.add_field(
        name="⚡ 1h Momentum",
        value=f"{result['momentum_1h']:+.2f}%",
        inline=True
    )

    embed.add_field(
        name="⚡ 4h Momentum",
        value=f"{result['momentum_4h']:+.2f}%",
        inline=True
    )

    reason_text = "\n".join(
        f"• {x}"
        for x in reasons[:7]
    )

    if not reason_text:
        reason_text = "No major confirmation."

    embed.add_field(
        name="🧠 Analysis",
        value=reason_text,
        inline=False
    )

    embed.set_footer(
        text=(
            "Uses closed candles • "
            "Signal is not a guaranteed prediction"
        )
    )

    return embed


# ============================================================
# SEND ALERT
# ============================================================

async def send_signal(result):

    if ALERT_CHANNEL_ID == 0:

        logging.warning(
            "ALERT_CHANNEL_ID is not configured."
        )

        return

    channel = bot.get_channel(
        ALERT_CHANNEL_ID
    )

    if channel is None:

        logging.warning(
            "Alert channel could not be found."
        )

        return

    embed = create_signal_embed(
        result
    )

    await channel.send(
        embed=embed
    )


# ============================================================
# AUTOMATIC WATCHER
# ============================================================

@tasks.loop(
    minutes=SCAN_INTERVAL_MINUTES
)
async def fomo_watcher():

    logging.info(
        "🔎 Starting automatic market scan..."
    )

    try:

        results = await asyncio.to_thread(
            scan_market
        )

        if not results:

            logging.warning(
                "Scanner returned no results."
            )

            return

        strongest_fomo = max(
            results,
            key=lambda x: x["fomo_score"]
        )

        strongest_drop = max(
            results,
            key=lambda x: x["drop_score"]
        )

        # -------------------------
        # FOMO alert
        # -------------------------

        if (
            strongest_fomo["fomo_score"]
            >= FOMO_ALERT_SCORE
        ):

            key = (
                strongest_fomo["symbol"],
                "FOMO"
            )

            if key not in last_alerts:

                await send_signal(
                    strongest_fomo
                )

                last_alerts[key] = True

        # -------------------------
        # Drop alert
        # -------------------------

        if (
            strongest_drop["drop_score"]
            >= DROP_ALERT_SCORE
        ):

            key = (
                strongest_drop["symbol"],
                "DROP"
            )

            if key not in last_alerts:

                await send_signal(
                    strongest_drop
                )

                last_alerts[key] = True

        # -------------------------
        # Reset alerts
        # -------------------------

        for key in list(last_alerts):

            symbol, alert_type = key

            result = next(
                (
                    x for x in results
                    if x["symbol"] == symbol
                ),
                None
            )

            if result is None:
                continue

            if (
                alert_type == "FOMO"
                and result["fomo_score"] < 60
            ):

                del last_alerts[key]

            elif (
                alert_type == "DROP"
                and result["drop_score"] < 60
            ):

                del last_alerts[key]

        logging.info(
            f"✅ Scan complete: "
            f"{len(results)} coins analyzed."
        )

    except Exception as e:

        logging.error(
            f"Watcher error: {e}"
        )


# ============================================================
# BOT READY
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
            f"Binance connection error: {e}"
        )

    if not fomo_watcher.is_running():

        fomo_watcher.start()

        logging.info(
            "🔎 Automatic FOMO watcher started."
        )


# ============================================================
# !PRICE
# ============================================================

@bot.command(name="price")
async def get_price(
    ctx,
    symbol: str = "BTC/USDT"
):

    try:

        ticker = await asyncio.to_thread(
            exchange.fetch_ticker,
            symbol.upper()
        )

        price = ticker["last"]

        await ctx.send(
            f"📈 Current **{symbol.upper()}** price: "
            f"`${price:,.2f}`"
        )

    except Exception as e:

        await ctx.send(
            f"❌ Error fetching price: `{e}`"
        )


# ============================================================
# !BUY
# ============================================================

@bot.command(name="buy")
async def buy_bitcoin(
    ctx,
    amount: float
):

    symbol = "BTC/USDT"

    if amount <= 0:

        await ctx.send(
            "❌ Amount must be greater than 0."
        )

        return

    try:

        await ctx.send(
            f"🔄 Processing paper buy order "
            f"for `{amount}` BTC..."
        )

        order = await asyncio.to_thread(
            exchange.create_market_buy_order,
            symbol,
            amount
        )

        embed = discord.Embed(
            title="✅ Paper Buy Executed",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Symbol",
            value=order["symbol"],
            inline=True
        )

        embed.add_field(
            name="Amount",
            value=f"{order['amount']} BTC",
            inline=True
        )

        embed.add_field(
            name="Status",
            value=str(
                order.get("status", "unknown")
            ).upper(),
            inline=True
        )

        await ctx.send(
            embed=embed
        )

    except Exception as e:

        await ctx.send(
            f"❌ Order Failed: `{e}`"
        )


# ============================================================
# !SELL
# ============================================================

@bot.command(name="sell")
async def sell_bitcoin(
    ctx,
    amount: float
):

    symbol = "BTC/USDT"

    if amount <= 0:

        await ctx.send(
            "❌ Amount must be greater than 0."
        )

        return

    try:

        await ctx.send(
            f"🔄 Processing paper sell order "
            f"for `{amount}` BTC..."
        )

        order = await asyncio.to_thread(
            exchange.create_market_sell_order,
            symbol,
            amount
        )

        embed = discord.Embed(
            title="✅ Paper Sell Executed",
            color=discord.Color.red()
        )

        embed.add_field(
            name="Symbol",
            value=order["symbol"],
            inline=True
        )

        embed.add_field(
            name="Amount",
            value=f"{order['amount']} BTC",
            inline=True
        )

        embed.add_field(
            name="Status",
            value=str(
                order.get("status", "unknown")
            ).upper(),
            inline=True
        )

        await ctx.send(
            embed=embed
        )

    except Exception as e:

        await ctx.send(
            f"❌ Order Failed: `{e}`"
        )


# ============================================================
# !BALANCE
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
            "💰 **Paper Wallet Balance**\n"
            f"• **USDT:** ${usdt_free:,.2f}\n"
            f"• **BTC:** {btc_free} BTC"
        )

    except Exception as e:

        await ctx.send(
            f"❌ Error fetching balance: `{e}`"
        )


# ============================================================
# !FOMO
# ============================================================

@bot.command(name="fomo")
async def fomo_command(ctx):

    await ctx.send(
        "🔎 Scanning for the strongest FOMO setups..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    results = sorted(
        results,
        key=lambda x: x["fomo_score"],
        reverse=True
    )[:5]

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
                f"💰 ${result['price']:,.6f}\n"
                f"RSI: {result['rsi']:.1f} | "
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"1h: {result['momentum_1h']:+.2f}% | "
                f"4h: {result['momentum_4h']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# !DROPS
# ============================================================

@bot.command(name="drops")
async def drops_command(ctx):

    await ctx.send(
        "🔎 Scanning for strongest downward-risk setups..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    results = sorted(
        results,
        key=lambda x: x["drop_score"],
        reverse=True
    )[:5]

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
                f"💰 ${result['price']:,.6f}\n"
                f"RSI: {result['rsi']:.1f} | "
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"1h: {result['momentum_1h']:+.2f}% | "
                f"4h: {result['momentum_4h']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# !SCAN
# ============================================================

@bot.command(name="scan")
async def scan_command(ctx):

    await ctx.send(
        "🔎 Running full FOMO + DROP scan..."
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
            f"Price: ${best_fomo['price']:,.6f}"
        ),
        inline=False
    )

    embed.add_field(
        name="📉 Strongest Drop Risk",
        value=(
            f"**{best_drop['symbol']}**\n"
            f"Score: **{best_drop['drop_score']}/100**\n"
            f"Price: ${best_drop['price']:,.6f}"
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
# !WATCH
# ============================================================

@bot.command(name="watch")
async def watch_command(ctx):

    if not fomo_watcher.is_running():

        fomo_watcher.start()

        await ctx.send(
            f"🟢 **FOMO watcher started.**\n"
            f"Scanning every "
            f"{SCAN_INTERVAL_MINUTES} minutes."
        )

    else:

        await ctx.send(
            "🟢 FOMO watcher is already running."
        )


# ============================================================
# !STOPWATCH
# ============================================================

@bot.command(name="stopwatch")
async def stopwatch_command(ctx):

    if fomo_watcher.is_running():

        fomo_watcher.cancel()

        await ctx.send(
            "🔴 **FOMO watcher stopped.**"
        )

    else:

        await ctx.send(
            "🔴 FOMO watcher is already stopped."
        )


# ============================================================
# !TESTSIGNAL
# ============================================================

@bot.command(name="testsignal")
async def test_signal(ctx):

    await ctx.send(
        "🧠 Analyzing BTC..."
    )

    result = await asyncio.to_thread(
        analyze_symbol,
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
# START
# ============================================================

if __name__ == "__main__":

    if not DISCORD_TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN is missing from .env/Railway Variables"
        )

    bot.run(
        DISCORD_TOKEN
    )
