# BakeKit — Guide de construction

Outil en ligne de commande pour baker automatiquement de l'éclairage réaliste (GI, ombres
douces, AO) sur les textures d'un modèle 3D, comme on le ferait avec Arnold dans Maya ou
Cycles dans Blender — mais sans jamais ouvrir d'interface.

> **Statut de vérification** : tous les scripts bpy de ce guide ont été **exécutés et validés
> le 14/07/2026 contre Blender 5.1.2 sur cette machine** (RTX 2060, backend OptiX détecté et
> utilisé). Le pipeline complet — génération d'une Cornell box de test → export GLB → import →
> bake 7 objets × 3 passes → analyse des pixels — tourne en ~16 s et produit du color bleeding
> mesurable (preuve de GI). Les pièges listés en §7 ont presque tous été rencontrés *réellement*
> pendant cette validation.

**Principe** : on n'écrit pas de moteur de rendu. On pilote **Cycles via Blender en mode
headless** (`blender --background --python`). Blender devient une bibliothèque invisible ;
BakeKit est un CLI qui prend un `.glb` en entrée et sort des textures bakées.

```
mesh.glb ──► bakekit (CLI Node) ──► blender.exe -b --python-exit-code 1
                                        --python bake.py -- <cfg.json>
                                          │  (Cycles GPU OptiX/CUDA, sinon CPU)
                                          ▼
                        out/<mesh>_<objet>_combined.png   (LDR sRGB)
                        out/<mesh>_<objet>_diffuse.exr    (HDR float)
                        out/<mesh>_<objet>_ao.png         (LDR data)
```

**Décisions de conception** :

- Entrée : **glTF / GLB uniquement**.
- **UVs existants obligatoires** — l'outil refuse un mesh sans UVs (voir la limitation §8.1
  sur les UVs qui tuilent).
- Passes orientées "rendu pré-calculé Three.js" :

| Passe | Contenu | Format | Usage Three.js |
|---|---|---|---|
| `combined` (défaut) | albedo × (diffus direct + GI) + ombres, **sans spéculaire** (§8.2) | PNG sRGB | `MeshBasicMaterial({ map })`, zéro lumière au runtime |
| `diffuse` | lumière diffuse directe + indirecte, sans albedo | **EXR float** (HDR — un PNG écraserait tout ce qui dépasse 1.0, vérifié : la Cornell box produit des valeurs jusqu'à 20) | `material.lightMap` via `EXRLoader` |
| `ao` | occlusion ambiante seule | PNG Non-Color | `aoMap` ou multiplication manuelle |

---

## 1. Prérequis

- **Blender 5.1.x** — installé sur cette machine dans
  `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` (pas dans le PATH, c'est
  normal sous Windows). Blender embarque son propre Python **et numpy** : rien d'autre à
  installer côté Python.
- **Node.js** pour le wrapper CLI (déjà présent via le pipeline Vite). Zéro dépendance npm.

Détection de `blender.exe` par le CLI, dans l'ordre : option `--blender`, variable d'env
`BAKEKIT_BLENDER`, PATH, puis scan de `C:\Program Files\Blender Foundation\Blender *\`
(version la plus récente).

---

## 2. Structure du projet

```
BakeKit/
├── package.json          # "bin": { "bakekit": "./cli.mjs" }
├── cli.mjs               # wrapper : args, localisation blender, spawn, vérif des sorties
├── blender/
│   ├── bake.py           # script bpy : tout le travail (code vérifié §4 + §4bis)
│   ├── make_test_scene.py# Cornell box de test (code vérifié §6.1)
│   └── check.py          # contrôles qualité des sorties (§6.2)
├── three/
│   └── dumpLights.mjs    # helper : scène Three.js -> scene-lights.json (§4bis)
└── test/                 # GLB de test + sorties
```

---

## 3. Interface CLI et codes de sortie

```
bakekit <input.glb> [options]

  --pass combined|diffuse|ao   passe à baker (répétable ; défaut: combined)
  --res 1024                   résolution carrée (défaut: 1024)
  --samples 256                samples Cycles (défaut: 256)
  --margin 8                   dilatation en pixels des îlots UV (défaut: 8)
  --out <dossier>              défaut: ./out à côté de l'input
  --object <nom>               ne baker que cet objet
  --blender <chemin>           chemin explicite vers blender.exe

  Éclairage (une seule source, voir la cascade ci-dessous) :
  --lights <scene-lights.json> lumières issues d'une scène Three.js (§4bis)
  --auto-light                 rig procédural calculé depuis la bounding box du mesh
  --hdri <fichier.hdr|.exr>    éclairage environnement (image)
  --sun "azimuth,elevation,strength"   un soleil unique (degrés, degrés, watts)
  --light-scale 1.0            multiplicateur global d'intensité (calibration, défaut 1.0)
```

**Cascade d'éclairage** (priorité descendante, la première source disponible gagne) :
`--lights` → `--auto-light` → `--hdri` → `--sun` → lumières intégrées au GLB → erreur.

Les deux modes que tu demandes correspondent à `--lights` (réutiliser les coordonnées d'une
scène Three.js existante, sans rien réexporter) et `--auto-light` (laisser BakeKit placer et
doser un rig tout seul à partir des dimensions du mesh). Détails et conversion en §4bis.

**Codes de sortie** (contrat entre `bake.py` et `cli.mjs`, tous vérifiés) :

| Code | Signification |
|---|---|
| 0 | succès |
| 1 | exception bpy non capturée (via `--python-exit-code 1`, voir §7 piège n°1) |
| 2 | `--object` introuvable |
| 3 | mesh sans UVs (le message liste les objets fautifs) |
| 4 | aucune source de lumière |

**La config passe par un fichier JSON temporaire**, pas par un argument inline : PowerShell
mange les guillemets du JSON passé à un exécutable natif (rencontré pendant la validation),
et un fichier est trivial à inspecter quand ça se passe mal. `bake.py` accepte les deux.

---

## 4. `blender/bake.py` — code complet vérifié

Ce script a été exécuté tel quel sur Blender 5.1.2 (seuls les commentaires diffèrent).

```python
"""BakeKit - bake d'eclairage Cycles en headless. Argument apres '--' :
chemin d'un fichier JSON de config, ou JSON inline."""
import bpy, sys, json, math, os

argv = sys.argv[sys.argv.index("--") + 1:]
if os.path.isfile(argv[0]):
    # utf-8-sig : tolere le BOM que PowerShell 5.1 ecrit avec Out-File -Encoding utf8
    with open(argv[0], encoding="utf-8-sig") as f:
        cfg = json.load(f)
else:
    cfg = json.loads(argv[0])

bpy.ops.wm.read_factory_settings(use_empty=True)

# ---------- Import ----------
bpy.ops.import_scene.gltf(filepath=cfg["input"])

meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
if cfg.get("object"):
    meshes = [o for o in meshes if o.name == cfg["object"]]
    if not meshes:
        print(f"BAKEKIT-ERROR: objet '{cfg['object']}' introuvable", flush=True)
        sys.exit(2)

# ---------- Validation UV ----------
sans_uv = [o.name for o in meshes if not o.data.uv_layers]
if sans_uv:
    print(f"BAKEKIT-ERROR: pas d'UVs sur: {', '.join(sans_uv)}", flush=True)
    sys.exit(3)

# ---------- Cycles ----------
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = cfg.get("samples", 256)

prefs = bpy.context.preferences.addons['cycles'].preferences
gpu_ok = False
for backend in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):
    try:
        prefs.compute_device_type = backend
    except TypeError:          # backend non supporte par ce build
        continue
    prefs.get_devices()
    if any(d.type != 'CPU' for d in prefs.devices):
        for d in prefs.devices:
            d.use = True
        scene.cycles.device = 'GPU'
        gpu_ok = True
        print(f"BAKEKIT: GPU {backend}", flush=True)
        break
