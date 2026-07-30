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
function ml_courbe_svg(array $series, int $largeur = 720, int $hauteur = 220,
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
            $xy[] = [$t, $v, $p['v'], $p['t']];   // brut conservé pour l'infobulle
            $min = min($min, $v); $max = max($max, $v);
            $t_min = min($t_min, $t); $t_max = max($t_max, $t);
        }
        if (count($xy) >= 2) {
            $traces[] = ['nom' => (string)($s['nom'] ?? ''), 'xy' => $xy,
                         'couleur' => $s['couleur'] ?? ML_PALETTE[0]];
        }
    }
    if (!$traces || $t_max <= $t_min) {
        return '<p class="note">Pas encore assez de points pour tracer une '
             . 'courbe : le robot ajoute un relevé par passage, le soir.</p>';
    }

    // Plancher d'amplitude. Sur des comptes qui n'ont presque pas bougé, une
    // échelle ajustée au dixième de pour cent transforme du bruit en montagnes
    // russes : 0,05 % d'écart occuperait toute la hauteur. On impose donc une
    // fenêtre minimale — 1 % en base 100 — pour que l'œil garde la mesure de
    // ce qu'il regarde.
    $plancher = $base100 ? 1.0 : max(abs($max), 1.0) * 0.01;
    $etendue = max($max - $min, $plancher);
    $centre = ($max + $min) / 2;
    $min = $centre - $etendue / 2; $max = $centre + $etendue / 2;
    $marge = $etendue * 0.12;
    $min -= $marge; $max += $marge; $etendue = $max - $min;

    $g = 52; $d = 10; $ht = 10; $hb = 22;   // marges
    $ix = $largeur - $g - $d; $iy = $hauteur - $ht - $hb;
    $px = fn($t) => $g + ($t - $t_min) / ($t_max - $t_min) * $ix;
    $py = fn($v) => $ht + (1 - ($v - $min) / $etendue) * $iy;
    $fmt = fn($v) => $base100
        ? number_format($v, 2, ',', "\u{202F}")
        : ml_montant($v, 0) . ' $';

    $svg = sprintf('<svg viewBox="0 0 %d %d" width="100%%" height="%d" '
        . 'role="img" aria-label="Évolution de l\'équité" '
        . 'style="overflow:visible">', $largeur, $hauteur, $hauteur);

    // --- grille horizontale chiffrée -------------------------------------
    // Quatre repères plutôt que les deux extrêmes : sans graduation on ne sait
    // pas si l'écart affiché vaut 0,5 % ou 50 %, et toute courbe finit par
    // ressembler à un krach.
    for ($i = 0; $i <= 4; $i++) {
        $v = $min + $etendue * $i / 4;
        $y = $py($v);
        $svg .= sprintf('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" '
            . 'stroke="currentColor" opacity=".10"/>', $g, $y, $g + $ix, $y);
        $svg .= sprintf('<text x="%d" y="%.1f" font-size="10" fill="currentColor" '
            . 'opacity=".55" text-anchor="end">%s</text>',
            $g - 6, $y + 3, htmlspecialchars($fmt($v), ENT_QUOTES));
    }

    // --- ligne du départ --------------------------------------------------
    if ($base100 && $min < 100 && $max > 100) {
        $y = $py(100);
        $svg .= sprintf('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" '
            . 'stroke="currentColor" stroke-dasharray="4 3" opacity=".45"/>',
            $g, $y, $g + $ix, $y);
        $svg .= sprintf('<text x="%.1f" y="%.1f" font-size="9" fill="currentColor" '
            . 'opacity=".55">départ</text>', $g + $ix - 32, $y - 4);
    }

    // --- repères de dates -------------------------------------------------
    $n_dates = min(4, max(2, (int)floor($ix / 110)));
    for ($i = 0; $i < $n_dates; $i++) {
        $t = $t_min + ($t_max - $t_min) * $i / max($n_dates - 1, 1);
        $x = $px($t);
        $ancre = $i === 0 ? 'start' : ($i === $n_dates - 1 ? 'end' : 'middle');
        $svg .= sprintf('<text x="%.1f" y="%d" font-size="10" fill="currentColor" '
            . 'opacity=".55" text-anchor="%s">%s</text>',
            $x, $hauteur - 6, $ancre,
            htmlspecialchars(date('d/m', (int)$t), ENT_QUOTES));
    }

    // --- courbes ----------------------------------------------------------
    foreach ($traces as $tr) {
        $couleur = htmlspecialchars($tr['couleur'], ENT_QUOTES);
        $pts = [];
        foreach ($tr['xy'] as [$t, $v]) {
            $pts[] = sprintf('%.1f,%.1f', $px($t), $py($v));
        }
        $svg .= sprintf('<polyline fill="none" stroke="%s" stroke-width="2" '
            . 'stroke-linejoin="round" stroke-linecap="round" points="%s"/>',
            $couleur, implode(' ', $pts));

        // Un marqueur par RELEVÉ. Sans eux, quatre points reliés se lisent
        // comme une mesure continue : le trait suggère une densité de données
        // qui n'existe pas. Le titre natif donne date et montant au survol,
        // sans une ligne de JavaScript.
        foreach ($tr['xy'] as [$t, $v, $brut, $quand]) {
            $svg .= sprintf('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s">'
                . '<title>%s — %s : %s $</title></circle>',
                $px($t), $py($v), $couleur,
                htmlspecialchars($tr['nom'], ENT_QUOTES),
                htmlspecialchars((string)$quand, ENT_QUOTES),
                htmlspecialchars(ml_montant((float)$brut), ENT_QUOTES));
        }
        // point de départ marqué : une courbe qui commence au milieu du
        // graphique est un compte ouvert plus tard, pas un trou de données
        [$t0, $v0] = $tr['xy'][0];
        if ($t0 > $t_min) {
            $svg .= sprintf('<circle cx="%.1f" cy="%.1f" r="4.5" fill="none" '
                . 'stroke="%s" stroke-width="1.4" opacity=".8"><title>%s : '
                . 'premier relevé</title></circle>',
                $px($t0), $py($v0), $couleur,
                htmlspecialchars($tr['nom'], ENT_QUOTES));
        }
    }
    return $svg . '</svg>';
}

