"""BakeKit - bake d'eclairage Cycles en headless (etape 1 : preuve de GI).
Argument apres '--' : chemin d'un fichier JSON de config, ou JSON inline.
Reduit au strict necessaire : import GLB, device Cycles, bake 'combined' du Sol, PNG."""
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

sol = bpy.context.scene.objects["Sol"]

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

# ---------- Bake combined du Sol ----------
res = cfg.get("res", 1024)
img = bpy.data.images.new("bake_Sol_combined", res, res, alpha=False, float_buffer=False)
img.colorspace_settings.name = 'sRGB'

# Chaque materiau doit avoir un node Image Texture ACTIF pointant sur img :
# c'est la que Cycles ecrit le resultat. Node non connecte = OK, mais actif = obligatoire.
for m in sol.data.materials:
    m.use_nodes = True
    nodes = m.node_tree.nodes
    node = nodes.new('ShaderNodeTexImage')
    node.image = img
    nodes.active = node

bpy.ops.object.select_all(action='DESELECT')
sol.select_set(True)
bpy.context.view_layer.objects.active = sol

bpy.ops.object.bake(type='COMBINED',
                    pass_filter={'EMIT', 'DIRECT', 'INDIRECT', 'DIFFUSE', 'COLOR', 'TRANSMISSION'},
                    margin=cfg.get("margin", 8),
                    margin_type='EXTEND',
                    uv_layer=sol.data.uv_layers.active.name)

out_dir = cfg["out"]
os.makedirs(out_dir, exist_ok=True)
base = os.path.splitext(os.path.basename(cfg["input"]))[0]
chemin = os.path.join(out_dir, f"{base}_Sol_combined.png")
img.filepath_raw = chemin
img.file_format = 'PNG'
img.save()
print("BAKEKIT-OUT: " + json.dumps({"object": "Sol", "pass": "combined", "file": chemin, "res": res}),
      flush=True)
print("BAKEKIT: done", flush=True)
