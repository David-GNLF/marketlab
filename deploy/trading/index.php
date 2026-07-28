<?php
/**
 * MarketLab — environnement de trading virtuel avec levier (v2, façon XM).
 *
 * Capital de départ 1 000 $ virtuels par compte. Ordres au marché, limite et
 * stop, levier 1-20, stop/objectif modifiables sur position ouverte, tableau
 * de bord de marge (équité, marge utilisée, marge libre, niveau de marge) et
 * espace « Mon compte » (levier par défaut, mot de passe, remise à zéro).
 *
 * Exécution au COURS FRAIS servi par le relais (cours_lib.php) : crypto en
 * temps réel, forex à la minute, actions et matières au différé de ~15 min
 * des sources gratuites. Chaque prix est affiché avec son âge, et le repli
 * sur le dernier cours publié est signalé comme tel. Les ordres en attente,
 * stops, objectifs et liquidations sont appliqués par le robot quotidien sur
 * les extrêmes de séance officiels.
 *
 * Un fichier JSON par compte (trading/comptes/<nom>.json) : le robot
 * n'écrit que le sien, cette page n'écrit que celui de l'utilisateur
 * connecté — pas de conflit d'écriture.
 */

declare(strict_types=1);

require_once __DIR__ . '/../cours_lib.php';

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

/** Format français des montants, IDENTIQUE à celui du rafraîchissement en
 *  JavaScript (toLocaleString « fr-FR ») : sans cela le même nombre changerait
 *  d'apparence à chaque mise à jour automatique. */
function montant(float $x, int $decimales = 2): string {
    return number_format($x, $decimales, ',', "\u{202F}");
}

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

/** Cotations fraîches de tous les actifs, chargées une seule fois par page
 *  (un appel réseau groupé, mis en cache 60 s côté serveur). */
function cotations(): array {
    static $cotations = null;
    if ($cotations === null) $cotations = ml_cours(actifs_disponibles());
    return $cotations;
}

/** Identité de l'actif (nom, avis de l'outil) : elle vient de l'instantané
 *  publié, qui est le travail d'analyse — seul le PRIX est rafraîchi. */
function identite_titre(string $symbole): ?array {
    static $cache = [];
    if (array_key_exists($symbole, $cache)) return $cache[$symbole];
    $f = DOSSIER_DONNEES . '/titres/' . $symbole . '.json';
    if (!is_file($f)) return $cache[$symbole] = null;
    $d = json_decode((string)file_get_contents($f), true);
    return $cache[$symbole] = [
        'nom' => $d['nom'] ?? $symbole,
        'avis' => $d['signaux']['avis'] ?? null,
        'close_publie' => $d['signaux']['close'] ?? null,
    ];
}

/**
 * LE prix de référence de la plateforme : cotation fraîche du relais, repli
 * sur le dernier cours publié. Utilisé pour l'exécution, la valorisation et
 * l'affichage — une seule définition, donc jamais deux montants différents.
 */
function dernier_cours(string $symbole): ?array {
    $cote = cotations()[$symbole] ?? null;
    $identite = identite_titre($symbole);
    if (!$cote) {
        $cote = ml_cours_un($symbole);          // actif hors liste chargée
        if (!$cote) return null;
    }
    return [
        'prix' => (float)$cote['prix'],
        'var_pct' => $cote['var_pct'],
        'age_s' => $cote['age_s'],
        'source' => $cote['source'],
        'frais' => $cote['frais'],
        // l'état du marché est TRANSPORTÉ, jamais recalculé à partir d'une
        // cotation amputée : c'est ce qui laissait passer des ordres au
        // marché sur une bourse fermée
        'marche_ouvert' => $cote['marche_ouvert']
            ?? ml_marche_ouvert($cote),
        'nom' => $identite['nom'] ?? $symbole,
        'avis' => $identite['avis'] ?? null,
    ];
}

function fiche_titre(string $symbole): ?array { return dernier_cours($symbole); }

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

function prix_liquidation(array $p): float {
    $sens = $p['sens'] === 'long' ? 1 : -1;
    return $p['prix_entree'] - $sens * $p['prix_entree'] / max($p['levier'], 1);
}

function marge_utilisee(array $compte): float {
    return array_sum(array_map(fn($p) => (float)$p['marge'],
                     $compte['positions'] ?? []));
}

function marge_reservee(array $compte): float {
    return array_sum(array_map(fn($o) => (float)$o['marge'],
                     $compte['ordres'] ?? []));
}

function pnl_flottant(array $compte): float {
    $total = 0.0;
    foreach ($compte['positions'] ?? [] as $p) {
        $c = dernier_cours($p['symbole']);
        if ($c) $total += pnl_position($p, $c['prix']);
    }
    return $total;
}

/** Équité du compte. La définition n'est PAS ici : elle est unique pour
 *  toute la plateforme (cours_lib.php) et cette page ne fait que lui passer
 *  les cotations déjà chargées. */
function equite(array $compte): float {
    $prix = [];
    foreach (cotations() as $sym => $c) $prix[$sym] = $c['prix'];
    return ml_equite_compte($compte, $prix);
}

// ------------------------------------------------------------------- actions

/**
 * L'identité vient de l'authentification du SITE, pas d'un second mot de
 * passe. Tout le domaine est protégé : le serveur a donc déjà vérifié qui
 * vous êtes avant que cette page ne s'exécute. Avoir deux identités et deux
 * mots de passe pour la même personne était une complication sans contrepartie
 * — et un compte pouvait être supprimé d'un côté sans l'autre.
 */
function identite_site(): string {
    $nom = $_SERVER['PHP_AUTH_USER'] ?? $_SERVER['REMOTE_USER']
        ?? $_SERVER['REDIRECT_REMOTE_USER'] ?? '';
    $nom = strtolower(trim((string)$nom));
    return preg_match('/^[a-z0-9_.-]{2,24}$/', $nom) ? $nom : '';
}

