"""
La commande des evals : `uv run --env-file .env.evals python -m evals`.

Chaque scénario tourne `RUNS` fois, chacune sur une application neuve : préparation des données,
travail de l'agent, contrôles, puis juge quand le scénario en a un. Une exécution qui plante
compte comme un échec et n'arrête pas les autres. Une ligne par scénario s'affiche dès qu'il est
terminé, avec son taux de réussite et ses messages d'échec, chacun une seule fois.
Équivalent conceptuel d'une commande Artisan dans Laravel : lancée à la main, jamais en CI, car
chaque exécution appelle un vrai modèle et coûte de l'argent.
"""

import asyncio
import logging

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from evals.agent import run_agent
from evals.app import eval_app
from evals.judge import judge
from evals.scenarios import SCENARIOS, Repos, Scenario
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.categories.repository import CategoryRepository
from src.modules.todos.repository import TodoRepository

RUNS = 1


def _repos(session: AsyncSession) -> Repos:
    """The repositories handed to a scenario, all on `session`."""
    return Repos(
        todos=TodoRepository(session),
        categories=CategoryRepository(session),
        approvals=ApprovalRequestRepository(session),
    )


async def _run_once(anthropic_client: AsyncAnthropic, scenario: Scenario) -> list[str]:
    """Run `scenario` once on a fresh app and return its failures (empty when it passed).

    The setup is committed before the agent starts, since the agent's requests open their own
    sessions; the checks read through a new session, after the agent's commits.
    """
    async with eval_app() as (session_factory, user_id, api_token):
        if scenario.setup:
            async with session_factory() as session:
                await scenario.setup(_repos(session), user_id)
                await session.commit()

        run = await run_agent(anthropic_client, api_token, scenario.prompt)

        async with session_factory() as session:
            failures = await scenario.check(run, _repos(session))

            if scenario.judge_criterion:
                judge_failure = await judge(
                    anthropic_client,
                    scenario.judge_criterion,
                    run.final_text,
                )

                if judge_failure:
                    failures.append(judge_failure)

            return failures


async def main() -> None:
    """Run every scenario `RUNS` times and print one line per scenario."""
    # The request logs of httpx (MCP server → API) and httpx2 (Anthropic SDK) would bury the table.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    async with AsyncAnthropic() as anthropic_client:
        for scenario in SCENARIOS:
            passed = 0
            failures: list[str] = []

            for _ in range(RUNS):
                try:
                    run_failures = await _run_once(anthropic_client, scenario)
                except Exception as e:
                    run_failures = [f"Execution crashed: {e!r}"]

                if not run_failures:
                    passed += 1
                else:
                    failures.extend(run_failures)

            print(f"{scenario.name:<35} passed {passed}/{RUNS}")

            for failure in dict.fromkeys(failures):
                print(f"  - {failure}")


if __name__ == "__main__":
    asyncio.run(main())
