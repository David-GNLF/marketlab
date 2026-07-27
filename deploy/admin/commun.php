<?php
/**
 * MarketLab — fonctions partagées entre le panneau d'administration et la
 * page publique de définition de mot de passe (/acces/).
 */

declare(strict_types=1);

require_once __DIR__ . '/../cours_lib.php';

const ML_HTPASSWD    = __DIR__ . '/../.htpasswd';
const ML_ROLES       = __DIR__ . '/roles.json';
const ML_INVITATIONS = __DIR__ . '/invitations.json';
const ML_AUDIT       = __DIR__ . '/audit.log';
const ML_TRADING     = __DIR__ . '/../trading/comptes';
const ML_URL         = 'https://marketlab.gnlfconsult.com';
const ML_EXP_INVITATION = 72 * 3600;   // 72 h
const ML_CAPITAL_TRADING = 1000.0;

// ------------------------------------------------------------------ fichiers

/** Montants au format français, comme la page trading. */
function ml_montant(float $x, int $decimales = 2): string {
    return number_format($x, $decimales, ',', "\u{202F}");
}

function ml_lire(string $chemin): array {
    if (!is_file($chemin)) return [];
    $d = json_decode((string)file_get_contents($chemin), true);
    return is_array($d) ? $d : [];
}

function ml_ecrire(string $chemin, array $d): bool {
    $tmp = $chemin . '.tmp';
    if (file_put_contents($tmp, json_encode($d, JSON_PRETTY_PRINT
        | JSON_UNESCAPED_UNICODE), LOCK_EX) === false) return false;
    return rename($tmp, $chemin);
}

// ------------------------------------------------------------------ htpasswd

function ml_htpasswd_lire(): array {
    $comptes = [];
    if (is_file(ML_HTPASSWD)) {
        foreach (file(ML_HTPASSWD, FILE_IGNORE_NEW_LINES
                 | FILE_SKIP_EMPTY_LINES) as $l) {
            $p = strpos($l, ':');
            if ($p !== false) $comptes[substr($l, 0, $p)] = substr($l, $p + 1);
        }
    }
    return $comptes;
}

function ml_htpasswd_ecrire(array $comptes): bool {
    $contenu = '';
    foreach ($comptes as $n => $h) $contenu .= "$n:$h\n";
    $tmp = ML_HTPASSWD . '.tmp';
    if (file_put_contents($tmp, $contenu, LOCK_EX) === false) return false;
    return rename($tmp, ML_HTPASSWD);
}

/** Vérifie un mot de passe contre un hachage bcrypt OU apr1 (historique). */
function ml_verifier_mdp(string $mdp, string $hachage): bool {
    if (str_starts_with($hachage, '$2y$') || str_starts_with($hachage, '$2a$')) {
        return password_verify($mdp, $hachage);
    }
    if (preg_match('#^\$apr1\$([^$]+)\$#', $hachage, $m)) {
        return hash_equals($hachage, ml_apr1($mdp, $m[1]));
    }
    return false;
}

/** Implémentation APR1-MD5 (format htpasswd historique d'Apache). */
function ml_apr1(string $mdp, string $sel): string {
    $ctx = $mdp . '$apr1$' . $sel;
    $bin = md5($mdp . $sel . $mdp, true);
    for ($i = strlen($mdp); $i > 0; $i -= 16) {
        $ctx .= substr($bin, 0, min(16, $i));
    }
    for ($i = strlen($mdp); $i > 0; $i >>= 1) {
        $ctx .= ($i & 1) ? chr(0) : $mdp[0];
    }
    $bin = md5($ctx, true);
    for ($i = 0; $i < 1000; $i++) {
        $nouveau = ($i & 1) ? $mdp : $bin;
        if ($i % 3) $nouveau .= $sel;
        if ($i % 7) $nouveau .= $mdp;
        $nouveau .= ($i & 1) ? $bin : $mdp;
        $bin = md5($nouveau, true);
    }
    $t = '';
    foreach ([[0, 6, 12], [1, 7, 13], [2, 8, 14], [3, 9, 15], [4, 10, 5]] as $g) {
        $t .= ml_apr1_b64((ord($bin[$g[0]]) << 16) | (ord($bin[$g[1]]) << 8)
              | ord($bin[$g[2]]), 4);
    }
    $t .= ml_apr1_b64(ord($bin[11]), 2);
    return '$apr1$' . $sel . '$' . $t;
}

function ml_apr1_b64(int $v, int $n): string {
    $alpha = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
    $s = '';
    for ($i = 0; $i < $n; $i++) { $s .= $alpha[$v & 0x3f]; $v >>= 6; }
    return $s;
}

// --------------------------------------------------------------------- rôles

function ml_roles(string $nom): array {
    return ml_lire(ML_ROLES)[$nom]['roles'] ?? [];
}

