"""BakeKit - controles qualite des sorties de bake (etape 6).
Verifie : aucune image uniforme (bake rate), bleeding mur->sol, bleeding mur->objet voisin.
Exit 0 si tout est correct, 1 sinon. Execute dans le Python de Blender (numpy inclus)."""
import bpy, os, sys
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
sys.exit(0 if ok else 1)
