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
