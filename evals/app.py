"""
L'application en mémoire sur laquelle tournent les evals des outils MCP.

Chaque exécution d'un scénario part d'une base SQLite et d'un Redis neufs, avec un utilisateur et
son jeton d'API. Contrairement aux tests, aucune dépendance n'est remplacée : seules les sources
(la fabrique de sessions, le client Redis) sont redirigées vers la mémoire. Chaque requête commite
donc comme en production, et ce qu'un appel d'outil de l'agent écrit existe encore au suivant.
Équivalent conceptuel d'une base SQLite en mémoire avec RefreshDatabase dans Laravel, mais sans
transaction autour du test : les commits sont réels.
"""

import os
from contextlib import asynccontextmanager

import fakeredis
from sqlalchemy import StaticPool, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["JWT_SECRET_KEY"] = "eval-secret-key-not-for-production-1234567890"
os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "False"

import src.core.database as database  # noqa: E402
import src.core.redis as core_redis  # noqa: E402
import src.main as main  # noqa: E402
from src.core.database import Base  # noqa: E402
from src.core.security import generate_api_token, hash_password, hash_token  # noqa: E402
from src.modules.api_tokens.repository import ApiTokenRepository  # noqa: E402
from src.modules.users.repository import UserRepository  # noqa: E402
from src.modules.users.schemas import UserCreate  # noqa: E402

engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


@asynccontextmanager
async def eval_app():
    """Run the API on a fresh in-memory database and Redis for one eval run.

    Yields the session factory (to set up a scenario's data and check the database afterwards),
    the id of the agent's user (the owner of the scenario's data) and the user's plain API token.
    On exit, whatever happened, the application is
    pointed back at its original database and Redis, and the tables are dropped: Python modules
    live as long as the process, so a harness test run inside pytest must not leave the rest of
    the suite on this database.
    """
    # --- entrée ---
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original_factory = database.async_session_factory
    original_main_session = main.async_session_factory
    original_redis_client = core_redis.redis_client

    database.async_session_factory = session_factory
    main.async_session_factory = session_factory
    core_redis.redis_client = fakeredis.FakeAsyncRedis()

    try:
        async with session_factory() as session:
            repository = UserRepository(session)
            user = await repository.create(
                UserCreate(
                    email="user@test.com",
                    first_name="Test",
                    last_name="User",
                    password="secret123",
                    confirm_password="secret123",
                ),
                hash_password("secret123"),
            )

            plain_token = generate_api_token()
            await ApiTokenRepository(session).create(
                owner_id=user.id, name="Agent", token_hash=hash_token(plain_token), expires_at=None
            )

            await session.commit()

        yield session_factory, user.id, plain_token
    finally:
        # --- sortie : s'exécute toujours, même si le scénario plante ---
        database.async_session_factory = original_factory
        main.async_session_factory = original_main_session
        core_redis.redis_client = original_redis_client

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
