// Cloudflare may answer with an HTML error page, not JSON.
// Test the public API client without contacting any network or real session.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/public.js','utf8');
const start=source.indexOf('async function api(');
const end=source.indexOf('function tab(',start);
assert.ok(start>=0&&end>start);
const client=source.slice(start,end);

async function check(response,expected){
 const ctx=vm.createContext({
  fetch:async()=>response,
  key:'0123456789abcdef'.repeat(4),
  locale:'pt',
  t:key=>({quota:'Limite Cloudflare atingido',unavailable:'Serviço indisponível'})[key]||key,
  failure:(status)=>'Falha HTTP '+status
 });
 vm.runInContext(client,ctx);
 await assert.rejects(ctx.api('/api/jobs'),error=>error.message===expected);
}
const html=content=>({
 ok:false,status:503,headers:{get:name=>name==='content-type'?'text/html; charset=UTF-8':null},
 text:async()=>content
});
(async()=>{
 await check(html('<h1>Error 1027</h1>'),'Limite Cloudflare atingido');
 await check(html('<html>Gateway unavailable</html>'),'Serviço indisponível');
 await check({ok:false,status:429,headers:{get:()=> 'application/json'},json:async()=>({detail:'limit'})},
             'Falha HTTP 429');
 const dictionary=fs.readFileSync('web/i18n.js','utf8');
 assert.match(dictionary,/const quotaLabels=\{/);
 for(const lang of ['en','pt','es','fr','zh','hi','ar','bn','id','ur']){
  assert.match(dictionary,new RegExp('(?:\\{|,)'+lang+':'));
 }
 console.log('Cloudflare HTML error recognition and fallback messages passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
