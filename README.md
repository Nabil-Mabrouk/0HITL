# 0-HITL

0-HITL ("Zero Human In The Loop") est un prototype d'agent autonome en Python qui combine :

- une boucle de raisonnement basee sur LiteLLM
- une execution d'outils isolee dans des conteneurs Docker
- une passerelle FastAPI avec telemetrie temps reel
- un systeme de skills chargees a la demande

Le projet vise une architecture d'"Agent OS", mais l'etat actuel du depot correspond surtout a un proof of concept exploitable et documente ci-dessous.

## Etat Du Projet

Le code implemente deja les briques suivantes :

- API `POST /chat` avec sessions memorisees en memoire
- streaming de pensee et d'evenements via WebSocket
- execution de commandes dans un runtime Docker persistant par session
- isolation du workspace par session
- generation et exposition d'artefacts via des URLs `/session-files/...`
- chargement dynamique de skills depuis `./skills`
- garde-fous heuristiques avant execution d'outils
- journalisation JSONL de session et archive SQLite simple
- authentification locale avec bootstrap du premier owner, cookies de session et comptes locaux

Certaines promesses presentes dans les notes de conception ou l'ancien README ne sont pas encore completement branchees :

- multi-agent complet
- veritable RAG vectoriel
- scan VirusTotal integre au flux principal
- durcissement reseau / production plus pousse

## Architecture

### Flux principal

1. Le client appelle `POST /chat`.
2. `ZeroHitlEngine` enrichit le contexte avec le profil actif et les souvenirs utiles.
3. Le LLM repond en texte ou en appels d'outils.
4. Les outils sont controles par `SuperEgo`, puis executes via le registre d'outils.
5. Les commandes shell partent dans `SecureRunner`, qui lance ou reutilise un conteneur Docker de session.
6. Les evenements sont diffuses en temps reel au dashboard via WebSocket.
7. Les sorties utiles sont archivees dans la base de memoire longue et les traces de session sont ecrites en JSONL.

### Composants principaux

- `core/engine.py` : boucle agentique, streaming, tool calls, retries et livraison d'artefacts
- `core/runner.py` : sandbox Docker persistant par session, avec mode reseau online/offline
- `core/skills.py` : catalogue de skills et chargement dynamique des outils
- `core/superego.py` : analyse heuristique de risque avant execution
- `core/memory.py` : archive SQLite simple et logs JSONL de session
- `core/auth.py` : comptes locaux, sessions persistantes et bootstrap owner
- `gateway/api.py` : API FastAPI, WebSocket et serveur de fichiers de session
- `gateway/static/` : dashboard minimal "Mission Control"

## Arborescence

