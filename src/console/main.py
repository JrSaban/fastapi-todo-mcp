"""
Point d'entrée de la console `charmer` : lit la ligne de commande et lance la commande choisie.
Chaque commande vit dans son propre fichier de `src/console/` et reçoit les arguments lus.
Équivalent conceptuel du Kernel console dans Laravel, que le fichier `artisan` appelle ;
ici, c'est `charmer.py`, à la racine.
"""

import argparse

from src.console.create_admin import create_admin
from src.console.seed_demo import seed_demo


def main() -> None:
    """Parse the command line and run the chosen command."""
    parser = argparse.ArgumentParser(
        prog="charmer",
        description="Console application for To-Do List Management",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin_parser = subparsers.add_parser("create-admin", help="Create an admin user.")
    create_admin_parser.set_defaults(func=create_admin)

    seed_demo_parser = subparsers.add_parser(
        "seed-demo", help="Create demo users, categories and todos."
    )
    seed_demo_parser.set_defaults(func=seed_demo)

    args = parser.parse_args()
    args.func(args)
