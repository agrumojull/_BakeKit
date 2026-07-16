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
