<?php
/**
 * MarketLab — lecture et analyse des comptes de trading virtuel.
 *
 * Tout ce qui est ici se DÉDUIT des comptes : le journal des trades fermés
 * (`historique`) et la série d'équité que le robot ajoute à chaque passage
 * (`equity`, plafonnée à 400 points). Rien n'est recopié ni recalculé
 * autrement — en particulier l'équité COURANTE vient de `ml_equite_compte()`,
 * seule implémentation de la plateforme, celle-là même qu'utilisent la page
 * trading et le concours. Deux définitions de l'équité, c'est deux montants
 * qui divergent et un utilisateur qui ne sait plus lequel croire.
 *
 * Fonctions pures : elles prennent un compte déjà chargé et ne touchent ni au
 * disque ni au réseau, ce qui les rend vérifiables.
 */

declare(strict_types=1);

/** Série d'équité normalisée : [['t' => horodatage, 'v' => valeur], …]. */
function ml_serie_equite(array $compte): array {
    $points = [];
    foreach ($compte['equity'] ?? [] as $p) {
        if (!is_array($p) || count($p) < 2) continue;
        $v = (float)$p[1];
        if (!is_finite($v)) continue;
        $points[] = ['t' => (string)$p[0], 'v' => $v];
    }
    usort($points, fn($a, $b) => strcmp($a['t'], $b['t']));
    return $points;
}

/**
 * Performance sur les `jours` derniers jours, en %.
 *
 * Renvoie null quand la série ne remonte pas assez loin — plutôt que de
 * comparer au plus ancien point disponible, ce qui ferait passer une fenêtre
 * de 3 jours pour une performance « à 30 jours » et flatterait ou punirait un
 * compte selon son ancienneté.
 */
function ml_perf_fenetre(array $serie, int $jours): ?float {
    if (count($serie) < 2) return null;
    $fin = end($serie);
    $limite = date('Y-m-d H:i', strtotime("-$jours days"));
    $depart = null;
    foreach ($serie as $p) {
        if ($p['t'] >= $limite) { $depart = $p; break; }
    }
    if ($depart === null) return null;
    // la série ne couvre pas la fenêtre demandée : le plus ancien point est
    // postérieur à la borne, donc on ne sait rien de ce qui précède
    if ($depart === $serie[0] && $serie[0]['t'] > $limite) {
        $couverture = (strtotime($fin['t']) - strtotime($serie[0]['t'])) / 86400;
        if ($couverture < $jours * 0.6) return null;
    }
    if ($depart['v'] <= 0) return null;
    return ($fin['v'] / $depart['v'] - 1) * 100;
}

/** Plus forte baisse depuis un sommet, en % — le vrai coût d'une stratégie. */
function ml_drawdown_max(array $serie): float {
    $sommet = null; $pire = 0.0;
    foreach ($serie as $p) {
        if ($sommet === null || $p['v'] > $sommet) $sommet = $p['v'];
        if ($sommet > 0) {
            $baisse = ($p['v'] / $sommet - 1) * 100;
            if ($baisse < $pire) $pire = $baisse;
        }
    }
    return $pire;
}

/**
 * Statistiques du journal des trades fermés.
 *
 * Le FACTEUR DE PROFIT (gains cumulés ÷ pertes cumulées) est le chiffre à
 * regarder avant le taux de réussite : on peut gagner sept fois sur dix et
 * perdre de l'argent si les trois pertes sont trois fois plus grosses. En
 * dessous de 1, la stratégie détruit du capital quel que soit son taux de
 * réussite.
 *
 * La répartition PAR MOTIF DE SORTIE est l'autre chiffre instructif : si les
 * sorties se font massivement sur stop, ce n'est pas le choix des titres qui
 * est en cause mais le dimensionnement — un stop trop serré transforme du
 * bruit ordinaire en perte réalisée.
 */
