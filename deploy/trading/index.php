<?php
/**
 * MarketLab — environnement de trading virtuel avec levier.
 *
 * Capital de départ 1 000 $ virtuels par compte. Ordres long/short avec
 * levier 1-20, stop et objectif optionnels. Exécution au DERNIER COURS
 * PUBLIÉ par le site (donnees/titres/*.json) : pas de flux temps réel, et
 * c'est assumé — l'environnement sert à juger des décisions à l'échelle de
 * jours, pas à scalper. Les stops/objectifs/liquidations sont appliqués par
 * le robot quotidien sur les extrêmes de séance.
 *
 * Un fichier JSON par compte (trading/comptes/<nom>.json) : le robot
 * n'écrit que le sien, cette page n'écrit que celui de l'utilisateur
 * connecté — pas de conflit d'écriture.
 */

declare(strict_types=1);

const DOSSIER_COMPTES = __DIR__ . '/comptes';
const DOSSIER_DONNEES = __DIR__ . '/../donnees';
const CAPITAL_DEPART = 1000.0;
const LEVIER_MAX = 20;
const SPREAD_PCT = 0.05;        // frais simulés par exécution (aller)
const MISE_MIN = 10.0;

session_set_cookie_params(['httponly' => true,
    'secure' => !empty($_SERVER['HTTPS']), 'samesite' => 'Strict']);
header('Cache-Control: no-store');
session_start();

// ---------------------------------------------------------------- utilitaires

function h(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }

function chemin_compte(string $nom): string {
    return DOSSIER_COMPTES . '/' . $nom . '.json';
}

function lire_compte(string $nom): ?array {
    $c = chemin_compte($nom);
    if (!is_file($c)) return null;
    $d = json_decode((string)file_get_contents($c), true);
    return is_array($d) ? $d : null;
}

function ecrire_compte(string $nom, array $d): bool {
    if (!is_dir(DOSSIER_COMPTES)) mkdir(DOSSIER_COMPTES, 0755, true);
    $tmp = chemin_compte($nom) . '.tmp';
    if (file_put_contents($tmp, json_encode($d, JSON_PRETTY_PRINT
        | JSON_UNESCAPED_UNICODE), LOCK_EX) === false) return false;
    return rename($tmp, chemin_compte($nom));
}

function actifs_disponibles(): array {
    $liste = [];
    foreach (glob(DOSSIER_DONNEES . '/titres/*.json') ?: [] as $f) {
        $liste[] = basename($f, '.json');
    }
    sort($liste);
    return $liste;
}

function dernier_cours(string $symbole): ?array {
    $f = DOSSIER_DONNEES . '/titres/' . $symbole . '.json';
    if (!is_file($f)) return null;
    $d = json_decode((string)file_get_contents($f), true);
    $prix = $d['signaux']['close'] ?? null;
    return $prix ? ['prix' => (float)$prix,
                    'nom' => $d['nom'] ?? $symbole] : null;
}

function date_donnees(): string {
    $meta = json_decode((string)@file_get_contents(
        DOSSIER_DONNEES . '/meta.json'), true);
    return $meta['genere_le'] ?? '?';
}

function jeton_csrf(): string {
    if (empty($_SESSION['csrf_t'])) $_SESSION['csrf_t'] = bin2hex(random_bytes(24));
    return $_SESSION['csrf_t'];
}

// ------------------------------------------------------------------- calculs

function pnl_position(array $p, float $prix): float {
    $sens = $p['sens'] === 'long' ? 1 : -1;
    return ($prix - $p['prix_entree']) * $p['quantite'] * $sens;
}

function equite(array $compte): float {
    $total = $compte['solde'];
    foreach ($compte['positions'] as $p) {
        $c = dernier_cours($p['symbole']);
        $total += $p['marge'] + ($c ? pnl_position($p, $c['prix']) : 0);
    }
    return $total;
}

// ------------------------------------------------------------------- actions

$connecte = $_SESSION['trader'] ?? null;
$message = null;
$action = $_POST['a'] ?? null;

