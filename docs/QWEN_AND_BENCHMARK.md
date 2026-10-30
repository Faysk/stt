# Motores, resultados e benchmarks (preview de 30/10/2026)

Este documento descreve a **branch experimental**, não o estado atualmente
publicado em `tts.faysk.dev`. Não executar instalação nem copiar pesos para o
ambiente `faster-whisper` existente. O código Qwen está atrás de uma seleção
**manual** de motor e do anúncio de capacidade do worker.

## Contrato de transcrição

- `schema_version: 1` no JSON exportado; resultados têm
  `result_state` (`unknown`, `complete`, `silence`, `partial`).
- Segmentos exigem floats finitos, tempo crescente de início, fim >= início
  e fim dentro de `duration + 0.5s`; sobreposições legítimas são preservadas.
- `timing=segments`: TXT, MD, JSON e SRT.
- `timing=none`: TXT, MD e JSON com `text` de verdade. **Não há SRT**:
  um motor sem alinhamento não inventa timestamps; download SRT devolve 404.
- Uma saída de ASR vazia com sinal de áudio não pode ser tratada como silêncio
  confirmado. O adaptador Qwen falha fechado neste cenário. Para áudio próximo
  ao silêncio digital, uma janela pode ser explicitamente omitida.

## Receita imutável do Qwen3 (experimental)

O catálogo em `server/qwen_engine.py` contém os IDs e revisões de pesos
aproveitados como referência **somente leitura** do
`Faysk/tda@4b47603f87f4300b661ab2022cd7337d66c3700a`.
Ele usa o Transformers nativo, **nunca** os pesos faster-whisper/CTranslate2.
A receita de ambiente está em `runtime/qwen3-windows-x64.json`.

| Perfil | Repo dos pesos | Revisão obrigatória |
|---|---|---|
| `qwen-fast` | `Qwen/Qwen3-ASR-0.6B-hf` | `7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c` |
| `qwen-quality` | `Qwen/Qwen3-ASR-1.7B-hf` | `bcd2b5b7f32b480ab5790554cfa8347f246a14f3` |

**Não há download automático de pesos.** O operador deve obter os ficheiros
do commit fixado, obedecer às respetivas licenças e armazená-los em
`TTS_QWEN_MODELS/<directory>` com `model-revision.json` contendo o mesmo
`repository` e `revision`. Esse manifesto é uma declaração de proveniência
local, **não valida criptograficamente** os bytes dos pesos; falta aceitar uma
verificação integral com hash/manifest no hardware de destino.

Para validar separação, numa máquina Windows isolada, crie um ambiente Python
3.12 independente de `.venv`:

```powershell
py -3.12 -m venv .venv-qwen
.\.venv-qwen\Scripts\python.exe -m pip install --upgrade pip
# Consulte runtime/qwen3-windows-x64.json para versões fixadas.
# Instale o Torch CUDA compatível usando o índice oficial do PyTorch,
# e as demais bibliotecas NO AMBIENTE QWEN, nunca no uv.lock do Whisper.
$env:TTS_QWEN_PYTHON = (Resolve-Path '.\.venv-qwen\Scripts\python.exe').Path
$env:TTS_QWEN_MODELS = 'E:\Models\qwen'
```

A API mantém as opções atuais do console em Whisper. A seleção Qwen é manual
pelo endpoint autenticado `PUT /api/admin/settings` com:
`{"worker":"notebook","engine":"qwen3","model":"qwen-fast",
"device":"cuda","compute":"bfloat16","language_hint":"auto",
"paused":false}`.
O motor requer executor preparado, Python Qwen diferente de Python Whisper,
pesos locais com revisão fixada, CUDA disponível, compute capability >= 7.5,
e VRAM disponível (preflight 3 GiB para 0.6B, 6 GiB para 1.7B).
**Estes limites são gates preliminares, não capacidade física comprovada.**
O servidor só atribui trabalhos se o executor anunciar aquele modelo pronto.
Os idiomas aceitam autodetecção (`auto`) ou hint (por exemplo `Portuguese`
ou `English`). Não há fallback silencioso para CPU, outro modelo ou motor.

A primeira adaptação usa janelas contíguas de 60 segundos, com decode PyAV
streaming em mono/16 kHz e sem sobreposição. Esta etapa **não resolve** os
requisitos completos de deduplicação de fronteiras, checkpoints e alinhador.
Os resultados só têm texto; publicar em produção exige antes ensaios reais
PT, EN e mistos com áudio cortado em fronteiras, além de validação de drivers,
VRAM, latência, idiomas, silêncio e modelos pesados. É uma feature parcial da #7.

