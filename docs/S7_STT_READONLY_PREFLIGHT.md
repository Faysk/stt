# S7 Edge / Termux — pré-teste somente leitura

O script `scripts/probe-s7-stt.sh` examina capacidades **sem instalar, gravar, reiniciar, fazer downloads ou executar inferência**. Útil para avaliar a viabilidade de um worker CPU ARM antes de tocar no Observatory. Issue: https://github.com/Faysk/stt/issues/66

Execute no PowerShell 7, em um computador com SSH autorizado para o S7:

```powershell
cd E:\Projects\stt
git pull --ff-only origin main
$probe = (Get-Content -Raw -Encoding UTF8 .\scripts\probe-s7-stt.sh) -replace "`r", ""
$probe | ssh -p 8022 <TERMUX_USER>@<S7_LAN_IP> sh -s
```

Substitua os marcadores pelo usuário Termux e pelo IP local do aparelho informados no inventário privado. Confira a identidade/chave do host ao conectar; não desative a verificação do SSH. A saída foi desenhada para omitir caminhos, nomes de conta, IPs e credenciais.

O relatório informa Android/SDK/ABI, arquitetura kernel, memória, espaço interno e SD, temperatura **da bateria**, e presença de FFmpeg, clang, cmake, whisper-cli e módulos Python. Presença de executável/módulo não comprova que ele funcione com o Android/Bionic. Valores `unavailable` não são zero.

**Importante:** `inference_test=not_run` confirma que não houve teste de transcrição. Não usar a fila STT de produção nem iniciar downloads/modelos até avaliar os resultados. Preserve pelo menos 2 GiB livres no armazenamento interno e o banco do Observatory no microSD.