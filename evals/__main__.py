import asyncio
import logging

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from evals.agent import run_agent
from evals.app import eval_app
from evals.scenarios import SCENARIOS, Repos, Scenario
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.categories.repository import CategoryRepository
from src.modules.todos.repository import TodoRepository

RUNS = 1


def _repos(session: AsyncSession) -> Repos:
    return Repos(
        todos=TodoRepository(session),
        categories=CategoryRepository(session),
        approvals=ApprovalRequestRepository(session),
    )


async def _run_once(anthropic_client: AsyncAnthropic, scenario: Scenario) -> list[str]:
    async with eval_app() as (session_factory, user_id, api_token):
        if scenario.setup:
            async with session_factory() as session:
                await scenario.setup(_repos(session), user_id)
                await session.commit()

        run = await run_agent(anthropic_client, api_token, scenario.prompt)

        async with session_factory() as session:
            return await scenario.check(run, _repos(session))


async def main() -> None:
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
