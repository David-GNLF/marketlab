<?php
/**
 * MarketLab — relais de cotations (source de prix UNIQUE de la plateforme).
 *
 * Pourquoi un relais côté serveur plutôt qu'un appel direct du navigateur :
 * - les fournisseurs gratuits n'autorisent pas tous les appels navigateur
 *   (CORS) alors qu'ils répondent parfaitement à un appel serveur ;
 * - un cache partagé de 60 s fait qu'une seule requête sort par symbole et
 *   par minute, quel que soit le nombre de visiteurs ;
 * - la page trading, le panneau admin et le site lisent ainsi TOUS le même
 *   prix — c'est la règle qui a réglé la divergence de montants.
 *
 * Fraîcheur réelle, sans promesse mensongère : crypto en temps réel
 * (Binance), forex à la minute et actions/matières avec le différé de ~15
 * minutes imposé par les bourses aux sources gratuites. Chaque prix est donc
 * renvoyé AVEC son horodatage et son âge, et l'interface les affiche.
 *
 * Repli : si une source ne répond pas, on sert le dernier cours publié par
 * le site (source « publié ») — jamais d'écran vide, jamais de prix inventé.
 */

declare(strict_types=1);

const ML_COURS_TTL       = 60;      // secondes avant de rafraîchir un symbole
const ML_COURS_FRAIS_MAX = 900;     // au-delà, le prix n'est plus dit « direct »
const ML_COURS_TIMEOUT   = 8;
const ML_COURS_MAX_SYM   = 80;

function ml_cours_racine(): string { return __DIR__; }

function ml_cours_fichier_cache(): string {
    return ml_cours_racine() . '/cache/cours.json';
}

/** Symboles autorisés : ceux que le site publie. C'est aussi la protection
 *  contre l'usage du relais comme proxy vers n'importe quelle URL. */
function ml_cours_symboles_valides(): array {
    static $liste = null;
    if ($liste !== null) return $liste;
    $liste = [];
    foreach (glob(ml_cours_racine() . '/donnees/titres/*.json') ?: [] as $f) {
        $liste[basename($f, '.json')] = true;
    }
    return $liste;
}

function ml_cours_publie(string $symbole): ?array {
    $f = ml_cours_racine() . '/donnees/titres/' . $symbole . '.json';
    if (!is_file($f)) return null;
    $d = json_decode((string)file_get_contents($f), true);
    $prix = $d['signaux']['close'] ?? null;
    if (!$prix) return null;
    $var = null;
    $hist = $d['historique'] ?? [];
    $n = count($hist);
    if ($n >= 2 && !empty($hist[$n - 2]['close'])) {
        $var = ((float)$hist[$n - 1]['close'] / (float)$hist[$n - 2]['close'] - 1) * 100;
    }
    return ['prix' => (float)$prix, 'var_pct' => $var,
            'horodatage' => null, 'source' => 'publié'];
}

function ml_cours_lire_cache(): array {
    $f = ml_cours_fichier_cache();
    if (!is_file($f)) return [];
    $d = json_decode((string)file_get_contents($f), true);
    return is_array($d) ? $d : [];
}

function ml_cours_ecrire_cache(array $cache): void {
    $dossier = dirname(ml_cours_fichier_cache());
    if (!is_dir($dossier)) @mkdir($dossier, 0755, true);
    // le cache n'a rien à faire sur le web, même derrière l'authentification
    if (!is_file($dossier . '/.htaccess')) {
        @file_put_contents($dossier . '/.htaccess', "Require all denied\n");
    }
    $tmp = ml_cours_fichier_cache() . '.tmp';
    if (@file_put_contents($tmp, json_encode($cache, JSON_UNESCAPED_UNICODE),
                           LOCK_EX) !== false) {
        @rename($tmp, ml_cours_fichier_cache());   // remplacement atomique
    }
}

function ml_cours_url(string $symbole): string {
    if (str_ends_with($symbole, 'USDT')) {
        return 'https://api.binance.com/api/v3/ticker/24hr?symbol='
             . rawurlencode($symbole);
    }
    return 'https://query1.finance.yahoo.com/v8/finance/chart/'
         . rawurlencode($symbole) . '?interval=1m&range=1d';
}

function ml_cours_url_secours(string $symbole): ?string {
    if (str_ends_with($symbole, 'USDT')) {
        return 'https://data-api.binance.vision/api/v3/ticker/24hr?symbol='
             . rawurlencode($symbole);
    }
    return 'https://query2.finance.yahoo.com/v8/finance/chart/'
         . rawurlencode($symbole) . '?interval=1m&range=1d';
}

/** Traduit la réponse brute d'une source en cotation normalisée. */
function ml_cours_analyser(string $symbole, string $corps): ?array {
    $d = json_decode($corps, true);
    if (!is_array($d)) return null;

    if (str_ends_with($symbole, 'USDT')) {
        if (!isset($d['lastPrice'])) return null;
        $prix = (float)$d['lastPrice'];
        if ($prix <= 0) return null;
        return ['prix' => $prix,
                'var_pct' => isset($d['priceChangePercent'])
                    ? (float)$d['priceChangePercent'] : null,
                'horodatage' => isset($d['closeTime'])
                    ? (int)round($d['closeTime'] / 1000) : time(),
                'source' => 'direct'];
    }

    $meta = $d['chart']['result'][0]['meta'] ?? null;
    if (!is_array($meta) || empty($meta['regularMarketPrice'])) return null;
    $prix = (float)$meta['regularMarketPrice'];
    if ($prix <= 0) return null;
    $veille = $meta['previousClose'] ?? $meta['chartPreviousClose'] ?? null;
    return ['prix' => $prix,
            'var_pct' => $veille ? ($prix / (float)$veille - 1) * 100 : null,
            'horodatage' => isset($meta['regularMarketTime'])
                ? (int)$meta['regularMarketTime'] : time(),
            'source' => 'direct'];
}

