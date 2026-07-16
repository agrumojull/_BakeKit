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
