"""
Serveur MCP : les outils qu'un agent IA peut appeler sur l'API Todo.

Chaque outil est un simple appel HTTP à l'API, avec le jeton d'API de l'utilisateur : le serveur
n'importe jamais `src/`, pour que l'agent passe par les mêmes règles que tout client (permissions,
rate limiting, idempotence). Les noms, docstrings et descriptions des paramètres sont en anglais :
c'est ce que l'agent lit pour choisir un outil et le remplir. Pas d'outil de suppression.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from mcp_server.api_client import TodoApiClient

PageLimit = Annotated[
    Literal[10, 25, 50, 100],
    Field(description="Number of elements to return. Must be one of 10, 25, 50, 100"),
]


def _filter_none_values(data: dict) -> dict:
    """Drop the arguments left at None, i.e. not given; empty strings and lists are kept."""
    return {k: v for k, v in data.items() if v is not None}


def build_mcp_server(api: TodoApiClient) -> MCPServer:
    """Build the MCP server whose tools call the API through `api`."""

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncGenerator[None, None]:
        """Close the API client's connections when the server stops."""
        yield
        await api.aclose()

    server = MCPServer("todo-api", lifespan=lifespan)

    @server.tool()
    async def create_todo(
        idempotency_key: Annotated[
            str,
            Field(
                min_length=1,
                max_length=255,
                description="A unique key for this creation, such as a UUID. Reuse the SAME key "
                "when retrying a call that may have failed, so the todo is never created twice.",
            ),
        ],
        title: Annotated[
            str,
            Field(
                min_length=1,
                max_length=255,
                description="Short title of the todo. 1 to 255 characters.",
            ),
        ],
        description: Annotated[
            str | None,
            Field(max_length=1000, description="Optional longer description. Max 1000 characters."),
        ] = None,
        category_ids: Annotated[
            list[int] | None,
            Field(max_length=20, description="List of category IDs to associate with this todo."),
        ] = None,
    ) -> dict:
        """Create a todo for the current user."""
        body = {"title": title, "description": description, "category_ids": category_ids or []}
        return await api.request("POST", "/todos", json=body, idempotency_key=idempotency_key)

    @server.tool()
    async def list_todos(
        skip: Annotated[int, Field(ge=0, description="Number of elements to skip.")] = 0,
        limit: PageLimit = 25,
        title: Annotated[
            str | None, Field(min_length=2, description="Filter on the title.")
        ] = None,
        is_completed: Annotated[
            bool | None, Field(description="Filter on the completion status.")
        ] = None,
        category_ids: Annotated[
            list[int] | None, Field(max_length=20, description="Filter on the category ids.")
        ] = None,
    ) -> dict:
        """Return a paginated list of todos for the current user."""
        params = {
            "skip": skip,
            "limit": limit,
            "title": title,
            "is_completed": is_completed,
            "category_ids": category_ids,
        }
        params = _filter_none_values(params)

        return await api.request("GET", "/todos", params=params)

    @server.tool()
    async def get_todo(
        todo_id: Annotated[int, Field(description="The ID of the todo to retrieve.")],
        include_categories: Annotated[
            bool, Field(description="Include categories in the response.")
        ] = False,
    ) -> dict:
        """Return a specific todo for the current user."""
        params = {}
        if include_categories:
            params = {"include": "categories"}

        return await api.request("GET", f"/todos/{todo_id}", params=params)

    @server.tool()
    async def update_todo(
        todo_id: Annotated[int, Field(description="The ID of the todo to update.")],
        title: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=255,
                description="Short title of the todo. 1 to 255 characters.",
            ),
        ] = None,
        description: Annotated[
            str | None,
            Field(
                max_length=1000,
                description="Optional longer description. Max 1000 characters. "
                "Pass an empty string to remove the description.",
            ),
        ] = None,
        category_ids: Annotated[
            list[int] | None,
            Field(
                max_length=20,
                description="List of category IDs to associate with this todo. "
                "Pass an empty list to remove all categories.",
            ),
        ] = None,
    ) -> dict:
        """Update a specific todo for the current user."""
        body = {
            "title": title,
            "description": description,
            "category_ids": category_ids,
        }
        body = _filter_none_values(body)

        if not body:
            raise ToolError("No fields to update.")

        return await api.request("PATCH", f"/todos/{todo_id}", json=body)

    @server.tool()
    async def complete_todo(
        todo_id: Annotated[int, Field(description="The ID of the todo to complete.")],
    ) -> dict:
        """Complete a specific todo for the current user."""
        return await api.request("PATCH", f"/todos/{todo_id}", json={"is_completed": True})

    @server.tool()
    async def list_categories(
        skip: Annotated[int, Field(ge=0, description="Number of elements to skip.")] = 0,
        limit: PageLimit = 25,
        name: Annotated[str | None, Field(min_length=2, description="Filter on the name.")] = None,
    ) -> dict:
        """Return a paginated list of categories. Categories are shared by all users"""
        params = {
            "skip": skip,
            "limit": limit,
            "name": name,
        }
        params = _filter_none_values(params)

        return await api.request("GET", "/categories", params=params)

    @server.tool()
    async def get_category(
        category_id: Annotated[int, Field(description="The ID of the category to retrieve.")],
    ) -> dict:
        """Return a specific category."""
        return await api.request("GET", f"/categories/{category_id}")

    @server.tool()
    async def create_category(
        idempotency_key: Annotated[
            str,
            Field(
                min_length=1,
                max_length=255,
                description="A unique key for this creation. Reuse the SAME key when retrying "
                "a call that may have failed, so the category is never created twice.",
            ),
        ],
        name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                description="Name of the category. 1 to 100 characters. "
                "Names are unique (case insensitive) within the API.",
            ),
        ],
        color: Annotated[
            str | None,
            Field(
                pattern=r"^#[a-fA-F0-9]{6}$",
                description='Optional color of the category. Default value is "#FAFAFA". '
                'Must be a valid hexadecimal color code (e.g., "#FF5733").',
            ),
        ] = None,
    ) -> dict:
        """Create a category."""
        body = {"name": name, "color": color}
        body = _filter_none_values(body)
        return await api.request("POST", "/categories", json=body, idempotency_key=idempotency_key)

    return server
