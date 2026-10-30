const $=id=>document.getElementById(id);
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
const validSession=value=>/^[a-f0-9]{64}$/.test(value||'');
let key;
try{
 key=localStorage.getItem('stt.session');
}catch{}
if(!validSession(key)){key=Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,'0')).join('');try{localStorage.setItem('stt.session',key)}catch{}}
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
function setUploadMeter(value){
 const native=$('upload-progress'),visual=$('upload-visual'),fill=$('upload-fill'),percent=$('upload-percent');
 if(value===null){
  visual.dataset.mode='indeterminate';native.removeAttribute('value');percent.hidden=true;fill.style.width='0%';return;
 }
 const n=Math.max(0,Math.min(100,Math.round(value)));
 visual.dataset.mode='determinate';native.value=n;fill.style.width=n+'%';percent.textContent=n+'%';percent.hidden=false;
}
function upload(file){return new Promise((resolve,reject)=>{
 const xhr=new XMLHttpRequest();let previous=-1;
 xhr.open('POST','/api/jobs/upload');xhr.setRequestHeader('x-session-key',key);xhr.setRequestHeader('x-ui-language',locale);xhr.timeout=300000;
 xhr.upload.onprogress=e=>{
  if(!e.lengthComputable){setUploadMeter(null);return}
  const n=Math.round(e.loaded/e.total*100);
  if(n!==previous){previous=n;setUploadMeter(n)}
 };
 xhr.onload=()=>{
  if(xhr.status===201){setUploadMeter(100);resolve()}
  else{let detail='';try{detail=JSON.parse(xhr.responseText).detail||''}catch{}reject(Error(failure(xhr.status,detail)))}
 };
 xhr.onerror=()=>reject(Error(t('network')));
 xhr.ontimeout=()=>reject(Error(t('timeout')));
 const data=new FormData();data.append('file',file);xhr.send(data);
})}
$('submit').onclick=async()=>{
 if(busy)return;
 if(mode==='file'&&!selected){message(t('select'),true);return}
 if(mode==='link'&&!$('url').value.trim()){message(t('paste'),true);return}
 busy=true;$('submit').disabled=true;
 const uploadPanel=document.querySelector('.upload-panel');
 uploadPanel.classList.add('is-busy');
 $('upload-visual').hidden=false;$('upload-progress').hidden=false;
 $('upload-progress').setAttribute('aria-label',t(mode==='file'?'uploading':'preparing'));
 setUploadMeter(mode==='file'?0:null);
 message(t(mode==='file'?'uploading':'preparing')+'…');
 try{
  if(mode==='file'){
   await upload(selected);selected=null;$('file').value='';$('file-label').textContent=t('drop');
  }else{
   await api('/api/jobs/youtube',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url:$('url').value.trim()})});
   $('url').value='';
  }
  message(t('sent'));
  await refresh();
  $('library').scrollIntoView({behavior:'smooth',block:'start'});
 }catch(e){message(e.message||t('error'),true)}
 finally{
  busy=false;$('submit').disabled=false;uploadPanel.classList.remove('is-busy');
  $('upload-progress').hidden=true;$('upload-visual').hidden=true;
 }
};
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
  currentTranscript=data.text;readerData=structured;readerMode=structured.timing==='none'?'plain':'timeline';const timelineButton=document.querySelector('[data-reader-mode="timeline"]');timelineButton.disabled=structured.timing==='none';timelineButton.title=structured.timing==='none'?'Timestamps unavailable for this model':'';$('preview-title').textContent=j.title||t('read');$('reader-meta').textContent=[duration(j.duration),structured.language?.toUpperCase()].filter(Boolean).join(' · ');$('copy').textContent=t('copy');renderReader();$('preview').showModal();$('preview-text').scrollTop=0;
 }catch(e){message(e.message,true)}finally{button.disabled=false}
}
function makeJobCard(j){
 const card=el('article','transcript-card');card.dataset.jobId=j.id;
 const head=el('div','transcript-card-head');
 const icon=el('span','file-symbol');
 const info=el('div','transcript-info');
 info.append(el('strong','transcript-title'),el('span','transcript-date'));
 head.append(icon,info);
 const meta=el('div','transcript-card-meta');
 meta.append(el('span','badge'),el('span','job-duration'),el('span','expiry'));
 const activity=el('div','job-activity');activity.hidden=true;
 const track=el('span','job-activity-track');
 track.append(el('span','job-activity-fill'));activity.append(track);
 card.append(head,meta,activity);
 return card;
}
function updateJobCard(card,j){
 const prior=card.dataset.status;
 const stage=j.status==='running'?(j.stage||'transcribing'):j.status;
 const stageChanged=prior&&(prior!==j.status||card.dataset.stage!==stage);
 card.dataset.status=j.status;card.dataset.stage=stage;
 card.querySelector('.file-symbol').textContent=j.title==='YouTube'||j.title?.includes('YouTube')?'↗':'≋';
 card.querySelector('.transcript-title').textContent=j.title||'Transcript';
 card.querySelector('.transcript-date').textContent=date(j.created);
 const meta=card.querySelector('.transcript-card-meta');
 const stateText=t(j.status==='done'?'readyState':j.status==='running'?(j.stage||'transcribing'):j.status)
  +(j.status==='queued'&&j.position?' · #'+j.position:'')
  +(j.status==='running'&&Number.isFinite(j.progress)&&j.progress>0?' · '+Math.round(j.progress)+'%':'');
 let state=meta.querySelector('.badge');
 if(stageChanged){
  const updated=el('span','badge state-'+j.status+' state-arrive',stateText);
  state.replaceWith(updated);state=updated;
 }else{
  state.className='badge state-'+j.status;
  if(state.textContent!==stateText)state.textContent=stateText;
 }
 if(!state.textContent)state.textContent=stateText;
 card.querySelector('.job-duration').textContent=duration(j.duration);
 card.querySelector('.expiry').textContent=t('expires')+' '+date(j.expires);
 const activity=card.querySelector('.job-activity');
 activity.hidden=j.status!=='running';
 if(j.status==='running'){
  const progress=Number.isFinite(j.progress)&&j.progress>0?Math.max(0,Math.min(100,j.progress)):null;
  const indeterminate=progress===null;
  activity.classList.toggle('is-indeterminate',indeterminate);
  const fill=activity.querySelector('.job-activity-fill');
  fill.style.width=indeterminate?'0%':progress+'%';
  if(indeterminate){
   activity.removeAttribute('role');activity.removeAttribute('aria-valuenow');
   activity.setAttribute('aria-hidden','true');
  }else{
   activity.removeAttribute('aria-hidden');activity.setAttribute('role','progressbar');
   activity.setAttribute('aria-label',t('transcribing'));
   activity.setAttribute('aria-valuemin','0');activity.setAttribute('aria-valuemax','100');
   activity.setAttribute('aria-valuenow',String(Math.round(progress)));
  }
 }
 let error=card.querySelector('.job-error');
 if(j.status==='failed'){
  if(!error){error=el('p','error small job-error');activity.after(error)}
  error.textContent=t('error');
 }else if(error)error.remove();
 let actions=card.querySelector('.transcript-card-actions');
 if(j.status!=='done'){if(actions)actions.remove();return}
 if(!actions){
  actions=el('div','transcript-card-actions');
  actions.append(el('button','button read-button'),el('div','download-formats'));
  card.append(actions);
 }
 const read=actions.querySelector('.read-button');
 read.textContent=t('read')+' ↗';
 read.onclick=()=>readTranscript(j,read);
 const formats=actions.querySelector('.download-formats');
 formats.setAttribute('role','group');formats.setAttribute('aria-label',t('download'));
 const allowed=j.timing==='none'?['txt','md','json']:['txt','md','srt','json'];
 if(formats.dataset.allowed!==allowed.join(',')){
  formats.dataset.allowed=allowed.join(',');formats.replaceChildren();
  for(const fmt of allowed){
   const down=el('button','format-button',fmt.toUpperCase());
   down.onclick=async()=>{down.disabled=true;try{await download(j.id,fmt)}finally{down.disabled=false}};
   formats.append(down);
  }
 }
 for(const down of formats.children){
  down.title=t('download')+' '+down.textContent;
  down.setAttribute('aria-label',down.title);
 }
}
function render(){
 const query=$('search').value.toLocaleLowerCase();
 const signature=JSON.stringify([jobs,locale,filter,query]);
 if(signature===renderSignature)return;
 renderSignature=signature;
 const visible=jobs.filter(j=>(filter==='all'||filter==='active'&&['queued','running','uploading'].includes(j.status)||filter===j.status)
  &&(j.title||'').toLocaleLowerCase().includes(query));
 $('count').textContent=jobs.length;$('library-count').textContent=jobs.length;
 const container=$('jobs');
 if(!visible.length){
  const label=t(jobs.length?'noMatch':'empty'),hint=t('emptyHint');
  const existing=container.querySelector('.empty');
  if(existing&&container.children.length===1){
   existing.querySelector('strong').textContent=label;
   existing.querySelector('p').textContent=hint;
  }else{
   const empty=el('div','empty');
   empty.append(el('span','empty-mark','≋'),el('strong','',label),el('p','muted',hint));
   container.replaceChildren(empty);
  }
  return;
 }
 const cards=new Map([...container.querySelectorAll('.transcript-card')].map(card=>[card.dataset.jobId,card]));
 const keep=new Set();
 for(let i=0;i<visible.length;i++){
  const j=visible[i];let card=cards.get(j.id);
  if(!card){card=makeJobCard(j);if(i<7)card.classList.add('card-appear')}
  updateJobCard(card,j);
  if(container.children[i]!==card)container.insertBefore(card,container.children[i]||null);
  keep.add(card);
 }
 for(const child of [...container.children])if(!keep.has(child))child.remove();
}
function showHealth(){if(!health)return;$('health').dataset.pressure=!health.online?'offline':health.paused?'paused':health.pressure;$('health-text').textContent=health.paused?t('paused'):!health.online?t('offline'):`${t('online')} · ${health.queue} ${t('inQueue')}`;}
async function refresh(){if(polling)return;polling=true;try{const [h,list]=await Promise.all([fetch('/api/health').then(r=>{if(!r.ok)throw Error();return r.json()}),api('/api/jobs').then(r=>r.json())]);health=h;jobs=list;showHealth();render()}catch{$('health').dataset.pressure='offline';$('health-text').textContent=t('unavailable')}finally{polling=false}}
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{filter=b.dataset.filter;for(const x of document.querySelectorAll('[data-filter]')){x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b))}render()};
$('search').oninput=render;$('close-preview').onclick=()=>$('preview').close();$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(currentTranscript);$('copy').textContent=t('copied')}catch{$('preview-text').focus()}};
for(const [value,name]of Object.entries(languageNames)){const option=el('option','',name);option.value=value;$('language').append(option)}$('language').value=locale;$('language').onchange=e=>setLanguage(e.target.value);
document.addEventListener('languagechange',()=>{$('language').value=locale;if(selected)$('file-label').textContent=selected.name;showHealth();render();message('')});for(const b of document.querySelectorAll('[data-reader-mode]'))b.onclick=()=>{readerMode=b.dataset.readerMode;renderReader()};$('preview').addEventListener('close',()=>{readerData=null;currentTranscript='';document.querySelector('[data-reader-mode="timeline"]').disabled=false;$('preview-text').replaceChildren()});applyLanguage();refresh();setInterval(()=>{if(!document.hidden)refresh()},5000);
