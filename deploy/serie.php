<?php
/**
 * Relais de bougies fraîches, pour l'échelle intrajournalière du graphique.
 *
 *   serie.php?s=AAPL            barres 5 min de la séance en cours (+ la veille)
 *   serie.php?s=BTCUSDT&i=15m   autre pas de temps
 *
 * POURQUOI CE FICHIER. Les bougies publiées avec le site datent de la
 * dernière génération nocturne : parfaites pour la structure, inutilisables
 * pour choisir un moment d'entrée. Le site étant statique, la seule façon
 * d'avoir la séance en cours est un petit relais serveur — le même patron que
 * `cours.php`, qui ne sert qu'un prix, appliqué cette fois à une série.
 *
 * Le front colle ce qu'il reçoit en bout de série publiée : si le relais est
 * indisponible, le graphique reste juste, simplement daté, et l'interface
 * l'écrit noir sur blanc au lieu de laisser croire à du direct.
 *
 * SÉCURITÉ. Le symbole est validé contre la liste des titres PUBLIÉS et rien
 * d'autre : sans cette porte, le fichier serait un proxy ouvert vers
 * n'importe quelle URL de la source. Le pas de temps est lui aussi restreint
 * à une liste blanche.
 */

declare(strict_types=1);

require_once __DIR__ . '/cours_lib.php';

const ML_SERIE_TTL     = 60;    // secondes : une barre 5 min ne bouge pas plus vite
const ML_SERIE_TIMEOUT = 10;
const ML_SERIE_JOURS   = 2;     // séance en cours + la précédente

/** Pas de temps acceptés, et profondeur demandée à la source pour chacun. */
function ml_serie_intervalles(): array {
    return ['5m' => '5d', '15m' => '5d', '1h' => '1mo'];
}

function ml_serie_fichier_cache(string $symbole, string $interval): string {
    // Le symbole est déjà validé contre la liste publiée quand on arrive ici ;
    // le nettoyage qui suit est une seconde barrière, pas la première.
    $sur = preg_replace('/[^A-Za-z0-9._=^-]/', '_', $symbole);
    return ml_cours_racine() . '/cache/serie_' . $sur . '_' . $interval . '.json';
}

function ml_serie_lire_cache(string $symbole, string $interval): ?array {
    $f = ml_serie_fichier_cache($symbole, $interval);
    if (!is_file($f)) return null;
    $age = time() - (int)@filemtime($f);
    if ($age > ML_SERIE_TTL) return null;
    $d = json_decode((string)@file_get_contents($f), true);
    return is_array($d) ? $d : null;
}

function ml_serie_ecrire_cache(string $symbole, string $interval, array $d): void {
    $f = ml_serie_fichier_cache($symbole, $interval);
    @mkdir(dirname($f), 0755, true);
    @file_put_contents($f, json_encode($d, JSON_UNESCAPED_UNICODE), LOCK_EX);
}

function ml_serie_url(string $symbole, string $interval): string {
    if (str_ends_with($symbole, 'USDT')) {
        // 576 barres de 5 min = deux journées pleines ; Binance plafonne à 1000
        return 'https://api.binance.com/api/v3/klines?symbol='
             . rawurlencode($symbole) . '&interval=' . rawurlencode($interval)
             . '&limit=576';
    }
    $plage = ml_serie_intervalles()[$interval] ?? '5d';
    return 'https://query1.finance.yahoo.com/v8/finance/chart/'
         . rawurlencode($symbole) . '?interval=' . rawurlencode($interval)
         . '&range=' . $plage;
}

/**
 * Traduit la réponse de la source en séries colonnaires — exactement le
 * format produit par marketlab/serie.py, pour que le front n'ait qu'UNE
 * façon de lire une série, d'où qu'elle vienne.
 */