$connecte = identite_site();
$message = null;
$action = $_POST['a'] ?? null;

// « claude » est le robot : personne d'autre ne pilote son compte.
$robot_reserve = $connecte === 'claude';
if ($robot_reserve) {
    $connecte = '';
    $message = ['erreur', 'Le compte « claude » est réservé au robot MarketLab.'];
}

$existe = $connecte !== '' && lire_compte($connecte) !== null;

if ($connecte !== '' && !$existe && $action === 'creer') {
    if (!hash_equals($_SESSION['csrf_t'] ?? '', $_POST['csrf'] ?? '§')) {
        $message = ['erreur', 'Session expirée — recharger la page.'];
    } else {
        ecrire_compte($connecte, [
            'nom' => $connecte,
            'capital_initial' => CAPITAL_DEPART, 'solde' => CAPITAL_DEPART,
            'positions' => [], 'ordres' => [], 'historique' => [],
            'levier_defaut' => 3,
            'equity' => [[date('Y-m-d H:i'), CAPITAL_DEPART]],
            'cree_le' => date('Y-m-d H:i'),
        ]);
        $existe = true;
        $message = ['ok', "Compte de trading créé avec " . CAPITAL_DEPART
            . ' $ virtuels. Bon courage face au robot.'];
    }
}

$actions_protegees = ['ouvrir', 'fermer', 'modifier', 'annuler_ordre',
                      'preferences', 'raz'];
