# Image de base Python 3.12 allégée
FROM python:3.12-slim

# On crée un utilisateur non-root
RUN useradd --system -ms /usr/sbin/nologin app

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
#    Seulement celles de l'API : ni les outils de dev (pytest, ruff), ni le serveur MCP, ni les evals
RUN uv sync --frozen --no-install-project --no-dev --no-group mcp --no-group evals

# 3. On copie le reste du projet
COPY . .

# 4. On finalise la synchronisation du projet
RUN uv sync --frozen --no-dev --no-group mcp --no-group evals

# 5. L'environnement est figé : sans ça, chaque `uv run` (API, worker, scheduler, alembic)
#    réinstallerait au démarrage les groupes par défaut du projet, que le build a exclus
ENV UV_NO_SYNC=1

# Port d'écoute de l'application
EXPOSE 8000

# On change l'utilisateur pour exécuter l'application
USER app

# Health check : vérifie que l'API répond. /health n'exige pas de token et n'est jamais
# comptée par le rate limit. Python plutôt que curl, absent de l'image slim : rien à installer.
# Le worker et le scheduler partagent l'image sans servir de HTTP : le compose le leur désactive
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s --start-interval=2s --retries=2 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Commande de démarrage par défaut :
# IMPORTANT : --host 0.0.0.0 permet au serveur d'écouter les connexions venant de l'extérieur du conteneur (votre machine hôte)
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