if ($action === 'inscription') {
    $nom = strtolower(trim($_POST['nom'] ?? ''));
    $mdp = $_POST['mdp'] ?? '';
    if (!preg_match('/^[a-z0-9_.-]{2,24}$/', $nom)) {
        $message = ['erreur', 'Nom invalide (2-24 : minuscules, chiffres, . _ -).'];
    } elseif ($nom === 'claude') {
        $message = ['erreur', 'Ce compte est réservé au robot MarketLab.'];
    } elseif (lire_compte($nom)) {
        $message = ['erreur', 'Ce compte existe déjà.'];
    } elseif (strlen($mdp) < 8) {
        $message = ['erreur', 'Mot de passe : 8 caractères minimum.'];
    } else {
        ecrire_compte($nom, [
            'nom' => $nom, 'mdp' => password_hash($mdp, PASSWORD_BCRYPT),
            'capital_initial' => CAPITAL_DEPART, 'solde' => CAPITAL_DEPART,
            'positions' => [], 'historique' => [],
            'equity' => [[date('Y-m-d H:i'), CAPITAL_DEPART]],
            'cree_le' => date('Y-m-d H:i'),
        ]);
        $message = ['ok', "Compte « $nom » créé avec " . CAPITAL_DEPART
            . ' $ virtuels — connectez-vous.'];
    }
}

if ($action === 'connexion') {
    $nom = strtolower(trim($_POST['nom'] ?? ''));
    $c = lire_compte($nom);
    if ($c && !empty($c['mdp']) && password_verify($_POST['mdp'] ?? '', $c['mdp'])) {
        session_regenerate_id(true);
        $_SESSION['trader'] = $nom;
        $connecte = $nom;
        jeton_csrf();
    } else {
        $message = ['erreur', 'Identifiants incorrects.'];
        sleep(1); // freine la force brute
    }
}

if ($action === 'deconnexion') {
    session_destroy();
    header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?'));
    exit;
}

if ($connecte && in_array($action, ['ouvrir', 'fermer'], true)) {
    if (!hash_equals($_SESSION['csrf_t'] ?? '', $_POST['csrf'] ?? '§')) {
        $message = ['erreur', 'Session expirée — recharger la page.'];
    } else {
        $compte = lire_compte($connecte);

        if ($action === 'ouvrir') {
            $symbole = $_POST['symbole'] ?? '';
            $sens = $_POST['sens'] === 'short' ? 'short' : 'long';
            $mise = (float)($_POST['mise'] ?? 0);
            $levier = max(1, min(LEVIER_MAX, (int)($_POST['levier'] ?? 1)));
            $stop = (float)($_POST['stop'] ?? 0) ?: null;
            $objectif = (float)($_POST['objectif'] ?? 0) ?: null;
            $cours = dernier_cours($symbole);

            if (!$cours) {
                $message = ['erreur', 'Actif inconnu.'];
            } elseif ($mise < MISE_MIN || $mise > $compte['solde']) {
                $message = ['erreur', 'Mise invalide (min ' . MISE_MIN
                    . ' $, max solde disponible ' . round($compte['solde'], 2) . ' $).'];
            } elseif ($stop !== null && (($sens === 'long' && $stop >= $cours['prix'])
                       || ($sens === 'short' && $stop <= $cours['prix']))) {
                $message = ['erreur', 'Stop du mauvais côté du prix.'];
            } else {
                $prix = $cours['prix'] * (1 + ($sens === 'long' ? 1 : -1)
                        * SPREAD_PCT / 100);   // spread simulé défavorable
                $notionnel = $mise * $levier;
                $compte['solde'] -= $mise;
                $compte['positions'][] = [
                    'id' => substr(bin2hex(random_bytes(4)), 0, 8),
                    'symbole' => $symbole, 'sens' => $sens,
                    'marge' => $mise, 'levier' => $levier,
                    'notionnel' => round($notionnel, 2),
                    'quantite' => $notionnel / $prix,
                    'prix_entree' => $prix,
                    'stop' => $stop, 'objectif' => $objectif,
                    'ouvert_le' => date('Y-m-d H:i'),
                    'source' => 'manuel',
                ];
                $message = ecrire_compte($connecte, $compte)
                    ? ['ok', strtoupper($sens) . " $symbole ouvert : marge $mise $ "
                       . "× levier $levier = notionnel " . round($notionnel)
                       . " $ @ " . round($prix, 4)
                       . " (spread " . SPREAD_PCT . " % inclus)"]
                    : ['erreur', 'Écriture du compte impossible.'];
            }
        } elseif ($action === 'fermer') {
            $id = $_POST['id'] ?? '';
            foreach ($compte['positions'] as $i => $p) {
                if ($p['id'] !== $id) continue;
                $cours = dernier_cours($p['symbole']);
                if (!$cours) { $message = ['erreur', 'Cours indisponible.']; break; }
                $sens = $p['sens'] === 'long' ? 1 : -1;
                $prix = $cours['prix'] * (1 - $sens * SPREAD_PCT / 100);
                $pnl = pnl_position($p, $prix);
                $rendu = max(0.0, $p['marge'] + $pnl);   // liquidation à 0
                $compte['solde'] += $rendu;
                $compte['historique'][] = [
                    'symbole' => $p['symbole'], 'sens' => $p['sens'],
                    'marge' => $p['marge'], 'levier' => $p['levier'],
                    'entree' => $p['prix_entree'], 'sortie' => round($prix, 4),
                    'pnl' => round($pnl, 2),
                    'ouvert_le' => $p['ouvert_le'],
                    'ferme_le' => date('Y-m-d H:i'), 'motif' => 'manuel',
                ];
                unset($compte['positions'][$i]);
                $compte['positions'] = array_values($compte['positions']);
                $compte['equity'][] = [date('Y-m-d H:i'), round(equite($compte), 2)];
                $message = ecrire_compte($connecte, $compte)
                    ? ['ok', "Position {$p['symbole']} fermée : P&L "
                       . round($pnl, 2) . ' $.']
                    : ['erreur', 'Écriture impossible.'];
                break;
            }
        }
    }
}

