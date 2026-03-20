# Memory And Model Strategy

Cette note fige trois decisions de conception pour 0-HITL.

## 1. Frontiere de memoire

0-HITL garde une seule base de memoire persistante par instance, mais la memoire durable n'est pas commune a tous les utilisateurs.

- `instance` : une seule base SQLite physique pour simplifier la sauvegarde, la recherche et la maintenance
- `utilisateur` : memoire structuree privee partagee entre toutes les sessions d'un meme compte
- `session` : traces operationnelles, artefacts et historique local a la session
- `espace partage` : futur chantier explicite pour une famille ou une organisation

Conclusion : une session n'a pas sa propre base de memoire durable. Elle alimente la memoire de son proprietaire.

## 2. Cout tokens et consolidation

La consolidation post-session ne renvoie pas toute la session brute au modele. Le moteur construit un snapshot compact :

- statut de mission
- demande utilisateur
- reponse finale
- outils utilises
- extrait recent de l'historique
- memoire active existante

Pour limiter les couts, 0-HITL distingue maintenant :

- `agent model` : conversation principale et tool use
- `memory model` : consolidation post-session et extraction de memoire structuree

Quand `GROQ_API_KEY` est present et qu'aucun override explicite n'est fourni, la strategie par defaut est :

- `agent` : `groq/openai/gpt-oss-20b`
- `memory` : `groq/openai/gpt-oss-20b`
- `deep_reasoning` : `groq/openai/gpt-oss-120b`

Une option locale restera pertinente plus tard pour les traitements de fond asynchrones, mais la priorite actuelle est une separation claire des roles de modeles.

## 3. Performance : prochaine lecture utile

Les metriques actuelles donnent deja :

- temps total de mission
- temps par appel LLM
- temps par tool call
- temps par sous-tache

La prochaine etape pour trouver les vrais goulots n'est pas encore l'optimisation. C'est l'instrumentation plus fine du runner, en particulier :

- `cold_start` vs `runtime_reused`
- `container_start_ms`
- `docker_exec_ms`
- `venv_bootstrap_ms`
- `command_wall_ms`
- `retry_count`

L'objectif est de separer clairement :

- le cout des appels LLM
- le cout de preparation Docker
- le cout de la commande executee elle-meme

## 4. Catalogue Groq recommande

Le registre actuel privilegie les roles suivants :

- `agent` : `groq/openai/gpt-oss-20b`
- `memory` : `groq/openai/gpt-oss-20b`
- `deep_reasoning` : `groq/openai/gpt-oss-120b`
- `coding` : `groq/moonshotai/kimi-k2-instruct-0905`
- `multilingual` : `groq/qwen/qwen3-32b`
- `vision` : `groq/meta-llama/llama-4-scout-17b-16e-instruct`
- `general_fallback` : `groq/llama-3.3-70b-versatile`
- `safety` : `groq/openai/gpt-oss-safeguard-20b`

Ce registre sert de socle pour les futurs routages par type de mission.
