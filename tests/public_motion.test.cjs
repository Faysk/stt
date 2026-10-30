const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Lightweight DOM double so rendering/lifecycle checks run in CI without a browser
// or a production API. Real UI data and percentages are never fabricated by app code.
class Node {
  constructor(tag='div') {
    this.tagName=tag;this.children=[];this.parentNode=null;this.className='';
    this.dataset={};this.style={};this.attributes={};this._text='';
    this.hidden=false;this.onclick=null;
  }
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  set textContent(s){this._text=String(s);for(const c of this.children)c.parentNode=null;this.children=[]}
  get classList(){
    return {
      add:name=>this.className=[...new Set([...this.className.split(/\s+/).filter(Boolean),name])].join(' '),
      remove:name=>this.className=this.className.split(/\s+/).filter(x=>x&&x!==name).join(' '),
      toggle:(name,on)=>{
        const a=new Set(this.className.split(/\s+/).filter(Boolean));
        const enabled=on===undefined?!a.has(name):on;
        if(enabled)a.add(name);else a.delete(name);
        this.className=[...a].join(' ');return enabled;
      }
    };
  }
  matches(selector){return selector.startsWith('.')&&this.className.split(/\s+/).includes(selector.slice(1))}
  querySelectorAll(selector){
    const result=[];
    for(const child of this.children){
      if(child.matches(selector))result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null}
  insertBefore(child,reference){
    if(child.parentNode)child.remove();
    const i=reference?this.children.indexOf(reference):-1;
    this.children.splice(i<0?this.children.length:i,0,child);child.parentNode=this;return child;
  }
  append(...children){for(const child of children)this.insertBefore(child,null)}
  replaceChildren(...children){for(const child of [...this.children])child.remove();this._text='';this.append(...children)}
  remove(){
    if(!this.parentNode)return;
    const i=this.parentNode.children.indexOf(this);
    if(i>=0)this.parentNode.children.splice(i,1);
    this.parentNode=null;
  }
  replaceWith(next){if(!this.parentNode)throw Error('Detached element');this.parentNode.insertBefore(next,this);this.remove()}
  after(next){
    if(!this.parentNode)throw Error('Detached element');
    const siblings=this.parentNode.children;
    this.parentNode.insertBefore(next,siblings[siblings.indexOf(this)+1]||null);
  }
  setAttribute(name,value){this.attributes[name]=String(value)}
  removeAttribute(name){delete this.attributes[name]}
  getAttribute(name){return this.attributes[name]??null}
  set value(v){this._value=v;this.setAttribute('value',v)}
  get value(){return this._value}
}
const nodes=Object.fromEntries(['jobs','search','count','library-count','upload-progress','upload-visual','upload-fill','upload-percent'].map(id=>[id,new Node()]));
nodes.search.value='';
const ctx=vm.createContext({
  document:{createElement:tag=>new Node(tag)},
  $:id=>nodes[id],
  el(tag,cls,text){const item=new Node(tag);if(cls)item.className=cls;if(text!==undefined)item.textContent=text;return item},
  t:key=>({readyState:'Ready',read:'Read',download:'Download',expires:'Expires',error:'Error',transcribing:'Transcribing',queued:'Queued',preparing:'Preparing',done:'Ready'}[key]||key),
  date:n=>'at '+n,
  duration:n=>n?n+' seconds':'—',
  readTranscript(){},
  download(){},
  jobs:[],filter:'all',locale:'en',renderSignature:'',
  console,Number,Map,Set
});
const source=fs.readFileSync('web/public.js','utf8');
const slice=(start,end)=>{
  const a=source.indexOf(start),b=source.indexOf(end,a+start.length);
  assert.ok(a!==-1&&b>a,'Functions must exist: '+start);return source.slice(a,b);
};
vm.runInContext(slice('function makeJobCard(j){','function showHealth(){'),ctx);
vm.runInContext(slice('function setUploadMeter(value){','function upload(file){'),ctx);
const job=(id,status,progress=0,stage='queued')=>({
 id,title:id+' audio',created:12345,expires:99999,status,progress,stage,
 duration:71,timing:'segments',position:1
});
const queued=job('one','queued'),done=job('two','done');
ctx.jobs=[queued,done];
ctx.render();
const one=nodes.jobs.children[0],two=nodes.jobs.children[1];
assert.equal(nodes.jobs.children.length,2);
assert.equal(one.dataset.status,'queued');
assert.equal(two.querySelectorAll('.format-button').length,4);
assert.equal(one.querySelector('.job-activity').hidden,true);

ctx.jobs=[job('one','running',0,'preparing'),done];
ctx.render();
assert.equal(nodes.jobs.children[0],one,'Stable DOM card required during polling');
assert.equal(nodes.jobs.children[1],two,'Unchanged cards must keep identity');
assert.equal(one.querySelector('.job-activity').hidden,false);
assert.equal(one.querySelector('.job-activity').className.includes('is-indeterminate'),true);
assert.equal(one.querySelector('.job-activity').getAttribute('aria-valuenow'),null);
assert.equal(one.querySelector('.badge').textContent,'Preparing');
assert.equal(one.querySelector('.badge').className.includes('state-arrive'),true);

ctx.jobs=[job('one','running',24,'transcribing'),done];
ctx.render();
assert.equal(nodes.jobs.children[0],one);
assert.equal(one.querySelector('.job-activity-fill').style.width,'24%');
assert.equal(one.querySelector('.job-activity').getAttribute('aria-valuenow'),'24');
assert.equal(one.querySelector('.badge').textContent,'Transcribing · 24%');
const badge=one.querySelector('.badge');
ctx.jobs=[job('one','running',47,'transcribing'),done];
ctx.render();
assert.equal(one.querySelector('.badge'),badge,'Progress updates must not reanimate badges');
assert.equal(one.querySelector('.job-activity-fill').style.width,'47%');
assert.equal(two.querySelector('.transcript-card-actions'),two.querySelector('.transcript-card-actions'));

ctx.jobs=[job('one','done',100,'done'),done];
ctx.render();
assert.equal(nodes.jobs.children[0],one,'Completed card must keep identity');
assert.equal(one.querySelector('.job-activity').hidden,true);
assert.equal(one.querySelectorAll('.format-button').length,4);
assert.equal(one.querySelector('.badge').textContent,'Ready');
const read=one.querySelector('.read-button');
ctx.jobs=[job('one','done',100,'done'),done];
ctx.render();
assert.equal(one.querySelector('.read-button'),read,'Read control must survive idle polls');

ctx.filter='active';ctx.render();
assert.equal(nodes.jobs.children.length,1);
assert.ok(nodes.jobs.children[0].matches('.empty'));
ctx.filter='all';ctx.render();
assert.equal(nodes.jobs.children.length,2);
ctx.jobs=[job('three','failed'),job('four','queued')];ctx.render();
assert.equal(nodes.jobs.children[0].querySelector('.job-error').textContent,'Error');
assert.equal(nodes.jobs.children[1].querySelector('.job-activity').hidden,true);

ctx.setUploadMeter(24);
assert.equal(nodes['upload-fill'].style.width,'24%');
assert.equal(nodes['upload-percent'].textContent,'24%');
assert.equal(nodes['upload-progress'].value,24);
assert.equal(nodes['upload-visual'].dataset.mode,'determinate');
ctx.setUploadMeter(null);
assert.equal(nodes['upload-percent'].hidden,true);
assert.equal(nodes['upload-visual'].dataset.mode,'indeterminate');
assert.equal(nodes['upload-progress'].getAttribute('value'),null);

const html=fs.readFileSync('web/index.html','utf8');
const css=fs.readFileSync('web/public-polish.css','utf8');
assert.match(html,/id="upload-visual"/);
assert.match(html,/id="upload-progress"[^>]*aria-label="Upload"/);
assert.match(css,/prefers-reduced-motion: reduce/);
console.log('STT lifecycle: stable cards, real progress, queued/running/done, reduced motion hooks passed.');
