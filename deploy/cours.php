<?php
/**
 * Point d'accès JSON des cotations, pour le site et la page trading.
 *
 *   cours.php?s=AAPL,BTCUSDT,EURUSD%3DX
 *   cours.php            (tous les symboles publiés)
 *
 * Le travail réel est dans cours_lib.php, partagé avec les pages PHP : une
 * seule implémentation, donc un seul prix pour toute la plateforme.
 */

declare(strict_types=1);

require_once __DIR__ . '/cours_lib.php';

header('Content-Type: application/json; charset=utf-8');
// jamais de cache navigateur : la fraîcheur est gérée côté serveur (60 s)
header('Cache-Control: no-store');

$demande = trim((string)($_GET['s'] ?? ''));
$symboles = $demande === ''
    ? array_keys(ml_cours_symboles_valides())
    : array_map('trim', explode(',', $demande));

$cours = ml_cours($symboles);

echo json_encode([
    'cours' => $cours,
    'genere_le' => date('Y-m-d H:i:s'),
    'ttl_s' => ML_COURS_TTL,
    'note' => 'crypto en temps réel (Binance) ; forex à la minute ; actions '
            . 'et matières avec le différé de ~15 min imposé par les bourses '
            . 'aux sources gratuites. « age_s » donne l\'âge réel de chaque '
            . 'cotation ; source « publié » = repli sur l\'instantané du site.',
], JSON_UNESCAPED_UNICODE);
