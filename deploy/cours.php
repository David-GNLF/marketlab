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
    // Valeurs MESURÉES le 31/07/2026 en séance, sur deux relevés espacés
    // de 90 s. La date fait partie de l'affirmation : une promesse de
    // fraîcheur sans date de mesure redevient fausse toute seule.
    // Chaîne à guillemets DOUBLES : le texte contient des apostrophes
    // françaises (« l'âge », « c'est »). En guillemets simples il faudrait
    // les échapper une à une, et un seul oubli casse la page entière — ce
    // qui vient d'arriver en écrivant ce commentaire.
    'note' => "mesuré le 31/07/2026, marchés ouverts : actions américaines, "
            . "indices et crypto en temps réel ; forex sous 2 min ; matières "
            . "premières différées de 10 min (différé du CME). « age_s » "
            . "donne l'âge réel de chaque cotation — c'est lui qui fait foi, "
            . "pas cette phrase générale ; source « publié » = repli sur "
            . "l'instantané du site.",
], JSON_UNESCAPED_UNICODE);
