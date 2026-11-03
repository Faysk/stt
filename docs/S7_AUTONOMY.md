# Operação autônoma e recuperação — 01/11/2026

## Estado implantado

A API pública, o banco SQLite e os arquivos ativos foram migrados para o S7. O Cloudflare mantém o frontend e encaminha a API pelo conector próprio do telefone. Existe uma única fila autoritativa; não há replicação entre dois bancos ativos.

- Coordenador: API FastAPI/Uvicorn isolada, somente em loopback, porta 8786.
- Dados, banco WAL e logs rotacionados: volume microSD da aplicação.
- Runtime e segredos: ambiente privado do Termux; tokens nunca são colocados no código compartilhado.
- Inferência: Whisper Tiny/whisper.cpp, CPU, duas threads.
- Supervisão: árvore runit exclusiva do STT, com API, worker e conector; entrada Termux:Boot existente inicia essa árvore após o SD ficar disponível.
- Proteção de armazenamento: piso interno de 2 GiB, cache do worker limitado a 64 MiB, reserva por trabalho de 24 MiB, orçamento de arquivos da API de 2 GiB e recusa de fallback para disco interno quando o SD não estiver disponível.

A instalação usou pacotes oficiais Termux e dependências PyPI fixadas no instalador. Pydantic Core foi compilado no aparelho; o backend Python do Maturin foi obtido da distribuição oficial com SHA-256 conferido, usando o binário do pacote Termux. O cache interno de compilação foi removido após validar a instalação. Espaço interno livre ao final: cerca de 4,12 GB.

## Capacidade pública

Sem um executor completo selecionado e online, a API admite WAV, MP3, OGG e M4A de até **8 MiB e 5 minutos**. Novos envios de YouTube ficam desabilitados. Limites são aplicados na admissão, no corpo HTTP inclusive chunked, na validação de duração e no cliente nativo.

Quando um executor completo selecionado estiver online, a API permite os formatos gerais, até 90 MiB e duas horas. O S7 continua limitado aos próprios formatos e duração; jobs longos conhecidos não são atribuídos a ele. A seleção de executor/modelo permanece manual.

O Android bloqueia a leitura de CPU global por psutil. Esse sensor é apresentado como indisponível, preservando RAM, armazenamento, fila e histórico; nenhuma utilização zero é inventada.

## Liga/desliga por executor

O painel oferece seleção do principal e alimentação lógica separadas. **Desligar** persiste a escolha no coordenador, bloqueia claims e preloads, invalida o lease em andamento e devolve o mesmo trabalho à fila, preservando proprietário e expiração. O heartbeat rejeitado encerra o processo de inferência; a confirmação do agente aparece como “Desligado · modelo liberado”. Há um intervalo curto até o próximo heartbeat, normalmente até 15 s, além da finalização do processo.

Permanece um agente leve para receber **Ligar**. Ele não carrega modelo, não consulta CUDA nem baixa pesos enquanto desligado. A inferência e o preload Windows são processos isolados; a consulta de capacidades também usa um processo curto para não reter contexto CUDA no agente. Os modelos já baixados continuam no disco e não são apagados pelo botão. Ligar não faz preload e não muda automaticamente a seleção do principal.

A API e o conector do S7 continuam ligados quando somente seu executor é desligado. Sem outro executor elegível, os trabalhos aguardam. A escolha de desligado sobrevive ao reinício da API e do agente.

Foi corrigida a leitura do JSON do whisper.cpp: tokens individuais podem conter sequências UTF-8 incompletas. Metadados de tokens não utilizados deixam de invalidar o texto completo; se houver substituição dentro do texto transcrito, o resultado é marcado como parcial. Falhas e resultados parciais ainda válidos, com arquivo original disponível, podem ser reprocessados pelo administrador. O trabalho `9003d6d7` foi recuperado no S7 em 108,11 s para 161,39 s de áudio, com resultado parcial sinalizado. O notebook confirmou o estado desligado e a ausência de bibliotecas CUDA/modelo nos processos do agente; ligar e desligar foram testados no painel sem alterar a seleção do S7.

## Retorno do notebook

`scripts/supervise.py --install` instala o valor `FayskSTT` em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. O supervisor inicia **após login do usuário**, verifica os processos a cada 10 s, adota lançadores comprovadamente pertencentes ao ambiente do projeto e aplica espera de 30 s entre tentativas de recuperação. Um lock impede duas instâncias de supervisão.

A instalação atual está configurada com `STT_HOST_API=0` e `STT_URL=https://stt.faysk.dev`: após reiniciar e entrar no Windows, somente o worker remoto volta; não são recriados API/banco/túnel independentes no notebook. O script `start.ps1` respeita esse modo.

O worker Windows antigo carregou a configuração local antes da migração e continua ligado à API antiga pausada. O novo coordenador não o anuncia como online. No próximo reinício/login ele usará o endpoint público correto. Não foi feito reboot físico do computador, nem está instalado serviço antes da tela de login: esta sessão não possui privilégios de administrador.

## Conexão pública e transição

Foi reutilizado o túnel STT existente, sem mudanças de DNS. O serviço de origem passou de loopback:8765 para loopback:8786. O conector do S7 alcança diretamente a API no próprio telefone e possui quatro conexões HA confirmadas.

