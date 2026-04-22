"""
Step 4: Verify database has accounts and desks that can actually run.

Reads DATABASE_URL from .env so this test hits the same DB the app uses.

Run:  python test_step4_database.py
"""

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+aiosqlite:///./xagent.db"
)


async def test() -> None:
    print("Step 4: Database test...")
    print(f"  DATABASE_URL={DATABASE_URL}")

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # Desks
        result = await db.execute(
            text(
                "SELECT id, name, topics, mode, is_active "
                "FROM desks WHERE is_deleted = 0 ORDER BY id"
            )
        )
        desks = result.fetchall()
        print(f"\n✓ Desks found: {len(desks)}")
        for d in desks:
            topics_preview = (d[2] or "")[:80]
            print(f"  Desk {d[0]}: {d[1]}  mode={d[3]} active={d[4]}")
            print(f"    Topics: {topics_preview}")

        # Accounts
        result = await db.execute(
            text(
                "SELECT id, handle, desk_ids, is_active "
                "FROM accounts WHERE is_deleted = 0 ORDER BY id"
            )
        )
        accounts = result.fetchall()
        print(f"\n✓ Accounts found: {len(accounts)}")
        for a in accounts:
            print(f"  @{a[1]}  desks={a[2]}  active={a[3]}")

        # Per-desk coverage
        print("\n✓ Per-desk account coverage:")
        for d in desks:
            desk_id = d[0]
            # desk_ids is JSON; crude substring check is enough for a diagnostic
            assigned = [
                a for a in accounts
                if a[2] and str(desk_id) in _extract_ids(a[2])
            ]
            if assigned:
                handles = ", ".join(f"@{a[1]}" for a in assigned)
                print(f"  Desk {desk_id} ({d[1]}): {len(assigned)} -> {handles}")
            else:
                print(f"  Desk {desk_id} ({d[1]}): ✗ NO ACCOUNTS — this desk will return 0 drafts")
                print(f"    Fix: open Telegram -> /addaccount -> assign to '{d[1]}'")

    await engine.dispose()


def _extract_ids(desk_ids_json: str) -> list[str]:
    """Cheaply pull ID tokens out of a JSON array string like '[1, 3]'."""
    import json
    try:
        parsed = json.loads(desk_ids_json)
        if isinstance(parsed, list):
            return [str(i) for i in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


if __name__ == "__main__":
    asyncio.run(test())
