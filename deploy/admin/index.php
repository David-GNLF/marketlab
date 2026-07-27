<?php
/**
 * MarketLab — espace d'administration des comptes d'accès.
 *
 * Gère les utilisateurs du .htpasswd qui protège le site : création,
 * changement de mot de passe, suppression. Aucune base de données : deux
 * fichiers JSON dans ce dossier (interdits d'accès web par le .htaccess).
 *
 * Sécurité : sessions httponly/secure/strict, jeton CSRF, verrouillage
 * après 8 échecs par IP (15 min), hachage bcrypt, écritures atomiques
 * avec verrou. Ce dossier reste par ailleurs derrière le Basic Auth du
 * site : il faut déjà un compte valide pour atteindre cette page.
 */

declare(strict_types=1);

const FICHIER_HTPASSWD = __DIR__ . '/../.htpasswd';
const FICHIER_ADMINS   = __DIR__ . '/admin_comptes.json';
const FICHIER_ESSAIS   = __DIR__ . '/tentatives.json';
const MAX_ECHECS = 8;
const FENETRE_ECHECS = 900; // secondes

session_set_cookie_params([
    'httponly' => true,
    'secure'   => !empty($_SERVER['HTTPS']),
    'samesite' => 'Strict',
]);
session_start();

// ---------------------------------------------------------------- utilitaires

function lire_json(string $chemin): array {
    if (!is_file($chemin)) return [];
    $donnees = json_decode((string)file_get_contents($chemin), true);
    return is_array($donnees) ? $donnees : [];
}

function ecrire_json(string $chemin, array $donnees): bool {
    $tmp = $chemin . '.tmp';
    if (file_put_contents($tmp, json_encode($donnees, JSON_PRETTY_PRINT
        | JSON_UNESCAPED_UNICODE), LOCK_EX) === false) return false;
    return rename($tmp, $chemin);
}

function ip_client(): string {
    return $_SERVER['REMOTE_ADDR'] ?? 'inconnue';
}

function verrouille(): int {
    $essais = lire_json(FICHIER_ESSAIS)[ip_client()] ?? [];
    $recents = array_filter($essais, fn($t) => $t > time() - FENETRE_ECHECS);
    if (count($recents) < MAX_ECHECS) return 0;
    return max($recents) + FENETRE_ECHECS - time();
}

function noter_echec(): void {
    $tous = lire_json(FICHIER_ESSAIS);
    $ip = ip_client();
    $tous[$ip] = array_values(array_filter($tous[$ip] ?? [],
        fn($t) => $t > time() - FENETRE_ECHECS));
    $tous[$ip][] = time();
    ecrire_json(FICHIER_ESSAIS, $tous);
}

function purger_echecs(): void {
    $tous = lire_json(FICHIER_ESSAIS);
    unset($tous[ip_client()]);
    ecrire_json(FICHIER_ESSAIS, $tous);
}

function jeton_csrf(): string {
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(24));
    }
    return $_SESSION['csrf'];
}

function verifier_csrf(): bool {
    return hash_equals($_SESSION['csrf'] ?? '', $_POST['csrf'] ?? '§');
}

function mot_de_passe_valide(string $p): ?string {
    if (strlen($p) < 10) return 'au moins 10 caractères requis';
    if (!preg_match('/[A-Za-z]/', $p) || !preg_match('/[0-9]/', $p))
        return 'lettres ET chiffres requis';
    return null;
}

// ------------------------------------------------------------------ htpasswd

function lire_htpasswd(): array {
    $comptes = [];
    if (is_file(FICHIER_HTPASSWD)) {
        foreach (file(FICHIER_HTPASSWD, FILE_IGNORE_NEW_LINES
                 | FILE_SKIP_EMPTY_LINES) as $ligne) {
            $pos = strpos($ligne, ':');
            if ($pos !== false) {
                $comptes[substr($ligne, 0, $pos)] = substr($ligne, $pos + 1);
            }
        }
    }
    return $comptes;
}

function ecrire_htpasswd(array $comptes): bool {
    $lignes = '';
    foreach ($comptes as $nom => $hachage) {
        $lignes .= $nom . ':' . $hachage . "\n";
    }
    $tmp = FICHIER_HTPASSWD . '.tmp';
    if (file_put_contents($tmp, $lignes, LOCK_EX) === false) return false;
    return rename($tmp, FICHIER_HTPASSWD);
}

// --------------------------------------------------------------------- état

$admins = lire_json(FICHIER_ADMINS);
$installe = !empty($admins['admins']);
$connecte = !empty($_SESSION['admin']);
$message = null;  // [type, texte]

// ------------------------------------------------------------------- actions

$action = $_POST['action'] ?? null;
$attente = verrouille();

