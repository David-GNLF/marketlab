# MarketLab — récapitulatif complet

*Document de référence. Il décrit ce que fait la plateforme, comment elle
tourne, et — surtout — ce qu'elle vaut réellement. Dernière mise à jour :
2026-07-28.*

En ligne : **https://marketlab.gnlfconsult.com**
Dépôt : **https://github.com/David-GNLF/marketlab** (public)

---

## 1. Ce que c'est

Une plateforme d'analyse des marchés financiers qui produit, chaque jour, un
**verdict motivé par actif** — quoi faire, quand, avec quelle marge de gain
projetée — et qui **mesure publiquement si ces verdicts valaient quelque
chose**.

Trois principes la gouvernent, et expliquent la plupart des choix techniques :

1. **Tout ce qui décide doit pouvoir être jugé.** Chaque verdict est
   journalisé avec les notes de ses composantes, puis confronté aux prix
   réels une fois son horizon écoulé.
2. **Une brique gagne sa place, elle ne la reçoit pas.** Aucune composante ne
   pèse dans le verdict sans avoir démontré sa valeur statistiquement.
3. **Aucune dépendance qui puisse casser sans surveillance.** Pas de serveur
   permanent, pas de modèle de langage, pas d'abonnement : sources gratuites,
   GitHub Actions, hébergement mutualisé.

**Portée** : 59 actifs suivis (screener) sur 8 univers — actions US, EU et
asiatiques, indices, forex, matières premières, crypto, BRVM — dont **36
salles de marché** complètes (analyse détaillée + plan chiffré).

---

## 2. Architecture

Un hébergement mutualisé cPanel ne peut pas exécuter l'application : pas de
processus permanent, requêtes coupées avant la fin des simulations. D'où le
choix fondateur : **déplacer les calculs hors du web**.

```
GitHub Actions (gratuit)          Hébergement cPanel (open.bj)
─────────────────────────         ────────────────────────────────
publication.yml  22h UTC   ─┐
  tests (bloquants)         │     site statique + JSON
  génération des verdicts   ├──►  /donnees/*.json
  audit de cohérence        │     /index.html  (React)
  robots de trading         │     /cours.php   (relais de cotations)
  publication FTPS         ─┘     /trading/    (espace de trading)
                                  /admin/      (administration)
alertes.yml      chaque heure ──► /donnees/alertes_recentes.json + ntfy
resume.yml       06h UTC      ──► résumé quotidien ntfy
```

- **Python** calcule et publie des JSON ; le site les lit sans serveur
  applicatif.
- **PHP** ne sert que ce qui doit être interactif : cotations, trading,
  administration.
- **FTPS différentiel** : seuls les fichiers modifiés sont transférés.

---

## 3. Les outils d'analyse (31 modules)

| Domaine | Modules | Ce qu'ils apportent |
|---|---|---|
| Technique | `indicators`, `signals`, `screener`, `levels` | tendance, momentum, RSI/MACD/Bollinger, supports et résistances, plan chiffré |
| Outils de courtier | `broker_tools` | ADX/DMI, Supertrend, Ichimoku, Fibonacci, Stochastique, OBV |
| Prévision | `forecast` | Monte Carlo, intervalles, probabilité de toucher un niveau, analogues historiques |
| Fondamentaux | `fundamentals` | valorisation, rentabilité, endettement, croissance |
| Macro & moteurs | `macro`, `drivers`, `eco_calendar` | carry forex, taux réels, structure des matières, agenda économique |
| Événements | `events` | études autour des publications de résultats |
| Saisonnalité | `seasonality` | régularités mensuelles et hebdomadaires |
| Sentiment | `sentiment_marche`, `news` | indice peur/avidité, VIX, VVIX, skew, tonalité des actualités |
| Positionnement | `cot` | rapports COT de la CFTC |
| Corrélations | `correlations` | matrices, clusters, diversification réelle |
| Décision | `decision` | **convergence** : le verdict et son bilan |

---

## 4. Le verdict — la convergence

`decision.dossier()` agrège sept composantes pondérées, applique des vetos,
et produit un avis lisible.

**Composantes et pondérations de base** : technique 25 %, prévision 20 %,
analogues 15 %, fondamentaux 15 % (actions seulement), moteurs 15 %,
saisonnalité 5 %, sentiment 5 %. Les poids sont renormalisés quand une
composante est absente.

**Brique candidate** : le consensus des six outils de courtier est journalisé
(`c_brokers`) et mesuré comme les autres, mais **ne pèse rien** tant qu'il n'a
pas fait ses preuves.

**Vetos** (ils priment sur la note) : espérance négative du plan,
publication de résultats dans l'horizon, régime « agitation sans direction »,
ratio gain/risque trop faible.

