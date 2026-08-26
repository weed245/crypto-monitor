import os
import aiohttp


X_API_URL = "https://api.x.com/2/tweets/search/recent"


async def search_recent_posts(query, max_results=10):
    token = os.getenv("X_BEARER_TOKEN")

    if not token:
        raise RuntimeError(
            "X_BEARER_TOKEN is missing from Railway Variables"
        )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    params = {
        "query": query,
        "max_results": max_results,
        "tweet.fields": "created_at,author_id,text",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            X_API_URL,
            headers=headers,
            params=params,
            timeout=30
        ) as response:

            data = await response.json()

            if response.status != 200:
                raise RuntimeError(
                    f"X API error {response.status}: {data}"
                )

            return data.get("data", [])
