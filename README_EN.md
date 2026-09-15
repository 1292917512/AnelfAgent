# AnelfAgent

**v0.3** · A unified agent framework — Autonomous Reasoning · Semantic Memory · Tool Orchestration · Multimodal Generation · Multi-Channel Communication

[简体中文](README.md) | **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent is an open-source AI agent runtime for individuals and teams. It ships with an autonomous decision engine, hybrid semantic memory, self-learning skills, sub-agent delegation, MCP tool bridging, and multi-platform channel adapters. It covers multimodal generation across text, image, speech, video, and music, and provides a modern WebUI for the full lifecycle of configuration, conversation, and operations.

> This repository is the **0.3 stable baseline**: the architecture and capabilities are well established, suitable for self-hosted deployment and downstream extension.

---

## Why AnelfAgent

| Capability | Description |
|---|---|
| **Entity-Driven** | Tools / models / channels / MCP / storage are all registered in the `EntityRegistry`, with two-level capability discovery |
| **Tag Routing** | `[key:value]` tags run through message metadata and tool injection, so the AI always gets a "just enough" toolset |
| **Two-Layer Thinking** | Meta-decision picks the action type → `think_loop` executes multi-round tool orchestration |
| **Robust Guardrails** | Tool guardrails / classified retries / candidate-chain fallback / context compression / result budgets / session tokens / threat scanning — programmatic safety nets |
| **Augmented Memory** | SQLite + FTS5 + Embedding hybrid recall, with optional Cognee knowledge-graph federation |
| **Multimodal Generation** | Unified adapters for image / speech / video / music (OpenAI · MiniMax · SiliconFlow · DashScope, etc.) |
| **Continuous Evolution** | Self-learning skill loop + heartbeat task scheduling + goal planning |
| **Secure & Controllable** | Unified permission engine (allow / ask / deny) + channel-based approval + WebUI auth + automatic secret redaction |
| **Self-Ops** | SSH remote management / memory backup / project updates / file sharing — the AI can manage its own deployment |
| **Multi-Channel** | QQ / Feishu (Lark) / WeChat / Telegram / Bilibili / Acfun / WebUI / HTTP / CLI + an OpenAI-compatible Responses API |

---

## Core Capabilities

### Entity Registration & Tool Gating

Every capability is registered as an entity, organized by groups and tags; the AI discovers tools through a catalog → group two-level flow.

```python
from entities._sdk import tool, entity

entity("weather", "Weather lookup — real-time weather information")

@tool(name="get_weather", group="weather", tags=["web"])
async def get_weather(city: str) -> str:
    """Get real-time weather for a city.

    Args:
        city: City name
    """
    return json.dumps({"city": city, "weather": "sunny", "temp": 25})
```

After **PFC multi-source merging**, two gating passes keep the schema lean:

| Source | Description |
|---|---|
| `always` | Always-on tools (`end_reply` / `send_message`, etc.) |
| `mcp:*` | MCP server tools |
| `channel` | Capability match of the current channel |
| `tag_match` | Activated by message tags (e.g. `media:image`) |
| `hot_recall` | Top-N frequently used tools |
| `discovered` / `activated` | Dynamic discovery and sleeping-group wake-up |

- **check_fn gating**: environment precondition checks (TTL cache + transient-failure grace); tools that fail the check never enter the schema
- **Sleep / activate**: `allow_sleep` tools only show a brief by default; the AI calls `activate_tool_group` to wake them on demand

### Autonomous Mind

```
Message queued → PFC gathers situation → meta-decision → execution
  → memory recall / skill injection → think_loop (multi-round LLM + tools)
  → end_reply → done
```

| Decision Type | Purpose |
|---|---|
| `REPLY` | Reply to messages |
| `REFLECT` | Heartbeat / reflection tasks |
| `REMEMBER` | Proactive memorization |
| `PROACTIVE` | Proactive outreach |
| `TOOL_ACTION` | Autonomous tool operations |
| `PLAN` | Goal planning |