```text
0-hitl/
|-- core/                 # Coeur agentique
|-- gateway/              # API FastAPI + dashboard statique
|-- profiles/             # Prompts systeme / profils
|-- skills/               # Skills chargees a la demande
|-- workspace/            # Donnees de session et artefacts
|-- docs/context.md       # Notes de conception / historique de spec
|-- docker-compose.yml
|-- Dockerfile
|-- main.py
`-- pyproject.toml
```

## Prerequis

### Recommandes

- Docker
- Docker Compose
- une cle API compatible LiteLLM
  - `GROQ_API_KEY`
  - `OPENAI_API_KEY`
  - ou `ANTHROPIC_API_KEY`

### Pour un lancement local hors Docker

- Python 3.12+
- les dependances du `pyproject.toml`
- Docker local accessible, car les outils shell reposent quand meme sur `SecureRunner`

## Configuration

Le projet lit principalement les variables suivantes :

| Variable | Obligatoire | Role |
| --- | --- | --- |
| `GROQ_API_KEY` | Non, mais recommande | Cle Groq ; si presente, 0-HITL prefere par defaut les modeles Groq pour l'agent et la consolidation memoire |
| `OPENAI_API_KEY` | Non, mais utile | Cle OpenAI pour les appels LLM |
| `ANTHROPIC_API_KEY` | Non, mais utile | Alternative OpenAI |
| `VIRUSTOTAL_API_KEY` | Non | Prevue pour le scan de contenu, pas encore branchee dans le flux principal |
| `HOST_WORKSPACE_PATH` | Optionnel | Override du chemin hote du workspace, surtout utile hors Docker Compose |
| `HOST_SKILLS_PATH` | Optionnel | Override du chemin hote des skills, surtout utile hors Docker Compose |
| `HITL_MODEL` | Optionnel | Override legacy pour forcer un seul modele LiteLLM partout |
| `HITL_MODEL_AGENT` | Optionnel | Modele principal pour le chat, le raisonnement et les tools |
| `HITL_MODEL_MEMORY` | Optionnel | Modele dedie a la consolidation post-session |
| `HITL_MODEL_DEEP_REASONING` | Optionnel | Modele de reference pour les futures missions plus lourdes |
| `HITL_MODEL_CODING` | Optionnel | Modele de reference pour coding / tool use difficile |
| `HITL_MODEL_MULTILINGUAL` | Optionnel | Modele de reference pour scenarios multilingues |
| `HITL_MODEL_VISION` | Optionnel | Modele de reference pour vision / multimodal |
| `HITL_MODEL_GENERAL_FALLBACK` | Optionnel | Fallback textuel generaliste |
| `HITL_MODEL_SAFETY` | Optionnel | Modele de reference pour moderation / classification |
| `HITL_MEMORY_DB_PATH` | Optionnel | Chemin SQLite de la memoire longue, par defaut `./workspace/system/memory.db` |
| `HITL_AUTH_DB_PATH` | Optionnel | Chemin SQLite de l'auth locale, par defaut `./workspace/system/auth.db` |
| `HITL_TASKS_DB_PATH` | Optionnel | Chemin SQLite des taches locales, par defaut `./workspace/system/tasks.db` |
| `HITL_AUTH_SESSION_COOKIE` | Optionnel | Nom du cookie de session HTTP |
| `HITL_AUTH_SESSION_DAYS` | Optionnel | Duree de validite d'une session navigateur |
| `HITL_AUTH_SECURE_COOKIE` | Optionnel | Active le flag `Secure` du cookie, utile derriere HTTPS |
| `HITL_TELEGRAM_ENABLED` | Optionnel | Active le connecteur Telegram v1 en long polling |
| `TELEGRAM_BOT_TOKEN` | Optionnel | Token du bot Telegram de l'instance |
| `HITL_TELEGRAM_API_BASE` | Optionnel | Base URL de l'API Telegram, par defaut `https://api.telegram.org` |
| `HITL_TELEGRAM_POLL_TIMEOUT` | Optionnel | Timeout du long polling Telegram en secondes |
| `HITL_TELEGRAM_LINK_CODE_TTL_MINUTES` | Optionnel | Duree de validite des codes `/link` generes par l'API |
| `HITL_TELEGRAM_MAX_MESSAGE_CHARS` | Optionnel | Taille max d'un message Telegram avant decoupage |
| `HITL_CORS_ALLOW_ORIGINS` | Optionnel | Liste d'origines navigateur autorisees, vide par defaut donc CORS ferme |
| `HITL_CORS_ALLOW_METHODS` | Optionnel | Methodes CORS autorisees si des origines sont configurees |
| `HITL_CORS_ALLOW_HEADERS` | Optionnel | Headers CORS acceptes si des origines sont configurees |
| `HITL_CORS_EXPOSE_HEADERS` | Optionnel | Headers exposes au navigateur |
| `HITL_CORS_ALLOW_CREDENTIALS` | Optionnel | Autorise les cookies cross-origin, a utiliser avec des origines explicites |

Exemple minimal :

