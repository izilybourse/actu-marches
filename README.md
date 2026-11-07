# Actu marchés izily (sans IA, sans clé API, gratuit)

Deux automatisations GitHub Actions alimentent le widget :
- `actu.yml` : article de clôture quotidien (lundi-vendredi, 22h UTC) -> data/actu.json (branche main)
- `live.yml` : cotations + titres de presse toutes les ~10 min en séance, toutes les heures sinon -> live.json (branche `data`, un seul commit écrasé à chaque fois)

Mise en place :
1. Créer un dépôt GitHub **public** `izilybourse/actu-marches` et y déposer ce dossier.
2. Settings > Actions > General > Workflow permissions : « Read and write permissions ».
3. Onglet Actions : lancer une fois « Actu marchés quotidienne » ET « Actu marchés en direct » avec « Run workflow », puis lire les journaux.
4. Hostinger : module « HTML personnalisé », coller tout le contenu de widget-hostinger.html.

## Sources des taux
OAT et Bund : Zonebourse (page « FRANCE 10Y Cash » / « GERMANY 10Y Cash »), puis repli Banque de France / Bundesbank
via ideal-investisseur.fr (J-1), puis Stooq. Le journal d'exécution indique la source retenue.

## Module d'accueil « Actualités économiques »
- `fetch_news.py` + `.github/workflows/news.yml` : toutes les 15 min, lit les flux RSS (Le Figaro Éco, BFM Business, Les Échos, Le Monde Éco, Boursorama), garde titre, lien, source, date et vignette, publie `news.json` sur la branche `news`.
- `widget-actus-accueil.html` : à coller dans un module HTML de la page d'accueil. Cartes en ligne (défilement horizontal sur mobile), rechargement des données toutes les 5 min, « Il y a … » recalculé chaque minute.
- Lancer le workflow « Actualités accueil » une fois avec « Run workflow » et lire le journal : il indique quels flux ont répondu.
