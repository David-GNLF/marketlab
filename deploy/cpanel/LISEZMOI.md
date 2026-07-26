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

Hébergeur : **open.bj**, panneau cPanel sur
`https://cloud740.thundercloud.uk:2083/`. Domaine visé :
**`marketlab.gnlfconsult.com`**.

### 1. Créer le sous-domaine dans cPanel

*Domaines → Créer un domaine* : `marketlab.gnlfconsult.com`, racine
`public_html/marketlab`.

Le DNS de `gnlfconsult.com` est délégué à `ns3.os-cloud.net` /
`ns4.os-cloud.net`, le cluster de l'hébergeur : cPanel y propage normalement
le nouveau sous-domaine tout seul. Vérifier après quelques minutes :

```bash
nslookup marketlab.gnlfconsult.com
```

L'adresse attendue est **149.255.62.147** (celle de `gnlfconsult.com`). Si
rien ne résout au bout d'une heure, ajouter l'enregistrement à la main dans
*cPanel → Éditeur de zone* : type `A`, nom `marketlab`, valeur
`149.255.62.147`. En dernier recours, demander à open.bj de propager la zone.

> Attention à ne pas confondre : `kilo.gnlfconsult.com` pointe vers
> `41.86.235.43`, une infrastructure différente. MarketLab doit viser
> l'hébergement cPanel, pas ce proxy.

### 2. Créer un compte FTP dédié

*Fichiers → Comptes FTP* : un compte limité au dossier `public_html/marketlab`
— ne pas réutiliser le compte principal, qui donne accès à tout l'hébergement.

### 3. Renseigner les accès en local

`data_local/cpanel.json` (jamais versionné) :

```json
{
  "hote": "cloud740.thundercloud.uk",
  "utilisateur": "marketlab@gnlfconsult.com",
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
`public_html/marketlab`. HTTPS est fourni par AutoSSL — vérifier que
`https://marketlab.gnlfconsult.com` répond bien avant d'activer la protection,
sans quoi le mot de passe circulerait en clair.

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
