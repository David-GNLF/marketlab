<?php
/**
 * Calcule l'équité d'un compte avec l'implémentation PHP réelle de la
 * plateforme, sur des cours figés fournis en entrée.
 *
 * Sert à vérifier depuis les tests Python que PHP et Python donnent le même
 * chiffre : les deux langages ont chacun leur code, donc rien ne garantit
 * a priori qu'ils s'accordent — sauf à le mesurer.
 *
 *   php tests/equite_php.php compte.json prix.json
 */

declare(strict_types=1);

require_once __DIR__ . '/../deploy/cours_lib.php';

if ($argc < 3) {
    fwrite(STDERR, "usage: php equite_php.php <compte.json> <prix.json>\n");
    exit(2);
}
$compte = json_decode((string)file_get_contents($argv[1]), true);
$prix = json_decode((string)file_get_contents($argv[2]), true);
if (!is_array($compte) || !is_array($prix)) {
    fwrite(STDERR, "entrées illisibles\n");
    exit(2);
}
printf("%.10f\n", ml_equite_compte($compte, $prix));
