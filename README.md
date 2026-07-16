# BakeKit

CLI qui bake de l'éclairage réaliste (GI, ombres douces, AO) sur les textures d'un modèle 3D
en pilotant Cycles/Blender en mode headless. Zéro dépendance npm, zéro interface graphique.

## Prérequis

- **Blender 5.1.x** (le chemin par défaut sous Windows :
  `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`). Blender embarque son propre
  Python + numpy — rien d'autre à installer côté Python.
- **Node.js** pour le wrapper CLI.

## Statut

Toutes les étapes de la [ROADMAP.md](ROADMAP.md) sont validées (0 à 7). L'étape 7 (intégration
Three.js) a été vérifiée via le harnais de démo autonome `demo/` ; l'intégration réelle dans
DreamDesk reste à confirmer avec ce même code (§9 du guide).

## Structure

```
BakeKit/
├── package.json          # "bin": { "bakekit": "./cli.mjs" }
├── cli.mjs               # wrapper : args, localisation blender, spawn, vérif des sorties
├── blender/
│   ├── bake.py            # script bpy : le bake proprement dit
│   ├── make_test_scene.py # génère la Cornell box de test
│   ├── check.py           # contrôles qualité des sorties
│   └── make_nolight_fixture.py # fixture de test (harnais npm test)
├── three/
│   └── dumpLights.mjs    # helper : scène Three.js -> scene-lights.json
├── demo/                 # démo Three.js autonome (étape 7) : `npm start` dans ce dossier
└── test/                 # GLB de test + sorties (run.mjs = npm test)
```

Pour lancer la démo Three.js : `cd demo && npm install && npm start`, puis ouvrir l'URL affichée.

## Documentation

- [BakeKit-guide.md](BakeKit-guide.md) — guide de construction complet avec le code vérifié
  étape par étape (options CLI, codes de sortie, pièges rencontrés, limitations connues).
- [ROADMAP.md](ROADMAP.md) — feuille de route incrémentale, jalons et gates.

## Licence

[MIT](LICENSE)
