"""
Les evals des outils MCP : un vrai modèle Claude utilise l'API à travers le serveur MCP.

Ce fichier s'exécute avant tout autre module du paquet, quel que soit l'ordre des imports. Il fixe
donc ici l'environnement que la configuration de `src/` lit à son import : un secret JWT factice,
le broker en mémoire de `APP_ENV=test`, et pas de logs de debug. Le `.env` du développeur n'est
jamais lu pour ces valeurs.
Équivalent conceptuel de `bootstrap/app.php` dans Laravel : ce qui doit être en place avant que
le reste ne démarre.
"""

import os

os.environ["JWT_SECRET_KEY"] = "eval-secret-key-not-for-production-1234567890"
os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "False"
