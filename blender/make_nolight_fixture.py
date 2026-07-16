"""BakeKit - fixture de test pour le harnais automatise (etape 6).
Reimporte cornell.glb, retire ses lumieres, reexporte dans test/tmp/cornell_nolight.glb.
Sert uniquement a declencher le cas d'erreur 'aucune source de lumiere' (exit 4)."""
import bpy, os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TMP = os.path.join(ROOT, "test", "tmp")
os.makedirs(TMP, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.path.join(ROOT, "test", "cornell.glb"))

for o in list(bpy.context.scene.objects):
    if o.type == 'LIGHT':
        bpy.data.objects.remove(o, do_unlink=True)

bpy.ops.export_scene.gltf(filepath=os.path.join(TMP, "cornell_nolight.glb"),
                          export_format='GLB', export_lights=True)
print("NOLIGHT-FIXTURE: ok", flush=True)
