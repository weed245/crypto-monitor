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

ALERT_CHANNEL_ID = int(
    os.getenv("ALERT_CHANNEL_ID", "0")
)

# Main scanner
TIMEFRAME = "1h"

FAST_MA = 9
SLOW_MA = 21

RSI_PERIOD = 14
VOLUME_PERIOD = 20
ATR_PERIOD = 14

# Main scanner frequency
MAIN_SCAN_MINUTES = 15

# Early scanner
EARLY_TIMEFRAME = "5m"
EARLY_SCAN_MINUTES = 5

# Alert thresholds
FOMO_ALERT_SCORE = 75
DROP_ALERT_SCORE = 75

EARLY_FOMO_SCORE = 80
EARLY_DROP_SCORE = 80

# Existing trade settings
TRADE_AMOUNT = 0.001
STOP_LOSS_PERCENT = 0.02
TAKE_PROFIT_PERCENT = 0.04


# ============================================================
# 🪙 COINS
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
# 📡 BINANCE
# ============================================================

exchange = ccxt.binance({
    "apiKey": EXCHANGE_API_KEY,
    "secret": EXCHANGE_SECRET_KEY,
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})

# KEEP SANDBOX / PAPER MODE
exchange.set_sandbox_mode(True)


# ============================================================
# 🧠 ALERT MEMORY
# ============================================================

last_alerts = {}


# ============================================================
# 📊 FETCH MARKET DATA
# ============================================================

def fetch_market_data(
    symbol,
    timeframe,
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
            f"{symbol} {timeframe} data error: {e}"
        )

        return None


# ============================================================
# 📈 INDICATORS
# ============================================================

