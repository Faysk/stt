// Pure scheduler regression checks: no network, DOM or production credentials.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function extract(source,start,end){
 const a=source.indexOf(start),b=source.indexOf(end,a+start.length);
 assert.ok(a>=0&&b>a,"Missing scheduler: "+start);
 return source.slice(a,b);
}
const publicCode=fs.readFileSync('web/public.js','utf8');
const publicCtx=vm.createContext({document:{hidden:false},jobs:[],failedRefreshes:0,Math});
vm.runInContext(extract(publicCode,'function publicRefreshDelay(){','function schedulePublicRefresh(){'),publicCtx);
assert.equal(publicCtx.publicRefreshDelay(),30000);
publicCtx.jobs=[{status:'running'}];assert.equal(publicCtx.publicRefreshDelay(),5000);
publicCtx.jobs=[{status:'done'}];assert.equal(publicCtx.publicRefreshDelay(),30000);
publicCtx.failedRefreshes=1;assert.equal(publicCtx.publicRefreshDelay(),10000);
publicCtx.failedRefreshes=6;assert.equal(publicCtx.publicRefreshDelay(),120000);
publicCtx.document.hidden=true;assert.equal(publicCtx.publicRefreshDelay(),60000);
assert.doesNotMatch(publicCode,/setInterval\(\(\)=>\{if\(!document.hidden\)refresh\(\)\},5000\)/);

const adminCode=fs.readFileSync('web/dashboard.js','utf8');
const adminCtx=vm.createContext({document:{hidden:false},activeJobs:false,failedAdminRefreshes:0,Math});
vm.runInContext(extract(adminCode,'function adminRefreshDelay(){','function scheduleAdminRefresh(){'),adminCtx);
assert.equal(adminCtx.adminRefreshDelay(),30000);
adminCtx.activeJobs=true;assert.equal(adminCtx.adminRefreshDelay(),10000);
adminCtx.failedAdminRefreshes=2;assert.equal(adminCtx.adminRefreshDelay(),40000);
adminCtx.failedAdminRefreshes=6;assert.equal(adminCtx.adminRefreshDelay(),120000);
adminCtx.document.hidden=true;assert.equal(adminCtx.adminRefreshDelay(),60000);
assert.match(adminCode,/Date\.now\(\)-lastPeriodLoad>=60000/);
assert.doesNotMatch(adminCode,/setInterval\(\(\)=>\{if\(\(key\|\|sessionMode\)\&\&!document.hidden\)refresh\(\)\},10000\)/);
console.log('Adaptive public/admin polling and backoff limits passed.');
