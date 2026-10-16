"""
Le contrat qu'un modèle respecte pour être enregistré dans le journal d'audit.

Un modèle audité déclare son type de ressource et son propriétaire ; son `id` lui vient déjà de
`Base`. Le contrat est vérifié par la forme (`Protocol`) : un modèle n'a pas à en hériter, il lui
suffit d'avoir ces attributs. Le service d'audit en déduit seul la ressource et son propriétaire.
Équivalent conceptuel du trait LogsActivity de spatie/laravel-activitylog dans Laravel.
"""

from typing import ClassVar, Protocol

from sqlalchemy.orm import Mapped

from src.modules.audit.models import AuditResourceType


class Auditable(Protocol):
    """A model the audit log can record: its resource type, its ID and its owner.

    `id` and `audit_owner_id` are read-only properties rather than plain `int` attributes: a
    mapped column is a descriptor, which a plain attribute in a protocol wouldn't accept.
    """

    audit_resource_type: ClassVar[AuditResourceType]

    id: Mapped[int]

    @property
    def audit_owner_id(self) -> int:
        """ID of the user the resource belongs to, who sees its audit entries."""
        ...
