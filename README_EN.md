# AnelfAgent

**v0.4.0** · A personally maintained, self-hosted runtime for autonomous AI

**Continuous Memory · Cross-Channel Presence · Proactive Work · Growing Skills · AI Self-Maintenance · Module Hot Reload**

**[🌐 Official Website · anelf.cn](https://anelf.cn)**

[简体中文](README.md) | **English**

[![CI](https://github.com/1292917512/AnelfAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/1292917512/AnelfAgent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent lets you develop a lasting AI presence on your own devices: **an agent with continuous memory and a persona, an understanding of different people across channels, the ability to advance work proactively, and permission to learn skills, modify extensions, and verify updates within the boundaries you set.**

QQ, WeChat, Feishu, Telegram, games, and the Web workspace are entry points to the same mind, memory, skills, and tools. Conversations, heartbeats, background events, sub-agents, and workflows form a runtime for ongoing work. The WebUI shows what the agent is doing, its supporting evidence, and whether operations actually finished.

This is a **personally maintained, continuously evolving MIT open-source project**. You control the code, deployment, model choices, permissions, and data. Everyday configuration and operations are available through both the WebUI and AI tools. Self-improvement takes the form of traceable memories, reusable skills, and verifiable module updates.

## Contents

- [Quick Start](#quick-start)
- [Autonomy and AI Self-Maintenance](#autonomy-and-ai-self-maintenance)
- [Memory and Cross-Channel Continuity](#memory-and-cross-channel-continuity)
- [Capabilities and Workspace](#capabilities-and-workspace)
- [Channels and Tool Ecosystem](#channels-and-tool-ecosystem)
- [Architecture and Development](#architecture-and-development)
- [Personal Deployment and Open-Source Collaboration](#personal-deployment-and-open-source-collaboration)

## Quick Start

### Requirements and Installation

| Dependency | Version / purpose |
|---|---|
| Python | **3.11–3.12**, the main runtime |
| Node.js | **24** (see `.nvmrc`; minimum 22.12), for WebUI builds and development |
| [uv](https://github.com/astral-sh/uv) | Python environments, dependencies, and lockfile management |
| Models | A working chat model; configure an Embedding model for semantic retrieval and add speech or generation models as needed |

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json

uv sync --locked
cd web/frontend
npm ci
npm run build
cd ../..
```

Edit models and credentials in `config/llm_clients.json`, or start with the templates and configure them on the WebUI Models page. Calls go through LiteLLM, with cloud providers or compatible local model services.

### Launch and First Configuration

```bash
./start.sh                 # macOS / Linux
```

On Windows, run from the project root:

```powershell
.\start.bat
```

Open **[http://127.0.0.1:8092/webui/](http://127.0.0.1:8092/webui/)**. Configure models and a persona, start a conversation, and connect platforms from Channels. Core-only and terminal modes are also available:

```bash
uv run python launch.py --no-webui
uv run python -m channels.cli
```

Cognee dependencies ship with the project and its integration is enabled by default; configure its chat and embedding models to use it. Native memory retrieval remains available when graph enhancement is unavailable. Web retrieval supports built-in sources, commercial providers, and separately deployed SearXNG / Crawl4AI. Local services can start in the background when Docker is available; see the [retrieval deployment guide](deploy/retrieval/README.md). External services and speech or vision runtimes are configured as needed.

### Everyday Operations and Updates

- `start.sh` / `start.bat` supervise crashes and stop restarting after the consecutive fatal-crash limit.
- An instance lock refuses a second takeover of the same checkout, protecting active data writes. Stop the old instance normally before starting another.
- Use `Ctrl+C` for a normal foreground shutdown. On macOS / Linux, `./stop.sh` and `./restart.sh` provide ordered shutdown and background restart.
- AI restart tools require supervision by the start scripts. Instances launched directly with `launch.py` need a manual restart. A restart handoff note can return ongoing work to the AI after recovery.
- After pulling a release, run `uv sync --locked`. When frontend resources change, run `npm ci` and `npm run build`. Module reloads and core upgrades follow different paths, described below.

## Autonomy and AI Self-Maintenance

### From Responding to Ongoing Work

```text
Conversations / heartbeats / platform events
                    ↓
Messages, memory, tasks, goals → decision → tools / delegation / workflows
                    ↑                                  ↓
              Next action ← actual receipts, progress, reflections, skills
```

The main Mind serializes top-level work through one scheduling worker and maintains a shared view across channels. Isolated sub-agents, LLM hooks, and workflow executions can run in parallel. User messages, scheduled work, and background completion events share the runtime; pending work survives busy periods, and maintenance yields at stage boundaries.

| Mechanism | How it supports autonomous work |
|---|---|
| Mind decisions | Replies, reflection, memory, proactive outreach, tool actions, and planning; ordinary conversations can take a direct reply path |
| Heartbeats and tasks | Every N beats, daily times, idle, manual, and event triggers; task content is separate from scheduling, with execution history for deduplication and verification |
| Goal planning | Persistent goals, steps, and progress; completion evidence closes work and clears terminal goals |
| Growing skills | Background review of actual operations produces reusable `SKILL.md` files; keyword and semantic matching suggest candidates, and the AI decides what to read, use, merge, or restore |
| Sub-agents | Profile-based model selection, parallel delegation, continuation, and steer / after instructions, with progress and usage attribution |
| Workflows | Saved DAG definitions, ask / tool steps, journaled recovery, reuse across revisions, and bounded repair-and-rerun |
| LLM hooks | Background work at reply and delegation event boundaries, with concurrency, cooldown, debounce, and recursion controls |

**The system records facts, the AI makes decisions, and actual receipts establish outcomes.** A matched skill has not necessarily been executed, an accepted operation has not necessarily finished, and generated text does not establish message delivery.

### AI Can Maintain Its Own Extensions

Within authorized source paths and tool permissions, the AI can complete this loop:

1. Read existing interfaces, memories, skills, and code; modify a tool entity or channel.
2. Run relevant checks, then load the implementation through a targeted reload or directory synchronization.
3. Read current tool names and groups from the receipt, call `list_entity_methods` for updated parameters, and invoke the tool to verify its behavior.
4. If loading fails, inspect rollback results, repair the source, and retry. Dependency, core, or state-module changes use installation and normal restart paths.
5. Save reusable methods as skills and retain useful memories or handoff notes for future work.

AI tools, Web management, and directory watching reuse the existing registry, lifecycle, permissions, and synchronization mechanisms.

| AI tool | Purpose |
|---|---|
| `reload_entity(entity_name)` | Reload one entity entry point by its actual directory name under `entities/` |
| `reload_channel(channel_id)` | Reload one channel; a running connection briefly disconnects and reconnects |
| `sync_modules(target="all", reload_existing=False)` | Reconcile additions/removals in `entities` / `channels` / `all`; explicitly pass `True` to refresh healthy existing modules |
| `update_project` / `update_and_restart` | Pull code, or update, build, and schedule a restart; conflicts, a dirty checkout, and build failures are reported |
| `restart_app(message)` | Restart in order and leave continuation information for the recovered agent |

All three module synchronization tools are **CRITICAL** and pass through the unified permission entry point. Channel operations also require the sensitive-operation switch. Rules can match targets such as `reload_entity(filesystem)`, `reload_channel(qq)`, and `sync_modules(entities)`. Source writes still require the existing filesystem sandbox and permissions; reloading does not expand write access.

### Hot Reload Coverage and Boundaries

**Tool entities and channel modules share hot discovery, synchronization, and targeted reload support. Component interfaces join the frontend through module contributions.** Different changes take effect as follows:

| Content | Update path and boundary |
|---|---|
| Entity / channel directory additions and removals | Automatic reconciliation through optional directory watching, or manual synchronization from Web or AI |
| One entity's code | Refresh `tools.py` / `router.py`, preserving state resource identity; cached helpers, connection managers, and process-level bridges require a restart |
| One channel's code | Refresh adapter, config model, and private Python dependencies; preserve config, tool switches, and learned routes; disabled channels stay stopped |
| Models, MCP, tasks, and most configuration | Their configuration watchers and Web / AI management interfaces; connection-related settings may require component reconnection |
| Entity / channel / core frontend | Vite Fast Refresh in development; manifest and locale changes trigger rediscovery and page reload; production pages need rebuilt assets and a browser refresh |
| Core framework, shared dependencies, separate runtimes | Normal restart or the owning module's installation / upgrade interface; uv manages Python dependencies, and reload does not install packages |

Loading has concurrency protection and module-level rollback. Failures, busy states, and partial completion are not reported as full success. **Batch synchronization is transactional per module**: success in one module is not undone because another fails. Failed channel cleanup retains resource ownership and reports the failure, avoiding a second concurrent connection. Rollback covers managed registrations and runtime resources, not arbitrary file, network, or external-system side effects.

## Memory and Cross-Channel Continuity

### One Mind, Multiple Places

Channels share the agent's memory, skills, and persona while retaining distinct conversation, user, group, and sub-session identifiers. Identity combines **channel + ID**; matching nicknames or numeric IDs do not establish the same person. Existing alias associations can support cross-channel history recognition, with separate voiceprint bindings for speech identity.

Cross-channel awareness connects related experiences and unfinished work. Outbound delivery names its channel and conversation explicitly and records actual delivery receipts. Switching sessions does not implicitly change the current call's recipient.

Memories belong to the same AI individual. Relevant material can be recalled across channels with subjects, provenance, dates, and privacy labels. Memory instructions govern private disclosure, and person-specific reflections have scope visibility restrictions. **Shared memory is not database isolation between untrusted tenants.** Configure access, permissions, and external delivery to fit your personal deployment.

### Each Memory System Has a Role

| Content | Authoritative entry point |
|---|---|
| Facts, events, reflections, permanent rules | Long-term memory: `memorize` / `recall` / `get_memory` |
| Personal preferences and interaction style | Entity profiles, maintained per person |
| Relationships between entities | Native relationship graph, with evidence and source-memory pointers |
| Reusable methods and experience | Skill directories and `SKILL.md` under `workspace/skills/` |
| Working notes and navigation | Markdown notes and `mem:ID` pointers, with protected system-managed blocks |
| Temporary reminders and goal progress | Short-term memory / tasks / planning, closed through their own lifecycles |
| Graph retrieval enhancement | Cognee-derived projections; native stores remain authoritative |

Store information according to its responsibility and reference it elsewhere by pointer, reducing contradictory copies.

### Retrieval, Correction, and Long-Term Governance

- **Hybrid recall**: SQLite + FTS5 + Embedding, with tags, time, and relationships. sqlite-vec performs exact KNN, with NumPy scoring fallback when the extension is unavailable; scanning cost still grows with data size.
- **Levels and budgets**: `off / tags / fast / deep`, selectable by channel and turn. Query planning, multiple retrieval lanes, and graph enhancement share preparation budgets; completed evidence survives timeouts, and the AI can request deeper recall.
- **Traceable material**: distinguish subjects from actual sources, and event dates from recording times and source windows. File hits retain paths and excerpt line numbers; graph synthesis is identified separately from original statements.
- **Continuous capture**: per-conversation message consumption with recoverable checkpoints. Deduplication distinguishes new, covered, and evolving facts; version checks prevent stale judgments from overwriting newer revisions.
- **Correction and forgetting**: update original entries, incorporate evidence feedback, decay importance, archive restorably, and retain tombstone summaries. Merges preserve ID redirects and lineage. Semantic similarity identifies candidates rather than proving identity.
- **Persona growth**: user-defined personas are separate from evidence-driven self-profiles. Reflections await verification and promotion; AI self-reports do not count as user confirmation.
- **Graph protection**: native results survive Cognee enhancement failures. Construction and maintenance use managed locks, checkpoints, publication, and recovery; embedding-model changes require vector-identity checks.

The Memory page exposes long-term entries, notes, relationships, recall testing, tag governance, and index maintenance. See [memory](docs/memory-system.md) and [skills](docs/skills.md).

## Capabilities and Workspace

### Models, Multimodality, and the Outside World

| Capability | Implementation and use |
|---|---|
| Model management | Provider → Model, selected by chat, tools, vision, embedding, and other capabilities; candidate fallback, Chat Completions / Responses protocols, and configuration-driven thinking parameters |
| Structured judgment | Choice / Score / Noul, with native Jev and ordinary model channels for deduplication and optional-work assessment |
| Realtime voice | Full duplex, endpoint detection, interruption, ASR, and streaming TTS; voiceprints, audio library, and channel identity integration |
| Vision and desktop | Image / video understanding, screenshots, screen watching, face recognition, and `desktop_act`, with post-action visual verification |
| Multimodal generation | Shared routing for images, speech, video, and music through configured models and components |
| Web retrieval | Search / reader provider matrix: built-in sources, BigModel, DeepSeek, SearXNG / Crawl4AI, and others |
| MCP | stdio / SSE / Streamable HTTP, OAuth, reconnection, and incremental tool-list synchronization; image results can reach vision models |
| Open interfaces | Bidirectional HTTP portal, OpenAI-compatible `/v1/responses`, and Feishu task delegation through ACP |

Context is layered by change frequency: stable instructions and history retain their prefix, while recall, status, and execution feedback go at the dynamic tail. Tool catalogs, groups, and capability gates support discovery on demand. Cache diagnostics distinguish client bytes, provider caching, and usage accounting; costs and availability depend on selected models and services.

### WebUI Workspace

- **Conversation and files**: file tree, editors, conversation, and a functional Dock; attach files or selections as context while keeping drafts separate from server snapshots.
- **Progress and completion**: linked main turns, sub-agents, model usage, and tool receipts; compact sub-agent summaries with details on demand, and distinct running and finished states.
- **Turn file changes**: a sidebar groups file operations from retained activity and opens diffs. It covers observed operations rather than a full Git diff; arbitrary external-command edits may be absent, and undo is not offered without reliable snapshots.
- **Long-running sessions**: paged history, backward loading, bounded recent-message windows, and conversation caches; realtime reconnection and snapshot catch-up avoid treating silence as completion.
- **Consistent interaction**: source sections, combined search, horizontally scrollable tabs, shared dialogs and secondary details, aligned actions, and consistent motion across themes, mobile layouts, and reduced-motion preferences.
- **AI-driven UI**: notifications, questions, drafts, and panel navigation; active, background, idle, and offline page states. Visibility does not prove reading or authorization.
- **Operations**: models, channels, tool sources, tasks, memory, permission audit, component credentials, data volumes, and services, with module-owned interfaces mounted through contributions.

## Channels and Tool Ecosystem

### Multi-Platform Channels

| Platform / module | Integration |
|---|---|
| **QQ** | OneBot v11 + NapCat |
| **WeChat `weixin`** | iLink Bot API, QR login, no public webhook; [guide](channels/weixin/README.md) |
| **Personal WeChat `wcauto`** | Windows desktop bridge, private / group messages, media, and Moments; separate bridge runtime and write-operation switches; [guide](channels/wcauto/README.md) |
| **Feishu (Lark)** | WebSocket events; separate [Feishu ACP task delegation](docs/feishu-task-binding.md) |
| **Telegram** | Bot API long polling / webhook |
| **GitHub** | Repository polling or webhooks, priority wakeups and aggregation, with AI queries and writeback; [guide](channels/github/README.md) |
| **AcFun** | Danmaku and private messages |
| **Minecraft** | Mineflayer MCP companion and game tasks; source and installed runtime are managed separately; [guide](channels/minecraft/README.md) |
| **WebUI** | Workspace conversation, SSE / WebSocket, and UI interaction |
| **HTTP API** | Bidirectional messages, media, and vision; synchronous, SSE, and callbacks; disabled by default; [protocol](channels/http_api/README.md) |
| **CLI** | Terminal interaction and debugging; [guide](channels/cli/README.md) |
| **Responses API** | Expose the agent as an OpenAI-compatible service |

Platforms require their respective accounts, credentials, and runtime environments. A connected channel and a verified real send/receive are different outcomes.

### Tool Entities

| Entity | Responsibility |
|---|---|
| `filesystem` / `codebox` | Files, directories, shell, Python, and tool orchestration under sandbox and permission rules |
| `operation` / `screen` | Desktop actions, semantic MCP operations, and screen vision sources |
| `smart_home` | Home Assistant and pluggable device domains; [guide](entities/smart_home/README.md) |
| `minimax` / `dashscope` | Multimodality, speech, voices, and provider-specific capabilities |
| `audiosync` / `sticker` | Audio ingestion, image / sticker collections, perception, and semantic retrieval |
| `vault` | Encrypted credentials, TOTP, generation, and lookup |
| `ssh` / `devops` | Remote management, updates, builds, restart, module synchronization, and crash reports |
| `share` / `ui` | File and media sharing, link lifecycles, and AI UI interaction |
| `ai_desktop` | Ambient time, holidays, weather, calendars, and quotas |
| `dify` / `sillytavern` | Dify application / workflow integration and SillyTavern processes / character cards |
| `plugins` / `mcp` | Marketplace subscriptions, installation / upgrade / removal, and MCP connections and tools |
| `model_control` / `entity_query` / `system` | Models and Ollama, two-level capability discovery, and system / Git / log tools |

The Tools page distinguishes **core, entity, channel, and external** sources from actual registration ownership. Core tools are not treated as individually reloadable entity extensions. Plugins can package manifests, skills, MCP settings, and Python tools; the AI can manage installation and upgrades within its permissions.

## Architecture and Development

### Layers and Module Ownership

```text
React WebUI → FastAPI routers → services → agent → core
                                            ↑
Entities → entities._sdk → registry / runtime bridge
Channels → agent.channel
```

`core` has no business-layer dependencies, and `agent` does not depend on Web. Concrete entities and channels are assembled only at the runtime composition root. Import-linter enforces direction. FlowMachine declares startup dependencies; Lifecycle starts long-lived components in registration order and cleans up in reverse. Instance locks remain held until critical storage closes.

| Directory | Responsibility |
|---|---|
| `core/` | Registry, config and paths, lifecycle, events, logs, networking, storage volumes, and shared contracts |
| `agent/mind/` · `context/` · `llm/` | Scheduling and execution, context layers, shared model SDK, and cache diagnostics |
| `agent/memory/` · `skills/` | Memories, relationships, graph projections, skill learning, and governance |
| `agent/task/` · `heartbeat/` · `planning/` | Task definitions, when to run them, and goal progress |
| `agent/delegation/` · `workflow/` · `hooks_llm/` | Isolated delegation, recoverable workflows, and async LLM work |
| `agent/audio/` · `voice/` · `tts/` · `realtime/` · `vision/` | Audiovisual and realtime capabilities |
| `agent/approval/` · `security/` · `judgment/` · `retrieval/` | Permissions, security, structured judgment, and retrieval |
| `channels/` · `entities/` | Extension modules owning backend code, config declarations, frontend, translations, and tests |
| `services/` · `web/` | Management facades, API, and React frontend |
| `acp/` · `deploy/` · `scripts/` | ACP, separate-service deployment, and operations |
| `config/` · `workspace/` · `tests/` | Local configuration, runtime workspace, and layered tests |

### Configuration and Data

| Location | Content and update path |
|---|---|
| `config/llm_clients.json` | Providers and models, shared by file watching, Web, and AI updates |
| `config/app_config.json` | Main settings; backend declarations generate the config center, with most changes applied live |
| `config/mcp_servers.json` | MCP services synchronized through connection management |
| `config/webui.json` | Port, authentication, and branding; port changes need restart, branding is customizable |
| `channels/<id>/channel_config.json` | Module-owned values; `CONFIG_MODEL` is the sole field declaration |
| `config/tasks/` · `config/heartbeat.json` | Task content and schedules |
| `config/provider_keys.json` | Component credentials; specialized authorization belongs to its module |
| `config/permission_rules.json` | Tool rules, target arguments, and conversation permissions |

`ConfigPaths` owns path resolution. `ANELF_CONFIG_DIR` / `ANELF_DATA_DIR` relocate configuration and data; `ANELF_<KEY>` overrides effective main settings. Fields supporting environment references accept `${ENV_VAR}`. MCP `env` / `headers` follow their service configuration contract.

Storage volumes manage SQLite, Cognee, and notes: online backups, validated publication, and restart-applied restore / migration. External PostgreSQL / MySQL support queries and table transfer. Back up both configuration and storage as part of your personal deployment.

### Extensions and Validation

- Entities declare tools in `entities/<name>/tools.py` and use framework capabilities through `entities._sdk`. Return JSON strings, add full types and Google docstrings, and use SDK `tool_error` / `error_from_exception` for failures.
- Channels provide `channels/<id>/adapter.py`, `config.py` (`CONFIG_MODEL`), and `__init__.py`; subclass `BaseChannel` and implement its interfaces. `CHANNEL_CLASS` may be exported; the configuration layer owns runtime value files.
- Module UI lives in entity `panel.tsx` / `panels/` or channel `frontend/`. Contributions mount workspace tools, dashboard cards, credential editors, routes, and tool views; core pages do not import concrete modules.
- Register runtime resources with Lifecycle and keep imports declarative. Document model-visible content, token impact, and cache impact for model I/O changes. Keep entity / channel tests inside their module's `tests/`.

```bash
uv sync --locked
uv run ruff check .
uv run lint-imports
uv run mypy core/
uv run pytest
scripts/check.sh                 # macOS / Linux: local CI mirror
```

For frontend development, keep the backend running and use another terminal:

```bash
cd web/frontend
npm ci
npm run dev                     # http://127.0.0.1:3000/webui/
# /api, including WebSocket, defaults to 127.0.0.1:8092
npm run lint
npm run typecheck
npm run test:modules
npm test
npx playwright install chromium
npm run test:e2e
npm run build
```

`ANELF_WEBUI_API_TARGET` overrides the development proxy. CI selects static gates, module / full tests, and frontend validation by change scope, including Windows and relevant macOS checks. The documentation size gate always remains active. Browser tests use isolated data; real accounts, games, and external services require separate live validation.

### Further Reading

| Topic | Documentation |
|---|---|
| Architecture and conventions | [Architecture reference](docs/architecture-reference.md) · [Workspace instructions](AGENTS.md) · [Design decisions](docs/design-decisions.md) |
| Autonomous execution | [Mind](docs/mind-architecture.md) · [Tasks / heartbeats / planning](docs/tasks-heartbeat-planning.md) · [Delegation and workflows](docs/delegation-and-workflow.md) |
| Memory and models | [Memory](docs/memory-system.md) · [Skills](docs/skills.md) · [Model SDK](docs/llm-and-models.md) · [Cache and cost](docs/context-cache.md) |
| Connections and multimodality | [Channels and scopes](docs/channels-and-scopes.md) · [Realtime voice](docs/realtime-voice.md) · [Voiceprints](docs/audio-voiceprint.md) · [Vision / capability routing](docs/vision-and-capabilities.md) |
| Security and operations | [Permissions and review](docs/security-and-approval.md) · [Operations](docs/operations.md) · [Networking](docs/network.md) · [MCP](docs/mcp-architecture.md) |
| Frontend and quality | [Frontend](docs/frontend.md) · [UI contributions](docs/ui-contributions.md) · [Testing](docs/testing.md) |
| Separate integrations | [Feishu ACP](docs/feishu-task-binding.md) · [Retrieval services](deploy/retrieval/README.md) · [VoiceHub](deploy/voicehub/README.md) · [Face service](deploy/face_server/README.md) |

## Personal Deployment and Open-Source Collaboration

### Permissions, Privacy, and Maintenance Boundaries

The project targets personal self-hosting. You choose channels, models, tools, and automated tasks, and manage account authorization, runtime permissions, backups, and access control. External models receive the context needed for their tasks; self-hosted data directories do not imply that all inference stays local.

`permission_rules.json` uses `tool_name(arg-glob)` with `allow / ask / deny`, at global and channel scope, with denial taking precedence. Guardian auxiliary review is **disabled by default**. When enabled, it reviews `ask` and CRITICAL calls without explicit rules; there are no human approval sessions. Unavailable Guardian review follows the autonomous allow policy and records the reason; broken permission configuration or evaluation errors block execution. Use explicit `deny` rules and deployment permissions for hard limits. Migrate legacy `approval_policies.json` into the current rules file.

The framework also provides file-write baselines, tool-loop guards, threat scanning, and ordinary-result / log redaction. Authorized temporary vault credential tools can expose plaintext to the current task model, while observation and persistence copies hide it; constrain such use through targets and permissions. WebUI supports an admin password and Bearer API keys; configure authentication before exposing it.

### Personally Maintained, Open to Contributions

Development follows the needs of a lasting AI used in everyday personal work, maintaining core contracts while keeping extensions self-contained. Reproducible issues, practical scenarios, channels, tools, skills, and documentation are welcome. Supported behavior and validation scope are established by implementation and the corresponding tests.

1. Fork the repository and create a feature branch.
2. Follow dependency direction, typing, and module ownership in [AGENTS.md](AGENTS.md).
3. Add appropriately scoped validation for behavior changes and distinguish live checks from isolated tests.
4. Describe the problem, resulting behavior, and validation in the PR. Remove temporary entities, channels, data, and credentials before submission.

**Stack**: Python · uv · FastAPI / Pydantic · LiteLLM · SQLite / FTS5 / sqlite-vec · Cognee · React 18 / TypeScript / Vite 6 · Tailwind CSS 4 · Zustand / TanStack Query · react-i18next.

Thanks to [LiteLLM](https://github.com/BerriAI/litellm), [FastAPI](https://github.com/fastapi/fastapi), [MCP](https://modelcontextprotocol.io/), [lark-oapi](https://github.com/larksuite/oapi-sdk-python), [NapCatQQ](https://github.com/NapNeko/NapCatQQ), and the broader open-source community. [Nekro Agent](https://github.com/KroMiose/nekro-agent) and [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) provided inspiration, and [Xerj](https://github.com/xerj-org/xerj) informed excerpt retrieval, source navigation, and index coverage design. Minecraft integration builds on the [awesome-mineflayer-mcp](https://github.com/G0Osey99/awesome-mineflayer-mcp) ecosystem.

AnelfAgent communicates with NapCat through OneBot without including or modifying its source. External components and services retain their own licenses and terms.

[MIT License](LICENSE) © 2025–2026 AnelfAgent Contributors · [Official Website](https://anelf.cn) · [GitHub](https://github.com/1292917512/AnelfAgent)