if ($action && $attente > 0) {
    $message = ['erreur', "Trop d'échecs : réessayer dans "
        . ceil($attente / 60) . ' min.'];
    $action = null;
}

if ($action === 'installer' && !$installe) {
    $jeton = trim($_POST['jeton'] ?? '');
    $mdp = $_POST['mdp'] ?? '';
    if (!hash_equals($admins['setup_token'] ?? '§', $jeton)) {
        noter_echec();
        $message = ['erreur', 'Jeton d\'installation invalide.'];
    } elseif ($e = mot_de_passe_valide($mdp)) {
        $message = ['erreur', "Mot de passe refusé : $e."];
    } else {
        $admins = ['admins' => ['dav' => password_hash($mdp, PASSWORD_BCRYPT)]];
        ecrire_json(FICHIER_ADMINS, $admins);
        $installe = true;
        $message = ['ok', 'Administration initialisée — connectez-vous.'];
    }
}

if ($action === 'connexion' && $installe && !$connecte) {
    $nom = trim($_POST['nom'] ?? '');
    $hachage = $admins['admins'][$nom] ?? null;
    if ($hachage && password_verify($_POST['mdp'] ?? '', $hachage)) {
        session_regenerate_id(true);
        $_SESSION['admin'] = $nom;
        $connecte = true;
        purger_echecs();
        jeton_csrf();
    } else {
        noter_echec();
        $message = ['erreur', 'Identifiants incorrects.'];
    }
}

if ($action === 'deconnexion' && $connecte) {
    session_destroy();
    header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?'));
    exit;
}

if ($connecte && in_array($action, ['creer', 'mdp', 'supprimer', 'mdp_admin'], true)) {
    if (!verifier_csrf()) {
        $message = ['erreur', 'Session expirée — recharger la page.'];
    } else {
        $comptes = lire_htpasswd();
        $nom = trim($_POST['nom'] ?? '');

        if ($action === 'creer') {
            $mdp = $_POST['mdp'] ?? '';
            if (!preg_match('/^[a-zA-Z0-9_.-]{2,32}$/', $nom)) {
                $message = ['erreur', 'Nom invalide (2-32 caractères : '
                    . 'lettres, chiffres, . _ -).'];
            } elseif (isset($comptes[$nom])) {
                $message = ['erreur', "Le compte « $nom » existe déjà."];
            } elseif ($e = mot_de_passe_valide($mdp)) {
                $message = ['erreur', "Mot de passe refusé : $e."];
            } else {
                $comptes[$nom] = password_hash($mdp, PASSWORD_BCRYPT);
                $message = ecrire_htpasswd($comptes)
                    ? ['ok', "Compte « $nom » créé : il peut se connecter au site."]
                    : ['erreur', 'Écriture du .htpasswd impossible.'];
            }
        } elseif ($action === 'mdp') {
            $mdp = $_POST['mdp'] ?? '';
            if (!isset($comptes[$nom])) {
                $message = ['erreur', "Compte « $nom » introuvable."];
            } elseif ($e = mot_de_passe_valide($mdp)) {
                $message = ['erreur', "Mot de passe refusé : $e."];
            } else {
                $comptes[$nom] = password_hash($mdp, PASSWORD_BCRYPT);
                $message = ecrire_htpasswd($comptes)
                    ? ['ok', "Mot de passe de « $nom » remplacé."]
                    : ['erreur', 'Écriture du .htpasswd impossible.'];
            }
        } elseif ($action === 'supprimer') {
            if (!isset($comptes[$nom])) {
                $message = ['erreur', "Compte « $nom » introuvable."];
            } elseif (count($comptes) <= 1) {
                $message = ['erreur', 'Impossible : ce compte est le dernier — '
                    . 'le site deviendrait inaccessible.'];
            } else {
                unset($comptes[$nom]);
                $message = ecrire_htpasswd($comptes)
                    ? ['ok', "Compte « $nom » supprimé."]
                    : ['erreur', 'Écriture du .htpasswd impossible.'];
            }
        } elseif ($action === 'mdp_admin') {
            $mdp = $_POST['mdp'] ?? '';
            if ($e = mot_de_passe_valide($mdp)) {
                $message = ['erreur', "Mot de passe refusé : $e."];
            } else {
                $admins['admins'][$_SESSION['admin']] =
                    password_hash($mdp, PASSWORD_BCRYPT);
                $message = ecrire_json(FICHIER_ADMINS, $admins)
                    ? ['ok', 'Mot de passe administrateur remplacé.']
                    : ['erreur', 'Écriture impossible.'];
            }
        }
    }
}

$comptes_site = $connecte ? lire_htpasswd() : [];

