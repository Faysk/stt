const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/admin-approve.js'),'utf8');
const flush=async()=>{for(let n=0;n<20;n++)await new Promise(resolve=>setImmediate(resolve))};
async function scenario(cancelNative=false){
 const nodes=new Map();const byId=id=>{if(!nodes.has(id))nodes.set(id,{disabled:false,hidden:id==='approval-done',textContent:'',dataset:{}});return nodes.get(id)};
 const calls=[];let nativeCalls=0;
 const credential={id:'BAUG',rawId:new Uint8Array([4,5,6]).buffer,type:'public-key',authenticatorAttachment:'platform',
  response:{clientDataJSON:new Uint8Array([1,2]).buffer,authenticatorData:new Uint8Array([3,4]).buffer,signature:new Uint8Array([5,6]).buffer,userHandle:null},getClientExtensionResults:()=>({})};
 vm.runInNewContext(source,{
  document:{getElementById:byId},location:{search:'?request=fixture',hash:'',pathname:'/admin-approve.html'},
  window:{isSecureContext:true,PublicKeyCredential:{}},URLSearchParams,Uint8Array,Date,history:{replaceState(){throw Error('No fragment expected')}},
  btoa:s=>Buffer.from(s,'binary').toString('base64'),atob:s=>Buffer.from(s,'base64').toString('binary'),
  setTimeout:()=>1,clearTimeout(){},
  navigator:{credentials:{create(){throw Error('Login must never enroll credentials')},get:async options=>{
   nativeCalls++;assert.deepEqual(Array.from(options.publicKey.challenge),[1,2,3]);
   if(cancelNative&&nativeCalls===1){const error=Error('Native cancelled');error.name='NotAllowedError';throw error}
   return credential;
  }}},
  fetch:async(url,options)=>{
   calls.push({url,options});let data;
   if(url.endsWith('/approve/context'))data={expires:Date.now()/1000+90};
   else if(url.endsWith('/ceremony/options'))data={id:'challenge',csrf:'phone-csrf',publicKey:{challenge:'AQID',allowCredentials:[{id:'BAUG'}],userVerification:'required'}};
   else if(url.endsWith('/ceremony/finish'))data={verified:true};else throw Error('Unexpected route');
   return {ok:true,json:async()=>data};
  }
 });
 await flush();
 assert.equal(nativeCalls,1,'Open native authentication automatically, without app confirmation');
 if(cancelNative){
  assert.equal(calls.filter(c=>c.url.endsWith('/ceremony/finish')).length,0);
  assert.equal(byId('approve-access').disabled,false,'Logo remains a single-tap retry');
  await byId('approve-access').onclick();await flush();
 }
 assert.equal(byId('approval-card').dataset.state,'done');
 assert.equal(byId('approval-done').hidden,false);assert.equal(byId('approve-access').hidden,true);
 const finish=calls.find(c=>c.url.endsWith('/ceremony/finish'));
 assert.ok(finish,'Successful native ceremony immediately sends the signed approval');
 assert.equal(finish.options.headers['x-csrf-token'],'phone-csrf');
 assert.equal(JSON.parse(finish.options.body).credential.response.signature,'BQY');
 for(const {options}of calls){assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store')}
}
(async()=>{await scenario();await scenario(true);console.log('Phone: automatic passkey, immediate signed approval and native-cancel fallback passed.');})().catch(error=>{console.error(error);process.exitCode=1});
