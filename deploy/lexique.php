<?php
/**
 * MarketLab — bulles d'explication du vocabulaire, côté PHP.
 *
 * Les définitions viennent de `donnees/glossaire.json`, produit par
 * `marketlab/glossaire.py` : le front React lit exactement le même fichier.
 * Écrire ici une seconde version des définitions garantirait qu'elles
 * divergent — c'est la raison d'être de ce détour par un fichier partagé.
 *
 * PAS UNE LIGNE DE JAVASCRIPT. Ces pages sont servies par un mutualisé et
 * doivent rester lisibles même si un script échoue. `:hover` ouvre la bulle à
 * la souris, `:focus-within` au clavier et au toucher — le déclencheur étant
 * un vrai bouton, il reçoit le focus dans les deux cas. L'attribut `title` du
 * navigateur aurait été plus court à écrire, mais il n'apparaît jamais au
 * toucher et reste inatteignable au clavier : inutilisable pour une aide
 * destinée à quelqu'un qui débute.
 */

declare(strict_types=1);

function ml_lexique(): array {
    static $cache = null;
    if ($cache !== null) return $cache;
    // remonter jusqu'à la racine du site : ce fichier peut être inclus depuis
    // /admin/ comme depuis /trading/
    foreach ([__DIR__ . '/donnees/glossaire.json',
              __DIR__ . '/../donnees/glossaire.json'] as $chemin) {
        if (is_file($chemin)) {
            $d = json_decode((string)file_get_contents($chemin), true);
            if (is_array($d) && !empty($d['termes'])) return $cache = $d['termes'];
        }
    }
    return $cache = [];
}

/**
 * Un terme expliqué. `ml_terme('levier')` ou `ml_terme('levier', 'Effet')`
 * pour imposer le libellé affiché.
 *
 * Si le glossaire est absent — publication incomplète, fichier non encore
 * envoyé — la fonction rend le texte seul. Une aide manquante ne doit jamais
 * faire disparaître l'information qu'elle accompagne.
 */
function ml_terme(string $code, ?string $texte = null): string {
    $def = ml_lexique()[$code] ?? null;
    $libelle = $texte ?? ($def['libelle'] ?? $code);
    $ech = fn($s) => htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
    if (!$def) return $ech($libelle);

    $bulle = '<strong>' . $ech($def['libelle']) . '</strong>'
           . '<span>' . $ech($def['court']) . '</span>';
    if (!empty($def['long'])) {
        $bulle .= '<span class="terme-long">' . $ech($def['long']) . '</span>';
    }
    return '<span class="terme"><button type="button" class="terme-declencheur">'
         . $ech($libelle) . '</button>'
         . '<span role="tooltip" class="terme-bulle">' . $bulle . '</span></span>';
}

/** Feuille de style des bulles, à inclure dans le <head> des pages PHP. */
function ml_lexique_styles(): string {
    return <<<'CSS'
<style>
  .terme { position: relative; display: inline-block; }
  .terme-declencheur {
    font: inherit; color: inherit; background: none; border: 0; padding: 0;
    cursor: help; border-bottom: 1px dotted currentColor;
  }
  .terme-declencheur:focus-visible { outline: 2px solid currentColor;
    outline-offset: 2px; }
  .terme-bulle {
    position: absolute; z-index: 40; left: 0; top: calc(100% + 6px);
    display: none; flex-direction: column; gap: 4px;
    width: max-content; min-width: 14rem; max-width: min(22rem, 78vw);
    background: Canvas; color: CanvasText;
    border: 1px solid rgba(128,128,128,.45); border-radius: .5rem;
    padding: .55rem .7rem; box-shadow: 0 6px 22px rgba(0,0,0,.18);
    font-size: .8rem; line-height: 1.5; font-weight: 400; text-align: left;
    white-space: normal; text-transform: none; letter-spacing: normal;
    cursor: auto;
  }
  /* Souris ET clavier/toucher : le bouton reçoit le focus dans les deux cas. */
  .terme:hover .terme-bulle, .terme:focus-within .terme-bulle { display: flex; }
  .terme-long { opacity: .72; }
  /* Contre le bord droit, la bulle sortirait de l'écran. */
  th:last-child .terme-bulle, td:last-child .terme-bulle,
  .terme.droite .terme-bulle { left: auto; right: 0; }
</style>
CSS;
}
