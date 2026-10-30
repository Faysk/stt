// DOM-level admin regression: selecting cards must update pending settings and request worker-scoped cache.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class El {
  constructor(tag='div',text='',cls='') {
    this.tagName=tag.toUpperCase();this._text=String(text);this.className=cls;
    this.children=[];this.dataset={};this.attributes={};this.listeners={};
    this.style={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;
  }
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('')}
  set textContent(s){this._text=String(s);this.children=[]}
  get firstChild(){return this._text?{textContent:this._text}:this.children[0]}
  append(...children){this.children.push(...children);return this}
  replaceChildren(...children){this._text='';this.children=[...children]}
  get classList(){return {
    add:x=>{this.className=[...new Set([...this.className.split(' ').filter(Boolean),x])].join(' ')},
    remove:x=>{this.className=this.className.split(' ').filter(y=>y&&y!==x).join(' ')},
    toggle:(x,enabled)=>{const n=new Set(this.className.split(' ').filter(Boolean));
      if(enabled===undefined? !n.has(x):enabled)n.add(x);else n.delete(x);
      this.className=[...n].join(' ')}
  }}
  setAttribute(k,v){this.attributes[k]=String(v)}
  addEventListener(kind,fn){(this.listeners[kind]??=[]).push(fn)}
  fire(kind,e={}){for(const fn of this.listeners[kind]||[])fn(e)}
  querySelectorAll(sel){
    const test=e=>sel[0]==='.'?e.className.split(' ').includes(sel.slice(1)):e.tagName.toLowerCase()===sel;
    const results=[];for(const child of this.children){if(test(child))results.push(child);results.push(...child.querySelectorAll(sel))}
    return results;
  }
  querySelector(sel){return this.querySelectorAll(sel)[0]||null}
}
const ids=['worker','model','device','compute','settings-message','settings-form','model-catalog','workers',
  'versions','device-cards','precision-cards','ssh-targets','config-summary','login','admin-key','admin-message',
  'runs','history','activity','usage','refresh','precision-explainer'];
