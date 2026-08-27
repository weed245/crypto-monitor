import os
import asyncio
import logging
import time

import ccxt
import discord
import pandas as pd

from discord.ext import commands, tasks
from dotenv import load_dotenv


# ============================================================
# ⚙️ ENVIRONMENT
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

ALERT_CHANNEL_ID = int(
    os.getenv("ALERT_CHANNEL_ID", "0")
)


# ============================================================
# ⚙️ SETTINGS
# ============================================================

MAIN_TIMEFRAME = "1h"
EARLY_TIMEFRAME = "5m"

MAIN_SCAN_MINUTES = 15
EARLY_SCAN_MINUTES = 5

FAST_MA = 9
SLOW_MA = 21

RSI_PERIOD = 14
VOLUME_PERIOD = 20

FOMO_ALERT_SCORE = 75
DROP_ALERT_SCORE = 75

EARLY_FOMO_SCORE = 70
EARLY_DROP_SCORE = 70

TRADE_SYMBOL = "BTC/USD"

# !buy 0.001 = 0.001 BTC
DEFAULT_TRADE_AMOUNT = 0.001


# ============================================================
# 🪙 KRAKEN COINS
# ============================================================

SCAN_SYMBOLS = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "XRP/USD",
    "DOGE/USD",
    "ADA/USD",
    "AVAX/USD",
    "LINK/USD",
    "LTC/USD",
    "DOT/USD",
    "SUI/USD",
    "BNB/USD",
]


# ============================================================
# 📝 LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


# ============================================================
# 🤖 DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# 🐙 KRAKEN CONNECTION
# ============================================================

exchange = ccxt.kraken({
    "apiKey": KRAKEN_API_KEY,
    "secret": KRAKEN_API_SECRET,
    "enableRateLimit": True,
})


# ============================================================
# 🧠 ALERT MEMORY
# ============================================================

last_alerts = {}


# ============================================================
# 📡 MARKET DATA
# ============================================================

def fetch_market_data(
    symbol,
    timeframe,
    limit=150
):

    try:

        if symbol not in exchange.markets:

            return None

        candles = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit
        )

        if not candles:

            return None

        df = pd.DataFrame(
            candles,
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
            f"Market data error {symbol}: {e}"
        )

        return None


# ============================================================
# 📊 INDICATORS
# ============================================================

