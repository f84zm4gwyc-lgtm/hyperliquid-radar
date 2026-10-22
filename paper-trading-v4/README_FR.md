# Hyperliquid BTC/ETH — Paper Trading V4 (prototype, non déployé)

**État : 20 octobre 2026.** Le code V4 contient la V3 et 2 nouveaux modules dédiés aux cotations WebSocket. **112 tests logiciels réussis** (dont 19 tests V4). Ce nombre n'est PAS une statistique de trading, ni la preuve d'un rendement. Toutes les données d'exemples sont FICTIVES.

## Réalisé sans toucher à Render

- `ws_tape.py` : transforme des messages WebSocket **enregistrés** (bid/ask `bbo`, mark `activeAssetCtx`) en cotations pour le simulateur. Chaque sortie contient les heures de capture, le timestamp de l'événement BBO, et les sources distinctes des timestamps. Seules les paires BTC/ETH à fraîcheur/écart compatibles sont retenues ; rejette données périmées, spreads excessifs, doublons et désordres temporels.
- `collect_ws.py` : enregistre **facultativement**, lorsqu'on l'exécute soi-même, le flux public WebSocket Hyperliquid dans un fichier JSONL local. Pas de connexion à un compte, pas de clé API, aucun ordre, pas de démarrage automatique et pas d'installation sur Render. L'heure de réception est celle **de l'ordinateur qui collecte** : son horloge doit être correcte. Limite de 3 600 secondes par capture, 100 000 messages, sorties sans écrasement de fichier.
- `replay_quotes.py` renforcé : si le mark ne possède que l'horodatage **local de réception** (comme dans `activeAssetCtx`), interdit de présenter le résultat comme une capture indépendante d'heure d'échange (`CAPTURED_LIVE_REVIEWED`). Empêche aussi le changement de source pendant le replay.
- `audit_metrics.py` : conserve la séparation entre démonstrations fictives, données non vérifiées et clôtures simulées éventuellement révisées par un humain. Les jeux de données produits par cette V4 sont **non évaluables comme performance** sans contrôles supplémentaires.

**Limites essentielles** : `activeAssetCtx` n'a pas de timestamp d'échange pour le mark ; une heure de réception locale **n'est pas** une heure d'échange. La prise d'un échantillon bid/ask + mark n'établit pas un fill réel. Les frais, funding, incidents de marché, latence réseau et liquidations restent à mieux modéliser. Aucun système live ne peut garantir qu'un stop sera exécuté au prix attendu. Un candidat `WATCH_ONLY` n'est pas un trade.

## Ce qui n'a PAS changé

- Le service Render V1.1 actuel n'est pas modifié. La V4 **n'est pas en production** et ne capture aucune nouvelle donnée à ta place aujourd'hui.
- Aucun nouvel abonnement payant. Aucun ordre réel.
- Sur le plan Render Free, un fichier local, même SQLite, est **éphémère** : il peut disparaître à la mise en veille, au redémarrage ou au redéploiement. Il faudra choisir et vérifier un stockage externe durable avant tout déploiement de l'enregistreur sur Render, sans activer d'offre payante sans accord explicite.
- L'audit du 21 octobre 2026 à 11h03 (Paris) reste inchangé.

## Tests facultatifs (pas d'action demandée aujourd'hui)

```bash
python -m unittest discover -s tests -q
```

### Tester le parcours sans argent ni Internet (exemple 100 % fictif)

```bash
python ws_tape.py --input examples/WS_RAW_FICTIF.jsonl --output exemple_cotations.jsonl --source SYNTHETIC_EXAMPLE
python replay_quotes.py --signal examples/SIGNAL_V4_FICTIF.json --quotes exemple_cotations.jsonl --out exemple_resultat.json --source SYNTHETIC_EXAMPLE
python audit_metrics.py exemple_resultat.json
```

Le résultat doit signaler `counted_closed_reviewed_quotes = 0`, **même si** la simulation affiche un gain : source `SYNTHETIC_EXAMPLE`. Éviter de lancer deux fois les mêmes commandes sans changer les noms de sortie ; elles refusent d'écraser des archives déjà créées.

### Enregistrement WebSocket local, facultatif et futur

Dépendance Python : `python -m pip install -r requirements-optional.txt`.

```bash
python collect_ws.py --output capture_publique_btc_eth.jsonl --duration-seconds 120
python ws_tape.py --input capture_publique_btc_eth.jsonl --output cotations_non_verifiees.jsonl --source RECORDED_PUBLIC_WS_UNREVIEWED
```

**Ces commandes tournent sur le PC qui les exécute**, donc pas PC éteint. Ne les lance pas pendant l'audit de Render : elles ne sont qu'une démonstration d'enregistrement indépendant. Le service Render existant n'est jamais modifié par ces commandes.

## Décision après l'audit

1. Vérifier la durée de disponibilité, les redémarrages et la fraîcheur des données Render.
2. Sélectionner une méthode de **stockage externe durable**, avec coûts et limites vérifiés.
3. Intégrer le journal de cotations sur un **canal lecture seule**, sans intervention sur les ordres.
4. Définir un processus indépendant de validation des signaux, puis commencer les vrais replays prospectifs, avec horodatages et frais conservateurs.
5. Ne juger aucun « taux de réussite » tant qu'on n'a pas un échantillon suffisamment large, propre et hors échantillon.

Documentation officielle : [Hyperliquid WebSocket](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions) et [Render Free](https://render.com/docs/free).

## Correctif audit 22/10 : auto-attestation interdite

`audit_metrics.py` rejette désormais par défaut les rapports qui s’autodéclarent `CAPTURED_LIVE_REVIEWED`. Un calcul de rendement ne peut pas être admis sur la seule foi d’un champ JSON. La CLI ne permet pas ce contournement : seuls des contrôles externes explicites, et restant des estimations hypothétiques, peuvent être utilisés pour un bilan exploratoire.
