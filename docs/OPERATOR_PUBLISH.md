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
4. Encerra somente os processos STT identificados pelos arquivos PID, **espera até 45 segundos a libertação da porta 8765** e só então reinicia API, executor e Tunnel configurado; se continuar ocupada, interrompe a publicação sem matar processos desconhecidos.
5. Repete o health gate antes de fazer npm run deploy com aprovação explícita apenas para Worker faysk-stt e domínio stt.faysk.dev.
6. Confirma novamente HTTP 200/200/403 e que o asset /admin-enhancements.js retorna 200, mostrando o SHA do checkout implantado.

## Limites e incidentes

- Não reinicia Windows, não cria autostart, não instala modelos, não altera DNS ou Tunnel, não administra Terraform, não atualiza segredos, não altera banco SQLite.
- Se um teste local falhar antes do stop, o script não muda serviço/Worker. Se falhar depois do stop (incluindo `STT_PORT_NOT_RELEASED`), investigue os logs locais; o script NÃO faz rollback automático. Se a API ficar indisponível, confirme se há processos STT restantes e se a porta 8765 está livre antes de executar `pwsh -File .\\scripts\\start.ps1`. Nunca execute novo `-Apply` apenas para restaurar o serviço.
- Se `STT_NODE_DEPS_NOT_READY` aparecer, a publicação é interrompida **antes** de parar a API. Numa janela de manutenção, identifique/feche o processo Wrangler de desenvolvimento que usa `node_modules/@esbuild/*/esbuild.exe`, execute `npm ci` **uma única vez fora do publicador**, valide com `node scripts/verify-node-deps.mjs` e recomece. Não encerre cegamente processos Node, Python ou Tunnel; não execute o deploy manual para contornar esse gate.
- HTTP 200/200/403 não comprova transcrição real, GPU nem persistência após reboot. Essas verificações continuam pendentes.
- Nunca compartilhe logs com tokens, conteúdo de transcrições, .env, banco, outbox ou arquivos de mídia.

## Teste funcional pós-publicação

1. Confira a interface em https://stt.faysk.dev/.
2. Abra https://stt.faysk.dev/admin.html com autorização e teste seleção/preload/modelos, cards CPU/GPU, filtros, CSV e feedback.
3. Envie um áudio curto autorizado, espere done, leia e baixe os arquivos TXT/SRT/JSON.
4. Execute scripts/collect-stt-preflight.ps1 para gerar um diagnóstico local sanitizado.
5. Registre status, SHA, ambiente e bugs nas issues STT #37/#57 e IaC #52, sem incluir dados sensíveis.

Este procedimento é específico da aplicação STT e não desbloqueia o gate de mapeamento integral do IaC. O objetivo é permitir ao operador testar a versão em main, com rastreabilidade e mínimo risco.