```env
GROQ_API_KEY=
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=
VIRUSTOTAL_API_KEY=
HOST_WORKSPACE_PATH=
HOST_SKILLS_PATH=
HITL_MODEL=
HITL_MODEL_AGENT=
HITL_MODEL_MEMORY=
HITL_MODEL_DEEP_REASONING=groq/openai/gpt-oss-120b
HITL_MODEL_CODING=groq/moonshotai/kimi-k2-instruct-0905
HITL_MODEL_MULTILINGUAL=groq/qwen/qwen3-32b
HITL_MODEL_VISION=groq/meta-llama/llama-4-scout-17b-16e-instruct
HITL_MODEL_GENERAL_FALLBACK=groq/llama-3.3-70b-versatile
HITL_MODEL_SAFETY=groq/openai/gpt-oss-safeguard-20b
HITL_MEMORY_DB_PATH=./workspace/system/memory.db
HITL_AUTH_DB_PATH=./workspace/system/auth.db
HITL_TASKS_DB_PATH=./workspace/system/tasks.db
HITL_AUTH_SESSION_COOKIE=zero_hitl_session
HITL_AUTH_SESSION_DAYS=30
HITL_AUTH_SECURE_COOKIE=false
HITL_TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
HITL_TELEGRAM_API_BASE=https://api.telegram.org
HITL_TELEGRAM_POLL_TIMEOUT=30
HITL_TELEGRAM_LINK_CODE_TTL_MINUTES=10
HITL_TELEGRAM_MAX_MESSAGE_CHARS=3500
HITL_CORS_ALLOW_ORIGINS=
HITL_CORS_ALLOW_METHODS=GET,POST,OPTIONS
HITL_CORS_ALLOW_HEADERS=Content-Type
HITL_CORS_EXPOSE_HEADERS=
HITL_CORS_ALLOW_CREDENTIALS=true
```

Un fichier d'exemple est fourni dans [`.env.example`](/C:/Projects/000-0-HITL/.env.example). Le plus simple est de le copier vers `.env`, puis de renseigner vos vraies cles.

Important :

- ne versionnez pas vos secrets
- sous Docker Compose, 0-HITL autodetecte maintenant les bind mounts de `workspace/` et `skills/`
- si vous utilisez `HOST_WORKSPACE_PATH` et `HOST_SKILLS_PATH`, preferez des chemins absolus
- pour un lancement local hors Docker, des chemins relatifs comme `./workspace` et `./skills` peuvent suffire
- le `CORS` est ferme par defaut ; configurez `HITL_CORS_ALLOW_ORIGINS` uniquement si un frontend externe doit appeler l'API
- si `GROQ_API_KEY` est present et qu'aucun override explicite n'est fourni, 0-HITL prefere `groq/openai/gpt-oss-20b` pour l'agent et la memoire
- si vous forcez `HITL_MODEL_AGENT`, la memoire suit ce modele par defaut tant que `HITL_MODEL_MEMORY` n'est pas defini
- si vous lancez le projet hors Docker, exportez vos variables dans le shell avant demarrage
- certains composants lisent l'environnement tres tot au chargement des modules

## Demarrage Rapide

### Option 1 : Docker Compose (chemin recommande)

1. Copiez le fichier d'exemple :

```bash
cp .env.example .env
```

2. Renseignez vos cles API.

3. Lancez ensuite :

```bash
docker compose up --build
```

L'application expose ensuite :

- Health check : `http://localhost:8000/health`
- API : `http://localhost:8000/chat`
- WebSocket : `ws://localhost:8000/ws/mission-control/{session_id}`
- Dashboard : `http://localhost:8000/dashboard`

Notes :

- le `setup-0-hitl.sh` est surtout pense pour Linux / WSL et automatise la creation du `.env` puis le `docker compose up`
- `docker-compose.yml` monte `./workspace` et `./skills` directement, puis le runner re-detecte leurs chemins hotes pour les sous-conteneurs
- le dashboard est servi sous `/dashboard`, pas sur la racine `/`

Premiere connexion :

- au premier acces, ouvrez `http://localhost:8000/dashboard`
- 0-HITL vous demandera de creer le premier compte `owner`
- ce compte protege ensuite `POST /chat`, `/session-files/...` et le WebSocket

### Option 2 : lancement local de developpement

Créez d'abord un environnement virtuel :

```bash
python -m venv .venv
```

Activez-le :

```powershell
.venv\Scripts\Activate.ps1
```

