import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from .models import Base, User, Store

DATABASE_URL = os.environ["DATABASE_URL"]  # ex.: sqlite+aiosqlite:///C:/.../roleta.db
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")

if "ssl=" not in DATABASE_URL:
    DATABASE_URL += "?ssl=require"

engine = create_async_engine(DATABASE_URL, echo=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
