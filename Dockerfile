# Image de base Python 3.12 allégée
FROM python:3.12-slim

# On récupère le binaire 'uv' directement depuis l'image officielle Astral
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

# Répertoire de travail dans le conteneur
WORKDIR /app

# Variables d'environnement Python standards :
# - PYTHONUNBUFFERED=1 : Affiche les logs immédiatement dans la console Docker sans mise en cache mémoire
# - PYTHONDONTWRITEBYTECODE=1 : Évite de créer des fichiers .pyc inutiles dans l'image
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 1. Optimisation du cache Docker : on copie d'abord les fichiers de dépendances
COPY pyproject.toml uv.lock ./

# 2. On installe les dépendances (si le code change mais pas les dépendances, Docker réutilise le cache)
RUN uv sync --frozen --no-install-project --no-group mcp --no-group evals

# 3. On copie le reste du projet
COPY . .

# 4. On finalise la synchronisation du projet
RUN uv sync --frozen --no-group mcp --no-group evals

# Port d'écoute de l'application
EXPOSE 8000

# Commande de démarrage par défaut :
# IMPORTANT : --host 0.0.0.0 permet au serveur d'écouter les connexions venant de l'extérieur du conteneur (votre machine hôte)
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
