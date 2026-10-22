# Radar BTC/ETH — moteur prospectif papier

Ce dossier est le moteur proposé pour la reprise. Il utilise **Freqtrade 2026.9 en simulation**, les données publiques du marché principal Hyperliquid et un journal SQLite persistant. Il n'a besoin d'aucune clé Hyperliquid, d'aucun wallet et d'aucun capital réel.

**État de livraison : testé localement sur Python 3.12/Linux et sur les flux réels Hyperliquid ; publication GitHub, hébergement permanent, webhook Slack et déclenchement Work non validés.** Les fichiers Docker constituent un emballage proposé ; Docker n'est pas disponible dans l'environnement de vérification et leur construction n'a pas été exécutée.

## Un seul chemin actif

- Freqtrade récupère les bougies, aligne les horizons, gère les ordres simulés, les frais natifs et les positions.
- Le flux WebSocket public fournit mark et meilleur bid/ask sans appel réseau dans les callbacks de risque.
- Le plan, les bougies publiques disponibles, les indicateurs, la cotation et leur empreinte sont enregistrés avant l'entrée simulée.
- Une seule position ou réservation peut occuper le budget global BTC/ETH. Les plans et l'envoi Slack survivent au redémarrage si le dossier `data` est conservé.
- L'émetteur Slack utilise une file persistante et un identifiant unique. Un accusé HTTP Slack ne prouve ni une notification téléphone ni le déclenchement de Work.

Les anciens collecteurs et laboratoires restent accessibles pour l'audit. Ce moteur n'exécute aucun de leurs simulateurs et n'agrège pas leurs résultats avec les siens.

## Démarrer sous Linux / WSL avec Python 3.12

