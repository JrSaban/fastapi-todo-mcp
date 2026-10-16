"""
Calcul des champs modifiés par une mise à jour, pour le journal d'audit.

Seuls les noms des champs dont la valeur change sont gardés, jamais les valeurs. Le calcul se
fait avant d'écrire, en comparant l'entité à ce qui va lui être affecté. Une relation (comme les
catégories d'un todo) n'est pas une colonne : le service concerné la compare lui-même.
Équivalent conceptuel de isDirty() / getDirty() d'Eloquent dans Laravel.
"""

from typing import Any


def changed_fields(entity: object, values: dict[str, Any]) -> list[str]:
    """Return the names of the fields whose new value differs from the entity's current one.

    Call it before the update is applied. A value sent unchanged is not a change, so an update
    that changes nothing returns an empty list.
    """

    changes = []
    for field, new in values.items():
        old = getattr(entity, field)

        if old != new:
            changes.append(field)

    return changes