/** Récupère plusieurs symboles EN PARALLÈLE (une seule attente réseau). */
function ml_cours_telecharger(array $symboles): array {
    if (!$symboles || !function_exists('curl_multi_init')) return [];
    $resultats = [];
    foreach ([false, true] as $secours) {
        if (!$symboles) break;
        $multi = curl_multi_init();
        $handles = [];
        foreach ($symboles as $sym) {
            $url = $secours ? ml_cours_url_secours($sym) : ml_cours_url($sym);
            if ($url === null) continue;
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT => ML_COURS_TIMEOUT,
                CURLOPT_CONNECTTIMEOUT => 5,
                CURLOPT_FOLLOWLOCATION => true,
                CURLOPT_USERAGENT => 'Mozilla/5.0 (compatible; MarketLab/1.0)',
            ]);
            // magasin de certificats du système quand il est disponible :
            // indispensable derrière un antivirus qui inspecte le TLS, sans
            // jamais désactiver la vérification (ce serait ouvrir la porte).
            if (defined('CURLSSLOPT_NATIVE_CA')) {
                @curl_setopt($ch, CURLOPT_SSL_OPTIONS, CURLSSLOPT_NATIVE_CA);
            }
            curl_multi_add_handle($multi, $ch);
            $handles[$sym] = $ch;
        }
        $actif = null;
        do {
            $etat = curl_multi_exec($multi, $actif);
            if ($actif) curl_multi_select($multi, 1.0);
        } while ($actif && $etat === CURLM_OK);

        $restants = [];
        foreach ($handles as $sym => $ch) {
            $corps = curl_multi_getcontent($ch);
            $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
            curl_multi_remove_handle($multi, $ch);
            curl_close($ch);
            $cote = ($code === 200 && is_string($corps))
                ? ml_cours_analyser($sym, $corps) : null;
            if ($cote) $resultats[$sym] = $cote;
            else $restants[] = $sym;
        }
        curl_multi_close($multi);
        $symboles = $restants;   // seuls les ratés repassent par le secours
    }
    return $resultats;
}

/**
 * Cotations pour une liste de symboles.
 *
 * Renvoie symbole => [prix, var_pct, horodatage, age_s, source, frais].
 * `source` vaut « direct » (fournisseur) ou « publié » (repli sur
 * l'instantané quotidien du site) ; `frais` dit si le prix a moins de 15
 * minutes — c'est ce que l'interface affiche à l'utilisateur.
 */
function ml_cours(array $symboles, bool $rafraichir = true): array {
    $valides = ml_cours_symboles_valides();
    $symboles = array_values(array_unique(array_filter(
        $symboles, fn($s) => isset($valides[$s]))));
    if (!$symboles) return [];
    $symboles = array_slice($symboles, 0, ML_COURS_MAX_SYM);

    $cache = ml_cours_lire_cache();
    $maintenant = time();
    $a_rafraichir = [];
    foreach ($symboles as $s) {
        $e = $cache[$s] ?? null;
        if (!$e || ($maintenant - (int)($e['recupere_le'] ?? 0)) >= ML_COURS_TTL) {
            $a_rafraichir[] = $s;
        }
    }

    // Un seul visiteur rafraîchit ; les autres sont servis immédiatement avec
    // le cache existant plutôt que d'attendre (et de multiplier les appels).
    if ($rafraichir && $a_rafraichir) {
        $dossier = dirname(ml_cours_fichier_cache());
        if (!is_dir($dossier)) @mkdir($dossier, 0755, true);
        $verrou = @fopen($dossier . '/cours.lock', 'c');
        if ($verrou && flock($verrou, LOCK_EX | LOCK_NB)) {
            try {
                foreach (ml_cours_telecharger($a_rafraichir) as $s => $cote) {
                    $cote['recupere_le'] = $maintenant;
                    $cache[$s] = $cote;
                }
                ml_cours_ecrire_cache($cache);
            } finally {
                flock($verrou, LOCK_UN);
                fclose($verrou);
            }
        } elseif ($verrou) {
            fclose($verrou);
        }
    }

    $sortie = [];
    foreach ($symboles as $s) {
        $e = $cache[$s] ?? null;
        if (!$e) $e = ml_cours_publie($s);
        if (!$e) continue;
        $horodatage = $e['horodatage'] ?? null;
        $age = $horodatage ? max(0, $maintenant - (int)$horodatage) : null;
        $sortie[$s] = [
            'prix' => (float)$e['prix'],
            'var_pct' => isset($e['var_pct']) && $e['var_pct'] !== null
                ? round((float)$e['var_pct'], 3) : null,
            'horodatage' => $horodatage,
            'age_s' => $age,
            'source' => $e['source'] ?? 'publié',
            'frais' => ($e['source'] ?? '') === 'direct'
                       && $age !== null && $age <= ML_COURS_FRAIS_MAX,
        ];
    }
    return $sortie;
}

/** Cotation d'un seul symbole (repli inclus). */
function ml_cours_un(string $symbole): ?array {
    $r = ml_cours([$symbole]);
    return $r[$symbole] ?? null;
}

/** Âge en texte lisible : « il y a 12 s », « il y a 14 min ». */
function ml_cours_age_texte(?int $secondes): string {
    if ($secondes === null) return 'âge inconnu';
    if ($secondes < 90) return "il y a {$secondes} s";
    if ($secondes < 5400) return 'il y a ' . (int)round($secondes / 60) . ' min';
    if ($secondes < 172800) return 'il y a ' . (int)round($secondes / 3600) . ' h';
    return 'il y a ' . (int)round($secondes / 86400) . ' j';
}