**Sortie par actif** : avis (Favorable / Neutre / Défavorable / S'abstenir),
note, concordance entre composantes, probabilité de hausse, scénario porteur
et risque extrême, et un **plan chiffré** — entrée, stop, objectif, ratio
gain/risque, probabilités de toucher l'un ou l'autre, espérance.

### Deux horizons en parallèle

Depuis le 2026-07-28, chaque actif porte **deux paris indépendants** :
20 séances (officiel) et **5 séances**. Ce ne sont pas les mêmes chiffres
relus autrement : prévision, analogues, stop et objectif sont recalculés pour
la fenêtre.

Le stop suit désormais **√(horizon/20)** — la volatilité croît comme la racine
du temps. Il vaut exactement 2×ATR à 20 séances (historique préservé) et
moitié moins à 5 séances. Une borne empêche un support éloigné de fabriquer un
stop hors de portée de la fenêtre.

---

## 5. Le tribunal : ce que l'outil vaut réellement

C'est la partie la plus importante, et la plus inconfortable.

**Journal** : `data_local/journal_decisions.csv`, clé (date + symbole +
horizon), avec la note de chaque composante. 3 372 lignes, dont un backfill
point-in-time de 2 ans (3 232 verdicts rétro, calculés uniquement sur des
données antérieures à leur date).

**Mesure de compétence** : IC de Spearman **transversal**, calculé date par
date entre la note et le rendement advenu, moyenné à la Fama-MacBeth, avec une
erreur-type de **Newey-West** au retard de l'horizon.

> **Pourquoi cette complication.** Les verdicts se recouvrent : avec un horizon
> de 20 séances et des verdicts quasi quotidiens, vingt lignes voisines
> décrivent presque le même bout de marché. Les traiter comme indépendantes
> gonfle la certitude d'un facteur √20 ≈ 4,5. Une première version est tombée
> dans ce piège et a annoncé comme « démontré » un résultat qui ne l'était pas.

### État au 2026-07-28

| Horizon | IC transversal | t | Épisodes indépendants | IC détectable |
|---|---|---|---|---|
| 3 séances | +0,0165 | 0,71 | 73 | 0,065 |
| **5 séances** | **+0,0232** | **1,08** | **43** | **0,060** |
| 10 séances | +0,0158 | 0,69 | 21 | 0,064 |
| 20 séances | +0,0123 | 0,39 | 10 | 0,088 |

**Conclusion honnête : aucune compétence démontrée, ni dans un sens ni dans
l'autre.** L'IC est positif à tous les horizons, mais aucun n'est
significatif. Deux ans de recul ne suffisent pas : à 20 séances, il en
faudrait une vingtaine ; à 5 séances, environ deux de plus. C'est la raison
d'être de l'horizon court.

**Apprentissage des pondérations** : une composante ne gagne du poids que si
la **borne basse de son IC à 95 %** est positive, calculée sur la taille
d'échantillon **effective** (n ÷ horizon). Aucune ne l'étant aujourd'hui, les
pondérations restent à leurs valeurs de base — et le rapport est réécrit
chaque jour, y compris quand il conclut « rien n'est démontré ».

**Bilan des alertes** : chaque alerte livrée est consignée avec sa règle et
son sens, puis jugée 5 séances plus tard. Une règle qui crie pour rien coûte
l'attention de son lecteur.

---

## 6. Le site

Onglets : **🎯 Décisions** (cartes triables du plus favorable au plus
défavorable, avec la ligne ⚡ du pari à 5 séances), **🔔 Alertes** (fil horaire
+ bilan des règles), **🏆 Concours**, **Marchés**, **Titre** (salle de marché
complète), **Macro & agenda**, **Fondamentaux**, **Corrélations**,
**Portefeuille**, et **🏦 Trader**.

**Cotations vivantes** — `cours_lib.php` est la source de prix **unique** de
toute la plateforme : cache partagé de 60 s, téléchargement parallèle, Binance
pour la crypto, Yahoo pour le reste, repli en cascade sur miroir puis sur le
dernier cours publié. Liste blanche des symboles publiés : le relais ne peut
pas servir de proxy.

Fraîcheur affichée sans complaisance : 🟢 direct, 🌙 marché fermé, 📄 repli.
Crypto en temps réel, forex à la minute, actions et matières au différé de
~15 min imposé par les bourses aux sources gratuites.

---

## 7. L'environnement de trading

1 000 $ virtuels par compte, levier jusqu'à ×20, spread simulé de 0,05 %.

- **Identité unique** : la page utilise l'authentification du site
  (`PHP_AUTH_USER`). Pas de second mot de passe.
- **Ticket d'ordre** : marché, limite ou stop, avec récapitulatif en direct
  (exposition, prix de liquidation, P&L au stop et à l'objectif, ratio
  gain/risque).
- **Règles de courtier** : ordres au marché refusés hors séance (d'après les
  heures publiées par la bourse, jamais d'après l'âge de la cotation) ; frais
  de portage nocturnes de 6 %/an sur la part empruntée ; durée de validité des
  ordres en attente, mise rendue à l'échéance.
- **Tenue quotidienne** : stops, objectifs et liquidations appliqués sur les
  extrêmes de séance réels ; si stop et objectif sont touchés le même jour, le
  **stop** est réputé atteint en premier (hypothèse prudente).
- **Équité** : une seule définition, dans `ml_equite_compte()` — cash + marge
  réservée + marges engagées + P&L latent. Page, admin et robot affichent le
  même montant.

### Les deux robots

| Robot | Horizon | Règles |
|---|---|---|
| `claude` | 20 séances | identiques |
| `claude5` | 5 séances | identiques |

Long uniquement sur avis Favorable avec plan ; mise 5 % de l'équité ×
multiplicateur de taille ; levier forex ×5, matières ×3, actions et crypto ×2 ;
4 positions maximum ; stop et objectif du plan ; clôture si le verdict se
retourne ; **tout est journalisé, y compris l'inaction**.

Ils ne diffèrent que par l'horizon : **l'écart entre eux ne mesure donc qu'une
chose — ce que vaut l'horizon.**

> Le robot est *long only* parce que le bilan n'a jamais justifié la vente à
> découvert.

---

## 8. Administration et accès

Le domaine entier est derrière une authentification. `/acces/` en est le seul
chemin exempté, pour permettre à un invité de définir son mot de passe.

- Création de compte **par invitation e-mail** : l'administrateur ne connaît
  jamais le mot de passe (jeton sha256 à usage unique, 72 h).
- Rôles par domaine (site / trading / admin), journal d'audit, réinitialisation
  par e-mail, remise à zéro d'un compte de trading.
- Protections : bcrypt, CSRF, verrouillage après 8 échecs en 15 min,
  `Cache-Control: no-store`.

---

## 9. Alertes

Sept règles : bascule d'avis, RSI extrême, événements macro imminents,
résultats à moins de 7 jours, sentiment extrême, mouvement de séance ≥ 3σ
(urgent), VIX en backwardation (urgent).

Livrées par **ntfy** (aucun compte requis), avec état anti-doublon persistant.
Chaque passage horaire alimente aussi le fil du site — et l'horodate même sans
alerte : *le silence redevient une information*.

Le robot notifie ses mouvements ; une liquidation part en priorité urgente.

---

## 10. Garde-fous

**Audit de cohérence bloquant** (`scripts/verifier_coherence.py`) : 24
invariants vérifiés de la configuration au site publié, avant toute
publication. Il empêche une dérive de périmètre silencieuse (un univers absent
du screener, une fiche manquante, un horizon incohérent).

**64 tests automatisés** exécutés en CI **avant** tout le reste :

| Fichier | Ce qu'il protège |
|---|---|
| `test_trading.py` | déclenchement des ordres, stops, liquidations, portage, équité |
| `test_invariant_equite.py` | l'équité Python == l'équité PHP, sur les mêmes comptes |
| `test_marche_ouvert.py` | le garde « marché fermé », y compris séances à cheval sur minuit |
| `test_apprentissage.py` | une composante ne gagne du poids que si c'est prouvé |
| `test_competence.py` | **taux de faux positifs sur du bruit pur** conforme au seuil |
| `test_poids_effectifs.py` | une conclusion négative ramène aux poids de base |
| `test_horizons.py` | la mesure parallèle et sa puissance |
| `test_plan_horizon.py` | le stop est à l'échelle de la durée de détention |

---

## 11. Ce qui tourne sans personne

Aucun abonnement, aucune clé d'API, aucun modèle de langage. Les dépendances
de production sont pandas, numpy, scipy, requests, yfinance, lxml, pyarrow.
Les seuls secrets sont les accès FTP et le topic ntfy.

- **22h00 UTC** — publication : tests, génération, audit, robots, transfert
- **:05, :20 et :40 de chaque heure** — alertes + fil du site
- **06h00 UTC** — résumé quotidien

> **Cadence réelle des alertes.** Les exécutions planifiées gratuites de
> GitHub sont au mieux-effort. Mesuré sur 24 h avec un **unique** cron
> horaire : 10 passages effectifs au lieu de 24, écarts de 1 h 30 à 4 h 30
> (médiane 2 h 24). D'où trois déclenchements par heure depuis le
> 2026-07-28 — cela ne garantit rien, mais ramène l'intervalle typique bien
> en deçà de l'heure.
>
> Multiplier les passages est sans danger : l'état anti-doublon empêche de
> renvoyer deux fois la même alerte, et le verrou de concurrence empêche deux
> passages simultanés. Le seul horodatage qui fasse foi est celui du dernier
> passage, affiché sur la page Alertes.

---

## 12. Limites assumées

- **L'outil n'a pas prouvé sa valeur.** C'est écrit sur la page d'accueil.
- Les cours d'actions et de matières sont **différés d'environ 15 minutes** :
  contrainte des bourses, pas du code.
- Le concours est un **arrêté du soir** ; la page de trading valorise en
  direct. Un écart pendant la journée est normal.
- L'argent est **virtuel**. Rien ici ne constitue un conseil en investissement.
