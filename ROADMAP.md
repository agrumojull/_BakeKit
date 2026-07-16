# BakeKit — Roadmap détaillée

Feuille de route incrémentale pour construire BakeKit, le CLI qui bake de l'éclairage
réaliste (GI, ombres douces, AO) sur les textures d'un modèle 3D en pilotant Cycles/Blender
en mode headless.

Document compagnon du [BakeKit-guide.md](BakeKit-guide.md) — le guide contient le code vérifié,
ce fichier organise le **travail** et ses jalons.

---

## Principes de découpage

- **Le cœur risqué d'abord, l'habillage ensuite.** La question qui peut tuer le projet
  (« Cycles calcule-t-il vraiment de la GI utilisable en headless ? ») est répondue dès l'étape 1.
- **Chaque étape a un livrable concret et un critère de passage (gate) mesurable.** On ne passe
  pas à l'étape suivante tant que le gate n'est pas vert.
- **Aucune dépendance npm, aucune interface graphique.** Blender est piloté en
  `--background --python`, le wrapper est du Node pur.
- **Tout est validable sur la Cornell box de test** générée par `make_test_scene.py`, sans
  asset externe.

### Environnement de référence
Validé le 14–16/07/2026 sur : Blender **5.1.2**, Windows 11, RTX 2060 (backend **OptiX** détecté
et utilisé), Node.js présent via le pipeline Vite. Blender embarque son Python + numpy → zéro
install côté Python.

### Vue d'ensemble des phases

| Phase | Étapes | Question à laquelle elle répond |
|---|---|---|
| **Prototype** | 0 → 3 | « Ça marche et c'est bien éclairé ? » |
| **Production** | 4 → 6 | « C'est solide ? » |
| **Intégration** | 7 | « La boucle avec DreamDesk est-elle fermée ? » |

### Légende de statut
- ✅ **Prouvé** — déjà exécuté et validé pendant la phase de vérification du guide.
- 🟡 **Partiel** — code écrit/vérifié mais pas encore assemblé dans le pipeline final.
- ⬜ **À faire** — reste à implémenter.

---

## Étape 0 — Bootstrap du projet  ✅

**Objectif.** Poser l'arborescence et générer les assets de test. Blender étant déjà installé,
l'étape se réduit au squelette du projet.

