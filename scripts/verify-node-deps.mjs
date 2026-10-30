// Release preflight: verify the already-installed npm tree without changing it.
// Package metadata and the hidden npm lock are checked against the committed lock.
// This prevents recurring npm ci from unlinking esbuild.exe used by Wrangler.
import {existsSync,readFileSync} from 'node:fs';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

function fail(message) {
  throw new Error('STT_NODE_DEPS_NOT_READY: ' + message);
}

function readJson(file) {
  try {
    return JSON.parse(readFileSync(file,'utf8'));
  } catch {
    fail('Missing or invalid ' + file.split(/[\\/]/).slice(-2).join('/') +
         '. Stop the Wrangler dev process and run npm ci once before retrying.');
  }
}

function stable(record) {
  return JSON.stringify(Object.entries(record || {}).sort(([a],[b])=>a.localeCompare(b)));
}

export function verifyNodeDeps(root) {
  root=resolve(root);
  const packageJson=readJson(join(root,'package.json'));
  const locked=readJson(join(root,'package-lock.json'));
  const installed=readJson(join(root,'node_modules','.package-lock.json'));
  if (locked.lockfileVersion !== 3 || installed.lockfileVersion !== 3) {
    fail('Unsupported npm lockfile format (requires v3).');
  }
  const required=locked.packages;
  const actual=installed.packages;
  if (!required || !required[''] || !actual ||
      typeof required!=='object' || typeof actual!=='object') {
    fail('Lockfile missing package inventory.');
  }
  for (const type of ['dependencies','devDependencies','optionalDependencies']) {
    if (stable(packageJson[type])!==stable(required[''][type])) {
      fail('package.json does not match package-lock.json: ' + type);
    }
  }
  // npm omits incompatible optional binaries on other OSes, which is normal.
  // Every non-optional lockfile package must be present; installed optional
  // packages, when present, must match the same immutable release metadata.
  for (const [name,spec] of Object.entries(required)) {
    if (name==='') continue;
    if (!name.startsWith('node_modules/') || name.includes('..')) {
      fail('Unexpected lockfile package path.');
    }
    if (!spec.optional && !actual[name]) {
      fail('Missing locked package: ' + name);
    }
  }
  for (const [name,metadata] of Object.entries(actual)) {
    if (!name.startsWith('node_modules/') || name.includes('..')) {
      fail('Unexpected installed package path.');
    }
    const spec=required[name];
    if (!spec) fail('Installed dependency not present in lockfile: ' + name);
    for (const field of ['version','integrity','resolved']) {
      if (spec[field] !== metadata[field]) {
        fail('Installed dependency differs from lockfile: ' + name + '/' + field);
      }
    }
    if (!existsSync(join(root,name,'package.json'))) {
      fail('Installed package directory incomplete: ' + name);
    }
  }
  const wrangler=join(root,'node_modules','wrangler','bin','wrangler.js');
  if (!existsSync(wrangler)) fail('Wrangler entrypoint missing.');
  if (process.platform==='win32' && process.arch==='x64' &&
      !existsSync(join(root,'node_modules','@esbuild','win32-x64','esbuild.exe'))) {
    fail('Windows esbuild binary missing.');
  }
  return {checked:Object.keys(actual).length,root};
}

const called=process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url);
if (called) {
  try {
    const report=verifyNodeDeps(dirname(dirname(fileURLToPath(import.meta.url))));
    process.stdout.write('NODE_DEPENDENCIES_VERIFIED: '+report.checked+' installed packages match lockfile; npm ci not required.\n');
  } catch (error) {
    process.stderr.write(error.message+'\n');
    process.exitCode=4;
  }
}
