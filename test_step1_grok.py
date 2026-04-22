"""
Step 1: Verify Grok API connection.

Uses the same env var this project already uses (XAI_API_KEY).
Falls back to GROK_API_KEY if present so the same test works either way.

Run:  python test_step1_grok.py
"""

import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

API_KEY = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
MODEL = os.getenv("XAI_MODEL_TEST", "grok-3")


async def test() -> None:
    print("Step 1: Grok API test...")
    print(f"  model={MODEL}")
    print(f"  key_set={'yes' if API_KEY else 'NO'}")

    if not API_KEY:
        print("✗ No API key. Set XAI_API_KEY in .env")
        return

    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url="https://api.x.ai/v1",
    )

    try:
        r = await client.chat.completions.create(
            model=MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": "Say hello"}],
        )
        print("✓ Grok connected:")
        print(f"  {r.choices[0].message.content}")
    except Exception as e:
        print(f"✗ Grok failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(test())
