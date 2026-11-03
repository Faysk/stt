// Simulated in-flight admin responses must never cross login/QR sessions.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('web/dashboard.js','utf8');
const between=(a,b)=>{
 const start=src.indexOf(a),end=src.indexOf(b,start+1);
 assert.ok(start>=0&&end>start,'Missing function: '+a);
 return src.slice(start,end);
};
const apiCode=between('async function api(', 'function precision()');
const refreshCode=between('async function refresh(', 'async function showDetail(');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
const ok=data=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>data});
const unauth={ok:false,status:401,headers:{get:()=> 'application/json'},json:async()=>({detail:'no'})};

async function testOldFetchCannotRenderOrSignOutNewSession(){
 const pending=deferred();let lockCalls=0;
 const ctx=vm.createContext({authEpoch:1,key:'old-key',sessionMode:false,sessionCsrf:'',
   fetch:()=>pending.promise,lockAdmin:()=>lockCalls++});
 vm.runInContext(apiCode,ctx);
 const inflight=ctx.api('/api/admin');
 ctx.authEpoch=2;ctx.key='new-key';
 pending.resolve(ok({secret:'previous user'}));
 await assert.rejects(inflight,/ADMIN_SESSION_CHANGED/);
 assert.equal(lockCalls,0);

 const old401=deferred();ctx.fetch=()=>old401.promise;
 const stale=ctx.api('/api/admin');
 ctx.authEpoch=3;ctx.key='next-key';old401.resolve(unauth);
 await assert.rejects(stale,/ADMIN_SESSION_CHANGED/);
 assert.equal(lockCalls,0,'Delayed 401 must not invalidate a new login');
}
async function testOldBodyCannotRenderAfterSessionSwitch(){
 const body=deferred();
 const ctx=vm.createContext({authEpoch:1,key:'old',sessionMode:false,sessionCsrf:'',
   fetch:async()=>({ok:true,status:200,json:()=>body.promise}),lockAdmin:()=>{}});
 vm.runInContext(apiCode,ctx);
 const stale=ctx.api('/api/admin/jobs');
 await Promise.resolve();
 ctx.authEpoch=2;ctx.key='new';
 body.resolve({secret:'old body'});
 await assert.rejects(stale,/ADMIN_SESSION_CHANGED/);
}
async function testRefreshRunsForNewSessionWithoutOldResponseClobber(){
 const first=deferred(),second=deferred();
 let calls=0,rendered=[],scheduled=0;
 const node=()=>({hidden:false,textContent:'',classList:{remove(){}}});
 const ctx=vm.createContext({
  authEpoch:1,key:'old',sessionMode:false,refreshingEpoch:-1,
  failedAdminRefreshes:0,lastPeriodLoad:0,document:{body:{classList:{remove(){}}}},
  $:node,api:()=>{calls++;return calls===1?first.promise:second.promise},
  renderOverview:d=>rendered.push(d.token),renderQueue:()=>{},renderSettings:()=>{},
  loadJobs:async()=>{},loadPeriod:async()=>{},
  scheduleAdminRefresh:()=>scheduled++,showError:e=>{throw e},Math,Date
 });
 vm.runInContext(refreshCode,ctx);
 const pendingOld=ctx.refresh(false);
 ctx.authEpoch=2;ctx.key='new';
 const pendingNew=ctx.refresh(false);
 assert.equal(calls,2,'New login cannot be blocked by prior in-flight refresh');
 second.resolve({token:'new'});await pendingNew;
 first.resolve({token:'old'});await pendingOld;
 assert.deepEqual(rendered,['new']);
 assert.equal(ctx.refreshingEpoch,-1);
 assert.equal(scheduled,1);
}
(async()=>{
 await testOldFetchCannotRenderOrSignOutNewSession();
 await testOldBodyCannotRenderAfterSessionSwitch();
 await testRefreshRunsForNewSessionWithoutOldResponseClobber();
 for(const marker of [
  'authEpoch++;historyRequestId++;clearTimeout(adminRefreshTimer)',
  'if(epoch!==authEpoch||requestId!==historyRequestId',
  'if(epoch!==authEpoch||requested!==period',
  'signIn(csrf){authEpoch++',
 ])assert.ok(src.includes(marker),'Missing session generation guard: '+marker);
 console.log('Admin session isolation, stale 401, stale JSON, concurrent refresh passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
