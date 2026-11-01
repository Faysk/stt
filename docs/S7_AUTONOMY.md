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
