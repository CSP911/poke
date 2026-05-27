# POKE Hub Deployment Guide

## Prerequisites

- **Node.js** >= 18
- **npm** (comes with Node.js)
- **nasm** (optional, fallback assembler): `brew install nasm` / `apt install nasm`
- **Cross compilers** (optional, for ARM/x86 C deploy):
  - `i686-elf-gcc` / `i686-elf-objcopy`
  - `aarch64-elf-gcc` / `aarch64-elf-as` / `aarch64-elf-objcopy`

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url>
cd poke
npm install

# 2. Configure environment
cp .env.example .env   # or create manually
# Edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...    (required for LLM features)
#   HUB_SECRET=your-secret-here     (optional, omit for open dev mode)
#   PORT=3333                        (default: 3333)
#   LOG_LEVEL=info                   (debug|info|warn|error)

# 3. Run
node hub.js

# 4. Test
npm test
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | For LLM | - | Anthropic API key for Claude |
| `HUB_SECRET` | No | _(open mode)_ | Bearer token for auth. Omit to disable auth. |
| `PORT` | No | `3333` | HTTP server port |
| `HTTPS` | No | - | Set to `1` to enable HTTPS on port 3334 |
| `HTTPS_PORT` | No | `3334` | HTTPS port (when HTTPS=1) |
| `LOG_LEVEL` | No | `info` | Log verbosity: `debug`, `info`, `warn`, `error` |

## HTTPS Setup (for mobile voice)

Web Speech API requires HTTPS. Generate self-signed certs for development:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes
HTTPS=1 node hub.js
```

## Device Profiles

Place JSON device profile files in `profiles/`. They are loaded on startup.
Each profile must have: `vendor_device`, `name`, `type`.

## Production Deployment

### systemd (Linux)

```ini
# /etc/systemd/system/poke-hub.service
[Unit]
Description=POKE Hub
After=network.target

[Service]
Type=simple
User=poke
WorkingDirectory=/opt/poke
ExecStart=/usr/bin/node hub.js
Restart=on-failure
RestartSec=5
Environment=NODE_ENV=production
Environment=LOG_LEVEL=warn
EnvironmentFile=/opt/poke/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable poke-hub
sudo systemctl start poke-hub
```

### Docker

```dockerfile
FROM node:18-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY . .
EXPOSE 3333
CMD ["node", "hub.js"]
```

```bash
docker build -t poke-hub .
docker run -d --name poke-hub -p 3333:3333 --env-file .env poke-hub
```

### Process Manager (PM2)

```bash
npm install -g pm2
pm2 start hub.js --name poke-hub
pm2 save
pm2 startup
```

## Health Check

```bash
curl http://localhost:3333/nodes
```

## Architecture

```
hub.js              Entry point (wires modules, starts server)
hub/
  logger.js         Structured logging with levels
  server.js         HTTP routing, auth, request handling
  nodes.js          Node registry, device profiles, health check
  agent.js          Agent loop, tool definitions, tool execution
  llm.js            All LLM calls (plan, generate, answer)
  compiler.js       Assembly/C compilation, image code execution
  transport.js      Network transport to POKE nodes (HTTP, TCP)
```

## Graceful Shutdown

The hub handles SIGTERM and SIGINT for clean shutdown. Health checks stop, connections drain, then the process exits. A 5-second forced exit timeout prevents hangs.
