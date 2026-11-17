"""Tests of the `seed-demo` console command.

Expectations are computed from `demo_data.py`, so editing the demo data doesn't break them.
"""

import argparse
from collections import Counter

import fakeredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.console import seed_demo as seed_demo_module
from src.console.demo_data import CATEGORIES, TODOS, USERS
from src.console.seed_demo import build_demo, seed_demo
from src.core.exceptions import ConflictError
from src.modules.audit.models import AuditAction, AuditLog, AuditResourceType
from src.modules.categories.models import Category
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.schemas import CategoryCreate
from src.modules.todos.models import Todo
from src.modules.users.models import User

DEMO_TODOS = [todo for todos in TODOS.values() for todo in todos]


@pytest.fixture(autouse=True)
def demo_uses_fake_redis(
    monkeypatch: pytest.MonkeyPatch, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """The command takes its Redis client from get_redis_client(), outside any dependency
    override: point the shared client at the test's fake."""
    monkeypatch.setattr("src.core.redis.redis_client", redis_client)


async def _count(db_session: AsyncSession, model: type) -> int:
    return await db_session.scalar(select(func.count()).select_from(model)) or 0


# --- build_demo ---


async def test_build_demo_creates_the_accounts(db_session: AsyncSession):
    """The accounts are committed, only the one marked so is an admin, and the returned
    passwords are the plain demo ones, for the final printout."""
    accounts = await build_demo()

    users = (await db_session.scalars(select(User))).all()
    assert {user.email: user.is_admin for user in users} == {
        demo["email"]: demo.get("is_admin", False) for demo in USERS
    }
    assert {email: password for email, (_, password) in accounts.items()} == {
        demo["email"]: demo["password"] for demo in USERS
    }


async def test_build_demo_creates_each_category_by_its_owner(db_session: AsyncSession):
    await build_demo()

    rows = (
        await db_session.execute(
            select(Category.name, Category.color, User.email).join(
                User, Category.created_by_id == User.id
            )
        )
    ).all()
    assert sorted(rows) == sorted(
        (category.name, category.color, email)
        for email, categories in CATEGORIES.items()
        for category in categories
    )


async def test_build_demo_creates_the_todos_in_their_state(db_session: AsyncSession):
    """Each todo belongs to its owner, carries the categories it names (another user's
    included), and is completed or soft-deleted as the data says."""
    await build_demo()

    todos = (
        await db_session.scalars(
            select(Todo).options(selectinload(Todo.categories)).order_by(Todo.id)
        )
    ).all()
    emails = dict((await db_session.execute(select(User.id, User.email))).tuples().all())

    actual = [
        (
            emails[todo.owner_id],
            todo.title,
            todo.description,
            sorted(category.name for category in todo.categories),
            todo.is_completed,
            todo.deleted_at is not None,
        )
        for todo in todos
    ]
    expected = [
        (
            email,
            demo["title"],
            demo.get("description"),
            sorted(demo.get("categories", [])),
            demo.get("is_completed", False),
            demo.get("is_deleted", False),
        )
        for email, demos in TODOS.items()
        for demo in demos
    ]
    assert actual == expected


async def test_build_demo_audits_every_write_as_the_system(db_session: AsyncSession):
    """Every write goes through a service, so each one has its entry, with no actor."""
    await build_demo()

    entries = (await db_session.scalars(select(AuditLog))).all()
    assert all(entry.actor_id is None and entry.auth_method is None for entry in entries)
    assert Counter((entry.resource_type, entry.action) for entry in entries) == Counter(
        {
            (AuditResourceType.USER, AuditAction.CREATED): len(USERS),
            (AuditResourceType.USER, AuditAction.UPDATED): sum(
                demo.get("is_admin", False) for demo in USERS
            ),
            (AuditResourceType.CATEGORY, AuditAction.CREATED): sum(
                len(categories) for categories in CATEGORIES.values()
            ),
            (AuditResourceType.TODO, AuditAction.CREATED): len(DEMO_TODOS),
            (AuditResourceType.TODO, AuditAction.UPDATED): sum(
                demo.get("is_completed", False) for demo in DEMO_TODOS
            ),
            (AuditResourceType.TODO, AuditAction.DELETED): sum(
                demo.get("is_deleted", False) for demo in DEMO_TODOS
            ),
        }
    )
    completions = [
        entry.changed_fields
        for entry in entries
        if (entry.resource_type, entry.action) == (AuditResourceType.TODO, AuditAction.UPDATED)
    ]
    assert all(fields == ["is_completed"] for fields in completions)


async def test_build_demo_run_twice_fails_and_writes_nothing_more(db_session: AsyncSession):
    """The accounts come first: a second run fails on the first email, before any other
    write, and the whole transaction is rolled back."""
    await build_demo()
    counts = [await _count(db_session, model) for model in (User, Category, Todo, AuditLog)]

    with pytest.raises(ConflictError):
        await build_demo()

    assert [await _count(db_session, model) for model in (User, Category, Todo, AuditLog)] == (
        counts
    )


async def test_build_demo_with_a_taken_category_name_writes_nothing(
    db_session: AsyncSession, current_user: User
):
    """A database already in use can hold a demo category's name: the accounts created before
    it are rolled back with the rest."""
    taken_name = next(iter(CATEGORIES.values()))[0].name
    await CategoryRepository(db_session).create(
        created_by_id=current_user.id, data=CategoryCreate(name=taken_name)
    )
    await db_session.commit()

    with pytest.raises(ConflictError):
        await build_demo()

    assert (await db_session.scalars(select(User.email))).all() == [current_user.email]
    assert await _count(db_session, Category) == 1
    assert await _count(db_session, Todo) == 0
    assert await _count(db_session, AuditLog) == 0


# --- seed_demo ---
# Sync tests: the command calls asyncio.run, which refuses to run inside a running loop.
# build_demo is replaced, so no database work leaves the loop asyncio.run creates.


def test_seed_demo_prints_the_accounts_to_log_in_with(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    async def fake_build_demo() -> dict[str, tuple[User, str]]:
        return {
            "admin@demo.com": (User(email="admin@demo.com", is_admin=True), "admin-pass"),
            "user@demo.com": (User(email="user@demo.com", is_admin=False), "user-pass"),
        }

    monkeypatch.setattr(seed_demo_module, "build_demo", fake_build_demo)

    seed_demo(argparse.Namespace())

    out = capsys.readouterr().out
    assert "User: admin@demo.com Admin: True Password: admin-pass" in out
    assert "User: user@demo.com Admin: False Password: user-pass" in out


def test_seed_demo_on_a_seeded_database_exits_with_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    async def fake_build_demo() -> dict[str, tuple[User, str]]:
        raise ConflictError("Email déjà pris.")

    monkeypatch.setattr(seed_demo_module, "build_demo", fake_build_demo)

    with pytest.raises(SystemExit) as exc:
        seed_demo(argparse.Namespace())

    assert exc.value.code == 1
    assert "Error creating demo data: Email déjà pris." in capsys.readouterr().err


def test_seed_demo_lets_an_unexpected_error_through(monkeypatch: pytest.MonkeyPatch):
    """Only the expected errors become a message: anything else keeps its traceback."""

    async def fake_build_demo() -> dict[str, tuple[User, str]]:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(seed_demo_module, "build_demo", fake_build_demo)

    with pytest.raises(RuntimeError):
        seed_demo(argparse.Namespace())
