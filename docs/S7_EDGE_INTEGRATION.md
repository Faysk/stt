# Galaxy S7 Edge como destino SSH e candidato a worker STT

**Estado:** acesso SSH suportado pelo console de administração (`#65`); **transcrição no Android ainda não homologada**. A seleção do S7 como executor continua bloqueada até o próprio worker reportar online, tipo de computação CPU compatível e pesos do modelo preparados. Seguimento: [issue #66](https://github.com/Faysk/stt/issues/66).

## Fontes e separação de responsabilidades

| Fonte | Fornece | Não fornece |
|---|---|---|
| [s7-edge-observatory](https://github.com/Faysk/s7-edge-observatory) | Frontend estático, documentação do contrato de leitura e UI | Dump real do telefone ou runtime Whisper |
| [network-observatory](https://github.com/Faysk/network-observatory) | Backend Python de telemetria e histórico SQLite, incluindo `app/host_telemetry.py` e `app/host_sources.py` | Inferência ASR comprovada |
| [Network Observatory: homologação 26/10/2026](https://github.com/Faysk/network-observatory/blob/main/docs/RELEASE_INVENTORY.md) | Evidência física de publicação LAN, dados históricos de espaço e testes locais | Disponibilidade atual, benchmark Whisper e consumo de RAM/energia por áudio |
| [S7 Observatory: API](https://github.com/Faysk/s7-edge-observatory/blob/main/docs/API.md) | Contrato `/api/host/snapshot`, `/api/host/history`, `/api/host/catalog` | Endpoint de execução de job STT |

**Atenção:** `s7-edge-observatory/js/data.js` é uma **fixture sintética**. Android 8, CPU fictícia de 8 núcleos, 4 GiB de RAM, nomes de hardware e valores exemplares contidos na fixture **não constituem leitura física**. A versão de produção **Android 8** está documentada de forma independente na homologação do `network-observatory`. A identidade `SM-G935F` leva o backend a rotular o SoC como `Exynos 8890 (inventário do modelo)`; isso não mede a frequência/performance real.

## Evidências já documentadas do aparelho

- Telemetria existente: CPU agregada e por núcleo (amostras dependentes de disponibilidade), `/proc/meminfo`, métricas de processo, armazenamento, rede, bateria, fontes térmicas e qualidade/idade de amostras. A **temperatura da bateria** não é a da CPU; GPU Mali e devfreq trazem valores brutos e não garantem aceleração ASR.
- Em **26/10/2026**: `network-observatory` reportou aproximadamente **4,90 GiB internos livres** e **62,8 GB livres no microSD**, **após backup**. Valores históricos sujeitos a alteração.
- Homologação 26/10: **27 testes específicos executados no próprio S7 em 12,84 s**. Isso testa o observatório, não o reconhecimento de fala.
- Persistência do observatório no microSD; manter **no mínimo 2 GiB internos livres**. Não migrar ou escrever em bancos do observatório durante experimentos.
- O preflight `network-observatory/tools/s7_preflight.py` já faz verificações leves e somente leitura de armazenamento, esquema SQLite e API loopback; **não** testa CTranslate2, ABI nem ASR.

## Integração planejada

1. **Gestão SSH:** configuração em `.env` privado no host da API STT: `STT_S7_SSH_HOST`, `STT_S7_SSH_USER`, `STT_S7_SSH_PORT`. O comando é exibido somente na área administrativa. O navegador **não abre** a sessão SSH. Não escrever IP ou credenciais reais em versionamento.
2. **Telemetria sem duplicação:** se for mostrar estado do S7 no STT, obter resumo **somente leitura**, por um backend autenticado dentro da rede de confiança, de `/api/host/snapshot`; preservar `timestamp`, `quality` e valores `null`. Nunca expor automaticamente no site público todo o JSON, IP, MAC ou métricas privadas. Não presumir que API do observatório e API STT partilham a mesma porta.
3. **Preflight de ASR isolado:** observar `getprop ro.build.version.release`, `ro.build.version.sdk`, `ro.product.cpu.abi`, `uname -m`, `python --version`, `ffmpeg -version`, `/proc/meminfo`, CPU online, espaço atual, temperatura da bateria e disponibilidade do serviço local. Leitura apenas; nenhuma instalação, download, alteração de banco ou reinício.
4. **Motor:** validar bibliotecas compatíveis com Android/Termux e Bionic. `faster-whisper`/CTranslate2 usa dependências não necessariamente distribuídas para Android; se incompatível, testar `whisper.cpp` ARM64 em um adaptador isolado. Nunca declarar `int8`/FP32 disponível só pela arquitetura.
5. **Benchmark fora da fila pública:** WAV PT-BR conhecido, modelo leve, WER/CER, tempo por minuto de áudio (RTF), RSS pico, throttling, temperatura e impacto na coleta do observatório. Aplicar limites e política de retenção do STT e validar outbox/heartbeat/lease/retries antes de qualquer worker público.
6. **Ativação:** só selecionar `s7-edge` depois que um worker real `STT_WORKER_ID=s7-edge` estiver online e anunciar o modelo preparado e compute CPU suportado. Aprovação operacional separada antes de aceitar empregos públicos.

[Pré-teste de compatibilidade Termux somente leitura](S7_STT_READONLY_PREFLIGHT.md).\n\n## Checklist de evidências adicionais

- [x] Localização do frontend e backend de telemetria; referências versionadas.
- [x] Homologação funcional em LAN no S7 e dados históricos de espaço.
- [ ] Versão atual de Android/ABI/Python, RAM disponível e espaço atual registrados por preflight read-only.
- [ ] Motor ARM/Termux selecionado com prova de transcrição real.
- [ ] Benchmark, consumo e isolamento comprovados sem interferir na operação do observatório.
- [ ] Protocolo de worker homologado em ambiente de teste.
- [ ] Revisão de segurança e aprovação para eventual ativação pública.

Nenhuma conexão SSH ou coleta ao vivo ocorreu durante a preparação desta documentação. A telemetria e os documentos lidos são **evidência histórica e código**, não um teste de acesso ao aparelho nesta sessão.