function ml_serie_analyser(string $symbole, string $corps): ?array {
    $d = json_decode($corps, true);
    if (!is_array($d)) return null;
    $t = $o = $h = $l = $c = $v = [];

    if (str_ends_with($symbole, 'USDT')) {
        foreach ($d as $k) {
            if (!is_array($k) || count($k) < 6) continue;
            $t[] = (int)round(((int)$k[0]) / 1000);
            $o[] = (float)$k[1]; $h[] = (float)$k[2];
            $l[] = (float)$k[3]; $c[] = (float)$k[4];
            $v[] = (int)round((float)$k[5]);
        }
    } else {
        $res = $d['chart']['result'][0] ?? null;
        if (!is_array($res)) return null;
        $ts = $res['timestamp'] ?? [];
        $q  = $res['indicators']['quote'][0] ?? [];
        if (!$ts || !isset($q['close'])) return null;
        foreach ($ts as $i => $sec) {
            // Yahoo renvoie des trous (null) sur les minutes sans transaction :
            // les garder produirait des bougies fantômes à zéro.
            if (!isset($q['close'][$i]) || $q['close'][$i] === null) continue;
            $t[] = (int)$sec;
            $o[] = (float)($q['open'][$i]  ?? $q['close'][$i]);
            $h[] = (float)($q['high'][$i]  ?? $q['close'][$i]);
            $l[] = (float)($q['low'][$i]   ?? $q['close'][$i]);
            $c[] = (float)$q['close'][$i];
            $v[] = (int)round((float)($q['volume'][$i] ?? 0));
        }
    }
    if (!$t) return null;

    // Deux journées suffisent : au-delà, c'est la série publiée qui fait foi
    // et le relais n'aurait plus qu'à retransmettre ce que le site sert déjà.
    $limite = time() - ML_SERIE_JOURS * 86400;
    $garde = [];
    foreach ($t as $i => $sec) { if ($sec >= $limite) $garde[] = $i; }
    if (!$garde) $garde = array_keys($t);
    $pren = static function (array $col) use ($garde) {
        $out = []; foreach ($garde as $i) { $out[] = $col[$i]; } return $out;
    };
    return ['t' => $pren($t), 'o' => $pren($o), 'h' => $pren($h),
            'l' => $pren($l), 'c' => $pren($c), 'v' => $pren($v),
            'n' => count($garde), 'interval' => '', 'source' => 'direct'];
}

function ml_serie(string $symbole, string $interval): ?array {
    $cache = ml_serie_lire_cache($symbole, $interval);
    if ($cache !== null) { $cache['source'] = 'cache'; return $cache; }

    $ch = curl_init(ml_serie_url($symbole, $interval));
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => ML_SERIE_TIMEOUT,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (compatible; MarketLab/1.0)',
    ]);
    $corps = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if (!is_string($corps) || $code !== 200) return null;

    $serie = ml_serie_analyser($symbole, $corps);
    if ($serie === null) return null;
    $serie['interval'] = $interval;
    ml_serie_ecrire_cache($symbole, $interval, $serie);
    return $serie;
}

// --------------------------------------------------------------------- sortie

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$symbole = trim((string)($_GET['s'] ?? ''));
$interval = trim((string)($_GET['i'] ?? '5m'));

if (!isset(ml_serie_intervalles()[$interval])) {
    http_response_code(400);
    echo json_encode(['erreur' => 'pas de temps non pris en charge',
                      'acceptes' => array_keys(ml_serie_intervalles())]);
    exit;
}
if ($symbole === '' || !isset(ml_cours_symboles_valides()[$symbole])) {
    http_response_code(404);
    echo json_encode(['erreur' => 'symbole inconnu du site']);
    exit;
}

$serie = ml_serie($symbole, $interval);
if ($serie === null) {
    // 200 volontairement : l'absence de barres fraîches est un cas NORMAL
    // (marché fermé, source indisponible), pas une panne du site. Le front
    // garde alors la série publiée et l'annonce comme datée.
    echo json_encode(['symbole' => $symbole, 'interval' => $interval,
                      'serie' => null, 'genere_le' => date('Y-m-d H:i:s'),
                      'note' => 'aucune barre fraîche disponible ; '
                              . 'le graphique reste sur la série publiée'],
                     JSON_UNESCAPED_UNICODE);
    exit;
}

echo json_encode([
    'symbole' => $symbole,
    'interval' => $interval,
    'serie' => $serie,
    'genere_le' => date('Y-m-d H:i:s'),
    'ttl_s' => ML_SERIE_TTL,
    // l'âge de la DERNIÈRE barre est le seul chiffre honnête sur la
    // fraîcheur : il vaut mieux qu'une promesse de « temps réel »
    'age_derniere_barre_s' => time() - (int)end($serie['t']),
], JSON_UNESCAPED_UNICODE);
