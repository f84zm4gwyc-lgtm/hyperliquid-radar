# Radar Hyperliquid Render — installation future

**STATUT : pack préparé localement, NON DÉPLOYÉ.**

## Action de ton côté (une fois seulement)

Nous ne pouvons actuellement pas écrire dans le dépôt GitHub depuis le connecteur ChatGPT (403). Pour déployer le prototype, il faut publier les sources dans le dépôt existant, dans un nouveau dossier `render-radar` **sans modifier** les fichiers V2 déjà actifs.

À placer dans `render-radar/` :
- `app.py`
- `engine.py`
- `requirements.txt`
- `README.md`
- `tests/test_app.py`
- `tests/test_engine.py`

Solution simple : décompresser le ZIP, ouvrir le dépôt GitHub, cliquer `Add file → Upload files` et glisser le dossier `render-radar` en conservant l'arborescence. Si le navigateur refuse l'ajout de dossier, utiliser GitHub Desktop pour déposer le répertoire et effectuer le commit. Vérifier sur GitHub que `render-radar/app.py` existe après le commit.

Une fois présent sur la branche main, le service Render pourra être créé depuis la connexion Render, avec :
- dépôt : `https://github.com/f84zm4gwyc-lgtm/hyperliquid-radar`
- nom : `hyperliquid-radar-readonly`
- région : Frankfurt
- runtime : Python
- build : `pip install -r render-radar/requirements.txt`
- start : `python render-radar/app.py`

**Service 24/7 : payant, nécessitera un accord explicite sur le prix.** Pour un démonstrateur ponctuel, le Render Free est possible mais il se met en veille et ne garde pas l'historique SQLite entre redémarrages. Ne jamais lui donner de clé wallet, de clé Hyperliquid de trading ni de permission d'écriture.

Notre tâche ChatGPT de trading reste active et indépendante ; ce prototype ne lui envoie pas encore les candidats en temps réel. On vérifiera d'abord la fiabilité des flux, puis on concevra un pont d'alertes avec contrôle de fraîcheur et journal d'exécution simulée.
