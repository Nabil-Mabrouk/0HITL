# Creating a Skill for 0-HITL

Ce guide explique comment ajouter une nouvelle skill au catalogue de 0-HITL, la tester proprement et la brancher sans casser le modele de securite actuel.

## Vue d'ensemble

Une skill 0-HITL est un dossier sous `skills/<skill_name>/` contenant trois fichiers :

```text
skills/<skill_name>/
|-- skill.yaml
|-- SKILL.md
`-- tools.py
```

- `skill.yaml` decrit la skill et ses permissions
- `SKILL.md` contient les instructions que l'agent recevra quand la skill est activee
- `tools.py` declare les outils Python charges dynamiquement via le decorateur `@tool`

Le catalogue est charge au demarrage par [core/skills.py](/C:/Projects/000-0-HITL/core/skills.py), mais les outils Python ne sont importes qu'au moment de `activate_skill`.

## 1. Choisir le bon type de skill

Avant d'ecrire du code, decide si le besoin merite vraiment une skill ou s'il vaut mieux enrichir une skill existante.

Questions utiles :

- Est-ce un nouveau domaine coherent, avec des outils qui vont ensemble ?
- Est-ce que ca reduit les usages de `execute_bash` ou les retries du modele ?
- Est-ce que ca peut rester simple a raisonner cote securite ?

Exemples dans le depot :

- [system](/C:/Projects/000-0-HITL/skills/system) : primitives de base fichier + shell
- [web](/C:/Projects/000-0-HITL/skills/web) : recherche et lecture HTTP
- [workspace_plus](/C:/Projects/000-0-HITL/skills/workspace_plus) : operations locales rapides sur le workspace
- [document](/C:/Projects/000-0-HITL/skills/document) : resume, structure et diff de texte
- [http_client](/C:/Projects/000-0-HITL/skills/http_client) : appels API et telechargements
- [python_runtime](/C:/Projects/000-0-HITL/skills/python_runtime) : execution Python structuree et inspection CSV

## 2. Creer le dossier et les metadonnees

Exemple minimal de `skill.yaml` :

```yaml
name: my_skill
description: Description courte et concrete de la skill.
version: 1.0.0
author: 0-HITL-Core
required_permissions:
  - filesystem
docker_image: python:3.12-slim
```

Champs importants :

- `name` : identifiant stable de la skill
- `description` : phrase courte visible dans le catalogue
- `required_permissions` : permissions declarees pour aider l'agent a comprendre le perimetre

Permissions utilisees aujourd'hui dans le repo :

- `filesystem`
- `execution`
- `network`

Ces permissions sont aujourd'hui surtout declaratives. Elles doivent quand meme rester honnetes, car elles servent de contrat de comportement et de base pour les futurs durcissements.

## 3. Ecrire `SKILL.md`

`SKILL.md` doit aider le modele a bien utiliser la skill une fois activee.

Bon format :

1. une phrase sur le role de la skill
2. 3 a 5 cas d'usage clairs
3. quelques conseils de bon usage

Exemple :

```md
# My Skill

Cette skill sert a ...

Utilise-la en priorite quand tu dois :

1. ...
2. ...
3. ...

Conseils :

- ...
- ...
```

Regarde [skills/document/SKILL.md](/C:/Projects/000-0-HITL/skills/document/SKILL.md) ou [skills/http_client/SKILL.md](/C:/Projects/000-0-HITL/skills/http_client/SKILL.md) pour un format concret.

## 4. Ecrire `tools.py`

Les outils doivent etre declares avec le decorateur `@tool` de [core/tools.py](/C:/Projects/000-0-HITL/core/tools.py).

Exemple minimal :

```python
from core.tools import tool


@tool
async def hello(name: str):
    """Returns a greeting."""
    return f"Hello {name}"
```

Recommandations pratiques :

- garde des signatures simples : `str`, `int`, `bool`, `float`
- si tu as besoin de structures riches, passe-les en JSON texte et parse-les dans l'outil
- retourne toujours une chaine lisible ou un objet avec `__str__` propre
- en cas d'erreur previsiblement recuperable, retourne `Error: ...` plutot qu'une stacktrace brute
- ajoute des helper functions privees pour la validation de chemin, d'URL, de JSON, etc.

## 5. Respecter les frontieres de securite

Quelques regles importantes dans le projet actuel :

- les chemins doivent rester dans le workspace de session
- les outils reseau doivent restreindre les URLs a `http` / `https`
- les outils ne doivent pas faire de suppression large ou d'ecriture arbitraire sans garde-fous
- si tu peux eviter `execute_bash`, fais-le

Pour les skills `filesystem`, reutilise le pattern de resolution vu dans :

- [skills/system/tools.py](/C:/Projects/000-0-HITL/skills/system/tools.py)
- [skills/workspace_plus/tools.py](/C:/Projects/000-0-HITL/skills/workspace_plus/tools.py)
- [skills/document/tools.py](/C:/Projects/000-0-HITL/skills/document/tools.py)

Pour les skills `network`, regarde :

- [skills/web/tools.py](/C:/Projects/000-0-HITL/skills/web/tools.py)
- [skills/http_client/tools.py](/C:/Projects/000-0-HITL/skills/http_client/tools.py)

Pour les skills qui deleguent au sandbox Docker, regarde :

- [skills/system/tools.py](/C:/Projects/000-0-HITL/skills/system/tools.py)
- [skills/python_runtime/tools.py](/C:/Projects/000-0-HITL/skills/python_runtime/tools.py)

## 6. Tester la skill

Chaque nouvelle skill devrait avoir au moins un test dedie au format `test_<skill_name>_skill.py`.

Pattern recommande :

- charger dynamiquement `tools.py` avec `importlib.util`
- mocker les dependances externes quand possible
- verifier les cas heureux
- verifier au moins un cas d'erreur ou de garde-fou

Exemples :

- [test_web_skill.py](/C:/Projects/000-0-HITL/test_web_skill.py)
- [test_workspace_plus_skill.py](/C:/Projects/000-0-HITL/test_workspace_plus_skill.py)
- [test_document_skill.py](/C:/Projects/000-0-HITL/test_document_skill.py)
- [test_http_client_skill.py](/C:/Projects/000-0-HITL/test_http_client_skill.py)
- [test_python_runtime_skill.py](/C:/Projects/000-0-HITL/test_python_runtime_skill.py)

Commande de validation typique :

```bash
docker compose build 0-hitl-brain
docker compose run --rm 0-hitl-brain python test_<skill_name>_skill.py
```

## 7. Verifier que la skill est visible

Une fois le dossier cree :

- le `skill.yaml` est detecte au demarrage
- la skill apparait dans le catalogue expose au modele
- `activate_skill("<skill_name>")` charge dynamiquement les outils

Le chargement est gere par [core/skills.py](/C:/Projects/000-0-HITL/core/skills.py).

## 8. Mettre a jour la documentation

Quand une skill est validee :

- ajoute-la a la section `Skills` du [README.md](/C:/Projects/000-0-HITL/README.md)
- ajoute son test a la section `Tests` du README
- si elle change vraiment le perimetre produit, mets a jour aussi [TODO.md](/C:/Projects/000-0-HITL/TODO.md)

## Checklist rapide

- dossier `skills/<skill_name>/` cree
- `skill.yaml` propre et honnete
- `SKILL.md` clair pour le modele
- outils `@tool` avec signatures simples
- garde-fous de chemin / JSON / URL si necessaire
- test dedie `test_<skill_name>_skill.py`
- validation Docker OK
- README mis a jour
