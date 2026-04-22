"""
Step 5: Full pipeline via HTTP — hits the live FastAPI server.

Prerequisite: `uvicorn backend.main:app --reload` is running on port 8000.

Run:  python test_step5_full_run.py
"""

import asyncio
import json
import os
import sys

import httpx

BASE = os.getenv("APP_BASE_URL", "http://localhost:8000")
DESK_ID = int(os.getenv("TEST_DESK_ID", "2"))


async def test() -> None:
    print("Step 5: Full pipeline test...")
    print(f"  base={BASE}  desk_id={DESK_ID}")

    async with httpx.AsyncClient(timeout=120) as client:
        # Health
        try:
            r = await client.get(f"{BASE}/health")
            print(f"Health: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"✗ Health check failed: {type(e).__name__}: {e}")
            print(f"  Is the server running at {BASE}?")
            sys.exit(1)

        # Desk metadata
        r = await client.get(f"{BASE}/api/desks/{DESK_ID}")
        if r.status_code != 200:
            print(f"✗ GET /api/desks/{DESK_ID} -> {r.status_code}: {r.text[:200]}")
            return
        desk = r.json()
        print(f"\nDesk {DESK_ID}: {desk.get('name')}")
        print(f"  Topics: {desk.get('topics')}")
        print(f"  Mode: {desk.get('mode')}  Active: {desk.get('is_active')}")

        # Run desk
        print(f"\nRunning desk {DESK_ID}...")
        r = await client.post(f"{BASE}/api/agent/run-desk/{DESK_ID}", json={})
        print(f"  HTTP {r.status_code}")
        try:
            result = r.json()
        except Exception:
            print(f"  Non-JSON response: {r.text[:400]}")
            return
        print("Result:")
        print(json.dumps(result, indent=2)[:1500])

        drafts_count = (
            result.get("drafts_created")
            or result.get("drafts_generated")
            or 0
        )
        if drafts_count > 0:
            print(f"\n✓ SUCCESS — {drafts_count} draft(s) created")

            # Show pending drafts
            r = await client.get(f"{BASE}/api/drafts/pending")
            if r.status_code != 200:
                print(f"  (drafts/pending -> {r.status_code})")
                return
            drafts = r.json()
            items = drafts.get("items", []) if isinstance(drafts, dict) else drafts
            print(f"Pending drafts returned: {len(items)}")
            for d in items[:2]:
                handle = d.get("account_handle") or d.get("handle") or "?"
                text_preview = (d.get("text") or d.get("final_text") or "")[:100]
                print(f"\n  @{handle}")
                print(f"  '{text_preview}'")
        else:
            print("\n✗ No drafts generated.")
            reason = (
                result.get("reason")
                or result.get("error")
                or result.get("message")
                or "(no reason field in response)"
            )
            print(f"  Reason: {reason}")
            print("  Common causes:")
            print("   - Desk has no accounts assigned (see step 4)")
            print("   - Rate-limited (MIN_SECONDS_BETWEEN_RUNS)")
            print("   - Trend fetch returned zero topics (see step 2)")
            print("   - Grok auth failed (see step 1)")


if __name__ == "__main__":
    asyncio.run(test())
