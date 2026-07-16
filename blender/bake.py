"""BakeKit - bake d'eclairage Cycles en headless (etape 3 : cascade d'eclairage complete).
Argument apres '--' : chemin d'un fichier JSON de config, ou JSON inline."""
import bpy, sys, json, math, os
from mathutils import Vector

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

# --- Cascade : --lights -> --auto-light -> --hdri -> --sun -> lumieres du GLB -> erreur ---
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

# World neutre tres sombre si toujours absent : sans world, l'environnement est noir absolu
# et les zones non exposees a la lumiere directe restent 100% noires.
if scene.world is None:
    _world_bg([0.02, 0.02, 0.02])

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
