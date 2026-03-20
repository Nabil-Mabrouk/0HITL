# Document Skill

Cette skill sert a lire et condenser des documents texte du workspace sans passer par des scripts shell.

Utilise-la en priorite quand tu dois :

1. produire un resume rapide avec `summarize_file`
2. extraire la structure d'un document avec `extract_outline`
3. comparer deux versions avec `compare_texts`
4. decouper un long texte en morceaux exploitables avec `chunk_document`

Conseils :

- utilise d'abord `extract_outline` ou `summarize_file` avant de lire un gros document entier
- `compare_texts` est plus propre qu'un diff shell pour des fichiers texte de travail
- `chunk_document` est utile pour traiter progressivement des notes longues, comptes rendus ou docs techniques
