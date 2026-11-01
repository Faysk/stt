# Autonomia do S7 — lacunas verificadas em 01/11/2026

## Estado atual

O S7 executa Whisper Tiny e busca trabalho por HTTPS. SSH pela LAN foi validado para manutenção. A API, SQLite, arquivos e conector público continuam no notebook. Desligar o notebook interrompe novos envios e a coordenação: worker independente do USB não equivale a serviço independente do notebook.

No aparelho, a inspeção encontrou ausentes FastAPI, Uvicorn, python-multipart, python-dotenv, psutil, WebAuthn, qrcode e cloudflared. Nenhuma dependência foi instalada nem serviço do Observatory alterado nessa inspeção.

## Caminho para operação independente

1. Separar dependências da API das dependências de inferência Windows; não instalar faster-whisper/CTranslate2/PyAV no Android.
2. Homologar API isolada no S7, com banco, uploads, resultados e logs no microSD. Preservar 2 GiB livres no interno; manter orçamento explícito para cache temporário.
3. Usar uma única API e fila autoritativas. Não colocar dois bancos SQLite independentes atrás do mesmo domínio e não sincronizar bancos abertos por cópia de arquivo.
4. Provisionar conexão pública de saída própria do S7, com segredo de origem e limites de admissão. A porta SSH permanece apenas na rede local.
5. Manter a API no S7 e registrar notebook/3090 como executores opcionais. Quando somente o S7 estiver disponível, admitir apenas os formatos e tamanhos homologados e explicar as restrições antes do envio.
6. Validar sessões existentes, login passkey, expiração de 24 h, recibos/outbox, reserva de disco e comportamento quando SD ou rede ficam indisponíveis.
7. Testar com notebook efetivamente desligado: novo upload, inferência, leitura e exportação, seguido de reboot e ensaio prolongado com tela apagada. Só então trocar a origem pública.

Esta é uma proposta de implementação; a API autônoma não está instalada. A troca de origem não foi feita.

## Incidente OGG

O trabalho `a81a3c50` estava na fila sem tentativas porque a API ativa aceitava apenas WAV/MP3 para o S7; o upload era OGG, 384.738 bytes. A seleção exclusiva do S7 impedia o notebook de assumir esse formato.

O cliente foi atualizado para demuxer OGG explícito, mantendo protocolo FFmpeg limitado a `file,pipe`, entrada de 8 MiB, duração de 120 s e orçamento de armazenamento. Conversão sintética OGG/Opus de 1 s para PCM mono foi validada no aparelho pela conexão SSH. Isso não é uma transcrição concluída desse trabalho.

A alteração de elegibilidade da API está no código; sua ativação depende de reiniciar a API. A tentativa de reinício foi rejeitada pela política da ferramenta de execução antes de parar o serviço. A pausa temporária da fila foi desfeita e as configurações anteriores restauradas. O painel usa os formatos anunciados pela API para não antecipar suporte ainda não ativo.