**Livrables.**
- Arborescence `BakeKit/` conforme au §2 du guide (`cli.mjs`, `blender/`, `three/`, `test/`).
- `package.json` avec `"bin": { "bakekit": "./cli.mjs" }`, `"type": "module"`, zéro dépendance.
- `blender/make_test_scene.py` génère `test/cornell.glb` **et** `test/cornell_nouv.glb`
  (variante sans UVs pour le cas d'erreur).

**Tâches.**
1. Créer l'arbo et le `package.json`.
2. Porter `make_test_scene.py` (Cornell box : sol/fond/plafond blancs, mur gauche rouge, mur
   droit vert, deux cubes blancs récepteurs, une point light blanche au plafond).
3. Vérifier l'export glTF avec `export_lights=True` (sinon les lumières disparaissent).

**Gate.** Les deux GLB existent et se réimportent sans erreur dans Blender.

**Pièges à surveiller** (§7 du guide).
- Point light et non area light : `KHR_lights_punctual` ignore silencieusement les area lights.
- `smart_project` exige le mode Édition (`poll()` = False en mode objet) → boucle `mode_set(EDIT)`.
- `export_lights=True` obligatoire.

---

## Étape 1 — Cœur de bake (la preuve de GI)  ✅

**Objectif.** Le minimum qui répond à la question existentielle du projet : importer un GLB,
configurer Cycles GPU/CPU, baker **une passe `combined`** sur **un seul objet (le Sol)**, sortir
un PNG. Lancé à la main via un `cfg.json`, sans wrapper.

**Livrables.**
- `blender/bake.py` réduit à : import GLB → sélection device (cascade OptiX/CUDA/HIP/METAL/ONEAPI
  → CPU) → world neutre si absent → bake `combined` du Sol → PNG sRGB.
- Lancement manuel documenté (§5 du guide, bloc PowerShell).

**Tâches.**
1. Import glTF + sélection du device Cycles avec fallback CPU propre.
2. World sombre neutre `(0.02, 0.02, 0.02)` si aucun world, sinon les zones non éclairées
   restent noir absolu.
3. Créer le node Image Texture **actif** sur chaque matériau (cible d'écriture de Cycles).
4. Sélectionner + activer l'objet, appeler `bpy.ops.object.bake` avec `margin`/`margin_type`/`uv_layer`.

**Gate — le test qui valide le projet.**
`check.py` mesure sur le Sol du **color bleeding rouge ET vert** venant des murs colorés (le sol
est blanc, la lumière est blanche → toute teinte ne peut venir que de la GI).
Résultat de référence : ~1700 texels rouges, ~1900 verts (bake 256²/64 samples).

> Le bleeding **mur → objet voisin** (CubeA blanc contre le mur rouge) est prouvé dès que
> l'objet reste dans la scène pendant le bake — c'est le cas naturellement. `check.py` teste les
> deux à partir de l'étape 2 (référence : **7533 texels rouges** sur la surface du CubeA).

**Pièges à surveiller.**
- « No active image found in material » → `nodes.active = node` sur chaque matériau.
- Traits noirs aux seams → `--margin 8` minimum.

---

## Étape 2 — Multi-objets × passes  ✅

**Objectif.** Généraliser à la boucle `objets × passes` et ajouter les passes `diffuse` (EXR) et
`ao` (PNG data), chacune avec son format et son espace colorimétrique.

**Livrables.**
- Boucle `for obj in meshes: for passe in passes:` dans `bake.py`.
- Tables `PASSES` (filtres de rendu) et `FORMATS` (float_buffer / colorspace / format / ext) du §4.

**Détail des passes.**

| Passe | Contenu | Format | Raison du format |
|---|---|---|---|
| `combined` | albedo × (diffus direct + GI) + ombres, **sans spéculaire** | PNG sRGB | figé, prêt pour `MeshBasicMaterial` |
| `diffuse` | lumière diffuse directe + indirecte, **sans albedo** | **EXR float** | valeurs > 1.0 (jusqu'à ~20 mesuré) qu'un PNG 8 bits écraserait |
| `ao` | occlusion ambiante seule | PNG Non-Color | données, pas de couleur |

**Gate.** 7 objets × 3 passes = **21 sorties** sur la Cornell box ; la passe `diffuse` EXR
contient des valeurs > 1.0 (vérifié : jusqu'à ~20, et ~950 sur le plafond près de la lampe).

**Pièges à surveiller.**
- Lightmap clampée/bandée si sortie en PNG → passe `diffuse` en EXR float (§7 piège 8).
- `combined` exclut volontairement `GLOSSY` : le spéculaire dépend du point de vue, il n'a rien
  à faire dans une texture figée.

---

## Étape 3 — Éclairage complet (cascade + modes Three.js)  ✅

**Objectif.** Implémenter toute la cascade d'éclairage, y compris les deux modes de première
classe : `--lights` (réutiliser une scène Three.js existante) et `--auto-light` (rig procédural
depuis la bounding box).

**Cascade d'éclairage** (priorité descendante, la première source disponible gagne) :
```
--lights → --auto-light → --hdri → --sun → lumières intégrées au GLB → erreur (exit 4)
```

**Livrables.**
- Bloc éclairage complet de `bake.py` (§4bis) : `build_lights_from_three`, `build_auto_rig`,
  `setup_world_hdri`, `setup_sun`, conversion de repère `three_to_blender`.
- Helper web `three/dumpLights.mjs` : sérialise les lumières d'une scène Three.js → `scene-lights.json`.
- Option `--light-scale` pour la calibration d'intensité.

**Le point critique : la conversion de repère.**
Three.js/glTF sont **Y-up**, Blender est **Z-up**. L'import glTF applique `(x,y,z) → (x,-z,y)` ;
il faut appliquer **la même** transformation aux lumières, sinon décalage de 90°.
✅ Vérifié end-to-end le 16/07/2026 : une lumière Three.js `[0, 3.9, 0]` recrée exactement
l'éclairage d'origine (bleeding rouge/vert symétrique : 1513 / 1509 texels).

**Le point délicat : les unités d'intensité.**
Three.js (≥ r155) est en unités photométriques (candela pour point/spot, lux pour directional) ;
Blender est en watts / W·m⁻². Pas de facteur universel → `--light-scale` cale l'ensemble en une
passe. La **géométrie** de l'éclairage (positions, directions, ombres, bleeding) est exacte sans
réglage ; seule la luminosité globale demande une calibration.

**Tâches.**
1. Brancher la cascade dans `bake.py` avant le fallback GLB.
2. Écrire `dumpLights.mjs` (couleurs converties en linéaire via `convertSRGBToLinear`).
3. Ajouter le parsing `lights` / `auto-light` / `light-scale` côté `cli.mjs`.
4. Documenter la méthode de calibration `--light-scale` (bake test basse résolution → comparer → figer).

**Gate.**
- `--lights scene.json` sur un GLB sans lumière reproduit l'éclairage attendu (bleeding symétrique). ✅
- `--auto-light` éclaire correctement sans aucune saisie manuelle. ✅
- `--light-scale` module bien l'intensité globale.

**Statut.** Cascade assemblée dans `bake.py` et helper `dumpLights.mjs` créé. Revérifié sur cette
machine : `--lights` sur `cornell_nolight.glb` (fixture générée sans lumière) reproduit le
bleeding rouge/vert (2450/2980 texels) ; `--auto-light` produit 7/7 sorties non uniformes
(moyennes 0.12–0.41), confirmant un éclairage correct sans saisie manuelle.

---

## Étape 4 — Robustesse & codes de sortie  ✅

**Objectif.** Garantir que chaque cas d'erreur renvoie un code de sortie fiable — le contrat
entre `bake.py` et `cli.mjs`.

**Contrat de codes de sortie.**

| Code | Signification |
|---|---|
| 0 | succès |
| 1 | exception bpy non capturée (via `--python-exit-code 1`) |
| 2 | `--object` introuvable |
| 3 | mesh sans UVs (le message liste les objets fautifs) |
| 4 | aucune source de lumière |

**Livrables.**
- Validation UV en amont du bake (exit 3 + liste des objets sans UVs).
- Détection `--object` introuvable (exit 2).
- Cascade d'éclairage épuisée → exit 4.
- `--python-exit-code 1` systématique au lancement de Blender.
- Lignes `BAKEKIT-OUT: {json}` par sortie produite + logs `BAKEKIT:` d'étape.

**Gate.** Chaque cas d'erreur renvoie le bon code (table ci-dessus), vérifié sur :
`cornell_nouv.glb` → exit 3 ; `--object Inexistant` → exit 2 ; GLB sans lumière ni `--hdri`/`--sun`
→ exit 4 ; `--sun "45,60,3"` → exit 0.

**Piège central.** Sans `--python-exit-code 1`, une exception Python dans Blender sort avec le
code **0** : le wrapper croirait au succès. Les `sys.exit(n)` explicites restent prioritaires
(3 reste 3).

**Statut.** Revérifié sur cette machine, les 4 cas du gate renvoient le bon code : `cornell_nouv.glb`
→ exit 3 (liste `Sol, MurGauche, MurDroit, MurFond, Plafond, CubeA, CubeB`) ; `--object Inexistant`
→ exit 2 ; GLB sans lumière ni `--hdri`/`--sun` → exit 4 ; `--sun "45,60,3"` → exit 0 (bake complet).
Vérifié en bonus : une exception Python non gérée (JSON invalide) ressort bien en exit 1 grâce à
`--python-exit-code 1`.

---

## Étape 5 — Wrapper CLI complet  ✅

**Objectif.** `cli.mjs` complet : localisation de Blender, config par fichier temporaire, relais
des logs, vérification des sorties.

**Livrables.**
- Parsing d'options via `node:util parseArgs` (toutes les options du §3).
- Détection de `blender.exe` dans l'ordre : `--blender` → `BAKEKIT_BLENDER` → PATH → scan de
  `C:\Program Files\Blender Foundation\Blender *\` (version la plus récente).
- **Config via fichier JSON temporaire** (jamais en argument inline).
- `spawnSync` avec `maxBuffer: 64 Mo`, relais des lignes `BAKEKIT`, vérification d'existence des
  fichiers annoncés.

**Gate.** `node cli.mjs test/cornell.glb …` produit exactement le même résultat que le lancement
manuel de l'étape 1–3.

**Statut.** Revérifié sur cette machine : bake via `cli.mjs --pass combined --res 256 --samples 64`
comparé pixel à pixel au lancement manuel équivalent (même `bake.py`, même `cfg.json`) — bleeding
rouge/vert quasi identique (1692–1695 rouge, 1892 vert des deux côtés), écart max **0.016**/256
et moyen **0.00015** sur le sol, cohérent avec le bruit Monte Carlo normal d'un rendu GPU (Cycles
n'est pas garanti bit-exact d'un run à l'autre). Correction apportée par rapport au code du guide :
`outDir` est maintenant résolu en chemin absolu (`path.resolve(opts.out)`) — un `--out` relatif
faisait écrire Blender hors du dossier attendu par le check d'existence du wrapper.

**Pièges à surveiller.**
- **Quoting PowerShell** : un JSON inline passé à un exe natif perd ses guillemets → fichier temporaire.
- **BOM UTF-8** : `Out-File -Encoding utf8` (PS 5.1) écrit un BOM → `bake.py` lit en `utf-8-sig`.
- **`maxBuffer` Node (1 Mo par défaut)** : stdout de Blender tronqué sur un long bake → lignes
  `BAKEKIT-OUT` perdues → passer à 64 Mo.

---

## Étape 6 — Harnais de test automatisé  ✅

**Objectif.** Une seule commande qui enchaîne : génération de scène → bake → contrôle qualité →
cas d'erreur.

**Livrables.**
- `blender/check.py` : détecte les images uniformes (bake raté), mesure le bleeding mur→sol et
  mur→objet, sort en exit 0/1.
- Script `npm test` orchestrant scène → bake → check → cas d'erreur (exit 2/3/4).

**Gate.** `CHECK: OK` obtenu en **une commande**, sur une machine propre.

**Astuce du test.** Sol et cubes blancs + lumière blanche → sans GI tout serait neutre (R≈G≈B).
Toute teinte prouve la lumière rebondie sur les murs colorés — le test est **indépendant du
layout UV** produit par `smart_project`.

**Statut.** Revérifié sur cette machine : `npm test` (→ `test/run.mjs`) enchaîne génération de
scène, bake 128²/32 samples (21 sorties), `check.py` (bleeding sol rouge=505/vert=530, CubeA
rouge=1966), puis les trois cas d'erreur `cornell_nouv.glb` → exit 3, `--object Inexistant` →
exit 2, `cornell_nolight.glb` (fixture générée à la volée par `blender/make_nolight_fixture.py`,
non commitée) → exit 4. Sortie finale `CHECK: OK — 0 échec(s)` en une seule commande.

---

## Étape 7 — Boucle fermée Three.js (intégration DreamDesk)  ⬜

**Objectif.** Charger une texture bakée dans une vraie scène Three.js et valider le rendu de bout
en bout.

**Livrables.**
- Chemin « rendu 100 % pré-calculé » : passe `combined` en `MeshBasicMaterial({ map })`, zéro
  lumière au runtime (`flipY = false`, `colorSpace = SRGBColorSpace`).
- Chemin « lightmap HDR » : passe `diffuse` EXR chargée via `EXRLoader` en `material.lightMap`
  (albedo dynamique, lumière bakée).

**Gate.** Rendu correct de bout en bout dans DreamDesk ; `flipY` et `colorSpace` corrects
(pas de texture à l'envers ni délavée).

**Piège à surveiller.** Convention UV Three.js + colorimétrie → `flipY = false` (mesh chargé via
`GLTFLoader`) + `SRGBColorSpace` sur la passe combined ; l'EXR est déjà en linéaire, `flipY`
déjà false.

---

## Extensions futures (hors v1)

Non planifiées dans la roadmap, mais notées pour ne pas les réinventer :

- **Auto-unwrap d'un canal UV1 dédié lightmap** (`bpy.ops.uv.lightmap_pack`, présent en 5.1.2) —
  probablement la première extension utile, pour les assets dont l'UV0 tuile ou se chevauche.
- **Atlas multi-objets** : une seule texture pour toute la scène (réduit fichiers et draw calls).
- **Post-denoise OIDN** en ligne de commande sur les sorties.
- **Ré-export GLB** avec la lightmap déjà branchée dans les matériaux.
- **Import FBX géométrie-seule** (mesh + UVs) — fonctionnel en 5.1.2, mais plante sur tout FBX
  contenant une lumière (bug `CyclesLightSettings.cast_shadow`) → à catcher/ignorer. Combiné à
  `--lights`/`--auto-light`, ce n'est pas gênant.
- **Vrai dégradé hémisphérique** pour `HemisphereLight` (node Sky du World) au lieu du fond uniforme.
- **Options de format** : `--format png16|exr` par passe, `--mono` pour un PNG 1 canal grayscale.

---

## Limitations assumées (à connaître avant de s'appuyer sur BakeKit)

1. **UVs qui tuilent ou se chevauchent** : le contrat « UVs existants obligatoires » suppose un
   unwrap unique 0–1 sans chevauchement. Assets en tiling/mirroring **non bakables tels quels** →
   la vraie réponse est l'extension auto-unwrap UV1, pas un contournement.
2. **Spéculaire et métaux** : `combined` exclut le glossy ; les matériaux très métalliques
   ressortiront sombres. Outil fait pour du mat/stylisé, pas du métal poli.
3. **Une texture par objet** : beaucoup de fichiers/draw calls sur une grosse scène →
   l'atlas multi-objets est l'extension la plus rentable après la v1.
4. **Pas de barre de progression** : `bpy.ops.object.bake` est silencieux ; un bake CPU long
   semble gelé sans l'être (les logs `BAKEKIT-OUT` jalonnent quand même objet par objet).
5. **API mouvante** : validé sur 5.1.2. `Material.use_nodes`/`World.use_nodes` sont annoncés
   pour suppression dans Blender 6.0 (warnings déjà visibles) → prévoir une passe de migration.

---

## Récapitulatif des jalons

| Étape | Livrable clé | Gate | Statut |
|---|---|---|---|
| 0. Bootstrap | Arbo + `make_test_scene.py` → 2 GLB | Les deux GLB se réimportent | ✅ |
| 1. Cœur de bake | `bake.py` minimal, 1 passe, 1 objet | Bleeding rouge **et** vert sur le sol | ✅ |
| 2. Multi-objets × passes | Boucle + `diffuse` EXR + `ao` | 21 sorties, EXR > 1.0 | ✅ |
| 3. Éclairage complet | Cascade + `--lights` + `--auto-light` | Reproduction Three.js + rig auto | ✅ |
| 4. Robustesse & codes | exits 1/2/3/4, logs `BAKEKIT-OUT` | Chaque erreur → bon code | ✅ |
| 5. Wrapper CLI | `cli.mjs` complet | Résultat identique au manuel | ✅ |
| 6. Harnais de test | `check.py` + `npm test` | `CHECK: OK` en une commande | ✅ |
| 7. Boucle Three.js | Texture chargée dans DreamDesk | Rendu correct bout en bout | ⬜ |
