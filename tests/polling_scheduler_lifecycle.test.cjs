// Offline timer/lifecycle checks for Cloudflare quota-conscious public/admin polling.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function slice(source,start,end){
 const a=source.indexOf(start),b=source.indexOf(end,a);
 assert.ok(a>=0 && b>a,'scheduler function missing');
 return source.slice(a,b);
}
function clock(){
 const scheduled=new Map();
 let seq=0;
 return {
  scheduled,
  setTimeout(fn,ms){const id=++seq;scheduled.set(id,{fn,ms});return id},
  clearTimeout(id){scheduled.delete(id)},
  async tick(){
   const [id,timer]=scheduled.entries().next().value;
   scheduled.delete(id);
   await timer.fn();
  },
 };
}
async function publicScheduler(){
 const code=fs.readFileSync('web/public.js','utf8');
 const scheduler=clock();
 let calls=0;
 const ctx=vm.createContext({
  document:{hidden:false},jobs:[],failedRefreshes:0,refreshTimer:null,
  refresh:async()=>{calls++},Math,...scheduler
 });
 vm.runInContext(slice(code,'function publicRefreshDelay(){',"document.addEventListener('visibilitychange'"),ctx);
 ctx.schedulePublicRefresh();
 assert.equal(scheduler.scheduled.size,1);
 assert.equal([...scheduler.scheduled.values()][0].ms,30000);
 ctx.document.hidden=true;
 ctx.schedulePublicRefresh();
 assert.equal(scheduler.scheduled.size,0,'hidden public page must not poll');
 ctx.document.hidden=false;
 ctx.jobs=[{status:'running'}];
 ctx.schedulePublicRefresh();
 assert.equal([...scheduler.scheduled.values()][0].ms,5000);
 await scheduler.tick();
 assert.equal(calls,1);
 assert.equal(scheduler.scheduled.size,1,'one timer per refresh, never a chain of duplicates');
 ctx.failedRefreshes=2;
 ctx.schedulePublicRefresh();
 assert.equal([...scheduler.scheduled.values()][0].ms,20000);
}
async function adminScheduler(){
 const code=fs.readFileSync('web/dashboard.js','utf8');
 const scheduler=clock();
 let calls=0;
 const ctx=vm.createContext({
  document:{hidden:false},key:'token',sessionMode:false,
  activeJobs:false,failedAdminRefreshes:0,adminRefreshTimer:null,
  refresh:async()=>{calls++},Math,...scheduler
 });
 vm.runInContext(slice(code,'function adminRefreshDelay(){',"document.addEventListener('visibilitychange'"),ctx);
 ctx.scheduleAdminRefresh();
 assert.equal([...scheduler.scheduled.values()][0].ms,30000);
 ctx.document.hidden=true;
 ctx.scheduleAdminRefresh();
 assert.equal(scheduler.scheduled.size,0,'hidden admin page must not poll');
 ctx.document.hidden=false;
 ctx.key='';
 ctx.scheduleAdminRefresh();
 assert.equal(scheduler.scheduled.size,0,'logged-out admin must have no polling timer');
 ctx.key='token';ctx.activeJobs=true;
 ctx.scheduleAdminRefresh();
 assert.equal([...scheduler.scheduled.values()][0].ms,10000);
 await scheduler.tick();
 assert.equal(calls,1);
 assert.equal(scheduler.scheduled.size,1);
 assert.match(code,/function lockAdmin\(\)\{clearTimeout\(adminRefreshTimer\);adminRefreshTimer=null;lastPeriodLoad=0;/);
 assert.match(code,/signIn\(csrf\)\{[^}]*lastPeriodLoad=0/);
}
(async()=>{await publicScheduler();await adminScheduler();
 console.log('Public/admin timer cancellation, session reset and retry cadence passed.');
})().catch(err=>{console.error(err);process.exitCode=1});
