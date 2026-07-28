<?php
/**
 * Répond « ouvert » ou « ferme » pour une cotation donnée, avec la logique
 * réelle de la plateforme. Utilisé par les tests Python.
 *
 *   php tests/marche_php.php '<json de la cotation>'
 */

declare(strict_types=1);

require_once __DIR__ . '/../deploy/cours_lib.php';

$cote = json_decode((string)($argv[1] ?? ''), true);
if (!is_array($cote)) {
    fwrite(STDERR, "cotation illisible\n");
    exit(2);
}
echo ml_marche_ouvert($cote) ? "ouvert" : "ferme";
