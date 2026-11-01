# Login administrativo por QR e passkey

Implementação da [issue #84](https://github.com/Faysk/stt/issues/84). **Desativada por padrão; não implantada nem homologada fisicamente no S23+.** A autenticação pública das transcrições e a dos executores continuam independentes.

## Experiência

O PC exibe um QR de 90 segundos e um código de seis dígitos. O celular abre a página de aprovação no mesmo domínio, mostra contexto reduzido do navegador e pede que o operador confira o código. Aprovar ou rejeitar exige uma assinatura da passkey autorizada e verificação do usuário pelo autenticador (digital/PIN). Só o navegador que criou o pedido recebe a sessão administrativa. A página do celular não recebe essa sessão.

O QR contém uma URL com ID público aleatório de 256 bits. O vínculo secreto do PC fica em cookie HttpOnly independente. O código curto é uma conferência humana, não senha. O sistema mantém uma única credencial autorizada. Ela pode ser sincronizada pelo gerenciador de passkeys: **isso não comprova exclusividade física do S23+**. O QR customizado também não comprova proximidade Bluetooth; não aprove QR recebido de terceiros.

## Configuração e ativação em staging

```dotenv
STT_ADMIN_QR_ENABLED=0
STT_ADMIN_LEGACY_ENABLED=1
```

- QR desligado: fluxo antigo permanece disponível, sem cadastro público.
- QR ligado: a página cria automaticamente um pedido e tenta restaurar uma sessão válida.
- Legado desligado: `x-admin-key` deixa de autorizar rotas administrativas. Só desligar após pareamento e validação de recuperação; a CLI local continua disponível.
- RP ID fixo: `stt.faysk.dev`; origin exata: `https://stt.faysk.dev`. Não há variável que libere origins arbitrárias.

Aplicar primeiro dependências e backend em ambiente isolado, com snapshot SQLite, depois assets. O deploy Windows e o Worker são etapas diferentes; seguir `OPERATOR_PUBLISH.md`, preservar Tunnel e trabalhos em execução. A implementação não autoriza restart ou ativação automática. Teste local com RP substituído em memória serve apenas para QA da interface, não para cadastrar passkeys de produção.

## Primeiro pareamento e recuperação local

No ambiente do serviço, com acesso local confiável e as dependências bloqueadas por `uv.lock`:

```powershell
uv run python -m scripts.admin_pair
```

A CLI faz snapshot consistente via SQLite backup antes da migração e gera uma URL/QR privado de pareamento, válido por cinco minutos. Abrir no celular e cadastrar a passkey. O token está no fragmento da URL, removido imediatamente da página e enviado somente no corpo do pedido de opções. Não compartilhar, capturar em logs ou guardar esse QR. A aprovação de cadastro pelo autenticador deve ser feita pelo proprietário.

Sem credencial já autorizada, ninguém pode abrir cadastro público. Após o primeiro cadastro, nova inscrição online é recusada, inclusive para uma sessão administrativa comum. Administração de credenciais nesta versão é **somente pela CLI local**; não há inclusão silenciosa pelo painel.

```powershell
uv run python -m scripts.admin_pair --recover
uv run python -m scripts.admin_pair --revoke-all
```

`--recover` revoga credencial, sessões, pedidos e desafios e abre um novo pareamento. `--revoke-all` revoga tudo sem novo pareamento. São ações destrutivas de autenticação para execução consciente pelo operador; não apagam transcrições, fila ou outbox. A chave legada, quando habilitada, é outro meio de recuperação e não é revogada por esses comandos.

Snapshots em `db/auth-backups` têm o mesmo grau de sensibilidade da base. Preservar ACL restrita, não versionar nem servir por HTTP. Após restaurar uma base, executar recuperação/revogação antes de reativar acesso externo, para não ressuscitar sessões antigas. A migração é aditiva e idempotente, versão 1 em `admin_auth_migrations`; não exige apagar tabelas de jobs para rollback. Desabilitar a feature e voltar código/dependências/frontend compatíveis mantém o acesso legado apenas se sua flag estiver habilitada.

## Contrato e controles

| Rotas sob `/api/admin/auth` | Função |
| --- | --- |
| `GET config` | Flags de capacidade; sem dados de credenciais |
| `POST qr/start`, `qr/status`, `qr/cancel`, `qr/consume` | Pedido vinculado ao cookie do PC, polling, cancelamento e consumo atômico |
| `POST approve/context` | Contexto reduzido e código; nunca aprova |
| `POST ceremony/options`, `ceremony/finish` | Cadastro autorizado por token local ou aprovação/rejeição assinada |
| `GET session`, `POST logout` | Recuperação de CSRF e revogação de sessão |

- Criptografia delegada a `webauthn` (lockfile); valida origin, RP ID, challenge, assinatura, presença e UV. A aplicação exige credencial autorizada e userHandle do único admin, quando enviado. Contadores são verificados pela biblioteca, incluindo a regra de contador zero para passkeys sincronizáveis.
- Desafios são vinculados ao pedido, decisão e cookie da cerimônia; expiram em no máximo 120s e no prazo do QR/pareamento. Tentativa inválida consome o desafio. Trocar de QR invalida o anterior.
- `__Host-stt_admin`: Secure, HttpOnly, SameSite=Strict, Path=/, sem Domain; token aleatório de 384 bits, apenas hash persistido. Inatividade de 30 min, duração absoluta de 8h. Logout e recuperação revogam no servidor. Nenhum token administrativo no QR ou Web Storage.
- Operações de cookie exigem origin exata e custom header; mutações administrativas e pedidos vinculados exigem também CSRF sincronizado derivado do token secreto. Sem CORS permissivo. Opções/finish têm payload limitado a 32 KiB, inclusive streaming.
- Limites por rede e globais persistidos no SQLite; endereço de borda só é aceito após guard de EDGE_KEY. Há limite de cardinalidade de pedidos/desafios. Polling de 2,5s com backoff.
- Logs de autenticação usam apenas vocabulário de eventos, expiram em 30 dias. Desafios e pareamentos expiram; pedidos encerrados são purgados após uma janela curta de diagnóstico. Limpeza periódica também executa sem chamadas de login.
- Anti-cache/noindex, no-referrer, CSP e HSTS. QR gerado por biblioteca local `qrcode`, sem serviço externo. Aprovação é sempre explícita; GET nunca cria sessão.

## Evidência e homologação pendente

Testes Python geram um autenticador ES256 de fixture e executam cadastro e assertions reais contra o verificador WebAuthn, sem mocks da assinatura. Cobrem UV/origin/RP/challenge/userHandle/assinatura inválidos, chave desconhecida, replay, contador, vínculo de navegador, concorrência, expiração, cancelamento, rejeição, recuperação, CSRF, quotas, origem protegida e preservação das tabelas existentes. Testes Edge verificam múltiplos Set-Cookie. QA visual local cobre QR, cancelar e regenerar em desktop e viewport 390px.

Ainda necessários antes de habilitar em produção:

1. Dependências e migração em cópia de staging Windows, snapshot e recuperação/rollback conferidos.
2. Pareamento real pelo proprietário no S23+ e Chrome Windows/Android: aprovação, rejeição, timeout, logout, cookie após recarregar e passkey ausente em outro celular.
3. Publicação coordenada backend/Worker e teste do fluxo pelo domínio HTTPS, com origin direta bloqueada.
4. Confirmar que envio e transcrição públicos seguem funcionando e o executor principal permanece o mesmo.

## Referências

[Registro WebAuthn](https://duo-labs.github.io/py_webauthn/registration.html), [autenticação WebAuthn](https://duo-labs.github.io/py_webauthn/authentication.html) e [OWASP Passkey Security](https://cheatsheetseries.owasp.org/cheatsheets/Passkey_Security_Cheat_Sheet.html).
