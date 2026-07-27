<?php
/**
 * MarketLab — administration v2.
 *
 * - Création de comptes par INVITATION E-MAIL : l'utilisateur définit
 *   lui-même son mot de passe via un lien signé (72 h, usage unique) —
 *   l'administrateur ne connaît jamais les mots de passe.
 * - Rôles par utilisateur : site (accès au tableau de bord), trading
 *   (espace de trading virtuel), admin (ce panneau).
 * - Gestion des comptes de trading : remise à zéro (1 000 $), suppression.
 * - Journal d'audit de toutes les actions.
 *
 * Connexion : identifiant + mot de passe du SITE (htpasswd, bcrypt ou apr1)
 * avec rôle « admin » requis ; repli sur l'ancien compte administrateur
 * (admin_comptes.json) le temps de la transition.
 */

declare(strict_types=1);
require __DIR__ . '/commun.php';

const FICHIER_ADMINS_LEGACY = __DIR__ . '/admin_comptes.json';
const FICHIER_ESSAIS = __DIR__ . '/tentatives.json';
const MAX_ECHECS = 8;
const FENETRE_ECHECS = 900;
const ROLES_DISPONIBLES = ['site', 'trading', 'admin'];

session_set_cookie_params(['httponly' => true,
    'secure' => !empty($_SERVER['HTTPS']), 'samesite' => 'Strict']);
header('Cache-Control: no-store');
session_start();

function h(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }

function verrouille(): int {
    $e = ml_lire(FICHIER_ESSAIS)[$_SERVER['REMOTE_ADDR'] ?? '?'] ?? [];
    $r = array_filter($e, fn($t) => $t > time() - FENETRE_ECHECS);
    return count($r) >= MAX_ECHECS ? max($r) + FENETRE_ECHECS - time() : 0;
}

function noter_echec(): void {
    $tous = ml_lire(FICHIER_ESSAIS);
    $ip = $_SERVER['REMOTE_ADDR'] ?? '?';
    $tous[$ip] = array_values(array_filter($tous[$ip] ?? [],
        fn($t) => $t > time() - FENETRE_ECHECS));
    $tous[$ip][] = time();
    ml_ecrire(FICHIER_ESSAIS, $tous);
}

function jeton_csrf(): string {
    if (empty($_SESSION['csrf'])) $_SESSION['csrf'] = bin2hex(random_bytes(24));
    return $_SESSION['csrf'];
}

// ------------------------------------------------------------------- trading

function trading_comptes(): array {
    $liste = [];
    foreach (glob(ML_TRADING . '/*.json') ?: [] as $f) {
        $c = ml_lire($f);
        if ($c) $liste[basename($f, '.json')] = $c;
    }
    ksort($liste);
    return $liste;
}

function trading_raz(string $nom): bool {
    $f = ML_TRADING . "/$nom.json";
    $c = ml_lire($f);
    if (!$c) return false;
    $c['solde'] = ML_CAPITAL_TRADING;
    $c['capital_initial'] = ML_CAPITAL_TRADING;
    $c['positions'] = [];
    $c['ordres'] = [];
    $c['historique'] = [];
    $c['equity'] = [[date('Y-m-d H:i'), ML_CAPITAL_TRADING]];
    if ($nom === 'claude') $c['journal_robot'] = ['compte remis à zéro'];
    return ml_ecrire($f, $c);
}

// ------------------------------------------------------------------- actions

$connecte = $_SESSION['admin2'] ?? null;
$message = null;
$lien_secours = null;
$action = $_POST['action'] ?? null;
$attente = verrouille();

if ($action && !$connecte && $attente > 0) {
    $message = ['erreur', "Trop d'échecs : réessayer dans "
        . ceil($attente / 60) . ' min.'];
    $action = null;
}