System prompts are layered by change frequency to hit Anthropic / OpenAI **prefix caching**:

```
stable (persona + tool prompts, frozen within a conversation)
  → context (notes, low-frequency)
  → volatile (recall + skills + security markers, per turn)
```

### Robustness & Security

| Mechanism | Role |
|---|---|
| Tool guardrails | Exact-failure repeats / consecutive failures / no-progress loops → warn / block / halt |
| Error classification + adaptive retry | Rate limits, timeouts, context overflow and more drive backoff and model fallback |
| Candidate-chain fallback (resilience) | `chat_with_fallback` advances along candidate model chains; context overflow fails fast and larger-window candidates are tried directly, avoiding redundant compression |
| Model capability probing | Empirically tests tools / vision support and auto-corrects model capability profiles |
| Context compression | Overflow detection → keep head & tail + LLM summary, sustaining long conversations |
| Result budgets | Dynamically truncates tool results based on model window (per-item / per-round ratios) |
| Session tokens | One-time tokens mark trusted history, preventing injection forgery |
| Threat scanning + redaction | Intercepts tool results / memory writes; API keys, tokens and passwords are automatically masked |
| **Unified permission engine** | `tool_name(arg-glob)` + allow / ask / deny, global and per-channel scopes; high-risk operations require human approval via channel or WebUI |

### Model Management (LLMManager)

- **Two-level structure**: Provider → Model, organized by capability type (chat / tools / vision / embedding …)
- **Enable switches**: disabled models are automatically excluded from selection / fallback / execution paths, with persisted state
- **Sub-agent profiles** (`sub_agents`): name → ordered model candidate pool + execution facets (dedicated instructions / tool selectors / structured output contracts); built-in difficulty tiers 1–3 are pure model-pool syntactic sugar, with automatic downgrade when a tier is unavailable
- **Config-driven thinking contracts**: each model declares its reasoning parameter mapping in config (the `thinking` field) — zero model-name special-casing in code
- **Proxy support**: `HTTP(S)_PROXY` environment leases + deep-copyable proxy clients
- **Protocol adaptation**: both Chat Completions and Responses protocols (`agent/llm/responses`), unified through litellm

### Multimodal Generation

Unified adapters hide platform differences; the AI produces multimedia content directly through the `media` / `minimax` tools:

| Modality | Adapter | Platforms |
|---|---|---|
| Image | `ImageGenAdapter` | OpenAI · DashScope · SiliconFlow · MiniMax |
| Speech | `SpeechAdapter` | OpenAI · MiniMax (incl. voice cloning) |
| Video | `VideoGenAdapter` | OpenAI · MiniMax (V1 / V2, async task polling) |
| Music | `MusicAdapter` | MiniMax |

The companion **sticker entity** (`sticker`) supports collecting / searching / sending stickers, text-to-image and image-to-image search; candidates are injected via the multimodal convention so vision models can "see with their own eyes" before choosing.

### Hybrid Semantic Memory

Hybrid scoring over Embedding + FTS5 + tag matching + time decay; memory types cover entity profiles, knowledge, events, and permanent memories, plus Markdown notes. The storage layer is split into `memory/store/` (connection / retrieval / file index / queue), decoupled from upper domain logic.

- **Memory rules document**: write routing / tag discipline / query routing consolidated into a single system-level prompt document (`config/memory_rules.md`), editable from the WebUI memory page
- **Main-tag memory** (`main:hub`): a permanent memory pinned at the top of every reply cycle, maintained by the AI as a whole and self-healed by heartbeat
- **Forgetting governance**: importance relaxation + retrieval-practice effect + archive / tombstone fallback recall (restorable via `restore_memory`)
- **Graph governance**: edge-strength decay / weak-edge forgetting + an AI curation agenda (facts belong to the system, decisions belong to the AI)

Optionally enable **Cognee** knowledge-graph projection and federated recall (`config/cognee.json` / WebUI memory settings), coexisting with the authoritative SQLite store and degrading gracefully on failure.

