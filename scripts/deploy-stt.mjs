// Production mutations are intentional operator actions, never a default npm script.
import {readFileSync} from 'node:fs';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const expectedDomain='stt.faysk.dev', expectedName='faysk-stt';
const fail=message=>{console.error('[STT deployment blocked] '+message);process.exit(2)};
if(process.env.CI)fail('Never deploy from the test workflow');
if(process.env.STT_APPROVED_CUTOVER!==expectedDomain||
   process.env.STT_APPROVED_WORKER!==expectedName)
  fail('Requires explicit operator approval: STT_APPROVED_CUTOVER=stt.faysk.dev and STT_APPROVED_WORKER=faysk-stt');
const config=JSON.parse(readFileSync(resolve(root,'wrangler.stt.jsonc'),'utf8'));
if(config.name!==expectedName||
   config.routes?.length!==1||
   config.routes[0].pattern!==expectedDomain||
   config.routes[0].custom_domain!==true)
  fail('Wrangler target changed; review configuration before any deployment');
const result=spawnSync(process.execPath,[
 resolve(root,'node_modules/wrangler/bin/wrangler.js'),
 'deploy','--config','wrangler.stt.jsonc'
],{cwd:root,stdio:'inherit',shell:false});
if(result.error)fail('Cannot launch Wrangler; install locked npm dependencies first');
process.exit(result.status??2);
