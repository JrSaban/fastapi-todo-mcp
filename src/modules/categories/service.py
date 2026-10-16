"""
Service Layer (Logique métier pour les Categories).
"""

from src.modules.audit.changes import changed_fields
from src.modules.audit.service import AuditLogService
import json
from collections.abc import Sequence
from typing import Any, cast

import pydantic
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.core.redis import hash_redis_key
from src.modules.categories.models import Category
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.schemas import CategoryCreate, CategoryResponse, CategoryUpdate
from src.modules.users.models import User

REDIS_TTL = 60 * 60 * 8

logger = structlog.get_logger()


class CategoryService:
    def __init__(
        self,
        repository: CategoryRepository,
        redis_client: Redis,
        audit_service: AuditLogService
    ) -> None:
        self.repository = repository
        self.redis_client = redis_client
        self.audit = audit_service

    def _redis_key_category(self, *, key: int) -> str:
        """Get the Redis key for a category."""
        return f"category:{key}"

    def _redis_key_categories_list(self, *, gen: int = 0, keys: dict[str, Any]) -> str:
        """Get the Redis key for the list of categories."""
        filtered_key = {k: v for k, v in keys.items() if v is not None}
        return f"categories:list:{gen}:{hash_redis_key(json.dumps(filtered_key, sort_keys=True))}"

    async def _fetch_from_db(
        self, skip: int, limit: int, name: str | None
    ) -> tuple[Sequence[CategoryResponse], int]:
        categories = await self.repository.get_all(skip=skip, limit=limit, name=name)
        categories_resp = [CategoryResponse.model_validate(cat) for cat in categories]
        total = await self.repository.count(name=name)

        return (categories_resp, total)

    async def _invalidate_cache(self, *, key: int | None = None) -> None:
        """Invalidate cache."""
        try:
            await self.redis_client.incr("categories:list:gen")

            if key is not None:
                await self.redis_client.delete(self._redis_key_category(key=key))
        except RedisError:
            logger.warning("redis_unavailable", operation="invalidate_cache", exc_info=True)

    async def list_categories(
        self, skip: int = 0, limit: int = 25, name: str | None = None
    ) -> tuple[Sequence[CategoryResponse], int]:
        """Fetch all categories with pagination."""
        if name is not None:
            return await self._fetch_from_db(skip, limit, name)

        try:
            gen = cast(str | None, await self.redis_client.get("categories:list:gen"))
        except RedisError:
            gen = 0

        redis_key = self._redis_key_categories_list(
            gen=int(gen) if gen else 0,
            keys={"skip": skip, "limit": limit},
        )

        try:
            cached_response = await self.redis_client.get(redis_key)

            if cached_response:
                # Parse la réponse JSON en objet Python
                response_data = json.loads(cached_response)

                # Convertis les listes d'objets en Pydantic model
                categories = [
                    CategoryResponse.model_validate(cat) for cat in response_data["categories"]
                ]

                return (categories, int(response_data["total"]))
        except RedisError:
            logger.warning("redis_unavailable", operation="get_list_categories", exc_info=True)
        except (KeyError, TypeError, ValueError):
            logger.warning("cache_corrupted", operation="get_list_categories", exc_info=True)

        categories_resp, total = await self._fetch_from_db(skip, limit, name)

        try:
            await self.redis_client.set(
                redis_key,
                json.dumps(
                    {
                        "categories": [cat.model_dump(mode="json") for cat in categories_resp],
                        "total": total,
                    }
                ),
                ex=REDIS_TTL,
            )
        except RedisError:
            logger.warning("redis_unavailable", operation="set_list_categories", exc_info=True)

        return (categories_resp, total)

    async def get_category_cached(self, entity_id: int) -> CategoryResponse:
        """
        Récupère une catégorie via Redis
        Si absent, récupère via le repository et enregistre dans Redis
        """
        key = self._redis_key_category(key=entity_id)

        try:
            category = await self.redis_client.get(key)

            if category:
                return CategoryResponse.model_validate_json(category)
        except RedisError:
            logger.warning("redis_unavailable", operation="get_category", exc_info=True)
        except pydantic.ValidationError:
            logger.warning("cache_corrupted", operation="get_category", exc_info=True)

        category = await self.get_category_or_404(entity_id)
        category_response = CategoryResponse.model_validate(category)

        try:
            await self.redis_client.set(key, category_response.model_dump_json(), ex=REDIS_TTL)
        except RedisError:
            logger.warning("redis_unavailable", operation="set_category", exc_info=True)

        return category_response

    async def get_category_or_404(self, entity_id: int) -> Category:
        """Récupère une catégorie ou lève une exception HTTP 404."""
        category = await self.repository.get_by_id(entity_id)
        if not category:
            raise NotFoundError(f"Catégorie avec l'ID {entity_id} introuvable.")
        return category

    async def create_category(self, created_by_id: int, data: CategoryCreate) -> Category:
        """Crée une nouvelle catégorie."""
        try:
            category = await self.repository.create(created_by_id=created_by_id, data=data)
            await self._invalidate_cache()
            await self.audit.log_created(entity=category)
            return category
        except IntegrityError:
            raise ConflictError(f"Une catégorie avec le nom {data.name} existe déjà.") from None

    async def update_category(self, user: User, entity_id: int, data: CategoryUpdate) -> Category:
        """Met à jour une catégorie existante."""
        category = await self.get_category_or_404(entity_id)

        if not user.is_admin and category.created_by_id != user.id:
            raise ForbiddenError("Vous n'avez pas le droit de modifier cette catégorie.")

        try:
            updated = await self.repository.update(category, data)
        except IntegrityError:
            raise ConflictError(f"Une catégorie avec le nom {data.name} existe déjà.") from None

        changes = changed_fields(category, data.model_dump(exclude_unset=True))
        await self.audit.log_updated(entity=updated, changed_fields=changes)

        # Invalidate cache
        await self._invalidate_cache(key=entity_id)
        return updated

    async def delete_category(self, user: User, entity_id: int) -> None:
        """Supprime une categorie existante."""
        if not user.is_admin:
            raise ForbiddenError("Vous n'avez pas le droit de supprimer cette catégorie.")

        category = await self.get_category_or_404(entity_id)
        await self.repository.delete(category)
        await self.audit.log_deleted(entity=category)

        # Invalidate cache
        await self._invalidate_cache(key=entity_id)
