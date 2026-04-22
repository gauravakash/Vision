"""
Step 2: Verify trend fetching — xai-sdk Agent Tools returns parseable JSON.

Uses x_search + web_search via xai-sdk (replaces the deprecated
search_parameters path that returned 410).

Run:  python test_step2_trends.py
"""

import asyncio
import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

from xai_sdk import AsyncClient
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search

API_KEY = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
MODEL = os.getenv("GROK_MODEL_TRENDS", "grok-4")


async def test() -> None:
    print("Step 2: Trend fetch test (xai-sdk Agent Tools)...")
    if not API_KEY:
        print("✗ No API key. Set XAI_API_KEY in .env")
        return

    print(f"  model={MODEL}  key={API_KEY[:8]}... ({len(API_KEY)} chars)")

    client = AsyncClient(api_key=API_KEY)
    chat = client.chat.create(
        model=MODEL,
        tools=[x_search(), web_search()],
    )
    chat.append(user(
        "Search X/Twitter RIGHT NOW for the top 3 trending topics related to: "
        "football, premier league, f1.\n\n"
        "Return ONLY a JSON array. No markdown, no prose.\n\n"
        "[\n"
        '  {"tag": "#TopicName", "context": "what is happening", '
        '"volume_display": "2.4M", "volume_numeric": 2400000, '
        '"spike_percent": 45.0, "status": "rising"}\n'
        "]"
    ))

    try:
        response = await chat.sample()
    except Exception as e:
        print(f"✗ Trend fetch failed: {type(e).__name__}: {e}")
        return

    raw = getattr(response, "content", None) or ""
    print("\nRaw response (first 600 chars):")
    print(raw[:600])

    match = re.search(r"\[[\s\S]*\]", raw)
    if not match:
        print("\n✗ Could not locate JSON array in response.")
        return
    try:
        topics = json.loads(match.group())
    except json.JSONDecodeError as je:
        print(f"\n✗ JSON parse failed: {je}")
        return

    if not isinstance(topics, list):
        print(f"\n✗ Parsed value is not a list: {type(topics).__name__}")
        return

    print(f"\n✓ Parsed {len(topics)} topics:")
    for t in topics:
        tag = t.get("tag") or t.get("topic_tag") or "?"
        ctx = (t.get("context") or "")[:60]
        spike = t.get("spike_percent")
        print(f"  - {tag}  spike={spike}  :: {ctx}")


if __name__ == "__main__":
    asyncio.run(test())
