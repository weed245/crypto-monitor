import aiohttp


TOKEN_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)

TOKEN_PAIRS_URL = (
    "https://api.dexscreener.com/latest/dex/tokens/"
)


DEFAULT_MIN_LIQUIDITY = 1000
DEFAULT_MIN_VOLUME = 500

DEFAULT_CHAINS = {
    "solana",
    "ethereum",
    "base",
    "bsc",
    "arbitrum",
    "polygon"
}


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


def token_passes_filter(
    profile,
    market,
    min_liquidity,
    min_volume,
    allowed_chains
):

    chain_id = profile.get(
        "chainId",
        ""
    ).lower()

    if chain_id not in allowed_chains:
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

    if liquidity < min_liquidity:
        return False

    if volume < min_volume:
        return False

    return True


async def get_token_details(
    profiles,
    min_liquidity=DEFAULT_MIN_LIQUIDITY,
    min_volume=DEFAULT_MIN_VOLUME,
    allowed_chains=None
):

    if allowed_chains is None:

        allowed_chains = DEFAULT_CHAINS

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
                market,
                min_liquidity,
                min_volume,
                allowed_chains
            ):
                continue

            results.append({
                "profile": profile,
                "market": market
            })

    return results
