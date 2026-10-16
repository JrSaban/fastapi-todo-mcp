"""
L'acteur de la requête en cours : qui agit, et comment il s'est authentifié.

Rempli par l'authentification (`get_current_user`), lu par le journal d'audit au moment
d'enregistrer une écriture. Chaque requête a sa propre valeur ; sans acteur (job planifié),
l'écriture est attribuée au système.
Équivalent conceptuel de auth()->user() dans Laravel, avec en plus la méthode d'authentification.
"""

from contextvars import ContextVar
from dataclasses import dataclass

from src.modules.audit.models import AuditAuthMethod


@dataclass(frozen=True)
class Actor:
    """Who performs the current request, and how they authenticated."""

    user_id: int
    auth_method: AuditAuthMethod
    api_token_id: int | None


current_actor: ContextVar[Actor | None] = ContextVar("current_actor", default=None)
