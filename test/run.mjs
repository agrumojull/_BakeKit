#!/usr/bin/env node
// BakeKit - harnais de test automatise (etape 6).
// Enchaine : generation de scene -> bake -> controle qualite -> cas d'erreur (exit 2/3/4).
import { spawnSync, execSync } from 'node:child_process';
import { existsSync, readdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEST = path.join(ROOT, 'test');
const TMP = path.join(TEST, 'tmp');

function trouverBlender() {
  if (process.env.BAKEKIT_BLENDER) return process.env.BAKEKIT_BLENDER;
  try {
    // stderr ignoré : quand where échoue (cas normal), son message partirait dans la console
    return execSync('where blender', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
      .split(/\r?\n/)[0].trim();
  } catch { /* pas dans le PATH — cas normal sous Windows */ }
  const root = 'C:\\Program Files\\Blender Foundation';
  if (existsSync(root)) {
    const versions = readdirSync(root).filter(d => d.startsWith('Blender')).sort().reverse();
    for (const v of versions) {
      const exe = path.join(root, v, 'blender.exe');
      if (existsSync(exe)) return exe;
    }
  }
  console.error('blender.exe introuvable — définir BAKEKIT_BLENDER');
  process.exit(1);
}

const BLENDER = trouverBlender();
let echecs = 0;

function etape(titre, attendu, obtenu) {
  const ok = obtenu === attendu;
  console.log(`\n=== ${titre} : ${ok ? 'OK' : `ECHEC (attendu ${attendu}, obtenu ${obtenu})`} ===`);
  if (!ok) echecs++;
  return ok;
}

function runBlenderScript(script, args = []) {
  const r = spawnSync(BLENDER, ['-b', '--factory-startup', '--python-exit-code', '1',
                                 '--python', script, ...args],
                       { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  process.stdout.write(r.stdout ?? '');
  if (r.stderr) process.stderr.write(r.stderr);
  return r.status;
}

function runCli(args) {
  const r = spawnSync(process.execPath, [path.join(ROOT, 'cli.mjs'), ...args],
                       { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  process.stdout.write(r.stdout ?? '');
  if (r.stderr) process.stderr.write(r.stderr);
  return r.status;
}

rmSync(TMP, { recursive: true, force: true });

// 1. Génération de la scène de test (cornell.glb + cornell_nouv.glb)
etape('scène de test', 0, runBlenderScript(path.join(ROOT, 'blender', 'make_test_scene.py')));

// 2. Bake complet : 7 objets x 3 passes sur cornell.glb
etape('bake (7 objets x 3 passes)', 0, runCli([
  path.join(TEST, 'cornell.glb'),
  '--pass', 'combined', '--pass', 'diffuse', '--pass', 'ao',
  '--res', '128', '--samples', '32',
  '--out', path.join(TEST, 'out'),
]));

// 3. Contrôle qualité : bleeding mur->sol, mur->objet, aucune image uniforme
etape('check.py (preuve de GI)', 0, runBlenderScript(path.join(ROOT, 'blender', 'check.py')));

// 4. Cas d'erreur : exit 3 (UVs manquantes), exit 2 (--object introuvable), exit 4 (aucune lumière)
etape('exit 3 : cornell_nouv.glb sans UVs', 3, runCli([
  path.join(TEST, 'cornell_nouv.glb'), '--out', path.join(TMP, 'out-exit3'),
]));

etape('exit 2 : --object introuvable', 2, runCli([
  path.join(TEST, 'cornell.glb'), '--object', 'Inexistant', '--out', path.join(TMP, 'out-exit2'),
]));

const fixtureStatus = runBlenderScript(path.join(ROOT, 'blender', 'make_nolight_fixture.py'));
if (fixtureStatus !== 0) {
  console.error('ECHEC: génération de la fixture sans lumière');
  echecs++;
} else {
  etape('exit 4 : aucune source de lumière', 4, runCli([
    path.join(TMP, 'cornell_nolight.glb'), '--out', path.join(TMP, 'out-exit4'),
  ]));
}

rmSync(TMP, { recursive: true, force: true });

console.log(`\n${echecs === 0 ? 'CHECK: OK' : 'CHECK: ECHEC'} — ${echecs} échec(s) sur le harnais complet`);
process.exit(echecs === 0 ? 0 : 1);
