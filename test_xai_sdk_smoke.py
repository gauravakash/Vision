"""
Inline smoke test: verify xai-sdk Agent Tools API works end-to-end.

Uses x_search + web_search tools to pull live trends from X/Twitter.
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from xai_sdk import AsyncClient
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search


async def main() -> None:
    api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    if not api_key:
        print("✗ XAI_API_KEY missing from environment / .env")
        return

    print(f"Key: {api_key[:8]}... ({len(api_key)} chars)")

    client = AsyncClient(api_key=api_key)
    chat = client.chat.create(
        model=os.getenv("GROK_MODEL_TRENDS", "grok-4"),
        tools=[x_search(), web_search()],
    )
    chat.append(user(
        "Search X/Twitter for the top 3 trending topics in crypto right now. "
        "Return ONLY a JSON array with fields: tag, volume_display, spike_percent, "
        "status, context. No markdown."
    ))

    print("Calling chat.sample() with x_search + web_search tools...")
    response = await chat.sample()
    content = getattr(response, "content", None)
    print("\n--- RAW RESPONSE ---")
    print(content[:2000] if content else "(empty)")
    print("--- END ---")
    usage = getattr(response, "usage", None)
    if usage:
        print(f"\nUsage: {usage}")


if __name__ == "__main__":
    asyncio.run(main())
