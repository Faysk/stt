const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('web/theme.js','utf8');
function boot({prefersDark=true,stored=null,storageThrows=false,hasMedia=true}={}){
 const handlers={};let mediaHandler;let selected=stored;const writes=[];
 const attrs={};const btn={
   attributes:attrs,title:'',listeners:{},
   setAttribute(key,value){attrs[key]=String(value)},
   addEventListener(name,callback){this.listeners[name]=callback}
 };
 const meta={content:null,setAttribute(key,value){this[key]=value}};
 const document={
   documentElement:{dataset:{},style:{}},
   getElementById:id=>id==='theme-toggle'?btn:null,
   querySelector:selector=>selector==='meta[name="theme-color"]'?meta:null,
   addEventListener(name,fn){handlers[name]=fn}
 };
 const media={matches:prefersDark,addEventListener(name,fn){if(name==='change')mediaHandler=fn}};
 const ctx={window:{matchMedia:hasMedia?()=>media:undefined},
   document,localStorage:{
     getItem(){if(storageThrows)throw Error('privacy mode');return selected},
     setItem(key,value){if(storageThrows)throw Error('privacy mode');selected=value;writes.push([key,value])}
   },
   t:key=>({themeDark:'Switch to dark theme',themeLight:'Switch to light theme'})[key],
 };
 vm.runInNewContext(source,ctx);
 handlers.DOMContentLoaded();
 return {document,btn,meta,writes,media,changed:()=>mediaHandler?.(),languageChanged:()=>handlers.languagechange(),stored:()=>selected};
}
const dark=boot({prefersDark:true});
assert.equal(dark.document.documentElement.dataset.theme,'dark');
assert.equal(dark.btn.attributes['aria-pressed'],'false');
assert.equal(dark.btn.attributes['aria-label'],'Switch to light theme');
assert.equal(dark.meta.content,'#0e1117');
dark.media.matches=false;dark.changed();assert.equal(dark.document.documentElement.dataset.theme,'light');
dark.btn.listeners.click();assert.equal(dark.document.documentElement.dataset.theme,'dark');
assert.equal(dark.stored(),'dark');assert.deepEqual(dark.writes,[['stt.theme','dark']]);
dark.media.matches=true;dark.changed();assert.equal(dark.document.documentElement.dataset.theme,'dark');
dark.media.matches=false;dark.changed();assert.equal(dark.document.documentElement.dataset.theme,'dark','Explicit theme must override system change');
dark.languageChanged();assert.equal(dark.btn.attributes['aria-label'],'Switch to light theme');
dark.btn.listeners.click();assert.equal(dark.document.documentElement.dataset.theme,'light');
assert.equal(dark.btn.attributes['aria-pressed'],'true');
assert.equal(dark.meta.content,'#e9eff5');

const stored=boot({prefersDark:false,stored:'dark'});
assert.equal(stored.document.documentElement.dataset.theme,'dark','Saved override must win over OS preference');
const invalid=boot({prefersDark:false,stored:'not-a-theme'});
assert.equal(invalid.document.documentElement.dataset.theme,'light','Invalid saved value must be ignored');
const privateMode=boot({prefersDark:true,storageThrows:true});
privateMode.btn.listeners.click();assert.equal(privateMode.document.documentElement.dataset.theme,'light','Storage failure cannot block theme changes');
const noMedia=boot({hasMedia:false});
assert.equal(noMedia.document.documentElement.dataset.theme,'light','No matchMedia falls back to usable default');
console.log('STT theme: system default, OS changes, stored override, localization, private mode and fallback passed.');