def calculate_indicators(df):

    if df is None:
        return None

    if len(df) < 60:
        return None

    df = df.copy()

    # --------------------------------------------------------
    # MOVING AVERAGES
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

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

    df["rsi"] = (
        100 -
        (100 / (1 + rs))
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    ema12 = (
        df["close"]
        .ewm(
            span=12,
            adjust=False
        )
        .mean()
    )

    ema26 = (
        df["close"]
        .ewm(
            span=26,
            adjust=False
        )
        .mean()
    )

    df["macd"] = ema12 - ema26

    df["macd_signal"] = (
        df["macd"]
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    df["avg_volume"] = (
        df["volume"]
        .rolling(VOLUME_PERIOD)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["avg_volume"]
    )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    df["momentum_1"] = (
        df["close"]
        .pct_change(1)
        * 100
    )

    df["momentum_4"] = (
        df["close"]
        .pct_change(4)
        * 100
    )

    df["momentum_24"] = (
        df["close"]
        .pct_change(24)
        * 100
    )

    # --------------------------------------------------------
    # CANDLE CHANGE
    # --------------------------------------------------------

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
# 🚀 MAIN FOMO ANALYZER
# ============================================================

def analyze_symbol(symbol):

    df = fetch_market_data(
        symbol,
        MAIN_TIMEFRAME,
        150
    )

    if df is None:
        return None

    df = calculate_indicators(df)

    if df is None:
        return None

    # Last fully completed candle
    candle = df.iloc[-2]
    previous = df.iloc[-3]

    price = float(candle["close"])

    fomo = 0
    drop = 0

    up_reasons = []
    down_reasons = []

    # --------------------------------------------------------
    # MA TREND
    # --------------------------------------------------------

    if candle["fast_ma"] > candle["slow_ma"]:

        fomo += 15

        up_reasons.append(
            "9 MA above 21 MA"
        )

    else:

        drop += 15

        down_reasons.append(
            "9 MA below 21 MA"
        )

    # --------------------------------------------------------
    # MA CROSS
    # --------------------------------------------------------

    bullish_cross = (
        previous["fast_ma"]
        <= previous["slow_ma"]
        and
        candle["fast_ma"]
        >
        candle["slow_ma"]
    )

    bearish_cross = (
        previous["fast_ma"]
        >= previous["slow_ma"]
        and
        candle["fast_ma"]
        <
        candle["slow_ma"]
    )

    if bullish_cross:

        fomo += 25

        up_reasons.append(
            "Bullish MA crossover"
        )

    if bearish_cross:

        drop += 25

        down_reasons.append(
            "Bearish MA crossover"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = float(candle["rsi"])

    if 50 <= rsi < 70:

        fomo += 15

        up_reasons.append(
            f"RSI bullish {rsi:.1f}"
        )

    elif rsi >= 70:

        fomo += 8

        drop += 5

        up_reasons.append(
            f"RSI overbought {rsi:.1f}"
        )

    elif rsi < 40:

        drop += 15

        down_reasons.append(
            f"RSI weak {rsi:.1f}"
        )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if candle["macd"] > candle["macd_signal"]:

        fomo += 15

        up_reasons.append(
            "MACD bullish"
        )

    else:

        drop += 15

        down_reasons.append(
            "MACD bearish"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_ratio = float(
        candle["volume_ratio"]
    )

    if volume_ratio >= 3:

        fomo += 20

        drop += 15

        up_reasons.append(
            f"Volume {volume_ratio:.1f}x average"
        )

        down_reasons.append(
            f"Heavy volume {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 2:

        fomo += 15

        up_reasons.append(
            f"Volume spike {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 1.5:

        fomo += 10

        up_reasons.append(
            f"Strong volume {volume_ratio:.1f}x"
        )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum_1 = float(
        candle["momentum_1"]
    )

    momentum_4 = float(
        candle["momentum_4"]
    )

    momentum_24 = float(
        candle["momentum_24"]
    )

    if momentum_1 > 0.5:

        fomo += 8

        up_reasons.append(
            f"1-candle momentum +{momentum_1:.2f}%"
        )

    elif momentum_1 < -0.5:

        drop += 8

        down_reasons.append(
            f"1-candle momentum {momentum_1:.2f}%"
        )

    if momentum_4 > 1:

        fomo += 7

        up_reasons.append(
            f"Short momentum +{momentum_4:.2f}%"
        )

    elif momentum_4 < -1:

        drop += 7

        down_reasons.append(
            f"Short momentum {momentum_4:.2f}%"
        )

    if momentum_24 > 3:

        fomo += 5

        up_reasons.append(
            f"24-candle momentum +{momentum_24:.2f}%"
        )

    elif momentum_24 < -3:

        drop += 5

        down_reasons.append(
            f"24-candle momentum {momentum_24:.2f}%"
        )

    # --------------------------------------------------------
    # CANDLE STRENGTH
    # --------------------------------------------------------

    candle_change = float(
        candle["candle_change"]
    )

    if candle_change > 1:

        fomo += 5

        up_reasons.append(
            f"Strong candle +{candle_change:.2f}%"
        )

    elif candle_change < -1:

        drop += 5

        down_reasons.append(
            f"Strong bearish candle {candle_change:.2f}%"
        )

    fomo = min(100, fomo)
    drop = min(100, drop)

    return {
        "symbol": symbol,
        "price": price,
        "fomo_score": fomo,
        "drop_score": drop,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "momentum_1": momentum_1,
        "momentum_4": momentum_4,
        "momentum_24": momentum_24,
        "candle_change": candle_change,
        "up_reasons": up_reasons,
        "down_reasons": down_reasons
    }


# ============================================================
# ⚡ EARLY 5-MINUTE ANALYZER
# ============================================================

def analyze_early_move(symbol):

    df = fetch_market_data(
        symbol,
        EARLY_TIMEFRAME,
        100
    )

    if df is None:
        return None

    df = calculate_indicators(df)

    if df is None:
        return None

    candle = df.iloc[-2]
    previous = df.iloc[-3]

    price = float(candle["close"])

    fomo = 0
    drop = 0

    up_reasons = []
    down_reasons = []

    # --------------------------------------------------------
    # 5M PRICE MOVE
    # --------------------------------------------------------

    move = float(
        candle["candle_change"]
    )

    if move >= 1:

        fomo += 25

        up_reasons.append(
            f"5m move +{move:.2f}%"
        )

    elif move <= -1:

        drop += 25

        down_reasons.append(
            f"5m move {move:.2f}%"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_ratio = float(
        candle["volume_ratio"]
    )

    if volume_ratio >= 5:

        fomo += 30

        up_reasons.append(
            f"Extreme volume {volume_ratio:.1f}x"
        )

        if move < 0:
            drop += 30

            down_reasons.append(
                f"Extreme selling volume {volume_ratio:.1f}x"
            )

    elif volume_ratio >= 3:

        fomo += 25

        up_reasons.append(
            f"Volume spike {volume_ratio:.1f}x"
        )

        if move < 0:
            drop += 25

            down_reasons.append(
                f"Heavy selling volume {volume_ratio:.1f}x"
            )

    elif volume_ratio >= 2:

        fomo += 15

        up_reasons.append(
            f"Volume {volume_ratio:.1f}x average"
        )

        if move < 0:
            drop += 15

    # --------------------------------------------------------
    # ACCELERATION
    # --------------------------------------------------------

    previous_move = float(
        previous["candle_change"]
    )

    if (
        move > 0
        and
        move > previous_move
    ):

        fomo += 15

        up_reasons.append(
            "Upside acceleration"
        )

    if (
        move < 0
        and
        move < previous_move
    ):

        drop += 15

        down_reasons.append(
            "Downside acceleration"
        )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if (
        candle["macd"]
        >
        candle["macd_signal"]
    ):

        fomo += 10

        up_reasons.append(
            "MACD bullish"
        )

    else:

        drop += 10

        down_reasons.append(
            "MACD bearish"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = float(
        candle["rsi"]
    )

    if 50 <= rsi <= 70:

        fomo += 10

        up_reasons.append(
            f"RSI {rsi:.1f}"
        )

    elif rsi < 35:

        drop += 10

        down_reasons.append(
            f"RSI weak {rsi:.1f}"
        )

    elif rsi > 75:

        drop += 8

        down_reasons.append(
            f"RSI overheated {rsi:.1f}"
        )

    fomo = min(100, fomo)
    drop = min(100, drop)

    return {
        "symbol": symbol,
        "price": price,
        "fomo_score": fomo,
        "drop_score": drop,
        "move": move,
        "volume_ratio": volume_ratio,
        "rsi": rsi,
        "up_reasons": up_reasons,
        "down_reasons": down_reasons
    }


# ============================================================
# 🔎 FULL MARKET SCAN
# ============================================================

def scan_market():

    results = []

    for symbol in SCAN_SYMBOLS:

        result = analyze_symbol(symbol)

        if result:

            results.append(result)

    return results


# ============================================================
# ⚡ EARLY MARKET SCAN
# ============================================================

def scan_early_market():

    results = []

    for symbol in SCAN_SYMBOLS:

        result = analyze_early_move(symbol)

        if result:

            results.append(result)

    return results


# ============================================================
# 📢 DISCORD ALERT
# ============================================================

async def send_alert(embed):

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
            "Alert channel not found."
        )

        return

    await channel.send(
        embed=embed
    )


# ============================================================
# 🚀 MAIN ALERT EMBED
# ============================================================

def main_embed(result):

    fomo = result["fomo_score"]
    drop = result["drop_score"]

    if fomo >= drop:

        title = "🚀 FOMO ALERT"
        color = discord.Color.green()

        reasons = result["up_reasons"]

    else:

        title = "📉 DROP ALERT"
        color = discord.Color.red()

        reasons = result["down_reasons"]

    embed = discord.Embed(
        title=title,
        description=(
            f"**{result['symbol']}**"
        ),
        color=color
    )

    embed.add_field(
        name="💰 Price",
        value=f"${result['price']:,.6f}",
        inline=True
    )

    embed.add_field(
        name="🚀 FOMO",
        value=f"{fomo}/100",
        inline=True
    )

    embed.add_field(
        name="📉 Drop",
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
        value=f"{result['volume_ratio']:.1f}x",
        inline=True
    )

    embed.add_field(
        name="⚡ Momentum",
        value=f"{result['momentum_4']:+.2f}%",
        inline=True
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons[:6]
    )

    embed.add_field(
        name="🧠 Signals",
        value=reason_text or "No major signals.",
        inline=False
    )

    embed.set_footer(
        text="AI market signal — not a guaranteed prediction"
    )

    return embed


# ============================================================
# ⚡ EARLY ALERT EMBED
# ============================================================

def early_embed(result):

    if (
        result["fomo_score"]
        >=
        result["drop_score"]
    ):

        title = "🚀 EARLY PUMP"
        color = discord.Color.green()

        score = result["fomo_score"]

        reasons = result["up_reasons"]

    else:

        title = "📉 EARLY DROP"
        color = discord.Color.red()

        score = result["drop_score"]

        reasons = result["down_reasons"]

    embed = discord.Embed(
        title=title,
        description=(
            f"**{result['symbol']}**"
        ),
        color=color
    )

    embed.add_field(
        name="🔥 Score",
        value=f"{score}/100",
        inline=True
    )

    embed.add_field(
        name="💰 Price",
        value=f"${result['price']:,.6f}",
        inline=True
    )

    embed.add_field(
        name="⚡ 5m Move",
        value=f"{result['move']:+.2f}%",
        inline=True
    )

    embed.add_field(
        name="📊 Volume",
        value=f"{result['volume_ratio']:.1f}x",
        inline=True
    )

    embed.add_field(
        name="📊 RSI",
        value=f"{result['rsi']:.1f}",
        inline=True
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons[:6]
    )

    embed.add_field(
        name="🧠 Signals",
        value=reason_text or "No major signals.",
        inline=False
    )

    embed.set_footer(
        text="Early signal — not a guaranteed prediction"
    )

    return embed


# ============================================================
# ⏱️ MAIN WATCHER
# ============================================================

@tasks.loop(minutes=MAIN_SCAN_MINUTES)
async def fomo_watcher():

    logging.info(
        "🔎 Starting main FOMO scan..."
    )

    try:

        results = await asyncio.to_thread(
            scan_market
        )

        if not results:

            return

        strongest_fomo = max(
            results,
            key=lambda x: x["fomo_score"]
        )

        strongest_drop = max(
            results,
            key=lambda x: x["drop_score"]
        )

        # ----------------------------------------------------
        # FOMO
        # ----------------------------------------------------

        if (
            strongest_fomo["fomo_score"]
            >= FOMO_ALERT_SCORE
        ):

            key = (
                strongest_fomo["symbol"],
                "FOMO"
            )

            if key not in last_alerts:

                await send_alert(
                    main_embed(
                        strongest_fomo
                    )
                )

                last_alerts[key] = time.time()

        # ----------------------------------------------------
        # DROP
        # ----------------------------------------------------

        if (
            strongest_drop["drop_score"]
            >= DROP_ALERT_SCORE
        ):

            key = (
                strongest_drop["symbol"],
                "DROP"
            )

            if key not in last_alerts:

                await send_alert(
                    main_embed(
                        strongest_drop
                    )
                )

                last_alerts[key] = time.time()

        # Reset old alerts after 1 hour
        now = time.time()

        for key in list(last_alerts):

            if (
                now -
                last_alerts[key]
                >
                3600
            ):

                del last_alerts[key]

        logging.info(
            f"✅ Main scan complete: "
            f"{len(results)} coins."
        )

    except Exception as e:

        logging.error(
            f"Main watcher error: {e}"
        )


# ============================================================
# ⚡ EARLY WATCHER
# ============================================================

@tasks.loop(minutes=EARLY_SCAN_MINUTES)
async def early_fomo_watcher():

    logging.info(
        "⚡ Starting 5m early scan..."
    )

    try:

        results = await asyncio.to_thread(
            scan_early_market
        )

        if not results:

            return

        strongest_fomo = max(
            results,
            key=lambda x: x["fomo_score"]
        )

        strongest_drop = max(
            results,
            key=lambda x: x["drop_score"]
        )

        # ----------------------------------------------------
        # EARLY PUMP
        # ----------------------------------------------------

        if (
            strongest_fomo["fomo_score"]
            >= EARLY_FOMO_SCORE
        ):

            key = (
                strongest_fomo["symbol"],
                "EARLY_FOMO"
            )

            if key not in last_alerts:

                await send_alert(
                    early_embed(
                        strongest_fomo
                    )
                )

                last_alerts[key] = time.time()

        # ----------------------------------------------------
        # EARLY DROP
        # ----------------------------------------------------

        if (
            strongest_drop["drop_score"]
            >= EARLY_DROP_SCORE
        ):

            key = (
                strongest_drop["symbol"],
                "EARLY_DROP"
            )

            if key not in last_alerts:

                await send_alert(
                    early_embed(
                        strongest_drop
                    )
                )

                last_alerts[key] = time.time()

        logging.info(
            f"⚡ Early scan complete: "
            f"{len(results)} coins."
        )

    except Exception as e:

        logging.error(
            f"Early watcher error: {e}"
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
            "🐙 Kraken connection successful."
        )

        # Verify BTC market
        if TRADE_SYMBOL in exchange.markets:

            logging.info(
                f"💰 {TRADE_SYMBOL} market available."
            )

        else:

            logging.warning(
                f"{TRADE_SYMBOL} not found."
            )

    except Exception as e:

        logging.error(
            f"Kraken connection error: {e}"
        )

    if not fomo_watcher.is_running():

        fomo_watcher.start()

    if not early_fomo_watcher.is_running():

        early_fomo_watcher.start()

    logging.info(
        "🚀 Automatic FOMO watchers started."
    )


# ============================================================
# 💰 !PRICE
# ============================================================

@bot.command(name="price")
async def price_command(
    ctx,
    symbol: str = TRADE_SYMBOL
):

    try:

        symbol = symbol.upper()

        ticker = await asyncio.to_thread(
            exchange.fetch_ticker,
            symbol
        )

        price = ticker["last"]

        await ctx.send(
            f"📈 **{symbol}**: "
            f"${price:,.6f}"
        )

    except Exception as e:

        await ctx.send(
            f"❌ Price error: `{e}`"
        )


# ============================================================
# 🟢 !BUY
# ============================================================

@bot.command(name="buy")
async def buy_command(
    ctx,
    amount: float
):

    if amount <= 0:

        await ctx.send(
            "❌ Amount must be greater than 0."
        )

        return

    try:

        symbol = TRADE_SYMBOL

        ticker = await asyncio.to_thread(
            exchange.fetch_ticker,
            symbol
        )

        price = float(
            ticker["last"]
        )

        estimated_cost = (
            amount * price
        )

        balance = await asyncio.to_thread(
            exchange.fetch_balance
        )

        usd_balance = float(
            balance
            .get("USD", {})
            .get("free", 0)
            or 0
        )

        if estimated_cost > usd_balance:

            await ctx.send(
                "❌ **Insufficient USD balance.**\n"
                f"Needed: `${estimated_cost:,.2f}`\n"
                f"Available: `${usd_balance:,.2f}`"
            )

            return

        formatted_amount = (
            exchange.amount_to_precision(
                symbol,
                amount
            )
        )

        await ctx.send(
            f"🟢 **REAL KRAKEN ORDER**\n"
            f"Buying `{formatted_amount}` BTC\n"
            f"Estimated cost: "
            f"`${estimated_cost:,.2f}`"
        )

        order = await asyncio.to_thread(
            exchange.create_market_buy_order,
            symbol,
            float(formatted_amount)
        )

        embed = discord.Embed(
            title="✅ BUY ORDER EXECUTED",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Symbol",
            value=symbol,
            inline=True
        )

        embed.add_field(
            name="Amount",
            value=f"{formatted_amount} BTC",
            inline=True
        )

        embed.add_field(
            name="Order ID",
            value=str(
                order.get("id", "unknown")
            ),
            inline=False
        )

        embed.add_field(
            name="Status",
            value=str(
                order.get(
                    "status",
                    "unknown"
                )
            ).upper(),
            inline=True
        )

        await ctx.send(
            embed=embed
        )

    except Exception as e:

        logging.error(
            f"BUY ERROR: {e}"
        )

        await ctx.send(
            f"❌ **Buy failed:** `{e}`"
        )


# ============================================================
# 🔴 !SELL
# ============================================================

@bot.command(name="sell")
async def sell_command(
    ctx,
    amount: float
):

    if amount <= 0:

        await ctx.send(
            "❌ Amount must be greater than 0."
        )

        return

    try:

        symbol = TRADE_SYMBOL

        balance = await asyncio.to_thread(
            exchange.fetch_balance
        )

        btc_balance = float(
            balance
            .get("BTC", {})
            .get("free", 0)
            or 0
        )

        if amount > btc_balance:

            await ctx.send(
                "❌ **Insufficient BTC balance.**\n"
                f"Trying to sell: `{amount}` BTC\n"
                f"Available: `{btc_balance}` BTC"
            )

            return

        formatted_amount = (
            exchange.amount_to_precision(
                symbol,
                amount
            )
        )

        await ctx.send(
            f"🔴 **REAL KRAKEN ORDER**\n"
            f"Selling `{formatted_amount}` BTC"
        )

        order = await asyncio.to_thread(
            exchange.create_market_sell_order,
            symbol,
            float(formatted_amount)
        )

        embed = discord.Embed(
            title="✅ SELL ORDER EXECUTED",
            color=discord.Color.red()
        )

        embed.add_field(
            name="Symbol",
            value=symbol,
            inline=True
        )

        embed.add_field(
            name="Amount",
            value=f"{formatted_amount} BTC",
            inline=True
        )

        embed.add_field(
            name="Order ID",
            value=str(
                order.get("id", "unknown")
            ),
            inline=False
        )

        embed.add_field(
            name="Status",
            value=str(
                order.get(
                    "status",
                    "unknown"
                )
            ).upper(),
            inline=True
        )

        await ctx.send(
            embed=embed
        )

    except Exception as e:

        logging.error(
            f"SELL ERROR: {e}"
        )

        await ctx.send(
            f"❌ **Sell failed:** `{e}`"
        )


# ============================================================
# 💵 !BALANCE
# ============================================================

@bot.command(name="balance")
async def balance_command(ctx):

    try:

        balance = await asyncio.to_thread(
            exchange.fetch_balance
        )

        btc = float(
            balance
            .get("BTC", {})
            .get("free", 0)
            or 0
        )

        usd = float(
            balance
            .get("USD", {})
            .get("free", 0)
            or 0
        )

        await ctx.send(
            "💰 **KRAKEN BALANCE**\n"
            f"• USD: `${usd:,.2f}`\n"
            f"• BTC: `{btc:.8f}`"
        )

    except Exception as e:

        await ctx.send(
            f"❌ Balance error: `{e}`"
        )


# ============================================================
# 🚀 !FOMO
# ============================================================

@bot.command(name="fomo")
async def fomo_command(ctx):

    await ctx.send(
        "🔎 Scanning Kraken for FOMO setups..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    if not results:

        await ctx.send(
            "❌ No results."
        )

        return

    results.sort(
        key=lambda x: x["fomo_score"],
        reverse=True
    )

    embed = discord.Embed(
        title="🚀 KRAKEN FOMO — TOP 5",
        color=discord.Color.green()
    )

    for result in results[:5]:

        embed.add_field(
            name=(
                f"{result['symbol']} — "
                f"{result['fomo_score']}/100"
            ),
            value=(
                f"💰 ${result['price']:,.6f}\n"
                f"RSI: {result['rsi']:.1f}\n"
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"Momentum: "
                f"{result['momentum_4']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# 📉 !DROPS
# ============================================================

@bot.command(name="drops")
async def drops_command(ctx):

    await ctx.send(
        "🔎 Scanning Kraken for drop risk..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    if not results:

        await ctx.send(
            "❌ No results."
        )

        return

    results.sort(
        key=lambda x: x["drop_score"],
        reverse=True
    )

    embed = discord.Embed(
        title="📉 KRAKEN DROP RISK — TOP 5",
        color=discord.Color.red()
    )

    for result in results[:5]:

        embed.add_field(
            name=(
                f"{result['symbol']} — "
                f"{result['drop_score']}/100"
            ),
            value=(
                f"💰 ${result['price']:,.6f}\n"
                f"RSI: {result['rsi']:.1f}\n"
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"Momentum: "
                f"{result['momentum_4']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# ⚡ !EARLY
# ============================================================

@bot.command(name="early")
async def early_command(ctx):

    await ctx.send(
        "⚡ Running Kraken 5-minute scanner..."
    )

    results = await asyncio.to_thread(
        scan_early_market
    )

    if not results:

        await ctx.send(
            "❌ No results."
        )

        return

    results.sort(
        key=lambda x: max(
            x["fomo_score"],
            x["drop_score"]
        ),
        reverse=True
    )

    embed = discord.Embed(
        title="⚡ EARLY FOMO SCANNER",
        color=discord.Color.gold()
    )

    for result in results[:5]:

        if (
            result["fomo_score"]
            >=
            result["drop_score"]
        ):

            direction = "🚀 EARLY PUMP"
            score = result["fomo_score"]

        else:

            direction = "📉 EARLY DROP"
            score = result["drop_score"]

        embed.add_field(
            name=(
                f"{direction} | "
                f"{result['symbol']} | "
                f"{score}/100"
            ),
            value=(
                f"💰 ${result['price']:,.6f}\n"
                f"5m: {result['move']:+.2f}%\n"
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"RSI: {result['rsi']:.1f}"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# 🔎 !SCAN
# ============================================================

@bot.command(name="scan")
async def scan_command(ctx):

    await ctx.send(
        "🧠 Running full Kraken scan..."
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
        title="🧠 KRAKEN AI MARKET SCAN",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🚀 Strongest FOMO",
        value=(
            f"**{best_fomo['symbol']}**\n"
            f"Score: "
            f"**{best_fomo['fomo_score']}/100**\n"
            f"${best_fomo['price']:,.6f}"
        ),
        inline=False
    )

    embed.add_field(
        name="📉 Strongest Drop",
        value=(
            f"**{best_drop['symbol']}**\n"
            f"Score: "
            f"**{best_drop['drop_score']}/100**\n"
            f"${best_drop['price']:,.6f}"
        ),
        inline=False
    )

    embed.add_field(
        name="🪙 Coins",
        value=str(len(results)),
        inline=True
    )

    embed.add_field(
        name="⏱️ Main",
        value=MAIN_TIMEFRAME,
        inline=True
    )

    embed.add_field(
        name="⚡ Early",
        value=EARLY_TIMEFRAME,
        inline=True
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# ▶️ !WATCH
# ============================================================

@bot.command(name="watch")
async def watch_command(ctx):

    if not fomo_watcher.is_running():

        fomo_watcher.start()

    if not early_fomo_watcher.is_running():

        early_fomo_watcher.start()

    await ctx.send(
        "🟢 **Automatic scanners running.**\n"
        "• Main scanner: every 15 minutes\n"
        "• Early scanner: every 5 minutes"
    )


# ============================================================
# ⏹️ !STOPWATCH
# ============================================================

@bot.command(name="stopwatch")
async def stopwatch_command(ctx):

    if fomo_watcher.is_running():

        fomo_watcher.cancel()

    if early_fomo_watcher.is_running():

        early_fomo_watcher.cancel()

    await ctx.send(
        "🔴 **Automatic scanners stopped.**"
    )


# ============================================================
# 🧪 !TESTSIGNAL
# ============================================================

@bot.command(name="testsignal")
async def testsignal_command(ctx):

    await ctx.send(
        "🧠 Testing BTC/USD signal..."
    )

    result = await asyncio.to_thread(
        analyze_symbol,
        "BTC/USD"
    )

    if result is None:

        await ctx.send(
            "❌ Could not analyze BTC."
        )

        return

    await ctx.send(
        embed=main_embed(result)
    )


# ============================================================
# 🧪 !TESTEARLY
# ============================================================

@bot.command(name="testearly")
async def testearly_command(ctx):

    await ctx.send(
        "⚡ Testing BTC/USD early signal..."
    )

    result = await asyncio.to_thread(
        analyze_early_move,
        "BTC/USD"
    )

    if result is None:

        await ctx.send(
            "❌ Could not analyze BTC."
        )

        return

    await ctx.send(
        embed=early_embed(result)
    )


# ============================================================
# 🚀 START
# ============================================================

if __name__ == "__main__":

    if not DISCORD_TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN is missing."
        )

    if not KRAKEN_API_KEY:

        raise RuntimeError(
            "KRAKEN_API_KEY is missing."
        )

    if not KRAKEN_API_SECRET:

        raise RuntimeError(
            "KRAKEN_API_SECRET is missing."
        )

    bot.run(
        DISCORD_TOKEN
    )
