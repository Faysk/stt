const $=id=>document.getElementById(id);
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
let key;try{key=localStorage.getItem('tts.session')}catch{}
if(!/^[a-f0-9]{64}$/.test(key||'')){key=Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,'0')).join('');try{localStorage.setItem('tts.session',key)}catch{}}
let mode='file',selected=null,busy=false,polling=false,jobs=[],health=null,filter='all',renderSignature='',currentTranscript='',readerData=null,readerMode='timeline';
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error)}
function failure(status,detail=''){if(status===413)return t('large');if(status===429)return detail.includes('cheia')?t('full'):t('rate');if(status>=500)return t('unavailable');if(status===400||status===422)return t(mode==='link'?'invalid':'error');return t('error')}
async function api(path,options={}){const r=await fetch(path,{...options,headers:{'x-session-key':key,'x-ui-language':locale,...options.headers}});if(!r.ok){const data=await r.json().catch(()=>({}));throw Error(failure(r.status,data.detail))}return r}
function tab(next){mode=next;for(const m of ['file','link']){$(m+'-tab').classList.toggle('active',m===next);$(m+'-tab').setAttribute('aria-selected',String(m===next));$(m+'-panel').hidden=m!==next}message('')}
$('file-tab').onclick=()=>tab('file');$('link-tab').onclick=()=>tab('link');
for(const m of ['file','link'])$(m+'-tab').onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const next=m==='file'?'link':'file';tab(next);$(next+'-tab').focus()}};
function select(file){if(!file)return;if(file.size>90*1024**2){message(t('large'),true);return}selected=file;$('file-label').textContent=file.name;message(`${(file.size/1024**2).toFixed(1)} MB · ${t('ready')}`)}
$('file').onchange=e=>select(e.target.files[0]);$('drop').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('file').click()}};
for(const event of ['dragenter','dragover'])$('drop').addEventListener(event,e=>{e.preventDefault();$('drop').classList.add('drag')});
for(const event of ['dragleave','drop'])$('drop').addEventListener(event,e=>{e.preventDefault();$('drop').classList.remove('drag')});
$('drop').addEventListener('drop',e=>select(e.dataTransfer.files[0]));
function upload(file){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/jobs/upload');xhr.setRequestHeader('x-session-key',key);xhr.setRequestHeader('x-ui-language',locale);xhr.timeout=300000;xhr.upload.onprogress=e=>{if(e.lengthComputable){const n=Math.round(e.loaded/e.total*100);$('upload-progress').value=n;message(`${t('uploading')} · ${n}%`)}};xhr.onload=()=>{if(xhr.status===201)resolve();else{let detail='';try{detail=JSON.parse(xhr.responseText).detail||''}catch{}reject(Error(failure(xhr.status,detail)))}};xhr.onerror=()=>reject(Error(t('network')));xhr.ontimeout=()=>reject(Error(t('timeout')));const data=new FormData();data.append('file',file);xhr.send(data)})}
$('submit').onclick=async()=>{if(busy)return;if(mode==='file'&&!selected){message(t('select'),true);return}if(mode==='link'&&!$('url').value.trim()){message(t('paste'),true);return}busy=true;$('submit').disabled=true;message(t(mode==='file'?'uploading':'preparing')+'…');$('upload-progress').hidden=mode!=='file';try{if(mode==='file'){await upload(selected);selected=null;$('file').value='';$('file-label').textContent=t('drop')}else{await api('/api/jobs/youtube',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url:$('url').value.trim()})});$('url').value=''}message(t('sent'));await refresh();$('library').scrollIntoView({behavior:'smooth',block:'start'})}catch(e){message(e.message||t('error'),true)}finally{busy=false;$('submit').disabled=false;$('upload-progress').hidden=true}};
function date(n){return new Date(n*1000).toLocaleString(locale,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}
function duration(n){if(!n)return '—';const m=Math.floor(n/60);return m?`${m} ${t('minutes')} ${Math.round(n%60)} ${t('seconds')}`:`${Math.round(n)} ${t('seconds')}`}
async function download(id,fmt){try{const r=await api(`/api/jobs/${id}/download/${fmt}`);const url=URL.createObjectURL(await r.blob());const a=el('a');a.href=url;const title=jobs.find(j=>j.id===id)?.title||'transcript';const filename=title.replace(/\.[a-z0-9]{2,5}$/i,'').replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_').slice(0,120).trim()||'transcript';a.download=`${filename}.${fmt}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){message(e.message,true)}}
function timestamp(n){const value=Math.max(0,Math.floor(n||0));return `${String(Math.floor(value/3600)).padStart(2,'0')}:${String(Math.floor(value/60)%60).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`}
function readingBlocks(segments){
 const blocks=[];let block=null;
 for(const segment of segments){const text=segment.text.trim();if(!text)continue;
  if(block&&(segment.start-block.end>3||block.text.length>=600||block.text.length>=320&&/[.!?。؟।]$/.test(block.text))){blocks.push(block);block=null}
  if(!block)block={start:segment.start,end:segment.end,text};
  else{const separator=/[\u3400-\u9fff]$/.test(block.text)&&/^[\u3400-\u9fff]/.test(text)?'':' ';block.text+=separator+text;block.end=segment.end}
 }
 if(block)blocks.push(block);return blocks;
}
function renderReader(){
 const content=$('preview-text');content.replaceChildren();content.classList.toggle('plain-reading',readerMode==='plain');
 for(const b of document.querySelectorAll('[data-reader-mode]')){const active=b.dataset.readerMode===readerMode;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))}
 if(!readerData)return;
 const segments=readingBlocks(readerData.segments||[]);
 if(readerMode==='plain'||!segments.length){const paragraphs=segments.length?segments.map(s=>s.text):currentTranscript.split(/\n\s*\n/);for(const paragraph of paragraphs)content.append(el('p','reader-paragraph',paragraph));return}
 for(const segment of segments){const row=el('div','reader-row');const time=el('time','reader-time',timestamp(segment.start));time.title=`${timestamp(segment.start)} – ${timestamp(segment.end)}`;row.append(time,el('p','reader-paragraph',segment.text.trim()));content.append(row)}
}
async function readTranscript(j,button){
 button.disabled=true;
 try{const [data,structured]=await Promise.all([api(`/api/jobs/${j.id}`).then(r=>r.json()),api(`/api/jobs/${j.id}/download/json`).then(r=>r.json())]);
  currentTranscript=data.text;readerData=structured;$('preview-title').textContent=j.title||t('read');$('reader-meta').textContent=[duration(j.duration),structured.language?.toUpperCase()].filter(Boolean).join(' · ');$('copy').textContent=t('copy');renderReader();$('preview').showModal();$('preview-text').scrollTop=0;
 }catch(e){message(e.message,true)}finally{button.disabled=false}
}
function render(){
 const query=$('search').value.toLocaleLowerCase();const signature=JSON.stringify([jobs,locale,filter,query]);if(signature===renderSignature)return;renderSignature=signature;
 const visible=jobs.filter(j=>(filter==='all'||filter==='active'&&['queued','running','uploading'].includes(j.status)||filter===j.status)&&(j.title||'').toLocaleLowerCase().includes(query));
 $('count').textContent=jobs.length;$('library-count').textContent=jobs.length;$('jobs').replaceChildren();
 if(!visible.length){const e=el('div','empty');e.append(el('span','empty-mark','≋'),el('strong','',t(jobs.length?'noMatch':'empty')),el('p','muted',t('emptyHint')));$('jobs').append(e);return}
 for(const j of visible){
  const card=el('article','transcript-card');const head=el('div','transcript-card-head');const icon=el('span','file-symbol',j.title==='YouTube'||j.title?.includes('YouTube')?'↗':'≋');const info=el('div','transcript-info');const name=el('strong','transcript-title',j.title||'Transcript');info.append(name,el('span','transcript-date',date(j.created)));head.append(icon,info);card.append(head);
  const state=el('span',`badge state-${j.status}`,t(j.status==='running'?(j.stage||'transcribing'):j.status));if(j.status==='queued'&&j.position)state.append(document.createTextNode(` · #${j.position}`));if(j.status==='running'&&j.progress>0)state.append(document.createTextNode(` · ${Math.round(j.progress)}%`));
  const meta=el('div','transcript-card-meta');meta.append(state,el('span','',duration(j.duration)),el('span','expiry',`${t('expires')} ${date(j.expires)}`));card.append(meta);
  if(j.status==='failed')card.append(el('p','error small',t('error')));
  if(j.status==='done'){
   const actions=el('div','transcript-card-actions');const read=el('button','button read-button',t('read')+' ↗');read.onclick=()=>readTranscript(j,read);
   const formats=el('div','download-formats');formats.setAttribute('role','group');formats.setAttribute('aria-label',t('download'));
   for(const format of ['txt','md','srt','json']){const down=el('button','format-button',format.toUpperCase());down.title=`${t('download')} ${format.toUpperCase()}`;down.setAttribute('aria-label',`${t('download')} ${format.toUpperCase()}`);down.onclick=async()=>{down.disabled=true;try{await download(j.id,format)}finally{down.disabled=false}};formats.append(down)}
   actions.append(read,formats);card.append(actions);
  }
  $('jobs').append(card);
 }
}
function showHealth(){if(!health)return;$('health').dataset.pressure=!health.online?'offline':health.paused?'paused':health.pressure;$('health-text').textContent=health.paused?t('paused'):!health.online?t('offline'):`${t('online')} · ${health.queue} ${t('inQueue')}`;}
async function refresh(){if(polling)return;polling=true;try{const [h,list]=await Promise.all([fetch('/api/health').then(r=>{if(!r.ok)throw Error();return r.json()}),api('/api/jobs').then(r=>r.json())]);health=h;jobs=list;showHealth();render()}catch{$('health').dataset.pressure='offline';$('health-text').textContent=t('unavailable')}finally{polling=false}}
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{filter=b.dataset.filter;for(const x of document.querySelectorAll('[data-filter]')){x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b))}render()};
$('search').oninput=render;$('close-preview').onclick=()=>$('preview').close();$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(currentTranscript);$('copy').textContent=t('copied')}catch{$('preview-text').focus()}};
for(const [value,name]of Object.entries(languageNames)){const option=el('option','',name);option.value=value;$('language').append(option)}$('language').value=locale;$('language').onchange=e=>setLanguage(e.target.value);
document.addEventListener('languagechange',()=>{$('language').value=locale;if(selected)$('file-label').textContent=selected.name;showHealth();render();message('')});for(const b of document.querySelectorAll('[data-reader-mode]'))b.onclick=()=>{readerMode=b.dataset.readerMode;renderReader()};$('preview').addEventListener('close',()=>{readerData=null;currentTranscript='';$('preview-text').replaceChildren()});applyLanguage();refresh();setInterval(()=>{if(!document.hidden)refresh()},5000);
