# Galaxy S7 Edge — worker nativo para stt.faysk.dev

**Estado em 01/11/2026:** S7 Edge online como worker secundário supervisionado, com transcrição real concluída pela API pública. O notebook permanece como executor principal. A seleção no administrador permite torná-lo principal; não é necessária para o modo secundário já habilitado nesta instalação.

## Validação ponta a ponta — 01/11/2026

Upload público do áudio de referência JFK, WAV de 11 segundos, concluído no `s7-edge` com Tiny, CPU e duas threads. Tempo total do worker: **10,00 s**, incluindo preparação e inferência; CPU: 17,90 s; pico RSS: 174,46 MiB. TXT, Markdown, SRT e JSON foram baixados pela sessão proprietária e conferidos. Outbox e workspace vazios após confirmação. O aparelho continuou reportando por HTTPS após remover o encaminhamento SSH via USB.

O teste encontrou um erro preexistente de leitura WAV (`getchannels` em vez de `getnchannels`), corrigido na [PR #89](https://github.com/Faysk/stt/pull/89), com regressão usando WAV real. O mesmo job terminou na terceira tentativa, após recuperação dos leases anteriores. A supervisão foi integrada na [PR #88](https://github.com/Faysk/stt/pull/88).

O painel público confirmou S7 online e normalizou sua seleção para Tiny/CPU/float32; a configuração salva continuou notebook/small/GPU/float16. Não houve troca do executor principal nem reinício da API nesta validação. Ainda faltam reboot físico, teste prolongado com tela apagada/Doze, temperatura e qualidade em português. MP3 é elegível pelo contrato, mas este ensaio físico usou WAV.

## Execução supervisionada (implementação de 01/11/2026)

`scripts/setup_s7_service.py` provisiona uma árvore runit **exclusiva do STT**, sem iniciar/parar os serviços do Observatory. Usa SSH com chave de host já conhecida; um encaminhamento USB pode usar `--host-key-alias` para conferir a identidade original, sem desativar StrictHostKeyChecking.

O padrão é prévia somente leitura. Após revisar, usar `--apply --start`, fornecendo `--host`, `--port`, `--user` e `--identity` de uma conexão já autorizada. Nenhuma chave é criada, transferida ou impressa por esse instalador; a chave restrita do worker deve existir previamente.

- Código canônico, configuração de log e logs rotacionados ficam no microSD em `~/storage/external-1/stt-worker`; quatro arquivos de log de até 64 KiB, além do corrente.
- A cópia de execução e os pequenos entrypoints permanecem no ambiente privado do Termux: código modificável no armazenamento compartilhado não é executado com acesso à chave. Temporários e outbox usam cache privado `~/.cache/stt-worker` (0700), reconstruível a partir dos originais que continuam na API.
- Antes de aceitar trabalho, reserva 16 MiB para a operação; mantém piso de 2 GiB livres no interno e orçamento de 64 MiB para o workspace. Download e subprocessos revalidam armazenamento; um subprocesso é encerrado se o orçamento for ultrapassado. Resultado de outbox limitado a 1 MiB.
- Runit reinicia somente o processo STT, com pausa de 15s após saída. `sv status ~/.local/share/stt-services/worker` mostra o estado. O instalador não reinicia uma instância já ativa para atualizar código: drenar o trabalho antes de uma atualização supervisionada.
- O entrypoint exclusivo `~/.termux/boot/20-stt-worker` espera até cinco minutos pelo microSD; nunca usa o interno como substituto de logs/dados persistentes. Requer Termux:Boot já instalado e habilitado. Configurar o script não comprova recuperação após reboot físico.
- O instalador recusa migração se houver resultados pendentes no antigo workspace. Não apagar outbox para desbloquear a instalação.

Os scripts `s7/service-run.sh`, `start-service.sh` e `boot-service.sh` são as fontes dos entrypoints. Nenhuma destas mudanças muda o executor principal configurado na API ou instala outros modelos. A ativação real continua dependendo das flags da API e da validação E2E.

## Compatibilidade comprovada

Em 30/10/2026, o Samsung S7 Edge real (Android 8, arm64-v8a) compilou `whisper.cpp` no Termux via Clang/CMake. O commit observado foi `d1be6fd` (rótulo CMake 1.9.5-dev; aviso do tag anotado durante clone). `whisper-cli` com Tiny multilíngue, áudio JFK de 11 s e 2 threads completou a inferência em **6,39177 s**: RTF aproximado **0,581** em apenas um ensaio. Runtime CPU (NEON/ARM FMA/OpenMP); qualidade em PT-BR e desempenho sustentado não medidos.

## Arquitetura

- O telefone abre conexões **de saída por HTTPS** a `https://stt.faysk.dev/api/worker/*`. Não precisa abrir SSH, conectar à LAN nem usar proxy reverso para receber jobs.
- O código `s7/worker.py` usa somente biblioteca padrão do Python Android 3.14.8; não instala `httpx`, `faster-whisper` ou `ctranslate2`.
- A API aceita `STT_S7_WORKER_KEY` separada e restringe o token ao worker `s7-edge` e aos seus leases. O token mestre `STT_WORKER_KEY` continua somente no Windows.
- O dispositivo anuncia `engine=whisper.cpp`, modelo `tiny` GGML, CPU e perfil compatível `float32` no contrato do STT. **Não significa que os pesos GGML Tiny sejam float32**: o peso do modelo compilado pode ser FP16. Não habilitar modos INT8/floating mistos sem ensaio real.
- Com `STT_S7_SIDE_WORKER=1` e `STT_S7_ENABLED=1` no host Windows, o S7 trabalha **em paralelo** com o notebook, recebendo somente tarefas pequenas enquanto o worker principal continua com os demais formatos/modelos. Desligado por padrão; não habilitar antes de validar ponta a ponta.
- A API só atribui ao S7 **uploads de áudio com extensão `.wav` ou `.mp3` com até 8 MiB**. No telefone, a conversão FFmpeg transforma o conteúdo em WAV PCM16 mono 16kHz e limita a **120 segundos** de áudio validado. **Outros formatos, vídeos, YouTube e jobs maiores continuam aguardando outro executor** quando o S7 for o worker principal. Com o modo secundário opcional habilitado, o notebook continua executando os demais jobs.
- Execução subprocesso isolada, 2 threads, prazo máximo local 10 minutos, heartbeat a cada 15 s; spool de resultado privado e atômico com confirmação por recibo da API. Em falha de conversão/inferência, devolve erro sem expor a mídia nos logs. O código jamais acessa os bancos do Observatory.

## 1. Preparação no Windows

Atualize o checkout em `main` e publique a **API** com o fluxo supervisionado [OPERATOR_PUBLISH.md](OPERATOR_PUBLISH.md), com fila drenada e autorização explícita antes de reiniciar. Essa etapa é necessária para o backend conhecer a chave nova e as regras de atribuição. Não publicar a frontend antes dos testes de CI.

O script `scripts/setup-s7-worker.ps1` gera uma chave **distinta**, gravada somente no `.env` privado do Windows e em `~/.stt-s7-worker-key` no Android, `chmod 600`. Ele copia também `s7/worker.py` para `~/stt-lab/stt-worker.py`. Execute com `-Apply` somente após revisar o ambiente:

```powershell
cd E:\Projects\stt
pwsh -File .\scripts\setup-s7-worker.ps1 -S7Host <IP_LOCAL> -S7User <USUARIO_TERMUX>
pwsh -File .\scripts\setup-s7-worker.ps1 -S7Host <IP_LOCAL> -S7User <USUARIO_TERMUX> -Apply
```

**Não compartilhe** conteúdos de `.env`, arquivo da chave do S7 ou tokens. Execute SSH apenas de uma máquina autorizada e confira a chave do host já conhecida. O script não ativa jobs nem reinicia a API.

## 2. Preparação no Termux

```sh
# O whisper.cpp Tiny já foi compilado no teste físico.
pkg install ffmpeg

# Sem root e sem interferir no Observatory. Verifique os arquivos:
test -x ~/stt-lab/whisper.cpp/build/bin/whisper-cli
test -s ~/stt-lab/whisper.cpp/models/ggml-tiny.bin
test -f ~/stt-lab/stt-worker.py
test "$(stat -c %a ~/.stt-s7-worker-key)" = 600

# Inicia conectado ao site em modo de registro; nenhum job se STT_S7_ENABLED=0:
python ~/stt-lab/stt-worker.py
```

Aparecerá `s7-edge` no painel após o primeiro `POST /api/worker/claim`. Evite os comandos `pkg upgrade` ou `pip install -r pyproject.toml` neste aparelho. Os serviços do Observatory permanecem fora de escopo.

## 3. Ativar uma transcrição pequena

1. Depois de confirmar que o S7 está online e anuncia Tiny, defina `STT_S7_ENABLED=1` no `.env` do host Windows e reinicie **somente a API** pelo procedimento supervisionado, após drenar a fila.
2. No console admin, selecione **s7-edge → Whisper → Tiny → CPU → float32** e salve. O perfil `float32` é uma compatibilidade da API e a UI apresenta ressalva sobre os pesos GGML.
3. Envie um WAV ou MP3 curto de teste autorizado pelo site; acompanhe heartbeat e resultado TXT/SRT/JSON. Se houver jobs de YouTube na fila, troque manualmente para o executor notebook quando desejar processá-los.
4. Após aprovar o teste isolado, se quiser operar **os dois simultaneamente**, retorne o worker principal ao notebook no admin e defina `STT_S7_SIDE_WORKER=1` no `.env` do Windows; faça reinício supervisionado da API. O S7 assume apenas WAV/MP3 pequenos elegíveis, enquanto o notebook fica com os demais. Isso é um roteamento simples, não um balanceador geral para todos os modelos.

## Verificações e limites

- O provisionamento básico não cria autostart. O instalador `setup_s7_service.py` descrito acima instala supervisão e entrada Termux:Boot; recuperação após reboot físico e estabilidade prolongada ainda precisam de ensaio.
- O modelo GGML é distinto do cache CTranslate2 do Windows; pedidos de preload do painel para o S7 são desabilitados.
- O resultado e o status do trabalho ficam na API central. O S7 mantém temporariamente cópia da mídia durante inferência em pasta privada, que é apagada ao finalizar; um resultado pendente da API fica em outbox privado até confirmação ou expiração.
- Queda de energia, Doze do Android e troca de rede podem encerrar o processo. Não divulgar tokens e não deixar o Android 8 com portas abertas na internet.
- A primeira transcrição pública foi conferida; isso habilita uso experimental como secundário, sem comprovar estabilidade sustentada ou qualidade para todos os idiomas.
