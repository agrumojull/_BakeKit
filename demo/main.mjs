// BakeKit - démo d'intégration Three.js (étape 7).
// Chemin A : passe combined -> MeshBasicMaterial, zéro lumière runtime.
// Chemin B : passe diffuse (EXR) -> MeshStandardMaterial.lightMap, albedo dynamique.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { EXRLoader } from 'three/addons/loaders/EXRLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const statusEl = document.getElementById('status');
const log = (msg) => { statusEl.textContent += `\n${msg}`; console.log(msg); };

const GLB = '/test/cornell.glb';
const OUT = '/test/out';

function makeRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setSize(canvas.width, canvas.height, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  return renderer;
}

function makeCamera(canvas) {
  const cam = new THREE.PerspectiveCamera(75, canvas.width / canvas.height, 0.1, 100);
  cam.position.set(0, 1.6, 2.2);   // à l'intérieur de la box, vue d'ensemble sur les murs colorés
  cam.lookAt(0, 1.3, -1.2);
  return cam;
}

// ---------- Chemin A : rendu 100% pré-calculé, zéro lumière ----------
async function buildSceneA() {
  const canvas = document.getElementById('canvasA');
  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = makeCamera(canvas);
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 1, 0);

  const gltf = await new GLTFLoader().loadAsync(GLB);
  const root = gltf.scene;
  scene.add(root);

  const texLoader = new THREE.TextureLoader();
  const meshes = [];
  root.traverse((o) => { if (o.isMesh) meshes.push(o); });

  await Promise.all(meshes.map(async (o) => {
    const url = `${OUT}/cornell_${o.name}_combined.png`;
    const tex = await texLoader.loadAsync(url);
    tex.flipY = false;                      // mesh chargé via GLTFLoader
    tex.colorSpace = THREE.SRGBColorSpace;
    // DoubleSide : la conversion Z-up (Blender) -> Y-up (glTF/three) peut inverser le sens
    // de bouclage des faces selon la rotation d'origine ; on ne prend pas de risque ici.
    o.material = new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide });
    log(`A: ${o.name} <- cornell_${o.name}_combined.png`);
  }));

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();
  window.__debugA = { scene, camera, renderer, meshes };
  return () => meshes.length;
}

// ---------- Chemin B : lightmap HDR, albedo dynamique ----------
async function buildSceneB() {
  const canvas = document.getElementById('canvasB');
  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, canvas.width / canvas.height, 0.1, 100);
  camera.position.set(0, 3.2, 2.4);   // vue plongeante sur le sol seul
  camera.lookAt(0, 0, 0);
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0, 0);

  const gltf = await new GLTFLoader().loadAsync(GLB);
  const sol = gltf.scene.getObjectByName('Sol');

  const lm = await new EXRLoader().loadAsync(`${OUT}/cornell_Sol_diffuse.exr`);
  // EXRLoader : flipY deja false, donnees lineaires. channel=0 car le GLB n'a
  // qu'un seul canal UV (lightMap lit UV1 par defaut depuis r152).
  lm.channel = 0;

  sol.material = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    lightMap: lm,
    lightMapIntensity: 1.0,
  });
  scene.add(sol);
  log('B: Sol <- cornell_Sol_diffuse.exr (lightMap)');
  window.__debugB = { scene, camera, renderer, sol };

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();
}

(async () => {
  try {
    await buildSceneA();
    await buildSceneB();
    log('\nOK : deux scenes chargees et rendues.');
  } catch (e) {
    log(`ERREUR: ${e.message}`);
    throw e;
  }
})();