O conector Windows que já estava em execução usa temporariamente `scripts/origin_relay.py`, um encaminhamento SSH autenticado de loopback:8786 até a mesma API do S7. Assim ambos os conectores alcançam o **mesmo banco**, sem split brain. Esse relay não é necessário para o conector do celular e não tem inicialização automática. Após reboot, o supervisor do notebook inicia apenas o worker; o S7 mantém a conexão pública.

O túnel auxiliar criado durante a preparação foi removido sem receber tráfego. O túnel STT e os serviços do Observatory foram preservados.

## Migração e evidências

A API pública entrou brevemente em manutenção; o banco original foi copiado pela API de backup do SQLite, com fila pausada e sem uploads/processamentos ativos. Foram preservados 15 registros, sessões e credenciais passkey. Arquivos ainda válidos foram transferidos por SSH; a integridade SQLite foi conferida antes da troca. O arquivo temporário de mídia da migração foi removido após a validação. Os dados originais Windows permaneceram preservados sob a manutenção anterior de expiração.

Resultados na API publicada:

| Ensaio | Áudio | Tempo no worker | Resultado |
| --- | --- | --- | --- |
| OGG que aguardava na fila, `a81a3c50` | 154,01 s | 112,44 s | concluído no S7, uma tentativa |
| Upload público de referência JFK, `1797da92` | 11 s | 14,72 s | concluído no S7, uma tentativa |
| Referência AAC em M4A, `29f82eaa` | 11 s | 10,60 s | concluído no S7; TXT público conferido |

TXT, Markdown, SRT e JSON do áudio de referência foram baixados pela sessão proprietária no domínio público; texto de referência e timestamps foram conferidos. A sessão administrativa existente continuou funcionando após a cópia. Fila e outbox ficaram vazios; API local, histórico e conector próprio do S7 foram conferidos. Com fila sem trabalhos ativos, o worker foi encerrado por sinal e reiniciado automaticamente pelo runit, preservando a configuração da fila. O supervisor Windows foi verificado com uma única instância e o script de início respeitou o modo de worker remoto. A origem sem segredo continua respondendo 403.

## Verificações pendentes

- Reboot físico do Windows, seguido de login e registro do notebook como executor remoto.
- Reboot físico do S7 e retorno por Termux:Boot.
- Ensaio prolongado com tela apagada/Doze, temperatura e perda de rede/energia.
- Validação linguística/qualidade em português; conclusão do OGG não representa avaliação humana da transcrição.

Não foi realizado desligamento físico do notebook neste ensaio. A independência está implantada na arquitetura e o conector próprio do S7 está ativo; o teste físico de reboot continua pendente.

Depois que o S7 recebe novos trabalhos, não se deve apontar o site de volta para a cópia Windows antiga: ela não recebe os novos dados. Qualquer retorno de coordenador exige migração consistente própria, com admissão suspensa.


## Proposta de mitigação da quota 1027 — PR de STT #98 (03/11/2026)

**Código ainda não implantado no S7.** O agente `s7/worker.py` e a configuração runit passam a direcionar chamadas internas para `http://127.0.0.1:8786` (API autoritativa no **mesmo aparelho**) em vez de percorrer o Worker Cloudflare. O middleware FastAPI **continua exigindo `x-edge-key`**, e endpoints de worker seguem exigindo `x-worker-key` escopada. Não há bypass de origem nem segundo banco.

A chave Edge usada pelo agente local é lida apenas do arquivo `$HOME/.local/share/stt-api/runtime/.env` já instalado com `chmod 600` por `setup_s7_api.py`. A biblioteca standard do Python lê exclusivamente a entrada JSON `STT_EDGE_KEY`; o código recusa arquivos symlink, permissão aberta, tamanho excessivo e segredo inválido. A credencial **nunca** deve ser anexada a chamadas HTTPS públicas. Nenhum segredo é versionado.

### Gate de rollout no operador (não realizado)

1. Confirmar S7 API escutando apenas loopback:8786 e apontando ao SQLite/microSD autoritativos, com `STT_REQUIRE_EDGE=1`. Confirmar `runtime/.env` privado/0600 e chave Edge compatível com o processo API existente, sem imprimir o valor.
2. Rodar testes de unidade/CI, inspecionar pendências (outbox, running, uploading). **Não reiniciar API/Tunnel ou modificar DNS.**
3. Aplicar atualização do arquivo de worker e entrypoint runit em janela controlada via `scripts/setup_s7_service.py`; **testar primeiro sem restart**, iniciar somente agente após confirmar que não há trabalho ativo ou outbox pendente. O supervisor pode precisar de reinício explícito do serviço worker; `--start` isolado não garante reload se já ativo.
4. Comparar contadores por rota/proxy durante 15–30 min: controle e claims do S7 devem atingir **zero** requisições públicas ao Cloudflare Worker, mantendo `/api/health` e upload/download externos normais.
5. Reverter apenas `s7/worker.py` e `s7/service-run.sh` ao commit anterior em caso de falha; **não reverter o SQLite S7 para Windows** nem expor a origem.

A melhoria no canal local é independente do polling adaptativo ([STT #99](https://github.com/Faysk/stt/issues/99)); a quota compartilhada e os alertas permanecem em [IaC #64](https://github.com/Faysk/iac/issues/64). Resultado físico de produção, reboot e Doze ainda pendentes.
