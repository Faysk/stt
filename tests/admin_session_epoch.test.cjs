// Session-epoch contract: a response from an old admin session cannot
// log out the new session, render its private data, or overwrite new state.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/dashboard.js','utf8');
const first=source.indexOf('async function api(');
const last=source.indexOf('function precision()',first);
assert.ok(first>=0&&last>first);
const apiCode=source.slice(first,last);

function deferred(){
 let resolve;
 const promise=new Promise(done=>{resolve=done});
 return {promise,resolve};
}
async function run(){
 const calls=[];
 const ctx=vm.createContext({
  adminGeneration:0,key:'old-secret',sessionMode:false,sessionCsrf:'',
  fetch:()=>{const d=deferred();calls.push(d);return d.promise},
  lockAdmin(){ctx.adminGeneration++;ctx.lockCount++},
  lockCount:0,Error,Promise
 });
 vm.runInContext(apiCode,ctx);
 const old401=ctx.api('/api/admin');
 ctx.adminGeneration=1;ctx.key='new-secret';
 calls[0].resolve({ok:false,status:401,headers:{get:()=>''},json:async()=>({detail:'old'})});
 await assert.rejects(old401,/STALE_ADMIN_SESSION/);
 assert.equal(ctx.lockCount,0,'A stale 401 cannot log out the new session');

 const old200=ctx.api('/api/admin');
 const pendingJSON=deferred();
 calls[1].resolve({ok:true,status:200,json:()=>pendingJSON.promise});
 // Yield so the async client reaches response.json before switching sessions.
 await Promise.resolve(); await Promise.resolve();
 ctx.adminGeneration=2;ctx.key='third-secret';
 pendingJSON.resolve({private:'old-session'});
 await assert.rejects(old200,/STALE_ADMIN_SESSION/);

 const current401=ctx.api('/api/admin');
 calls[2].resolve({ok:false,status:401,headers:{get:()=>''},json:async()=>({})});
 await assert.rejects(current401,/Chave inválida/);
 assert.equal(ctx.lockCount,1,'The current session still locks on HTTP 401');

 assert.match(source,/function lockAdmin\(\)\{adminGeneration\+\+;historyRequestId\+\+;refreshing=false/);
 assert.match(source,/function loadJobs\(\)\{const generation=adminGeneration/);
 assert.match(source,/function loadPeriod\(\)\{const generation=adminGeneration/);
 assert.match(source,/if\(generation!==adminGeneration\|\|\(!key&&!sessionMode\)\)return/);
 assert.match(source,/signIn\(csrf\)\{adminGeneration\+\+/);
 assert.match(source,/\$\('login'\)\.onclick=\(\)=>\{adminGeneration\+\+/);
 console.log('Admin auth epoch protects old 401 and old JSON responses.');
}
run().catch(error=>{console.error(error);process.exitCode=1});