if (!$existe) {
    $action = null;   // aucune action de trading sans compte
}
if ($connecte && in_array($action, $actions_protegees, true)) {
    if (!hash_equals($_SESSION['csrf_t'] ?? '', $_POST['csrf'] ?? '§')) {
        $message = ['erreur', 'Session expirée — recharger la page.'];
    } else {
        $compte = lire_compte($connecte);
        $compte['ordres'] = $compte['ordres'] ?? [];

        if ($action === 'ouvrir') {
            $symbole = $_POST['symbole'] ?? '';
            $sens = $_POST['sens'] === 'short' ? 'short' : 'long';
            $type = in_array($_POST['type_ordre'] ?? 'marche',
                             ['marche', 'limite', 'stop'], true)
                ? $_POST['type_ordre'] : 'marche';
            $mise = (float)($_POST['mise'] ?? 0);
            $levier = max(1, min(LEVIER_MAX, (int)($_POST['levier'] ?? 1)));
            $stop = (float)($_POST['stop'] ?? 0) ?: null;
            $objectif = (float)($_POST['objectif'] ?? 0) ?: null;
            $prix_ordre = (float)($_POST['prix_ordre'] ?? 0) ?: null;
            $validite = max(1, min(365, (int)($_POST['validite'] ?? 30)));
            $cours = dernier_cours($symbole);
            $s = $sens === 'long' ? 1 : -1;
            // pour un ordre en attente, stop/objectif se jugent par rapport
            // au prix d'exécution demandé, pas au cours actuel
            $ref = $type === 'marche' ? ($cours['prix'] ?? 0.0) : ($prix_ordre ?? 0.0);

            if (!$cours) {
                $message = ['erreur', 'Actif inconnu.'];
            } elseif ($mise < MISE_MIN || $mise > $compte['solde']) {
                $message = ['erreur', 'Mise invalide (min ' . MISE_MIN
                    . ' $, max solde disponible ' . round($compte['solde'], 2) . ' $).'];
            } elseif ($type !== 'marche' && !$prix_ordre) {
                $message = ['erreur', "Un ordre $type demande un prix de "
                    . 'déclenchement.'];
            } elseif ($type === 'limite' && $s * $prix_ordre >= $s * $cours['prix']) {
                $message = ['erreur', $sens === 'long'
                    ? 'Un achat limite se place SOUS le cours actuel '
                      . '(acheter moins cher). Pour acheter au-dessus, '
                      . 'utiliser un ordre stop.'
                    : 'Une vente limite se place AU-DESSUS du cours actuel. '
                      . 'Pour vendre en dessous, utiliser un ordre stop.'];
            } elseif ($type === 'stop' && $s * $prix_ordre <= $s * $cours['prix']) {
                $message = ['erreur', $sens === 'long'
                    ? 'Un achat stop se place AU-DESSUS du cours actuel '
                      . '(entrer sur cassure). Pour acheter en dessous, '
                      . 'utiliser un ordre limite.'
                    : 'Une vente stop se place SOUS le cours actuel.'];
            } elseif ($stop !== null && $s * $stop >= $s * $ref) {
                $message = ['erreur', 'Stop du mauvais côté du prix.'];
            } elseif ($objectif !== null && $s * $objectif <= $s * $ref) {
                $message = ['erreur', 'Objectif du mauvais côté du prix.'];
            } elseif ($type === 'marche' && !ml_marche_ouvert($cours)) {
                // Marché fermé : exécuter « au marché » sur une cotation de
                // plusieurs heures serait un gain (ou une perte) offert par
                // le retard, pas par une décision. Un vrai courtier refuse.
                $message = ['erreur', "Marché fermé pour $symbole — dernière "
                    . 'cotation ' . ml_cours_age_texte($cours['age_s'])
                    . '. Un ordre au marché s\'exécuterait sur un prix périmé. '
                    . 'Placez un ordre limite ou stop : il se déclenchera '
                    . 'dès que le marché rouvrira et touchera votre prix.'];
            } elseif ($type !== 'marche') {
                // ordre en attente : la mise est réservée immédiatement,
                // l'exécution est vérifiée chaque soir sur haut/bas de séance
                $compte['solde'] -= $mise;
                $compte['ordres'][] = [
                    'id' => substr(bin2hex(random_bytes(4)), 0, 8),
                    'symbole' => $symbole, 'sens' => $sens, 'type' => $type,
                    'prix' => $prix_ordre, 'marge' => $mise, 'levier' => $levier,
                    'stop' => $stop, 'objectif' => $objectif,
                    'cree_le' => date('Y-m-d H:i'), 'source' => 'manuel',
                    // sans échéance, un ordre jamais touché gèlerait sa marge
                    // indéfiniment : on borne, comme un courtier
                    'expire_le' => date('Y-m-d',
                        strtotime('+' . $validite . ' days')),
                ];
                $message = ecrire_compte($connecte, $compte)
                    ? ['ok', 'Ordre ' . strtoupper($type) . ' '
                       . strtoupper($sens) . " $symbole placé @ $prix_ordre "
                       . "(mise $mise $ réservée × levier $levier). Il sera "
                       . 'exécuté dès que la séance touche ce prix — '
                       . "vérification chaque soir. Valable $validite jours, "
                       . 'ensuite la mise vous est rendue.']
                    : ['erreur', 'Écriture du compte impossible.'];
            } else {
                $prix = $cours['prix'] * (1 + $s * SPREAD_PCT / 100);
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
                    'base_prix' => $cours['source'],
                    'age_prix_s' => $cours['age_s'],
                ];
                $message = ecrire_compte($connecte, $compte)
                    ? ['ok', strtoupper($sens) . " $symbole ouvert : marge $mise $ "
                       . "× levier $levier = notionnel " . round($notionnel)
                       . " $ @ " . round($prix, 4)
                       . " (spread " . SPREAD_PCT . " % inclus) — cotation "
                       . $cours['source'] . ', ' . ml_cours_age_texte($cours['age_s'])]
                    : ['erreur', 'Écriture du compte impossible.'];
            }
        } elseif ($action === 'fermer') {
            $id = $_POST['id'] ?? '';
            foreach ($compte['positions'] as $i => $p) {
                if ($p['id'] !== $id) continue;
                $cours = dernier_cours($p['symbole']);
                if (!$cours) { $message = ['erreur', 'Cours indisponible.']; break; }
                if (!ml_marche_ouvert($cours)) {
                    // même raison qu'à l'ouverture : sortir sur un prix
                    // périmé fabriquerait un résultat que le marché n'a pas
                    // donné. Les stops, eux, restent surveillés chaque soir.
                    $message = ['erreur', "Marché fermé pour {$p['symbole']} — "
                        . 'dernière cotation ' . ml_cours_age_texte($cours['age_s'])
                        . '. La position reste protégée : son stop et son '
                        . 'objectif sont vérifiés chaque soir sur les extrêmes '
                        . 'de séance.'];
                    break;
                }
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
        } elseif ($action === 'modifier') {
            $id = $_POST['id'] ?? '';
            $stop = (float)($_POST['stop'] ?? 0) ?: null;
            $objectif = (float)($_POST['objectif'] ?? 0) ?: null;
            foreach ($compte['positions'] as $i => $p) {
                if ($p['id'] !== $id) continue;
                $cours = dernier_cours($p['symbole']);
                $s = $p['sens'] === 'long' ? 1 : -1;
                // le stop se juge par rapport au cours ACTUEL : il doit rester
                // du côté perdant, l'objectif du côté gagnant
                if ($cours && $stop !== null && $s * $stop >= $s * $cours['prix']) {
                    $message = ['erreur', 'Stop du mauvais côté du cours actuel ('
                        . round($cours['prix'], 4) . ').'];
                } elseif ($cours && $objectif !== null
                          && $s * $objectif <= $s * $cours['prix']) {
                    $message = ['erreur', 'Objectif du mauvais côté du cours actuel ('
                        . round($cours['prix'], 4) . ').'];
                } else {
                    $compte['positions'][$i]['stop'] = $stop;
                    $compte['positions'][$i]['objectif'] = $objectif;
                    $message = ecrire_compte($connecte, $compte)
                        ? ['ok', "{$p['symbole']} : stop "
                           . ($stop ?? 'retiré') . ', objectif '
                           . ($objectif ?? 'retiré') . '.']
                        : ['erreur', 'Écriture impossible.'];
                }
                break;
            }
        } elseif ($action === 'annuler_ordre') {
            $id = $_POST['id'] ?? '';
            foreach ($compte['ordres'] as $i => $o) {
                if ($o['id'] !== $id) continue;
                $compte['solde'] += $o['marge'];   // la réservation est rendue
                unset($compte['ordres'][$i]);
                $compte['ordres'] = array_values($compte['ordres']);
                $message = ecrire_compte($connecte, $compte)
                    ? ['ok', "Ordre {$o['symbole']} annulé — {$o['marge']} $ "
                       . 'rendus au solde.']
                    : ['erreur', 'Écriture impossible.'];
                break;
            }
        } elseif ($action === 'preferences') {
            $compte['levier_defaut'] = max(1, min(LEVIER_MAX,
                (int)($_POST['levier_defaut'] ?? 3)));
            $message = ecrire_compte($connecte, $compte)
                ? ['ok', 'Levier par défaut : ×' . $compte['levier_defaut']
                   . ' (pré-rempli dans le ticket d\'ordre).']
                : ['erreur', 'Écriture impossible.'];
        } elseif ($action === 'raz') {
            if (($_POST['confirmation'] ?? '') !== 'RAZ') {
                $message = ['erreur', 'Pour confirmer, taper RAZ dans le champ.'];
            } else {
                $compte['solde'] = CAPITAL_DEPART;
                $compte['capital_initial'] = CAPITAL_DEPART;
                $compte['positions'] = [];
                $compte['ordres'] = [];
                $compte['historique'] = [];
                $compte['equity'] = [[date('Y-m-d H:i'), CAPITAL_DEPART]];
                $message = ecrire_compte($connecte, $compte)
                    ? ['ok', 'Compte remis à ' . CAPITAL_DEPART . ' $ — '
                       . 'positions, ordres et historique effacés.']
                    : ['erreur', 'Écriture impossible.'];
            }
        }
    }
}