### Self-Learning Skills & Sub-Agents

- **Skill loop**: post-conversation background review via the LLM hooks plane → distilled into `workspace/skills/SKILL.md` → semantic-match injection → heartbeat curation (downgrade / archive)
- **Sub-agents**: `delegate_task` supports parallel fan-out, background mode and independent iteration budgets with profile-based model selection; `follow_up_agent` resumes losslessly from the full transcript; progress streams / usage attribution / a Web panel provide end-to-end observability

### LLM Hooks Plane

A unified registration primitive for "deriving context-carrying asynchronous LLM work at LLM thinking boundaries", parallel to the heartbeat / task system: multiple hooks can attach to the same event (`after_reply` / `delegation_resolved` / `llm_end` …) and run concurrently, each with independent governance (concurrency / cooldown / debounce). Skill background review, task event triggers, and entity hooks are all built on it.

### Heartbeat & Tasks

Task content (`config/tasks/*.json`) is separated from scheduling (`config/heartbeat.json`):

| Trigger Mode | Description |
|---|---|
| `heartbeat` | Runs every N heartbeats |
| `scheduled` | At specified times each day (per-slot independent dedup) |
| `idle` | Fires after N consecutive beats without thinking activity (exactly one globally) |
| `manual` | Manual / AI-initiated only |
| `trigger_event` | Event-triggered (via the LLM hooks plane, orthogonal to scheduling) |

Each heartbeat also runs built-in maintenance: entity profiling, memory health checks, skill curation, log compaction, idle conversation folding, and more.

### Plugins & Hot-Plug

- **Plugin system** (`entities/plugins`): a plugin is a directory package of manifest + skills/ + .mcp.json + tools.py, with marketplace subscriptions (local directory or git repo); the AI can install / upgrade / remove plugins autonomously
- **Module hot-plug**: adding or removing entity / channel directories is reconciled automatically via directory watching (new directories register instantly, removed ones are fully torn down); manual hot-sync is also available from the WebUI

### Self-Ops & External Data

| Entity / Service | Capability |
|---|---|
| **SSH remote management** | Connection management / command execution / file transfer, persistent remote working-directory tracking, default connections and a WebUI panel |
| **DevOps** | Sync memory to a private GitHub repo / pull project updates / restart the app (with restart handoff messages) |
| **File sharing** | Generate externally downloadable links for workspace files and manage their lifecycle |
| **External SQL sources** | Read-only PostgreSQL / MySQL connection registry (`config/db_connections.json`), browsable and queryable from the WebUI data page |
| **Storage volumes** | All persistent data is registered as storage volumes (8 volumes), supporting online hot backup / restore / migration / SQL export-import, operated visually from the Web data page |
| **Data directory migration** | Online hot-backup copy + verification + `data_root` switching |

### Multi-Channel Adapters

Directory auto-discovery; a new channel only needs `channels/{name}/adapter.py` + `channel_config.json`:

| Platform | Highlights |
|---|---|
| **QQ** | OneBot v11 + NapCat (direct connection) |
| **Feishu (Lark)** | WebSocket event-driven |
| **WeChat** | iLink Bot API, scan-to-login, no public webhook required (see [`channels/weixin/README.md`](channels/weixin/README.md)) |
| **Telegram** | Bot API long polling |
| **Bilibili / Acfun** | Danmaku and private-message integration |
| **WebUI** | SSE push; three-pane conversation workbench (file tree / conversation flow / Dock) |
| **HTTP API** | Synchronous request-response |
| **CLI** | Terminal debugging |
| **Responses API** | OpenAI-compatible gateway (`/v1/responses`) — serve AnelfAgent itself as a model endpoint |

The WebUI workbench supports the AI **driving the interface in reverse** (`ui_notify` / `ui_ask` / `ui_open_panel` etc. → SSE `ui_command`).

### MCP Bridging

Supports stdio / SSE / Streamable HTTP; background async connections, tools auto-registered as entities with hot reload; tool-list change notifications are hot-synced, and image results are persisted to disk and injected into vision models.