```bash
source .venv/bin/activate
```

Installez ensuite les dependances du projet :

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Puis lancez l'application :

```bash
python main.py
```

Ou avec Uvicorn :

```bash
uvicorn gateway.api:app --host 0.0.0.0 --port 8000 --reload
```

Le lancement local est pratique pour iterer sur FastAPI et le moteur, mais les outils shell ont toujours besoin d'un Docker fonctionnel en local.

## Utilisation

### Premiere connexion et session navigateur

0-HITL utilise maintenant une auth locale basee sur des comptes stockes sur l'instance.

- si aucun utilisateur n'existe, commencez par `POST /auth/bootstrap` ou passez par le dashboard
- ensuite connectez-vous via `POST /auth/login`
- le serveur depose un cookie HTTP-only reutilise par le dashboard et le WebSocket
- `GET /health` reste public ; les routes sensibles demandent une session valide

Endpoints d'auth utiles :

- `GET /auth/setup-status` : savoir si l'instance attend encore le premier owner
- `POST /auth/bootstrap` : creer le premier compte `owner`
- `POST /auth/login` : ouvrir une session navigateur
- `POST /auth/logout` : fermer la session courante
- `GET /auth/me` : recuperer l'utilisateur courant
- `GET /auth/users` et `POST /auth/users` : lister / creer des comptes locaux cote owner
- `GET /sessions/{sid}/permissions` : lister les partages explicites d'une session possedee
- `POST /sessions/{sid}/permissions` : accorder un acces `viewer` ou `operator` a un autre compte local
- `DELETE /sessions/{sid}/permissions/{username}` : revoquer un partage explicite
- `POST /sessions/{sid}/emergency-stop` : arreter une session et demander l'arret de son runtime Docker

Le dashboard inclut aussi un panneau `Local Accounts` visible pour le `owner`, afin de creer rapidement des comptes `admin` ou `member`.

### Partage de session

Les sessions sont privees par defaut. Un proprietaire peut partager une session precise avec un autre compte local.

- une session possedee est referencee simplement par `session_id`
- une session partagee est referencee par `owner_username:session_id`
- `viewer` : peut lire les artefacts de session et suivre la telemetrie WebSocket
- `operator` : herite de `viewer` et peut aussi declencher `EMERGENCY STOP`
- seul le proprietaire peut utiliser `POST /chat` sur sa propre session

Ce premier niveau de permissions vise la consultation et l'arret d'urgence. Il n'ouvre pas encore un vrai mode de collaboration ecriture/chat sur une session partagee.

### Endpoint `GET /health`

Permet de verifier rapidement que l'API est demarree.

Exemple de reponse :

```json
{
  "status": "ok",
  "service": "0-hitl",
  "active_sessions": 0,
  "models": {
    "agent": "groq/openai/gpt-oss-20b",
    "memory": "groq/openai/gpt-oss-20b"
  }
}
```

### Endpoint `POST /chat`

Authentification requise via cookie de session.

Corps attendu :

```json
{
  "user_input": "Create a script that prints hello",
  "session_id": "demo-session"
}
```

- `session_id` est optionnel
- si omis, une session UUID est creee
- si fourni, la session est reutilisee cote serveur
- cote backend, les sessions sont scopees par utilisateur pour eviter les collisions entre comptes
- les references partagees du type `owner_username:session_id` sont refusees ici pour les non-proprietaires
- en pratique, `POST /chat` reste reserve au proprietaire de la session

