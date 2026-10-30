/* Progressive admin controls. Keeps the existing API and HTML selects as fallbacks. */
(() => {
  'use strict';
  let dashboardData = null;
  let saving = false;
  let formRevision = 0;
  const pendingPreloads = new Set();
  const descriptions = {
    int8: ['8-bit', 'Menor consumo de memória; ótimo ponto de partida na CPU.'],
    float32: ['32-bit', 'Precisão numérica máxima; maior custo de memória e processamento.'],
    int8_float32: ['8 + 32-bit', 'Pesos quantizados e operações em 32 bits; requer suporte do executor.'],
    float16: ['16-bit', 'Boa velocidade e memória na GPU CUDA.'],
    int8_float16: ['8 + 16-bit', 'Pesos quantizados com computação em 16 bits; reduz VRAM.'],
    bfloat16: ['BF16', 'Faixa numérica ampla com 16 bits; depende da GPU e do motor.'],
    int8_bfloat16: ['8 + BF16', 'Quantização mista; disponível apenas em builds compatíveis.']
  };
  const legacy = {cpu:['int8','float32'], cuda:['float16','int8_float16']};
  const approved = {cpu:['int8','float32','int8_float32'],
                    cuda:['float16','int8_float16','float32','int8','bfloat16','int8_bfloat16']};
  const workerInfo = () => dashboardData?.workers.find(w => w.id === $('worker').value)?.info || {};
  const workerCache = model => (workerInfo().models || []).some(m => m.id === model);
  const matchingPreload = model => (dashboardData?.preloads || []).find(p =>
    p.worker === $('worker').value && p.model === model);
  const stateLabel = state => ({
    pending:'Na fila de preparo', running:'Baixando / validando',
    ready:'Preparo concluído', failed:'Falhou'
  }[state] || 'Sob demanda');
  const markDirty = () => {
    dirty = true;
    formRevision++;
    $('settings-message').textContent = 'Alterações ainda não salvas.';
  };
  const showWarning = text => {
    $('settings-message').textContent = text;
    $('settings-message').classList.add('error');
  };
  const optionList = device => {
    const exposed = workerInfo().compute_types?.[device];
    return approved[device].filter(type =>
      legacy[device].includes(type) || (Array.isArray(exposed) && exposed.includes(type)));
  };
  precision = function() {
    const device = $('device').value;
    const current = $('compute').value;
    const options = optionList(device);
    const select = $('compute');
    select.replaceChildren(...options.map(value => {
      const option = node('option',value);
      option.value = value;
      return option;
    }));
    select.value = options.includes(current) ? current : (options[0] || '');
  };
  function rerender() {
    if (dashboardData) renderSettings(dashboardData);
  }
  function stateOf(model) {
    if (workerCache(model)) return {label:'No cache do executor', cls:'good'};
    const p = matchingPreload(model);
    if (p && (p.status === 'pending' || p.status === 'running')) {
      return {label:stateLabel(p.status), cls:'working'};
    }
    if (p?.status === 'failed') return {label:'Falha no preparo', cls:'bad'};
    return {label:'Sob demanda', cls:'neutral'};
  }
  async function requestPreload(model) {
    const worker = $('worker').value;
    const id = worker + ':' + model;
    const status = stateOf(model);
    if (status.cls === 'good' || status.cls === 'working' || pendingPreloads.has(id)) return;
    pendingPreloads.add(id);
    rerender();
    try {
      await api('/api/admin/model-preloads', {
        method:'POST', body:JSON.stringify({worker,model})
      });
      $('settings-message').textContent = 'Preparação solicitada no executor ' + worker + '.';
      await refresh(false);
    } catch(error) {
      showWarning('Não foi possível preparar ' + model + ': ' + error.message);
    } finally {
      pendingPreloads.delete(id);
      rerender();
    }
  }
  function enhanceModels() {
    const selected = $('model').value;
    for (const card of $('model-catalog').querySelectorAll('.model-card')) {
      const model = card.querySelector('h3')?.firstChild?.textContent?.trim();
      if (!model) continue;
      const state = stateOf(model);
      const tag = card.querySelector('.badge');
      if (tag) {
        tag.textContent = pendingPreloads.has($('worker').value + ':' + model)
          ? 'Solicitando...' : state.label;
        tag.className = 'badge ' + (pendingPreloads.has($('worker').value + ':' + model) ? 'working' : state.cls);
      }
      const isSelected = model === selected;
      const isActive = model === dashboardData.settings.model && $('worker').value === dashboardData.settings.worker;
      card.classList.toggle('active',isSelected);
      card.classList.toggle('pending-selection',isSelected && !isActive);
      card.setAttribute('role','button');
      card.setAttribute('tabindex','0');
      card.setAttribute('aria-pressed',String(isSelected));
      card.setAttribute('aria-label','Selecionar modelo ' + model + '. ' + state.label);
      if (isSelected) {
        const flag = node('span',isActive ? 'Configuração ativa' : 'Seleção pendente','selection-flag');
        card.append(flag);
      }
      for (const link of card.querySelectorAll('a')) link.addEventListener('click',e => e.stopPropagation());
      const selectModel = () => {
        if ($('model').value !== model) {
          $('model').value = model;
          markDirty();
        }
        rerender();
        void requestPreload(model);
      };
      card.addEventListener('click',e => {
        if (e.target.closest('a,button')) return;
        selectModel();
      });
      card.addEventListener('keydown',e => {
        if (e.target !== card || !['Enter',' '].includes(e.key)) return;
        e.preventDefault();
        selectModel();
      });
    }
  }
  function chooseWorker(id) {
    if ($('worker').value !== id) {
      $('worker').value = id;
      markDirty();
    }
    rerender();
  }
  function enhanceWorkers() {
    const workers = dashboardData.workers;
    const cards = Array.from($('workers').querySelectorAll('.worker-card'));
    cards.forEach((card,index) => {
      const w = workers[index], info = w.info || {}, online = dashboardData.now - w.seen < 90;
      if (!w) return;
      const gpu = info.gpu?.name || 'GPU não detectada';
      const types = info.compute_types || {};
      const details = node('div','', 'worker-features');
      details.append(node('span','CPU · ' + (info.cpu || 'n/d') + ' threads','feature-pill'),
        node('span',gpu,'feature-pill'),
        node('span',(info.models || []).length + ' modelos em cache','feature-pill'),
        node('span','CPU: ' + (types.cpu || []).join(', ') + ' / CUDA: ' + (types.cuda || []).join(', '),'feature-pill'));
      card.append(details);
      const selected = $('worker').value === w.id;
      card.classList.toggle('is-selected',selected);
      card.classList.toggle('is-offline',!online);
      card.setAttribute('role','button');
      card.setAttribute('tabindex','0');
      card.setAttribute('aria-pressed',String(selected));
      card.setAttribute('aria-label','Selecionar executor ' + w.id + (online ? ', online' : ', offline'));
      card.addEventListener('click',() => chooseWorker(w.id));
      card.addEventListener('keydown',e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chooseWorker(w.id); }
      });
    });
  }
  function renderDevices() {
    const mount = $('device-cards');
    if (!mount) return;
    mount.replaceChildren();
    const info = workerInfo();
    const gpuReady = Boolean(info.gpu && (info.compute_types?.cuda || []).length);
    const devices = [
      {id:'cpu',title:'CPU',details:'Compatibilidade e menor uso de VRAM',meta:(info.cpu || 'n/d') + ' threads lógicas',allowed:true},
      {id:'cuda',title:'GPU · CUDA',details:'Aceleração em hardware NVIDIA',meta:info.gpu?.name || 'CUDA não confirmada neste executor',allowed:gpuReady}
    ];
    for (const d of devices) {
      const b = node('button','', 'choice-card');
      b.type = 'button';
      b.disabled = !d.allowed && $('device').value !== d.id;
      b.classList.toggle('selected',$('device').value === d.id);
      b.setAttribute('aria-pressed',String($('device').value === d.id));
      b.append(node('strong',d.title),node('span',d.details),node('small',d.meta));
      b.onclick = () => {
        if ($('device').value !== d.id) {
          $('device').value = d.id;
          precision();
          markDirty();
        }
        rerender();
      };
      mount.append(b);
    }
  }
  function renderPrecisions() {
    const mount = $('precision-cards');
    if (!mount) return;
    mount.replaceChildren();
    const current = $('compute').value;
    for (const type of optionList($('device').value)) {
      const entry = descriptions[type] || [type,'Tipo de computação disponibilizado pelo executor.'];
      const b = node('button','', 'choice-card precision-card');
      b.type = 'button';
      b.classList.toggle('selected',type === current);
      b.setAttribute('aria-pressed',String(type === current));
      b.append(node('strong',type),node('small',entry[0]),node('span',entry[1]));
      b.onclick = () => {
        if ($('compute').value !== type) {
          $('compute').value = type;
          markDirty();
        }
        rerender();
      };
      mount.append(b);
    }
  }
  const originalRender = renderSettings;
  renderSettings = function(data) {
    dashboardData = data;
    originalRender(data);
    enhanceModels();
    enhanceWorkers();
    renderDevices();
    renderPrecisions();
  };
  // Native selects remain keyboard-accessible; visual cards reflect their values.
  for (const id of ['worker','model','device','compute']) {
    $(id).addEventListener('change',() => {
      markDirty();
      if (id === 'device' || id === 'worker') precision();
      rerender();
    });
  }
  $('settings-form').addEventListener('input',() => { formRevision++; });
  $('settings-form').onsubmit = async e => {
    e.preventDefault();
    if (saving) return;
    saving = true;
    const revision = formRevision;
    const submit = $('settings-form').querySelector('button[type="submit"]');
    submit.disabled = true;
    submit.classList.add('is-loading');
    submit.textContent = 'Salvando...';
    $('settings-message').textContent = 'Aplicando configuração para próximas execuções...';
    $('settings-message').classList.remove('error');
    try {
      await api('/api/admin/settings',{method:'PUT',body:JSON.stringify({
        worker:$('worker').value, model:$('model').value,
        device:$('device').value, compute:$('compute').value,
        paused:$('paused').checked
      })});
      if (revision === formRevision) dirty = false;
      $('settings-message').textContent = dirty
        ? 'Configuração salva; ainda existem alterações pendentes.'
        : '✓ Configuração salva para próximas execuções.';
      await refresh(false);
    } catch(error) {
      showWarning('Não foi possível salvar: ' + error.message);
    } finally {
      saving = false;
      submit.disabled = false;
      submit.classList.remove('is-loading');
      submit.textContent = 'Salvar configuração';
    }
  };
  const originalRefresh = refresh;
  refresh = async function(...args) {
    $('refresh').classList.add('is-loading');
    try { return await originalRefresh(...args); }
    finally { $('refresh').classList.remove('is-loading'); }
  };
})();
