# MarketLab — boîte à outils d'analyse des marchés financiers

Outils personnels d'aide à la décision pour l'analyse des marchés (actions US/EU,
forex, crypto, BRVM) : données, indicateurs techniques, screener, signaux,
contexte macro et backtesting.

> **Avertissement** : ces outils produisent des analyses et des signaux
> statistiques, pas des prédictions fiables. Aucun signal ne constitue un
> conseil en investissement. Toujours valider par backtest et gérer le risque
> (taille de position, stop-loss).

## Architecture

```
marketlab/
├── marketlab/              # bibliothèque cœur
│   ├── config.py           # chemins, univers de titres suivis (watchlists)
│   ├── data/               # fournisseurs de données (tous gratuits)
│   │   ├── base.py         # cache disque (parquet) + interface commune
│   │   ├── yahoo.py        # actions US/EU, indices, forex (yfinance)
│   │   ├── binance.py      # crypto spot (API publique REST, sans clé)
│   │   ├── fred.py         # séries macro US (CSV fredgraph, sans clé)
│   │   └── brvm.py         # BRVM : import CSV manuel + scraping best-effort
│   ├── forecast.py         # PRÉVISION : cône Monte Carlo, GARCH/EWMA, régime,
│   │                       #   analogues historiques, calibration
│   ├── levels.py           # supports/résistances, pivots, plan de position
│   ├── fundamentals.py     # valorisation, qualité, croissance, solidité (actions)
│   ├── correlations.py     # matrice, bêta, risque et diversification du portefeuille
│   ├── events.py           # étude d'événements autour des résultats trimestriels
│   ├── seasonality.py      # effets de calendrier, testés contre le sur-apprentissage
│   ├── indicators.py       # SMA, EMA, RSI, MACD, Bollinger, ATR, volatilité
│   ├── signals.py          # règles de signaux + score composite par titre
│   ├── screener.py         # balayage d'un univers → tableau classé
│   ├── backtest.py         # moteur vectorisé : équité, Sharpe, drawdown
│   └── macro.py            # tableau de bord macro (inflation, taux, courbe)
├── app/
│   └── dashboard.py        # dashboard web Streamlit (Phases 1-3)
├── api/
│   └── main.py             # API REST FastAPI (Phase 4) — port 8600, /docs
├── front/                  # front React (Vite + Recharts), proxy /api → 8600
└── scripts/
    ├── demo.py             # test de bout en bout en ligne de commande
    ├── alertes.py          # moteur d'alertes → canal(aux) configuré(s)
    ├── configurer_alertes.py  # assistant : ntfy / e-mail / Windows / Telegram
    ├── historiser.py       # snapshot quotidien des scores
    └── paper.py            # CLI paper trading
```

## Démarrage

Le projet utilise un **environnement virtuel dédié** (`.venv`) : les tâches
planifiées Windows ne voient pas forcément le `site-packages` utilisateur, un
venv au chemin explicite supprime toute ambiguïté.

```bash
C:\Python314\python.exe -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

```bash
.venv\Scripts\python scripts\demo.py          # données + indicateurs + backtest
.venv\Scripts\python -m streamlit run app\dashboard.py   # http://localhost:8501
```

Interface Phase 4 (API + front React) :

```bash
python -m uvicorn main:app --app-dir api --port 8600
```

```bash
npm install --prefix front && npm run dev --prefix front
```

Front sur http://localhost:5180 (proxy vers l'API) ; docs interactives de
l'API sur http://localhost:8600/docs.

## Alertes (canal au choix)

Quatre canaux au choix, configurables par un assistant :

```bash
.venv\Scripts\python scripts\configurer_alertes.py
```

| Canal | Compte requis | Portée |
|---|---|---|
| **ntfy** *(recommandé)* | **aucun** | push sur téléphone (app ntfy) et navigateur |
| **email** | compte mail existant | partout |
| **windows** | aucun | notification de bureau, PC allumé uniquement |
| **telegram** | compte Telegram + bot @BotFather | push sur téléphone |

Plusieurs canaux peuvent être actifs en même temps (`"canaux": ["ntfy", "email"]`
dans `data_local/notifications.json`). L'ancien `data_local/telegram.json` reste
lu automatiquement s'il existe.

**ntfy en pratique** : le nom du topic *est* le secret — qui le connaît reçoit
les alertes. L'assistant en génère un aléatoire ; s'abonner ensuite depuis
l'app ntfy (Android/iOS) ou en ouvrant `https://ntfy.sh/<topic>`. Les messages
transitent en clair par le serveur public ntfy.sh ; pour une confidentialité
totale, ntfy s'auto-héberge (renseigner alors `serveur` et éventuellement
`jeton` dans la configuration).

