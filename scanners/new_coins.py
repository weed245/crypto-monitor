import aiohttp


TOKEN_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
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
                    f"Token API returned "
                    f"{response.status}: {text}"
                )

            data = await response.json()

            if not isinstance(data, list):
                return []

            return data