function ml_definir_roles(string $nom, array $roles, ?string $email = null): bool {
    $tous = ml_lire(ML_ROLES);
    $tous[$nom] = ['roles' => array_values(array_unique($roles)),
                   'email' => $email ?? ($tous[$nom]['email'] ?? null)];
    return ml_ecrire(ML_ROLES, $tous);
}

// --------------------------------------------------------------------- audit

function ml_audit(string $acteur, string $action): void {
    $ligne = sprintf("[%s] %s — %s (%s)\n", date('Y-m-d H:i:s'), $acteur,
                     $action, $_SERVER['REMOTE_ADDR'] ?? '?');
    file_put_contents(ML_AUDIT, $ligne, FILE_APPEND | LOCK_EX);
}

// --------------------------------------------------------------- invitations

function ml_creer_invitation(string $nom, string $email, array $roles,
                             string $type): string {
    $jeton = bin2hex(random_bytes(24));
    $inv = ml_lire(ML_INVITATIONS);
    // une seule invitation active par compte
    foreach ($inv as $k => $v) {
        if (($v['nom'] ?? '') === $nom) unset($inv[$k]);
    }
    $inv[hash('sha256', $jeton)] = [
        'nom' => $nom, 'email' => $email, 'roles' => $roles, 'type' => $type,
        'expire' => time() + ML_EXP_INVITATION,
    ];
    ml_ecrire(ML_INVITATIONS, $inv);
    return $jeton;
}

function ml_valider_invitation(string $jeton): ?array {
    $inv = ml_lire(ML_INVITATIONS);
    $cle = hash('sha256', $jeton);
    $v = $inv[$cle] ?? null;
    if (!$v) return null;
    if (($v['expire'] ?? 0) < time()) {
        unset($inv[$cle]);
        ml_ecrire(ML_INVITATIONS, $inv);
        return null;
    }
    return $v + ['cle' => $cle];
}

function ml_consommer_invitation(string $cle): void {
    $inv = ml_lire(ML_INVITATIONS);
    unset($inv[$cle]);
    ml_ecrire(ML_INVITATIONS, $inv);
}

function ml_lien_invitation(string $jeton): string {
    return ML_URL . '/acces/?j=' . $jeton;
}

/** Équité d'un compte de trading : cash + marge réservée + marges engagées
 *  + P&L latent, au COURS FRAIS du relais (cours_lib.php) — exactement la
 *  même définition et la même source de prix que la page trading. C'est
 *  cette règle unique qui empêche deux montants de diverger. */
function ml_equite_trading(array $compte): float {
    $total = (float)($compte['solde'] ?? 0);
    // mise réservée par les ordres en attente : toujours au compte
    foreach ($compte['ordres'] ?? [] as $o) {
        $total += (float)($o['marge'] ?? 0);
    }
    $positions = $compte['positions'] ?? [];
    if (!$positions) return $total;

    $cours = ml_cours(array_values(array_unique(
        array_column($positions, 'symbole'))));
    foreach ($positions as $p) {
        $total += (float)$p['marge'];
        $prix = $cours[$p['symbole']]['prix'] ?? null;
        if ($prix) {
            $sens = ($p['sens'] ?? 'long') === 'long' ? 1 : -1;
            $total += ((float)$prix - (float)$p['prix_entree'])
                      * (float)$p['quantite'] * $sens;
        }
    }
    return $total;
}


function ml_envoyer_invitation(string $email, string $nom, string $jeton,
                               string $type): bool {
    $lien = ml_lien_invitation($jeton);
    $sujet = $type === 'creation'
        ? 'MarketLab — création de votre accès'
        : 'MarketLab — réinitialisation de votre mot de passe';
    $corps = ($type === 'creation'
        ? "Bonjour,\n\nUn accès à MarketLab a été créé pour vous "
          . "(identifiant : $nom).\nDéfinissez votre mot de passe — vous seul "
          . "le connaîtrez :\n\n$lien\n\n"
        : "Bonjour,\n\nRéinitialisation demandée pour votre compte MarketLab "
          . "($nom).\nChoisissez votre nouveau mot de passe :\n\n$lien\n\n")
        . "Ce lien expire dans 72 heures. Si vous n'êtes pas à l'origine de "
        . "cette demande, ignorez ce message.\n\n— MarketLab (message "
        . "automatique, ne pas répondre)";
    $entetes = "From: MarketLab <noreply@gnlfconsult.com>\r\n"
        . "Content-Type: text/plain; charset=UTF-8\r\n";
    // enveloppe -f explicite : requise par nombre de configurations Exim
    // pour que le message parte réellement (et aligne le Return-Path)
    return @mail($email, '=?UTF-8?B?' . base64_encode($sujet) . '?=',
                 $corps, $entetes, '-fnoreply@gnlfconsult.com');
}