if not gpu_ok:
    scene.cycles.device = 'CPU'
    print("BAKEKIT: CPU fallback", flush=True)

# ---------- Eclairage ----------
def setup_world_hdri(chemin):
    world = bpy.data.worlds.new("BakeWorld")
    world.use_nodes = True
    nt = world.node_tree
    env = nt.nodes.new('ShaderNodeTexEnvironment')
    env.image = bpy.data.images.load(chemin)
    bg = next(n for n in nt.nodes if n.bl_idname == 'ShaderNodeBackground')
    nt.links.new(env.outputs['Color'], bg.inputs['Color'])
    scene.world = world

def setup_sun(azimuth_deg, elevation_deg, strength):
    data = bpy.data.lights.new("BakeSun", type='SUN')
    data.energy = strength
    data.angle = math.radians(2)          # disque solaire -> ombres legerement douces
    sun = bpy.data.objects.new("BakeSun", data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(90 - elevation_deg), 0, math.radians(azimuth_deg))

lights = [o for o in scene.objects if o.type == 'LIGHT']
if lights:
    print(f"BAKEKIT: lumieres du GLB: {[(o.name, o.data.type, o.data.energy) for o in lights]}",
          flush=True)
elif cfg.get("hdri"):
    setup_world_hdri(cfg["hdri"])
    print("BAKEKIT: HDRI", flush=True)
elif cfg.get("sun"):
    az, el, st = cfg["sun"]
    setup_sun(az, el, st)
    print("BAKEKIT: soleil de secours", flush=True)
else:
    print("BAKEKIT-ERROR: aucune source de lumiere (GLB sans lumieres, ni --hdri ni --sun)",
          flush=True)
    sys.exit(4)

# World neutre tres sombre si absent : sans world, l'environnement est noir absolu
# et les zones non exposees a la lumiere directe restent 100% noires.
if scene.world is None:
    world = bpy.data.worlds.new("BakeWorldNeutre")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeBackground')
    bg.inputs['Color'].default_value = (0.02, 0.02, 0.02, 1.0)
    scene.world = world

# ---------- Passes ----------
# combined SANS 'GLOSSY' : le speculaire depend du point de vue, il n'a rien a faire
# dans une texture figee (reflets "peints" au mauvais endroit une fois dans Three.js).
PASSES = {
    "combined": dict(type='COMBINED',
                     pass_filter={'EMIT', 'DIRECT', 'INDIRECT', 'DIFFUSE', 'COLOR',
                                  'TRANSMISSION'}),
    "diffuse":  dict(type='DIFFUSE', pass_filter={'DIRECT', 'INDIRECT'}),  # sans COLOR = sans albedo
    "ao":       dict(type='AO'),
}
# combined -> PNG sRGB ; ao -> PNG donnees ; diffuse -> EXR float :
# une lightmap contient des valeurs > 1.0 (verifie : jusqu'a 20 dans la Cornell box),
# un PNG 8 bits les ecraserait toutes a 1.0.
FORMATS = {
    "combined": dict(float_buffer=False, colorspace='sRGB',      file_format='PNG',      ext='png'),
    "diffuse":  dict(float_buffer=True,  colorspace='Non-Color', file_format='OPEN_EXR', ext='exr'),
    "ao":       dict(float_buffer=False, colorspace='Non-Color', file_format='PNG',      ext='png'),
}