## Proteção de GPU antes de decodificar/transcrever (draft PR #31)

O adaptador valida o ficheiro **antes de carregar pesos na GPU**:

1. Recebe o upload ou faz download de YouTube respeitando o limite real de **90 MiB** (incluindo callback de progresso).
2. Executa uma primeira passagem de **decode PyAV em streaming**, sem reter PCM na memória, para somar as durações dos frames, rejeitar áudio vazio/inválido e barrar **> 7200 segundos** mesmo que o contentor tenha metadata falsa.
3. Só depois importa Torch/Transformers, verifica CUDA/VRAM, carrega o modelo local e inicia inferência.
4. Durante o ASR, `iter_windows()` **mantém uma segunda barreira** ao número de amostras efetivamente resampladas.

A pré-decodificação pode adicionar latência e I/O: `media_probe_s` é medido separadamente, e o custo deve ser avaliado na GPU/Windows reais. Um erro de download ou decode não deve reservar VRAM para pesos do modelo. Testes sintéticos exercitam os limites e verificam que áudio inválido não chega ao Torch. **Esses testes não provam qualidade de áudio, FPS, VRAM, compatibilidade com ficheiros reais nem integridade criptográfica dos pesos**; tudo isso continua na issue [#28](https://github.com/Faysk/tts/issues/28).

## Métricas v1

`server/metrics.py` define `schema_version=1` e valida tempos finitos
positivos, soma de estágios não superior ao tempo do motor (com tolerância
para clocks) e origem explícita do cache. Estágios:

- `download_s`, `media_probe_s`, `model_load_s`, `asr_s`,
  `alignment_s`, `serialization_s` e `delivery_s`.
- `engine_elapsed_s`, `new_audio_seconds`, `reused_audio_seconds`
  e `cache_source` (`none`, `checkpoint`, `result-cache`, `unknown`).

O infer atual mede os estágios conhecidos; os demais ficam `null`, e nunca
zero falsamente medido. `started-created` é uma medida de fila no servidor;
`finished-created` mede o tempo total do job. Nenhuma métrica de GPU/CPU
do host isolado comprova energia exclusiva do job.

O código atual **não reutiliza** checkpoints ou inferência para calibração;
`reused_audio_seconds=0` e `cache_source=none` são declarações do caminho
novo. Não reclassificar processamento antigo como inferência fresca.

## Benchmark v2 com referência humana

Crie um dataset **local e não versionado** com referências revisadas por pessoa:

```json
{
  "samples": [
    {"id": "pt-limpo", "media": "audio/pt-limpo.wav",
     "reference": "reference/pt-limpo.txt", "language": "pt"},
    {"id": "en-misto", "media": "audio/en-misto.wav",
     "reference": "reference/en-misto.txt", "language": "mixed"}
  ]
}
```

Execute com cada receita, mantendo corpus, dispositivo e repetição documentados:

```powershell
uv run python scripts/benchmark_v2.py dataset.json --engine whisper --model small --device cpu --compute int8 --repeat 3 --out logs/whisper-v2.json
uv run python scripts/benchmark_v2.py dataset.json --engine qwen3 --model qwen-fast --device cuda --compute bfloat16 --repeat 3 --out logs/qwen-v2.json
```

O relatório guarda apenas ID da amostra, SHA-256 da mídia, runtime, revisão
reportada pelo executor, métricas, WER/CER, tempos e resultado de cada tentativa.
Não guarda texto privado, ficheiros locais ou as referências humanas. Cada trial
começa num novo subprocesso/ambiente temporário; não é benchmark de cache quente.
**Não afirmar superioridade Qwen/Whisper** sem comparação no mesmo corpus.

## Critérios pendentes de produção

- Prova de recuperação após kill/reboot e falhas reais de transporte/disco.
- Teste Windows do encerramento da árvore de processos e exceções de I/O lento.
- Assinatura/hash confiável de pesos, dependências isoladas instaladas e ensaio
  físico com RTX 4070/3090.
- Alinhamento sem falsificar timestamps, sobreposições/fronteiras e retomada de
  janelas/checkpoints para áudio de duas horas.
- Testes controlados de fila e HTTP a partir da fronteira Cloudflare, carga,
  limite de multipart e avaliação de qualidade PT/EN/misto.
- Métrica end-to-end de entrega depois do ACK, serialização observada e
  dataset público/licenciado para repetibilidade independente.

Nenhum item acima é considerado concluído só pela aprovação da CI.
