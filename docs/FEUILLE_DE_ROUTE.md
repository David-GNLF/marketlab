# Feuille de route MarketLab

Ce fichier est **la** source de la feuille de route affichée dans l'espace
DevApp du site. Une case n'est cochée que lorsque la chose est livrée ET
vérifiée en ligne — pas quand elle est écrite.

Format : `- [x] Titre — précision`.

## Socle de données

- [x] Instantané statique publié par FTPS (aucun calcul à la visite)
- [x] Cotations fraîches par relais PHP, cache serveur de 60 s
- [x] Magasin de barres 5 minutes et volatilité réalisée
- [x] Série horaire publiée (120 jours) pour les pas H1 et H4
- [x] Bougies OHLCV publiées sur cinq ans, format colonnaire
- [x] Relais de bougies fraîches pour la séance en cours
- [ ] Cotations d'actions sous les 5 minutes — dépend d'une source tierce
- [ ] Historique BRVM automatisé (aujourd'hui import CSV manuel)

## Moteur de décision

- [x] Verdict avec vetos, plan de trade et taille de position
- [x] Deux horizons suivis en parallèle (20 et 5 séances)
- [x] Mesure de compétence Fama-MacBeth avec erreur-type Newey-West
- [x] Calibration des probabilités par régression isotone
- [x] Stop dimensionné à l'horizon, pas fixe
- [x] Indice de surprise économique
- [ ] Repondération automatique sur bilan réel glissant

## Robots

- [x] « claude » — tous marchés, 20 séances (référence)
- [x] « claude5 » — même univers, horizon 5 séances
- [x] « claudefx » — forex uniquement, 1 000 $
- [x] Rapport de séance : MAE, MFE, stops qui coupaient du bruit
- [ ] Passage en argent réel — conditionné à une compétence démontrée

## Interface

- [x] Retrait des pictogrammes décoratifs, animations calmées
- [x] Logo et signature « by GNLF Consult »
- [x] Graphique de trading : bougies, 7 pas, réticule OHLC, échelle log
- [x] Plan de trade tracé sur le prix
- [x] Liste de suivi et filtres par thème
- [x] Coquille en rail de navigation, pleine largeur
- [x] Même graphique dans l'espace de trading
- [x] Espace DevApp
- [ ] Version installable sur téléphone (PWA)

## Exploitation

- [x] Publication quotidienne automatique
- [x] Veille d'alertes en balayage continu
- [x] Point du matin sur l'activité des robots
- [x] Pages PHP livrées avec la publication
- [x] Garde-fou : les comptes de trading ne sont jamais écrasés
- [ ] Surveillance externe de disponibilité du site
