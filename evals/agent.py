"""
La boucle d'agent des evals : un vrai modèle Claude utilise les outils du serveur MCP.

Le modèle reçoit la demande de l'utilisateur, les 14 outils et les instructions du serveur. Il
demande un outil, la boucle l'exécute et lui renvoie le résultat, jusqu'à ce qu'il réponde en
texte. Tout tourne dans le processus : le serveur MCP appelle l'application FastAPI sans réseau,
sur la base que prépare `evals/app.py`. Seul l'appel au modèle sort sur Internet.
Équivalent conceptuel d'un test Dusk dans Laravel : un vrai client pilote l'application, mais le
client est ici un modèle qui choisit lui-même ses actions.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlockParam, ToolParam
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from mcp_server.api_client import TodoApiClient
from mcp_server.config import Settings
from mcp_server.server import build_mcp_server
from src.main import app

MAX_TURNS = 20
MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class ToolCall:
    """One tool the agent called, with the arguments it chose and whether the tool failed."""

    name: str
    input: dict[str, Any]
    is_error: bool = field(default=False)


@dataclass
class AgentRun:
    """What the agent did during one run, as the scenario checks see it.

    `final_text` stays `None` when the run ends without a text answer (`MAX_TURNS` reached);
    `stop_reason` tells how the last model call ended (`end_turn`, `max_tokens`, `refusal`...).
    """

    tool_calls: list[ToolCall] = field(default_factory=list)
    final_text: str | None = None
    stop_reason: str | None = None


def _text(result: CallToolResult) -> str:
    """The tool's text output. A result may hold images or resources too, never ours."""
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def build_server(api_token: str) -> MCPServer:
    """The MCP server, whose API client calls the FastAPI app in process (ASGITransport) with
    `api_token`. The host in the URL is never resolved."""
    settings = Settings(TODO_API_URL="http://testserver/api/v1", TODO_API_TOKEN=api_token)
    return build_mcp_server(TodoApiClient(settings, transport=httpx.ASGITransport(app=app)))


async def run_agent(anthropic_client: AsyncAnthropic, api_token: str, prompt: str) -> AgentRun:
    """Let the model work on `prompt` with the MCP tools, as the owner of `api_token`.

    The tools and the server's instructions (the system prompt) are sent on every call, behind a
    cache breakpoint, so the runs after the first one read them from the cache. A failed tool is
    sent back to the model with `is_error`, as a real MCP client would, and the model may retry.

    `anthropic_client` is passed in rather than created here so the harness tests can replace the
    model with a fake one.
    """
    mcp_server = build_server(api_token)

    async with Client(mcp_server) as mcp:
        listed = await mcp.list_tools()
        tools: list[ToolParam] = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in listed.tools
        ]
        system: list[TextBlockParam] = [
            {"type": "text", "text": mcp.instructions or "", "cache_control": {"type": "ephemeral"}}
        ]

        messages: list[MessageParam] = [{"role": "user", "content": prompt}]
        run = AgentRun()

        for _ in range(MAX_TURNS):
            response = await anthropic_client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": "low"},
            )

            run.stop_reason = response.stop_reason
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                run.final_text = ""
                for content in response.content:
                    if content.type == "text":
                        run.final_text += content.text
                return run

            tool_results = []
            for content in response.content:
                if content.type == "tool_use":
                    result = await mcp.call_tool(content.name, content.input)
                    run.tool_calls.append(
                        ToolCall(name=content.name, input=content.input, is_error=result.is_error)
                    )

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": content.id,
                            "content": _text(result),
                            "is_error": result.is_error,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        return run