if ($action === 'connexion' && !$connecte) {
    $nom = trim($_POST['nom'] ?? '');
    $mdp = $_POST['mdp'] ?? '';
    $ok = false;
    $hachage = ml_htpasswd_lire()[$nom] ?? null;
    if ($hachage && in_array('admin', ml_roles($nom), true)
        && ml_verifier_mdp($mdp, $hachage)) {
        $ok = true;
    } else {   // repli : ancien compte administrateur dédié
        $legacy = ml_lire(FICHIER_ADMINS_LEGACY)['admins'][$nom] ?? null;
        if ($legacy && password_verify($mdp, $legacy)) $ok = true;
    }
    if ($ok) {
        session_regenerate_id(true);
        $_SESSION['admin2'] = $nom;
        $connecte = $nom;
        jeton_csrf();
        ml_audit($nom, 'connexion au panneau');
    } else {
        noter_echec();
        $message = ['erreur', 'Identifiants incorrects ou rôle admin absent.'];
    }
}

if ($action === 'deconnexion' && $connecte) {
    session_destroy();
    header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?'));
    exit;
}

if ($connecte && $action && $action !== 'connexion') {
    if (!hash_equals($_SESSION['csrf'] ?? '', $_POST['csrf'] ?? '§')) {
        $message = ['erreur', 'Session expirée — recharger la page.'];
    } else {
        $nom = trim($_POST['nom'] ?? '');
        $email = trim($_POST['email'] ?? '');
        $roles = array_values(array_intersect(
            (array)($_POST['roles'] ?? []), ROLES_DISPONIBLES));

        switch ($action) {
        case 'inviter':
            if (!preg_match('/^[a-z0-9_.-]{2,24}$/', $nom)) {
                $message = ['erreur', 'Identifiant invalide (2-24 : '
                    . 'minuscules, chiffres, . _ -).'];
            } elseif (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
                $message = ['erreur', 'Adresse e-mail invalide.'];
            } elseif (isset(ml_htpasswd_lire()[$nom])) {
                $message = ['erreur', "« $nom » existe déjà — utiliser "
                    . '« réinitialiser » pour changer son mot de passe.'];
            } elseif ($nom === 'claude') {
                $message = ['erreur', 'Identifiant réservé au robot.'];
            } else {
                if (!$roles) $roles = ['site'];
                $jeton = ml_creer_invitation($nom, $email, $roles, 'creation');
                $envoye = ml_envoyer_invitation($email, $nom, $jeton, 'creation');
                $lien_secours = ml_lien_invitation($jeton);
                ml_audit($connecte, "invitation créée pour $nom <$email> "
                    . '(' . implode('+', $roles) . ')'
                    . ($envoye ? '' : ' — ENVOI E-MAIL EN ÉCHEC'));
                $message = $envoye
                    ? ['ok', "Invitation envoyée à $email — $nom définira "
                       . 'son mot de passe lui-même (lien valable 72 h).']
                    : ['erreur', "L'e-mail n'a pas pu partir : transmettre "
                       . 'le lien de secours ci-dessous manuellement.'];
            }
            break;

        case 'reinitialiser':
            $comptes = ml_htpasswd_lire();
            $infos = ml_lire(ML_ROLES)[$nom] ?? [];
            $dest = $infos['email'] ?? '';
            if (!isset($comptes[$nom])) {
                $message = ['erreur', "Compte « $nom » introuvable."];
            } elseif (!$dest) {
                $message = ['erreur', "Aucune adresse e-mail connue pour "
                    . "« $nom » — l'inviter à nouveau avec son adresse."];
            } else {
                $jeton = ml_creer_invitation($nom, $dest,
                    $infos['roles'] ?? ['site'], 'reinitialisation');
                $envoye = ml_envoyer_invitation($dest, $nom, $jeton,
                                                'reinitialisation');
                $lien_secours = ml_lien_invitation($jeton);
                ml_audit($connecte, "réinitialisation demandée pour $nom");
                $message = $envoye
                    ? ['ok', "Lien de réinitialisation envoyé à $dest."]
                    : ['erreur', 'E-mail en échec : lien de secours ci-dessous.'];
            }
            break;

        case 'roles':
            if (!isset(ml_htpasswd_lire()[$nom])) {
                $message = ['erreur', "Compte « $nom » introuvable."];
            } elseif ($nom === $connecte && !in_array('admin', $roles, true)) {
                $message = ['erreur', 'Impossible de retirer son propre '
                    . 'rôle admin.'];
            } else {
                ml_definir_roles($nom, $roles ?: ['site']);
                ml_audit($connecte, "rôles de $nom : "
                    . implode('+', $roles ?: ['site']));
                $message = ['ok', "Rôles de « $nom » mis à jour."];
            }
            break;

        case 'supprimer':
            $comptes = ml_htpasswd_lire();
            if (!isset($comptes[$nom])) {
                $message = ['erreur', "Compte « $nom » introuvable."];
            } elseif ($nom === $connecte) {
                $message = ['erreur', 'Impossible de supprimer son propre '
                    . 'compte.'];
            } elseif (count($comptes) <= 1) {
                $message = ['erreur', 'Dernier compte : suppression refusée.'];
            } else {
                unset($comptes[$nom]);
                ml_htpasswd_ecrire($comptes);
                $tous = ml_lire(ML_ROLES);
                unset($tous[$nom]);
                ml_ecrire(ML_ROLES, $tous);
                ml_audit($connecte, "compte $nom supprimé");
                $message = ['ok', "Compte « $nom » supprimé (son éventuel "
                    . 'compte de trading est conservé).'];
            }
            break;

        case 'trading_raz':
            if ($nom && trading_raz($nom)) {
                ml_audit($connecte, "compte de trading $nom remis à "
                    . ML_CAPITAL_TRADING . ' $');
                $message = ['ok', "Compte de trading « $nom » remis à "
                    . ML_CAPITAL_TRADING . ' $ (positions et historique effacés).'];
            } else {
                $message = ['erreur', 'Remise à zéro impossible.'];
            }
            break;

        case 'trading_supprimer':
            if ($nom !== 'claude' && $nom
                && @unlink(ML_TRADING . "/$nom.json")) {
                ml_audit($connecte, "compte de trading $nom supprimé");
                $message = ['ok', "Compte de trading « $nom » supprimé."];
            } else {
                $message = ['erreur', $nom === 'claude'
                    ? 'Le compte du robot se remet à zéro mais ne se '
                      . 'supprime pas.' : 'Suppression impossible.'];
            }
            break;

        case 'annuler_invitation':
            $inv = ml_lire(ML_INVITATIONS);
            foreach ($inv as $k => $v) {
                if (($v['nom'] ?? '') === $nom) unset($inv[$k]);
            }
            ml_ecrire(ML_INVITATIONS, $inv);
            ml_audit($connecte, "invitation de $nom annulée");
            $message = ['ok', "Invitation de « $nom » annulée."];
            break;
        }
    }
}

