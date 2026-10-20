# Radar Hyperliquid BTC/ETH — lecture seule

Ce dépôt est un **collecteur de données publiques**, pas un robot de trading.

- Toutes les ~30 minutes, GitHub Actions interroge l'API officielle Hyperliquid (`/info`), pour les perpétuels BTC et ETH.
- Il récupère les bougies **terminées** 1H, 4H, 1D ; calcule EMA20/50/200, RSI14, MACD, ATR14, volumes et bornes des 20 bougies précédentes.
- Il relève le prix mark, le funding horaire et l'open interest.
- Il publie dans le dossier `docs/` les fichiers `report.md`, `latest.json` et six graphiques PNG.
- **Aucun portefeuille connecté, aucune clé API, aucun ordre, aucun accès aux fonds.** Ne renseignez pas de seed ou clé privée.

## Installation (GitHub)

1. Créer un dépôt GitHub **public** `hyperliquid-radar` (ne pas y mettre d'information personnelle).
2. Décompresser le ZIP et importer tous les fichiers **en conservant `.github/workflows/update-radar.yml`**.
3. Ouvrir l'onglet `Actions`, puis `Actualiser radar Hyperliquid BTC/ETH`, puis `Run workflow`.
4. Après succès (pastille verte), ouvrir `docs/report.md` et vérifier que la date UTC correspond au jour de l'exécution.
5. Envoyer à ChatGPT **l'URL de `docs/report.md`** du dépôt public pour configurer le suivi automatique. Une fois la passerelle accessible, le radar ChatGPT pourra *essayer de lire cette source* lors de ses vérifications ; ce n'est pas un accès direct garanti ni du temps réel continu.

### Exemple de lien à communiquer

`https://github.com/VOTRE_PSEUDO/hyperliquid-radar/blob/main/docs/report.md`

### Si le rapport n'est pas mis à jour

- Vérifier `Actions` > dernière exécution ; le programme refuse de publier de nouvelles données si l'API renvoie une erreur.
- Si `git push` échoue (403), vérifier dans `Settings` > `Actions` > `General` > `Workflow permissions` l'autorisation **Read and write permissions**, ou les restrictions du dépôt. Le fichier workflow comporte déjà `contents: write`.
- GitHub peut retarder ou manquer les tâches programmées, et arrêter les schedules des dépôts publics après 60 jours sans activité. Contrôler systématiquement l'heure de la dernière mise à jour.

## Important

- La source est la paire **perp BTC/ETH** : le spot BTC Hyperliquid utilise UBTC et peut coter différemment.
- Les indicateurs sont descriptifs et ne garantissent **aucun** trade. Le scan macro/news et la décision finale restent séparés.
- Cette solution publie des données. Pour que ChatGPT les utilise réellement en tâche automatique, le lien doit être renseigné dans la tâche existante et la lecture publique doit être vérifiée. La simple connexion GitHub ne suffit pas.

Sources : https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint ; https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule
