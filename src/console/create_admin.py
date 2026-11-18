"""
Commande `create-admin` : crée un compte admin, en posant les questions dans le terminal.
Seule façon de créer le premier admin d'une installation, la route PATCH /users/{id}/admin
exigeant déjà un admin. Le droit de la lancer vient de l'accès au serveur.
Équivalent conceptuel d'une commande dans app/Console/Commands dans Laravel.
"""

import argparse
import asyncio
import sys
from getpass import getpass

from pydantic import ValidationError

from src.core.database import async_session_factory
from src.core.exceptions import ConflictError
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.service import AuditLogService
from src.modules.users.models import User
from src.modules.users.repository import UserRepository
from src.modules.users.schemas import UserCreate
from src.modules.users.service import UserService


def build_user() -> UserCreate:
    """Ask for the account's details in the terminal, the password without echoing it.

    An empty pseudo becomes None: pseudos are unique, so a stored empty string would block the
    next admin created without one.
    """
    email = input("email address: ")
    first_name = input("first name: ")
    last_name = input("last name: ")
    pseudo = input("pseudo (optional): ")

    if not pseudo.strip():
        pseudo = None

    password = getpass("password: ")
    confirm_password = getpass("confirm password: ")

    return UserCreate(
        email=email,
        first_name=first_name,
        last_name=last_name,
        pseudo=pseudo,
        password=password,
        confirm_password=confirm_password,
    )


async def commit_admin(data: UserCreate) -> User:
    """Create the admin in its own session and commit it.

    The command is an entry point, like a job: no request transaction exists to commit for it.
    """
    async with async_session_factory() as session:
        user_service = UserService(
            UserRepository(session), AuditLogService(AuditLogRepository(session))
        )
        user = await user_service.create_admin(data)
        await session.commit()
        return user


def create_admin(args: argparse.Namespace) -> None:
    """Run the `create-admin` command: ask the questions, then create the admin."""
    try:
        print("Creating an admin user")
        data_user = build_user()

        user = asyncio.run(commit_admin(data_user))
        print(f"Admin created successfully: ID: {user.id}, email: {user.email}")
    except (ValidationError, ConflictError) as e:
        print(f"Error creating admin: {e}", file=sys.stderr)
        sys.exit(1)
