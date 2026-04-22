"""
Step 3: Verify single-draft generation.

Run:  python test_step3_draft.py
"""

import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

API_KEY = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
MODEL = os.getenv("XAI_MODEL_TEST", "grok-3")

SYSTEM_PROMPT = """
You are writing a single tweet.

RULES:
- English only
- No emojis
- No exclamation marks
- Max 18 words per sentence
- Take a clear stance
- Max 280 characters

Write ONLY the tweet. Nothing else.
""".strip()


async def test() -> None:
    print("Step 3: Draft generation test...")
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
            max_tokens=200,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Topic: Arsenal Crisis\n"
                        "Context: Arsenal lost 1-2 to Bournemouth, "
                        "third defeat in four games.\n\n"
                        "Write ONE tweet about this.\n"
                        "Tone: Analytical\n"
                        "Style: Opinion-first"
                    ),
                },
            ],
        )
        draft = (r.choices[0].message.content or "").strip()
        print("✓ Draft generated:")
        print(f"  '{draft}'")
        print(f"  Length: {len(draft)} chars")
    except Exception as e:
        print(f"✗ Draft failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(test())