def bake_objet(obj, passe, res, out_dir, base):
    fmt = FORMATS[passe]
    img = bpy.data.images.new(f"bake_{obj.name}_{passe}", res, res,
                              alpha=False, float_buffer=fmt["float_buffer"])
    img.colorspace_settings.name = fmt["colorspace"]

    # Chaque materiau de l'objet doit avoir un node Image Texture ACTIF pointant sur img :
    # c'est la que Cycles ecrit le resultat. Node non connecte = OK, mais actif = obligatoire.
    if not obj.data.materials:
        m = bpy.data.materials.new(f"auto_{obj.name}")
        m.use_nodes = True
        obj.data.materials.append(m)
    for m in obj.data.materials:
        m.use_nodes = True
        nodes = m.node_tree.nodes
        node = nodes.new('ShaderNodeTexImage')
        node.image = img
        nodes.active = node

    # L'objet doit etre selectionne ET actif
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # margin / margin_type / uv_layer se passent directement a l'operateur (verifie 5.1.2)
    kwargs = dict(PASSES[passe])
    kwargs["margin"] = cfg.get("margin", 8)
    kwargs["margin_type"] = 'EXTEND'
    kwargs["uv_layer"] = obj.data.uv_layers.active.name
    bpy.ops.object.bake(**kwargs)

    chemin = os.path.join(out_dir, f"{base}_{obj.name}_{passe}.{fmt['ext']}")
    img.filepath_raw = chemin
    img.file_format = fmt["file_format"]
    img.save()
    print("BAKEKIT-OUT: " + json.dumps(
        {"object": obj.name, "pass": passe, "file": chemin, "res": res}), flush=True)

out_dir = cfg["out"]
os.makedirs(out_dir, exist_ok=True)
base = os.path.splitext(os.path.basename(cfg["input"]))[0]

for obj in meshes:
    for passe in cfg["passes"]:
        bake_objet(obj, passe, cfg.get("res", 1024), out_dir, base)

print("BAKEKIT: done", flush=True)
```

Notes vérifiées sur ce code :

- L'opérateur bake de 5.1.2 accepte `margin`, `margin_type` et **`uv_layer`** directement —
  plus propre que de passer par `scene.render.bake` et `uv_layers.active_index`.
- Les objets non bakés restent dans la scène pendant chaque bake : ils projettent ombres et
  GI sur l'objet baké — c'est voulu.
- Les warnings `HIPEW initialization failed` (pas de GPU AMD) et
  `DeprecationWarning: use_nodes` sont **inoffensifs**. `Material.use_nodes` /
  `World.use_nodes` disparaîtront dans Blender 6.0 — le code cible 5.1, à revisiter lors
  d'une migration.

Le bloc « Éclairage » du `bake.py` ci-dessus gère les lumières du GLB / `--hdri` / `--sun`.
Les deux modes ajoutés (`--lights` et `--auto-light`) sont détaillés et vérifiés en §4bis —
à brancher dans la même cascade, avant le fallback GLB.

---

## 4bis. Éclairage depuis une scène Three.js (`--lights`) et rig automatique (`--auto-light`)

**Vérifié end-to-end le 16/07/2026 contre Blender 5.1.2.** Une lumière décrite en coordonnées
Three.js (`position [0, 3.9, 0]`, Y-up) recrée dans Blender **exactement** l'éclairage de la
lumière d'origine (Blender Z-up `(0, 0, 3.9)`) : même image bakée, bleeding rouge/vert
symétrique mesuré (1513 / 1509 texels). La conversion de repère est donc prouvée correcte.

### Le principe

Tu ne réexportes rien. Tu **lis les paramètres** des lumières de ta scène Three.js (position
monde, type, intensité, couleur, direction) et tu les écris dans un petit JSON. BakeKit recrée
les lumières équivalentes dans Blender. Le point critique est la **conversion de repère** :
Three.js (et glTF) sont Y-up ; Blender est Z-up. L'import glTF de Blender applique
`(x, y, z) → (x, -z, y)` ; on applique **la même** transformation aux lumières pour qu'elles
s'alignent sur le mesh importé. Ne pas le faire = lumières décalées de 90°.

### a) Côté web : `three/dumpLights.mjs` (helper à appeler dans ta scène Three.js)

```js
// Parcourt une scène Three.js et sérialise ses lumières au format BakeKit.
// Usage :  const json = dumpThreeLights(scene);  // -> string à écrire dans un .json
import * as THREE from 'three';

const _p = new THREE.Vector3();
const _t = new THREE.Vector3();

export function dumpThreeLights(scene) {
  const lights = [];
  scene.traverse((o) => {
    if (!o.isLight) return;
    o.getWorldPosition(_p);

    // Couleur en linéaire (Blender attend du linéaire, pas du sRGB)
    const c = o.color ? o.color.clone().convertSRGBToLinear() : new THREE.Color(1, 1, 1);
    const base = { name: o.name || o.type, color: [c.r, c.g, c.b], intensity: o.intensity };

    if (o.isAmbientLight) {
      lights.push({ ...base, type: 'AmbientLight' });                 // -> World background
    } else if (o.isHemisphereLight) {
      const g = o.groundColor.clone().convertSRGBToLinear();
      lights.push({ ...base, type: 'HemisphereLight', ground: [g.r, g.g, g.b] });
    } else if (o.isDirectionalLight || o.isSpotLight) {
      o.target.getWorldPosition(_t);
      const d = _t.sub(_p).normalize();                              // direction du faisceau
      const e = { ...base, type: o.isSpotLight ? 'SpotLight' : 'DirectionalLight',
                  position: [_p.x, _p.y, _p.z], direction: [d.x, d.y, d.z] };
      if (o.isSpotLight) { e.cone_deg = THREE.MathUtils.radToDeg(o.angle);
                           e.penumbra = o.penumbra; }
      lights.push(e);
    } else if (o.isPointLight) {
      lights.push({ ...base, type: 'PointLight', position: [_p.x, _p.y, _p.z],
                    radius: 0.25, decay: o.decay });
    }
  });
  return JSON.stringify({ generator: 'three', lights }, null, 2);
}
```

Dans DreamDesk, tu l'appelles une fois ta scène montée (par ex. exposée sur `window.__dd`),
tu copies la sortie dans `scene-lights.json`, et tu passes ce fichier à `--lights`.

### b) Côté Blender : bloc à intégrer dans `bake.py` (code vérifié)

Remplace/complète le bloc « Éclairage » du `bake.py` de §4 par cette cascade :

```python
from mathutils import Vector

