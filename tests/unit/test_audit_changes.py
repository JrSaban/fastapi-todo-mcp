import pytest

from src.modules.audit.changes import changed_fields
from src.modules.todos.models import Todo


def _todo() -> Todo:
    return Todo(id=1, owner_id=1, title="Titre", description=None, is_completed=False)


def test_a_value_sent_unchanged_is_not_a_change():
    assert changed_fields(_todo(), {"title": "Titre", "is_completed": False}) == []


def test_only_the_fields_whose_value_differs_are_returned():
    assert changed_fields(_todo(), {"title": "Titre", "is_completed": True}) == ["is_completed"]


def test_filling_an_empty_field_is_a_change():
    """None → a value is a real change, the case a "skip when None" shortcut would miss."""
    assert changed_fields(_todo(), {"description": "Nouvelle"}) == ["description"]


def test_clearing_a_field_is_a_change():
    todo = _todo()
    todo.description = "Ancienne"

    assert changed_fields(todo, {"description": None}) == ["description"]


def test_an_unknown_field_fails_instead_of_being_ignored():
    """A typo in a field name must surface, not silently drop the change from the log."""
    with pytest.raises(AttributeError):
        changed_fields(_todo(), {"titel": "Titre"})