function h(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES, 'UTF-8');
}
?>
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>MarketLab — administration</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         max-width: 660px; margin: 24px auto; padding: 0 16px;
         background: Canvas; color: CanvasText; }
  h1 { font-size: 20px; } h2 { font-size: 15px; margin: 22px 0 8px; }
  .carte { border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
           border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  label { display: block; font-size: 13px; margin: 8px 0 2px; opacity: .8; }
  input, button { font-size: 14px; padding: 7px 10px; border-radius: 6px;
                  border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  input { width: 100%; box-sizing: border-box; background: Field; color: FieldText; }
  button { cursor: pointer; background: #2a78d6; color: #fff; border: none;
           margin-top: 10px; }
  button.danger { background: #d03b3b; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  td, th { text-align: left; padding: 6px 4px;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, transparent); }
  .ok { color: #0a7a0a; } .erreur { color: #d03b3b; }
  .note { font-size: 12px; opacity: .65; }
  form.enligne { display: inline; }
</style>
</head>
<body>
<h1>🛠️ MarketLab — administration</h1>

<?php if ($message): ?>
  <p class="<?= $message[0] ?>"><?= h($message[1]) ?></p>
<?php endif; ?>

<?php if (!$installe): ?>
  <div class="carte">
    <h2>Première installation</h2>
    <p class="note">Saisir le jeton d'installation fourni au déploiement,
      puis choisir le mot de passe administrateur.</p>
    <form method="post">
      <input type="hidden" name="action" value="installer">
      <label>Jeton d'installation</label>
      <input name="jeton" required autocomplete="off">
      <label>Mot de passe administrateur (≥ 10 caractères, lettres et chiffres)</label>
      <input name="mdp" type="password" required minlength="10">
      <button>Initialiser</button>
    </form>
  </div>

<?php elseif (!$connecte): ?>
  <div class="carte">
    <h2>Connexion administrateur</h2>
    <form method="post">
      <input type="hidden" name="action" value="connexion">
      <label>Identifiant</label>
      <input name="nom" required autocomplete="username">
      <label>Mot de passe</label>
      <input name="mdp" type="password" required autocomplete="current-password">
      <button>Se connecter</button>
    </form>
    <p class="note">Verrouillage 15 min après <?= MAX_ECHECS ?> échecs.</p>
  </div>

<?php else: ?>
  <p>Connecté : <strong><?= h($_SESSION['admin']) ?></strong>
    <form class="enligne" method="post">
      <input type="hidden" name="action" value="deconnexion">
      <button class="danger">Se déconnecter</button>
    </form>
  </p>

  <div class="carte">
    <h2>Comptes d'accès au site (<?= count($comptes_site) ?>)</h2>
    <table>
      <tr><th>Identifiant</th><th>Hachage</th><th></th></tr>
      <?php foreach ($comptes_site as $nom => $hachage): ?>
      <tr>
        <td><strong><?= h($nom) ?></strong></td>
        <td class="note"><?= str_starts_with($hachage, '$2y$')
            ? 'bcrypt' : 'apr1 (ancien format)' ?></td>
        <td>
          <form class="enligne" method="post"
                onsubmit="return confirm('Supprimer « <?= h($nom) ?> » ?')">
            <input type="hidden" name="action" value="supprimer">
            <input type="hidden" name="nom" value="<?= h($nom) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="danger">Supprimer</button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <div class="carte">
    <h2>Créer un compte</h2>
    <form method="post">
      <input type="hidden" name="action" value="creer">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <label>Identifiant</label>
      <input name="nom" required pattern="[a-zA-Z0-9_.\-]{2,32}">
      <label>Mot de passe (≥ 10 caractères, lettres et chiffres)</label>
      <input name="mdp" type="password" required minlength="10">
      <button>Créer le compte</button>
    </form>
  </div>

  <div class="carte">
    <h2>Changer le mot de passe d'un compte</h2>
    <form method="post">
      <input type="hidden" name="action" value="mdp">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <label>Identifiant</label>
      <input name="nom" required>
      <label>Nouveau mot de passe</label>
      <input name="mdp" type="password" required minlength="10">
      <button>Remplacer</button>
    </form>
  </div>

  <div class="carte">
    <h2>Mot de passe administrateur</h2>
    <form method="post">
      <input type="hidden" name="action" value="mdp_admin">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <label>Nouveau mot de passe administrateur</label>
      <input name="mdp" type="password" required minlength="10">
      <button>Remplacer</button>
    </form>
    <p class="note">Distinct des comptes du site : il ne sert qu'à cette page.</p>
  </div>
<?php endif; ?>

<p class="note">Les comptes gèrent l'accès Basic Auth du site
  marketlab.gnlfconsult.com. Espace protégé : double authentification
  (compte site + compte administrateur), bcrypt, CSRF, anti-bruteforce.</p>
</body>
</html>
