<?php
/**
 * MarketLab — historique et statistiques d'un compte de trading virtuel.
 *
 * Le panneau d'administration donnait l'instantané : équité, solde, nombre de
 * positions et de trades. Le journal des trades fermés et la série d'équité
 * étaient pourtant déjà dans le fichier du compte, jamais montrés. Cette page
 * les expose.
 *
 * Elle ne CALCULE rien qui existe ailleurs : l'équité courante vient de
 * `ml_equite_compte()` (seule implémentation de la plateforme), la série
 * d'équité et le journal sont lus tels que le robot les a écrits.
 */

declare(strict_types=1);

session_start();
require_once __DIR__ . '/commun.php';
require_once __DIR__ . '/comptes_lib.php';

$connecte = $_SESSION['admin2'] ?? null;
if (!$connecte) { header('Location: ./'); exit; }

function h(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES, 'UTF-8');
}

$nom = (string)($_GET['nom'] ?? '');
// Le nom vient de l'URL : il ne doit jamais servir à composer un chemin sans
// contrôle, sinon « ../../ » ouvrirait n'importe quel fichier de l'hébergement.
if ($nom === '' || !preg_match('/^[a-z0-9_-]{1,32}$/i', $nom)
    || !is_file(ML_TRADING . "/$nom.json")) {
    http_response_code(404);
    $introuvable = true;
} else {
    $introuvable = false;
    $compte = ml_lire(ML_TRADING . "/$nom.json");
    $serie = ml_serie_equite($compte);
    $stats = ml_stats_trades($compte);
    $equite = ml_equite_compte($compte);
    $capital = (float)($compte['capital_initial'] ?? ML_CAPITAL_TRADING);
    $trades = array_reverse($compte['historique'] ?? []);   // le plus récent d'abord
}

