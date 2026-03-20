# TODO

Checklist de travail reoriente vers la valeur produit de 0-HITL comme assistant local-first, prive et utile au quotidien.

## Prochaines Priorites

- [x] Logger le temps total d'une mission, le temps par appel LLM, par tool call et par sous-tache dans les traces de session.
- [x] Ajouter un petit analyseur des logs pour reperer regulierement les bottlenecks, les tools lents et les etapes qui echouent souvent.
- [x] Definir clairement ce que 0-HITL retient apres une session : faits, preferences, procedures, incidents et lecons utiles.
- [x] Ajouter une consolidation de fin de session vers la memoire persistante au lieu de ne garder que des traces brutes.
- [x] Formaliser la frontiere memoire entre instance, utilisateur et session.
- [x] Introduire un `memory_model` distinct du modele principal pour la consolidation post-session.
- [x] Ajouter un registre de modeles favoris privilegient Groq avec des roles explicites.
- [x] Mesurer plus finement l'impact du cold start Docker, de la reutilisation du runtime et des retries.
- [ ] Implementer un premier connecteur externe simple pour declencher une mission sans passer par le dashboard, idealement Telegram ou email.

## Observability & Performance

- [x] Logger les durees de reponse dans `logs/session.jsonl` avec des champs standardises.
- [ ] Mesurer les delais de bout en bout : reception de la requete, reponse finale, duree des retries et duree d'execution Docker.
- [x] Distinguer explicitement `cold_start`, `runtime_reused`, `container_start_ms` et `docker_exec_ms` dans les logs du runner.
- [ ] Ajouter une analyse plus fine de la part `venv_bootstrap_ms` vs `command_wall_ms` dans le rapport de bottlenecks.
- [x] Identifier les bottlenecks recurrentes a partir des logs et produire un mini rapport exploitable.
- [ ] Automatiser plus tard un scan periodique des logs avec sortie de rapport ou alerte.
- [ ] Ajouter plus de traces utiles pour les erreurs critiques du moteur et du runner.
- [ ] Afficher la reponse finale de l'agent plus clairement dans le dashboard.

## Memory & Learning

- [x] Clarifier le role exact de `memory.db` et des logs JSONL dans la doc.
- [x] Definir une politique de memoire post-session simple et explicite.
- [x] Extraire en fin de session les faits stables, preferences utilisateur et procedures reutilisables.
- [x] Distinguer la memoire utile long terme des traces purement operationnelles.
- [x] Ajouter un mecanisme de revision / remplacement / expiration fine des souvenirs structures quand ils deviennent obsoletes.
- [ ] Exploiter les echecs et reussites de session pour ameliorer prompts, outils ou workflows futurs.
- [ ] Ajouter plus tard une vue d'inspection / correction manuelle de la memoire structuree.
- [ ] Restaurer les sessions actives apres redemarrage du serveur a partir des traces utiles.

## Channels & Integrations

- [x] Ajouter un premier connecteur Telegram avec mapping clair vers un compte local 0-HITL.
- [ ] Ajouter un connecteur email pour deposer des missions asynchrones.
- [ ] Definir comment une identite externe se rattache a un utilisateur local, une session et une piste d'audit.
- [ ] Evaluer plus tard un connecteur WhatsApp si le socle Telegram/email est deja fiable.

## Skills & Tools

- [x] Ajouter une ou deux skills d'exemple supplementaires.
- [ ] Enrichir la base de skills et tools a partir des usages reels observes dans les logs.
- [ ] Brancher `security_gate.scan_content()` dans un vrai flux utile, idealement a l'activation des skills ou avant execution sensible.
- [ ] Revoir les regles `SuperEgo` et couvrir davantage de commandes dangereuses.
- [x] Documenter plus richement comment creer, tester et brancher une nouvelle skill.

## Security & Hardening

- [ ] Revoquer les secrets presents dans `.env` et regenerer de nouvelles cles.
- [ ] Verifier que l'autodetection des bind mounts Docker fonctionne proprement sur Linux, WSL et Windows.
- [ ] Durcir progressivement le modele de permissions quand les connecteurs externes seront introduits.
- [ ] Prevoir une piste d'audit plus complete pour les actions sensibles et les arrets manuels.

## Demarrage Local, DX & Qualite

- [ ] Verifier de bout en bout le demarrage local avec `.venv`, installation des dependances et lancement de `main.py`.
- [ ] Verifier la documentation Windows PowerShell pour l'activation de `.venv`.
- [ ] Verifier que le mode local fonctionne bien avec Docker actif sur la machine.
- [ ] Ajouter une section "depannage" minimale dans le `README.md` pour les erreurs courantes de demarrage.
- [ ] Migrer les scripts de test existants vers `pytest`.
- [ ] Documenter le cycle de vie complet d'une session.
- [ ] Documenter clairement la difference entre "fonctionnalites actuelles" et "roadmap".

## Decisions Produit A Trancher

- [ ] Decider si le projet doit rester un mono-agent robuste ou investir vraiment dans le multi-agent.
- [ ] Definir le perimetre exact de la memoire long terme pour un usage personnel, familial ou organisationnel.
- [ ] Clarifier si les connecteurs externes doivent etre vus comme simples interfaces de commande ou comme vraies surfaces conversationnelles persistantes.

## Nice To Have / Labo

- [ ] Ajouter un vrai mode collaboratif sur session partagee avec chat/ecriture et audit.
- [ ] Ajouter un vrai kill switch global multi-session avec audit.
- [ ] Experimenter des conversations entre plusieurs instances de 0-HITL autour d'un sujet ou d'une actualite.
- [ ] Explorer des simulations plus ambitieuses de plusieurs instances specialisees jouant des roles distincts.

## Fondations Deja En Place

- [x] Ajouter un fichier `.env.example` sans secret avec toutes les variables attendues.
- [x] Ajouter un fichier `.dockerignore` pour exclure `.env`, `workspace/`, caches, logs et artefacts locaux.
- [x] Verifier que le `Dockerfile` installe correctement les dependances et demarre bien avec `docker compose up --build`.
- [x] Ajouter un endpoint `GET /health` simple pour confirmer que l'API est bien en ligne.
- [x] Fermer le `CORS` par defaut et preparer une configuration par environnement.
- [x] Ajouter une authentification locale minimale pour l'API et le dashboard.
- [x] Ajouter une interface d'administration minimale des comptes locaux depuis le dashboard.
- [x] Ajouter des permissions plus fines sur les espaces partages et les artefacts.
- [x] Rendre le bouton `EMERGENCY STOP` du dashboard fonctionnel cote backend.
- [x] Ajouter un test smoke pour `POST /chat`.
- [x] Ajouter un test smoke pour le WebSocket `/ws/mission-control/{session_id}`.
- [x] Ajouter un test pour `/session-files/{sid}/{file_path}`.
- [x] Ajouter une commande de test unique documentee dans le `README.md`.