const ML_PALETTE = ['#4c78a8', '#f58518', '#54a24b', '#b279a2',
                    '#e45756', '#72b7b2', '#9d755d', '#eeca3b'];

/**
 * Couleurs des comptes : DISTINCTES, et stables d'un écran à l'autre.
 *
 * La version précédente tirait la couleur d'un hachage du nom
 * (`crc32 % 8`). Sur cinq comptes, deux se sont retrouvés avec le même orange
 * — et une légende où deux entrées portent la même pastille ne sert plus à
 * rien : impossible de savoir quelle courbe on lit. L'anniversaire fait que la
 * collision est probable bien avant d'épuiser la palette.
 *
 * L'attribution se fait donc par RANG dans la liste triée des comptes : deux
 * comptes ne peuvent plus partager une couleur tant qu'ils sont moins nombreux
 * que la palette, et l'ordre alphabétique garantit que la couleur d'un compte
 * ne change pas d'une page à l'autre.
 */
function ml_couleurs_comptes(array $noms): array {
    $noms = array_values(array_unique($noms));
    sort($noms, SORT_STRING);
    $out = [];
    foreach ($noms as $i => $n) $out[$n] = ML_PALETTE[$i % count(ML_PALETTE)];
    return $out;
}

/** Couleur d'un compte, en tenant compte de tous les autres. */
function ml_couleur_compte(string $nom, ?array $tous = null): string {
    $tous = $tous ?? [$nom];
    return ml_couleurs_comptes($tous)[$nom] ?? ML_PALETTE[0];
}

