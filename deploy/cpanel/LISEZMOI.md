# Déploiement sur hébergement mutualisé cPanel

## Pourquoi cette architecture

Un hébergement mutualisé ne peut pas faire tourner MarketLab tel quel : pas de
processus permanent (donc pas de Streamlit), requêtes coupées au bout de
quelques dizaines de secondes (or le Monte Carlo et le walk-forward prennent
des minutes), et des quotas d'inodes qu'une installation de scikit-learn et
pyarrow ferait exploser.

La solution n'est pas d'emballer l'application autrement, mais de **déplacer
les calculs hors du web** :

```
  Poste de travail (ou cron)          Hébergement cPanel
  ─────────────────────────           ──────────────────
  scripts/publier.py                  site statique
    ├─ calcule tout                     ├─ index.html + assets/
    ├─ écrit site/donnees/*.json  ─────▶ ├─ donnees/*.json
    └─ transfère en FTPS                 └─ (aucun Python, aucune base)
```

Conséquences : **zéro dépendance côté serveur**, affichage instantané (les
fichiers sont déjà calculés), aucune limite de durée sur les calculs, et un
fonctionnement garanti sur n'importe quel hébergement — y compris depuis un
téléphone.

En contrepartie, le site est en **consultation seule** : les opérations
(paper trading, validation d'ordres) se font depuis le poste de travail, et
l'exploration fine reste dans le dashboard Streamlit local.

## Mise en place

### 1. Créer le sous-domaine dans cPanel

*Domaines → Créer un domaine* : `marketlab.open.bj`, avec pour racine
`public_html/marketlab`. cPanel crée le dossier et l'entrée DNS si le domaine
est géré chez lui.

### 2. Créer un compte FTP dédié

*Fichiers → Comptes FTP* : un compte limité au dossier `public_html/marketlab`
— ne pas réutiliser le compte principal, qui donne accès à tout l'hébergement.

### 3. Renseigner les accès en local

`data_local/cpanel.json` (jamais versionné) :

```json
{
  "hote": "cloud740.thundercloud.uk",
  "utilisateur": "marketlab@open.bj",
  "mot_de_passe": "...",
  "dossier_distant": "/public_html/marketlab"
}
```

Vérifier la connexion :

```bash
.venv\Scripts\python scripts\publier.py --tester
```

### 4. Publier

```bash
npm run build --prefix front
.venv\Scripts\python scripts\publier.py
```

Le transfert est **différentiel** : seuls les fichiers modifiés sont renvoyés,
donc les publications suivantes ne transmettent que les JSON (quelques
centaines de kilo-octets).

### 5. Automatiser

Ajouter une tâche planifiée quotidienne, après la clôture américaine :

```
schtasks /Create /SC DAILY /ST 23:00 /TN "MarketLab\Publication" /TR "C:\Users\Dav\Downloads\PROJET\marketlab\.venv\Scripts\python.exe C:\Users\Dav\Downloads\PROJET\marketlab\scripts\publier.py"
```

## Sécurité

Le site expose ton portefeuille et tes positions. Sur un hébergement public,
le protéger par mot de passe : *cPanel → Confidentialité du répertoire* sur
`public_html/marketlab`. HTTPS est fourni par AutoSSL — le vérifier avant
d'activer la protection, sans quoi le mot de passe circulerait en clair.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Page blanche | `front/dist` absent : lancer `npm run build --prefix front` |
| « Données indisponibles » | Les JSON n'ont pas été transférés : relancer la publication |
| Chiffres figés | La tâche planifiée ne tourne plus : vérifier le Planificateur |
| Échec FTPS | Mot de passe FTP modifié, ou hébergeur exigeant le mode passif désactivé |

> Une variante avec API Python (« Setup Python App » de cPanel) serait possible
> pour rendre le site interactif, mais elle réintroduit les limites de durée et
> les quotas d'inodes. L'architecture statique a été retenue pour cette raison.
