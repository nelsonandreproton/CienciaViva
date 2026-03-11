# Ciência Viva no Laboratório — Monitor 2026

Monitoriza https://www.cienciaviva.pt/ciencia-viva-no-laboratorio/ e envia um alerta Telegram quando as inscrições para 2026 abrirem.

## Como funciona

- Verifica a página uma vez por dia (09:00 hora de Lisboa por padrão), com 3 tentativas e backoff em caso de falha
- **Deteção primária**: procura "2026" + palavras-chave de inscrição na página → alerta imediato
- **Deteção secundária**: deteta qualquer alteração de conteúdo (debounce de 2 dias) → alerta para verificação manual
- **Lembrete semanal**: enquanto `alerted_2026=true`, envia lembrete de 7 em 7 dias
- Guarda estado em `data/state.json` para evitar alertas duplicados
- Erros de rede limitados a 1 alerta por 24h

## Deploy na VM Hetzner

### Pré-requisitos

- Docker + Docker Compose instalados na VM
- Bot Telegram criado via [@BotFather](https://t.me/BotFather)
- Chat ID (envia `/start` ao [@userinfobot](https://t.me/userinfobot))

### Instalação

```bash
# 1. Copiar ficheiros para a VM
scp -r . user@<IP_VM>:/opt/cienciaviva-monitor/

# 2. Na VM
cd /opt/cienciaviva-monitor
mkdir -p data

# 3. Configurar credenciais
cp .env.example .env
nano .env   # preenche BOT_TOKEN e CHAT_ID

# 4. Build e arrancar
docker compose up -d --build
```

### Verificar funcionamento

```bash
# Ver logs em tempo real
docker compose logs -f

# Testar envio de mensagem Telegram
docker compose exec monitor python monitor.py --test

# Forçar uma verificação imediata
docker compose exec monitor python monitor.py --check-now

# Limpar estado após falso positivo ou após te inscreveres
docker compose exec monitor python monitor.py --reset

# Ver estado atual
cat data/state.json

# Ver saúde do container
docker ps
```

### Gestão

```bash
# Parar
docker compose down

# Reiniciar
docker compose restart

# Atualizar código
docker compose up -d --build
```

## Variáveis de ambiente

| Variável | Descrição | Default |
|---|---|---|
| `BOT_TOKEN` | Token do bot Telegram (obrigatório) | — |
| `CHAT_ID` | ID do chat de destino (obrigatório) | — |
| `CHECK_TIME` | Hora da verificação diária (hora de Lisboa) | `09:00` |
| `STATE_FILE` | Caminho do ficheiro de estado | `/data/state.json` |

## Reset de estado

Após te inscreveres (ou em caso de falso positivo):

```bash
docker compose exec monitor python monitor.py --reset
```

Para apagar todo o histórico e começar do zero:

```bash
docker compose down
rm data/state.json
docker compose up -d
```

## CI/CD (GitHub Actions)

O workflow em `.github/workflows/deploy.yml` faz deploy automático a cada push para `main`.

Configura os seguintes secrets no repositório GitHub:

| Secret | Descrição |
|---|---|
| `HETZNER_HOST` | IP ou hostname da VM |
| `HETZNER_USER` | Utilizador SSH (ex: `root`) |
| `HETZNER_SSH_KEY` | Chave SSH privada (conteúdo de `~/.ssh/id_ed25519`) |