Exemple :

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"user_input\":\"List the files in the workspace\",\"session_id\":\"demo-session\"}"
```

Reponse typique :

```json
{
  "session_id": "demo-session",
  "response": "..."
}
```

### WebSocket Mission Control

Authentification requise via le meme cookie de session que l'API HTTP.

Connectez-vous au WebSocket d'une session pour suivre en direct :

- `THOUGHT_START`
- `THOUGHT`
- `TOOL_START`
- `TOOL_SUCCESS`
- `TOOL_ERROR`
- `SECURITY_WARNING`
- `SECURITY_ALERT`
- `EMERGENCY_STOP`
- `MEMORY_HIT`
- `RUNTIME_STATUS`

Exemple d'URL :

```text
ws://localhost:8000/ws/mission-control/demo-session
```

Si une session vous a ete partagee explicitement, utilisez plutot une reference du type :

```text
ws://localhost:8000/ws/mission-control/owner:demo-session
```

Un acces `viewer` suffit pour suivre la telemetrie d'une session partagee.

Pour une bonne experience, ouvrez le WebSocket avant d'envoyer la requete `POST /chat`.

### Fichiers et artefacts de session

Authentification requise via cookie de session.

Les fichiers produits dans une session peuvent etre servis par l'API :

```text
/session-files/{session_id}/{file_path}
```

Pour une session partagee, utilisez la reference publique `owner_username:session_id`.
Un acces `viewer` suffit pour lire les artefacts explicitement partages.

Le moteur sait aussi rajouter une URL d'artefact dans certaines reponses si l'utilisateur demande explicitement un lien vers un fichier genere.

### Endpoint `POST /sessions/{sid}/emergency-stop`

Authentification requise via cookie de session.

Cette route :

- marque la session comme stoppee
- coupe le runtime Docker de la session
- diffuse un evenement `EMERGENCY_STOP` au dashboard
- retire la session active du registre memoire pour permettre un redemarrage propre

Sur une session partagee, cette route demande au minimum un acces `operator`.

## Workspaces Et Persistance

Chaque session dispose de son propre espace de travail, cree sous `workspace/sessions/<session_id>/`.

On y retrouve generalement :

- `files/` : fichiers manipulables par les outils
- `files/artifacts/` : artefacts utiles a exposer au client
- `.venv/` : environnement Python persistant pour la session Docker
- `.cache/` : cache pip / matplotlib
- `logs/session.jsonl` : trace JSONL de session, avec messages bruts et evenements structures (`mission_started`, `llm_call_completed`, `tool_call_completed`, `sandbox_command_completed`, `subtask_completed`, etc.)

En parallele :

- `workspace/system/memory.db` stocke l'archive SQLite simple et la memoire structuree post-session
- `workspace/system/auth.db` stocke les comptes locaux, les sessions navigateur et les liaisons Telegram par defaut
- `workspace/system/tasks.db` stocke les taches locales creees via la skill `tasks`
- l'historique actif de conversation reste en memoire Python tant que le processus FastAPI tourne

Consequence importante :

- un redemarrage du serveur conserve les fichiers et l'archive SQLite
- mais ne reconstruit pas automatiquement les sessions actives en memoire

### Memoire post-session

Apres chaque mission terminee hors `EMERGENCY STOP`, 0-HITL lance maintenant une consolidation asynchrone de memoire.

Cette consolidation produit des elements structures, scopes par utilisateur :

- `summary` : resume court de la mission
- `fact` : information durable sur le contexte utilisateur
- `preference` : choix ou contrainte recurrente
- `procedure` : workflow ou recette reutilisable ayant de la valeur future
- `incident` : echec recurrent, contrainte ou point d'attention a retenir

Principes actuels :

- la memoire structuree est stockee dans `workspace/system/memory.db`
- les traces brutes et timings restent dans `logs/session.jsonl`
- la memoire est reinjectee plus tard sous forme de `Relevant structured memory`
- un souvenir plus recent peut explicitement remplacer un souvenir actif devenu obsolete
- les souvenirs expires sont retires automatiquement des resultats actifs
- les secrets bruts, tokens, cookies et credentials ne doivent pas etre promus en memoire structuree
- la consolidation est asynchrone pour limiter l'impact sur la latence de reponse

### Strategie memoire et modeles

Les decisions actuelles sont les suivantes :

- une instance garde une seule base de memoire persistante, mais les souvenirs sont scopes par utilisateur, pas partages globalement
- une session garde surtout des traces et artefacts ; la memoire durable vit au niveau du compte utilisateur
- la consolidation post-session utilise un snapshot compact, pas toute la session brute
- 0-HITL distingue maintenant un `agent model` et un `memory model`
- quand `GROQ_API_KEY` est configure sans override explicite, l'agent et la consolidation memoire preferent `groq/openai/gpt-oss-20b`

Le registre recommande de modeles est centralise dans [core/model_registry.py](/C:/Projects/000-0-HITL/core/model_registry.py), et la note d'architecture correspondante est documentee dans [docs/memory-and-model-strategy.md](/C:/Projects/000-0-HITL/docs/memory-and-model-strategy.md).

### Analyse des logs de session

Le depot fournit maintenant un petit analyseur pour scanner les `session.jsonl` et faire ressortir :

- les sessions les plus lentes
- les tools les plus lents
- les tools les plus instables
- les cold starts Docker, les reuses de runtime et les `docker_exec_ms`
- les signes de retry pressure sur une mission
- un `Decision Summary` avec bottleneck principal et action recommandee
- quelques notes de bottleneck directement exploitables

Commande recommandee :

```bash
python analyze_session_logs.py --workspace ./workspace --top 5
```

Options utiles :

- `--session <session_id>` : analyser une seule session
- `--json` : produire un rapport JSON
- `--top N` : limiter le nombre d'elements affiches
- `--output <path>` : sauver le rapport JSON ; si `<path>` est un dossier, ecrit `perf-latest.json` dedans

Exemple de sauvegarde :

```bash
python analyze_session_logs.py --workspace ./workspace --top 5 --output ./workspace/system/reports/
```

## Skills

Les skills sont declarees dans `./skills/<nom>/` et chargees au demarrage sous forme de catalogue. Leurs outils Python ne sont importes qu'au moment de l'activation.

Structure attendue :

```text
skills/<skill_name>/
|-- skill.yaml
|-- SKILL.md
`-- tools.py
```