Depuis ce dossier :

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python run.py check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python run.py trade
```

`Ctrl+C` arrête le processus proprement. Un essai borné s'obtient avec `run.py trade --seconds 95`. Pour lire le journal après arrêt ou pendant l'exécution : `run.py summary`. `--data /chemin/persistant` permet de choisir le dossier conservé. La commande `check` valide les versions et le schéma de configuration, sans prouver une connexion de marché ou Slack.

Le lanceur impose `--dry-run`, une configuration générée sans identifiants de compte, deux paires, un maximum d'une position, API de contrôle désactivée et suppression des surcharges d'environnement `FREQTRADE__...`. Les options arbitraires de Freqtrade ne sont pas acceptées. L'utilisation directe de Freqtrade en dehors de ce lanceur sort de ce périmètre.

## Option Docker pour un ordinateur déjà équipé

```bash
docker compose up --build -d
docker compose logs --tail=100
docker compose exec radar-paper python run.py summary
docker compose stop
```

Le volume nommé `radar_data` conserve les deux bases. Une machine éteinte ou en veille ne surveille pas le marché. Ce fichier ne crée ni abonnement ni ressource Render. La construction Docker doit être vérifiée sur l'hôte avant d'en faire l'exécution permanente.

## Slack et contrôle macro

Le webhook entrant Slack doit être créé pour **le canal dédié**. Sa valeur reste uniquement dans la variable `RADAR_SLACK_WEBHOOK` du processus. Avec Docker Compose, copier `.env.example` en `.env` puis renseigner cette valeur localement. Aucun webhook ni jeton n'est livré dans le code.

Pour tester l'émetteur avec le processus de trading arrêté : `run.py test-slack`. Le message commence par `RADAR_E2E_TEST`, porte explicitement `TEST` et ne contient aucun signal de trading. Si la tâche Work est effectivement créée, elle doit accuser une seule fois ce test avec son identifiant ; cet accusé et la réception utilisateur doivent être constatés séparément. Un envoi ambigu, par exemple un timeout après le POST, devient `UNKNOWN` et n'est pas renvoyé aveuglément ; la livraison doit être vérifiée dans le canal avec son identifiant. Les erreurs 429 sont différées, puis le signal et sa fraîcheur sont recontrôlés.

**Pas d'alerte de trading lorsque le contexte macro est inconnu.** Une revue réellement faite et sourcée doit être écrite dans `data/macro_review.json`, selon l'exemple fourni. `ALLOW`, un auteur, des URL HTTPS, une heure de revue et une expiration dans les deux heures sont nécessaires. Le logiciel vérifie la forme et la validité temporelle de cette attestation ; il ne peut pas établir que son auteur a lu les sources. L'absence de revue empêche l'alerte, tout en autorisant la recherche papier locale. Aucune revue macro automatique n'a été mise en place.

Le contrat de tâche Work est dans `work-event-contract.json`. Son existence n'indique pas qu'une tâche a été créée. L'API Work refuse actuellement le canal malgré l'appartenance de ChatGPT vérifiée dans Slack. Une reconnexion de l'application est requise avant la recette : événement contrôlé → Slack → exécution Work → réception visible de la notification. La tâche horaire existante n'est pas désactivée tant que son remplacement n'est pas prouvé.

## Hypothèse et règles figées

Cassure d'un extrême des vingt bougies 15m précédentes, volume au moins 1,3 fois la moyenne précédente, clôture et EMA20/EMA50 alignées en 1h et 4h. Calculs TA-Lib, dont ATR14 de Wilder. Les horizons supérieurs ne sont utilisables qu'une fois clôturés. Cette version n'implémente pas une confirmation sur tous les horizons 5m/1d ni une analyse d'actualités autonome : c'est une hypothèse de recherche délimitée, pas le radar discrétionnaire complet.

La bougie de signal doit avoir clôturé depuis 3 à 180 secondes et mark/carnet doivent dater de 5 secondes au plus. Un désaccord de prix, une donnée future ou un flux interrompu empêche l'entrée. Stop à la plus grande distance entre 1,5 ATR et 0,5 % du prix ; objectifs à 2,5R et 5R ; sorties 50/50 après arrondi.

Levier isolé x2, marge au plus 1 000 USDC, perte théorique au stop incluant la réserve de coûts au plus 52 USDC, ratio net pondéré au moins 1,8. La réserve est au moins 0,5 % du nominal. Arrondis de prix et de quantité appliqués avant le calcul, puis nouveau contrôle de la proposition native Freqtrade. Les règles ne sont pas abaissées pour obtenir un premier trade. La validité d'une entrée en attente expire cinq minutes après la décision initiale.

## Ce que le résultat papier permet de conclure

Le mark est observé en continu par WebSocket ; le moteur de décision de sortie lit sa valeur environ toutes les cinq secondes. **Il peut manquer une traversée de niveau entre deux lectures.** Les journaux de sortie conservent les observations utilisées ; ils ne constituent pas une bande exhaustive de tous les événements mark/carnet. Une absence de donnée ou un intervalle supérieur à quinze secondes pendant la position entraîne un classement `UNVERIFIABLE_GAP`. Un saut directement au-delà des deux objectifs est explicitement identifié. Une position native sans plan associé est liquidée uniquement en simulation et signalée comme anomalie.

Les sorties sont des ordres limites simulés au prix opposé du carnet, avec le modèle d'exécution Freqtrade. Ils peuvent ne pas être remplis immédiatement. Le stop normal est logiciel sur mark échantillonné ; le filet Freqtrade à -99 % n'est **pas** le stop de risque annoncé. La limite de 52 USDC est un budget théorique, pas une perte garantie en cas de saut ou d'interruption.

Le PnL natif déduit les frais configurés (0,045 % par côté) et les estimations de funding disponibles dans Freqtrade. La réserve de coûts de 0,5 % sert au dimensionnement : elle n'est pas automatiquement intégralement déduite du PnL natif. Frais futurs, slippage, latence et fills restent des hypothèses. Les résultats sont nommés `MARK_SAMPLED_PAPER_ESTIMATE`, jamais fills réels ni avantage statistique démontré. Le journal local et son hash ne sont pas une preuve d'horodatage indépendante.

Un premier résultat papier accepté exige : plan enregistré avant entrée, données sources conservées, aucun rééchelonnement rétroactif, entrée et sorties natives retrouvées, coûts et interruptions documentés, rapprochement des deux bases après redémarrage. Une clôture profitable ne démontre pas la rentabilité d'une stratégie. Le backtest et l'hyperoptimisation de cette classe prospective sont refusés.

## Fichiers conservés dans data

| Fichier | Utilité |
|---|---|
| `radar.sqlite` | Plans, empreintes, événements, file de livraison |
| `freqtrade.sqlite` | Ordres et positions exclusivement simulés par Freqtrade |
| `status.json` | Dernière observation de santé, avec heure et état du flux |
| `paper.log` | Diagnostic du moteur |
| `macro_review.json` | Attestation macro facultative, expirant explicitement |

Les deux bases sont séparées pour éviter les verrous entre le callback natif et le journal. Conserver l'ensemble du dossier, y compris les fichiers SQLite WAL éventuels ; faire les sauvegardes à l'arrêt. Un ancien `status.json` ne prouve pas que le moteur tourne encore.

## Intégration du lot historique

Le lot corrige également le mélange millisecondes/secondes de `render-radar/app.py`, conserve l'expiration initiale dans `prospective_lab.py` et impose l'historique Git complet pour vérifier la création des plans. Les tests de régression correspondants accompagnent ces corrections. Le workflow `validate-paper-engine.yml` valide le lot sans secret ni ordre ni déploiement. Aucun service existant n'a été remplacé automatiquement.
