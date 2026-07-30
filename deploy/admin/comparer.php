<?php
/**
 * MarketLab — comparaison des comptes de trading virtuel.
 *
 * Le concours classe les comptes sur la performance DEPUIS L'OUVERTURE, ce qui
 * avantage mécaniquement le plus ancien en marché porteur. Cette page compare
 * sur des fenêtres glissantes : un compte ouvert la semaine dernière et un
 * compte ouvert il y a deux mois deviennent comparables sur les sept derniers
 * jours, et seulement là-dessus.
 *
 * Une fenêtre que la série d'un compte ne couvre pas affiche « — ». Remplir la
 * case avec ce qu'on a serait pire que la laisser vide : le chiffre aurait
 * l'air d'une performance à 30 jours alors qu'il en mesurerait trois.
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

const FENETRES = [7 => '7 jours', 30 => '30 jours', 90 => '90 jours'];

$comptes = [];
foreach (glob(ML_TRADING . '/*.json') ?: [] as $f) {
    $c = ml_lire($f);
    $nom = (string)($c['nom'] ?? basename($f, '.json'));
    if ($nom === '') continue;
    $serie = ml_serie_equite($c);
    $capital = (float)($c['capital_initial'] ?? ML_CAPITAL_TRADING);
    $equite = ml_equite_compte($c);
    $comptes[$nom] = [
        'nom' => $nom, 'serie' => $serie, 'equite' => $equite,
        'capital' => $capital,
        'depuis_ouverture' => ($equite / max($capital, 1e-9) - 1) * 100,
        'drawdown' => ml_drawdown_max($serie),
        'stats' => ml_stats_trades($c),
        'releves' => count($serie),
        'couleur' => ml_couleur_compte($nom),
    ];
}
uasort($comptes, fn($a, $b) => $b['depuis_ouverture'] <=> $a['depuis_ouverture']);

$series_svg = [];
foreach ($comptes as $c) {
    if (count($c['serie']) >= 2) {
        $series_svg[] = ['nom' => $c['nom'], 'points' => $c['serie'],
                         'couleur' => $c['couleur']];
    }
}

function pc(?float $x, int $d = 2): string {
    return $x === null ? '—' : number_format($x, $d, ',', "\u{202F}") . ' %';
}
?>
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MarketLab — comparaison des comptes</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0 auto; padding: 1.2rem; max-width: 62rem; }
  h1 { font-size: 1.3rem; margin: 0 0 .3rem; }
  h2 { font-size: 1rem; margin: 1.5rem 0 .5rem; }
  a { color: inherit; }
  .note { opacity: .7; font-size: .87rem; }
  .carte { border: 1px solid rgba(128,128,128,.35); border-radius: .5rem;
           padding: .8rem 1rem; margin: .8rem 0; }
  table { border-collapse: collapse; width: 100%; font-size: .9rem; }
  th, td { text-align: left; padding: .34rem .5rem;
           border-bottom: 1px solid rgba(128,128,128,.22); }
  th { font-weight: 600; opacity: .75; white-space: nowrap; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .gain { color: #2e7d43; } .perte { color: #c0392b; }
  .pastille { display: inline-block; width: .6rem; height: .6rem;
              border-radius: 50%; margin-right: .4rem; }
  .scroll { overflow-x: auto; }
</style>
</head>
<body>

<h1>Comparaison des comptes</h1>
<p class="note"><a href="./">← Administration</a></p>

<div class="carte">
  <h2 style="margin-top:0">Trajectoires, en base 100</h2>
  <?= ml_courbe_svg($series_svg, 860, 260, true) ?>
  <p class="note" style="margin-top:.6rem">
    <?php foreach ($comptes as $c): ?>
      <span style="margin-right:1rem; white-space:nowrap">
        <span class="pastille" style="background:<?= h($c['couleur']) ?>"></span>
        <?= h($c['nom']) ?></span>
    <?php endforeach; ?>
  </p>
  <p class="note">Base 100 au premier relevé de CHAQUE compte, et non en
    dollars : deux comptes ouverts à des dates différentes n'ont pas le même
    point de départ, et superposer des montants comparerait des trajectoires
    décalées plutôt que des performances.</p>
</div>

<div class="carte">
  <h2 style="margin-top:0">Performances par fenêtre</h2>
  <div class="scroll">
  <table>
    <tr>
      <th>Compte</th>
      <?php foreach (FENETRES as $lib): ?><th class="num"><?= $lib ?></th><?php endforeach; ?>
      <th class="num">Depuis l'ouverture</th>
      <th class="num">Baisse max</th>
      <th class="num">Relevés</th>
    </tr>
    <?php foreach ($comptes as $c): ?>
    <tr>
      <td>
        <span class="pastille" style="background:<?= h($c['couleur']) ?>"></span>
        <a href="./compte.php?nom=<?= urlencode($c['nom']) ?>">
          <strong><?= h($c['nom']) ?></strong></a>
      </td>
      <?php foreach (array_keys(FENETRES) as $j):
        $p = ml_perf_fenetre($c['serie'], $j); ?>
        <td class="num <?= $p === null ? 'note' : ($p >= 0 ? 'gain' : 'perte') ?>">
          <?= pc($p) ?></td>
      <?php endforeach; ?>
      <td class="num <?= $c['depuis_ouverture'] >= 0 ? 'gain' : 'perte' ?>">
        <strong><?= pc($c['depuis_ouverture']) ?></strong></td>
      <td class="num <?= $c['drawdown'] < -10 ? 'perte' : '' ?>">
        <?= pc($c['drawdown'], 1) ?></td>
      <td class="num note"><?= $c['releves'] ?></td>
    </tr>
    <?php endforeach; ?>
  </table>
  </div>
  <p class="note">« — » signifie que la série du compte ne couvre pas la
    fenêtre, pas qu'il n'a rien gagné. Le classement du concours porte sur la
    performance depuis l'ouverture, qui avantage mécaniquement le plus ancien
    quand le marché monte : les fenêtres glissantes corrigent ce biais.</p>
</div>

<div class="carte">
  <h2 style="margin-top:0">Comportement de trading</h2>
  <div class="scroll">
  <table>
    <tr><th>Compte</th><th class="num">Trades</th><th class="num">Réussite</th>
        <th class="num">Facteur de profit</th><th class="num">Espérance</th>
        <th class="num">Durée moyenne</th></tr>
    <?php foreach ($comptes as $c): $s = $c['stats']; ?>
    <tr>
      <td><a href="./compte.php?nom=<?= urlencode($c['nom']) ?>"><?= h($c['nom']) ?></a></td>
      <td class="num"><?= $s['n'] ?></td>
      <td class="num"><?= $s['reussite'] === null ? '—' : pc($s['reussite'], 0) ?></td>
      <td class="num <?= $s['facteur_profit'] === null ? ''
                        : ($s['facteur_profit'] >= 1 ? 'gain' : 'perte') ?>">
        <?= $s['facteur_profit'] === null ? '—'
            : number_format($s['facteur_profit'], 2, ',', ' ') ?></td>
      <td class="num <?= ($s['esperance'] ?? 0) >= 0 ? 'gain' : 'perte' ?>">
        <?= $s['esperance'] === null ? '—' : ml_montant($s['esperance']) . ' $' ?></td>
      <td class="num"><?= $s['duree_moyenne_h'] === null ? '—'
        : number_format($s['duree_moyenne_h'] / 24, 1, ',', ' ') . ' j' ?></td>
    </tr>
    <?php endforeach; ?>
  </table>
  </div>
  <p class="note">Un facteur de profit sous 1 signale une stratégie qui détruit
    du capital, même avec un bon taux de réussite. « — » quand le compte n'a
    pas encore essuyé de perte : annoncer un facteur infini sur trois trades
    gagnants se lirait comme une performance.</p>
</div>

</body>
</html>