Guide pratique : [docs/creating-a-skill.md](/C:/Projects/000-0-HITL/docs/creating-a-skill.md)

## Telegram

0-HITL embarque maintenant un premier connecteur Telegram v1, pense pour une instance privee et self-hosted.

Principes actuels :

- mode `long polling`, pas de webhook requis
- un chat Telegram prive est lie a un compte local 0-HITL
- le connecteur renvoie uniquement la reponse finale de l'agent
- un chat garde par defaut une session Telegram persistante, remisable avec `/new`

Variables a renseigner :

- `HITL_TELEGRAM_ENABLED=true`
- `TELEGRAM_BOT_TOKEN=...`

Flow minimal :

1. l'utilisateur se connecte au dashboard ou a l'API avec son compte local
2. il appelle `POST /integrations/telegram/link-code`
3. il ouvre le bot Telegram et envoie `/link CODE`
4. les messages Telegram suivants sont routes vers sa session 0-HITL

Endpoints utiles :

- `GET /integrations/telegram` : statut du connecteur et chats Telegram lies pour l'utilisateur courant
- `POST /integrations/telegram/link-code` : genere un code de liaison temporaire
- `DELETE /integrations/telegram/links/{chat_id}` : supprime une liaison Telegram pour l'utilisateur courant

Commandes Telegram v1 :

- `/start` ou `/help` : aide rapide
- `/link CODE` : lie le chat prive au compte local
- `/new` : cree une nouvelle session Telegram par defaut
- `/whoami` : affiche le compte local et la session Telegram courante

Le connecteur Telegram est initialement pense pour des chats prives. Les groupes ne sont pas supportes dans cette v1.

### Skills embarquees : `system`, `web`, `workspace_plus`, `document`, `http_client`, `python_runtime` et `tasks`

Le depot contient une skill `system` qui fournit les outils de base :

- `activate_skill`
- `write_file`
- `read_file`
- `ls`
- `execute_bash`
- `get_artifact_url`

Cette skill est importante : c'est elle qui donne a l'agent les primitives de lecture, ecriture et execution dans le sandbox.

Le depot contient aussi une skill d'exemple `web`, orientee lecture seule sur le reseau :

- `search_web`
- `fetch_url`
- `extract_page_text`
- `extract_links`