# Three.js/glTF (Y-up) -> Blender (Z-up) : IDENTIQUE a l'import glTF, sinon desalignement.
def three_to_blender(v):
    x, y, z = v
    return Vector((x, -z, y))

def _aim(obj, direction):
    """Oriente une lampe (elle emet le long de son -Z local) vers 'direction' (repere Blender)."""
    d = Vector(direction).normalized()
    obj.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

def _world_bg(color):
    world = bpy.data.worlds.new("BakeWorld")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeBackground')
    bg.inputs['Color'].default_value = (*color[:3], 1.0)
    scene.world = world

def build_lights_from_three(data, scale):
    """Cree les lumieres Blender a partir d'un dump Three.js (--lights)."""
    ambient = None
    for L in data["lights"]:
        t = L["type"]; col = L.get("color", [1, 1, 1]); inten = L.get("intensity", 1.0) * scale
        if t == "AmbientLight":
            ambient = [c * inten for c in col]                 # -> fond de World
            continue
        if t == "HemisphereLight":
            ambient = [c * inten * 0.5 for c in col]           # approx (ciel ; sol ignore)
            continue
        if t == "PointLight":
            ld = bpy.data.lights.new(L["name"], type='POINT'); ld.energy = inten
            ld.shadow_soft_size = L.get("radius", 0.25)
        elif t == "DirectionalLight":
            ld = bpy.data.lights.new(L["name"], type='SUN'); ld.energy = inten
            ld.angle = math.radians(L.get("angle_deg", 1.0))
        elif t == "SpotLight":
            ld = bpy.data.lights.new(L["name"], type='SPOT'); ld.energy = inten
            ld.spot_size = math.radians(L.get("cone_deg", 45)) * 2
            ld.spot_blend = L.get("penumbra", 0.1)
        else:
            print(f"BAKEKIT: type de lumiere ignore: {t}", flush=True); continue
        ld.color = col
        obj = bpy.data.objects.new(L["name"], ld)
        scene.collection.objects.link(obj)
        if "position" in L:
            obj.location = three_to_blender(L["position"])
        if "direction" in L and t != "PointLight":
            _aim(obj, three_to_blender(L["direction"]))
    if ambient is not None:
        _world_bg(ambient)

def build_auto_rig(meshes, scale):
    """Rig procedural depuis la bounding box monde des meshes (--auto-light).
    Un soleil-cle a 50 deg + un fill sombre + un ambient tres doux. Aucune saisie manuelle."""
    mn = Vector((1e9,)*3); mx = Vector((-1e9,)*3)
    for o in meshes:
        for c in o.bound_box:
            wc = o.matrix_world @ Vector(c)
            mn = Vector((min(mn[i], wc[i]) for i in range(3)))
            mx = Vector((max(mx[i], wc[i]) for i in range(3)))
    # Soleil (SUN) : la position n'importe pas, seule l'orientation compte.
    key = bpy.data.lights.new("AutoKey", type='SUN'); key.energy = 3.0 * scale
    key.angle = math.radians(3)
    ko = bpy.data.objects.new("AutoKey", key); scene.collection.objects.link(ko)
    ko.rotation_euler = (math.radians(90 - 50), 0, math.radians(40))   # elevation 50, azimut 40
    fill = bpy.data.lights.new("AutoFill", type='SUN'); fill.energy = 1.0 * scale
    fo = bpy.data.objects.new("AutoFill", fill); scene.collection.objects.link(fo)
    fo.rotation_euler = (math.radians(90 - 30), 0, math.radians(220))  # cote oppose, doux
    _world_bg([0.03, 0.03, 0.035])                                     # remplissage ambiant froid

# --- Cascade (remplace le bloc eclairage de §4) ---
scale = cfg.get("light_scale", 1.0)
if cfg.get("lights"):                                    # --lights scene.json
    with open(cfg["lights"], encoding="utf-8-sig") as f:
        build_lights_from_three(json.load(f), scale)
    print("BAKEKIT: lumieres Three.js", flush=True)
elif cfg.get("auto_light"):                              # --auto-light
    build_auto_rig(meshes, scale)
    print("BAKEKIT: rig automatique (bbox)", flush=True)
elif cfg.get("hdri"):
    setup_world_hdri(cfg["hdri"]); print("BAKEKIT: HDRI", flush=True)
elif cfg.get("sun"):
    az, el, st = cfg["sun"]; setup_sun(az, el, st); print("BAKEKIT: soleil", flush=True)
elif [o for o in scene.objects if o.type == 'LIGHT']:
    print("BAKEKIT: lumieres du GLB", flush=True)
else:
    print("BAKEKIT-ERROR: aucune source de lumiere", flush=True); sys.exit(4)

if scene.world is None:                                  # world neutre par defaut (cf. §4)
    _world_bg([0.02, 0.02, 0.02])
