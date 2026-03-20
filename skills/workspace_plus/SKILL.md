# Workspace Plus Skill

Cette skill sert a manipuler rapidement le workspace courant sans lancer `execute_bash`.

Utilise-la en priorite quand tu dois :

1. rechercher des fichiers avec `find_files`
2. trouver du texte dans plusieurs fichiers avec `grep_files`
3. visualiser un arbre de dossiers avec `tree_workspace`
4. creer, copier, deplacer ou supprimer des chemins avec `make_directory`, `copy_path`, `move_path` et `delete_path`

Conseils :

- prefere ces outils a `execute_bash` pour les operations simples sur les fichiers
- reste dans le workspace courant : les outils bloquent les sorties de perimetre
- pour les suppressions de dossier, active `recursive=true` seulement si tu es sur du chemin cible