function ml_stats_trades(array $compte): array {
    $h = $compte['historique'] ?? [];
    $vide = ['n' => 0, 'gagnants' => 0, 'reussite' => null, 'pnl' => 0.0,
             'gain_moyen' => null, 'perte_moyenne' => null,
             'facteur_profit' => null, 'esperance' => null,
             'meilleur' => null, 'pire' => null, 'duree_moyenne_h' => null,
             'par_motif' => [], 'par_actif' => []];
    if (!$h) return $vide;

    $gains = []; $pertes = []; $durees = [];
    $motifs = []; $actifs = [];
    foreach ($h as $t) {
        $pnl = (float)($t['pnl'] ?? 0);
        if ($pnl >= 0) $gains[] = $pnl; else $pertes[] = $pnl;

        $ouvert = strtotime((string)($t['ouvert_le'] ?? ''));
        $ferme  = strtotime((string)($t['ferme_le'] ?? ''));
        if ($ouvert && $ferme && $ferme >= $ouvert) {
            $durees[] = ($ferme - $ouvert) / 3600;
        }
        $motif = (string)($t['motif'] ?? 'non précisé');
        $motifs[$motif] = ($motifs[$motif] ?? ['n' => 0, 'pnl' => 0.0]);
        $motifs[$motif]['n']++;
        $motifs[$motif]['pnl'] += $pnl;

        $sym = (string)($t['symbole'] ?? '?');
        $actifs[$sym] = ($actifs[$sym] ?? ['n' => 0, 'pnl' => 0.0]);
        $actifs[$sym]['n']++;
        $actifs[$sym]['pnl'] += $pnl;
    }

    $somme_gains = array_sum($gains);
    $somme_pertes = abs(array_sum($pertes));
    $n = count($h);
    $pnl_total = $somme_gains - $somme_pertes;

    uasort($actifs, fn($a, $b) => $b['pnl'] <=> $a['pnl']);
    arsort($motifs);

    return [
        'n' => $n,
        'gagnants' => count($gains),
        'reussite' => $n ? count($gains) / $n * 100 : null,
        'pnl' => $pnl_total,
        'gain_moyen' => $gains ? $somme_gains / count($gains) : null,
        'perte_moyenne' => $pertes ? -$somme_pertes / count($pertes) : null,
        // null et non INF quand aucune perte : « infini » sur trois trades
        // gagnants n'apprend rien et se lit comme une performance
        'facteur_profit' => $somme_pertes > 0 ? $somme_gains / $somme_pertes : null,
        'esperance' => $n ? $pnl_total / $n : null,
        'meilleur' => $h ? max(array_map(fn($t) => (float)($t['pnl'] ?? 0), $h)) : null,
        'pire' => $h ? min(array_map(fn($t) => (float)($t['pnl'] ?? 0), $h)) : null,
        'duree_moyenne_h' => $durees ? array_sum($durees) / count($durees) : null,
        'par_motif' => $motifs,
        'par_actif' => $actifs,
    ];
}

/**
 * Courbe(s) d'équité en SVG, sans aucune dépendance.
 *
 * `series` : [['nom' => …, 'points' => serie, 'couleur' => '#…'], …]
 *
 * Les courbes sont tracées en BASE 100 quand elles sont plusieurs : deux
 * comptes ouverts à des dates différentes n'ont pas le même capital de départ
 * en valeur absolue, et superposer des dollars comparerait des trajectoires
 * décalées plutôt que des performances.
 */