/**
 * Prix affiché avec une précision adaptée à sa grandeur.
 *
 * Les cours arrivent bruts du relais : `2224.1516`, `6.395196`, `0.00003214`.
 * Les afficher tels quels donne une fausse impression de précision sur les
 * grands nombres et écrase les petits. On garde donc un nombre de décimales
 * proportionné — quatre chiffres significatifs suffisent à décider, jamais
 * huit.
 */
function ml_prix(float $x): string {
    $a = abs($x);
    if ($a === 0.0) return '0';
    if ($a >= 1000) return number_format($x, 2, ',', "\u{202F}");
    if ($a >= 10)   return number_format($x, 3, ',', "\u{202F}");
    if ($a >= 1)    return number_format($x, 4, ',', "\u{202F}");
    if ($a >= 0.01) return number_format($x, 5, ',', "\u{202F}");
    return rtrim(rtrim(number_format($x, 8, ',', "\u{202F}"), '0'), ',');
}

/**
 * Enrichit une position ouverte de tout ce qui manque pour décider.
 *
 * Une ligne qui n'affiche que l'entrée, le stop et l'objectif oblige à faire
 * les soustractions de tête. Ce qu'on veut savoir, c'est : où en est-on, à
 * quelle distance du stop, et ce qu'on risque encore.
 *
 * `$cote` — cotation courante du relais, ou null si le symbole n'a pas répondu.
 */
function ml_position_detail(array $p, ?array $cote): array {
    $entree  = (float)($p['prix_entree'] ?? 0);
    $marge   = (float)($p['marge'] ?? 0);
    $levier  = (float)($p['levier'] ?? 1) ?: 1.0;
    $sens    = ($p['sens'] ?? 'long') === 'short' ? -1 : 1;
    $qte     = (float)($p['quantite'] ?? 0);
    $prix    = $cote['prix'] ?? null;

    $d = [
        'prix' => $prix,
        'notionnel' => $marge * $levier,
        // marge épuisée quand la perte égale la mise : le cours a parcouru
        // 1/levier depuis l'entrée, dans le mauvais sens
        'liquidation' => $entree * (1 - $sens / $levier),
        'pnl' => null, 'pnl_%_marge' => null, 'variation_%' => null,
        'vers_stop_%' => null, 'vers_objectif_%' => null,
        'ratio' => null, 'frais' => $cote['fraicheur'] ?? null,
    ];
    if ($prix !== null && $entree > 0) {
        $d['variation_%'] = ($prix / $entree - 1) * 100 * $sens;
        $d['pnl'] = ($prix - $entree) * $qte * $sens;
        if ($marge > 0) $d['pnl_%_marge'] = $d['pnl'] / $marge * 100;
    }
    $ref = $prix ?? ($entree ?: null);
    if ($ref && !empty($p['stop'])) {
        $d['vers_stop_%'] = ((float)$p['stop'] / $ref - 1) * 100;
    }
    if ($ref && !empty($p['objectif'])) {
        $d['vers_objectif_%'] = ((float)$p['objectif'] / $ref - 1) * 100;
    }
    // Ratio gain/risque encore devant soi, depuis le cours ACTUEL : c'est lui
    // qui dit s'il reste quelque chose à aller chercher, pas celui d'origine.
    if ($d['vers_stop_%'] !== null && $d['vers_objectif_%'] !== null) {
        $risque = abs($d['vers_stop_%']);
        if ($risque > 1e-9) $d['ratio'] = abs($d['vers_objectif_%']) / $risque;
    }
    return $d;
}

/** Restreint une série aux `jours` derniers jours (null = tout l'historique). */
function ml_filtrer_serie(array $serie, ?int $jours): array {
    if ($jours === null) return $serie;
    $limite = date('Y-m-d H:i', strtotime("-$jours days"));
    return array_values(array_filter($serie, fn($p) => $p['t'] >= $limite));
}