$comptes_site = $connecte ? ml_htpasswd_lire() : [];
$roles_tous = $connecte ? ml_lire(ML_ROLES) : [];
$invitations = $connecte ? ml_lire(ML_INVITATIONS) : [];
$comptes_trading = $connecte ? trading_comptes() : [];
$audit = ($connecte && is_file(ML_AUDIT))
    ? array_slice(file(ML_AUDIT, FILE_IGNORE_NEW_LINES), -25) : [];
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
         max-width: 780px; margin: 24px auto; padding: 0 16px;
         background: Canvas; color: CanvasText; }
  h1 { font-size: 20px; } h2 { font-size: 15px; margin: 20px 0 8px; }
  .carte { border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
           border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  label { display: block; font-size: 13px; margin: 8px 0 2px; opacity: .8; }
  input:not([type=checkbox]), select, button { font-size: 14px;
    padding: 7px 10px; border-radius: 6px;
    border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  input:not([type=checkbox]) { width: 100%; box-sizing: border-box;
    background: Field; color: FieldText; }
  button { cursor: pointer; background: #2a78d6; color: #fff; border: none;
           margin-top: 8px; }
  button.danger { background: #d03b3b; }
  button.sobre { background: transparent; color: inherit;
    border: 1px solid color-mix(in srgb, CanvasText 30%, transparent); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td, th { text-align: left; padding: 6px 4px; border-bottom:
    1px solid color-mix(in srgb, CanvasText 12%, transparent);
    vertical-align: top; }
  .ok { color: #0a7a0a; } .erreur { color: #d03b3b; }
  .note { font-size: 12px; opacity: .65; }
  .lien { word-break: break-all; font-size: 12px; background:
    color-mix(in srgb, CanvasText 6%, transparent); padding: 8px;
    border-radius: 6px; }
  form.enligne { display: inline; }
</style>
</head>
<body>
<h1>🛠️ MarketLab — administration</h1>

<?php if ($message): ?>
  <p class="<?= $message[0] ?>"><?= h($message[1]) ?></p>
<?php endif; ?>
<?php if ($lien_secours): ?>
  <div class="carte">
    <strong>Lien d'invitation (secours)</strong>
    <p class="note">À transmettre par un canal sûr si l'e-mail n'arrive pas.
      Valable 72 h, usage unique.</p>
    <div class="lien"><?= h($lien_secours) ?></div>
  </div>
<?php endif; ?>

<?php if (!$connecte): ?>
  <div class="carte">
    <h2>Connexion administrateur</h2>
    <form method="post">
      <input type="hidden" name="action" value="connexion">
      <label>Identifiant</label><input name="nom" required>
      <label>Mot de passe</label>
      <input name="mdp" type="password" required>
      <button>Se connecter</button>
    </form>
    <p class="note">Compte du site avec rôle « admin » (ou ancien compte
      administrateur). Verrouillage 15 min après <?= MAX_ECHECS ?> échecs.</p>
  </div>

<?php else: ?>
  <p>Connecté : <strong><?= h($connecte) ?></strong>
    <form class="enligne" method="post">
      <input type="hidden" name="action" value="deconnexion">
      <button class="danger">Déconnexion</button>
    </form>
  </p>

  <div class="carte">
    <h2>✉️ Inviter un utilisateur</h2>
    <p class="note">L'invité reçoit un lien par e-mail et définit SON mot de
      passe : personne d'autre ne le connaît. Le rôle « trading » crée aussi
      son compte virtuel de 1 000 $.</p>
    <form method="post">
      <input type="hidden" name="action" value="inviter">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <label>Identifiant</label>
      <input name="nom" required pattern="[a-z0-9_.\-]{2,24}">
      <label>Adresse e-mail</label>
      <input name="email" type="email" required>
      <label>Rôles</label>
      <label><input type="checkbox" name="roles[]" value="site" checked>
        site — tableau de bord</label>
      <label><input type="checkbox" name="roles[]" value="trading">
        trading — compte virtuel 1 000 $</label>
      <label><input type="checkbox" name="roles[]" value="admin">
        admin — ce panneau</label>
      <button>Envoyer l'invitation</button>
    </form>
  </div>

  <?php if ($invitations): ?>
  <div class="carte">
    <h2>Invitations en attente (<?= count($invitations) ?>)</h2>
    <table>
      <tr><th>Identifiant</th><th>E-mail</th><th>Rôles</th><th>Expire</th>
          <th></th></tr>
      <?php foreach ($invitations as $v): ?>
      <tr>
        <td><?= h($v['nom']) ?></td>
        <td class="note"><?= h($v['email']) ?></td>
        <td class="note"><?= h(implode('+', $v['roles'])) ?></td>
        <td class="note"><?= date('d/m H:i', $v['expire']) ?></td>
        <td>
          <form class="enligne" method="post">
            <input type="hidden" name="action" value="annuler_invitation">
            <input type="hidden" name="nom" value="<?= h($v['nom']) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="sobre">Annuler</button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
  </div>
  <?php endif; ?>

  <div class="carte">
    <h2>👥 Utilisateurs (<?= count($comptes_site) ?>)</h2>
    <table>
      <tr><th>Identifiant</th><th>E-mail</th><th>Rôles</th><th>Actions</th></tr>
      <?php foreach ($comptes_site as $n => $hachage):
            $infos = $roles_tous[$n] ?? []; ?>
      <tr>
        <td><strong><?= h($n) ?></strong><br>
          <span class="note"><?= str_starts_with($hachage, '$2y$')
            ? 'bcrypt' : 'apr1' ?></span></td>
        <td class="note"><?= h($infos['email'] ?? '—') ?></td>
        <td>
          <form method="post">
            <input type="hidden" name="action" value="roles">
            <input type="hidden" name="nom" value="<?= h($n) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <?php foreach (ROLES_DISPONIBLES as $r): ?>
              <label style="display:inline"><input type="checkbox"
                name="roles[]" value="<?= $r ?>"
                <?= in_array($r, $infos['roles'] ?? ['site'], true)
                    ? 'checked' : '' ?>> <?= $r ?></label>
            <?php endforeach; ?>
            <button class="sobre">OK</button>
          </form>
        </td>
        <td>
          <form class="enligne" method="post">
            <input type="hidden" name="action" value="reinitialiser">
            <input type="hidden" name="nom" value="<?= h($n) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="sobre">Réinit. mdp</button>
          </form>
          <form class="enligne" method="post"
                onsubmit="return confirm('Supprimer <?= h($n) ?> ?')">
            <input type="hidden" name="action" value="supprimer">
            <input type="hidden" name="nom" value="<?= h($n) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="danger">Supprimer</button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <div class="carte">
    <h2>🏦 Comptes de trading virtuel (<?= count($comptes_trading) ?>)</h2>
    <p class="note">Équité = cash + marges engagées + P&amp;L latent (la mesure
      du concours). Solde dispo = cash utilisable pour de nouveaux ordres.</p>
    <table>
      <tr><th>Compte</th><th>Équité</th><th>Solde dispo</th><th>Positions</th>
          <th>Trades</th><th>Actions</th></tr>
      <?php foreach ($comptes_trading as $n => $c): ?>
      <tr>
        <td><?= $n === 'claude' ? '🤖 ' : '👤 ' ?><strong><?= h($n) ?></strong></td>
        <td><strong><?= ml_montant(ml_equite_trading($c)) ?> $</strong></td>
        <td class="note"><?= ml_montant((float)($c['solde'] ?? 0)) ?> $</td>
        <td><?= count($c['positions'] ?? []) ?></td>
        <td><?= count($c['historique'] ?? []) ?></td>
        <td>
          <form class="enligne" method="post"
                onsubmit="return confirm('Remettre <?= h($n) ?> à <?= ML_CAPITAL_TRADING ?> $ ? Positions et historique seront effacés.')">
            <input type="hidden" name="action" value="trading_raz">
            <input type="hidden" name="nom" value="<?= h($n) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="sobre">Remise à zéro</button>
          </form>
          <?php if ($n !== 'claude'): ?>
          <form class="enligne" method="post"
                onsubmit="return confirm('Supprimer le compte de trading <?= h($n) ?> ?')">
            <input type="hidden" name="action" value="trading_supprimer">
            <input type="hidden" name="nom" value="<?= h($n) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="danger">Supprimer</button>
          </form>
          <?php endif; ?>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <?php if ($audit): ?>
  <div class="carte">
    <h2>📜 Journal d'audit (25 dernières actions)</h2>
    <?php foreach (array_reverse($audit) as $l): ?>
      <p class="note" style="margin:3px 0"><?= h($l) ?></p>
    <?php endforeach; ?>
  </div>
  <?php endif; ?>
<?php endif; ?>

<p class="note">Invitations signées à usage unique (72 h) · mots de passe
  définis par les utilisateurs eux-mêmes (bcrypt) · rôles site/trading/admin ·
  toutes les actions sont auditées.</p>
</body>
</html>
