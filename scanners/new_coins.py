import aiohttp


TOKEN_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)

TOKEN_PAIRS_URL = (
    "https://api.dexscreener.com/latest/dex/tokens/"
)


async def get_latest_token_profiles():

    timeout = aiohttp.ClientTimeout(total=30)

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

            # Choose the pair with the largest
            # reported liquidity when available.
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

        for token in profiles:

            chain_id = token.get(
                "chainId",
                "unknown"
            )

            token_address = token.get(
                "tokenAddress"
            )

            if not token_address:
                continue

            market = await get_token_market_data(
                session,
                chain_id,
                token_address
            )

            results.append({
                "profile": token,
                "market": market
            })

    return results