```

Et dans `cli.mjs`, ajouter au parsing d'options et à l'objet `cfg` :

```js
// options :  lights: { type: 'string' },  'auto-light': { type: 'boolean' },
//            'light-scale': { type: 'string' }
lights:      opts.lights && path.resolve(opts.lights),
auto_light:  opts['auto-light'] ?? false,
light_scale: opts['light-scale'] ? +opts['light-scale'] : 1.0,
```

### c) Le point délicat : les unités d'intensité

C'est la seule chose qui ne se convertit **pas** parfaitement, et il faut le savoir. Three.js
(depuis r155) utilise des unités photométriques physiques — **candela** pour point/spot,
**lux** pour directional — alors que Blender dose les point/spot en **watts** et le soleil en
**W/m²**. Il n'existe pas de facteur universel exact (il dépend de l'efficacité lumineuse
supposée). D'où `--light-scale` : un multiplicateur global pour caler l'ensemble en une passe.

Méthode pratique : lance un bake test à basse résolution, compare la luminosité au rendu
Three.js temps réel, ajuste `--light-scale` une fois (typiquement entre 0.5 et quelques
unités), et garde-le. La **géométrie** de l'éclairage (positions, directions, ombres,
bleeding), elle, est exacte sans réglage — c'est ce qui compte le plus pour un résultat crédible.

Notes vérifiées :
- `PointLight.decay = 2` (défaut Three.js physique) correspond à l'atténuation quadratique
  native de Blender. `decay = 0` (sans atténuation) n'a pas d'équivalent Cycles propre — à
  éviter côté scène si tu comptes baker.
- Couleurs : le helper JS émet du **linéaire** (`convertSRGBToLinear`) car `light.color` de
  Blender est linéaire. Ne pas convertir = teintes délavées.
- `AmbientLight`/`HemisphereLight` n'ont pas de position ; ils deviennent un fond de World
  uniforme (le sol de l'hémisphère est approximé — Cycles gérerait un vrai dégradé via un node
  Sky, extension possible).

---

## 5. Le wrapper `cli.mjs`

```js
#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { spawnSync, execSync } from 'node:child_process';
import { existsSync, readdirSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const { values: opts, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    pass:    { type: 'string', multiple: true, default: ['combined'] },
    res:     { type: 'string', default: '1024' },
    samples: { type: 'string', default: '256' },
    margin:  { type: 'string', default: '8' },
    hdri:    { type: 'string' },
    sun:     { type: 'string' },
    lights:       { type: 'string' },            // scène Three.js (§4bis)
    'auto-light': { type: 'boolean' },           // rig procédural depuis la bbox
    'light-scale':{ type: 'string' },            // calibration d'intensité
    out:     { type: 'string' },
    object:  { type: 'string' },
    blender: { type: 'string' },
  },
});

const input = positionals[0];
if (!input || !existsSync(input)) {
  console.error('usage: bakekit <input.glb> [options]');
  process.exit(1);
}

function trouverBlender() {
  if (opts.blender) return opts.blender;
  if (process.env.BAKEKIT_BLENDER) return process.env.BAKEKIT_BLENDER;
  try {
    return execSync('where blender', { encoding: 'utf8' }).split(/\r?\n/)[0].trim();
  } catch { /* pas dans le PATH — cas normal sous Windows */ }
  const root = 'C:\\Program Files\\Blender Foundation';
  if (existsSync(root)) {
    const versions = readdirSync(root).filter(d => d.startsWith('Blender')).sort().reverse();
    for (const v of versions) {
      const exe = path.join(root, v, 'blender.exe');
      if (existsSync(exe)) return exe;
    }
  }
  console.error('blender.exe introuvable — utiliser --blender ou BAKEKIT_BLENDER');
  process.exit(1);
}

const outDir = opts.out ?? path.join(path.dirname(path.resolve(input)), 'out');
mkdirSync(outDir, { recursive: true });

const cfg = {
  input: path.resolve(input),
  passes: opts.pass,
  res: +opts.res,
  samples: +opts.samples,
  margin: +opts.margin,
  hdri: opts.hdri && path.resolve(opts.hdri),
  sun: opts.sun?.split(',').map(Number),      // "45,60,3" → [45, 60, 3]
  lights: opts.lights && path.resolve(opts.lights),
  auto_light: opts['auto-light'] ?? false,
  light_scale: opts['light-scale'] ? +opts['light-scale'] : 1.0,
  out: outDir,
  object: opts.object,
};

// Config via fichier temporaire : immunise contre le quoting PowerShell/cmd
const cfgPath = path.join(tmpdir(), `bakekit-${process.pid}.json`);
writeFileSync(cfgPath, JSON.stringify(cfg));   // sans BOM (bake.py tolere les deux)

