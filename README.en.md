# wozaine (我在呢 — "I'm Here")

[中文说明](README.md) · **English**

**When your mom or dad misses you, one tap lets them hear your voice: *"I'm here."***

*wozaine* is an open-source, self-hosted AI voice companion for elderly parents. A palm-sized ESP32-S3 box sits on their table: one tap starts a natural conversation in your cloned voice, remembers their daily life, gently collects their life stories into a printable oral biography book, and sends you a concise daily report on your phone.

> Built for a real elderly parent who talks to it every day. Zero cloud lock-in on your family's history — all recordings, transcripts, and memories stay on your own server. No camera, no surveillance.

## Highlights

- **Built for elderly ergonomics**: 1.3s silence tolerance for natural pauses, 640ms audio pre-roll so the first syllable is never clipped, 30s idle auto-close, and pre-cached offline Opus fallback voice so the device never responds with dead silence.
- **Voice-first, zero-surveillance ethics**: No photo/video APIs, no camera initialization in firmware patches, strict CSP (`script-src 'self'`, `style-src 'self'`), and `Permissions-Policy: camera=()` — enforced in CI by `tests/test_no_camera.py` and `tests/test_web_static.py`.
- **Oral biography ("StoryWorth" in daily chat)**: Asks one small, concrete memory question per conversation, saves original quotes with audio playback, follows up days later to verify details, and renders chapters into a browsable flip-book (`story.py` + `book.py`).
- **Dialect typo book (`typo_book.py`)**: Fixes recurring ASR misrecognitions of family names/places via pinyin tolerance, context-aware LLM judging, historical cascading corrections, and per-occurrence undo while always preserving `asr_raw`.
- **Multi-provider failover & alerts**: Primary + backup ASR (Aliyun Bailian / Tencent Cloud) and TTS (MiniMax / CosyVoice), OpenAI-compatible LLM endpoint (`DEEPSEEK_API_URL` + `DEEPSEEK_EXTRA_BODY`), and webhook alerts (WeCom / Feishu / DingTalk / custom command).
- **Flexible family roles**: Works for mom, dad, or grandparents via `family.json` (`elder_label`, `elder_call`, `elder_pronoun`, `child_role`, `book_title`) without editing Python source code.

## Try the Web Console in 60 Seconds (No Hardware or API Keys)

### Option A: Docker Compose

```bash
docker compose --profile demo up --build
# Optional scenes: ok | talking | charging | low | offline | alert
DEMO_SCENE=low docker compose --profile demo up --build
```

Open `http://127.0.0.1:3093` to explore the watch-face home status, daily reports, conversation audio logs, memory editor, and flip-book using fictional demo data (`tools/demo_data.py`).

### Option B: Local Python (Linux / macOS / Windows)

```bash
# macOS / Linux
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python3 tools/demo_data.py demo-data
COMPANION_DATA_DIR=demo-data venv/bin/python3 tools/demo_bridge.py --scene ok &
COMPANION_DATA_DIR=demo-data venv/bin/python3 server.py
```

```powershell
# Windows PowerShell
.\deploy\start_windows.ps1 -Demo
```

> Note: Local demo mode isolates business data in `demo-data/`, while `.env` and `persona.md` stay at the repo root — avoid saving config/persona edits inside demo mode if you already have production files there.

## Self-Hosted Deployment

### 1. Ubuntu / Debian Server (Recommended for Public HTTPS)

```bash
git clone https://github.com/runliu2me/wozaine.git && cd wozaine
cp .env.example .env                  # Fill in LLM / ASR / TTS API keys
cp family.example.json family.json    # Names, roles, cities, book title
cp persona.example.md persona.md      # How you talk to your parent
bash deploy/install.sh --domain chat.example.com --email you@example.com
```

### 2. Docker Compose (Behind Your Own Reverse Proxy)

Prepare `.env` (including `ADMIN_PASSWORD_HASH`, `ADMIN_SESSION_SECRET`, and `XIAOZHI_CTL_TOKEN`), `family.json`, and `persona.md` in the repo root, ensure `data/` and `public/` are writable by UID 1000 on Linux (`mkdir -p data public && chown 1000:1000 data public`), then run:

```bash
docker compose --profile serve up -d --build
```

All ports (`3093`, `3095–3098`) bind to `127.0.0.1` with `ALLOW_LOCAL=0` by default — put Nginx, Caddy, or Traefik with HTTPS in front (mirroring [deploy/nginx.conf.template](deploy/nginx.conf.template), including `auth_request /_auth` on `/ws/call` and `/ws/intercom`). After editing `.env` on the host, restart the container (`docker compose --profile serve restart wozaine`). For scheduled jobs (`daily_report.py` / `device_monitor.py`), invoke them through the entrypoint so `.env` is loaded safely: `docker compose --profile serve exec -T wozaine /app/deploy/docker-entrypoint.sh python3 daily_report.py`.

### 3. Firmware & Hardware

The server implements the standard [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) protocol. Stock upstream firmware works by pointing its Custom OTA URL to your server; applying the companion patches in [firmware/](firmware/README.md) adds intercom, proactive check-ins, remote reboot, crash reporting, and Wi-Fi resilience. Verified boards include `zhengchen-minicam` and `ESP32-S3-BOX-3`.

## Documentation

Full guides are maintained in Chinese (and any AI coding assistant can walk you through deployment via [AGENTS.md](AGENTS.md)):

- [README.md](README.md) — Main Chinese overview & screenshots
- [AGENTS.md](AGENTS.md) — Step-by-step AI-assisted deployment checklist
- [docs/部署.md](docs/部署.md) — Full server setup & API key guide
- [docs/跨平台.md](docs/跨平台.md) — Windows, macOS (Apple Silicon), Docker & LAN device testing
- [docs/定制指南.md](docs/定制指南.md) — Persona, family roles (dad/daughter/etc.), calendar sync, phone line, custom LLM/ASR/TTS providers
- [docs/网络安全.md](docs/网络安全.md) & [SECURITY.md](SECURITY.md) — Security architecture, threat model & reporting
- [firmware/README.md](firmware/README.md) — Patching, building, flashing & board notes
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — Third-party licenses & attributions

## Running Tests

```bash
for t in tests/test_*.py; do venv/bin/python3 "$t" || exit 1; done
for f in web/*.js; do node --check "$f" || exit 1; done
```

## License

MIT — see [LICENSE](LICENSE). Never commit real recordings, credentials, or family data.