$compte = $connecte ? lire_compte($connecte) : null;
if ($compte) $compte['ordres'] = $compte['ordres'] ?? [];
$actifs = actifs_disponibles();
$symbole_choisi = $_GET['s'] ?? '';
if (!in_array($symbole_choisi, $actifs, true)) $symbole_choisi = '';
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
         max-width: 1080px; margin: 24px auto; padding: 0 16px;
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
  button.sobre { background: transparent; border: 1px solid
    color-mix(in srgb, CanvasText 30%, transparent); color: CanvasText;
    padding: 4px 8px; font-size: 12px; margin: 0; }
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
  nav.ancres { display: flex; gap: 4px; flex-wrap: wrap; margin: 10px 0;
    border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
    padding-bottom: 8px; }
  nav.ancres a { text-decoration: none; color: CanvasText; font-size: 13px;
    padding: 5px 10px; border-radius: 6px;
    background: color-mix(in srgb, CanvasText 7%, transparent); }
  details.modif summary { cursor: pointer; font-size: 12px; opacity: .75; }
  .defile { overflow-x: auto; }
  #recap-ticket { font-size: 13px; background:
    color-mix(in srgb, CanvasText 6%, transparent); border-radius: 6px;
    padding: 10px 12px; margin-top: 10px; line-height: 1.6; }
  .avis-achat { color: #0a7a0a; font-weight: 600; }
  .avis-vente { color: #d03b3b; font-weight: 600; }
  @keyframes ml-flash { from { background: color-mix(in srgb,
      #2a78d6 35%, transparent); } to { background: transparent; } }
  .clignote { animation: ml-flash 1s ease-out; }
  .pouls { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    background: #0a7a0a; animation: ml-pouls 2s infinite; vertical-align: middle; }
  @keyframes ml-pouls { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
  @media (prefers-reduced-motion: reduce) {
    .clignote, .pouls { animation: none; }
  }
</style>
</head>
<body>
<h1>🏦 MarketLab — trading virtuel</h1>
<p class="note">Argent 100 % virtuel — 1 000 $ de départ, levier jusqu'à
  ×<?= LEVIER_MAX ?>, spread simulé de <?= SPREAD_PCT ?> %.
  <strong>Cotations rafraîchies en continu</strong> : crypto en temps réel,
  forex à la minute, actions et matières au différé de ~15 min imposé par les
  bourses aux sources gratuites. L'âge de chaque cotation est affiché — rien
  n'est présenté comme « direct » sans l'être. L'analyse (avis, verdicts),
  elle, date de l'instantané du <?= h(date_donnees()) ?>.
  Ordres en attente, stops, objectifs et liquidations sont vérifiés chaque
  soir sur les extrêmes de séance officiels. Le robot « claude » trade selon
  les verdicts de l'outil ; comparez-vous à lui sur la
  <a href="../">page Concours du site</a> — dont le classement est un
  <em>arrêté du soir</em>, alors que cette page vaut en direct : un écart
  entre les deux pendant la journée est normal.</p>

<?php if ($message): ?>
  <p class="<?= $message[0] ?>"><?= h($message[1]) ?></p>
<?php endif; ?>

<?php if (!$connecte): ?>
  <div class="carte">
    <h2>Identification impossible</h2>
    <p class="note">Cette page utilise l'identité avec laquelle vous êtes
      entré sur le site — aucun second mot de passe n'est demandé. Si vous
      voyez ce message, c'est que le serveur n'a pas transmis votre
      identifiant : ouvrez le site par
      <a href="../">sa page d'accueil</a>, puis revenez ici.</p>
  </div>

<?php elseif (!$existe): ?>
  <div class="carte">
    <h2>Bienvenue, <?= h($connecte) ?></h2>
    <p>Vous n'avez pas encore de compte de trading. Un clic suffit :
      pas de nouveau mot de passe à retenir, votre identité du site fait foi.</p>
    <form method="post">
      <input type="hidden" name="a" value="creer">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <button>Créer mon compte (<?= CAPITAL_DEPART ?> $ virtuels)</button>
    </form>
    <p class="note">Vous affronterez le robot « claude », qui applique les
      verdicts de l'outil. Le classement est sur la page Concours du site.</p>
  </div>

<?php else: ?>
  <?php $eq = equite($compte);
        $mu = marge_utilisee($compte);
        $mr = marge_reservee($compte);
        $pnl_f = pnl_flottant($compte);
        $niveau = $mu > 0 ? $eq / $mu * 100 : null;
        $perf = ($eq / $compte['capital_initial'] - 1) * 100;
        $levier_defaut = (int)($compte['levier_defaut'] ?? 3); ?>
  <p>Compte : <strong><?= h($connecte) ?></strong>
    <span class="note">— identité du site, pas de second mot de passe.</span>
  </p>

  <nav class="ancres">
    <a href="#marche">👁 Marché</a>
    <a href="#ticket">🧾 Nouvel ordre</a>
    <a href="#positions">📌 Positions (<?= count($compte['positions']) ?>)</a>
    <a href="#ordres">⏳ Ordres en attente (<?= count($compte['ordres']) ?>)</a>
    <a href="#historique">📜 Historique</a>
    <a href="#moncompte">⚙️ Mon compte</a>
  </nav>

  <div class="carte grille">
    <div class="tuile"><div class="l">Équité <span class="pouls"></span></div>
      <div class="v" id="v-equite"><?= montant($eq, 2) ?> $</div></div>
    <div class="tuile"><div class="l">Solde (marge libre)</div>
      <div class="v"><?= montant($compte['solde'], 2) ?> $</div></div>
    <div class="tuile"><div class="l">Marge utilisée</div>
      <div class="v"><?= montant($mu, 2) ?> $</div></div>
    <div class="tuile"><div class="l">Marge réservée (ordres)</div>
      <div class="v"><?= montant($mr, 2) ?> $</div></div>
    <div class="tuile"><div class="l">P&amp;L flottant <span class="pouls"></span></div>
      <div class="v <?= $pnl_f >= 0 ? 'pos' : 'neg' ?>" id="v-pnl">
        <?= ($pnl_f >= 0 ? '+' : '') . montant($pnl_f, 2) ?> $</div></div>
    <div class="tuile"><div class="l">Niveau de marge</div>
      <div class="v" id="v-niveau"><?= $niveau === null ? '—'
          : montant($niveau, 0) . ' %' ?></div></div>
    <div class="tuile"><div class="l">Performance</div>
      <div class="v <?= $perf >= 0 ? 'pos' : 'neg' ?>" id="v-perf">
        <?= ($perf >= 0 ? '+' : '') . montant($perf, 2) ?> %</div></div>
  </div>
  <p class="note" id="etat-flux">Cotations mises à jour automatiquement
    toutes les 30 secondes.</p>

  <div class="carte" id="marche">
    <h2>👁 Observation du marché</h2>
    <p class="note">Cours rafraîchis automatiquement, avec l'âge réel de
      chaque cotation (🟢 = direct, ⏳ = différé de la source gratuite,
      📄 = repli sur l'instantané publié). L'avis, lui, vient de l'analyse
      quotidienne. « Trader » pré-remplit le ticket d'ordre ; le détail
      complet (salle de marché SENS / QUAND / MARGE) est sur
      <a href="../">le site</a>, onglet Titre.</p>
    <div class="defile">
    <table>
      <tr><th>Actif</th><th>Nom</th><th>Cours</th><th>Var. séance</th>
          <th>Fraîcheur</th><th>Avis de l'outil</th><th></th></tr>
      <?php foreach ($actifs as $sym): $f = fiche_titre($sym);
            if (!$f) continue; ?>
      <tr>
        <td><strong><?= h($sym) ?></strong></td>
        <td class="note"><?= h($f['nom']) ?></td>
        <td data-prix="<?= h($sym) ?>"><?= round($f['prix'], 4) ?></td>
        <td data-var="<?= h($sym) ?>"
            class="<?= ($f['var_pct'] ?? 0) >= 0 ? 'pos' : 'neg' ?>">
          <?= $f['var_pct'] === null ? '—'
              : (($f['var_pct'] >= 0 ? '+' : '')
                 . montant($f['var_pct'], 2) . ' %') ?></td>
        <td class="note" data-age="<?= h($sym) ?>">
          <?= $f['source'] === 'publié' ? '📄 publié'
              : (ml_marche_ouvert($f)
                 ? '🟢 ' . h(ml_cours_age_texte($f['age_s']))
                 : '🌙 fermé · ' . h(ml_cours_age_texte($f['age_s']))) ?></td>
        <td class="<?= str_starts_with((string)$f['avis'], 'Achat')
            ? 'avis-achat' : (str_starts_with((string)$f['avis'], 'Vente')
            ? 'avis-vente' : 'note') ?>"><?= h($f['avis'] ?? '—') ?></td>
        <td><a class="sobre" style="text-decoration:none;padding:4px 8px;
             border-radius:6px;border:1px solid
             color-mix(in srgb, CanvasText 30%, transparent)"
             href="?s=<?= urlencode($sym) ?>#ticket">Trader</a></td>
      </tr>
      <?php endforeach; ?>
    </table>
    </div>
  </div>

  <div class="carte" id="ticket">
    <h2>🧾 Nouvel ordre</h2>
    <form method="post" id="form-ticket">
      <input type="hidden" name="a" value="ouvrir">
      <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
      <div class="grille">
        <div><label>Actif</label>
          <select name="symbole" id="t-symbole">
            <?php foreach ($actifs as $s): $f = fiche_titre($s); ?>
              <option value="<?= h($s) ?>" data-prix="<?= $f['prix'] ?? '' ?>"
                <?= $s === $symbole_choisi ? 'selected' : '' ?>>
                <?= h($s) ?><?= $f ? ' — ' . round($f['prix'], 4) : '' ?></option>
            <?php endforeach; ?>
          </select></div>
        <div><label>Sens</label>
          <select name="sens" id="t-sens">
            <option value="long">🟢 Acheter (long)</option>
            <option value="short">🔴 Vendre (short)</option></select></div>
        <div><label>Type d'ordre</label>
          <select name="type_ordre" id="t-type">
            <option value="marche">Au marché (immédiat)</option>
            <option value="limite">Limite (meilleur prix)</option>
            <option value="stop">Stop (sur cassure)</option></select></div>
        <div id="bloc-prix" style="display:none"><label>Prix de déclenchement</label>
          <input name="prix_ordre" id="t-prix" type="number" step="any"></div>
        <div id="bloc-validite" style="display:none"><label>Validité (jours)</label>
          <input name="validite" id="t-validite" type="number" min="1"
                 max="365" value="30"></div>
        <div><label>Mise / marge ($)</label>
          <input name="mise" id="t-mise" type="number" min="<?= MISE_MIN ?>"
                 step="1" value="50" required></div>
        <div><label>Levier (1-<?= LEVIER_MAX ?>)</label>
          <input name="levier" id="t-levier" type="number" min="1"
                 max="<?= LEVIER_MAX ?>" value="<?= $levier_defaut ?>" required></div>
        <div><label>Stop (optionnel)</label>
          <input name="stop" id="t-stop" type="number" step="any"></div>
        <div><label>Objectif (optionnel)</label>
          <input name="objectif" id="t-objectif" type="number" step="any"></div>
      </div>
      <div id="recap-ticket"></div>
      <button>Passer l'ordre</button>
      <p class="note">Perte maximale = la mise (liquidation automatique à
        marge épuisée, contrôlée chaque soir sur les extrêmes de séance —
        pas de solde négatif). Notionnel = mise × levier. Un ordre limite ou
        stop réserve la mise immédiatement et s'exécute dès qu'une séance
        touche le prix demandé (annulable tant qu'il n'est pas exécuté).</p>
    </form>
  </div>

  <div class="carte" id="positions">
    <h2>📌 Positions ouvertes</h2>
    <?php if (!$compte['positions']): ?>
      <p class="note">Aucune position.</p>
    <?php else: ?>
    <div class="defile">
    <table>
      <tr><th>Actif</th><th>Sens</th><th>Levier</th><th>Marge</th>
          <th>Entrée</th><th>Cours</th><th>P&L</th><th>Stop</th>
          <th>Objectif</th><th>Liquidation</th><th></th></tr>
      <?php foreach ($compte['positions'] as $p):
            $c = dernier_cours($p['symbole']);
            $pnl = $c ? pnl_position($p, $c['prix']) : null; ?>
      <tr data-position="<?= h($p['id']) ?>"
          data-symbole="<?= h($p['symbole']) ?>"
          data-quantite="<?= $p['quantite'] ?>"
          data-entree="<?= $p['prix_entree'] ?>"
          data-marge="<?= $p['marge'] ?>"
          data-sens="<?= $p['sens'] === 'long' ? 1 : -1 ?>">
        <td><strong><?= h($p['symbole']) ?></strong>
          <?php if (($p['source'] ?? '') === 'ordre'): ?>
            <span class="note" title="issue d'un ordre en attente">⏳</span>
          <?php endif; ?></td>
        <td><?= $p['sens'] === 'long' ? '🟢 long' : '🔴 short' ?></td>
        <td>×<?= (int)$p['levier'] ?></td>
        <td><?= montant($p['marge'], 0) ?> $</td>
        <td><?= round($p['prix_entree'], 4) ?></td>
        <td data-prix="<?= h($p['symbole']) ?>">
          <?= $c ? round($c['prix'], 4) : '—' ?></td>
        <td class="<?= $pnl >= 0 ? 'pos' : 'neg' ?>" data-pnl>
          <?= $pnl === null ? '—' : ($pnl >= 0 ? '+' : '')
              . montant($pnl, 2) ?> $</td>
        <td><?= $p['stop'] ?? '—' ?></td>
        <td><?= $p['objectif'] ?? '—' ?></td>
        <td class="note"><?= round(prix_liquidation($p), 4) ?></td>
        <td style="white-space:nowrap">
          <details class="modif">
            <summary>Modifier</summary>
            <form method="post">
              <input type="hidden" name="a" value="modifier">
              <input type="hidden" name="id" value="<?= h($p['id']) ?>">
              <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
              <label>Stop</label>
              <input name="stop" type="number" step="any"
                     value="<?= $p['stop'] ?? '' ?>" style="width:110px">
              <label>Objectif</label>
              <input name="objectif" type="number" step="any"
                     value="<?= $p['objectif'] ?? '' ?>" style="width:110px">
              <button class="sobre">OK</button>
              <p class="note">Champ vide = protection retirée.</p>
            </form>
          </details>
          <form method="post" onsubmit="return confirm('Fermer ?')">
            <input type="hidden" name="a" value="fermer">
            <input type="hidden" name="id" value="<?= h($p['id']) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="danger" style="margin-top:4px">Fermer</button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
    </div>
    <?php endif; ?>
  </div>

  <div class="carte" id="ordres">
    <h2>⏳ Ordres en attente</h2>
    <?php if (!$compte['ordres']): ?>
      <p class="note">Aucun ordre en attente. Un ordre limite ou stop placé
        dans le ticket ci-dessus apparaîtra ici jusqu'à son exécution (dès
        qu'une séance touche le prix) ou son annulation.</p>
    <?php else: ?>
    <div class="defile">
    <table>
      <tr><th>Actif</th><th>Sens</th><th>Type</th><th>Prix demandé</th>
          <th>Cours actuel</th><th>Mise réservée</th><th>Levier</th>
          <th>Stop</th><th>Objectif</th><th>Placé le</th><th>Expire le</th>
          <th></th></tr>
      <?php foreach ($compte['ordres'] as $o):
            $c = dernier_cours($o['symbole']); ?>
      <tr>
        <td><strong><?= h($o['symbole']) ?></strong></td>
        <td><?= $o['sens'] === 'long' ? '🟢 long' : '🔴 short' ?></td>
        <td><?= h($o['type']) ?></td>
        <td><?= round($o['prix'], 4) ?></td>
        <td data-prix="<?= h($o['symbole']) ?>">
          <?= $c ? round($c['prix'], 4) : '—' ?></td>
        <td><?= montant($o['marge'], 0) ?> $</td>
        <td>×<?= (int)$o['levier'] ?></td>
        <td><?= $o['stop'] ?? '—' ?></td>
        <td><?= $o['objectif'] ?? '—' ?></td>
        <td class="note"><?= h($o['cree_le']) ?></td>
        <td class="note"><?= h($o['expire_le'] ?? 'sans échéance') ?></td>
        <td>
          <form method="post" onsubmit="return confirm('Annuler cet ordre ?')">
            <input type="hidden" name="a" value="annuler_ordre">
            <input type="hidden" name="id" value="<?= h($o['id']) ?>">
            <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
            <button class="danger">Annuler</button>
          </form>
        </td>
      </tr>
      <?php endforeach; ?>
    </table>
    </div>
    <?php endif; ?>
  </div>

  <div class="carte" id="historique">
    <h2>📜 Historique
      (<?= count($compte['historique'] ?? []) ?> trades clos)</h2>
    <?php $hist = $compte['historique'] ?? [];
          if (!$hist): ?>
      <p class="note">Aucun trade clos pour l'instant.</p>
    <?php else:
          $gagnants = count(array_filter($hist, fn($t) => $t['pnl'] > 0));
          $pnls = array_column($hist, 'pnl'); ?>
    <p class="note">Trades gagnants : <?= $gagnants ?>/<?= count($hist) ?>
      (<?= round($gagnants / count($hist) * 100) ?> %) ·
      P&amp;L cumulé : <?= montant(array_sum($pnls), 2) ?> $ ·
      meilleur : <?= montant(max($pnls), 2) ?> $ ·
      pire : <?= montant(min($pnls), 2) ?> $</p>
    <div class="defile">
    <table>
      <tr><th>Actif</th><th>Sens</th><th>Levier</th><th>Entrée</th>
          <th>Sortie</th><th>P&L</th><th>Motif</th><th>Fermé le</th></tr>
      <?php foreach (array_reverse($hist) as $t): ?>
      <tr>
        <td><?= h($t['symbole']) ?></td>
        <td><?= $t['sens'] ?></td>
        <td>×<?= (int)$t['levier'] ?></td>
        <td><?= round($t['entree'], 4) ?></td>
        <td><?= round($t['sortie'], 4) ?></td>
        <td class="<?= $t['pnl'] >= 0 ? 'pos' : 'neg' ?>">
          <?= ($t['pnl'] >= 0 ? '+' : '') . montant($t['pnl'], 2) ?> $</td>
        <td class="note"><?= h($t['motif']) ?></td>
        <td class="note"><?= h($t['ferme_le']) ?></td>
      </tr>
      <?php endforeach; ?>
    </table>
    </div>
    <?php endif; ?>
  </div>

  <div class="carte" id="moncompte">
    <h2>⚙️ Mon compte</h2>
    <p class="note">Créé le <?= h($compte['cree_le'] ?? '?') ?> ·
      capital initial <?= montant($compte['capital_initial'], 0) ?> $.</p>
    <div class="grille">
      <form method="post">
        <input type="hidden" name="a" value="preferences">
        <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
        <label>Levier par défaut du ticket (1-<?= LEVIER_MAX ?>)</label>
        <input name="levier_defaut" type="number" min="1"
               max="<?= LEVIER_MAX ?>" value="<?= $levier_defaut ?>">
        <button class="sobre" style="margin-top:8px">Enregistrer</button>
      </form>
      <div>
        <label>Mot de passe</label>
        <p class="note">Il n'y en a plus qu'un : celui du site, que vous
          gérez depuis <a href="../acces/">la page d'accès</a>. Ce compte de
          trading suit votre identité.</p>
      </div>
      <form method="post"
            onsubmit="return confirm('Tout remettre à zéro ? Positions, ordres et historique seront définitivement effacés.')">
        <input type="hidden" name="a" value="raz">
        <input type="hidden" name="csrf" value="<?= jeton_csrf() ?>">
        <label>Remise à zéro (repartir à <?= CAPITAL_DEPART ?> $) —
          taper <strong>RAZ</strong> pour confirmer</label>
        <input name="confirmation" pattern="RAZ" required
               placeholder="RAZ">
        <button class="danger" style="margin-top:8px">Remettre à zéro</button>
      </form>
    </div>
  </div>

<script>
// --------------------------------------------------------------- cotations
// La page se met à jour toute seule : prix, variations, P&L des positions,
// P&L flottant, équité et niveau de marge. Le serveur mutualise les appels
// (cache de 60 s) : ce sondage ne coûte donc presque rien au fournisseur.
const ML = {
  solde: <?= json_encode(round($compte['solde'], 6)) ?>,
  margeUtilisee: <?= json_encode(round($mu, 6)) ?>,
  margeReservee: <?= json_encode(round($mr, 6)) ?>,
  capitalInitial: <?= json_encode((float)$compte['capital_initial']) ?>,
  symboles: <?= json_encode(array_values($actifs), JSON_UNESCAPED_UNICODE) ?>,
  intervalle: 30000,
};

(function () {
  const nf = (x, d = 2) => Number(x).toLocaleString('fr-FR',
      {minimumFractionDigits: d, maximumFractionDigits: d});

  function ageTexte(s) {
    if (s === null || s === undefined) return 'âge inconnu';
    if (s < 90) return `il y a ${s} s`;
    if (s < 5400) return `il y a ${Math.round(s / 60)} min`;
    if (s < 172800) return `il y a ${Math.round(s / 3600)} h`;
    return `il y a ${Math.round(s / 86400)} j`;
  }

  function peindre(el, valeur, texte) {
    if (!el) return;
    if (el.textContent.trim() !== texte.trim()) {
      el.classList.remove('clignote');
      void el.offsetWidth;              // redémarre l'animation
      el.classList.add('clignote');
    }
    el.textContent = texte;
    if (valeur !== null) {
      el.classList.toggle('pos', valeur >= 0);
      el.classList.toggle('neg', valeur < 0);
    }
  }

  function appliquer(cours) {
    // prix et variations partout où le symbole apparaît
    for (const [sym, c] of Object.entries(cours)) {
      document.querySelectorAll(`[data-prix="${CSS.escape(sym)}"]`)
        .forEach((el) => peindre(el, null, nf(c.prix, 4)));
      document.querySelectorAll(`[data-var="${CSS.escape(sym)}"]`)
        .forEach((el) => peindre(el, c.var_pct,
          c.var_pct === null ? '—'
            : `${c.var_pct >= 0 ? '+' : ''}${nf(c.var_pct, 2)} %`));
      document.querySelectorAll(`[data-age="${CSS.escape(sym)}"]`)
        .forEach((el) => {
          el.textContent = c.source === 'publié' ? '📄 publié'
            : c.marche_ouvert ? `🟢 ${ageTexte(c.age_s)}`
                              : `🌙 fermé · ${ageTexte(c.age_s)}`;
        });
      // le ticket doit calculer sur le prix vivant
      document.querySelectorAll(`#t-symbole option[value="${CSS.escape(sym)}"]`)
        .forEach((o) => { o.dataset.prix = c.prix; });
    }

    // P&L de chaque position, puis les tuiles du tableau de bord
    let pnlTotal = 0;
    document.querySelectorAll('[data-position]').forEach((tr) => {
      const c = cours[tr.dataset.symbole];
      if (!c) return;
      const pnl = (c.prix - parseFloat(tr.dataset.entree))
                * parseFloat(tr.dataset.quantite) * parseFloat(tr.dataset.sens);
      pnlTotal += pnl;
      peindre(tr.querySelector('[data-pnl]'), pnl,
              `${pnl >= 0 ? '+' : ''}${nf(pnl)} $`);
    });

    const equite = ML.solde + ML.margeReservee + ML.margeUtilisee + pnlTotal;
    const perf = (equite / ML.capitalInitial - 1) * 100;
    peindre(document.getElementById('v-pnl'), pnlTotal,
            `${pnlTotal >= 0 ? '+' : ''}${nf(pnlTotal)} $`);
    peindre(document.getElementById('v-equite'), null, `${nf(equite)} $`);
    peindre(document.getElementById('v-perf'), perf,
            `${perf >= 0 ? '+' : ''}${nf(perf)} %`);
    peindre(document.getElementById('v-niveau'), null,
            ML.margeUtilisee > 0
              ? `${nf(equite / ML.margeUtilisee * 100, 0)} %` : '—');

    const etat = document.getElementById('etat-flux');
    if (etat) {
      const n = Object.values(cours).filter((c) => c.frais).length;
      etat.textContent = `Cotations actualisées à ${new Date()
        .toLocaleTimeString('fr-FR')} — ${n}/${Object.keys(cours).length} `
        + `en direct. Rafraîchissement toutes les ${ML.intervalle / 1000} s.`;
    }
  }

  let enCours = false;
  // `premier` : un onglet ouvert en arrière-plan doit tout de même se mettre
  // à jour une fois ; l'économie ne commence qu'après.
  async function rafraichir(premier = false) {
    if (enCours || (document.hidden && !premier)) return;
    enCours = true;
    try {
      const r = await fetch('../cours.php?s='
        + encodeURIComponent(ML.symboles.join(',')), {cache: 'no-store'});
      if (r.ok) appliquer((await r.json()).cours || {});
    } catch (e) {
      const etat = document.getElementById('etat-flux');
      if (etat) etat.textContent = 'Cotations momentanément indisponibles — '
        + 'les montants affichés restent ceux du dernier rafraîchissement.';
    } finally {
      enCours = false;
    }
  }

  setInterval(rafraichir, ML.intervalle);
  document.addEventListener('visibilitychange',
    () => { if (!document.hidden) rafraichir(); });
  setTimeout(() => rafraichir(true), 1500);
})();

// Récapitulatif du ticket en direct : notionnel, quantité, prix de
// liquidation approximatif et P&L projeté au stop / à l'objectif.
(function () {
  const $ = (id) => document.getElementById(id);
  const champs = ['t-symbole', 't-sens', 't-type', 't-prix', 't-mise',
                  't-levier', 't-stop', 't-objectif'];
  const fmt = (x, d = 2) => Number.isFinite(x)
      ? x.toLocaleString('fr-FR', {maximumFractionDigits: d}) : '—';

  function recalc() {
    const type = $('t-type').value;
    $('bloc-prix').style.display = type === 'marche' ? 'none' : '';
    $('bloc-validite').style.display = type === 'marche' ? 'none' : '';
    const opt = $('t-symbole').selectedOptions[0];
    const cours = parseFloat(opt ? opt.dataset.prix : '');
    const prixOrdre = parseFloat($('t-prix').value);
    const ref = type === 'marche' ? cours : (prixOrdre || cours);
    const mise = parseFloat($('t-mise').value);
    const levier = parseInt($('t-levier').value, 10) || 1;
    const sens = $('t-sens').value === 'short' ? -1 : 1;
    const stop = parseFloat($('t-stop').value);
    const objectif = parseFloat($('t-objectif').value);

    const lignes = [];
    if (Number.isFinite(mise) && Number.isFinite(ref) && ref > 0) {
      const notionnel = mise * levier;
      const qte = notionnel / ref;
      const liq = ref - sens * ref / levier;
      lignes.push(`Exposition : <strong>${fmt(notionnel)} $</strong>`
        + ` (mise ${fmt(mise)} $ × levier ${levier})`
        + ` ≈ ${fmt(qte, 4)} unité(s) @ ${fmt(ref, 4)}`);
      lignes.push(`Liquidation approximative vers <strong>${fmt(liq, 4)}`
        + `</strong> (marge épuisée — perte = la mise)`);
      if (Number.isFinite(stop)) {
        const p = (stop - ref) * qte * sens;
        lignes.push(`Au stop ${fmt(stop, 4)} : P&L ≈ `
          + `<strong>${p >= 0 ? '+' : ''}${fmt(p)} $</strong>`);
      }
      if (Number.isFinite(objectif)) {
        const p = (objectif - ref) * qte * sens;
        lignes.push(`À l'objectif ${fmt(objectif, 4)} : P&L ≈ `
          + `<strong>${p >= 0 ? '+' : ''}${fmt(p)} $</strong>`);
      }
      if (Number.isFinite(stop) && Number.isFinite(objectif)) {
        const risque = Math.abs((stop - ref) * qte);
        const gain = Math.abs((objectif - ref) * qte);
        if (risque > 0)
          lignes.push(`Ratio gain/risque ≈ <strong>${fmt(gain / risque, 2)}`
            + `</strong> (viser ≥ 1,5)`);
      }
    } else {
      lignes.push('Renseigner la mise pour voir l\'exposition.');
    }
    $('recap-ticket').innerHTML = lignes.join('<br>');
  }

  champs.forEach((id) => {
    const el = $(id);
    if (el) { el.addEventListener('input', recalc);
              el.addEventListener('change', recalc); }
  });
  recalc();
})();
</script>
<?php endif; ?>
</body>
</html>
