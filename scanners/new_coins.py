import aiohttp


TOKEN_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)

TOKEN_PAIRS_URL = (
    "https://api.dexscreener.com/latest/dex/tokens/"
)


# --------------------------------------------------
# FILTER SETTINGS
# --------------------------------------------------

MIN_LIQUIDITY_USD = 1000
MIN_VOLUME_24H_USD = 500

ALLOWED_CHAINS = {
    "solana",
    "ethereum",
    "base",
    "bsc",
    "arbitrum",
    "polygon"
}


# --------------------------------------------------
# GET TOKEN PROFILES
# --------------------------------------------------

async def get_latest_token_profiles():

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            TOKEN_PROFILES_URL
        ) as response:

            if response.status != 200:

                text = await response.text()

                raise RuntimeError(
                    f"Token profile API returned "
                    f"{response.status}: {text}"
                )

            data = await response.json()

            if not isinstance(data, list):
                return []

            return data


# --------------------------------------------------
# GET MARKET DATA
# --------------------------------------------------

async def get_token_market_data(
    session,
    chain_id,
    token_address
):

    url = (
        f"{TOKEN_PAIRS_URL}"
        f"{token_address}"
    )

    try:

        async with session.get(
            url
        ) as response:

            if response.status != 200:
                return None

            data = await response.json()

            pairs = data.get(
                "pairs",
                []
            )

            if not pairs:
                return None

            # Prefer the pair with the
            # highest reported liquidity.
            pairs.sort(
                key=lambda pair: (
                    pair.get(
                        "liquidity",
                        {}
                    ).get(
                        "usd",
                        0
                    ) or 0
                ),
                reverse=True
            )

            return pairs[0]

    except Exception as error:

        print(
            f"Market data error for "
            f"{token_address}: {error}"
        )

        return None


# --------------------------------------------------
# FILTER TOKEN
# --------------------------------------------------

def token_passes_filter(
    profile,
    market
):

    chain_id = profile.get(
        "chainId",
        ""
    ).lower()

    if chain_id not in ALLOWED_CHAINS:
        return False

    if not market:
        return False

    liquidity = (
        market.get(
            "liquidity",
            {}
        ).get(
            "usd"
        ) or 0
    )

    volume = (
        market.get(
            "volume",
            {}
        ).get(
            "h24"
        ) or 0
    )

    if liquidity < MIN_LIQUIDITY_USD:
        return False

    if volume < MIN_VOLUME_24H_USD:
        return False

    return True


# --------------------------------------------------
# GET FILTERED TOKEN DETAILS
# --------------------------------------------------

async def get_token_details(
    profiles
):

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    results = []

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        for profile in profiles:

            chain_id = profile.get(
                "chainId",
                "unknown"
            )

            token_address = profile.get(
                "tokenAddress"
            )

            if not token_address:
                continue

            market = await get_token_market_data(
                session,
                chain_id,
                token_address
            )

            if not token_passes_filter(
                profile,
                market
            ):
                continue

            results.append({
                "profile": profile,
                "market": market
            })

    return results
