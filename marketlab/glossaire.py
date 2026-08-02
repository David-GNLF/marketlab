"""Glossaire — la définition de chaque terme, écrite UNE fois.

POURQUOI ICI. Le vocabulaire de MarketLab est éparpillé entre le site React,
les pages PHP du trading et de l'administration, et les notifications. Écrire
« qu'est-ce que le RSI » à trois endroits garantit trois formulations, dont
deux finiront fausses. Le glossaire est donc produit avec les autres blocs et
publié en JSON : le front le lit, les pages PHP aussi.

COMMENT SONT ÉCRITES LES DÉFINITIONS. Pour quelqu'un qui découvre — c'est la
seule contrainte, et elle est exigeante :

* pas de jargon DANS la définition d'un jargon. Expliquer le RSI par
  « oscillateur borné mesurant la vélocité » ne rend service à personne ;
* dire ce que ça VAUT autant que ce que c'est. Un novice a besoin de savoir
  qu'un RSI à 80 ne veut pas dire « ça va baisser », sinon il lira le chiffre
  comme un ordre ;
* ne rien promettre. Plusieurs de ces mesures ont été testées sur ce projet et
  se sont révélées sans pouvoir prédictif ; le glossaire le dit.

`court` tient dans une infobulle. `long` s'affiche dans un panneau, et peut
comporter la mise en garde.
"""

from __future__ import annotations