const nodes=Object.fromEntries(ids.map(id=>[id,new El('div')]));
nodes.worker.value='notebook';nodes.model.value='small';nodes.device.value='cpu';nodes.compute.value='int8';
nodes.login.onclick=()=>{};
const document={activeElement:null,querySelector(){return null;}};
const el=(tag,text='',cls='')=>new El(tag,text,cls);
const calls=[];
const clipboard=[];
const data={
 settings:{worker:'notebook',model:'small',device:'cpu',compute:'int8',paused:false},
 workers:[{id:'notebook',seen:100,info:{system:'Windows',cpu:28,models:[{id:'small',revision:'a'.repeat(40)}],
    compute_types:{cpu:['int8','int16','float32','int8_float32'],cuda:['float16']},
    gpu:{name:'RTX 4070',vram_mb:8192,free_vram_mb:5000}}},
    {id:'cpu-only',seen:100,info:{system:'Linux',cpu:8,models:[],compute_types:{cpu:['int8']}}},
    {id:'s7-edge',seen:100,info:{system:'Android',cpu:6,engine:'whisper.cpp',
      models:[{id:'tiny',revision:'ggml-tiny'}],
      compute_types:{cpu:['float32'],cuda:[]}}}],
 catalog:[{id:'small',repository:'Systran/faster-whisper-small'},
          {id:'turbo',repository:'mobiuslabsgmbh/faster-whisper-large-v3-turbo'},
          {id:'tiny',repository:'Systran/faster-whisper-tiny'}],
 now:100,preloads:[],ssh_targets:[{id:'s7-edge',label:'Galaxy S7 Edge',configured:true,command:'ssh -p 8022 u0_a999@192.0.2.10'}]
};
const ctx=vm.createContext({
 document,$:id=>nodes[id],node:el,
 dirty:false,loaded:false,refreshing:false,
 renderSettings(d){
   if(!ctx.dirty){nodes.worker.value=d.settings.worker;nodes.model.value=d.settings.model;
     nodes.device.value=d.settings.device;nodes.compute.value=d.settings.compute}
   nodes['model-catalog'].replaceChildren(...d.catalog.map(m=>{
     const card=el('article','','model-card');
     card.append(el('h3',m.id).append(el('span','Sob demanda','badge')),
       el('p',m.repository),el('code',m.repository),el('code','Revisão não baixada'),el('a','Referência'));
     return card;
   }));
   nodes.workers.replaceChildren(...d.workers.map(w=>el('div',w.id,'worker-card')));
 },
 refresh:async()=>{},loadJobs:async()=>{},loadPeriod:async()=>{},
 precision(){}, api:async(path,opts)=>{calls.push({path,opts});return {id:'test',status:'pending'}},
 navigator:{clipboard:{writeText:async text=>clipboard.push(text)}},
 console,Set,Map,Promise,Number
});
vm.runInContext(fs.readFileSync('web/admin-enhancements.js','utf8'),ctx);
(async()=>{
  ctx.renderSettings(data);
  assert.equal(nodes['model-catalog'].children.length,3);
  const sshCard=nodes['ssh-targets'].children[0];
  assert.match(sshCard.textContent,/Galaxy S7 Edge · SSH/);
  assert.match(sshCard.textContent,/nenhum executor STT online/);
  sshCard.querySelector('button').fire('click');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(clipboard[0],'ssh -p 8022 u0_a999@192.0.2.10');

  assert.equal(nodes['precision-cards'].children.length,4);
  assert.ok(nodes['precision-cards'].children.some(card=>card.dataset.compute==='int16'),
    'INT16 should be offered only when this CPU reports support');
  assert.match(nodes['precision-explainer'].textContent,/FP16.*GPU CUDA/);
  assert.ok(!nodes['precision-cards'].children.some(card=>card.dataset.compute==='float16'),
    'FP16 should not be offered for CPU, where the runtime falls back to FP32');
  assert.equal(nodes['precision-cards'].children.find(card=>card.dataset.compute==='int8_float32')?.children[1]?.textContent,
    'INT8 + FP32 · Misto');
  const gpuCard=nodes['device-cards'].children[1];
  gpuCard.onclick();
  assert.equal(nodes.device.value,'cuda');
  assert.ok(nodes['precision-cards'].children.some(card=>card.dataset.compute==='float16'));
  assert.match(nodes['precision-explainer'].textContent,/FP16 usa ponto flutuante de 16 bits/);
  nodes['device-cards'].children[0].onclick();
  assert.equal(nodes.device.value,'cpu');
  const small=nodes['model-catalog'].children[0];
  assert.equal(small.attributes['aria-pressed'],'true');
  assert.match(small.textContent,/No cache do executor/);
  const turbo=nodes['model-catalog'].children[1];
  turbo.fire('click',{target:{closest:()=>null}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes.model.value,'turbo','model card must change pending selection');
  assert.equal(ctx.dirty,true,'click must require save');
  assert.equal(calls.filter(c=>c.path==='/api/admin/model-preloads').length,1);
  assert.deepEqual(JSON.parse(calls[0].opts.body),{worker:'notebook',model:'turbo'});
  assert.match(nodes['config-summary'].textContent,/Alterações pendentes/);
  const cpucard=nodes.workers.children[1];
  cpucard.fire('click');
  assert.equal(nodes.worker.value,'cpu-only');
  assert.equal(nodes['device-cards'].children[1].disabled,true);
  assert.equal(nodes['precision-cards'].children.length,2);
  assert.ok(!nodes['precision-cards'].children.some(card=>card.dataset.compute==='int16'),
    'Unsupported INT16 must not be offered on a CPU-only worker');
  // Live S7 capabilities take priority over notebook's small/int8 selection.
  const s7card=nodes.workers.children[2];
  s7card.fire('click');
  assert.equal(nodes.worker.value,'s7-edge');
  assert.equal(nodes.model.value,'tiny');
  assert.equal(nodes.device.value,'cpu');
  assert.equal(nodes.compute.value,'float32');
  assert.equal(nodes['precision-cards'].children.length,1);
  assert.equal(nodes['precision-cards'].children[0].dataset.compute,'float32');
  assert.match(nodes['settings-message'].textContent,/modo secundário/);
  const oldRequests=calls.length;
  const invalidModel=nodes['model-catalog'].children[0];
  invalidModel.fire('click',{target:{closest:()=>null}});
  assert.equal(nodes.model.value,'tiny','S7 cannot switch to notebook-only models');
  assert.equal(calls.length,oldRequests,'S7 never schedules remote model preloads');

  // Returning to the configured notebook restores its active precision/model.
  nodes.workers.children[0].fire('click');
  assert.equal(nodes.worker.value,'notebook');
  assert.equal(nodes.model.value,'small');
  assert.equal(nodes.device.value,'cpu');
  assert.equal(nodes.compute.value,'int8');

  const html=fs.readFileSync('web/admin.html','utf8');
  assert.match(html,/id="configuration-state" hidden/);
  assert.doesNotMatch(html,/Seleção alternativa por menus|class="config-advanced"/);
  assert.match(html,/id="precision-explainer"/);
  console.log('Admin controls: card-only settings, CPU/GPU FP16, mixed precision, model and cache interactions OK.');
})().catch(e=>{console.error(e);process.exitCode=1});
