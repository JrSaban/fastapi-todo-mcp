"""Tests of the `charmer` console: the command line parsing and the `create-admin` command."""

import argparse
from collections.abc import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.console import create_admin as create_admin_module
from src.console.create_admin import build_user, commit_admin, create_admin
from src.console.main import main
from src.core.exceptions import ConflictError
from src.modules.audit.models import AuditAction, AuditLog
from src.modules.users.models import User
from src.modules.users.schemas import UserCreate

PASSWORD = "secret123"


def _answer(monkeypatch: pytest.MonkeyPatch, inputs: list[str], passwords: list[str]) -> None:
    """Script the terminal: `inputs` for input(), in order, `passwords` for getpass()."""
    typed: Iterator[str] = iter(inputs)
    hidden: Iterator[str] = iter(passwords)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(typed))
    monkeypatch.setattr(create_admin_module, "getpass", lambda _prompt: next(hidden))


def _admin_data(email: str = "admin@test.com") -> UserCreate:
    return UserCreate(
        email=email,
        first_name="Ada",
        last_name="Admin",
        password=PASSWORD,
        confirm_password=PASSWORD,
    )


# --- main ---


def test_main_without_command_exits_with_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr("sys.argv", ["charmer"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert "command" in capsys.readouterr().err


def test_main_runs_the_chosen_command_with_the_parsed_args(monkeypatch: pytest.MonkeyPatch):
    received: list[argparse.Namespace] = []
    monkeypatch.setattr("sys.argv", ["charmer", "create-admin"])
    monkeypatch.setattr("src.console.main.create_admin", received.append)

    main()

    assert len(received) == 1
    assert received[0].command == "create-admin"


# --- build_user ---


def test_build_user_reads_every_answer(monkeypatch: pytest.MonkeyPatch):
    _answer(
        monkeypatch,
        ["Admin@Test.com", "Ada", "Admin", "ada"],
        [PASSWORD, PASSWORD],
    )

    data = build_user()

    assert data.email == "admin@test.com"
    assert (data.first_name, data.last_name, data.pseudo) == ("Ada", "Admin", "ada")
    assert data.password == PASSWORD


@pytest.mark.parametrize("pseudo", ["", "   "])
def test_build_user_turns_a_blank_pseudo_into_none(monkeypatch: pytest.MonkeyPatch, pseudo: str):
    """A stored empty pseudo would collide, through the unique index, with the next admin
    created without one."""
    _answer(monkeypatch, ["admin@test.com", "Ada", "Admin", pseudo], [PASSWORD, PASSWORD])

    assert build_user().pseudo is None


def test_build_user_with_mismatched_passwords_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
):
    _answer(monkeypatch, ["admin@test.com", "Ada", "Admin", ""], [PASSWORD, "other123"])

    with pytest.raises(ValidationError):
        build_user()


# --- commit_admin ---


async def test_commit_admin_commits_an_audited_admin(db_session: AsyncSession):
    """The command commits on its own: the admin and both audit entries are there, written
    by the system (no actor)."""
    user = await commit_admin(_admin_data())

    stored = await db_session.scalar(select(User).where(User.id == user.id))
    assert stored is not None
    assert stored.is_admin
    entries = (await db_session.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    assert [(e.action, e.changed_fields, e.actor_id) for e in entries] == [
        (AuditAction.CREATED, None, None),
        (AuditAction.UPDATED, ["is_admin"], None),
    ]


async def test_commit_admin_with_taken_email_writes_nothing(
    db_session: AsyncSession, current_user: User
):
    await db_session.commit()

    with pytest.raises(ConflictError):
        await commit_admin(_admin_data(email=current_user.email))

    admins = (await db_session.scalars(select(User).where(User.is_admin))).all()
    assert admins == []
    assert (await db_session.scalars(select(AuditLog))).all() == []


# --- create_admin ---
# Sync tests: the command calls asyncio.run, which refuses to run inside a running loop.
# commit_admin is replaced, so no database work leaves the loop asyncio.run creates.


def test_create_admin_prints_the_new_admin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _answer(monkeypatch, ["admin@test.com", "Ada", "Admin", ""], [PASSWORD, PASSWORD])

    async def fake_commit_admin(data: UserCreate) -> User:
        return User(id=42, email=data.email)

    monkeypatch.setattr(create_admin_module, "commit_admin", fake_commit_admin)

    create_admin(argparse.Namespace())

    assert "ID: 42, email: admin@test.com" in capsys.readouterr().out


def test_create_admin_with_taken_email_exits_with_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _answer(monkeypatch, ["admin@test.com", "Ada", "Admin", ""], [PASSWORD, PASSWORD])

    async def fake_commit_admin(data: UserCreate) -> User:
        raise ConflictError("Email déjà pris.")

    monkeypatch.setattr(create_admin_module, "commit_admin", fake_commit_admin)

    with pytest.raises(SystemExit) as exc:
        create_admin(argparse.Namespace())

    assert exc.value.code == 1
    assert "Email déjà pris." in capsys.readouterr().err


def test_create_admin_with_invalid_answers_exits_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _answer(monkeypatch, ["not-an-email", "Ada", "Admin", ""], ["short", "short"])

    async def fake_commit_admin(data: UserCreate) -> User:
        raise AssertionError("must not be reached")

    monkeypatch.setattr(create_admin_module, "commit_admin", fake_commit_admin)

    with pytest.raises(SystemExit) as exc:
        create_admin(argparse.Namespace())

    assert exc.value.code == 1
    assert "Error creating admin" in capsys.readouterr().err


def test_create_admin_lets_an_unexpected_error_through(monkeypatch: pytest.MonkeyPatch):
    """Only the two expected errors become a message: anything else keeps its traceback."""
    _answer(monkeypatch, ["admin@test.com", "Ada", "Admin", ""], [PASSWORD, PASSWORD])

    async def fake_commit_admin(data: UserCreate) -> User:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(create_admin_module, "commit_admin", fake_commit_admin)

    with pytest.raises(RuntimeError):
        create_admin(argparse.Namespace())