TERMES: dict[str, dict] = {
    # ---------------------------------------------------------------- socle
    "equite": {
        "libelle": "Équité",
        "court": "Ce que vaudrait le compte si tout était soldé maintenant.",
        "long": "Les liquidités, plus les mises engagées dans les positions "
                "ouvertes, plus (ou moins) les gains latents de ces positions. "
                "C'est la mesure du concours, et la seule qui compte pour "
                "comparer deux comptes.",
        "categorie": "Trading",
    },
    "solde": {
        "libelle": "Solde disponible",
        "court": "Argent libre, utilisable pour ouvrir une nouvelle position.",
        "long": "Différent de l'équité : ce qui est immobilisé en garantie sur "
                "les positions ouvertes n'apparaît pas ici, bien qu'il vous "
                "appartienne toujours.",
        "categorie": "Trading",
    },
    "levier": {
        "libelle": "Levier",
        "court": "Multiplicateur : ×5 fait bouger le résultat cinq fois plus.",
        "long": "Avec 100 $ et un levier de 5, on prend une position de 500 $. "
                "Une hausse de 2 % du titre rapporte 10 % de la mise — et une "
                "baisse de 2 % en coûte autant. Le levier multiplie les deux "
                "sens, jamais un seul.",
        "categorie": "Trading",
    },
    "marge": {
        "libelle": "Mise (marge)",
        "court": "Somme immobilisée en garantie d'une position.",
        "long": "C'est le montant maximal que la position peut faire perdre : "
                "au-delà, elle est liquidée automatiquement. Elle reste votre "
                "argent tant que la position vit.",
        "categorie": "Trading",
    },
    "notionnel": {
        "libelle": "Notionnel",
        "court": "Taille réelle de la position : mise × levier.",
        "long": "Une mise de 50 $ à effet 4 expose 200 $ de marché. C'est ce "
                "montant qui subit les variations du cours, pas la mise.",
        "categorie": "Trading",
    },
    "liquidation": {
        "libelle": "Prix de liquidation",
        "court": "Cours auquel la mise serait entièrement perdue.",
        "long": "La position se ferme d'office à ce niveau. Il se situe à "
                "1/levier de parcours depuis l'entrée, dans le mauvais sens : "
                "à effet 5, une baisse de 20 % suffit. Plus le levier est "
                "élevé, plus ce mur est proche.",
        "categorie": "Trading",
    },
    "stop": {
        "libelle": "Stop",
        "court": "Niveau de sortie automatique si le cours va contre vous.",
        "long": "Il limite la perte, au prix d'un risque : trop serré, il se "
                "déclenche sur une oscillation ordinaire et transforme du "
                "bruit en perte réelle.",
        "categorie": "Trading",
    },
    "objectif": {
        "libelle": "Objectif",
        "court": "Niveau de sortie automatique si le cours va dans votre sens.",
        "categorie": "Trading",
    },
    "long_court": {
        "libelle": "Long / court",
        "court": "Long = parier sur la hausse. Court = parier sur la baisse.",
        "categorie": "Trading",
    },
    "pnl": {
        "libelle": "P&L",
        "court": "Profit and Loss : le gain ou la perte, en argent.",
        "long": "« Latent » tant que la position est ouverte — il bouge à "
                "chaque cotation. « Réalisé » une fois la position fermée : "
                "là seulement il est acquis.",
        "categorie": "Trading",
    },

    # -------------------------------------------------------------- mesures
    "facteur_profit": {
        "libelle": "Facteur de profit",
        "court": "Total des gains ÷ total des pertes. Sous 1, on perd.",
        "long": "Le chiffre à regarder AVANT le taux de réussite. On peut "
                "gagner sept fois sur dix et perdre de l'argent si les trois "
                "pertes sont trois fois plus grosses que les gains. Sous 1, la "
                "stratégie détruit du capital quel que soit son taux de "
                "réussite.",
        "categorie": "Mesure",
    },
    "reussite": {
        "libelle": "Taux de réussite",
        "court": "Part des trades terminés en gain.",
        "long": "Séduisant et trompeur seul : il ne dit rien de la TAILLE des "
                "gains et des pertes. À lire avec le facteur de profit.",
        "categorie": "Mesure",
    },
    "esperance": {
        "libelle": "Espérance par trade",
        "court": "Gain moyen d'un trade, pertes comprises.",
        "long": "Négative, elle signifie que répéter la stratégie fait perdre "
                "à coup sûr sur la durée, même après quelques beaux coups.",
        "categorie": "Mesure",
    },
    "drawdown": {
        "libelle": "Baisse maximale",
        "court": "Plus forte chute depuis un sommet. Le vrai coût à supporter.",
        "long": "Une stratégie qui gagne 30 % en passant par −40 % en cours de "
                "route est intenable en pratique : on abandonne avant. Ce "
                "chiffre mesure ce qu'il a fallu encaisser.",
        "categorie": "Mesure",
    },
    "base100": {
        "libelle": "Base 100",
        "court": "Tout ramené à 100 au départ, pour comparer des trajectoires.",
        "long": "Deux comptes ouverts à des dates différentes n'ont pas le "
                "même point de départ en dollars. En base 100, 105 signifie "
                "« +5 % depuis son propre début », quel que soit le montant.",
        "categorie": "Mesure",
    },
    "ratio_gain_risque": {
        "libelle": "Ratio gain / risque",
        "court": "Ce qu'il reste à gagner rapporté à ce qu'on risque encore.",
        "long": "Calculé depuis le cours ACTUEL, pas depuis l'entrée. Sous 1, "
                "la position a déjà donné le meilleur d'elle-même : on risque "
                "plus que ce qu'on peut encore prendre.",
        "categorie": "Mesure",
    },
    "volatilite": {
        "libelle": "Volatilité",
        "court": "Ampleur habituelle des variations. Ni bonne, ni mauvaise.",
        "long": "Elle dit de combien un actif bouge, pas dans quel sens. "
                "Élevée, elle impose des positions plus petites — c'est tout "
                "ce qu'elle impose.",
        "categorie": "Mesure",
    },
    "var": {
        "libelle": "VaR",
        "court": "Perte que l'on ne dépasse que dans 5 % des scénarios.",
        "long": "Une VaR de −8 % signifie : dans 95 cas sur 100 la perte reste "
                "sous 8 %. Elle ne dit RIEN de ce qui se passe dans les 5 cas "
                "restants, qui peuvent être bien pires.",
        "categorie": "Mesure",
    },
    "quantile": {
        "libelle": "Quantile",
        "court": "Un niveau qu'un pourcentage donné de scénarios ne dépasse pas.",
        "categorie": "Mesure",
    },

    # ----------------------------------------------------- outils graphiques
    "rsi": {
        "libelle": "RSI",
        "court": "De 0 à 100 : mesure si l'actif vient de beaucoup monter ou "
                 "baisser.",
        "long": "Au-dessus de 70 on parle de « suracheté », sous 30 de "
                "« survendu ». Ce ne sont PAS des signaux d'achat ou de vente : "
                "un actif en forte tendance reste au-dessus de 70 pendant des "
                "semaines. C'est une description de ce qui vient de se passer, "
                "pas une prévision.",
        "categorie": "Indicateur",
    },
    "atr": {
        "libelle": "ATR",
        "court": "Amplitude moyenne d'une séance, en unités de prix.",
        "long": "Sert surtout à placer un stop : un stop plus serré que "
                "l'amplitude ordinaire du titre sera touché par le simple "
                "bruit du marché.",
        "categorie": "Indicateur",
    },
    "adx": {
        "libelle": "ADX",
        "court": "Force de la tendance, sans dire son sens.",
        "long": "Sous 20, le marché va de côté et les stratégies de suivi de "
                "tendance y perdent leur temps. Il ne dit jamais si ça monte "
                "ou si ça baisse.",
        "categorie": "Indicateur",
    },
    "sma": {
        "libelle": "Moyenne mobile",
        "court": "Cours moyen des N dernières séances, qui lisse les à-coups.",
        "categorie": "Indicateur",
    },
    "supertrend": {
        "libelle": "Supertrend",
        "court": "Suiveur de tendance qui bascule haussier/baissier.",
        "long": "Construit à partir de l'amplitude moyenne. Comme tout "
                "suiveur, il a raison dans les tendances marquées et se "
                "trompe souvent quand le marché hésite.",
        "categorie": "Indicateur",
    },
    "ichimoku": {
        "libelle": "Ichimoku",
        "court": "Ensemble de lignes situant le cours par rapport à son passé.",
        "categorie": "Indicateur",
    },
    "fibonacci": {
        "libelle": "Fibonacci",
        "court": "Niveaux de repli calculés sur un mouvement passé.",
        "long": "Très suivis, donc parfois auto-réalisateurs, mais sans "
                "fondement démontré.",
        "categorie": "Indicateur",
    },
    "obv": {
        "libelle": "OBV",
        "court": "Cumule les volumes selon le sens des séances.",
        "long": "Sans objet sur le forex, où il n'existe pas de volume central.",
        "categorie": "Indicateur",
    },
    "stochastique": {
        "libelle": "Stochastique",
        "court": "Situe la clôture dans l'amplitude récente, de 0 à 100.",
        "categorie": "Indicateur",
    },

    # --------------------------------------------------------------- macro
    "vix": {
        "libelle": "VIX",
        "court": "« Indice de la peur » : nervosité attendue sur les actions "
                 "américaines.",
        "long": "Il monte quand le marché baisse vite. Il mesure l'ampleur "
                "attendue des mouvements, pas leur direction.",
        "categorie": "Macro",
    },
    "volatilite_implicite": {
        "libelle": "Volatilité implicite",
        "court": "La volatilité que le marché des options price pour l'avenir.",
        "long": "Déduite du prix des options : la prévision d'un adversaire "
                "qui met de l'argent derrière la sienne. Elle dépasse en "
                "général la volatilité ensuite réalisée — non par erreur, mais "
                "parce qu'elle contient une prime d'assurance.",
        "categorie": "Macro",
    },
    "prime_variance": {
        "libelle": "Prime de variance",
        "court": "L'écart entre la volatilité pricée et celle qui se réalise.",
        "long": "Ce que les acheteurs de protection paient en trop, la plupart "
                "du temps. La vendre rapporte régulièrement — puis ruine d'un "
                "coup les jours où l'assurance sert. Prime de risque "
                "documentée, pas une anomalie à arbitrer.",
        "categorie": "Macro",
    },
    "backwardation": {
        "libelle": "Backwardation",
        "court": "Le court terme coûte plus cher que le long terme : stress.",
        "long": "Sur le VIX, cela signale que le marché redoute l'immédiat "
                "plus que l'avenir — configuration rare, souvent brève.",
        "categorie": "Macro",
    },
    "contango": {
        "libelle": "Contango",
        "court": "Le long terme coûte plus cher que le court terme : normal.",
        "categorie": "Macro",
    },
    "cot": {
        "libelle": "COT",
        "court": "Positions des gros intervenants sur les contrats à terme, "
                 "publiées chaque semaine.",
        "long": "Publié par le régulateur américain avec trois jours de "
                "décalage. Un positionnement extrême signale un marché "
                "encombré : beaucoup sont déjà du même côté, donc il reste peu "
                "d'acheteurs pour pousser plus haut.",
        "categorie": "Macro",
    },
    "carry": {
        "libelle": "Carry",
        "court": "Écart de taux d'intérêt entre deux devises.",
        "long": "Détenir la devise mieux rémunérée rapporte cet écart chaque "
                "jour — jusqu'à ce qu'un mouvement de change l'efface, ce qui "
                "arrive brutalement.",
        "categorie": "Macro",
    },
    "surprise_eco": {
        "libelle": "Surprise économique",
        "court": "Écart entre le chiffre publié et celui qu'on attendait.",
        "long": "C'est l'écart qui fait bouger les cours, pas le chiffre : un "
                "excellent résultat déjà anticipé ne provoque rien.",
        "categorie": "Macro",
    },
    "taux_reel": {
        "libelle": "Taux réel",
        "court": "Taux d'intérêt une fois l'inflation retirée.",
        "long": "Référence de l'or : quand les taux réels montent, détenir un "
                "métal qui ne rapporte aucun intérêt coûte plus cher.",
        "categorie": "Macro",
    },

    # ------------------------------------------------------ méthode & aveux
    "ic": {
        "libelle": "IC (pouvoir prédictif)",
        "court": "Mesure si une note annonce vraiment ce qui va suivre. 0 = rien.",
        "long": "Corrélation entre la note donnée et le rendement advenu. "
                "Zéro signifie « aucun lien », négatif signifie « lien "
                "inversé ». Mesuré sur ce projet : aucune compétence n'est "
                "démontrée, dans aucun sens — la mesure et son incertitude "
                "sont publiées en tête de la page Décisions plutôt que "
                "cachées.",
        "categorie": "Méthode",
    },
    "calibration": {
        "libelle": "Calibration",
        "court": "Un intervalle « 80 % » contient-il vraiment 80 % des cas ?",
        "long": "C'est la seule chose que ce projet prévoit correctement : la "
                "couverture réelle des intervalles est de 80 à 87 % pour 80 % "
                "annoncés. La DIRECTION, elle, reste proche du hasard.",
        "categorie": "Méthode",
    },
    "backtest": {
        "libelle": "Backtest",
        "court": "Rejouer une stratégie sur le passé pour voir ce qu'elle "
                 "aurait donné.",
        "long": "Piège classique : à force d'essayer des réglages, on finit "
                "par en trouver un qui aurait marché par hasard. Un beau "
                "backtest ne prouve presque rien.",
        "categorie": "Méthode",
    },
    "walk_forward": {
        "libelle": "Validation glissante",
        "court": "Entraîner sur le passé, tester sur la suite, et recommencer.",
        "long": "Seule façon honnête de juger : le modèle n'a jamais vu les "
                "données sur lesquelles on le note.",
        "categorie": "Méthode",
    },
    "monte_carlo": {
        "libelle": "Monte-Carlo",
        "court": "Simuler des milliers de futurs possibles pour en tirer une "
                 "fourchette.",
        "long": "Donne une distribution, pas une prévision. Le cône affiché "
                "n'annonce aucun prix : il dit où le cours a des chances de se "
                "trouver.",
        "categorie": "Méthode",
    },
    "spearman": {
        "libelle": "Corrélation de rang",
        "court": "Mesure si deux classements vont dans le même ordre.",
        "categorie": "Méthode",
    },
    "verdict": {
        "libelle": "Verdict",
        "court": "Note de −100 à +100 résumant plusieurs analyses.",
        "long": "Un point d'attention, pas une recommandation. Le bilan publié "
                "montre que ces verdicts n'ont pas démontré de capacité à "
                "trier les hausses des baisses.",
        "categorie": "Méthode",
    },
    "veto": {
        "libelle": "Veto",
        "court": "Blocage qui prime sur la note, quelle qu'elle soit.",
        "long": "Par exemple : un plan dont les simulations touchent le stop "
                "plus souvent que l'objectif est écarté même si la note est "
                "bonne.",
        "categorie": "Méthode",
    },
}


def par_categorie() -> dict[str, list[dict]]:
    """Termes groupés par thème, pour un affichage en panneau d'aide."""
    groupes: dict[str, list[dict]] = {}
    for code, t in TERMES.items():
        groupes.setdefault(t["categorie"], []).append({"code": code, **t})
    for liste in groupes.values():
        liste.sort(key=lambda x: x["libelle"].lower())
    return dict(sorted(groupes.items()))


def bloc() -> dict:
    """Bloc publié : le glossaire complet, plus son regroupement."""
    return {"termes": TERMES, "par_categorie": par_categorie(),
            "n": len(TERMES)}
