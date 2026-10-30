const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('web/i18n.js','utf8');
function boot(languages,saved=null,search=''){
  const document={documentElement:{},querySelectorAll:()=>[],dispatchEvent:()=>{}};
  const context={navigator:{languages},localStorage:{getItem:()=>saved,setItem:()=>{}},location:{search},URLSearchParams,document,Event};
  return vm.runInNewContext(source+'\n({strings,locale,languageNames,applyLanguage,setLanguage,t,document})',context);
}
const base=boot(['xx']);
assert.equal(base.locale,'en');
assert.equal(Object.keys(base.strings).length,10);
for(const [language,values]of Object.entries(base.strings)){
  assert.deepEqual(Object.keys(values).sort(),Object.keys(base.strings.en).sort(),language+' has missing interface keys');
  assert.ok(Object.values(values).every(v=>typeof v==='string'&&v.length>0));
}
assert.equal(boot(['pt-BR','en']).locale,'pt');
assert.equal(boot(['en'],'fr').locale,'fr');
assert.equal(boot(['en'],'fr','?lang=id').locale,'id');
const arabic=boot(['ar-SA']);arabic.applyLanguage();assert.equal(arabic.document.documentElement.dir,'rtl');
arabic.setLanguage('en');assert.equal(arabic.document.documentElement.dir,'ltr');
assert.equal(boot(['ur-PK']).locale,'ur');
console.log('Localization: ten complete dictionaries, browser detection, fallback, selection and RTL passed.');
