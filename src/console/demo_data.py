"""
Données de la commande `seed-demo`. Les catégories et les todos sont rangés sous l'email de leur
propriétaire, et un todo désigne ses catégories par leur nom : leurs id n'existent qu'une fois
créés en base. Aucun mot de passe ici : la commande les tire au hasard. Les emails sont en
`example.com`, un domaine réservé (RFC 2606) qui n'appartient à personne.
"""

from typing import NotRequired, TypedDict

from src.modules.categories.schemas import CategoryCreate


class DemoUser(TypedDict):
    """A demo account: the fields of `UserCreate` but the password, plus whether it is the
    admin."""

    email: str
    first_name: str
    last_name: str
    pseudo: str
    is_admin: NotRequired[bool]


class DemoTodo(TypedDict):
    """A demo todo: its categories by name, and the state to leave it in once created."""

    title: str
    description: NotRequired[str]
    categories: NotRequired[list[str]]
    is_completed: NotRequired[bool]
    is_deleted: NotRequired[bool]


USERS: list[DemoUser] = [
    {
        "email": "admin@example.com",
        "first_name": "Admin",
        "last_name": "Demo",
        "pseudo": "admindemo",
        "is_admin": True,
    },
    {
        "email": "johndoe@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "pseudo": "johndoe",
    },
    {
        "email": "janedoe@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "pseudo": "janedoe",
    },
]

CATEGORIES: dict[str, list[CategoryCreate]] = {
    "admin@example.com": [CategoryCreate(name="Home", color="#8FBC8F")],
    "johndoe@example.com": [
        CategoryCreate(name="Work"),
        CategoryCreate(name="Sport"),
    ],
    "janedoe@example.com": [
        CategoryCreate(name="Family", color="#FF69B4"),
        CategoryCreate(name="Food"),
    ],
}

TODOS: dict[str, list[DemoTodo]] = {
    "admin@example.com": [
        {
            "title": "Call plumber",
        },
        {
            "title": "Change WiFi password",
            "description": "Change WiFi password to something more secure",
            "categories": ["Home"],
        },
        {
            "title": "Run 5km",
            "description": "Run 5km for fitness",
            "categories": ["Sport"],
            "is_completed": True,
        },
        {
            "title": "Build demo data",
            "description": "Need to add demo data to the app",
            "categories": ["Work"],
            "is_completed": True,
        },
        {
            "title": "Make sandwiches for kids lunch",
            "description": "Kids want ham and cheese sandwiches for lunch.",
            "categories": ["Food", "Family"],
            "is_completed": True,
            "is_deleted": True,
        },
    ],
    "johndoe@example.com": [
        {
            "title": "Buy milk",
        },
        {
            "title": "Fix the roof",
            "description": "The roof is leaking",
            "categories": ["Home", "Work"],
        },
        {
            "title": "KravMaga training",
            "description": "Train KravMaga for fitness",
            "categories": ["Sport"],
            "is_completed": True,
        },
        {
            "title": "Buy birthday present for daughter",
            "description": "Need to buy birthday present for daughter",
            "categories": ["Work", "Family"],
        },
        {
            "title": "Ask for raise",
            "description": "I've been working hard and I think I deserve a raise",
            "categories": ["Work"],
            "is_deleted": True,
        },
        {
            "title": "Dinner for kids good grades",
            "description": "The kids got good grades. Let's celebrate with dinner.",
            "categories": ["Food", "Family"],
            "is_completed": True,
        },
        {
            "title": "Soccer tournament on Saturday",
            "description": "Kids soccer tournament on Saturday.",
            "categories": ["Sport", "Family"],
            "is_completed": True,
            "is_deleted": True,
        },
        {
            "title": "Haircut",
            "description": "Get a haircut",
            "is_completed": True,
            "is_deleted": True,
        },
    ],
    "janedoe@example.com": [
        {
            "title": "Book dentist appointment",
        },
        {
            "title": "Plan weekly meals",
            "description": "Pick five dinners and write the shopping list.",
            "categories": ["Food", "Home"],
        },
        {
            "title": "Yoga class",
            "description": "Tuesday evening class at the community center.",
            "categories": ["Sport"],
            "is_completed": True,
        },
        {
            "title": "Prepare quarterly report",
            "description": "Gather the sales figures and draft the summary for Friday.",
            "categories": ["Work"],
        },
        {
            "title": "Call grandma",
            "description": "She wants to hear about the holidays.",
            "categories": ["Family"],
            "is_completed": True,
        },
        {
            "title": "Bake a cake for Sunday",
            "description": "Chocolate cake for the family lunch.",
            "categories": ["Food", "Family"],
        },
        {
            "title": "Renew gym membership",
            "categories": ["Sport"],
            "is_completed": True,
            "is_deleted": True,
        },
        {
            "title": "Answer client emails",
            "description": "Three clients are waiting for a quote.",
            "categories": ["Work"],
            "is_completed": True,
        },
    ],
}
