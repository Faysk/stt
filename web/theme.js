/* STT public theme: system-following until an explicit local preference is chosen. */
(()=>{
  'use strict';
  const storageKey='stt.theme';
  const media=typeof window.matchMedia==='function'
    ?window.matchMedia('(prefers-color-scheme: dark)'):null;
  let choice=null;
  try{
    const stored=localStorage.getItem(storageKey);
    if(stored==='light'||stored==='dark')choice=stored;
  }catch{}
  const systemTheme=()=>media&&media.matches?'dark':'light';
  const current=()=>choice||systemTheme();
  const apply=()=>{
    const mode=current();
    document.documentElement.dataset.theme=mode;
    document.documentElement.style.colorScheme=mode;
    const toggle=document.getElementById('theme-toggle');
    if(toggle){
      toggle.setAttribute('aria-pressed',String(mode==='light'));
      const next=mode==='light'?'dark':'light';
      const label=typeof t==='function'?t(next==='dark'?'themeDark':'themeLight'):
        (next==='dark'?'Switch to dark theme':'Switch to light theme');
      toggle.setAttribute('aria-label',label);
      toggle.title=label;
    }
    const meta=document.querySelector('meta[name="theme-color"]');
    if(meta)meta.setAttribute('content',mode==='dark'?'#0e1117':'#e9eff5');
  };
  // Must run in the head before CSS is painted, to avoid a light-theme flash.
  apply();
  const onSystemChange=()=>{if(choice===null)apply()};
  if(media){
    if(typeof media.addEventListener==='function')media.addEventListener('change',onSystemChange);
    else if(typeof media.addListener==='function')media.addListener(onSystemChange);
  }
  document.addEventListener('DOMContentLoaded',()=>{
    apply();
    const toggle=document.getElementById('theme-toggle');
    if(toggle)toggle.addEventListener('click',()=>{
      choice=current()==='dark'?'light':'dark';
      try{localStorage.setItem(storageKey,choice)}catch{}
      apply();
    });
  });
  document.addEventListener('languagechange',apply);
})();
