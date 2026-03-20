# Tasks Skill

Cette skill sert a gerer une petite liste de taches locale par utilisateur.

Utilise-la en priorite quand tu dois :

1. creer une action a suivre avec `create_task`
2. lister les priorites ouvertes avec `list_tasks`
3. marquer une tache comme terminee avec `complete_task`
4. mettre a jour ou supprimer une tache avec `update_task` et `delete_task`

Conseils :

- utilise `project` pour regrouper des taches autour d'un meme sujet
- utilise `priority=high` pour les urgences reelles
- garde les titres courts et mets le detail dans `notes`
- la skill est scoped par utilisateur ; sans utilisateur authentifie, elle retombe en mode session-local
