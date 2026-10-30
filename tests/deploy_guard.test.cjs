const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const pkg=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
assert.equal(pkg.scripts.deploy,'node scripts/deploy-stt.mjs');
assert.match(pkg.scripts['deploy:stt:dry-run'],/wrangler\.stt\.jsonc --dry-run/);
const result=spawnSync(process.execPath,[path.join(root,'scripts/deploy-stt.mjs')],{
 cwd:root,encoding:'utf8',env:{...process.env,CI:'',STT_APPROVED_CUTOVER:'',STT_APPROVED_WORKER:''}
});
assert.equal(result.status,2);
assert.match(result.stderr,/STT deployment blocked/);
assert.match(result.stderr,/explicit operator approval/);
// A partial acknowledgement still must never reach the provider.
const partial=spawnSync(process.execPath,[path.join(root,'scripts/deploy-stt.mjs')],{
 cwd:root,encoding:'utf8',env:{...process.env,CI:'',STT_APPROVED_CUTOVER:'stt.faysk.dev',STT_APPROVED_WORKER:'faysk-tts'}
});
assert.equal(partial.status,2);
console.log('Deploy guard: implicit/incorrect Worker deployment safely denied.');
