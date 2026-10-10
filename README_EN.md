# AnelfAgent

**v0.3** · A unified agent framework — Autonomous Reasoning · Semantic Memory · Tool Orchestration · Multimodal Generation · Realtime Voice · Multi-Channel Communication

[简体中文](README.md) | **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent is an open-source, self-hosted AI agent runtime. Give it one LLM API key and it becomes an assistant that **remembers things long-term, teaches itself skills, works proactively, and lives inside multiple chat platforms at once**. It ships with an autonomous decision engine, hybrid semantic memory, self-learning skills, sub-agent delegation, recoverable workflows, MCP tool bridging, and multi-platform channel adapters. It covers text, image, speech, video, and music, and comes with a modern WebUI for configuration, conversation, and operations.

> This repository is the **0.3 stable baseline**: the architecture and capabilities are well established, ready for self-hosted deployment and downstream extension.

---

## Quick Start

> Goal: **a running agent in 5 minutes**. All you need is Python, Node.js, and one LLM API key.

### 1. Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | **3.11 – 3.12** | Main runtime |
| Node.js | **24** (recommended, see `.nvmrc`; minimum 22.12) | Builds the WebUI frontend; optional — without it everything runs except the web interface |
| [uv](https://github.com/astral-sh/uv) | latest | Python package manager (strongly recommended; the start script uses it automatically) |

### 2. Clone & Configure

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

# Create the three core config files from templates
cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json
```

Open `config/llm_clients.json` and fill in your API key under any provider (OpenAI / Anthropic / DeepSeek / Zhipu — 100+ providers supported through litellm). **One working chat model plus one embedding model is enough to start**; everything else can be configured later from the WebUI.

> You can also skip editing JSON entirely: start with it empty, log into the WebUI, and add models from the "Models" page with a few clicks (hot-reload, no restart).

### 3. Build the Frontend & Launch

```bash
# Build the WebUI (one-time; rebuild after frontend code updates)
cd web/frontend && npm install && npm run build && cd ../..

# Launch (the start script syncs Python dependencies automatically)
./start.sh                 # macOS / Linux
start.bat                  # Windows
```

### 4. Open the WebUI

Visit: **http://127.0.0.1:8092/webui/**

From the WebUI you can chat with the AI, add/switch models, enable channels, schedule heartbeat tasks, inspect memory and skills, and manage permissions — **almost everything is point-and-click with hot-reload on save**.

### Daily Operations

```bash
./restart.sh               # One-shot background restart (after config/code changes)
Ctrl + C                   # Stop (when running in the foreground via start.sh)
uv run python launch.py --no-webui   # Agent core only, no web interface
```

- `start.sh` includes a **crash guardian**: crashes are auto-restarted, giving up after 5 consecutive crashes
- Single-instance guard: a second launch automatically cleans up the stale process holding the port — no double-running
- For boot autostart / background daemon: use `restart.sh` (nohup), or wrap it in systemd / pm2 yourself

### Connect Your First Chat Channel

Go to the "Channels" page in the WebUI and follow the prompts. The fastest to try are **WeChat** (QR-code login, no public endpoint needed) and **QQ** (via NapCat). See the "Multi-Platform Channels" section below for per-platform notes.

---

## What Can It Do?

In one sentence: **a 7×24 online AI that remembers, learns skills, and gets things done on its own**.

| Scenario | Description |
|---|---|
| 💬 Chat assistant | Lives in QQ / WeChat / Feishu / Telegram / Bilibili / AcFun / WebUI — one brain across all platforms |
| 🧠 Long-term memory | Semantic memory + knowledge graph: your preferences, facts, and relationships, remembered and sharpened over time |
| 🌱 Self-improvement | Distills "skills" (troubleshooting know-how, task playbooks) from conversations and reuses them next time |
| 🛠 Real work | Files, shell commands, web search, desktop control, smart home, server administration — governed by rules and AI review |
| ⏰ Proactivity | Heartbeat scheduling: timed reminders, recurring tasks, idle-time self-reflection — no prompt needed |
| 🎨 Multimodal | Image, speech, video, and music generation; sticker search; image understanding; speech transcription |
| 📞 Realtime voice | Full-duplex call engine: listen while speaking, barge-in anytime, voiceprints tell *who* is talking |
| 👀 Vision | Screenshots / screen-watching (continuous region monitoring), face recognition, desktop control (see-and-click) |
| 🤝 Delegation | Fan one task out to parallel sub-agents and aggregate results; resumable runs and mid-run steering |
| 🔄 Workflows | Declarative DAG orchestration with journaled crash recovery — finished steps are never re-paid |
| 🔌 Open | MCP tool bridging, OpenAI-compatible Responses API, plugin marketplace, webhook/HTTP channels |

---

## Core Capabilities

### Autonomous Mind

```
Message queued → situation gathered (messages/tasks/memory/goals) → meta-decision → think_loop (multi-round tools) → done
```

| Decision Type | Purpose |
|---|---|
| `REPLY` | Reply to messages |
| `REFLECT` | Heartbeat / reflection tasks |
| `REMEMBER` | Proactive memorization |
| `PROACTIVE` | Proactive outreach |
| `TOOL_ACTION` | Autonomous tool operations |
| `PLAN` | Goal planning |

System prompts are layered by change frequency (stable → summary → history → context → volatile → provider) and kept byte-stable to hit Anthropic / OpenAI **prefix caching**, cutting long-conversation cost significantly.

### Entity Registry & Tool Gating

Every capability (tools / models / channels / MCP / storage) is registered in the `EntityRegistry`. The AI discovers tools through a two-level catalog → group flow and always receives a "just enough" toolset:

| Source | Description |
|---|---|
| `always` | Always-on tools (`end_reply` / `send_message`, etc.) |
| `mcp:*` | MCP server tools |
| `channel` | Capability match of the current channel |
| `tag_match` | Activated by message tags (e.g. `media:image`) |
| `hot_recall` | Top-N frequently used tools |
| `discovered` / `activated` | Dynamic discovery and sleeping-group wake-up |

- **check_fn gating**: environment precondition checks (TTL cache + transient-failure grace); failing tools never enter the schema
- **Sleep / activate**: `allow_sleep` tools show only a brief by default; the AI calls `activate_tool_group` to wake them on demand

### Hybrid Semantic Memory

Hybrid scoring over Embedding + FTS5 + tag matching + time decay. Optional **Cognee** knowledge-graph projection and federated recall — with automatic degradation on failure and SQLite always the authoritative store.

- **LLM retrieval planning**: a light model plans multiple complementary queries for the first-pass recall; an async deep probe streams in extra findings while the AI thinks; a three-key ledger guarantees each fact appears at most once per reply
- **Memory rules document**: write routing / tag discipline / query routing consolidated into one system-level prompt document, editable from the WebUI memory page
- **Forgetting governance**: importance relaxation + retrieval practice effect + archive/tombstone fallback recall (restorable); graph edge decay + an AI curation agenda (facts from the system, decisions from the AI)
- **Write-time dedup**: a structured judgment engine classifies each write as novel / covered / evolution / fragments — the hot path makes zero extra LLM calls for most writes

### Self-Learning Skills

After each conversation, an LLM hook reviews the transcript in the background and distills skills into `workspace/skills/SKILL.md`; a two-layer recall (catalog + semantic matching) injects them when relevant; heartbeat curation demotes / archives / merges stale ones. Skills accumulate, and the agent gets better with use. Typing `/skill-name` invokes a skill deterministically.

### Sub-Agents & Workflows

- **Sub-agents**: `delegate_task` supports parallel fan-out, background mode, and independent iteration budgets, with profile-based model selection (built-in easy/medium/hard tiers); `follow_up_agent` resumes a run losslessly from its full transcript; progress streams, usage attribution, and a Web panel give end-to-end observability; `send_to_agent` steers mid-run (at step boundaries) or appends after completion
- **Workflows**: declare a DAG in JSON (ask sub-agent steps / tool steps + dependencies); journaled crash recovery (finished steps reused via input fingerprints — never re-paid), revision imports from parent runs, and gated re-runs (fix-then-retry when a step result misses expectations)

### Heartbeat & Tasks

Task content (`config/tasks/*.json`) is separated from scheduling, with five orthogonal triggers:

| Trigger | Description |
|---|---|
| `heartbeat` | Every N heartbeats |
| `scheduled` | Fixed times each day (per-slot dedup) |
| `idle` | After sustained inactivity (one global slot, e.g. self-reflection) |
| `manual` | Manual / AI-initiated only |
| `trigger_event` | Fired by events (via the LLM hook surface) |

Each heartbeat also runs built-in maintenance: memory health checks, skill curation, log compaction, idle-conversation folding, graph governance agenda, and more.

### Realtime Voice & Vision

- **Realtime call engine** (`agent/realtime`): full-duplex sessions, smart turn detection (smart_turn), barge-in, echo handling; qwen realtime ASR preferred with FunASR fallback
- **Full voice stack** (`agent/audio` · `voice` · `tts`): ASR transcription, streaming TTS (MiniMax / CosyVoice / Qwen-TTS / edge-tts), and **voiceprint recognition** — the agent can tell *who* is speaking across channels
- **Vision framework** (`agent/vision` + the `screen` entity): screenshots / screen-watching (continuous region monitoring), face recognition (optional [`deploy/face_server`](deploy/face_server) sidecar), and desktop control via `desktop_act` (see-and-click mouse/keyboard)
- **Multimodal generation**: unified adapters for image / speech / video / music (OpenAI · MiniMax · SiliconFlow · DashScope, etc.)

### Model Management (LLMManager)

- **Two-level structure**: Provider → Model, organized by capability (chat / tools / vision / embedding …); disabled models are removed from every path
- **Candidate-chain fallback**: `chat_with_fallback` advances along candidate models; context overflow fails fast to a larger-window candidate
- **Config-driven thinking contracts**: each model declares its reasoning-parameter mapping in config — zero model-name special-casing in code
- **Dual protocols**: Chat Completions and Responses (auto-falls back on 404 in `auto` mode), unified through litellm
- **Hot reload**: hand-edited `llm_clients.json` takes effect via three paths (file watcher / Web / AI tools); unchanged clients are never touched
- **Structured judgment** (`agent/judgment`): a unified Choice / Score / Noul judging channel with optional TypeSafe Jev integration and plain-model fallback

### Security & Permissions

| Mechanism | Role |
|---|---|
| Unified permission engine | `tool_name(arg-glob)` + allow / ask / deny, at global and per-channel scope; rules define boundaries; ask requests AI review without human approval |
| CRITICAL risk backstop | `@tool(risk="CRITICAL")` automatically receives AI review unless an explicit rule applies |
| Approval audit | All non-default decisions are persisted; no human approval or trust counters |
| Tool guardrails | Exact-failure repeats / consecutive failures / no-progress loops → warn / block / halt |
| Session tokens + threat scanning | One-time tokens mark trusted history; injection-pattern scanning guards tool results and memory writes |
| Automatic redaction | API keys / tokens / passwords are masked in tool results and logs |
| WebUI auth | `auth.password` in `config/webui.json` (empty = no login); with `auth.strict=true` and no password, a 32-char admin password is generated for you; Bearer API keys for programmatic access |

### LLM Hook Surface

A unified registration primitive for "deriving context-aware async LLM work at LLM thinking boundaries": multiple hooks can attach to the same event (`after_reply` / `delegation_resolved` / `llm_end` …) and run in parallel, each independently governed (concurrency / cooldown / debounce / recursion guard). Background skill review, task event triggers, and entity hooks are all built on it.

### MCP Bridging

stdio / SSE / Streamable HTTP; background async connections with tools auto-registered as entities and hot-reloadable; tool-list change notifications sync incrementally; image results are persisted and injected into vision models (screenshot-style MCP servers are literally *seen*); liveness probing, reconnect budgets, and OAuth included.

### Plugins & Hot-Swapping

- **Minecraft companion**: reuses the open-source Mineflayer MCP runtime for Java 26.1 actions and in-game chat.
  See [`channels/minecraft/README.md`](channels/minecraft/README.md) for setup.
- **Plugin system** (`entities/plugins`): a plugin is a directory package with a manifest + skills/ + .mcp.json + tools.py; marketplace subscriptions (local directory or git repo); the AI can install / upgrade / remove plugins itself
- **Module hot-swap**: adding or removing entity/channel directories is reconciled automatically via directory watching (instant registration, complete teardown), with a manual hot-sync button in the WebUI

### Self-Ops & Data

| Capability | Description |
|---|---|
| SSH remote management | Connections / commands / file transfer — the AI can administer its own deployment |
| DevOps | Project code updates / frontend builds / app restarts (with restart handoff notes) |
| Storage volumes | All persistent data registered as 8 volumes with online hot backup / restore / migration / SQL export-import, visualized in the WebUI |
| External SQL sources | PostgreSQL / MySQL connection registry, browsable from the WebUI |
| File sharing | Generate public download links for workspace files and manage their lifecycle |

---

## Multi-Platform Channels

Channels auto-discover from directories and hot-swap; adding one only takes `channels/{name}/adapter.py` + config:

| Platform | Notes |
|---|---|
| **QQ** | OneBot v11 + NapCat (direct connection) |
| **WeChat** | iLink Bot API, QR-code login, no public webhook needed (see [`channels/weixin/README.md`](channels/weixin/README.md)) |
| **Feishu (Lark)** | WebSocket event-driven; plus a standalone Feishu task-delegation ACP integration ([`acp/`](acp) + `scripts/anelf-acp`, see [`docs/feishu-task-binding.md`](docs/feishu-task-binding.md)) |
| **Telegram** | Bot API long polling / webhook |
| **Bilibili / AcFun** | Danmaku and private messages |
| **WebUI** | Built-in three-pane workbench (file tree / conversation / dock), SSE push, AI-driven UI commands |
| **HTTP API** | Synchronous request-response |
| **CLI** | Terminal debugging |
| **Responses API** | Expose AnelfAgent itself as an OpenAI-compatible model service (`/v1/responses`) |

---

## Built-In Tools (entities/)

| Entity | Description |
|---|---|
| `filesystem` | Operating system — file read/write, directory management, shell commands, Python execution (sandboxed) |
| `codebox` | Code orchestration — loop/branch over other tools inside Python scripts, for batch multi-step tasks |
| `operation` | Desktop control (`desktop_act`) and MCP operation registration/semantic execution |
| `screen` | Screen source — screenshots / screen-watching for the vision framework |
| `smart_home` | Smart home — Home Assistant integration, pluggable domains (lights / AC / curtains …) |
| `minimax` | MiniMax — image understanding/generation, speech synthesis/voice management, web search, streaming TTS |
| `dashscope` | Alibaba DashScope — streaming/batch ASR, CosyVoice/Qwen-TTS, voice cloning |
| `audiosync` | Audio source sync — external audio (directories/pushes) into the core audio library |
| `sticker` | Stickers & image perception — collect/semantic-search/send stickers, text-to-image and image-to-image search |
| `vault` | Password book — encrypted credential store / TOTP authenticator / fuzzy search / leak checkup |
| `ssh` | SSH remote management — connections / commands / file transfer |
| `devops` | Ops management — app restarts / frontend builds / project updates |
| `share` | Share & push — file downloads / media rendering / URL pushes via public links |
| `ui` | UI interaction — notifications / modal questions / panel switching / draft injection |
| `ai_desktop` | AI desktop — pluggable ambient context (time / holidays / weather / calendar / quotas) |
| `dify` | Connect existing Dify instances: app & workflow DSL management, invocation, MCP bridging |
| `sillytavern` | SillyTavern management: process lifecycle / git updates / character cards |
| `plugins` | Plugin install / upgrade / removal and marketplace subscriptions |
| `mcp` | MCP server bridging (dynamic registration) |
| `model_control` | Model switching / parameter tuning / local Ollama management |
| `entity_query` | Two-level entity catalog discovery |
| `system` | Environment info — system / Git / log queries |

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────┐     ┌─────────────┐     ┌────────────┐
│  Frontend   │────▶│  Web API     │────▶│ Services │────▶│   Agent     │────▶│   core/    │
│  (React)    │     │  (FastAPI)   │     │          │     │ Mind / LLM  │     │ Registry   │
└─────────────┘     └──────────────┘     └──────────┘     └──────┬──────┘     └────────────┘
                                                                 │
                                              ┌──────────────────┼──────────────────┐
                                              ▼                  ▼                  ▼
                                        ┌──────────┐     ┌────────────┐     ┌────────────┐
                                        │ Channels │     │  Entities  │     │    MCP     │
                                        │ adapters │     │   tools    │     │  bridging  │
                                        └──────────┘     └────────────┘     └────────────┘
```

**Dependency direction (strictly one-way, enforced by import-linter):**

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity        channels/ → agent.channel → core.entity
Forbidden: agent → web | core → business layers | services → web | entities → agent (bridged via _sdk)
```

### Directory Responsibilities

| Directory | Responsibility |
|---|---|
| `core/` | EntityRegistry / config (`ConfigPaths` dynamic paths) / lifecycle / tags / events / gating / redaction / logging / storage-volume registry |
| `agent/mind/` | Thinking loop / PFC / prompt layering / guardrails / compression / thinking sessions |
| `agent/llm/` | LLM clients & manager / classified retries / resilient fallback / capability probing / multimodal adapters / Responses protocol |
| `agent/memory/` | Hybrid semantic memory + notes + optional Cognee knowledge graph |
| `agent/skills/` · `delegation/` · `workflow/` | Self-learning skills / sub-agent scheduling / journaled workflow engine |
| `agent/hooks_llm/` · `hooks/` | LLM hook surface (async, parallel) / user shell hooks (synchronous gatekeepers) |
| `agent/heartbeat/` · `task/` · `planning/` | Heartbeat scheduling / task definitions / goal planning |
| `agent/realtime/` · `voice/` · `tts/` · `audio/` | Realtime call engine / voice sessions / TTS pipeline / audio capability registries (ASR · voiceprint) |
| `agent/vision/` | Vision framework (screenshots / screen-watching / faces / desktop-control sources) |
| `agent/judgment/` · `retrieval/` | Structured judgment (Jev) / web-retrieval providers |
| `agent/approval/` · `security/` | Unified permission & AI review / session tokens / threat scanning |
| `channels/` | Channel adapters (directory auto-discovery + hot-swap) |
| `entities/` | Tool entities (directory auto-discovery + hot-swap, registered via `_sdk`) |
| `services/` · `web/` | Business service layer / FastAPI routers + React frontend |
| `acp/` | Feishu task-delegation ACP integration (standalone top-level package) |
| `deploy/` | Reference sidecar services (`face_server` face recognition / `voicehub` voice hub) |
| `config/` | JSON config · SQLite data · personas · task definitions |
| `tests/` | Layered pytest suites (entity/channel unit tests live inside each module's `<module>/tests/`) |

### Project Layout (Summary)

```
AnelfAgent/
├── launch.py                 # Entry point (thin composition root)
├── start.sh / start.bat      # Start scripts (with crash guardian)
├── restart.sh                # One-shot background restart
├── core/                     # Base framework (zero business dependencies)
├── agent/                    # Agent core (no web dependency)
├── channels/                 # qq / weixin / feishu / telegram / bilibili / acfun / webui / http_api / cli
├── entities/                 # filesystem / codebox / smart_home / minimax / vault / ssh / mcp / ...
├── services/ · web/          # Business services · Web API + React frontend
├── acp/ · deploy/ · scripts/ # Feishu ACP · sidecar references · ops scripts
├── config/                   # Config & data (templates are *.example.json)
└── workspace/                # Runtime workspace (skills / uploads, generated locally)
```

---

## Configuration

| File | Contents | Hot-Reload |
|---|---|---|
| `config/llm_clients.json` | Model providers and models | ✅ (file watcher / Web / AI tools) |
| `config/app_config.json` | Main config (memory / heartbeat / network — hundreds of keys) | Mostly hot-applied when saved from the WebUI config center |
| `config/mcp_servers.json` | MCP server list | ✅ |
| `config/webui.json` | WebUI port / auth / branding | Port changes need a restart |
| `channels/<id>/channel_config.json` | Per-channel config | ✅ |
| `config/tasks/*.json` + `config/heartbeat.json` | Task definitions + scheduling | ✅ |

- **Env overrides**: `ANELF_<KEY>` overrides same-named keys in `app_config.json`; secrets can be externalized with the `${ENV_VAR}` reference syntax
- **Relocation**: `ANELF_CONFIG_DIR` / `ANELF_DATA_DIR` move the config and data directories out of the project tree
- **Config center**: the WebUI `/config` page is fully data-driven — newly registered backend keys appear automatically and hot-apply on save

---

## Development Guide

### Adding a Tool

Create a directory under `entities/` with a `tools.py`; the framework auto-discovers it:

```python
# entities/weather/tools.py
from entities._sdk import tool, entity
import json

entity("weather", "Weather lookup — real-time weather information")

@tool(name="get_weather", group="weather", tags=["web"])
async def get_weather(city: str) -> str:
    """Get real-time weather for a city.

    Args:
        city: City name
    """
    return json.dumps({"city": city, "weather": "sunny", "temp": 25})
```

Conventions: return `str` (JSON), full type annotations + Google docstrings, and route errors through `core.tool_errors`.

Entities obtain `tool_error`, `ErrorCause`, and runtime capabilities through `entities._sdk`. Include `cause`, `retryable`, and an actionable `hint`; result budgets preserve these failure fields. Construct tags with `core.tags.tag_label` and parse them with `etag_all`, instead of assembling strings or separate regexes. Identify users by both channel and ID. Put changing state and recalled facts at the context tail, keeping timestamps and runtime status out of stable instruction prefixes. Tags describe provenance and content, not permissions.

### Adding a Channel

Provide under `channels/{name}/`: `adapter.py` (subclass `BaseChannel`) + `config.py` (expose a `CONFIG_MODEL` pydantic model) + `channel_config.json` + `__init__.py` (export `CHANNEL_CLASS`).

Declare configuration fields only in `CONFIG_MODEL`, write them through `set_channel_config`, and construct conversation scopes with `build_entity_scope`. Keep channel and entity tests in their module's `tests/` directory with unique filenames such as `test_weather_tools.py` to avoid full-suite import collisions.

### Adding a Heartbeat Task

Create a task JSON under `config/tasks/` and bind a schedule on the WebUI heartbeat page; or set `trigger_event` to fire the task from events.

### Packages & Tests

```bash
uv sync                          # Install dependencies
uv run pytest                    # Full suite (unit + credential-free integration)
uv run pytest tests/unit         # Layered unit tests
uv run ruff check .              # Lint
uv run lint-imports              # Dependency-direction contracts
uv run mypy core/                # Type check (strict core layer)
scripts/check.sh                 # Local CI mirror gate (run before pushing)
```

### WebUI Development and Validation

```bash
cd web/frontend
npm ci
npm run dev                    # /webui/; proxies /api to 127.0.0.1:8091
npm run lint
npm run typecheck
npm test
npx playwright install chromium
npm run test:e2e                # Desktop/mobile browsers with isolated API fixtures
npm run build
```

Routes and navigation share the manifest in `src/lib/core-routes.ts`; channel and entity frontends remain auto-discovered. Page shells compose domain editors, shared components own interaction primitives, and `lib/api/` owns requests. TanStack Query manages server data; Zustand manages workbench state. Drafts stay separate from server snapshots. File tabs are identified by both their root directory and relative path.

The workbench is the home screen. Desktop navigation expands on hover or keyboard focus and can be pinned; mobile uses a navigation drawer. Context and thinking traces have independent pages, while the workbench Dock reuses snapshot and delegation components. File references use `lib/file-reference.ts`; `services/file_references.py` handles sandbox resolution and history conversion. Render references within Markdown without splitting paragraphs.

CI (GitHub Actions): a repo-wide lint gate + module-matrix test legs + frontend static checks, component/browser regressions, and builds; doc-only commits are skipped entirely.

Linux and Windows tests upload full logs and JUnit results; browser failures retain screenshots and traces. Unhandled thread exceptions, unraisable exceptions, and leaked SQLite connections remain test failures.

For deeper architectural conventions see [`AGENTS.md`](AGENTS.md) (workspace instructions injected for editors/agents, not a runtime dependency). The current mind architecture is described in [`docs/mind-architecture.md`](docs/mind-architecture.md), the detailed project reference is in [`docs/architecture-reference.md`](docs/architecture-reference.md), stable design decisions are recorded in [`docs/design-decisions.md`](docs/design-decisions.md), and the focused guides cover [`docs/context-cache.md`](docs/context-cache.md) and [`docs/mcp-architecture.md`](docs/mcp-architecture.md).

---

## Tech Stack

| Category | Technologies |
|---|---|
| Runtime | Python 3.11–3.12 · [uv](https://github.com/astral-sh/uv) · FastAPI · Uvicorn · Pydantic v2 |
| LLM | litellm (100+ providers unified) · Chat Completions / Responses dual protocols |
| Storage | aiosqlite (WAL) · FTS5 · sqlite-vec · optional Cognee knowledge graph |
| Voice | FunASR / qwen realtime ASR · MiniMax / CosyVoice / Qwen-TTS / edge-tts · silero VAD / smart_turn |
| External data | asyncpg (PostgreSQL) · aiomysql (MySQL) · asyncssh (SSH) · Home Assistant |
| Protocols | MCP SDK · OneBot v11 · iLink · lark-oapi |
| Frontend | React 18 · TypeScript · Vite 6 · Tailwind CSS 4 · Zustand · TanStack Query · react-i18next (zh/en) |

---

## Sensitive Data Management

Personal config is separated from framework code via `.gitignore`: API keys, tokens, memory stores, and channel secrets never enter the repository — only `*.example.json` templates are committed. Config values support the `${ENV_VAR}` reference syntax to externalize secrets into environment variables.

Backing up personal data (API config / memory data / channel secrets / personas) is the user's own responsibility (e.g. scheduled NAS backups, or exports from the storage-volume panel).

---

## Open Source & Acknowledgements

Released under the **[MIT License](LICENSE)** — stars, issues, and PRs are welcome.

**Repository**: https://github.com/1292917512/AnelfAgent

| Project | Used For | License |
|---|---|---|
| [litellm](https://github.com/BerriAI/litellm) | Unified LLM API | MIT |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | QQ OneBot v11 protocol endpoint | Mixed |
| [lark-oapi](https://github.com/larksuite/oapi-sdk-python) | Feishu / Lark SDK | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) / [MCP](https://modelcontextprotocol.io/) | Web & tool protocols | MIT |

Special thanks to [Nekro Agent](https://github.com/KroMiose/nekro-agent) and [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) for inspiration.

> **Protocol note**: AnelfAgent talks to NapCatQQ over OneBot v11 WebSocket; it neither contains nor modifies NapCat source code. The WeChat channel integrates Tencent's iLink Bot API, with protocol implementation referencing community adapter practices.

### Contributing

1. Fork the repository and create a feature branch
2. Keep the dependency direction and type-annotation conventions (see `AGENTS.md`)
3. Add or update `tests/` for behavior changes
4. Write a clear PR describing motivation and verification

---

## License

[MIT](LICENSE) © 2025–2026 AnelfAgent Contributors

### Tool permissions and AI review

`config/permission_rules.json` is the sole rule source; see `config/permission_rules.example.json`. `allow` executes, `deny` blocks, and `ask` requests Guardian AI review. CRITICAL tools receive review unless an explicit rule applies. Deny rules take priority. User filters require a channel scope; a group ID does not identify the caller.

Unavailable AI review permits autonomous execution with an audit record and a task-local AI notice. It never creates a human approval session or sends channel notifications. Rule loading or evaluation failures block execution. Configure the reviewer and total timeout in `approval/guardian`; the shared deadline covers history retrieval and model calls, and configuration changes reset the circuit breaker.

Human decision endpoints, channel approval commands, session grants, approval-count trust and `approval_policies.json` are removed. Before upgrading, place required rules in `permission_rules.json` or save them in Permissions. An installation with only the retired file reports a configuration error and blocks tool execution instead of silently dropping restrictions. Existing audit records remain readable.