Cette skill sert a faire de la recherche web simple, recuperer une page HTTP, extraire son texte utile et lister ses liens sans passer par du bash artisanal. Son `skill.yaml` declare explicitement la permission `network`.

Le depot contient enfin une skill `workspace_plus`, orientee manipulation locale rapide du workspace :

- `find_files`
- `grep_files`
- `tree_workspace`
- `make_directory`
- `copy_path`
- `move_path`
- `delete_path`

Cette skill sert a rechercher, organiser et nettoyer des fichiers sans invoquer `execute_bash`, ce qui la rend utile pour reduire les petits appels shell lents et repetitifs.

Le depot contient aussi une skill `document`, orientee lecture et condensation de documents texte :

- `summarize_file`
- `extract_outline`
- `compare_texts`
- `chunk_document`

Cette skill sert a resumer des notes, extraire une structure markdown/HTML, comparer deux versions et decouper un long document en morceaux exploitables sans bricoler de scripts shell.

Le depot contient aussi une skill `http_client`, orientee appels API et telechargements structures :

- `http_get`
- `http_post_json`
- `head_url`
- `download_file`

Cette skill sert a parler a des endpoints HTTP, verifier des metadonnees de reponse et enregistrer des fichiers dans le workspace sans passer par `curl` ou `execute_bash`.

Le depot contient aussi une skill `python_runtime`, orientee scripts Python et lecture rapide de CSV :

- `run_python`
- `run_python_file`
- `inspect_csv`

Cette skill sert a lancer un snippet Python en sandbox, executer un script du workspace et inspecter rapidement la structure d'un CSV sans retomber dans des commandes shell generiques.

Le depot contient aussi une skill `tasks`, orientee suivi local de petites actions a mener :

- `create_task`
- `list_tasks`
- `complete_task`
- `update_task`
- `delete_task`

Cette skill sert a garder une todo locale par utilisateur directement dans 0-HITL, ce qui la rend utile pour les suivis personnels, les priorites de projet et les futures integrations Telegram/email.

## Securite

Le projet applique deja plusieurs garde-fous utiles :

- sandbox Docker separe par session
- desactivation du reseau par defaut dans le runtime d'outils
- blocage heuristique de certaines commandes dangereuses
- confinement des acces fichier au workspace de session
- sanitation des `session_id`
- comptes locaux avec cookie HTTP-only et bootstrap du premier owner
- isolation des sessions et de la memoire longue par utilisateur authentifie
- partage de session explicite avec permissions `viewer` / `operator`
- `CORS` ferme par defaut, activable explicitement par environnement
- `EMERGENCY STOP` de session relie au backend avec arret du runtime Docker

Mais il faut considerer le projet comme experimental :

- les comptes restent simples (`owner`, `admin`, `member`) et il n'existe pas encore de vrais espaces collaboratifs avec permissions d'ecriture fines
- le scan VirusTotal existe dans le code mais n'est pas integre au flux principal
- il n'existe pas encore de kill switch global multi-session ni de file d'audit complete des arrets manuels

N'exposez pas ce service tel quel sur Internet sans reverse proxy, HTTPS et durcissement complementaire.

## Tests

Le depot contient plusieurs scripts de test :

