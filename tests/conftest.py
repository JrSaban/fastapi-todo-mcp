import os
from collections.abc import AsyncGenerator, Awaitable, Callable

import fakeredis
import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

"""
Configuration globale des tests avec pytest (conftest.py).
Équivalent conceptuel de tests/TestCase.php avec RefreshDatabase dans Laravel.
"""

# Doit être fixé avant TOUT import de src.* : Settings() (instancié au chargement de
# src.core.config, importé transitivement par la plupart des modules de src) lit la
# variable d'environnement DEBUG dès sa construction, et setup_logging() (dans
# src.main) s'en sert pour choisir le niveau du root logger.
os.environ.setdefault("DEBUG", "False")
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production-123456789012"
os.environ["APP_ENV"] = "test"

import src.core.security as security  # noqa: E402
from src.core.database import Base, get_db_session  # noqa: E402
from src.core.redis import get_redis_client  # noqa: E402
from src.core.security import hash_password  # noqa: E402
from src.main import app  # noqa: E402
from src.modules.audit.context import Actor, current_actor  # noqa: E402
from src.modules.audit.models import AuditAuthMethod  # noqa: E402
from src.modules.auth.router import get_current_user  # noqa: E402
from src.modules.users.models import User  # noqa: E402
from src.modules.users.repository import UserRepository  # noqa: E402
from src.modules.users.schemas import UserCreate  # noqa: E402

# Argon2 is deliberately slow (~40 ms per hash or verify), which protects nothing in tests:
# give them the cheapest parameters. Production keeps the defaults.
security._password_hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

# Base de données SQLite en mémoire vive (ultra-rapide, isolée par session de test)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(test_engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


test_session_factory = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


@pytest.fixture(autouse=True)
async def setup_test_database() -> AsyncGenerator[None, None]:
    """
    Fixture automatique (autouse=True) :
    - Avant chaque test : crée toutes les tables vierges.
    - Après chaque test : supprime toutes les tables pour garantir une isolation totale.
    Équivalent de RefreshDatabase dans Laravel.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield  # Le test s'exécute ici

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def background_code_uses_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs and the job-tracking middleware open their own sessions from the production
    factory, out of reach of dependency_overrides: point them at the test database, or
    every test that runs a job would write into the real Postgres."""
    for module in (
        "src.main",
        "src.modules.api_tokens.tasks",
        "src.modules.approvals.tasks",
        "src.modules.maintenance.tasks",
        "src.modules.maintenance.router",
        "src.modules.jobs.middleware",
    ):
        monkeypatch.setattr(f"{module}.async_session_factory", test_session_factory)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Fournit une session BDD de test active si un test en a besoin directement."""
    async with test_session_factory() as session:
        yield session


@pytest.fixture
async def client(
    db_session: AsyncSession,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Client HTTP asynchrone (httpx.AsyncClient).
    Interroge l'application FastAPI en mémoire (sans ouvrir de vrai port réseau).
    Surcharge la dépendance de production 'get_db_session' par notre session de test.
    """

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis_client() -> AsyncGenerator[fakeredis.FakeAsyncRedis, None]:
        yield redis_client

    # Surcharge les dépendances de FastAPI
    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_redis_client] = override_get_redis_client
    # Les middlewares ne passent pas par dependency_overrides : ils appellent
    # get_redis_client() directement, qui lit cet attribut du module.
    monkeypatch.setattr("src.core.redis.redis_client", redis_client)

    # Crée le client de test
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    # Nettoyage : rétablit la configuration d'origine
    app.dependency_overrides.clear()


@pytest.fixture
async def current_user(db_session: AsyncSession) -> User:
    """Crée un utilisateur normal directement en base (sans passer par l'API)."""
    repository = UserRepository(db_session)
    return await repository.create(
        UserCreate(
            email="user@test.com",
            first_name="Test",
            last_name="User",
            password="secret123",
            confirm_password="secret123",
        ),
        hash_password("secret123"),
    )


@pytest.fixture
async def other_user(db_session: AsyncSession) -> User:
    """Crée un second utilisateur normal directement en base, pour tester l'isolation
    entre utilisateurs (aucun client authentifié n'est fourni pour lui : on ne
    l'utilise que comme propriétaire de données créées via un repository)."""
    repository = UserRepository(db_session)
    return await repository.create(
        UserCreate(
            email="other@test.com",
            first_name="Other",
            last_name="User",
            password="secret123",
            confirm_password="secret123",
        ),
        hash_password("secret123"),
    )


@pytest.fixture
async def authenticated_client(client: AsyncClient, current_user: User) -> AsyncClient:
    """Le client de test, mais avec get_current_user déjà surchargé par current_user."""
    app.dependency_overrides[get_current_user] = _as_session_user(current_user)
    return client


def _as_session_user(user: User) -> Callable[[], Awaitable[User]]:
    """Override of get_current_user acting as a login session of `user`.

    It sets the request's actor like the real dependency does, so audit entries written during
    the request carry this user. It must be async: a sync dependency runs in a worker thread,
    and a context variable set there wouldn't reach the route.
    """

    async def override() -> User:
        current_actor.set(
            Actor(user_id=user.id, auth_method=AuditAuthMethod.SESSION, api_token_id=None)
        )
        return user

    return override


@pytest.fixture
async def current_admin_user(db_session: AsyncSession) -> User:
    """Crée un utilisateur admin directement en base (sans passer par l'API)."""
    repository = UserRepository(db_session)
    admin_user = await repository.create(
        UserCreate(
            email="admin@test.com",
            first_name="Test",
            last_name="User",
            password="secret123",
            confirm_password="secret123",
        ),
        hash_password("secret123"),
    )
    return await repository.set_admin(admin_user, True)


@pytest.fixture
async def authenticated_admin(client: AsyncClient, current_admin_user: User) -> AsyncClient:
    """Le client de test, mais avec get_current_user déjà surchargé par current_admin_user."""
    app.dependency_overrides[get_current_user] = _as_session_user(current_admin_user)
    return client


@pytest.fixture
async def redis_client() -> AsyncGenerator[fakeredis.FakeAsyncRedis, None]:
    """Fixture pour un client Redis mocké (fakeredis)"""
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
