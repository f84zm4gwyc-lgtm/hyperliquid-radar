# Hyperliquid BTC / ETH — radar technique

**Mise à jour UTC : 2026-10-20T07:55:54.173000+00:00**

> Prix perpétuels Hyperliquid; le BTC spot (UBTC) peut présenter un écart. Cette page ne constitue pas un signal de trading. Vérifier que les données sont fraîches avant décision.

## BTC — prix mark 81507.0

Funding horaire : 0.00125% | Open interest : 40039.55332 BTC | Notionnel indicatif : 3263503872.45 USD

| UTC bougie | Cours clôture | EMA20 | EMA50 | EMA200 | RSI14 | MACD hist. | Volume / moyenne 20 | Plus haut 20 préc. | Plus bas 20 préc. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h — 2026-10-20T07:00:00+00:00 | 81797.0 | 81966.4973 | 81949.548 | 82264.5672 | 45.49 | -30.3018 | 0.605x | 82866.0 | 81171.0 |
| 4h — 2026-10-20T04:00:00+00:00 | 82120.0 | 81943.1559 | 82242.2255 | 82262.7832 | 51.47 | 72.6569 | 1.205x | 83138.0 | 80644.0 |
| 1d — 2026-10-20T00:00:00+00:00 | 81914.0 | 82539.4412 | 80559.9225 | 76197.5121 | 48.17 | -399.1524 | 0.873x | 87235.0 | 79732.0 |

Graphiques (bougies clôturées) : [1h](BTC_1h.png) · [4h](BTC_4h.png) · [1d](BTC_1d.png)

## ETH — prix mark 2389.2

Funding horaire : 0.000694% | Open interest : 1078746.9304 ETH | Notionnel indicatif : 2577342166.11 USD

| UTC bougie | Cours clôture | EMA20 | EMA50 | EMA200 | RSI14 | MACD hist. | Volume / moyenne 20 | Plus haut 20 préc. | Plus bas 20 préc. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h — 2026-10-20T07:00:00+00:00 | 2397.4 | 2404.3751 | 2407.2728 | 2447.5285 | 45.54 | -0.4681 | 0.492x | 2438.7 | 2371.8 |
| 4h — 2026-10-20T04:00:00+00:00 | 2413.8 | 2412.9204 | 2446.6342 | 2528.9132 | 48.84 | 3.2501 | 0.776x | 2446.1 | 2326.9 |
| 1d — 2026-10-20T00:00:00+00:00 | 2407.2 | 2504.1542 | 2482.8677 | 2332.7376 | 37.91 | -21.5272 | 0.719x | 2779.4 | 2314.4 |

Graphiques (bougies clôturées) : [1h](ETH_1h.png) · [4h](ETH_4h.png) · [1d](ETH_1d.png)

## Interprétation / sécurité

- EMA, RSI et MACD calculés avec bougies terminées uniquement. Le prix *mark* instantané peut être différent de la dernière clôture.
- Plus haut/bas 20 = limites des 20 bougies clôturées précédentes (exclut la dernière). Un franchissement seul ne valide pas une entrée.
- Les données ETF, news, liquidations, calendrier macro et positions de l’utilisateur ne sont **pas** couvertes par ce rapport.
- En cas de retard ou d’échec GitHub Actions, le rapport reste ancien : toujours vérifier l’horodatage.
- Aucune clé, connexion wallet ni fonction de passage d’ordre.

Source : [Hyperliquid API publique](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).
