"""
Commande `seed-demo` : remplit une installation neuve avec les données de `demo_data.py` (un
admin, deux utilisateurs, leurs catégories et leurs todos), puis affiche les identifiants.
Tout passe par les services, donc chaque écriture est auditée, sans acteur : c'est le système.
Une seule transaction, comptes créés en premier : relancée sur une base déjà remplie, elle
échoue sur l'email du premier compte et n'écrit rien. Pour repartir de zéro, il faut vider la
base (`docker compose down -v`, puis `up`).
Équivalent conceptuel d'un seeder lancé par `php artisan db:seed` dans Laravel.
"""

import argparse
import asyncio
import sys

from pydantic import ValidationError

from src.console.demo_data import CATEGORIES, TODOS, USERS
from src.core.database import async_session_factory
from src.core.exceptions import ConflictError
from src.core.redis import get_redis_client
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.service import AuditLogService
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.service import CategoryService
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate, TodoUpdate
from src.modules.todos.service import TodoService
from src.modules.users.models import User
from src.modules.users.repository import UserRepository
from src.modules.users.schemas import UserCreate
from src.modules.users.service import UserService


async def create_users(user_service: UserService) -> dict[str, tuple[User, str]]:
    """Create the demo accounts, the admin through `create_admin`.

    Returns each account and its plain password, by email: the categories and todos name their
    owner by email, and the command prints the passwords at the end.
    """
    demo_users = {}

    for demo_user in USERS:
        user_data = UserCreate(
            email=demo_user["email"],
            first_name=demo_user["first_name"],
            last_name=demo_user["last_name"],
            pseudo=demo_user["pseudo"],
            password=demo_user["password"],
            confirm_password=demo_user["confirm_password"],
        )

        if demo_user.get("is_admin", False):
            user = await user_service.create_admin(user_data)
        else:
            user = await user_service.create_user(user_data)

        demo_users[demo_user["email"]] = (user, demo_user["password"])

    return demo_users


async def create_categories(
    category_service: CategoryService, users: dict[str, tuple[User, str]]
) -> dict[str, int]:
    """Create the demo categories, each one by its owner.

    Returns their IDs by name, since the demo todos name their categories.
    """
    demo_categories = {}

    for owner_email, list_categories in CATEGORIES.items():
        user, _ = users[owner_email]

        for category_item in list_categories:
            category = await category_service.create_category(user.id, category_item)
            demo_categories[category.name] = category.id

    return demo_categories


async def create_todos(
    todo_service: TodoService, users: dict[str, tuple[User, str]], categories: dict[str, int]
) -> None:
    """Create the demo todos, then complete or delete those marked so."""
    for owner_email, list_todos in TODOS.items():
        user, _ = users[owner_email]

        for todo_item in list_todos:
            cat_ids = [categories[cat_name] for cat_name in todo_item.get("categories", [])]

            todo_data = TodoCreate(
                title=todo_item["title"],
                description=todo_item.get("description"),
                category_ids=cat_ids,
            )
            todo = await todo_service.create_todo(user.id, todo_data)

            if todo_item.get("is_completed", False):
                await todo_service.update_todo(user, todo.id, TodoUpdate(is_completed=True))
            if todo_item.get("is_deleted", False):
                await todo_service.delete_todo(user, todo.id)


async def build_demo() -> dict[str, tuple[User, str]]:
    """Write all the demo data in one session and commit it once, at the end.

    Any failure leaves the database untouched. Returns the accounts for the final printout.
    """
    async with async_session_factory() as session:
        user_service = UserService(
            UserRepository(session), AuditLogService(AuditLogRepository(session))
        )
        category_service = CategoryService(
            CategoryRepository(session),
            get_redis_client(),
            AuditLogService(AuditLogRepository(session)),
        )
        todo_service = TodoService(
            TodoRepository(session),
            CategoryRepository(session),
            AuditLogService(AuditLogRepository(session)),
        )

        users = await create_users(user_service)
        categories = await create_categories(category_service, users)
        await create_todos(todo_service, users, categories)

        await session.commit()
        return users


def seed_demo(args: argparse.Namespace) -> None:
    """Run the `seed-demo` command: write the demo data, then print the accounts to log in with.

    An expected failure, such as accounts that already exist, becomes a one-line error and exit
    code 1.
    """
    try:
        print("Creating demo data")
        users = asyncio.run(build_demo())
        print("Demo data created successfully")

        for user, password in users.values():
            print(f"User: {user.email} Admin: {user.is_admin} Password: {password}")
    except (ValidationError, ConflictError) as e:
        print(f"Error creating demo data: {e}", file=sys.stderr)
        sys.exit(1)