$compte = $connecte ? lire_compte($connecte) : null;
$actifs = actifs_disponibles();
?>
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>MarketLab — trading virtuel</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         max-width: 860px; margin: 24px auto; padding: 0 16px;
         background: Canvas; color: CanvasText; }
  h1 { font-size: 20px; } h2 { font-size: 15px; margin: 20px 0 8px; }
  .carte { border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
           border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  label { display: block; font-size: 13px; margin: 8px 0 2px; opacity: .8; }
  input, select, button { font-size: 14px; padding: 7px 10px; border-radius: 6px;
    border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  input, select { background: Field; color: FieldText; }
  button { cursor: pointer; background: #2a78d6; color: #fff; border: none;
           margin-top: 10px; }
  button.danger { background: #d03b3b; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td, th { text-align: left; padding: 6px 4px; border-bottom:
           1px solid color-mix(in srgb, CanvasText 12%, transparent); }
  .ok { color: #0a7a0a; } .erreur { color: #d03b3b; }
  .note { font-size: 12px; opacity: .65; }
  .grille { display: grid; grid-template-columns: repeat(auto-fit,
            minmax(140px, 1fr)); gap: 10px; }
  .tuile .l { font-size: 12px; opacity: .65; }
  .tuile .v { font-size: 20px; font-weight: 600; }
  .pos { color: #0a7a0a; } .neg { color: #d03b3b; }
</style>
</head>
<body>
<h1>🏦 MarketLab — trading virtuel</h1>
<p class="note">Argent 100 % virtuel — 1 000 $ de départ, levier jusqu'à
  ×<?= LEVIER_MAX ?>. Exécution au dernier cours publié
  (<?= h(date_donnees()) ?>) avec spread simulé de <?= SPREAD_PCT ?> % :
  cet environnement juge des décisions de fond, pas du scalping. Le robot
  « claude » trade selon les verdicts de l'outil ; comparez-vous à lui sur la
  page Concours du site.</p>

<?php if ($message): ?>
  <p class="<?= $message[0] ?>"><?= h($message[1]) ?></p>
<?php endif; ?>

<?php if (!$connecte): ?>
  <div class="carte">
    <h2>Connexion</h2>
    <form method="post">
      <input type="hidden" name="a" value="connexion">
      <label>Compte</label><input name="nom" required>
      <label>Mot de passe</label>
      <input name="mdp" type="password" required>
      <button>Se connecter</button>
    </form>
  </div>
  <div class="carte">
    <h2>Créer un compte de trading (1 000 $ virtuels)</h2>
    <form method="post">
      <input type="hidden" name="a" value="inscription">
      <label>Nom de compte</label>
      <input name="nom" required pattern="[a-z0-9_.\-]{2,24}">
      <label>Mot de passe (≥ 8 caractères)</label>
      <input name="mdp" type="password" required minlength="8">
      <button>Créer mon compte</button>
    </form>
  </div>

<?php else: ?>
  <?php $eq = equite($compte);
        $perf = ($eq / $compte['capital_initial'] - 1) * 100; ?>
  <p>Compte : <strong><?= h($connecte) ?></strong>
    <form style="display:inline" method="post">
      <input type="hidden" name="a" value="deconnexion">
      <button class="danger">Déconnexion</button>
    </form>
  </p>
  <div class="carte grille">
    <div class="tuile"><div class="l">Équité</div>
      <div class="v"><?= number_format($eq, 2) ?> $</div></div>
    <div class="tuile"><div class="l">Performance</div>
      <div class="v <?= $perf >= 0 ? 'pos' : 'neg' ?>">
        <?= ($perf >= 0 ? '+' : '') . number_format($perf, 2) ?> %</div></div>
    <div class="tuile"><div class="l">Solde disponible</div>
      <div class="v"><?= number_format($compte['solde'], 2) ?> $</div></div>
    <div class="tuile"><div class="l">Positions ouvertes</div>
      <div class="v"><?= count($compte['positions']) ?></div></div>
  </div>

  <div class="carte">
    <h2>Positions ouvertes</h2>
    <?php if (!$compte['positions']): ?>
      <p class="note">Aucune position.</p>
    <?php else: ?>
    <table>
      <tr><th>Actif</th><th>Sens</th><th>Levier</th><th>Marge</th>
          <th>Entrée</th><th>Cours</th><th>P&L</th><th>Stop</th>
          <th>Objectif</th><th></th></tr>
      <?php foreach ($compte['positions'] as $p):
            $c = dernier_cours($p['symbole']);
            $pnl = $c ? pnl_position($p, $c['prix']) : null; ?>
      <tr>
        <td><strong><?= h($p['symbole']) ?></strong></td>
        <td><?= $p['sens'] === 'long' ? '🟢 long' : '🔴 short' ?></td>
        <td>×<?= (int)$p['levier'] ?></td>
        <td><?= number_format($p['marge'], 0) ?> $</td>
        <td><?= round($p['prix_entree'], 4) ?></td>
        <td><?= $c ? round($c['prix'], 4) : '—' ?></td>
        <td class="<?= $pnl >= 0 ? 'pos' : 'neg' ?>">
          <?= $pnl === null ? '—' : ($pnl >= 0 ? '+' : '')
              . number_format($pnl, 2) ?> $</td>
        <td><?= $p['stop'] ?? '—' ?></td>
        <td><?= $p['objectif'] ?? '—' ?></td>
        <td>
          <form method="post" onsubmit="return confirm('Fermer ?')">
            <input type="hidden" name="a" value="fermer">
            <input type="hidden" name="id" value="<?= h($p['id']) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="danger">Fermer</button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
    <?php endif; ?>
  </div>

  <div class="carte">
    <h2>Ouvrir une position</h2>
    <form method="post">
      <input type="hidden" name="a" value="ouvrir">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <div class="grille">
        <div><label>Actif</label>
          <select name="symbole">
            <?php foreach ($actifs as $s): ?>
              <option><?= h($s) ?></option>
            <?php endforeach; ?>
          </select></div>
        <div><label>Sens</label>
          <select name="sens"><option value="long">Long (hausse)</option>
            <option value="short">Short (baisse)</option></select></div>
        <div><label>Mise / marge ($)</label>
          <input name="mise" type="number" min="<?= MISE_MIN ?>" step="1"
                 value="50" required></div>
        <div><label>Levier (1-<?= LEVIER_MAX ?>)</label>
          <input name="levier" type="number" min="1" max="<?= LEVIER_MAX ?>"
                 value="3" required></div>
        <div><label>Stop (optionnel)</label>
          <input name="stop" type="number" step="any"></div>
        <div><label>Objectif (optionnel)</label>
          <input name="objectif" type="number" step="any"></div>
      </div>
      <button>Ouvrir la position</button>
      <p class="note">Perte maximale = la mise (liquidation automatique à
        marge épuisée, contrôlée chaque soir sur les extrêmes de séance —
        pas de solde négatif). Notionnel = mise × levier.</p>
    </form>
  </div>

  <?php if ($compte['historique']): ?>
  <div class="carte">
    <h2>Historique (<?= count($compte['historique']) ?> trades clos)</h2>
    <table>
      <tr><th>Actif</th><th>Sens</th><th>Levier</th><th>Entrée</th>
          <th>Sortie</th><th>P&L</th><th>Motif</th><th>Fermé le</th></tr>
      <?php foreach (array_reverse($compte['historique']) as $t): ?>
      <tr>
        <td><?= h($t['symbole']) ?></td>
        <td><?= $t['sens'] ?></td>
        <td>×<?= (int)$t['levier'] ?></td>
        <td><?= round($t['entree'], 4) ?></td>
        <td><?= round($t['sortie'], 4) ?></td>
        <td class="<?= $t['pnl'] >= 0 ? 'pos' : 'neg' ?>">
          <?= ($t['pnl'] >= 0 ? '+' : '') . number_format($t['pnl'], 2) ?> $</td>
        <td class="note"><?= h($t['motif']) ?></td>
        <td class="note"><?= h($t['ferme_le']) ?></td>
      </tr>
      <?php endforeach; ?>
    </table>
  </div>
  <?php endif; ?>
<?php endif; ?>
</body>
</html>