function pc(?float $x, int $d = 1): string {
    return $x === null ? '—' : number_format($x, $d, ',', "\u{202F}") . ' %';
}
function mt(?float $x): string {
    return $x === null ? '—' : ml_montant($x) . ' $';
}
?>
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MarketLab — compte <?= h($nom) ?></title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0 auto; padding: 1.2rem; max-width: 60rem; }
  h1 { font-size: 1.3rem; margin: 0 0 .3rem; }
  h2 { font-size: 1rem; margin: 1.5rem 0 .5rem; }
  a { color: inherit; }
  .note { opacity: .7; font-size: .87rem; }
  .carte { border: 1px solid rgba(128,128,128,.35); border-radius: .5rem;
           padding: .8rem 1rem; margin: .8rem 0; }
  .rangee { display: flex; flex-wrap: wrap; gap: 1.4rem; margin: .6rem 0; }
  .tuile .v { font-size: 1.25rem; font-weight: 600; }
  .tuile .l { font-size: .78rem; opacity: .65; text-transform: uppercase;
              letter-spacing: .03em; }
  table { border-collapse: collapse; width: 100%; font-size: .9rem; }
  th, td { text-align: left; padding: .32rem .5rem;
           border-bottom: 1px solid rgba(128,128,128,.22); }
  th { font-weight: 600; opacity: .75; white-space: nowrap; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .gain { color: #2e7d43; } .perte { color: #c0392b; }
  .scroll { overflow-x: auto; }
  .badge { display: inline-block; padding: .05rem .5rem; border-radius: 1rem;
           font-size: .78rem; background: rgba(128,128,128,.16); }
</style>
</head>
<body>

<?php if ($introuvable): ?>
  <h1>Compte introuvable</h1>
  <p class="note"><a href="./">← Retour à l'administration</a></p>
<?php else: ?>

<h1>Compte <?= h($nom) ?><?= $nom === 'claude' ? ' (robot)' : '' ?></h1>
<p class="note"><a href="./">← Administration</a> ·
  <a href="./comparer.php">Comparer les comptes</a></p>

<div class="carte">
  <div class="rangee">
    <div class="tuile"><div class="l">Équité</div>
      <div class="v"><?= mt($equite) ?></div></div>
    <div class="tuile"><div class="l">Performance</div>
      <div class="v <?= $equite >= $capital ? 'gain' : 'perte' ?>">
        <?= pc(($equite / max($capital, 1e-9) - 1) * 100, 2) ?></div>
      <div class="note">depuis <?= ml_montant($capital, 0) ?> $</div></div>
    <div class="tuile"><div class="l">Trades fermés</div>
      <div class="v"><?= $stats['n'] ?></div></div>
    <div class="tuile"><div class="l">Positions ouvertes</div>
      <div class="v"><?= count($compte['positions'] ?? []) ?></div></div>
    <div class="tuile"><div class="l">Baisse maximale</div>
      <div class="v <?= ml_drawdown_max($serie) < -10 ? 'perte' : '' ?>">
        <?= pc(ml_drawdown_max($serie), 1) ?></div>
      <div class="note">depuis un sommet</div></div>
  </div>
</div>

<div class="carte">
  <h2 style="margin-top:0">Évolution de l'équité</h2>
  <?= ml_courbe_svg([['nom' => $nom, 'points' => $serie,
                      'couleur' => ml_couleur_compte($nom)]], 720, 200, false) ?>
  <p class="note">Un relevé par passage du robot, le soir. La courbe commence
    au premier passage suivant l'ouverture du compte — pas à sa création.</p>
  <div class="rangee">
    <?php foreach ([7 => '7 jours', 30 => '30 jours', 90 => '90 jours'] as $j => $lib):
      $p = ml_perf_fenetre($serie, $j); ?>
      <div class="tuile"><div class="l"><?= $lib ?></div>
        <div class="v <?= $p === null ? '' : ($p >= 0 ? 'gain' : 'perte') ?>">
          <?= $p === null ? '—' : pc($p, 2) ?></div>
        <?php if ($p === null): ?>
          <div class="note">série trop courte</div>
        <?php endif; ?>
      </div>
    <?php endforeach; ?>
  </div>
  <p class="note">Une fenêtre que la série ne couvre pas affiche « — » plutôt
    qu'une valeur calculée sur ce qui existe : comparer trois jours de relevés
    à une performance « 30 jours » flatterait ou punirait un compte selon sa
    seule ancienneté.</p>
</div>

<?php if ($stats['n'] > 0): ?>
<div class="carte">
  <h2 style="margin-top:0">Ce que dit le journal</h2>
  <div class="rangee">
    <div class="tuile"><div class="l">Réussite</div>
      <div class="v"><?= pc($stats['reussite']) ?></div>
      <div class="note"><?= $stats['gagnants'] ?> / <?= $stats['n'] ?> trades</div></div>
    <div class="tuile"><div class="l">Facteur de profit</div>
      <div class="v <?= $stats['facteur_profit'] === null ? ''
                       : ($stats['facteur_profit'] >= 1 ? 'gain' : 'perte') ?>">
        <?= $stats['facteur_profit'] === null ? '—'
            : number_format($stats['facteur_profit'], 2, ',', ' ') ?></div>
      <div class="note">gains ÷ pertes</div></div>
    <div class="tuile"><div class="l">Espérance / trade</div>
      <div class="v <?= ($stats['esperance'] ?? 0) >= 0 ? 'gain' : 'perte' ?>">
        <?= mt($stats['esperance']) ?></div></div>
    <div class="tuile"><div class="l">Gain moyen</div>
      <div class="v gain"><?= mt($stats['gain_moyen']) ?></div></div>
    <div class="tuile"><div class="l">Perte moyenne</div>
      <div class="v perte"><?= mt($stats['perte_moyenne']) ?></div></div>
    <div class="tuile"><div class="l">Durée moyenne</div>
      <div class="v"><?= $stats['duree_moyenne_h'] === null ? '—'
        : number_format($stats['duree_moyenne_h'] / 24, 1, ',', ' ') . ' j' ?></div></div>
  </div>
  <p class="note">Le facteur de profit passe avant le taux de réussite : on
    peut gagner sept fois sur dix et perdre de l'argent si les trois pertes
    sont trois fois plus grosses. En dessous de 1, la stratégie détruit du
    capital quel que soit son taux de réussite.</p>

  <h2>Comment les positions se ferment</h2>
  <table>
    <tr><th>Motif de sortie</th><th class="num">Trades</th>
        <th class="num">P&amp;L cumulé</th></tr>
    <?php foreach ($stats['par_motif'] as $motif => $m): ?>
      <tr><td><?= h((string)$motif) ?></td>
          <td class="num"><?= $m['n'] ?></td>
          <td class="num <?= $m['pnl'] >= 0 ? 'gain' : 'perte' ?>">
            <?= mt($m['pnl']) ?></td></tr>
    <?php endforeach; ?>
  </table>
  <p class="note">C'est souvent le tableau le plus instructif. Des sorties
    massivement sur stop ne mettent pas en cause le choix des titres mais le
    dimensionnement : un stop trop serré transforme du bruit ordinaire en
    perte réalisée.</p>

  <h2>Par actif</h2>
  <table>
    <tr><th>Actif</th><th class="num">Trades</th><th class="num">P&amp;L</th></tr>
    <?php foreach ($stats['par_actif'] as $sym => $a): ?>
      <tr><td><?= h((string)$sym) ?></td>
          <td class="num"><?= $a['n'] ?></td>
          <td class="num <?= $a['pnl'] >= 0 ? 'gain' : 'perte' ?>">
            <?= mt($a['pnl']) ?></td></tr>
    <?php endforeach; ?>
  </table>
</div>

<div class="carte">
  <h2 style="margin-top:0">Historique des trades (<?= count($trades) ?>)</h2>
  <div class="scroll">
  <table>
    <tr><th>Fermé le</th><th>Actif</th><th>Sens</th><th class="num">Levier</th>
        <th class="num">Mise</th><th class="num">Entrée</th>
        <th class="num">Sortie</th><th class="num">P&amp;L</th>
        <th class="num">Durée</th><th>Motif</th></tr>
    <?php foreach ($trades as $t):
      $pnl = (float)($t['pnl'] ?? 0);
      $marge = (float)($t['marge'] ?? 0);
      $o = strtotime((string)($t['ouvert_le'] ?? ''));
      $f = strtotime((string)($t['ferme_le'] ?? ''));
      $duree = ($o && $f && $f >= $o) ? ($f - $o) / 86400 : null; ?>
      <tr>
        <td class="note"><?= h((string)($t['ferme_le'] ?? '—')) ?></td>
        <td><strong><?= h((string)($t['symbole'] ?? '?')) ?></strong></td>
        <td><?= h((string)($t['sens'] ?? '—')) ?></td>
        <td class="num">×<?= h((string)($t['levier'] ?? '—')) ?></td>
        <td class="num"><?= ml_montant($marge) ?></td>
        <td class="num"><?= h((string)($t['entree'] ?? '—')) ?></td>
        <td class="num"><?= h((string)($t['sortie'] ?? '—')) ?></td>
        <td class="num <?= $pnl >= 0 ? 'gain' : 'perte' ?>">
          <strong><?= sprintf('%+.2f', $pnl) ?></strong>
          <?php if ($marge > 0): ?>
            <span class="note">(<?= sprintf('%+.1f', $pnl / $marge * 100) ?> %)</span>
          <?php endif; ?>
        </td>
        <td class="num"><?= $duree === null ? '—'
          : number_format($duree, 1, ',', ' ') . ' j' ?></td>
        <td><span class="badge"><?= h((string)($t['motif'] ?? '—')) ?></span></td>
      </tr>
    <?php endforeach; ?>
  </table>
  </div>
  <p class="note">Le pourcentage à côté du P&amp;L rapporte le gain à la MISE
    engagée, pas au capital : c'est ce qui rend deux trades de tailles
    différentes comparables.</p>
</div>
<?php else: ?>
<div class="carte">
  <p class="note">Aucun trade fermé pour l'instant — le journal se remplit à la
    première position clôturée.</p>
</div>
<?php endif; ?>

<?php if ($compte['positions'] ?? []): ?>
<div class="carte">
  <h2 style="margin-top:0">Positions ouvertes (<?= count($compte['positions']) ?>)</h2>
  <div class="scroll">
  <table>
    <tr><th>Actif</th><th>Sens</th><th class="num">Levier</th>
        <th class="num">Mise</th><th class="num">Entrée</th>
        <th class="num">Stop</th><th class="num">Objectif</th><th>Ouvert le</th></tr>
    <?php foreach ($compte['positions'] as $p): ?>
      <tr>
        <td><strong><?= h((string)($p['symbole'] ?? '?')) ?></strong></td>
        <td><?= h((string)($p['sens'] ?? '—')) ?></td>
        <td class="num">×<?= h((string)($p['levier'] ?? '—')) ?></td>
        <td class="num"><?= ml_montant((float)($p['marge'] ?? 0)) ?></td>
        <td class="num"><?= h((string)($p['prix_entree'] ?? '—')) ?></td>
        <td class="num"><?= h((string)($p['stop'] ?? '—')) ?></td>
        <td class="num"><?= h((string)($p['objectif'] ?? '—')) ?></td>
        <td class="note"><?= h((string)($p['ouvert_le'] ?? '—')) ?></td>
      </tr>
    <?php endforeach; ?>
  </table>
  </div>
</div>
<?php endif; ?>

<?php endif; ?>
</body>
</html>
