FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    docker.io \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copie des fichiers de config
COPY pyproject.toml README.md ./

# Installation des dépendances avec uvicorn[standard]
RUN uv pip install --system --no-cache-dir -r pyproject.toml

# Copie du code
COPY . .

# On s'assure que le dossier static existe pour le dashboard
RUN mkdir -p gateway/static

EXPOSE 8000

# Lancement propre
CMD ["python", "main.py"]