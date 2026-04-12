"""
Async SQLAlchemy 2.0 database setup for X Agent.

Exports:
    engine          — AsyncEngine (module-level singleton)
    AsyncSessionLocal — session factory
    Base            — declarative base for all models
    get_db()        — FastAPI dependency that yields a session
    init_db()       — called at startup to create tables + seed data
    close_db()      — called at shutdown to dispose the engine
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# Declarative base — all models import this
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession for the request lifetime.

    Usage::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """
    Create all tables (if they don't exist) and seed default data.

    Called once during application startup via the lifespan context manager.
    """
    # Import models here to ensure they're registered on Base.metadata
    # before create_all is called.  The local import avoids circular deps
    # at module load time.
    from backend import models  # noqa: F401 — registers ORM metadata

    logger.info("Creating database tables …")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready.")

    await _seed_default_data()


async def _seed_default_data() -> None:
    """Insert seed desks if the desks table is empty."""
    from sqlalchemy import func, select

    from backend.models import Desk

    async with AsyncSessionLocal() as session:
        count_result = await session.execute(select(func.count()).select_from(Desk))
        count: int = count_result.scalar_one()

        if count > 0:
            logger.debug("Seed data already present — skipping.")
            return

        logger.info("Seeding default desks …")
        desks = [
            Desk(
                name="Geopolitics",
                color="#C0392B",
                topics=[
                    "war", "diplomacy", "sanctions", "nato",
                    "gaza", "ukraine", "ceasefire", "geopolitics",
                    "foreign policy",
                ],
                timing_slots=["07:00", "13:00", "18:00"],
                daily_video=1,
                daily_photo=2,
                daily_text=4,
            ),
            Desk(
                name="World Sports",
                color="#185FA5",
                topics=[
                    "football", "premier league", "f1", "tennis",
                    "olympics", "champions league", "world cup",
                    "nba", "arsenal", "real madrid",
                ],
                timing_slots=["12:00", "19:00", "22:00"],
                daily_video=3,
                daily_photo=2,
                daily_text=5,
            ),
            Desk(
                name="Indian Politics",
                color="#FF5C1A",
                topics=[
                    "bjp", "congress", "modi", "parliament",
                    "budget", "supreme court", "election",
                    "india politics", "rahul gandhi", "yogi",
                ],
                timing_slots=["08:00", "13:00", "20:00"],
                daily_video=1,
                daily_photo=2,
                daily_text=5,
            ),
            Desk(
                name="Indian Sports",
                color="#1A7A4A",
                topics=[
                    "ipl", "cricket", "bcci", "virat kohli",
                    "rohit sharma", "india cricket", "ms dhoni",
                    "test cricket", "t20", "kabaddi",
                ],
                timing_slots=["10:00", "19:30", "23:00"],
                daily_video=3,
                daily_photo=2,
                daily_text=7,
            ),
            Desk(
                name="Thinkers Commentary",
                color="#7C3ABD",
                topics=[
                    "philosophy", "economics", "culture", "society",
                    "ideas", "opinion", "essay", "intellectual",
                    "debate", "analysis",
                ],
                timing_slots=["09:00", "13:00", "20:00"],
                daily_video=0,
                daily_photo=1,
                daily_text=4,
            ),
            Desk(
                name="Technology",
                color="#C67B00",
                topics=[
                    "ai", "openai", "claude", "anthropic",
                    "startup", "silicon valley", "chatgpt", "llm",
                    "tech", "machine learning", "software",
                ],
                timing_slots=["09:00", "12:00", "15:00"],
                daily_video=1,
                daily_photo=2,
                daily_text=4,
            ),
            Desk(
                name="Indian Business",
                color="#0F6E56",
                topics=[
                    "nifty", "sensex", "rbi", "startup india",
                    "zomato", "reliance", "adani", "tata",
                    "indian economy", "ipo", "markets",
                ],
                timing_slots=["08:00", "15:30", "20:00"],
                daily_video=0,
                daily_photo=2,
                daily_text=4,
            ),
            Desk(
                name="Entertainment",
                color="#D4537E",
                topics=[
                    "netflix", "bollywood", "ott", "movies",
                    "web series", "celebrity", "music", "oscar",
                    "bafta", "grammy", "box office",
                ],
                timing_slots=["12:00", "19:00", "21:00"],
                daily_video=3,
                daily_photo=3,
                daily_text=4,
            ),
        ]

        session.add_all(desks)
        await session.commit()
        logger.info("Seeded %d default desks.", len(desks))


# ---------------------------------------------------------------------------
# Shutdown helper
# ---------------------------------------------------------------------------


async def close_db() -> None:
    """Dispose the engine connection pool. Called during app shutdown."""
    await engine.dispose()
    logger.info("Database connection pool disposed.")
