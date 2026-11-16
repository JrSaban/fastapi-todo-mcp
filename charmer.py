"""
Lanceur de la console : `python -m charmer <commande>` (par exemple `create-admin`).
Il ne fait qu'appeler `src.console.main.main` ; les commandes vivent dans `src/console/`,
monté dans les conteneurs, alors que ce fichier ne l'est pas : il ne doit jamais changer.
Équivalent conceptuel du fichier `artisan` à la racine d'un projet Laravel.
"""

from src.console.main import main

if __name__ == "__main__":
    main()