function ml_courbe_svg(array $series, int $largeur = 720, int $hauteur = 200,
                       bool $base100 = true): string {
    $traces = [];
    $min = INF; $max = -INF; $t_min = INF; $t_max = -INF;

    foreach ($series as $s) {
        $pts = $s['points'] ?? [];
        if (count($pts) < 2) continue;
        $ref = $base100 ? (float)$pts[0]['v'] : 1.0;
        if ($base100 && $ref <= 0) continue;
        $xy = [];
        foreach ($pts as $p) {
            $t = strtotime($p['t']);
            if (!$t) continue;
            $v = $base100 ? $p['v'] / $ref * 100 : $p['v'];
            $xy[] = [$t, $v];
            $min = min($min, $v); $max = max($max, $v);
            $t_min = min($t_min, $t); $t_max = max($t_max, $t);
        }
        if (count($xy) >= 2) {
            $traces[] = ['nom' => $s['nom'] ?? '', 'xy' => $xy,
                         'couleur' => $s['couleur'] ?? '#4c78a8'];
        }
    }
    if (!$traces || $t_max <= $t_min) {
        return '<p class="note">Pas encore assez de points pour tracer une '
             . 'courbe : le robot ajoute un relevé par passage.</p>';
    }
    // marge verticale pour que la ligne ne colle jamais au bord
    $etendue = max($max - $min, 1e-9);
    $min -= $etendue * 0.08; $max += $etendue * 0.08;
    $etendue = $max - $min;

    $g = 44; $d = 8; $ht = 8; $hb = 18;   // marges
    $ix = $largeur - $g - $d; $iy = $hauteur - $ht - $hb;
    $px = fn($t) => $g + ($t - $t_min) / ($t_max - $t_min) * $ix;
    $py = fn($v) => $ht + (1 - ($v - $min) / $etendue) * $iy;

    $svg = sprintf('<svg viewBox="0 0 %d %d" width="100%%" height="%d" '
        . 'role="img" aria-label="Évolution de l\'équité">', $largeur, $hauteur,
        $hauteur);

    // repère : la ligne du départ, seule graduation qui veuille dire quelque
    // chose sur une base 100 — au-dessus on gagne, en dessous on perd
    if ($base100 && $min < 100 && $max > 100) {
        $y = $py(100);
        $svg .= sprintf('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" '
            . 'stroke="currentColor" stroke-dasharray="3 3" opacity=".35"/>',
            $g, $y, $g + $ix, $y);
        $svg .= sprintf('<text x="%d" y="%.1f" font-size="10" '
            . 'fill="currentColor" opacity=".6" text-anchor="end">départ</text>',
            $g - 5, $y + 3);
    }
    $svg .= sprintf('<text x="%d" y="%.1f" font-size="10" fill="currentColor" '
        . 'opacity=".6" text-anchor="end">%s</text>', $g - 5, $py($max) + 9,
        $base100 ? number_format($max, 1, ',', ' ') : ml_montant($max, 0));
    $svg .= sprintf('<text x="%d" y="%.1f" font-size="10" fill="currentColor" '
        . 'opacity=".6" text-anchor="end">%s</text>', $g - 5, $py($min) - 2,
        $base100 ? number_format($min, 1, ',', ' ') : ml_montant($min, 0));

    foreach ($traces as $tr) {
        $pts = [];
        foreach ($tr['xy'] as [$t, $v]) {
            $pts[] = sprintf('%.1f,%.1f', $px($t), $py($v));
        }
        $svg .= sprintf('<polyline fill="none" stroke="%s" stroke-width="1.8" '
            . 'stroke-linejoin="round" points="%s"/>',
            htmlspecialchars($tr['couleur'], ENT_QUOTES),
            implode(' ', $pts));
    }
    $svg .= sprintf('<text x="%d" y="%d" font-size="10" fill="currentColor" '
        . 'opacity=".55">%s</text>', $g, $hauteur - 4,
        htmlspecialchars(date('d/m/Y', (int)$t_min), ENT_QUOTES));
    $svg .= sprintf('<text x="%.1f" y="%d" font-size="10" fill="currentColor" '
        . 'opacity=".55" text-anchor="end">%s</text>', $g + $ix, $hauteur - 4,
        htmlspecialchars(date('d/m/Y', (int)$t_max), ENT_QUOTES));
    return $svg . '</svg>';
}

/** Palette stable : un compte garde sa couleur d'un écran à l'autre. */
function ml_couleur_compte(string $nom): string {
    $palette = ['#4c78a8', '#f58518', '#54a24b', '#b279a2',
                '#e45756', '#72b7b2', '#eeca3b', '#9d755d'];
    return $palette[abs(crc32($nom)) % count($palette)];
}
