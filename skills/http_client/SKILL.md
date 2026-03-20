# HTTP Client Skill

Cette skill sert a parler a des APIs HTTP et a telecharger des fichiers dans le workspace sans bricoler `curl` ou `execute_bash`.

Utilise-la en priorite quand tu dois :

1. lire une API ou une ressource JSON avec `http_get`
2. envoyer un corps JSON a une API avec `http_post_json`
3. verifier rapidement les metadonnees d'une URL avec `head_url`
4. telecharger un fichier dans le workspace avec `download_file`

Conseils :

- passe les `headers_json` et `params_json` comme des objets JSON serialises
- utilise `head_url` avant `download_file` si tu veux verifier le type ou la taille d'une ressource
- prefere `http_get` / `http_post_json` a `execute_bash` pour les integrations API
