# BakeKit

CLI qui bake de l'éclairage réaliste (GI, ombres douces, AO) sur les textures d'un modèle 3D
en pilotant Cycles/Blender en mode headless. Zéro dépendance npm, zéro interface graphique.

## Prérequis

- **Blender 5.1.x** (le chemin par défaut sous Windows :
  `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`). Blender embarque son propre
  Python + numpy — rien d'autre à installer côté Python.
- **Node.js** pour le wrapper CLI.

## Statut

Projet en cours de construction incrémentale, voir [ROADMAP.md](ROADMAP.md) pour le détail des
étapes et leurs critères de passage (gates). Étapes 0 (bootstrap) et 1 (preuve de GI) validées.

## Structure

```
BakeKit/
├── package.json          # "bin": { "bakekit": "./cli.mjs" }
├── cli.mjs               # wrapper : args, localisation blender, spawn, vérif des sorties
├── blender/
│   ├── bake.py           # script bpy : le bake proprement dit
│   ├── make_test_scene.py# génère la Cornell box de test
│   └── check.py          # contrôles qualité des sorties
├── three/
│   └── dumpLights.mjs    # helper : scène Three.js -> scene-lights.json
└── test/                 # GLB de test + sorties
```

## Documentation

- [BakeKit-guide.md](BakeKit-guide.md) — guide de construction complet avec le code vérifié
  étape par étape (options CLI, codes de sortie, pièges rencontrés, limitations connues).
- [ROADMAP.md](ROADMAP.md) — feuille de route incrémentale, jalons et gates.

## Licence

[MIT](LICENSE)
