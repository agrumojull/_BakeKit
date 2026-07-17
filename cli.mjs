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

// Validation des options : mieux vaut un message clair ici qu'un traceback Python (exit 1)
const PASSES_CONNUES = ['combined', 'diffuse', 'ao'];
const passesInconnues = opts.pass.filter(p => !PASSES_CONNUES.includes(p));
if (passesInconnues.length) {
  console.error(`passe(s) inconnue(s): ${passesInconnues.join(', ')} — choix: ${PASSES_CONNUES.join(', ')}`);
  process.exit(1);
}
for (const k of ['res', 'samples', 'margin']) {
  if (Number.isNaN(+opts[k])) { console.error(`--${k} doit être un nombre (reçu: ${opts[k]})`); process.exit(1); }
}
const sun = opts.sun?.split(',').map(Number);
if (sun && (sun.length !== 3 || sun.some(Number.isNaN))) {
  console.error(`--sun attend "azimut,élévation,intensité" (ex: "45,60,3"), reçu: ${opts.sun}`);
  process.exit(1);
}

function trouverBlender() {
  if (opts.blender) return opts.blender;
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
  console.error('blender.exe introuvable — utiliser --blender ou BAKEKIT_BLENDER');
  process.exit(1);
}

const outDir = opts.out ? path.resolve(opts.out) : path.join(path.dirname(path.resolve(input)), 'out');
mkdirSync(outDir, { recursive: true });

const cfg = {
  input: path.resolve(input),
  passes: opts.pass,
  res: +opts.res,
  samples: +opts.samples,
  margin: +opts.margin,
  hdri: opts.hdri && path.resolve(opts.hdri),
  sun,                                        // "45,60,3" → [45, 60, 3]
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

if (r.error) {   // spawn impossible (ENOENT, EACCES…) : status est null, stderr vide
  console.error(`impossible de lancer Blender (${blender}): ${r.error.message}`);
  process.exit(1);
}

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