- `test_api_smoke.py` : smoke test de l'API sur parser CORS, bootstrap/login/logout, administration des comptes, partages `viewer` / `operator`, emergency stop, `/health`, `/chat`, `/session-files/...` et le WebSocket avec moteur mocke
- `test_suite.py` : tests de base sur `SuperEgo`, registre d'outils, contexte, profils et isolation du workspace
- `test_engine_mock.py` : boucle agentique avec LLM mocke
- `test_log_analysis.py` : analyseur des `session.jsonl` avec classement des sessions lentes, tools lents et erreurs recurrentes
- `test_memory_post_session.py` : consolidation post-session et reinjection de la memoire structuree
- `test_model_registry.py` : resolution des roles de modeles et verification du `memory_model` dedie
- `test_runner_metrics.py` : extraction des metriques runner (`cold_start`, `container_start_ms`, `docker_exec_ms`, `command_wall_ms`) et propagation dans `tool_call_completed`
- `test_document_skill.py` : validation de la skill `document` sur resume, outline, diff et decoupage de texte
- `test_http_client_skill.py` : validation de la skill `http_client` sur GET, POST JSON, HEAD et telechargement dans le workspace
- `test_python_runtime_skill.py` : validation de la skill `python_runtime` sur execution Python en sandbox et inspection CSV
- `test_tasks_skill.py` : validation de la skill `tasks` sur creation, listing, completion, suppression et isolation par utilisateur
- `test_telegram_connector.py` : validation du flow Telegram v1 sur `/link`, `/new`, message normal et `/whoami`
- `test_web_skill.py` : validation de la skill `web` sur recherche, fetch HTTP, extraction de texte et de liens
- `test_workspace_plus_skill.py` : validation de la skill `workspace_plus` sur recherche de fichiers, grep, arbre, copie, deplacement et suppression
- `test_persistent_runtime.py` : persistance du runtime Docker et du venv de session
- `test_real_agent.py` : test de bout en bout avec un vrai modele

Commande minimale recommandee pour verifier rapidement la base :

```bash
python test_api_smoke.py
```

Exemples :

```bash
python test_api_smoke.py
python test_suite.py
python test_engine_mock.py
python test_log_analysis.py
python test_memory_post_session.py
python test_model_registry.py
python test_runner_metrics.py
python test_document_skill.py
python test_http_client_skill.py
python test_python_runtime_skill.py
python test_tasks_skill.py
python test_telegram_connector.py
python test_web_skill.py
python test_workspace_plus_skill.py
python test_persistent_runtime.py
python test_real_agent.py
```

## Limitations Actuelles

- Le multi-agent est present sous forme d'ebauche, mais pas encore integre au chemin principal.
- La "memoire a 3 niveaux" est pour l'instant une combinaison de memoire en RAM, logs JSONL et archive SQLite textuelle, pas un vrai moteur vectoriel.
- Le dashboard charge Tailwind et Lucide depuis des CDN publics ; sans acces Internet, son rendu peut etre degrade.
- Le `Dockerfile` copie tout le depot avec `COPY . .` ; verifiez bien vos fichiers locaux avant de builder une image.
- Un `.dockerignore` est maintenant present, mais il faut continuer a surveiller le contenu local avant build.
- Le projet documente une vision ambitieuse dans `docs/context.md`, mais tout n'est pas encore implemente.

## Notes De Developpement

- Profil systeme par defaut : `profiles/orchestrateur.md`
- Modele par defaut : `groq/openai/gpt-oss-20b` si `GROQ_API_KEY` est present, sinon `gpt-4o`
- Modele de memoire par defaut : `groq/openai/gpt-oss-20b` si `GROQ_API_KEY` est present, sinon le modele agent
- Le moteur compacte le contexte lorsque la fenetre approche la saturation
- Les reponses d'outils sont diffusees au dashboard pendant l'execution

## Roadmap Raisonnable

Les prochaines evolutions les plus naturelles pour ce depot seraient :

- brancher reellement le scan de contenu avant activation / execution de skills
- restaurer les sessions actives depuis les traces JSONL
- etendre le partage de session vers un vrai mode collaboratif avec ecriture/chat et audit
- ajouter un kill switch global multi-session et une piste d'audit des arrets
- rendre la documentation des skills et du cycle de session plus riche
- formaliser un mode multi-agent reel, ou retirer cette promesse du positionnement

## Resume

0-HITL est un bon socle experimental pour explorer une architecture d'agent autonome avec FastAPI, LiteLLM, sandbox Docker et skills chargees a la demande. Le projet est particulierement interessant pour tester :

- l'orchestration de tool calls
- l'isolation d'execution par session
- la livraison d'artefacts
- l'observabilite temps reel d'un agent

Si vous cherchez une base de travail pour prototyper un "Agent OS" Python, ce depot est deja utile. Si vous cherchez une plateforme prete pour la production, il reste encore plusieurs briques a fiabiliser.
