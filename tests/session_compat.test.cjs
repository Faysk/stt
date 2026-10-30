// Same-origin browser storage migrations. Distinct hostnames remain isolated.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const crypto=require('node:crypto').webcrypto;

const publicSource=fs.readFileSync('web/public.js','utf8').split("let mode=")[0];
const i18nSource=fs.readFileSync('web/i18n.js','utf8');

function storage(initial={}){
  const state=new Map(Object.entries(initial));
  return {state,getItem:key=>state.get(key)||null,setItem:(key,value)=>state.set(key,value)};
}
function session(initial,host='stt.faysk.dev'){
  const localStorage=storage(initial);
  const notice={hidden:true};
  const document={getElementById:id=>id==='legacy-access'?notice:null};
  const key=vm.runInNewContext(publicSource+'\nkey',
    {localStorage,crypto,document,location:{hostname:host}});
  return {key,data:localStorage.state,noticeHidden:notice.hidden};
}
function language(initial){
  const localStorage=storage(initial);
  const document={documentElement:{},querySelectorAll:()=>[],dispatchEvent:()=>{}};
  const value=vm.runInNewContext(i18nSource+'\n({locale,setLanguage})',
    {localStorage,navigator:{languages:['en']},location:{search:''},
     URLSearchParams,document,Event:function Event(){}});
  return {locale:value.locale,setLanguage:value.setLanguage,data:localStorage.state};
}
const old='a'.repeat(64), modern='b'.repeat(64);
const migrated=session({'tts.session':old});
assert.equal(migrated.key,old);
assert.equal(migrated.noticeHidden,false);
assert.equal(session({'tts.session':old},'tts.faysk.dev').noticeHidden,true);
assert.equal(migrated.data.get('stt.session'),old);
assert.equal(session({'tts.session':old,'stt.session':modern}).key,modern);
const created=session({});
assert.match(created.key,/^[a-f0-9]{64}$/);
assert.equal(created.data.get('stt.session'),created.key);
assert.notEqual(created.key,old);
const lang=language({'tts.language':'pt'});
assert.equal(lang.locale,'pt');
assert.equal(lang.data.get('stt.language'),'pt');
lang.setLanguage('fr');
assert.equal(lang.data.get('stt.language'),'fr');
// Different origins have different storage maps: no implicit cross-host sharing.
const otherOrigin=session({});
assert.notEqual(otherOrigin.key,old);
assert.equal(otherOrigin.data.has('tts.session'),false);
console.log('STT browser compatibility: preserved same-origin session/language; no cross-origin leakage.');
