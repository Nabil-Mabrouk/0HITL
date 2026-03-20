# 🚀 0-HITL (Zero Human In The Loop)

L'Assistant IA Autonome, Sécurisé et Auto-Apprenant (Next-Gen).
Basé sur une architecture "Deep Intelligence" avec Pydantic, LiteLLM et un Sandbox Docker.

## 👁️ La Vision : L'Autonomie Absolue
0-HITL est un système d'exploitation pour agents IA conçu pour l'indépendance totale. Il exécute, échoue, apprend de ses erreurs et s'auto-répare.

## 🛠️ Architecture
- **Engine (Le Cerveau)** : Boucle de raisonnement asynchrone (LiteLLM).
- **SecureRunner (L'Armure)** : Isolation des outils dans des micro-conteneurs Docker.
- **SuperEgo (La Conscience)** : Guardrails heuristiques avant exécution.
- **SkillManager (L'Extension)** : Chargement dynamique de compétences (JIT).
- **3-Tier Memory** : RAM (Hot), JSONL (Session/Arbre), SQLite (Neural/RAG).
- **Cognitive Resilience** : Auto-réparation et apprentissage des erreurs.

## 📦 Installation
1. Assurez-vous d'avoir **Docker** et **Docker Compose** installés.
2. Clonez ce dépôt.
3. Remplissez le fichier `.env` avec vos clés API.
4. Lancez le script de déploiement automatique :

```bash
chmod +x setup-0-hitl.sh
./setup-0-hitl.sh
```

## 🚀 Utilisation
Le système démarre la Gateway FastAPI. Vous pouvez interagir avec l'API sur le port `8000`.

- **API REST** : `POST http://localhost:8000/chat`
- **WebSocket (Mission Control)** : `ws://localhost:8000/ws/mission-control/{session_id}`

## 🧩 Création de Skills
Ajoutez un dossier dans `/skills` avec :
- `skill.yaml` : Permissions.
- `SKILL.md` : Instructions pour l'IA.
- `tools.py` : Fonctions Python décorées par `@tool`.
