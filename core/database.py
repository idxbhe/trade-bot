from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from sqlalchemy.orm import declarative_base
from core.config import config

# Create Async Engine
engine = create_async_engine(
    config.DATABASE_URL,
    echo=False,  # Set to True for SQL query debugging
    future=True
)

# Create session factory
async_session = async_sessionmaker(
    engine, 
    expire_on_commit=False, 
    class_=AsyncSession
)

# Declarative base for models
Base = declarative_base()

import models.trade_history

async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        # Drop all tables first to reset the schema and avoid sqlite no column errors
        await conn.run_sync(Base.metadata.drop_all)
        # Enable WAL mode for better concurrency
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        # Create all tables (in a real app, use Alembic for migrations)
        await conn.run_sync(Base.metadata.create_all)

async def get_session() -> AsyncSession:
    """Get database session"""
    async with async_session() as session:
        yield session