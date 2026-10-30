/* Card-first admin controls. Hidden selects store API-compatible form state. */
(() => {
  'use strict';
  let dashboardData = null;
  let saving = false;
  let formRevision = 0;
  const pendingPreloads = new Set();
  const descriptions = {
    int16: ['INT16 · Inteiros de 16 bits', 'Pesos quantizáveis em INT16 e outras camadas FP32. Exige CPU compatível; usa mais RAM que INT8 e não garante ganho de velocidade.'],
    int8: ['INT8 · Quantizado', 'Menos memória; ponto de partida recomendado para CPU. Outras camadas usam ponto flutuante.'],
    float32: ['FP32 · Completo', 'Cálculos em 32 bits; consome mais memória e não garante melhor transcrição.'],
    int8_float32: ['INT8 + FP32 · Misto', 'Pesos de camadas quantizáveis em INT8; restantes em FP32. Na CPU pode ser equivalente a INT8.'],
    float16: ['FP16 · Metade', '16 bits em ponto flutuante, eficiente em GPU NVIDIA compatível. Bom equilíbrio de velocidade e VRAM.'],
    int8_float16: ['INT8 + FP16 · Misto', 'Pesos quantizados em INT8 e restantes em FP16; economiza VRAM. Compare tempo e resultado.'],
    bfloat16: ['BF16 · 16 bits', 'Outra representação de 16 bits, com maior faixa de expoente; requer suporte CUDA.'],
    int8_bfloat16: ['INT8 + BF16 · Misto', 'Pesos INT8 e restantes em BF16; somente com suporte real do executor.']
  };
  const legacy = {cpu:['int8','float32'], cuda:['float16','int8_float16']};
  const approved = {cpu:['int8','int16','float32','int8_float32'],
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
    // whisper.cpp exposes one validated CPU profile, not CTranslate2 modes.
    if ($('worker').value === 's7-edge')
      return approved[device].filter(type => Array.isArray(exposed) && exposed.includes(type));
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
    const worker = dashboardData?.workers.find(w => w.id === $('worker').value);
    if (!worker || dashboardData.now - worker.seen >= 90)
      return {label:workerCache(model) ? 'Cache relatado · offline' : 'Executor offline', cls:'neutral'};
    if (workerCache(model)) return {label:'No cache do executor', cls:'good'};
    const p = matchingPreload(model);
    if (p && (p.status === 'pending' || p.status === 'running')) {
      return {label:stateLabel(p.status), cls:'working'};
    }
    if (p?.status === 'ready') return {label:'Preparado em disco', cls:'good'};
    if (p?.status === 'failed') return {label:'Falha no preparo', cls:'bad'};
    return {label:'Sob demanda', cls:'neutral'};
  }
  async function requestPreload(model) {
    const worker = $('worker').value;
    if (worker === 's7-edge') {
      showWarning('S7 Edge usa pesos GGML preparados localmente; não aceita preload remoto.');
      return;
    }
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
      const revision = (workerInfo().models || []).find(m => m.id === model)?.revision ||
        matchingPreload(model)?.revision;
      const code = card.querySelectorAll('code')[1];
      if (code) code.textContent = revision
        ? 'Revisão no executor: ' + revision
        : 'Revisão ainda não confirmada neste executor';
      card.dataset.model = model;
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
      const info = workerInfo();
      if ($('device').value === 'cuda' &&
          (!info.gpu || !(info.compute_types?.cuda || []).length)) {
        $('device').value = 'cpu';
        $('settings-message').textContent = 'CUDA indisponível no executor escolhido; seleção ajustada para CPU.';
      }
      precision();
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
      const ram = info.ram_total_mb ? (info.ram_total_mb / 1024).toFixed(1) + ' GB RAM' : 'RAM não informada';
      const types = info.compute_types || {};
      const details = node('div','', 'worker-features');
      details.append(node('span','CPU · ' + (info.cpu || 'n/d') + ' threads','feature-pill'),
        node('span',ram,'feature-pill'),
        node('span',gpu,'feature-pill'),
        node('span',info.gpu ? info.gpu.vram_mb + ' MB VRAM total · ' + (info.gpu.free_vram_mb ?? 'n/d') + ' MB livres (snapshot)' : 'CUDA não detectada','feature-pill'),
        node('span',(info.models || []).length + ' modelos em cache','feature-pill'),
        node('span','CPU: ' + (types.cpu || []).join(', ') + ' / CUDA: ' + (types.cuda || []).join(', '),'feature-pill'));
      card.append(details);
      card.dataset.worker = w.id;
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
  function renderSshTargets() {
    const mount = $('ssh-targets');
    if (!mount) return;
    mount.replaceChildren();
    for (const target of dashboardData.ssh_targets || []) {
      const worker = dashboardData.workers.find(w => w.id === target.id);
      const online = Boolean(worker && dashboardData.now - worker.seen < 90);
      const info = worker?.info || {};
      const types = info.compute_types?.cpu || [];
      const modelReady = (info.models || []).some(m => m.id === $('model').value);
      const capable = online && types.includes($('compute').value) &&
        $('device').value === 'cpu' && modelReady;
      const card = node('article','', 'worker-card ssh-target-card');
      card.append(
        node('strong', target.label + ' · SSH'),
        node('p', capable ? 'Executor STT registrado · validar transcrição real'
          : online ? 'Worker conectado · capacidade do modelo/CPU ainda não validada'
            : 'Somente acesso SSH · nenhum executor STT online', 'small muted'),
        node('code',target.command || 'SSH não configurado no servidor')
      );
      if (target.configured && target.command) {
        const copy = node('button','Copiar comando SSH','button subtle');
        copy.type = 'button';
        copy.addEventListener('click',async () => {
          try {
            if (!navigator.clipboard?.writeText) throw Error('Clipboard indisponível');
            await navigator.clipboard.writeText(target.command);
            $('settings-message').textContent = 'Comando SSH copiado. Execute no terminal com acesso à rede local.';
          } catch {
            $('settings-message').textContent = 'Copie manualmente o comando SSH exibido no card.';
          }
        });
        card.append(copy);
      } else {
        card.append(node('p','Defina STT_S7_SSH_HOST e STT_S7_SSH_USER no .env privado e reinicie a API.','small muted'));
      }
      mount.append(card);
    }
  }
  function renderDevices() {
    const mount = $('device-cards');
    if (!mount) return;
    mount.replaceChildren();
    const info = workerInfo();
    const gpuReady = Boolean(info.gpu && (info.compute_types?.cuda || []).length);
    const devices = [
      {id:'cpu',title:'CPU',details:'Compatibilidade e menor uso de VRAM',meta:(info.cpu || 'n/d') + ' threads lógicas',allowed:true},
      {id:'cuda',title:'GPU · CUDA',details:'Aceleração em hardware NVIDIA',meta:info.gpu ? info.gpu.name + ' · ' + info.gpu.vram_mb + ' MB VRAM' : 'CUDA não confirmada neste executor',allowed:gpuReady}
    ];
    for (const d of devices) {
      const b = node('button','', 'choice-card');
      b.type = 'button';
      b.dataset.device = d.id;
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
    const device = $('device').value;
    const options = optionList(device);
    for (const type of options) {
      const entry = $('worker').value === 's7-edge' && type === 'float32'
        ? ['CPU GGML · Tiny', 'Perfil de execução do whisper.cpp; o arquivo GGML Tiny pode ter pesos FP16. Não equivale ao FP32 do CTranslate2.']
        : descriptions[type] || [type,'Tipo de computação disponibilizado pelo executor.'];
      const b = node('button','', 'choice-card precision-card');
      b.type = 'button';
      b.dataset.compute = type;
      b.classList.toggle('selected',type === current);
      b.setAttribute('aria-pressed',String(type === current));
      b.append(node('strong',type),node('small',entry[0]),node('span',entry[1]));
      const recommended = device === 'cpu' && type === 'int8' ||
        device === 'cuda' && type === 'float16';
      if (recommended) b.append(node('small','Ponto de partida recomendado','choice-recommendation'));
      b.onclick = () => {
        if ($('compute').value !== type) {
          $('compute').value = type;
          markDirty();
        }
        rerender();
      };
      mount.append(b);
    }
    const hint = $('precision-explainer');
    if (hint && $('worker').value === 's7-edge') hint.textContent = 'S7 Edge: whisper.cpp em CPU ARM64, modelo GGML Tiny. O nome float32 é um perfil compatível da API, não uma garantia sobre a precisão de cada peso interno.';
    else if (hint) hint.textContent = device === 'cpu'
      ? 'FP16 (ponto flutuante de 16 bits) é para GPU CUDA compatível. INT16 (inteiros de 16 bits) aparece aqui somente se a CPU anunciar suporte; pode usar mais RAM que INT8. INT8 + FP32 quantiza parte dos pesos, não combina transcrições e não garante qualidade melhor.'
      : 'FP16 usa ponto flutuante de 16 bits. INT8 + FP16 quantiza os pesos de camadas compatíveis e usa FP16 nas demais; geralmente economiza VRAM, mas velocidade e qualidade devem ser medidas. Só mostramos formatos adicionais confirmados pelo executor.';
  }
  const originalRender = renderSettings;
  renderSettings = function(data) {
    const focused = document.activeElement;
    const selector = focused?.dataset?.model ? '[data-model="' + focused.dataset.model + '"]'
      : focused?.dataset?.worker ? '[data-worker="' + focused.dataset.worker + '"]'
      : focused?.dataset?.device ? '[data-device="' + focused.dataset.device + '"]'
      : focused?.dataset?.compute ? '[data-compute="' + focused.dataset.compute + '"]' : null;
    dashboardData = data;
    originalRender(data);
    enhanceModels();
    enhanceWorkers();
    renderSshTargets();
    renderDevices();
    renderPrecisions();
    $('config-summary').textContent = (dirty ? '● Alterações pendentes · ' : '✓ Configuração ativa · ') +
      $('worker').value + ' / ' + $('model').value + ' / ' +
      ($('device').value === 'cuda' ? 'GPU' : 'CPU') + ' / ' + $('compute').value;
    $('config-summary').classList.toggle('has-pending',dirty);
    if (selector) document.querySelector(selector)?.focus({preventScroll:true});
  };
  // Hidden form state keeps the API payload stable; visible cards handle keyboard and pointer input.
  for (const id of ['worker','model','device','compute']) {
    $(id).addEventListener('change',() => {
      markDirty();
      if (id === 'worker') {
        const info = workerInfo();
        if ($('device').value === 'cuda' &&
            (!info.gpu || !(info.compute_types?.cuda || []).length)) {
          $('device').value = 'cpu';
          $('settings-message').textContent = 'CUDA indisponível neste executor; dispositivo ajustado para CPU.';
        }
      }
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
  const originalLoadJobs = loadJobs;
  let pendingHistoryLoads = 0;
  loadJobs = async function(...args) {
    const section = $('history');
    pendingHistoryLoads++;
    section.classList.add('is-fetching');
    if (!$('runs').children.length) {
      for (let n = 0; n < 5; n++) {
        const row = node('tr','','skeleton-row');
        const cell = node('td');
        cell.colSpan = 17;
        cell.append(node('span','','skeleton-bar'));
        row.append(cell);
        $('runs').append(row);
      }
    }
    try {
      const result = await originalLoadJobs(...args);
      const rows = Array.from($('runs').querySelectorAll('tr'));
      for (const [index,job] of lastRows.entries()) {
        const rtf = job.duration > 0 && job.elapsed != null ? job.elapsed / job.duration : null;
        const cell = rows[index]?.children[12];
        if (!cell || rtf == null || !Number.isFinite(rtf)) continue;
        cell.classList.toggle('rtf-fast',rtf < 1);
        cell.classList.toggle('rtf-slow',rtf >= 1);
        cell.title = rtf < 1
          ? 'Processamento mais rápido que a duração do áudio'
          : 'Processamento igual ou mais lento que a duração do áudio';
      }
      return result;
    }
    finally {
      pendingHistoryLoads--;
      if (!pendingHistoryLoads) section.classList.remove('is-fetching');
    }
  };
  const originalLoadPeriod = loadPeriod;
  loadPeriod = async function(...args) {
    $('activity').classList.add('is-fetching');
    $('usage').classList.add('is-fetching');
    try { return await originalLoadPeriod(...args); }
    finally {
      $('activity').classList.remove('is-fetching');
      $('usage').classList.remove('is-fetching');
    }
  };
  const originalLogin = $('login').onclick;
  $('login').onclick = () => {
    if (!$('admin-key').value.trim()) {
      $('admin-message').textContent = 'Informe a chave de administração.';
      return;
    }
    $('login').classList.add('is-loading');
    $('admin-message').textContent = 'Autenticando e carregando o console...';
    originalLogin();
  };
  const originalRefresh = refresh;
  refresh = async function(...args) {
    $('refresh').classList.add('is-loading');
    try { return await originalRefresh(...args); }
    finally {
      $('refresh').classList.remove('is-loading');
      $('login').classList.remove('is-loading');
    }
  };
})();
