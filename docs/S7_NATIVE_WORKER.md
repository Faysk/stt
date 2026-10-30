# Galaxy S7 Edge — worker nativo para stt.faysk.dev

**Estado:** código integrado à API e cliente Termux. Não confundir com o cartão SSH da área administrativa. O executor só recebe jobs depois de provisionar chave exclusiva, instalar ffmpeg, publicar a API alterada, iniciar o cliente e **selecioná-lo explicitamente** no administrador.

## Compatibilidade comprovada

Em 30/10/2026, o Samsung S7 Edge real (Android 8, arm64-v8a) compilou `whisper.cpp` no Termux via Clang/CMake. O commit observado foi `d1be6fd` (rótulo CMake 1.9.5-dev; aviso do tag anotado durante clone). `whisper-cli` com Tiny multilíngue, áudio JFK de 11 s e 2 threads completou a inferência em **6,39177 s**: RTF aproximado **0,581** em apenas um ensaio. Runtime CPU (NEON/ARM FMA/OpenMP); qualidade em PT-BR e desempenho sustentado não medidos.

## Arquitetura

- O telefone abre conexões **de saída por HTTPS** a `https://stt.faysk.dev/api/worker/*`. Não precisa abrir SSH, conectar à LAN nem usar proxy reverso para receber jobs.
- O código `s7/worker.py` usa somente biblioteca padrão do Python Android 3.14.8; não instala `httpx`, `faster-whisper` ou `ctranslate2`.
- A API aceita `STT_S7_WORKER_KEY` separada e restringe o token ao worker `s7-edge` e aos seus leases. O token mestre `STT_WORKER_KEY` continua somente no Windows.
- O dispositivo anuncia `engine=whisper.cpp`, modelo `tiny` GGML, CPU e perfil compatível `float32` no contrato do STT. **Não significa que os pesos GGML Tiny sejam float32**: o peso do modelo compilado pode ser FP16. Não habilitar modos INT8/floating mistos sem ensaio real.
- Com `STT_S7_SIDE_WORKER=1` e `STT_S7_ENABLED=1` no host Windows, o S7 trabalha **em paralelo** com o notebook, recebendo somente tarefas pequenas enquanto o worker principal continua com os demais formatos/modelos. Desligado por padrão; não habilitar antes de validar ponta a ponta.\n- A API só atribui ao S7 **uploads de áudio com extensão `.wav` ou `.mp3` com até 8 MiB**. No telefone, a conversão FFmpeg transforma o conteúdo em WAV PCM16 mono 16kHz e limita a **120 segundos** de áudio validado. **Outros formatos, vídeos, YouTube e jobs maiores continuam aguardando outro executor** quando o S7 estiver selecionado; o STT atual possui seleção manual de um worker, não escalonamento multiworker.
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

- O script **não** cria autostart persistente, service supervisor ou cron; se o Termux fechar, o S7 ficará offline. Supervisão/autostart só após homologar uma transcrição real de ponta a ponta e medir memória, impacto térmico e observabilidade.
- O modelo GGML é distinto do cache CTranslate2 do Windows; pedidos de preload do painel para o S7 são desabilitados.
- O resultado e o status do trabalho ficam na API central. O S7 mantém temporariamente cópia da mídia durante inferência em pasta privada, que é apagada ao finalizar; um resultado pendente da API fica em outbox privado até confirmação ou expiração.
- Queda de energia, Doze do Android e troca de rede podem encerrar o processo. Não divulgar tokens e não deixar o Android 8 com portas abertas na internet.
- Não declarar S7 **em produção** até conferir o primeiro job contra API publicada e controles de limite/permissão; os testes CI não são substitutos.
