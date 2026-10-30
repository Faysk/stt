# Primeiro ensaio local

30/10/2026. Windows, Intel i7-14700HX, NVIDIA RTX 4070 Laptop (8 GB). Motor faster-whisper 1.2.1 / CTranslate2 4.8.4 / PyAV 16.1.0. Modelo `small`, beam size 5, VAD ativado, 8 threads de CPU. Dependências completas em `uv.lock`.

Amostra sintética de voz, 12,59225 segundos. Três repetições por configuração, um processo novo por execução, pesos já presentes no disco. O tempo inclui carregamento do modelo e decodificação. O benchmark executa fora da fila pública, sem alterar a configuração dos visitantes.

| Configuração | Mediana total | Mediana RTF | Maior pico RAM amostrado |
| --- | ---: | ---: | ---: |
| CPU / int8 | 3,791 s | 0,301 | 579 MB |
| CUDA / float16 | 1,843 s | 0,146 | 536 MB |

Nesta amostra, a GPU levou aproximadamente metade do tempo da CPU. Isto não mede capacidade máxima, concorrência de inferência, precisão nem comportamento em áudios longos. A VRAM por processo não ficou disponível pelo `nvidia-smi` neste Windows; não foi contabilizada como zero. A detecção automática de idioma variou entre as configurações; não há conclusão de qualidade a partir deste ensaio sintético.

O teste completo também percorreu domínio público → Worker → Tunnel → upload → fila → executor → transcrição. Downloads e isolamento entre sessões são cobertos nos testes da API. Um ensaio separado com 20 claims simultâneos confirmou que apenas um recebe o mesmo trabalho.

Para repetir com sua própria mídia e, opcionalmente, uma referência humana:

```powershell
.venv\Scripts\python.exe scripts\benchmark.py caminho\audio.wav --model small --repeat 3 --reference caminho\referencia.txt --out logs\benchmark.json
```

O arquivo de relatório inclui recursos e WER quando houver referência, sem salvar o texto da transcrição. Para comparar modelos, repita com a mesma mídia, referência e parâmetros. Resultados detalhados locais ficam em `logs/benchmark.json`, ignorado pelo Git.
