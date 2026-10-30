# Publicar o STT no Windows + Cloudflare (procedimento do operador)

O git merge apenas atualiza o repositório. O backend e o executor ficam no Windows; o frontend e proxy de API ficam no Cloudflare Worker faysk-stt. GitHub Actions executa testes/dry-run e NÃO tem deploy automático. Esta publicação manual é uma ação explícita do operador.

## Antes de publicar

- Use PowerShell 7 no computador onde o STT está instalado (E:\Projects\stt).
- No painel, aguarde fila vazia e nenhum job em execução. O parâmetro QueueDrained é uma confirmação manual; o script não consegue provar sozinho que os trabalhos terminaram.
- Garanta que Wrangler está autenticado na conta correta e que a configuração privada .env do STT já existe. Não envie credenciais ao GitHub ou chat.
- Branch main, alterações rastreadas limpas. Arquivos locais em db/, blob/, models/ e worker-outbox/ não são apagados.
- A execução envolve stop/start de processos locais e é potencialmente interruptiva. Não execute durante trabalhos ativos.
- Se algum release futuro precisar atualizar dependências Python ou migrar schema, faça isso em plano separado. Este script não roda uv sync nem migra dados automaticamente.

## Comandos

~~~powershell
cd E:\Projects\stt
git status --short
git pull --ff-only origin main

# Prévia somente leitura
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish-stt.ps1

# Depois de verificar que a fila está vazia
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish-stt.ps1 -Apply -QueueDrained
~~~

## O que faz o Apply

1. Confere repositório Faysk/stt, branch main, checkout limpo, .venv e .env.
2. Atualiza main com pull fast-forward, verifica **somente leitura** que as dependências já instaladas correspondem ao `package-lock.json` (incluindo versões/integridade dos pacotes; permite opcionais incompatíveis com a plataforma), executa testes Python/Node e dry-run Wrangler. **Não executa `npm ci` durante a publicação.**
3. Confirma os endpoints HTTP atuais 200 (site), 200 (API) e 403 (origem sem segredo).
4. Encerra somente **API e worker** STT identificados pelos arquivos PID, **preservando o Tunnel e seus processos filhos**. Espera até 45 segundos por **3 segundos contínuos com a porta 8765 livre** e só então tenta reiniciar API/worker; se voltar a ficar ocupada, informa o(s) PID(s) e interrompe sem matar processos desconhecidos. O Tunnel só é iniciado separadamente quando estiver parado, fora do publicador.
5. Repete o health gate antes de fazer npm run deploy com aprovação explícita apenas para Worker faysk-stt e domínio stt.faysk.dev.
6. Confirma novamente HTTP 200/200/403 e que o asset /admin-enhancements.js retorna 200, mostrando o SHA do checkout implantado.

## Diagnóstico de porta 8765 que reaparece