---

## Tech Stack

| Category | Technology |
|---|---|
| Runtime | Python 3.11–3.12 · [uv](https://github.com/astral-sh/uv) · FastAPI · Uvicorn · Pydantic v2 |
| LLM | litellm (100+ unified providers) · Chat Completions / Responses dual protocols |
| Storage | aiosqlite (WAL) · FTS5 · Embedding (sqlite-vec) · optional Cognee |
| External data | asyncpg (PostgreSQL) · aiomysql (MySQL) · asyncssh (SSH) |
| Document parsing | pypdf · python-docx · tiktoken |
| Protocol | MCP SDK |
| Frontend | React 18 · TypeScript · Vite 6 · Tailwind CSS 4 · Zustand · TanStack Query |
| i18n | react-i18next (Chinese / English) |

---

## Quick Start

### Requirements

- Python **3.11 ~ 3.12**
- Node.js **18+** (for building the frontend)
- [uv](https://github.com/astral-sh/uv) (recommended)

### Install & Run

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

# Create configs from templates and fill in your API keys
cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json

# Install dependencies
uv sync

# Build the frontend (optional; the API runs fine without it)
cd web/frontend && npm install && npm run build && cd ../..

# Start
./start.sh                 # macOS / Linux
start.bat                  # Windows
uv run python launch.py    # run directly
uv run python launch.py --no-webui   # agent only, no WebUI
```

Then open: **http://127.0.0.1:8092/webui/**

### Connect a Channel (example)

```bash
# WeChat: WebUI → Channel Management → scan to log in (recommended)
# or: uv run python scripts/weixin_setup.py
```

Environment variables can override config values via `ANELF_<KEY>`; secrets can be externalized with the `${ENV_VAR}` reference syntax.

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
                                        │ Adapters │     │   Tools    │     │   Bridge   │
                                        └──────────┘     └────────────┘     └────────────┘
```

**Dependency direction (strictly one-way):**

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity
channels/ → agent.channel → core.entity

agent.mind → agent.memory / heartbeat / task / planning
agent.heartbeat → agent.task + memory + mind (scheduled execution)
agent.planning → agent.memory

Forbidden: agent → web | core → agent | services → web | entities → agent (bridged via _sdk)
```

Enforced mechanically by import-linter (`uv run lint-imports`, CI red/green gate).

### Directory Responsibilities

| Directory | Responsibility |
|---|---|
| `core/` | EntityRegistry / config (`ConfigPaths` dynamic paths) / lifecycle / tags / events / gating / redaction / logging / storage-volume registry |
| `agent/mind/` | Thinking loop / PFC / prompt layering / guardrails / compression / thinking sessions |
| `agent/llm/` | LLM client & manager / classified retries / resilient fallback / capability probing / multimodal adapters / Responses protocol |
| `agent/memory/` | Hybrid semantic memory (`store/` layer) + notes + optional Cognee |
| `agent/skills/` | Skill storage / matching / background review / curation |
| `agent/delegation/` | Sub-agent scheduling (profiles / parallel fan-out / resume / run journal) |
| `agent/hooks_llm/` | LLM hooks plane (event-driven async LLM work registration primitive; review / task events / entity hooks launched in parallel) |
| `agent/approval/` | Unified permissions and approval gates |
| `agent/security/` | Session tokens / threat scanning |
| `agent/heartbeat/` · `task/` · `planning/` | Heartbeat scheduling / task definitions / goal planning |
| `agent/messages/` | Conversation scope building & parsing / persona presets |
| `agent/channel/` · `runtime/` · `storage/` | Channel management / startup assembly / storage routing & migration |
| `channels/` | Channel adapters (directory auto-discovery + hot-plug) |
| `entities/` | Tool entities (directory auto-discovery + hot-plug, registered via `_sdk`) |
| `services/` | Business facades for the Web API |
| `web/` | FastAPI routers + React frontend |
| `config/` | JSON configs · SQLite data · personas · task definitions |
| `tests/` | Layered pytest suites (`unit/` + `integration/`; entity/channel unit tests live in each module's `<module>/tests/` and move with the module) |

### Built-in Entities (entities/)

| Entity | Description |
|---|---|
| `filesystem` | File read/write / directory tree / search (sandboxed) |
| `web` | Search / scraping / web content extraction |
| `media` | Image recognition / speech transcription & synthesis, and other multimedia processing |
| `minimax` | MiniMax speech / image / voice cloning |
| `sticker` | Sticker collection / search / sending, text-to-image and image-to-image search |
| `ui` | UI interaction (`ui_notify` / `ui_ask` / `ui_open_panel`, etc.) |
| `ssh` | SSH connection management / command execution / file transfer |
| `devops` | Memory backup / project updates / app restart |
| `share` | File-sharing link management |
| `vault` | Password vault — encrypted credentials / TOTP authenticator / fuzzy search / breach check |
| `voiceprint` | Voiceprint library — speaker recognition & profile management, semantic search over transcripts |
| `ai_desktop` | AI desktop — pluggable injection of environment info such as time / holidays / weather / calendar / subscription quotas |
| `dify` | Connect to an existing Dify instance: app & workflow DSL management, run invocation, MCP bridging |
| `sillytavern` | Manage a local SillyTavern instance: process lifecycle / git updates / character-card management |
| `plugins` | Plugin install / upgrade / removal and marketplace subscriptions |
| `mcp` | MCP server bridging (dynamically registered) |
| `entity_query` | Two-level entity catalog discovery |
| `model_control` | Model switching / parameter tuning / Ollama management |
| `system` | System info / Python environment / Git / log queries |

### Project Layout (summary)

```
AnelfAgent/
├── launch.py                 # Entry point
├── core/                     # Foundation framework (zero business dependencies)
├── agent/
│   ├── mind/                 # Thinking loop / PFC / prompt layering / guardrails / compression / sessions
│   ├── llm/                  # LLM management / resilient fallback / probing / multimodal adapters / Responses
│   ├── memory/               # Hybrid semantic memory (store/) + notes + Cognee
│   ├── skills/ · delegation/ · hooks_llm/ · approval/ · security/
│   ├── heartbeat/ · task/ · planning/ · messages/
│   ├── channel/ · runtime/ · storage/
├── channels/                 # qq / feishu / weixin / telegram / bilibili / acfun / webui / http_api / cli
├── entities/                 # filesystem / web / media / vault / voiceprint / ai_desktop / dify / plugins / mcp / ...
├── services/ · web/ · config/ · scripts/ · tests/
└── workspace/                # Runtime workspace (uploads / skills, generated locally)
```

---

## Development Guide

### Adding a Tool

Create a directory under `entities/` and implement `tools.py`; the framework discovers it automatically:

```python
# entities/weather/tools.py
from entities._sdk import tool, entity
import json

entity("weather", "Weather lookup — real-time weather information")

@tool(
    name="get_weather",
    group="weather",
    tags=["web"],
    # Optional: gating and sleep
    # check_fn=lambda: True,
    # allow_sleep=True, sleep_brief="Weather lookup",
)
async def get_weather(city: str) -> str:
    """Get real-time weather for a city.

    Args:
        city: City name
    """
    return json.dumps({"city": city, "weather": "sunny", "temp": 25})
```

Conventions: return `str` (JSON), full type annotations + Google-style docstrings, catch exceptions internally, and route errors through `core.tool_errors` (imported via `_sdk` in entities).

When adding a new **group key**, also update: backend registration, `i18n/locales/{zh,en}/tools.json`, and the group ordering in `core/entity.py` (an entity can override it via `entity_manifest(order=)`).

### Adding a Heartbeat Task

Create a task JSON under `config/tasks/`, then bind a schedule on the WebUI heartbeat page; you can also set `trigger_event` to make the task event-triggered.

### Adding a Channel

Provide under `channels/{name}/`:

- `adapter.py` — subclass `BaseChannel`, implementing `channel_id` / `display_name` / `capabilities` / `start` / `stop` / `send_text`
- `config.py` — expose `CONFIG_MODEL` (the pydantic model is the single source of config declaration)
- `channel_config.json` (use `.example.json` as a template)
- `__init__.py` — export `CHANNEL_CLASS`

### Permission Rules

Current format: `config/permission_rules.json` (preferred); legacy `approval_policies.json` is auto-converted on load. Rules support hot reload. High-risk tools can be set to `ask`, confirmed via channel messages or the WebUI approval page.

### Packages & Testing

```bash
uv sync                          # Install dependencies (incl. Cognee)
uv run pytest                    # Full test run (unit + credential-free integration)
uv run pytest tests/unit         # Layered unit tests (core/agent/services/web, fast)
uv run pytest entities/minimax/tests # Single-module tests (entity/channel tests live in each module's tests/)
uv run pytest -m integration     # Integration tests only (credential-required cases skip automatically)
uv run ruff check .              # Lint
uv run lint-imports              # Dependency-direction contract check
uv run mypy core/                # Type checking (strict core layer)
uv add <package>                 # Add a dependency (do NOT pip install into the uv venv)

scripts/check.sh                 # Local CI mirror gate: one-shot verification identical to CI (run before push)
scripts/check.sh --fast          # Static gates only (ruff + lint-imports + mypy, three platforms)
```

CI (GitHub Actions, `.github/workflows/ci.yml`): on push/PR, changes are first classified by path — the `lint` job runs repo-wide static gates (ruff → import-linter → mypy core on three platforms), the `tests` job fans out into a dynamic per-module matrix (entity/channel changes only run that module's `tests/` suite; core changes trigger the full matrix), and the `frontend` job runs `npm run lint` + `npm run build`. Documentation-only commits are skipped entirely.

For deeper architectural conventions see [`AGENTS.md`](AGENTS.md) at the repo root (workspace instructions for editors / agents, not a runtime dependency).

---

## Sensitive Data Management

Personal configuration is separated from framework code via `.gitignore`: API keys, tokens, memory databases, heartbeat counters, channel secrets, etc. never enter the repository — only `*.example.json` templates are kept. Config values support the `${ENV_VAR}` reference syntax to externalize secrets into environment variables.

Backing up personal data (API configs / heartbeat & tasks / memory data / channel secrets / personas) is the user's own responsibility (e.g. periodic NAS backups, or exports via the storage-volume panel).

---

## Open Source & Acknowledgments

Released under the **[MIT License](LICENSE)** — stars, issues and PRs are welcome.

**Repository**: https://github.com/1292917512/AnelfAgent

AnelfAgent's multi-platform capabilities build upon these excellent open-source projects:

| Project | Purpose | License |
|---|---|---|
| [litellm](https://github.com/BerriAI/litellm) | Unified LLM API | MIT |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | QQ OneBot v11 protocol endpoint | Mixed |
| [lark-oapi](https://github.com/larksuite/oapi-sdk-python) | Feishu / Lark SDK | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) / [MCP](https://modelcontextprotocol.io/) | Web & tool protocols | MIT |

Special thanks to [Nekro Agent](https://github.com/KroMiose/nekro-agent) and [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) for the reference and inspiration.

> **License note**: AnelfAgent communicates with NapCatQQ over OneBot v11 WebSocket and does not include or modify NapCat source code. The WeChat channel integrates with Tencent's iLink Bot API, with protocol implementation informed by community adapter practices.

### Contributing

1. Fork the repository and create a feature branch
2. Follow the dependency-direction and type-annotation conventions (see `AGENTS.md`)
3. Add or update `tests/` for behavioral changes
4. Submit a clear PR explaining motivation and verification

Bug reports, design discussions and feature proposals are welcome in Issues.

---

## License

[MIT](LICENSE) © 2025–2026 AnelfAgent Contributors
