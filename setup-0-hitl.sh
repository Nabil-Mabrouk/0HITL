#!/bin/bash

# --- 0-HITL DEPLOYMENT SCRIPT (v1.0-ALPHA) ---
set -e

echo "🚀 [0-HITL] Initializing deployment..."

# 1. Docker check
if ! [ -x "$(command -v docker)" ]; then
  echo "❌ Error: Docker is not installed. Installation required."
  exit 1
fi

# 2. Creating directory structure
echo "📂 Creating system directories..."
mkdir -p core gateway skills profiles workspace

# 3. Creating .env file (if nonexistent)
if [ ! -f .env ]; then
  echo "📝 Configuring API Keys (leave empty if unknown)..."
  read -p "OpenAI Key (sk-...): " openai
  read -p "Anthropic Key (sk-...): " anthropic
  read -p "VirusTotal Key: " vt
  
  cat <<EOF > .env
OPENAI_API_KEY=$openai
ANTHROPIC_API_KEY=$anthropic
VIRUSTOTAL_API_KEY=$vt
HOST_WORKSPACE_PATH=$(pwd)/workspace
EOF
  echo "✅ .env file created."
fi

# 4. Critical permissions for Docker-out-of-Docker
echo "🛡️ Configuring Docker permissions..."
sudo chmod 666 /var/run/docker.sock || true

# 5. Build and Launch
echo "🏗️ Building Brain (Docker Image)..."
docker compose build

echo "⚡ Launching 0-HITL in Daemon mode..."
docker compose up -d

echo "------------------------------------------------"
echo "✅ DEPLOYMENT SUCCESSFUL !"
echo "🌐 Dashboard : http://localhost:8000"
echo "📜 Logs : docker compose logs -f"
echo "------------------------------------------------"
