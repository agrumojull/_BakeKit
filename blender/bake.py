"""BakeKit - bake d'eclairage Cycles en headless (etape 2 : multi-objets x passes).
Argument apres '--' : chemin d'un fichier JSON de config, ou JSON inline."""
import bpy, sys, json, os

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