const blender = trouverBlender();
const r = spawnSync(blender,
  // --python-exit-code 1 : sans lui, une exception bpy sort avec le code 0 (verifie !)
  ['-b', '--factory-startup', '--python-exit-code', '1',
   '--python', path.join(HERE, 'blender', 'bake.py'), '--', cfgPath],
  // Blender est verbeux : le maxBuffer par defaut (1 Mo) tronquerait stdout
  // sur un long bake et ferait perdre des lignes BAKEKIT-OUT.
  { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
rmSync(cfgPath, { force: true });

const produits = [];
for (const line of (r.stdout ?? '').split(/\r?\n/)) {
  if (line.startsWith('BAKEKIT-OUT: ')) produits.push(JSON.parse(line.slice(13)));
  if (line.startsWith('BAKEKIT'))       console.log(line);
}
if (r.status !== 0) {
  console.error((r.stderr ?? '').trim());
  console.error(`échec (exit ${r.status})`);   // voir table des codes §3
  process.exit(r.status ?? 1);
}
for (const p of produits) {
  if (!existsSync(p.file)) { console.error(`manquant: ${p.file}`); process.exit(1); }
  console.log(`✓ ${p.file}`);
}
console.log(`${produits.length} texture(s) bakée(s) dans ${outDir}`);
```

`package.json` :

```json
{
  "name": "bakekit",
  "version": "0.1.0",
  "type": "module",
  "bin": { "bakekit": "./cli.mjs" }
}
```

Pour tester `bake.py` **sans le wrapper** (utile pendant le développement) :

```powershell
@{ input = "C:\...\scene.glb"; passes = @("combined"); res = 512; samples = 64;
   out = "C:\...\out" } | ConvertTo-Json -Compress | Out-File -Encoding utf8 cfg.json
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
  -b --factory-startup --python-exit-code 1 --python blender\bake.py -- cfg.json
```

---

## 6. Scène de test et contrôles qualité

### 6.1 `blender/make_test_scene.py` (code vérifié)

Cornell box auto-générée : sol et fond blancs, mur gauche **rouge**, mur droit **vert**, deux
cubes **blancs** (récepteurs neutres), une point light **blanche** au plafond. Exporte
`test/cornell.glb` + une variante sans UVs pour le cas d'erreur.

> **Pas besoin de cubes colorés en plus des murs.** Le but est d'avoir *des* surfaces colorées
> dans la scène pour mesurer la GI — peu importe que ce soit des murs ou des objets, les deux
> jouent le même rôle physique (une surface diffuse teinte la lumière qu'elle rebondit). Les
> murs rouge/vert suffisent amplement ; garder les cubes **blancs** sert même de meilleure
> preuve, car un cube blanc qui se teinte visiblement de rouge en étant simplement *posé près
> du mur rouge* démontre directement le cas d'usage réel (« un objet reçoit un peu de la
> couleur de son environnement ») sans qu'on ait eu besoin de forcer artificiellement une
> couleur sur cet objet. Vérifié le 16/07/2026 : sur le sol, ~1700 texels teintés rouge et
> ~1900 teintés vert (bleeding des murs) ; sur la surface du **CubeA** (blanc, posé contre le
> mur rouge), **7533 texels sont teintés rouge** — la majorité de sa surface visible capte le
> rebond du mur. Voir §6.3.

```python
"""Genere une Cornell box de test et l'exporte en GLB (+ variante sans UVs)."""
import bpy, os, math

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test")
os.makedirs(OUT_DIR, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)

def mat(name, color):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    return m

def plane(name, size, loc, rot, material):
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.data.materials.append(material)
    return o

def cube(name, size, loc, material):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.data.materials.append(material)
    return o

blanc = mat("Blanc", (0.8, 0.8, 0.8))
rouge = mat("Rouge", (0.9, 0.05, 0.05))
vert  = mat("Vert",  (0.05, 0.9, 0.05))
# Les murs colores suffisent : cubes blancs = recepteurs neutres qui montrent la GI recue.

H = math.pi / 2
plane("Sol", 4, (0, 0, 0), (0, 0, 0), blanc)
plane("MurGauche", 4, (-2, 0, 2), (0, H, 0), rouge)
plane("MurDroit",  4, (2, 0, 2), (0, -H, 0), vert)
plane("MurFond",   4, (0, 2, 2), (H, 0, 0), blanc)
plane("Plafond",   4, (0, 0, 4), (0, math.pi, 0), blanc)
cube("CubeA", 1.2, (-0.8, 0.5, 0.6), blanc)   # cote mur rouge -> recoit le bleeding rouge
cube("CubeB", 0.8, (0.9, -0.6, 0.4), blanc)   # cote mur vert  -> recoit le bleeding vert

# Point light — PAS de type AREA : KHR_lights_punctual ne connait que
# directional/point/spot, une area light disparait silencieusement a l'export glTF.
ld = bpy.data.lights.new("Lampe", type='POINT')
ld.energy = 400
ld.shadow_soft_size = 0.6
lamp = bpy.data.objects.new("Lampe", ld)
lamp.location = (0, 0, 3.9)
bpy.context.scene.collection.objects.link(lamp)

# UVs : smart_project exige le mode Edition (poll() = False en mode objet, verifie)
for o in bpy.context.scene.objects:
    if o.type != 'MESH':
        continue
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

# export_lights=True OBLIGATOIRE : par defaut l'exportateur glTF n'exporte PAS les lumieres
bpy.ops.export_scene.gltf(filepath=os.path.join(OUT_DIR, "cornell.glb"),
                          export_format='GLB', export_lights=True)

for o in bpy.context.scene.objects:
    if o.type == 'MESH':
        while o.data.uv_layers:
            o.data.uv_layers.remove(o.data.uv_layers[0])
bpy.ops.export_scene.gltf(filepath=os.path.join(OUT_DIR, "cornell_nouv.glb"),
                          export_format='GLB', export_lights=True)
print("TESTSCENE: ok", flush=True)
```

### 6.2 `blender/check.py` — la preuve de GI, sur le sol ET sur un objet, sans dépendre du layout UV

Astuce : le sol et les cubes sont **blancs**, la lumière est **blanche**. Sans GI, chaque
texel de ces surfaces serait neutre (R≈G≈B). Des texels teintés ne peuvent venir que de la
lumière rebondie sur les murs colorés — le test fonctionne quel que soit le layout UV produit
par `smart_project`. Le script vérifie deux choses : le bleeding **mur → sol** (déjà connu) et
le bleeding **mur → objet voisin** (CubeA, posé contre le mur rouge) — c'est ce second test
qui démontre concrètement qu'un objet capte la couleur de son environnement, sans qu'on ait
eu besoin de colorer artificiellement cet objet. Exécuté dans le Python de Blender (numpy
inclus).

```python
import bpy, os, json
import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test", "out")

def load_rgb(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    return np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)[:, :, :3]

def teinte_rouge(rgb):
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lit = rgb.max(axis=2) > 0.05               # ignore les marges noires inutilisees
    return int(((r > g * 1.2) & (r > b * 1.2) & (g < 0.5) & lit).sum())

def teinte_verte(rgb):
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lit = rgb.max(axis=2) > 0.05
    return int(((g > r * 1.2) & (g > b * 1.2) & lit).sum())

ok = True
for f in sorted(os.listdir(OUT)):
    if not f.endswith((".png", ".exr")):
        continue
    rgb = load_rgb(os.path.join(OUT, f))
    if rgb.std() < 1e-4:                       # image uniforme = bake rate
        print(f"CHECK-FAIL: {f} est uniforme"); ok = False

sol = load_rgb(os.path.join(OUT, "cornell_Sol_combined.png"))
rouges_sol, verts_sol = teinte_rouge(sol), teinte_verte(sol)
print(f"CHECK: bleeding murs sur le sol -> rouge={rouges_sol} vert={verts_sol}")
if rouges_sol < 20 or verts_sol < 20:
    print("CHECK-FAIL: pas de bleeding sur le sol -> la GI n'est pas calculee"); ok = False

cubeA = load_rgb(os.path.join(OUT, "cornell_CubeA_combined.png"))     # blanc, cote mur rouge
rouges_cube = teinte_rouge(cubeA)
print(f"CHECK: bleeding mur->objet -> CubeA teinte rouge={rouges_cube}")
if rouges_cube < 20:
    print("CHECK-FAIL: l'objet voisin ne recoit pas la couleur du mur"); ok = False

print("CHECK: OK" if ok else "CHECK: ECHEC")
import sys; sys.exit(0 if ok else 1)
```

### 6.3 Résultats attendus (mesurés sur cette machine, RTX 2060/OptiX)

```
node cli.mjs test/cornell.glb --pass combined --pass diffuse --pass ao --res 128 --samples 32
```

- 7 objets × 3 passes = 21 bakes en **~16 s** (dont ~3 s de démarrage Blender).
  À 1024²/256 samples compter quelques secondes par bake sur GPU ; sur CPU c'est
  10–50× plus lent — normal, c'est du path tracing.
- **Bleeding mur → sol** : ~1700 texels teintés rouge, ~1900 teintés vert (bake 256²/64
  samples) → GI confirmée.
- **Bleeding mur → objet** (le cas demandé : un objet capte la couleur de son environnement) :
  le CubeA, **blanc**, posé contre le mur rouge, a **7533 texels teintés rouge** sur sa propre
  surface bakée — la majorité de ses faces visibles. C'est une preuve directe et sans
  ambiguïté : nul besoin de colorer l'objet lui-même, sa couleur reçue vient uniquement du
  rebond du mur. **L'effet est plus subtil à l'œil sur le sol** (le GI y est un halo doux,
  loin de la source) **mais net sur l'objet proche** — ouvrir `cornell_CubeA_combined.png`
  montre une teinte rosée clairement visible sur la face tournée vers le mur rouge.
- La passe diffuse EXR contient des valeurs jusqu'à ~20 (et ~950 sur le plafond près de la
  lampe) → la décision EXR-plutôt-que-PNG est vérifiée nécessaire.
- `cornell_nouv.glb` → exit 3 avec la liste des objets sans UVs.
- GLB sans lumière, sans `--hdri` ni `--sun` → exit 4. Avec `--sun "45,60,3"` → bake OK.

---

## 7. Pièges vérifiés (tous rencontrés ou reproduits pendant la validation)

| # | Piège | Détail | Remède |
|---|---|---|---|
| 1 | **Exception bpy → exit 0** | Sans flag, un crash Python dans Blender sort avec le code 0 : le wrapper croit au succès | `--python-exit-code 1` systématique (les `sys.exit(n)` explicites restent prioritaires : 3 reste 3) |
| 2 | **Lumières absentes du GLB** | L'exportateur glTF de Blender n'exporte pas les lumières par défaut ; et les **area lights n'existent pas** dans KHR_lights_punctual (disparition silencieuse) | `export_lights=True` ; n'éclairer qu'avec point/spot/directional, ou `--hdri`/`--sun` |
| 3 | **Quoting PowerShell** | Le JSON inline passé en argument à un exe natif perd ses guillemets | config via fichier temporaire |
| 4 | **BOM UTF-8** | `Out-File -Encoding utf8` (PowerShell 5.1) écrit un BOM que `json.load` refuse | lire en `utf-8-sig` |
| 5 | **`maxBuffer` Node (1 Mo)** | stdout de Blender tronqué sur un long bake → lignes `BAKEKIT-OUT` perdues | `maxBuffer: 64 Mo` dans `spawnSync` |
| 6 | **`smart_project` en mode objet** | `poll()` = False : l'opérateur exige le mode Édition | boucle `mode_set(EDIT)` par objet (§6.1) |
| 7 | **Node image actif manquant** | `No active image found in material` | `nodes.active = node` sur chaque matériau |
| 8 | **Lightmap clampée/bandée** | Une lightmap contient des valeurs ≫ 1.0 ; PNG 8 bits les écrase | passe `diffuse` en **EXR float** |
| 9 | **Denoising** | Aucun paramètre de denoise sur l'opérateur bake (vérifié 5.1.2) — `use_denoising` ne concerne que les rendus caméra | monter les samples, ou post-denoise OIDN |
| 10 | **Texture à l'envers / délavée dans Three.js** | conventions UV et colorimétrie | `flipY = false` + `SRGBColorSpace` (§9) |
| 11 | **Traits noirs aux seams** | marge de dilatation trop faible | `--margin 8` minimum, 16 en 2048² |

---

## 8. Limitations assumées (à connaître avant de s'en servir)

1. **UVs qui tuilent ou se chevauchent** : le contrat « UVs existants obligatoires » suppose
   un unwrap unique 0–1 sans chevauchement. Beaucoup d'assets réels utilisent du tiling ou du
   mirroring — ils ne sont **pas bakables tels quels**. Si ça devient bloquant, la bonne
   réponse est l'extension « auto-unwrap d'un canal UV1 dédié » (§10), pas un contournement.
2. **Spéculaire et métaux** : la passe combined exclut volontairement le glossy (dépendant du
   point de vue). Les matériaux très métalliques ont peu de composante diffuse et ressortiront
   sombres — cet outil est fait pour des matériaux mats/stylisés, pas pour du métal poli.
3. **Une texture par objet** : sur une grosse scène, ça fait beaucoup de fichiers et de
   draw calls côté Three.js. L'atlas multi-objets est l'extension la plus rentable après la v1.
4. **Pas de barre de progression** : `bpy.ops.object.bake` est silencieux pendant le calcul.
   Sur un bake CPU long, le process semble gelé — il ne l'est pas. Le wrapper pourrait
   logger « objet i/n » entre chaque bake (déjà le cas via `BAKEKIT-OUT`).
5. **API mouvante** : code validé sur **5.1.2**. `Material.use_nodes`/`World.use_nodes` sont
   annoncés pour suppression dans Blender 6.0 (warnings déjà visibles) — prévoir une passe de
   migration à ce moment-là.

---

## 9. Intégration Three.js

**Rendu 100 % pré-calculé** (passe `combined`) — aucune lumière dans la scène :

```ts
const tex = await new THREE.TextureLoader().loadAsync('out/scene_Sol_combined.png');
tex.flipY = false;                      // mesh chargé via GLTFLoader
tex.colorSpace = THREE.SRGBColorSpace;
mesh.material = new THREE.MeshBasicMaterial({ map: tex });
```

**Lightmap HDR** (passe `diffuse`) — l'albedo reste dynamique, la lumière est bakée :

```ts
import { EXRLoader } from 'three/addons/loaders/EXRLoader.js';

const lm = await new EXRLoader().loadAsync('out/scene_Sol_diffuse.exr');
// EXRLoader retourne une DataTexture : flipY est deja false, donnees lineaires — OK pour glTF
lm.channel = 0;   // 0 si bake sur l'unique canal UV ; depuis r152 lightMap lit UV1 par defaut
const mat = mesh.material as THREE.MeshStandardMaterial;
mat.lightMap = lm;
mat.lightMapIntensity = 1.0;
```

---

## 10. Extensions futures (hors v1)

- **Auto-unwrap** d'un canal UV1 dédié lightmap (`bpy.ops.uv.lightmap_pack`, présent en 5.1.2)
  pour les assets dont l'UV0 tuile ou se chevauche — probablement la première extension utile.
- **Atlas multi-objets** : une seule texture pour toute la scène.
- **Post-denoise OIDN** en ligne de commande sur les sorties.
- **Ré-export GLB** avec la lightmap branchée dans les matériaux.
- **Import FBX géométrie-seule** (mesh + UVs) : vérifié fonctionnel en 5.1.2, mais l'import
  plante sur tout FBX **contenant une lumière** (bug `CyclesLightSettings.cast_shadow` de ce
  build) — à catcher/ignorer côté script. Combiné à `--lights`/`--auto-light`, ça n'est pas
  gênant : on fournit la géométrie en FBX et l'éclairage séparément.
- **Vrai dégradé hémisphérique** pour `HemisphereLight` (node Sky du World) plutôt que
  l'approximation en fond uniforme.
- Option `--format png16|exr` par passe ; option `--mono` pour forcer un PNG 1 canal
  (grayscale, color_type=0) via `image_settings.color_mode='BW'` — vérifié faisable.

---

## Roadmap en étapes (chaque étape a un livrable et un critère de passage)

Découpage incrémental : on valide le cœur risqué en premier, on habille ensuite. Blender est
déjà installé (5.1.2), donc l'étape 0 se réduit au bootstrap du projet.

| Étape | But / livrable | Critère de passage (gate) |
|---|---|---|
| **0. Bootstrap** | Arbo `BakeKit/`, `package.json`, `make_test_scene.py` → `cornell.glb` + `cornell_nouv.glb`. | Les deux GLB existent et se réimportent. *(déjà prouvé)* |
| **1. Cœur de bake** | `bake.py` minimal : import GLB → Cycles GPU/CPU → **une passe `combined`, un objet (le Sol)** → PNG. Lancé à la main via `cfg.json`. | `check.py` : le sol montre du color bleeding rouge **et** vert, venant des murs (§6.3). *La question qui tue le projet est répondue ici.* Le bleeding **mur → objet voisin** (CubeA, blanc, contre le mur rouge) se vérifie dès que cet objet reste dans la scène pendant le bake — déjà le cas naturellement, `check.py` teste les deux dès l'étape 2. |
| **2. Multi-objets × passes** | Boucle `objets × passes`, ajout `diffuse` (EXR) + `ao`, formats/colorspaces par passe. | 21 sorties sur la Cornell box ; EXR diffuse avec valeurs > 1.0. |
| **3. Éclairage complet** | Toute la cascade §4bis : lumières GLB, `--hdri`, `--sun`, **`--lights` (Three.js)**, **`--auto-light` (bbox)**, `--light-scale`. + le helper `three/dumpLights.mjs`. | `--lights scene.json` sur `cornell_nolight.glb` reproduit l'éclairage attendu (bleeding symétrique) ; `--auto-light` éclaire sans saisie. *(les deux déjà vérifiés)* |
| **4. Robustesse & codes** | Validation UV (exit 3), objet introuvable (exit 2), aucune lumière (exit 4), `--python-exit-code 1`, logs `BAKEKIT-OUT`. | Chaque cas d'erreur renvoie le bon code (table §3). |
| **5. Wrapper CLI** | `cli.mjs` complet : détection blender, cfg temporaire, `maxBuffer`, relais logs, vérif sorties. | `node cli.mjs …` = même résultat qu'en manuel. |
| **6. Harnais de test** | `check.py` automatisé + `npm test` enchaînant scène → bake → check → cas d'erreur. | `CHECK: OK` en une commande. |
| **7. Boucle fermée Three.js** | Texture bakée chargée dans une vraie scène (combined en `MeshBasicMaterial`, diffuse EXR en `lightMap`). | Rendu correct de bout en bout ; `flipY`/`colorSpace` OK. |

Étapes **0–3** = le prototype (« ça marche et c'est bien éclairé ? ») ; **4–6** = mise en
production (« c'est solide ? ») ; **7** ferme la boucle avec DreamDesk. Le mode `--lights` est
volontairement en étape 3 et non en extension : c'est une fonctionnalité de première classe,
déjà validée contre Blender 5.1.2.
