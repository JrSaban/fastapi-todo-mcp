"""
Broker Taskiq : l'intermédiaire entre l'API, qui dépose des jobs, et les workers, qui les exécutent.
Équivalent conceptuel de la connexion `redis` de config/queue.php et du scheduler dans Laravel.

- `broker` : la file des jobs et le stockage de leurs résultats, dans Redis. Sous pytest
  (APP_ENV == "test"), un broker en mémoire qui exécute chaque job immédiatement.
- `scheduler` : dépose les jobs planifiés dans la file à leur échéance, sans rien exécuter.
  Un seul exemplaire doit tourner, sinon chaque job planifié s'exécute plusieurs fois.

Les jobs vivent dans les fichiers `tasks.py` des modules, découverts par le worker :
    taskiq worker src.core.broker:broker --fs-discover          # équivalent de queue:work
    taskiq scheduler src.core.broker:scheduler --fs-discover    # équivalent de schedule:run
"""

from taskiq import AsyncBroker, InMemoryBroker, TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq.serializers import JSONSerializer
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from src.core.config import settings
from src.core.logging import setup_logging
from src.modules.jobs.middleware import JobTrackingMiddleware

setup_logging()


def _build_broker() -> AsyncBroker:
    """Redis broker, or an in-memory one under tests."""
    if settings.APP_ENV == "test":
        return InMemoryBroker(await_inplace=True).with_middlewares(JobTrackingMiddleware())

    backend = RedisAsyncResultBackend(
        redis_url=settings.REDIS_URL,
        result_ex_time=settings.TASK_RESULT_TTL_HOURS * 3600,
        prefix_str="taskiq:result",
        serializer=JSONSerializer(),
    )

    return (
        RedisStreamBroker(url=settings.REDIS_URL, queue_name="taskiq:queue")
        .with_result_backend(backend)
        .with_middlewares(JobTrackingMiddleware())
    )


broker = _build_broker()
scheduler = TaskiqScheduler(
    broker=broker,
    sources=[LabelScheduleSource(broker)],
)
