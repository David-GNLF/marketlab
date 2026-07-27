<?php
/**
 * MarketLab — page PUBLIQUE de définition de mot de passe.
 *
 * Seule page hors Basic Auth : un invité n'a pas encore de compte pour
 * franchir la porte. Elle n'accepte QUE les jetons d'invitation signés,
 * à usage unique, expirant sous 72 h. Le mot de passe est défini par
 * l'utilisateur seul — l'administrateur ne le connaît jamais.
 */

declare(strict_types=1);
require __DIR__ . '/../admin/commun.php';

function h(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }

$jeton = $_GET['j'] ?? $_POST['j'] ?? '';
$invitation = $jeton ? ml_valider_invitation($jeton) : null;
$message = null;
$termine = false;

if ($_SERVER['REQUEST_METHOD'] === 'POST' && $invitation) {
    $mdp = $_POST['mdp'] ?? '';
    $mdp2 = $_POST['mdp2'] ?? '';
    if (strlen($mdp) < 10 || !preg_match('/[A-Za-z]/', $mdp)
        || !preg_match('/[0-9]/', $mdp)) {
        $message = ['erreur', 'Mot de passe : au moins 10 caractères, '
            . 'avec lettres et chiffres.'];
    } elseif ($mdp !== $mdp2) {
        $message = ['erreur', 'Les deux saisies ne correspondent pas.'];
    } else {
        $nom = $invitation['nom'];
        $comptes = ml_htpasswd_lire();
        $comptes[$nom] = password_hash($mdp, PASSWORD_BCRYPT);
        if (!ml_htpasswd_ecrire($comptes)) {
            $message = ['erreur', 'Écriture impossible — réessayer.'];
        } else {
            ml_definir_roles($nom, $invitation['roles'], $invitation['email']);
            // rôle trading : le compte virtuel est créé s'il n'existe pas
            if (in_array('trading', $invitation['roles'], true)
                && !is_file(ML_TRADING . "/$nom.json")) {
                if (!is_dir(ML_TRADING)) mkdir(ML_TRADING, 0755, true);
                ml_ecrire(ML_TRADING . "/$nom.json", [
                    'nom' => $nom,
                    'mdp' => password_hash($mdp, PASSWORD_BCRYPT),
                    'capital_initial' => ML_CAPITAL_TRADING,
                    'solde' => ML_CAPITAL_TRADING,
                    'positions' => [], 'historique' => [],
                    'equity' => [[date('Y-m-d H:i'), ML_CAPITAL_TRADING]],
                    'cree_le' => date('Y-m-d H:i')]);
            }
            ml_consommer_invitation($invitation['cle']);
            ml_audit($nom, $invitation['type'] === 'creation'
                ? 'compte activé par invitation e-mail'
                : 'mot de passe réinitialisé par e-mail');
            $termine = true;
        }
    }
}
?>
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>MarketLab — définir votre mot de passe</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         max-width: 460px; margin: 48px auto; padding: 0 16px;
         background: Canvas; color: CanvasText; }
  .carte { border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
           border-radius: 8px; padding: 18px; }
  label { display: block; font-size: 13px; margin: 10px 0 3px; opacity: .8; }
  input, button { font-size: 15px; padding: 9px 12px; border-radius: 6px;
    border: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
    width: 100%; box-sizing: border-box; }
  input { background: Field; color: FieldText; }
  button { cursor: pointer; background: #2a78d6; color: #fff; border: none;
           margin-top: 14px; }
  .ok { color: #0a7a0a; } .erreur { color: #d03b3b; }
  .note { font-size: 12px; opacity: .65; }
</style>
</head>
<body>
<h1>📈 MarketLab</h1>

<?php if ($termine): ?>
  <div class="carte">
    <p class="ok"><strong>Votre mot de passe est défini.</strong></p>
    <p>Identifiant : <strong><?= h($invitation['nom']) ?></strong></p>
    <p>Vous pouvez maintenant ouvrir
      <a href="<?= ML_URL ?>/">le tableau de bord</a>
      <?php if (in_array('trading', $invitation['roles'], true)): ?>
        ou <a href="<?= ML_URL ?>/trading/">l'espace de trading virtuel</a>
        (mêmes identifiants)
      <?php endif; ?>.
    </p>
    <p class="note">Astuce : le navigateur demandera l'identifiant et le mot
      de passe à la première visite (authentification du site).</p>
  </div>

<?php elseif (!$invitation): ?>
  <div class="carte">
    <p class="erreur">Lien d'invitation invalide ou expiré.</p>
    <p class="note">Les liens expirent après 72 heures. Demandez à
      l'administrateur de renvoyer une invitation.</p>
  </div>

<?php else: ?>
  <div class="carte">
    <p>Bonjour <strong><?= h($invitation['nom']) ?></strong> — définissez le
      mot de passe de votre accès MarketLab. Vous seul le connaîtrez.</p>
    <?php if ($message): ?>
      <p class="<?= $message[0] ?>"><?= h($message[1]) ?></p>
    <?php endif; ?>
    <form method="post">
      <input type="hidden" name="j" value="<?= h($jeton) ?>">
      <label>Mot de passe (≥ 10 caractères, lettres et chiffres)</label>
      <input name="mdp" type="password" required minlength="10"
             autocomplete="new-password">
      <label>Confirmez</label>
      <input name="mdp2" type="password" required minlength="10"
             autocomplete="new-password">
      <button>Définir mon mot de passe</button>
    </form>
    <p class="note">Accès accordés :
      <?= h(implode(', ', $invitation['roles'])) ?>. Lien à usage unique.</p>
  </div>
<?php endif; ?>
</body>
</html>
