# Python Runtime Skill

Cette skill sert a lancer du Python de maniere plus propre que `execute_bash`, puis a inspecter rapidement des CSV du workspace.

Utilise-la en priorite quand tu dois :

1. executer un petit snippet avec `run_python`
2. lancer un script deja present dans le workspace avec `run_python_file`
3. inspecter la structure et quelques statistiques d'un CSV avec `inspect_csv`

Conseils :

- prefere `run_python` ou `run_python_file` a `execute_bash` pour les scripts Python simples
- passe les arguments d'execution via `args_json` sous forme de tableau JSON
- utilise `inspect_csv` avant d'ecrire un script plus complexe si tu veux seulement comprendre rapidement un fichier tabulaire