def calculate_indicators(df):

    if df is None or len(df) < 60:
        return None

    df = df.copy()

    # --------------------------------------------------------
    # Moving averages
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

    df["rsi"] = 100 - (
        100 / (1 + rs)
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

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # --------------------------------------------------------
    # Volume
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
    # ATR
    # --------------------------------------------------------

    previous_close = df["close"].shift(1)

    tr1 = (
        df["high"] -
        df["low"]
    )

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

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    df["momentum_1"] = (
        df["close"].pct_change(1) * 100
    )

    df["momentum_4"] = (
        df["close"].pct_change(4) * 100
    )

    df["momentum_24"] = (
        df["close"].pct_change(24) * 100
    )

    # --------------------------------------------------------
    # Candle movement
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
# 🧠 MAIN FOMO ANALYSIS
# ============================================================

def analyze_symbol(symbol):

    df = fetch_market_data(
        symbol,
        TIMEFRAME,
        150
    )

    if df is None:
        return None

    df = calculate_indicators(df)

    if df is None:
        return None

    # Last completed candle
    candle = df.iloc[-2]
    previous = df.iloc[-3]

    price = float(candle["close"])

    fomo_score = 0
    drop_score = 0

    up_reasons = []
    down_reasons = []

    # --------------------------------------------------------
    # MA TREND
    # --------------------------------------------------------

    if candle["fast_ma"] > candle["slow_ma"]:

        fomo_score += 15

        up_reasons.append(
            "9 MA above 21 MA"
        )

    elif candle["fast_ma"] < candle["slow_ma"]:

        drop_score += 15

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
        > candle["slow_ma"]
    )

    bearish_cross = (
        previous["fast_ma"]
        >= previous["slow_ma"]
        and
        candle["fast_ma"]
        < candle["slow_ma"]
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

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

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
            "Potentially overextended"
        )

    elif rsi < 40:

        drop_score += 15

        down_reasons.append(
            f"RSI weak ({rsi:.1f})"
        )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if candle["macd"] > candle["macd_signal"]:

        fomo_score += 15

        up_reasons.append(
            "MACD bullish"
        )

    else:

        drop_score += 15

        down_reasons.append(
            "MACD bearish"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_ratio = float(
        candle["volume_ratio"]
    )

    if volume_ratio >= 2:

        fomo_score += 15
        drop_score += 10

        up_reasons.append(
            f"Volume spike {volume_ratio:.1f}x"
        )

        down_reasons.append(
            f"Heavy volume {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 1.5:

        fomo_score += 12

        up_reasons.append(
            f"Strong volume {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 1.2:

        fomo_score += 7

        up_reasons.append(
            f"Volume above average {volume_ratio:.1f}x"
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

        fomo_score += 8

        up_reasons.append(
            f"Momentum +{momentum_1:.2f}%"
        )

    elif momentum_1 < -0.5:

        drop_score += 8

        down_reasons.append(
            f"Momentum {momentum_1:.2f}%"
        )

    if momentum_4 > 1:

        fomo_score += 7

        up_reasons.append(
            f"4h momentum +{momentum_4:.2f}%"
        )

    elif momentum_4 < -1:

        drop_score += 7

        down_reasons.append(
            f"4h momentum {momentum_4:.2f}%"
        )

    if momentum_24 > 3:

        fomo_score += 5

        up_reasons.append(
            f"24h momentum +{momentum_24:.2f}%"
        )

    elif momentum_24 < -3:

        drop_score += 5

        down_reasons.append(
            f"24h momentum {momentum_24:.2f}%"
        )

    # --------------------------------------------------------
    # CANDLE STRENGTH
    # --------------------------------------------------------

    candle_change = float(
        candle["candle_change"]
    )

    if candle_change > 1:

        fomo_score += 5

        up_reasons.append(
            f"Bullish candle +{candle_change:.2f}%"
        )

    elif candle_change < -1:

        drop_score += 5

        down_reasons.append(
            f"Bearish candle {candle_change:.2f}%"
        )

    # --------------------------------------------------------
    # ATR VOLATILITY
    # --------------------------------------------------------

    atr = float(candle["atr"])

    atr_percent = (
        atr / price
    ) * 100

    if atr_percent >= 3:

        fomo_score += 5
        drop_score += 5

        up_reasons.append(
            f"High volatility {atr_percent:.1f}%"
        )

        down_reasons.append(
            f"High volatility {atr_percent:.1f}%"
        )

    # --------------------------------------------------------
    # FINAL SCORES
    # --------------------------------------------------------

    fomo_score = min(
        100,
        max(0, fomo_score)
    )

    drop_score = min(
        100,
        max(0, drop_score)
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
        "momentum_1": momentum_1,
        "momentum_4": momentum_4,
        "momentum_24": momentum_24,
        "atr_percent": atr_percent,
        "up_reasons": up_reasons,
        "down_reasons": down_reasons
    }


# ============================================================
# ⚡ EARLY 5-MINUTE ANALYSIS
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

    fomo_score = 0
    drop_score = 0

    up_reasons = []
    down_reasons = []

    # --------------------------------------------------------
    # 5-MINUTE PRICE MOVEMENT
    # --------------------------------------------------------

    move = float(
        candle["candle_change"]
    )

    if move >= 1:

        fomo_score += 25

        up_reasons.append(
            f"5m price jump +{move:.2f}%"
        )

    elif move <= -1:

        drop_score += 25

        down_reasons.append(
            f"5m price drop {move:.2f}%"
        )

    # --------------------------------------------------------
    # VOLUME SPIKE
    # --------------------------------------------------------

    volume_ratio = float(
        candle["volume_ratio"]
    )

    if volume_ratio >= 3:

        fomo_score += 30

        up_reasons.append(
            f"Extreme volume {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 2:

        fomo_score += 20

        up_reasons.append(
            f"Volume spike {volume_ratio:.1f}x"
        )

    if volume_ratio >= 3 and move < 0:

        drop_score += 25

        down_reasons.append(
            f"Heavy selling volume {volume_ratio:.1f}x"
        )

    elif volume_ratio >= 2 and move < 0:

        drop_score += 15

        down_reasons.append(
            f"Elevated selling volume {volume_ratio:.1f}x"
        )

    # --------------------------------------------------------
    # PRICE ACCELERATION
    # --------------------------------------------------------

    previous_move = float(
        previous["candle_change"]
    )

    if (
        move > 0
        and
        move > previous_move
    ):

        fomo_score += 15

        up_reasons.append(
            "Upside acceleration increasing"
        )

    if (
        move < 0
        and
        move < previous_move
    ):

        drop_score += 15

        down_reasons.append(
            "Downside acceleration increasing"
        )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if (
        candle["macd"] >
        candle["macd_signal"]
        and
        candle["macd"] >= previous["macd"]
    ):

        fomo_score += 15

        up_reasons.append(
            "MACD momentum increasing"
        )

    elif (
        candle["macd"] <
        candle["macd_signal"]
        and
        candle["macd"] <= previous["macd"]
    ):

        drop_score += 15

        down_reasons.append(
            "MACD momentum decreasing"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = float(
        candle["rsi"]
    )

    if 50 <= rsi <= 70:

        fomo_score += 10

        up_reasons.append(
            f"RSI bullish {rsi:.1f}"
        )

    elif rsi < 35:

        drop_score += 10

        down_reasons.append(
            f"RSI weak {rsi:.1f}"
        )

    elif rsi > 75:

        drop_score += 8

        down_reasons.append(
            f"RSI heavily overbought {rsi:.1f}"
        )

    # --------------------------------------------------------
    # SCORE LIMIT
    # --------------------------------------------------------

    fomo_score = min(
        100,
        max(0, fomo_score)
    )

    drop_score = min(
        100,
        max(0, drop_score)
    )

    if fomo_score >= drop_score:
        direction = "EARLY PUMP"

    else:
        direction = "EARLY DROP"

    return {
        "symbol": symbol,
        "price": price,
        "fomo_score": fomo_score,
        "drop_score": drop_score,
        "direction": direction,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "move": move,
        "up_reasons": up_reasons,
        "down_reasons": down_reasons
    }


# ============================================================
# 🔎 SCAN MARKET
# ============================================================

def scan_market():

    results = []

    for symbol in SCAN_SYMBOLS:

        try:

            result = analyze_symbol(
                symbol
            )

            if result:
                results.append(result)

        except Exception as e:

            logging.error(
                f"Main scanner {symbol}: {e}"
            )

    return results


# ============================================================
# ⚡ SCAN EARLY MARKET
# ============================================================

def scan_early_market():

    results = []

    for symbol in SCAN_SYMBOLS:

        try:

            result = analyze_early_move(
                symbol
            )

            if result:
                results.append(result)

        except Exception as e:

            logging.error(
                f"Early scanner {symbol}: {e}"
            )

    return results


# ============================================================
# 📢 SEND EMBED
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
# 🚀 MAIN SIGNAL EMBED
# ============================================================

def create_main_embed(result):

    fomo = result["fomo_score"]
    drop = result["drop_score"]

    if fomo >= drop:

        embed = discord.Embed(
            title="🚀 FOMO AI ALERT",
            description=(
                f"**{result['symbol']}**\n"
                "Potential upward momentum detected."
            ),
            color=discord.Color.green()
        )

        reasons = result["up_reasons"]

    else:

        embed = discord.Embed(
            title="📉 DROP AI ALERT",
            description=(
                f"**{result['symbol']}**\n"
                "Potential downward pressure detected."
            ),
            color=discord.Color.red()
        )

        reasons = result["down_reasons"]

    embed.add_field(
        name="💰 Price",
        value=f"${result['price']:,.6f}",
        inline=False
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
        value=f"{result['volume_ratio']:.2f}x",
        inline=True
    )

    embed.add_field(
        name="⚡ 1h",
        value=f"{result['momentum_1']:+.2f}%",
        inline=True
    )

    embed.add_field(
        name="⚡ 4h",
        value=f"{result['momentum_4']:+.2f}%",
        inline=True
    )

    reasons_text = "\n".join(
        f"• {x}"
        for x in reasons[:7]
    )

    embed.add_field(
        name="🧠 Why?",
        value=reasons_text or "No major confirmation.",
        inline=False
    )

    embed.set_footer(
        text="Signal only — not a guaranteed prediction"
    )

    return embed


# ============================================================
# ⚡ EARLY SIGNAL EMBED
# ============================================================

def create_early_embed(result):

    fomo = result["fomo_score"]
    drop = result["drop_score"]

    if fomo >= drop:

        embed = discord.Embed(
            title="⚡ EARLY FOMO DETECTED",
            description=(
                f"**{result['symbol']}**\n"
                "Unusual short-term upward activity detected."
            ),
            color=discord.Color.green()
        )

        reasons = result["up_reasons"]

    else:

        embed = discord.Embed(
            title="⚡ EARLY DROP DETECTED",
            description=(
                f"**{result['symbol']}**\n"
                "Unusual short-term downward activity detected."
            ),
            color=discord.Color.red()
        )

        reasons = result["down_reasons"]

    embed.add_field(
        name="💰 Price",
        value=f"${result['price']:,.6f}",
        inline=False
    )

    embed.add_field(
        name="🔥 FOMO",
        value=f"{fomo}/100",
        inline=True
    )

    embed.add_field(
        name="🔻 Drop",
        value=f"{drop}/100",
        inline=True
    )

    embed.add_field(
        name="📊 Volume",
        value=f"{result['volume_ratio']:.2f}x",
        inline=True
    )

    embed.add_field(
        name="⚡ 5m Move",
        value=f"{result['move']:+.2f}%",
        inline=True
    )

    embed.add_field(
        name="📊 RSI",
        value=f"{result['rsi']:.1f}",
        inline=True
    )

    reasons_text = "\n".join(
        f"• {x}"
        for x in reasons[:6]
    )

    embed.add_field(
        name="🧠 Why?",
        value=reasons_text or "No major confirmation.",
        inline=False
    )

    embed.set_footer(
        text="Early signal — not a guaranteed prediction"
    )

    return embed


# ============================================================
# ⏱️ MAIN AUTOMATIC WATCHER
# ============================================================

@tasks.loop(
    minutes=MAIN_SCAN_MINUTES
)
async def fomo_watcher():

    logging.info(
        "🔎 Running 1h FOMO scan..."
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

        # FOMO alert
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
                    create_main_embed(
                        strongest_fomo
                    )
                )

                last_alerts[key] = True

        # Drop alert
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
                    create_main_embed(
                        strongest_drop
                    )
                )

                last_alerts[key] = True

        # Reset alerts when scores fall
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
                and
                result["fomo_score"] < 60
            ):

                del last_alerts[key]

            elif (
                alert_type == "DROP"
                and
                result["drop_score"] < 60
            ):

                del last_alerts[key]

        logging.info(
            f"✅ 1h scan complete: "
            f"{len(results)} coins."
        )

    except Exception as e:

        logging.error(
            f"Main watcher error: {e}"
        )


# ============================================================
# ⚡ EARLY AUTOMATIC WATCHER
# ============================================================

@tasks.loop(
    minutes=EARLY_SCAN_MINUTES
)
async def early_fomo_watcher():

    logging.info(
        "⚡ Running 5m early FOMO scan..."
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

        # Early FOMO
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
                    create_early_embed(
                        strongest_fomo
                    )
                )

                last_alerts[key] = True

        # Early Drop
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
                    create_early_embed(
                        strongest_drop
                    )
                )

                last_alerts[key] = True

        # Reset early alerts
        for key in list(last_alerts):

            if (
                len(key) != 2
                or
                key[1] not in (
                    "EARLY_FOMO",
                    "EARLY_DROP"
                )
            ):
                continue

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
                alert_type == "EARLY_FOMO"
                and
                result["fomo_score"] < 65
            ):

                del last_alerts[key]

            elif (
                alert_type == "EARLY_DROP"
                and
                result["drop_score"] < 65
            ):

                del last_alerts[key]

        logging.info(
            f"⚡ 5m scan complete: "
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
            "📊 Binance connection successful."
        )

    except Exception as e:

        logging.error(
            f"Binance connection error: {e}"
        )

    if not fomo_watcher.is_running():

        fomo_watcher.start()

    if not early_fomo_watcher.is_running():

        early_fomo_watcher.start()

    logging.info(
        "🚀 Both FOMO scanners are running."
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

        symbol = symbol.upper()

        ticker = await asyncio.to_thread(
            exchange.fetch_ticker,
            symbol
        )

        price = ticker["last"]

        await ctx.send(
            f"📈 Current **{symbol}** price: "
            f"`${price:,.6f}`"
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

    if amount <= 0:

        await ctx.send(
            "❌ Amount must be greater than 0."
        )

        return

    symbol = "BTC/USDT"

    try:

        await ctx.send(
            f"🔄 Processing paper buy "
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

    if amount <= 0:

        await ctx.send(
            "❌ Amount must be greater than 0."
        )

        return

    symbol = "BTC/USDT"

    try:

        await ctx.send(
            f"🔄 Processing paper sell "
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
        "🔎 Scanning for strongest FOMO setups..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    if not results:

        await ctx.send(
            "❌ No market data available."
        )

        return

    results.sort(
        key=lambda x: x["fomo_score"],
        reverse=True
    )

    embed = discord.Embed(
        title="🚀 FOMO AI — Top 5",
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
                f"1h: {result['momentum_1']:+.2f}%"
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
        "🔎 Scanning for strongest drop-risk setups..."
    )

    results = await asyncio.to_thread(
        scan_market
    )

    if not results:

        await ctx.send(
            "❌ No market data available."
        )

        return

    results.sort(
        key=lambda x: x["drop_score"],
        reverse=True
    )

    embed = discord.Embed(
        title="📉 DROP AI — Top 5",
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
                f"1h: {result['momentum_1']:+.2f}%"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# !EARLY
# ============================================================

@bot.command(name="early")
async def early_command(ctx):

    await ctx.send(
        "⚡ Running 5-minute early-move scan..."
    )

    results = await asyncio.to_thread(
        scan_early_market
    )

    if not results:

        await ctx.send(
            "❌ No early market data available."
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

        if result["fomo_score"] >= result["drop_score"]:

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
                f"5m Move: {result['move']:+.2f}%\n"
                f"Volume: {result['volume_ratio']:.1f}x\n"
                f"RSI: {result['rsi']:.1f}"
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
        "🧠 Running full FOMO + DROP scan..."
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
        title="🧠 FOMO AI MARKET SCAN",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🚀 Strongest FOMO",
        value=(
            f"**{best_fomo['symbol']}**\n"
            f"Score: **{best_fomo['fomo_score']}/100**\n"
            f"${best_fomo['price']:,.6f}"
        ),
        inline=False
    )

    embed.add_field(
        name="📉 Strongest Drop Risk",
        value=(
            f"**{best_drop['symbol']}**\n"
            f"Score: **{best_drop['drop_score']}/100**\n"
            f"${best_drop['price']:,.6f}"
        ),
        inline=False
    )

    embed.add_field(
        name="🪙 Coins Scanned",
        value=str(len(results)),
        inline=True
    )

    embed.add_field(
        name="⏱️ Main Timeframe",
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

    started = []

    if not fomo_watcher.is_running():

        fomo_watcher.start()
        started.append("1h")

    if not early_fomo_watcher.is_running():

        early_fomo_watcher.start()
        started.append("5m")

    if started:

        await ctx.send(
            "🟢 **FOMO watchers started:** "
            + ", ".join(started)
        )

    else:

        await ctx.send(
            "🟢 Both FOMO watchers are already running."
        )


# ============================================================
# !STOPWATCH
# ============================================================

@bot.command(name="stopwatch")
async def stopwatch_command(ctx):

    stopped = []

    if fomo_watcher.is_running():

        fomo_watcher.cancel()
        stopped.append("1h")

    if early_fomo_watcher.is_running():

        early_fomo_watcher.cancel()
        stopped.append("5m")

    if stopped:

        await ctx.send(
            "🔴 **FOMO watchers stopped:** "
            + ", ".join(stopped)
        )

    else:

        await ctx.send(
            "🔴 Both watchers are already stopped."
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

    await ctx.send(
        embed=create_main_embed(result)
    )


# ============================================================
# !TESTEARLY
# ============================================================

@bot.command(name="testearly")
async def test_early(ctx):

    await ctx.send(
        "⚡ Analyzing BTC's 5-minute movement..."
    )

    result = await asyncio.to_thread(
        analyze_early_move,
        "BTC/USDT"
    )

    if result is None:

        await ctx.send(
            "❌ Could not analyze BTC."
        )

        return

    await ctx.send(
        embed=create_early_embed(result)
    )


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    if not DISCORD_TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN is missing from Railway Variables."
        )

    bot.run(
        DISCORD_TOKEN
    )
