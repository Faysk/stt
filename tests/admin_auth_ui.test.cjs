const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/admin-auth.js'), 'utf8');
const flush = async () => { for (let n=0;n<15;n++) await new Promise(resolve=>setImmediate(resolve)); };
async function scenario(enabled) {
  const elements = new Map();
  const byId = id => {
    if (!elements.has(id)) elements.set(id, {hidden:false,disabled:false,textContent:'',removeAttribute(name){delete this[name]}});
    return elements.get(id);
  };
  const calls=[],timers=new Map(),sessions=[];let timer=0,state='pending';
  const response = (status,data)=>({ok:status===200,status,json:async()=>data});
  const context = {
    document:{getElementById:byId},MutationObserver:class {observe(){}},
    window:{STTAdmin:{isAuthenticated:()=>sessions.length>0,signIn:async csrf=>sessions.push(csrf)},addEventListener(){}},
    btoa:s=>Buffer.from(s).toString('base64'), Date, console,
    URL, location:{origin:'https://stt.faysk.dev'},
    setInterval:()=>++timer,clearInterval(){},
    setTimeout:fn=>{const id=++timer;timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id),
    fetch:async(url,options)=>{
      calls.push({url,options});
      if(url.endsWith('/config'))return response(200,{qr_enabled:enabled,legacy_enabled:true});
      if(url.endsWith('/session'))return response(401,{detail:'No session'});
      if(url.endsWith('/qr/start'))return response(200,{request:'public',csrf:'browser-csrf',expires:Date.now()/1000+90,code:'123456',svg:'<svg/>',url:'https://stt.faysk.dev/admin-approve.html?request=public'});
      if(url.endsWith('/qr/status'))return response(200,{state});
      if(url.endsWith('/qr/cancel'))return response(200,{state:'cancelled'});
      if(url.endsWith('/qr/consume'))return response(200,{csrf:'session-csrf'});
      throw Error('Unexpected '+url);
    }
  };
  vm.runInNewContext(source,context);await flush();
  if(!enabled){assert.equal(calls.length,1);assert.equal(byId('qr-login').hidden,true);assert.equal(byId('legacy-login').open,true);return;}
  assert.equal(byId('qr-code').textContent,'123456');
  assert.equal(byId('qr-frame').hidden,false);
  assert.match(byId('login-qr').src,/^data:image\/svg\+xml;base64,/);
  assert.equal(sessions.length,0,'Showing/scanning a QR must never sign in');
  await byId('qr-cancel').onclick();await flush();
  assert.equal(byId('qr-frame').hidden,true);assert.equal(byId('qr-status').textContent,'Pedido cancelado.');
  await byId('qr-start').onclick();await flush();
  state='approved';
  const tick=[...timers.values()].at(-1);assert.ok(tick);await tick();await flush();
  assert.deepEqual(sessions,['session-csrf']);
  assert.equal(byId('qr-frame').hidden,true);
  for(const {options} of calls){assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.headers['x-admin-key'],undefined);}
  const consume=calls.find(c=>c.url.endsWith('/qr/consume'));
  assert.equal(consume.options.headers['x-csrf-token'],'browser-csrf');
  assert.equal(JSON.parse(consume.options.body).request,'public');
}
(async()=>{await scenario(false);await scenario(true);console.log('QR UI: feature fallback, inert QR, cancel/restart, approval and cookie-only session passed.');})().catch(error=>{console.error(error);process.exitCode=1});
