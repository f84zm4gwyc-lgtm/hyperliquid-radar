# Hyperliquid BTC / ETH — radar technique

**Mise à jour UTC : 2026-10-20T06:58:51.175000+00:00**

> Prix perpétuels Hyperliquid; le BTC spot (UBTC) peut présenter un écart. Cette page ne constitue pas un signal de trading. Vérifier que les données sont fraîches avant décision.

## BTC — prix mark 81795.4

Funding horaire : 0.00125% | Open interest : 39963.5964000001 BTC | Notionnel indicatif : 3268838352.98 USD

| UTC bougie | Cours clôture | EMA20 | EMA50 | EMA200 | RSI14 | MACD hist. | Volume / moyenne 20 | Plus haut 20 préc. | Plus bas 20 préc. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h — 2026-10-20T06:00:00+00:00 | 81697.0 | 81984.3391 | 81955.7744 | 82272.1014 | 42.86 | -30.8073 | 0.621x | 83073.0 | 81171.0 |
| 4h — 2026-10-20T04:00:00+00:00 | 82120.0 | 81943.1559 | 82242.2255 | 82262.7832 | 51.47 | 72.6569 | 1.205x | 83138.0 | 80644.0 |
| 1d — 2026-10-20T00:00:00+00:00 | 81914.0 | 82539.4412 | 80559.9225 | 76197.5121 | 48.17 | -399.1524 | 0.873x | 87235.0 | 79732.0 |

Graphiques (bougies clôturées) : [1h](BTC_1h.png) · [4h](BTC_4h.png) · [1d](BTC_1d.png)

## ETH — prix mark 2396.7

Funding horaire : 0.00099% | Open interest : 1077723.1426000006 ETH | Notionnel indicatif : 2582979055.87 USD

| UTC bougie | Cours clôture | EMA20 | EMA50 | EMA200 | RSI14 | MACD hist. | Volume / moyenne 20 | Plus haut 20 préc. | Plus bas 20 préc. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h — 2026-10-20T06:00:00+00:00 | 2395.8 | 2405.1093 | 2407.6758 | 2448.1443 | 44.58 | -0.1528 | 0.546x | 2446.1 | 2371.8 |
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
