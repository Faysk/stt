# Operação: correção de quota Cloudflare 1027 (S7 como coordenador)

**Revisão:** 03/11/2026. **Incidente:** [#97](https://github.com/Faysk/stt/issues/97). **PRs:** [#101](https://github.com/Faysk/stt/pull/101) comunicação S7 local, [#103](https://github.com/Faysk/stt/pull/103) frontend, [#105](https://github.com/Faysk/stt/pull/105) polling integrado. Métricas/runbook de conta: [IaC #64](https://github.com/Faysk/iac/issues/64).

> **Não execute o `publish-stt.ps1 -Apply` no ambiente S7-autônomo.** Esse roteiro legado ainda para/inicia API Windows/porta 8765; a fila/banco autoritativos estão no S7/loopback:8786 desde 01/11. Não é procedimento de publicação deste incidente.

## 0. Pré-condições (somente leitura e sem segredos em logs)

- [ ] Confirmar que o coordenador **autoritativo** continua no S7, DB no microSD e API local `127.0.0.1:8786`; Tunnel `faysk-stt` ativo e **sem mudanças de DNS**.
- [ ] Verificar operador com controle da sessão do STT, **zero jobs em `running`, `uploading` ou `queued`** e nenhuma entrega pendente no outbox do S7/notebook. Caso Cloudflare 1027 impossibilite consultar o painel, **não declarar a fila drenada**; obter evidência local autenticada fora do Worker.
- [ ] Prever rollback por commit, e backup operacional já existente do SQLite S7 sem copiar/transcrever dados. Nenhum segredo em Git, screenshots, logs ou chat.
- [ ] Confirmar commit, branch main, ambiente Python/Node, chaves SSH verificadas (host key), chave privada S7 0600, e `.env` privado do S7 mantido pelo provisionamento existente.
- [ ] Confirmar CI de todos os commits envolvidos. Anotar SHA de Worker/cloudflared atual para recuperação.

## 1. Buscar a versão aprovada (Windows; sem deploy)

```powershell
cd E:\Projects\stt
git status --short
git pull --ff-only origin main
git rev-parse HEAD
& .\.venv\Scripts\python.exe -m pytest -q
npm run check
npm run deploy:stt:dry-run
```

**Não** compartilhar saída com tokens, arquivos pessoais ou dados de jobs. O `npm run deploy:stt:dry-run` não muda Workers.

## 2. S7 worker isolado (não reiniciar API, cloudflared, Observatory)

Exemplo usando endereço/IP conhecido previamente pelo operador. **Verifique IP e host key; não copie o exemplo como garantia de endereço atual.**

```powershell
$HostS7 = "192.168.1.2" # confirmar pelo inventário/SSH conhecido
$UserS7 = "u0_a279"
$KeyS7 = Join-Path $HOME ".ssh/s7_lmonitor_ed25519"

# Preview: não instala, não inicia, não altera Tunnel/API
& .\.venv\Scripts\python.exe scripts/setup_s7_service.py --host $HostS7 --user $UserS7 --port 8022 --identity $KeyS7
```

Após validar a fila e o plano de rollback e **somente em janela aprovada**, a atualização do worker é feita por `scripts/setup_s7_service.py --apply` com os mesmos parâmetros (sem `--start`). Esse instalador preserva serviços alheios e executa pré-condições de SD/armazenamento/symlink; ele escreve a versão privada do worker e entrypoint runit, mas não garante que o processo **já em execução** tenha recarregado. Reiniciar **apenas** o serviço runit do worker por SSH verificado, não usar kill genérico do Python, e verificar status com `sv status` após confirmar que não há trabalho/outbox pendente.

A nova entrypoint usa `STT_URL=http://127.0.0.1:8786`. O agente envia `x-worker-key` escopada e lê **apenas** o valor `STT_EDGE_KEY` do arquivo privado `$HOME/.local/share/stt-api/runtime/.env` (modo 0600) para satisfazer o middleware; não publica a chave e **não** expõe a porta. Falha fechada caso a chave não exista ou seja inválida. **Jamais** desligar `STT_REQUIRE_EDGE`.

## 3. Notebook: somente executor remoto

- Verificar `STT_HOST_API=0`, `STT_URL=https://stt.faysk.dev`, supervisor/worker e que o notebook **não** iniciou API antiga nem Tunnel alternativo.
- Atualizar código pelo Git; atualizar/reiniciar o processo **apenas do worker remoto**, em janela sem jobs/outbox, com supervisor conhecido. Não chamar `publish-stt.ps1 -Apply`, nem iniciar segunda API/banco Windows. Não desligar GPU durante transcrição.
- Parâmetros padrão: `STT_WORKER_IDLE_SECONDS=20`, `STT_WORKER_DISABLED_SECONDS=30`; S7 `STT_S7_IDLE_SECONDS=20`. Configuração opcional limitada a 5–60 segundos; conferir p50/p95 da espera `queued→claimed`.

## 4. Publicação da borda (somente Worker `faysk-stt`, sem restart backend)

Após testes, dry-run, fila drenada, plano de rollback e aprovação de publicação, executar separadamente o deploy do **Worker e seus static assets** pelo guard de `scripts/deploy-stt.mjs`:

```powershell
$env:STT_APPROVED_CUTOVER = "stt.faysk.dev"
$env:STT_APPROVED_WORKER = "faysk-stt"
try {
    npm run deploy
} finally {
    Remove-Item Env:\STT_APPROVED_CUTOVER -ErrorAction SilentlyContinue
    Remove-Item Env:\STT_APPROVED_WORKER -ErrorAction SilentlyContinue
}
```

Esse comando usa Wrangler configurado localmente; **não** publica S7 worker, reinicia Tunnel ou altera DNS. Se a quota diária de Workers ainda estiver esgotada, um deploy bem-sucedido **não** significa recuperação pública: verificar a quota no dashboard e aguardar o próximo reset UTC ou mitigação autorizada. Não mudar `fail closed` para `fail open`.

## 5. Critérios de aceitação pós-publicação (exigem execução real)

- [ ] `https://stt.faysk.dev/` HTTP 200, `/api/health` HTTP 200, origem `stt-origin.faysk.dev/api/health` sem chave HTTP 403 (com quota disponível; não estressar Worker com loop).
- [ ] S7 worker consome **zero** invocações públicas para `control`/`claim` locais; verificar contadores reais (não apenas declarar por mudança de URL).
- [ ] Notebook e S7 online sem processos duplicados, `power` e preload coerentes; sessão admin preservada.
- [ ] E2E: envio curto autorizado, fila → running → done, texto/JSON/SRT/TXT e timestamps; sem token exposto. Testar também um job elegível S7.
- [ ] Verificar idle e ativo por 15–30 min, latência p50/p95 e erro/backoff; acompanhar um dia UTC completo e 50/75/90% da quota real da conta.
- [ ] Usar [IaC runbook](https://github.com/Faysk/iac/blob/main/docs/operations/STT_CLOUDFLARE_1027_RUNBOOK.md) para métricas. A coleta sem token read-only **não é evidência** de volume da Cloudflare.

## 6. Recuperação/rollback

Se só o **agente S7** falhar após instalação, voltar apenas o arquivo/entrypoint anterior do worker em janela segura e reiniciar esse serviço; preservar API/SQLite/microSD/Tunnel. Se **static assets/Worker Cloudflare** falharem, restaurar o deploy da revisão anterior validada com os mesmos guards de domínio/conta; não desativar `x-edge-key`, nem expor `stt-origin`. Não tentar trocar de volta para o SQLite Windows antigo: isso criaria split brain e poderia perder jobs.

**Estado desta documentação:** somente plano versionado e CI; não registrar como efetivamente publicado sem evidências do operador.


## Verificação remota read-only automatizada (STT #97/#98/#99)

`scripts/preflight_s7_1027.py` consulta o S7 via **SSH com host key exigida**. Não usa Cloudflare, não envia segredos nem executa `sv restart`. O script remoto verifica somente: TCP loopback:8786, modo dos ficheiros privados, presença (não o valor) de `STT_EDGE_KEY` e `STT_REQUIRE_EDGE`, estado do supervisor do worker, SQLite **somente leitura** para **contagens agregadas** de jobs, quantidade de outbox, hashes SHA-256 de worker/entrypoint e se o banco fica no microSD. Nunca imprime transcrições, chaves ou IDs de jobs.

```powershell
cd E:\Projects\stt
# Confirmar o endereço/IP, utilizador e host key do verdadeiro S7 Edge, não do S23+
$S7 = "192.168.1.2"  # APENAS exemplo histórico; confirmar antes
$Identity = Join-Path $HOME ".ssh/s7_lmonitor_ed25519"

# ANTES: aceitar que o código instalado possa diferir da main, sem tocar no serviço
& .\.venv\Scripts\python.exe scripts/preflight_s7_1027.py --host $S7 --user u0_a279 --identity $Identity --phase before

# DEPOIS de um rollout supervisionado: exigir hashes do worker e runit iguais à main
& .\.venv\Scripts\python.exe scripts/preflight_s7_1027.py --host $S7 --user u0_a279 --identity $Identity --phase after
```

- `checks_passed` **não autoriza** restart ou deploy; é um snapshot sem garantia de que a fila não mude após o comando.
- `version_mismatches` no `before` é **esperado** enquanto o S7 ainda usa código antigo. Na fase `after`, hashes diferentes ou destino ainda público bloqueiam o aceite.
- `jobs_queued`, `jobs_active` e `outbox_count` diferentes de zero **bloqueiam** uma janela de atualização; dados não disponíveis **também bloqueiam** (nunca interpretar erro de DB como fila vazia).
- Pode haver tokens em configurações locais de SSH, mas **nada disso deve ser enviado ao chat ou versionado**. O retorno JSON foi projetado para ser apenas estado agregado; confirmar revisão de código antes de executar.
- Rejeição do SSH/host key → saída de erro sanitizada e código 2; **não** desativar `StrictHostKeyChecking` para contornar.
- A verificação não prova que a origem externa esteja 403, nem que jobs tenham sido concluídos E2E ou que Cloudflare tenha quota disponível. Esses aceites seguem separadamente no runbook.

**Status:** automatização preparada e testável offline; execução SSH física e release reais pendentes.


## Redução adicional de chamadas do executor Windows — STT #99

O notebook remoto mantém a consulta de estado a cada ~20 s, mas agora a requisição já inclui o último estado de energia que foi **efetivamente aplicado** pelo executor. O backend compara `applied_enabled` com a preferência atual: em caso de nova ordem de desligamento, mostra `stopping` e aguarda o ACK após o processo parar. O notebook envia o POST separado de ACK/ready apenas na primeira sincronização, mudança de estado/erro de workspace/outbox ou renovação periódica a cada ~120 s.

O agente não marca `ready=true` enquanto o outbox tem uma entrega pendente; publica `ready=false / OUTBOX_PENDING` como no S7. Falhas de ACK não avançam o estado local de confirmação. O refresh da linha `workers.seen` continua em cada poll de controle, mesmo quando o ACK não é reenviado.

**Redução estimada, não medida:** notebook ocioso com `claim` e `preload-claim` ativos cai de ~4 para ~3 chamadas de API a cada 20 s, com ~1 renovação adicional de estado por 120 s. Comparar invocações reais Cloudflare e aceitação de power on/off, workspace inacessível, preload, lease/outbox antes da homologação.

Nenhum deploy ocorre por merge. O S7 local continua sem invocações públicas por chamadas internas quando a versão instalada e o runit corresponderem à `main`.


### Resultados parciais e pastas de trabalho (validação estrita)

O preflight considera **não drenado** qualquer ficheiro `result-*` (inclusive `result-<job>.json.<sufixo>.tmp` deixado por uma falha na gravação atómica). Se nenhum diretório de trabalho conhecido existir, a contagem será `null` em vez de **zero**; um symlink ou entrada inesperada também invalida a contagem. Após a instalação da nova versão, `primary_workspace_present` deve confirmar `~/.cache/stt-worker`; uma pasta legada isolada não basta para declarar o rollout concluído. A ferramenta nunca apaga nem repara os artefactos — preservar e investigar antes do restart isolado do worker.

Este gate evita que arquivos de resultado incompletos sejam confundidos com uma outbox limpa; não substitui medição de filas ativas ou testes reais.


### Verificação real da proteção da API local (sem Cloudflare)

O preflight read-only agora realiza **duas requisições GET exclusivamente ao loopback do S7** (`http://127.0.0.1:8786/api/health`): sem `x-edge-key` deve responder **403**, e com a credencial existente no `.env` privado do próprio S7 deve responder **200**. Não segue redirecionamentos nem utiliza proxies, não imprime a credencial, não transmite nada ao GitHub e não usa o domínio público. O estado é resumido em `origin_no_key_403` e `origin_private_key_200`, ambos obrigatórios para o aceite do preflight `before` e `after`. A confirmação 200 apenas mostra que a proteção e o health local estão funcionais — **não equivale** a uma transcrição E2E nem a quota disponível na Cloudflare. Se houver discrepância, a operação é bloqueada (não desligar `STT_REQUIRE_EDGE` para fazê-la passar).
