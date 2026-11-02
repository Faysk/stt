# Campanhas integrais CPU/GPU

Ferramentas locais para comparar modelos Whisper no mesmo áudio completo. Não
publicam mídia, transcrições ou resultados no repositório nem inserem os testes
na fila pública. Artefatos ficam em `benchmarks/`, ignorado pelo Git e separado
da retenção de 24h do serviço público. São mantidos para a análise do operador.

## Matriz e condições

- Modelos: Tiny, Base, Small, Medium, Large-v3 e Turbo, multilíngues.
- CPU: INT8, INT8/FP32, INT16 e FP32, quando anunciados pelo runtime.
- GPU: FP16, INT8/FP16, INT8, INT8/BF16, BF16, INT8/FP32 e FP32,
  quando anunciados pelo runtime.
- Com todos esses tipos disponíveis: **66 combinações**, 42 GPU e 24 CPU.
  GPU é executada primeiro; não existe inferência simultânea entre combinações.
- Uma passagem do vídeo inteiro em cada combinação. Modelo em processo novo,
  sem reutilizar transcrição anterior. O cache de arquivos/SO pode estar aquecido;
  não se trata de ensaio de disco frio nem de modelo residente entre trabalhos.
- Oito threads CPU, uma instância, beam 5, VAD, idioma automático, seed 0 e
  temperaturas iguais às do serviço. Oito threads reproduzem a configuração
  atual; isso não é um ensaio de escalabilidade com todos os núcleos do host.
- São fixados revisão dos pesos, SHA-256 de `model.bin`, hash da mídia, versões
  do runtime, hardware e opções de decodificação.
- A precisão efetivamente usada pelo CTranslate2 é registrada separadamente
  da solicitada. `int8` pode ser um alias de uma precisão mista; não contar
  aliases como algoritmos independentes.
- Falta de VRAM, erro de runtime e timeout ficam registrados. Não há troca
  silenciosa de modelo, dispositivo ou precisão para fazer um teste passar.

Uma passagem não mede variância estatística. Diferenças pequenas de tempo ou
memória não justificam, isoladamente, escolher um vencedor. Para uma decisão
final podem ser necessários ensaios repetidos das configurações finalistas.

## Métricas e limites da medição

Cada execução guarda decode, carregamento, ASR e tempo total do motor; CPU em
segundos, utilização normalizada pelos processadores lógicos, RSS, memória
dedicada de GPU por processo, telemetria da placa, temperaturas e clocks.
RTF é tempo de processamento dividido pela duração do áudio: menor é melhor.
O estágio ASR inclui VAD, detecção de idioma e geração dos segmentos.

Amostragem: processo a cada 0,5s; GPU/contadores Windows aproximadamente a cada
1s. Picos são amostrados, não uma prova de máximo instantâneo. O contador GPU
por PID é independente da VRAM global; leituras indisponíveis permanecem nulas.
Os auxiliares de medição não integram CPU/RAM do processo de inferência, mas
sua carga pode aparecer na telemetria global do host.

Potência/energia são da **placa GPU inteira**, incluindo outros processos e o
consumo de base. Não são energia exclusiva da transcrição nem consumo total
do notebook. Energia da CPU não é medida. Baseline de dez segundos é guardado
antes de cada teste; há intervalo de quinze segundos entre testes. Esses
intervalos, o download dos pesos e o download da mídia não entram no tempo do
motor. Raw traces são preservados para auditar carga concorrente e temperatura.

## Texto e precisão

São mantidos o texto completo, segmentos, timestamps, probabilidades e idioma
detectado de cada resultado. O painel local permite ouvir o áudio e escolher
duas execuções para comparação.

Legendas automáticas do YouTube, quando disponíveis no idioma original, são
uma **referência auxiliar não revisada**. `weak_wer`/`weak_cer` medem divergência
com essa referência; não são precisão humana nem percentual de acerto. A
normalização usa NFKC, caixa baixa e palavras sem pontuação. CER usa a mesma
normalização com espaços simples. A etapa de avaliação final deve usar uma
referência conferida com o áudio, especialmente nomes próprios e fala sobreposta.

## Preparar e acompanhar

Com o ambiente de desenvolvimento instalado:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_prepare.py benchmarks/minha-campanha --url https://youtu.be/VIDEO_ID
.\.venv\Scripts\python.exe scripts/benchmark_campaign.py install-startup benchmarks/minha-campanha
.\.venv\Scripts\python.exe scripts/benchmark_campaign.py run benchmarks/minha-campanha
```

O preparador baixa a mídia uma vez, obtém legendas auxiliares e fixa os seis
snapshots locais. A campanha reserva o executor `notebook` com o controle de
potência existente. O S7 continua atendendo os trabalhos compatíveis em modo
limitado. Configurações de modelo/dispositivo da fila não são reescritas.
O estado de potência original é restaurado somente após terminar a campanha.

O visualizador escuta apenas em loopback, começando pela porta livre 8877.
A URL efetiva fica em `report-server.json`. O servidor não é publicado na
Cloudflare. O painel atualiza automaticamente e oferece CSV e comparação de
textos. `report-data.json`, `comparison.csv`, `SUMMARY.md` e `runs/` ficam no
diretório da campanha. Não remover esse diretório enquanto a campanha estiver
ativa. O usuário pode pedir ao agente para consultar o andamento.

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_campaign.py status benchmarks/minha-campanha
.\.venv\Scripts\python.exe scripts/benchmark_campaign.py pause benchmarks/minha-campanha
```

Depois de confirmar estado `paused`, `restore` libera novamente o executor
público. `resume` retoma os testes que faltam, reservando o executor se não
houver trabalho público em execução. Sem essa liberação, a pausa interrompe o
benchmark e deixa o executor público desligado para o operador usar a máquina.
Se o executor for ligado manualmente durante o benchmark, a campanha pausa em
vez de disputar a máquina com a fila pública.

## Interrupções e inicialização

- Lock exclusivo impede dois coordenadores para a mesma campanha.
- Um processo em andamento só é adotado se PID, instante de criação, script e
  arquivo de configuração correspondem exatamente ao ensaio.
- A limpeza encerra somente processos comprovadamente pertencentes ao teste.
- Falhas do coordenador têm retry; resultado concluído é publicado atomicamente.
  Sharing violations transitórias do Windows têm espera limitada, sem invalidar
  o teste por uma leitura simultânea do próprio arquivo de progresso.
- Ensaios interrompidos recomeçam do início. Os ensaios concluídos não são
  repetidos. Tentativas de calibração da instrumentação ficam preservadas e
  excluídas do comparativo por marcador, sem reclassificar erro de medição como
  defeito do modelo.
- O limite por tentativa é 24h; o limite do worker público de duas horas de
  execução não é aplicado a esta campanha local autorizada para durar dias.
- `HKCU Run/FayskSTTBenchmark` configura retorno **depois do login** do usuário;
  não é serviço antes de login. A recuperação física após reboot continua
  precisando de homologação. Se não iniciar, o comando `run` retoma com os
  checkpoints. A entrada de startup é removida ao concluir/restaurar a campanha.
- O processo solicita que o Windows permaneça acordado durante a campanha,
  permitindo apagar a tela. Não muda o plano global de energia nem impede
  desligamento ou suspensão explícita do usuário.

Referências: [CTranslate2 compute types e conversões](https://opennmt.net/CTranslate2/quantization.html),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper).