Se `start.ps1` mostrar `STT_API_PORT_OCCUPIED` após `stop.ps1 -PreserveTunnel`, **não repita `-Apply`**. O publicador pode ter visto a porta livre momentaneamente antes de um relançamento externo. Use `Get-NetTCPConnection -LocalPort 8765 -State Listen` para obter `OwningProcess` e `Get-CimInstance Win32_Process -Filter 'ProcessId=<PID>'` para identificar o pai, o executável e o horário de início. Compare com `logs/pids.json` e verifique separadamente `server.worker`. Não imprima linhas de comando nem segredos de processos em relatórios públicos. Se a API estiver ativa mas o worker não, investigue a origem do supervisor antes de iniciar qualquer coisa; não crie um segundo Uvicorn e não encerre outros serviços por PID genérico. Rastrear em [STT #78](https://github.com/Faysk/stt/issues/78).

## Limites e incidentes

- Não reinicia Windows, não cria autostart, não instala modelos, não altera DNS ou Tunnel, não administra Terraform, não atualiza segredos, não altera banco SQLite. **Release nunca para nem reinicia o conector existente do Tunnel**; é responsabilidade do operador mantê-lo saudável.
- Se um teste local falhar antes do stop, o script não muda serviço/Worker. Se falhar depois do stop (incluindo `STT_PORT_NOT_RELEASED`), investigue os logs locais; o script NÃO faz rollback automático. Se a API ficar indisponível, confirme se há processos STT restantes e se a porta 8765 está livre antes de executar `pwsh -File .\\scripts\\start.ps1`. Nunca execute novo `-Apply` apenas para restaurar o serviço.
- Se `STT_NODE_DEPS_NOT_READY` aparecer, a publicação é interrompida **antes** de parar a API. Numa janela de manutenção, identifique/feche o processo Wrangler de desenvolvimento que usa `node_modules/@esbuild/*/esbuild.exe`, execute `npm ci` **uma única vez fora do publicador**, valide com `node scripts/verify-node-deps.mjs` e recomece. Não encerre cegamente processos Node, Python ou Tunnel; não execute o deploy manual para contornar esse gate.
- Se o gate inicial retornar HTTP 530 para API e origem, não repita `-Apply`: diagnostique primeiro `server.tunnel`/`cloudflared`, status de `logs/tunnel.pid`, logs sanitizados, e código 1XXX da Cloudflare (issue #75). O frontend 200 por si só não prova conectividade com a origem.
- HTTP 200/200/403 não comprova transcrição real, GPU nem persistência após reboot. Essas verificações continuam pendentes.
- Nunca compartilhe logs com tokens, conteúdo de transcrições, .env, banco, outbox ou arquivos de mídia.

## Teste funcional pós-publicação

1. Confira a interface em https://stt.faysk.dev/.
2. Abra https://stt.faysk.dev/admin.html com autorização e teste seleção/preload/modelos, cards CPU/GPU, filtros, CSV e feedback.
3. Envie um áudio curto autorizado, espere done, leia e baixe os arquivos TXT/SRT/JSON.
4. Execute scripts/collect-stt-preflight.ps1 para gerar um diagnóstico local sanitizado.
5. Registre status, SHA, ambiente e bugs nas issues STT #37/#57 e IaC #52, sem incluir dados sensíveis.

Este procedimento é específico da aplicação STT e não desbloqueia o gate de mapeamento integral do IaC. O objetivo é permitir ao operador testar a versão em main, com rastreabilidade e mínimo risco.

## Incidente real de publicação e recuperação — 30/10/2026

**Sequência comprovada pelo operador (revisão `7ad6d155`, anterior à correção de preservação do Tunnel):** `publish-stt.ps1 -Apply -QueueDrained` aprovou 86 testes Python (3 skipped), Node/Edge e dry-run Wrangler, bem como HTTP público 200/200 e origem protegida 403 antes do restart. Após parar serviços, a porta 8765 foi observada livre, porém o `start.ps1` recusou nova inicialização porque havia um listener na porta naquele instante. O publicador lançou exceção **antes** de executar deploy Worker. Não foi identificada a identidade do listener na disputa; classificar como janela/race **possível, não demonstrada**.

**Impacto observado em consulta posterior:** sem listener 8765, sem processo API/worker detectado, API pública HTTP **530**. **Recuperação:** com a porta livre, o operador executou **uma vez** `pwsh -NoProfile -File .\scripts\start.ps1`. Dez segundos depois, o frontend e `/api/health` públicos voltaram a HTTP **200**. A mesma rota consultada em `http://127.0.0.1:8765/api/health` sem `x-edge-key` retornou HTTP **403 esperado** com `STT_REQUIRE_EDGE=1`: **nunca mostrar, copiar ou desligar a chave para passar um smoke local**. Para verificação operacional segura, usar o endpoint público e a origem sem segredo, com expectativas 200/200/403.

O código atual do publicador preserva o Tunnel durante stop/restart da aplicação (`-PreserveTunnel`/`-SkipTunnel`) após [#76](https://github.com/Faysk/stt/issues/76). Essa alteração **ainda requer teste de operador no Windows**, não deve ser chamada de release homologado apenas pelo merge/CI. O script continua sem rollback automático se o restart falhar; não repetir `-Apply` até o serviço ter sido diagnosticado e a fila esvaziada. Não encerrar processos desconhecidos por PID nem tocar banco/outbox.

**Acompanhamento:** [#35](https://github.com/Faysk/stt/issues/35) supervisão e restart; [#75](https://github.com/Faysk/stt/issues/75) conectividade Tunnel/530; [#69](https://github.com/Faysk/stt/issues/69) rejeição 422/PR #71; [#72](https://github.com/Faysk/stt/issues/72) Windows BSOD; [#37](https://github.com/Faysk/stt/issues/37) E2E físico. Evidências cruzadas no [IaC #52](https://github.com/Faysk/iac/issues/52) e no [relatório operacional do IaC](https://github.com/Faysk/iac/blob/main/docs/operations/STT_RELEASE_ACCEPTANCE_2026-10-30.md). Nenhum dump, mídia, token, arquivo `.env` ou log privado deve ser publicado.