**Gmail en pratique** : activer la validation en 2 étapes puis créer un
« mot de passe d'application » sur `myaccount.google.com/apppasswords`.

> `data_local/notifications.json` contient des secrets : ne pas le partager.

Vérifier à tout moment : `.venv\Scripts\python scripts\alertes.py --test`

Règles : changement d'avis vers/depuis « Achat fort »/« Vente forte », RSI < 25
ou > 75 (1×/jour/titre), événements macro à fort impact sous 24 h.
`--dry-run` affiche sans envoyer.

**Anti-doublon** : l'état (`.cache/alert_state.json`) n'est consommé que si les
alertes ont réellement été livrées. Un dry-run, un Telegram non configuré ou un
envoi en échec laissent l'état intact — aucune alerte n'est perdue sans avoir
été vue. Conséquence : le premier envoi après configuration de Telegram
enverra d'un coup tous les signaux forts en cours.

## Tâches planifiées (Windows)

Deux tâches sont enregistrées sous le dossier `\MarketLab\` du Planificateur :

| Tâche | Fréquence | Lanceur |
|---|---|---|
| `\MarketLab\Alertes` | toutes les heures | `scripts\tache_alertes.cmd` |
| `\MarketLab\Historisation` | tous les jours à 22h30 | `scripts\tache_historiser.cmd` |

Les lanceurs `.cmd` forcent l'UTF-8, utilisent le python du `.venv`, écrivent
dans `data_local/logs/*.log` et **propagent le code de sortie** (sans quoi le
Planificateur afficherait « succès » même quand le script plante).

Gestion :

```bash
Get-ScheduledTask -TaskPath "\MarketLab\" | Select-Object TaskName, State
```

```bash
Get-ScheduledTaskInfo -TaskPath "\MarketLab\" -TaskName "Alertes" | Select-Object LastRunTime, LastTaskResult, NextRunTime
```

Pour recréer les tâches depuis zéro, voir `scripts/planifier.ps1`.

## Feuille de route

- **Phase 1 (livrée)** — données gratuites multi-marchés, indicateurs,
  screener, score de signaux, macro US, backtest simple, dashboard Streamlit.
- **Phase 2 (livrée)** — calendrier économique (ForexFactory), alertes
  Telegram (`scripts/alertes.py`), sentiment des news (Google News RSS +
  lexique FR/EN, onglet Analyse), historisation quotidienne des scores
  (`scripts/historiser.py`) et mesure du pouvoir prédictif du score
  (`score_history.py` : IC de Spearman + quintiles, onglet ML).
- **Phase 3 (livrée)** — modèles ML de direction (`ml.py` :
  HistGradientBoosting, features techniques normalisées, validation
  walk-forward stricte, stratégie sur probabilité nette de frais) et paper
  trading (`paper.py` + `scripts/paper.py` : portefeuille virtuel USD,
  achats/ventes au dernier cours, conversion EUR/USDT, mode `auto` qui
  exécute les signaux du screener). Onglets ML et Paper trading au dashboard.
- **Phase 4 (livrée)** — API FastAPI (port 8600, `/docs`) + front React
  (Vite + Recharts) ; features macro dans le ML ; **exécution
  semi-automatisée** (`orders.py` : propositions dimensionnées par le risque
  — 1 % du capital, stop 2×ATR, plafond 20 % — à valider une par une ; la
  validation exécute en PAPIER et produit un ticket à recopier chez le
  courtier ; MarketLab ne passe jamais d'ordre réel) ; **connecteur premium
  Twelve Data** optionnel (`data_local/providers.json` :
  `{"twelvedata_api_key": "..."}` — actions US + forex, repli Yahoo
  automatique) ; **build de production** : `npm run build --prefix front`
  puis l'API sert `front/dist` sur http://localhost:8600 (une seule commande
  pour tout : uvicorn).

## Prévision probabiliste (onglet 🔮 Prévision)

Ce que l'outil prévoit — et ce qu'il ne prévoit pas :

| Grandeur | Prévisible ? | Comment |
|---|---|---|
| **Volatilité** | **oui** (persistante) | EWMA (RiskMetrics λ=0,94) + GARCH(1,1) |
| **Distribution des prix** | **oui, en probabilité** | Monte Carlo, bootstrap par blocs (20 000 trajectoires) |
| **Zones de prix qui comptent** | oui | supports/résistances par regroupement d'extrêmes |
| **Régime de marché** | oui | tendance × volatilité relative |
| **Direction exacte du cours** | **non** | ~50 % mesuré — d'où le raisonnement en probabilités |

Le bootstrap **par blocs** (et non un tirage indépendant) préserve le
clustering de volatilité et les queues épaisses : une loi normale
sous-estimerait gravement les mouvements extrêmes.

**Calibration — le contrôle qualité qui compte.** `forecast.calibration()`
rejoue la projection à des dates passées, en n'utilisant que les données
alors disponibles, et compte la couverture réelle. Mesuré sur 100 tests par
titre :

| Titre | Intervalle 80 % | Intervalle 50 % | Direction |
|---|---|---|---|
| AAPL (20 j) | 82 % | 47 % | 57 % |
| MSFT (20 j) | 87 % | 56 % | 53 % |
| BTCUSDT (20 j) | 80 % | 55 % | 49 % |
| EURUSD (20 j) | 81 % | 51 % | 45 % |

Les intervalles tiennent leurs promesses ; la direction reste proche du
hasard. C'est exactement la raison pour laquelle il faut dimensionner par le
risque plutôt que parier sur un sens.

**Plan de position** (`levels.plan`) : entrée, stop (le plus protecteur entre
2×ATR et le support/résistance le plus proche), objectif, ratio gain/risque,
**probabilité de toucher** le stop et l'objectif issues des simulations,
espérance mathématique, et taille de position pour un risque donné. Une
espérance négative fait explicitement écarter la configuration.

## Fondamentaux (onglet 📊)

Quatre axes notés 0-100 à partir de seuils explicites (`fundamentals.SEUILS`),
puis moyenne pondérée : **valorisation** 30 % (PER, cours/actif, VE/EBITDA),
**qualité** 30 % (marges, rentabilité des capitaux), **croissance** 25 %
(CA, bénéfices), **solidité** 15 % (dette/capitaux, liquidité).

Actions uniquement — crypto, devises et indices n'ont pas de bilan et sont
écartés proprement. Les seuils ne sont **pas** normalisés par secteur : une
banque et un éditeur de logiciels n'ont pas les mêmes marges, d'où
`comparer()` qui met en regard des titres d'un même univers. Un axe sans
donnée est ignoré plutôt que noté zéro, et `couverture_donnees_%` indique la
part de critères réellement disponibles.

> Piège corrigé : yfinance renvoie `dividendYield` **déjà en pourcentage**
> (1,70 = 1,70 %). Le multiplier par 100 affiche un rendement de 170 %.

## Corrélations et risque (onglet 🔗)

- **Matrice de corrélation** et paires extrêmes : les plus corrélées sont des
  doublons déguisés, les moins corrélées la vraie diversification.
- **Corrélation par régime** — mesurée : sur les actions US, la corrélation
  moyenne passe de **0,25 en marché calme à 0,56 en marché agité**. La
  diversification se dégrade quand on en a le plus besoin.
- **Bêta** vs indice de référence, avec le R² (part réellement expliquée).
- **Risque de portefeuille** : volatilité réelle *vs* somme pondérée des
  volatilités, bénéfice de diversification, concentration (HHI et nombre de
  positions équivalentes), et surtout **contribution de chaque ligne au
  risque** — une position peut peser 36 % du capital et 54 % du risque.
- **Suggestions de diversification** : candidats les moins corrélés aux
  positions détenues.

Les rendements sont alignés sur les dates communes (indispensable : la crypto
cote 7 j/7, pas les actions) et calculés en devise locale — l'effet de change
d'un portefeuille mixte n'est pas isolé.

## Résultats trimestriels (onglet 📣)

Les publications concentrent les mouvements les plus brutaux : un écart de
plusieurs pourcents en une séance y est banal, et il traverse un stop sans
prévenir. Le module mesure, sur l'historique propre à chaque titre :

- **La réaction du jour J** : amplitude moyenne et médiane (l'amplitude est
  bien plus stable que le sens), part de hausses, pire et meilleure séance.
- **La dérive post-annonce** (*post-earnings announcement drift*) : rendement
  anormal cumulé avant et après publication.
- **Le lien surprise → réaction** : mesuré, souvent faible. Sur AAPL la
  corrélation est **négative (−0,25)** — battre le consensus ne garantit rien,
  le marché réagit aux perspectives plus qu'au chiffre publié.
- **Le risque d'événement à venir** : y a-t-il une publication dans mon
  horizon de position ?

Méthode : rendement anormal par modèle de marché (`AR = R − α − β·R_marché`),
avec β estimé sur une fenêtre **antérieure** à chaque événement pour éviter
toute contamination. Indice de référence choisi selon la place (^GSPC, ^FCHI,
^GDAXI, ^STOXX50E).

**Intégrations** — c'est là que ça devient utile :

- `levels.plan()` signale automatiquement une publication tombant dans
  l'horizon et l'ajoute à sa recommandation.
- Les **alertes** préviennent 7 jours à l'avance pour les titres détenus en
  papier et ceux à avis fort (règle 4, une fois par publication).

> Les dates futures sont des *estimations* Yahoo tant que l'entreprise n'a pas
> confirmé — à vérifier auprès de la société avant d'engager une décision.

## Saisonnalité (onglet 🗓️)

Domaine où le sur-apprentissage guette : en testant 12 mois au seuil de 5 %,
l'espérance est de **0,6 faux positif par titre**. Chaque effet passe donc
trois garde-fous, et n'est retenu que s'il franchit les trois :

1. **Test de Student** sur la moyenne des rendements ;
2. **Correction de Bonferroni** — p-value multipliée par le nombre d'effets
   testés simultanément (conservateur, et c'est voulu) ;
3. **Stabilité temporelle** — l'effet se retrouve-t-il sur la première *et* la
   seconde moitié de l'historique ?

Effets couverts : mois, jour de la semaine, période du mois (les flux de fonds
suivent le calendrier), et l'effet Halloween (« Sell in May »).

**Ce que ça donne réellement**, sur 20 ans d'historique :

| Titre | Effets survivants |
|---|---|
| S&P 500 | juillet (+2,54 %/mois, p corrigée 0,039) |
| AAPL | juillet (+6,68 %, 89,5 % de mois positifs) · lundi, mardi, mercredi |
| MSFT | **aucun** |
| BTCUSDT | **aucun** (9 ans seulement) |

L'effet Halloween, pourtant le plus célèbre, **ne ressort significatif sur
aucun** des titres testés. C'est le résultat attendu d'une méthode honnête :
la plupart des régularités de calendrier ne résistent pas à un test correct.
Les effets retenus sont à traiter comme un léger biais de contexte, jamais
comme un signal autonome.

## Paper trading

```bash
python scripts/paper.py init --capital 10000
python scripts/paper.py acheter AAPL 1500
python scripts/paper.py etat                 # valorisation + P&L
python scripts/paper.py auto --dry-run       # signaux du screener, simulés
python scripts/paper.py auto                 # exécution papier
```

Portefeuille dans `data_local/paper_portfolio.json`. Forex exclu (levier non
modélisé) ; exécution au dernier cours de clôture, sans spread ni slippage —
les performances papier sont donc optimistes par construction.

## Historisation quotidienne des scores

```
schtasks /Create /SC DAILY /ST 22:30 /TN "MarketLab historisation" /TR "python C:\Users\Dav\Downloads\PROJET\marketlab\scripts\historiser.py"
```

Accumule les scores réellement émis dans `data_local/historique_scores.csv`
pour vérifier, avec le temps, leur pouvoir prédictif hors historique.

## Sources de données (gratuites)

| Source | Marchés | Clé requise |
|---|---|---|
| Yahoo Finance (`yfinance`) | Actions US/EU, indices, forex, matières premières | non |
| Binance API publique | Crypto spot (OHLCV jusqu'à 1m) | non |
| FRED (fredgraph CSV) | Macro US : CPI, taux, chômage, courbe des taux | non |
| BRVM | Actions UEMOA — import CSV manuel + scraping best-effort | non |
