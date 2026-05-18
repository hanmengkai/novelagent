# Novel Agent V2

**English** | [中文](README_CN.md)

[![GitHub](https://img.shields.io/badge/GitHub-hanmengkai%2Fnovеlagent-181717?logo=github)](https://github.com/hanmengkai/novelagent)
[![Gitee](https://img.shields.io/badge/Gitee-hmk__855__admin%2Fnovеlagent-C71D23?logo=gitee)](https://gitee.com/hmk_855_admin/novelagent)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

Industrial-grade long-form novel generation system powered by a LangGraph state machine. Supports million-character continuous generation with zero character/world-view drift.

---

## Why This Architecture

Using ChatGPT or a single LLM to write long-form novels hits three unavoidable walls:

| Problem | Why common approaches fail | How this system solves it |
|---------|---------------------------|--------------------------|
| **Context window** | Beyond 128k tokens, models forget — characters contradict themselves, plots collapse | Facts stored as structured JSON; local vector retrieval fetches only what's needed per chapter; LLM sees a precisely trimmed context regardless of total novel length |
| **Fact hallucination** | LLMs invent new settings that overwrite old ones; character traits and abilities drift randomly | MCP service layer is the single source of truth; LLMs can only read facts, never create them; all changes must pass Checker validation before being written |
| **Narrative collapse** | No global structure; plots flatten over time; planted foreshadowing never resolves; emotional arcs collapse | Director owns the main storyline; Narrative Controller manages pacing; a foreshadow state machine enforces resolution; chapter conflict is guaranteed by rules |

> In essence: this is not "let the LLM write a novel" — it **demotes the LLM to a dumb executor**, with a state machine, fact system, and rule layer jointly driving the narrative.

---

## Features

- **No context limit** — LangGraph state machine + four-tier storage (hot in-memory cache / local vector retrieval / JSON fact files / MinIO full-text archive); theoretically infinite generation
- **Zero fact drift** — MCP service layer hard-isolates facts; Checker validates every chapter; RepairAgent applies surgical JSON Patch fixes; chapters that fail validation never reach the archive
- **Foreshadow closure guarantee** — `BURIED → ACTIVE → DUE → RESOLVED` state machine; overdue unresolved foreshadows block subsequent chapter generation; Checker includes a dedicated overdue-foreshadow audit
- **Three-element character engine** — every character is initialized with `want` (external goal) / `fear` (inner dread) / `contradiction` (tension between the two); plot advances only when characters make decisions within this triangle, not when external events push them
- **Hook density enforcement** — Planner mandates one change-node per 800–1000 characters (information reversal / decision conflict / crisis / emotional breakthrough); chapter-end suspense scored 1–5
- **Scene diversity detection** — Director reads the last 5 chapters' scene-type distribution and blocks repeated scene types; Narrative Controller emits `diversity_warning` to force differentiation
- **Character arc tracking** — Compactor extracts each character's `want/fear/contradiction phase/milestone` every 10 chapters and writes it to `character_arc_status` for Narrative Controller
- **Strong emotional anchor planning** — outline layer mandates 2–3 high-emotion anchor scenes per act (betrayal / sacrifice / extreme choice etc.); volume planning assigns anchors to specific chapters, preventing flat emotional curves throughout the novel
- **No author-explanation paragraphs** — Writer and Editor both enforce: world-building must surface through character action/dialogue/sensation; standalone background explanation blocks trigger an `EXPOSITION_DUMP` error
- **Specialist agent division** — Writer has no memory and only writes text; Checker uses the strongest model for reasoning validation; no context cross-contamination between roles
- **Self-healing loop** — Checker → RepairAgent → re-validation; logic errors are fixed automatically without human intervention
- **Multi-model routing** — DeepSeek / Qwen / GLM / Kimi routed by role; heavy reasoning goes to the strong model, generation tasks go to the fast model
- **Structured fact extraction** — FactExtractor pulls character state changes, locations, and new foreshadows from each chapter's text independently of Writer, preventing self-reporting bias
- **Thematic core system** — initialization distills a `central_question` / `emotional_contract` / four-act thematic beats stored in Director context; prevents narrative drift into generic tropes
- **User plan fidelity** — core plot points the user provides per volume are extracted and injected into volume planning, keeping generated content aligned with user intent
- **Character arc auto-advancement** — MemoryMCP auto-advances the protagonist's `arc_stage` (resolve → doubt → choice → transformation) based on volume progress, synchronized with thematic beats

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | LangGraph 0.2 / LangChain 0.3 |
| Web service | FastAPI + Uvicorn |
| Fact storage | JSON files (`data/<novel_id>/`) |
| Cache | In-memory dict + file persistence (`data/_memory_cache.json`) |
| Vector retrieval | sentence-transformers (local) + ChromaDB |
| Object storage | MinIO (full-text chapter archive) |
| LLM | DeepSeek / Qwen / GLM / Kimi (OpenAI-compatible API) |

---

## Architecture

```
LangGraph Orchestrator  (state machine / branching / flow control)
        ↓
Narrative Control Layer (pacing / conflict density / emotional curve / scene diversity / arc tracking)
        ↓
Agent Execution Layer
  Director → Planner → Writer → Editor → Checker → RepairAgent
        ↓
Fact & Memory Layer
  FactExtractor → MemoryMCP → ForeshadowMCP → Compactor
        ↓
MCP Service Layer  (Memory / World / Foreshadow / Style / Reader)
        ↓
Data Layer  (JSON files / in-memory cache / vector retrieval / MinIO)
```

---

## Quick Start

### Minimal Setup (5-minute start)

No Docker, no vector database — just **one LLM API key**.

**Minimal `.env` (DeepSeek example):**

```ini
# Fill in only the provider you're using; leave others blank
DEFAULT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# Vector search (optional): set false to skip embedding model download
# Falls back to keyword search — core generation is unaffected
VECTOR_SEARCH_ENABLED=false

# MinIO archive (optional): skip if you don't start MinIO

# Web access key (required)
WEB_SECRET_PATH=mynovel
```

**Four steps:**

```bash
# 1. Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy and edit config
cp .env.example .env
# Fill in DEEPSEEK_API_KEY and WEB_SECRET_PATH; keep everything else as default

# 3. Verify connectivity
python main.py health

# 4. Start the web console
./start.sh
# Open http://localhost:9101/mynovel/
```

> **Component requirements:**
> | Component | Required | Notes |
> |-----------|----------|-------|
> | LLM API key (any one) | **Yes** | Choose deepseek / qwen / glm / kimi / ollama |
> | Vector search (ChromaDB + embedding model) | No | `VECTOR_SEARCH_ENABLED=false` to disable; falls back to keyword search |
> | MinIO | No | Skip full-text archive without affecting generation or web console |
> | Docker | No | Only needed if using MinIO |

---

### Full Setup

#### 1. Start infrastructure

```bash
docker-compose up -d
```

Only MinIO needs to start. All other storage is local files or in-memory.

#### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

#### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your LLM API key(s)
```

#### 4. Set web access key

The web console is protected by a path prefix. Set it in `.env`:

```ini
WEB_SECRET_PATH=your_custom_path   # e.g. WEB_SECRET_PATH=mynovel
```

Access URL: `http://localhost:9101/mynovel/`

> Leaving this blank prevents the web console from starting.

#### 5. Verify

```bash
python main.py health
```

---

## Configuration

Copy `.env.example` to `.env` and fill in as needed.

### Option A: Cloud API (recommended)

| Provider | Key variable | Get key at |
|----------|-------------|------------|
| DeepSeek | `DEEPSEEK_API_KEY` | platform.deepseek.com |
| Qwen | `QWEN_API_KEY` | dashscope.aliyuncs.com |
| GLM | `GLM_API_KEY` | open.bigmodel.cn |
| Kimi | `KIMI_API_KEY` | platform.moonshot.cn |

```ini
DEFAULT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
STRONG_MODEL=deepseek-reasoner   # Used by Checker — do not downgrade
```

### Option B: Ollama (local, free, no API key)

**Prerequisites:** install [Ollama](https://ollama.com) and pull a model

```bash
ollama pull qwen2.5:14b   # recommended: good writing quality, runs on 16 GB VRAM
ollama pull qwq:32b       # optional: use as STRONG_MODEL for better reasoning
```

**`.env` config:**

```ini
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen2.5:14b
STRONG_MODEL=qwq:32b     # if unavailable, set to qwen2.5:14b (lower validation quality)
JSON_MODEL=qwen2.5:14b
```

> Ollama does not support `json_object` format — the system automatically falls back to text parsing.

### STRONG_MODEL

`STRONG_MODEL` is used exclusively by **Checker** (7-dimension fact validation) and **RepairAgent**. Use a reasoning-capable model:
- Cloud: `deepseek-reasoner`, `qwq-32b`
- Local: `qwq:32b` (~20 GB VRAM)

Downgrading to a regular model won't crash the system, but fact validation quality drops noticeably.

---

## CLI

### Initialize a novel

```bash
python main.py init \
  --title "Sea of Stars" \
  --desc "Protagonist Lin Feng awakens from mediocrity and embarks on a cultivation journey..." \
  --volumes 10 \
  --world-type 玄幻
```

Outputs a `novel_id` required by all subsequent commands.

### Write a volume

```bash
python main.py write --novel-id <id> --volume 1
```

### Write a single chapter

```bash
python main.py chapter --novel-id <id> --volume 1 --chapter 5
```

### Continuous auto-generation

```bash
python main.py auto --novel-id <id> --start-volume 1
```

### List all novels

```bash
python main.py list
```

### Start the web console

**Option 1 — managed process (recommended)**

```bash
./start.sh              # start (logs → logs/web.log)
./start.sh stop
./start.sh restart
./start.sh status
```

**Option 2 — foreground**

```bash
python main.py web --port 9101
```

Access: `http://localhost:9101/<WEB_SECRET_PATH>/`

---

## Chapter Generation Pipeline

Every chapter is driven by the LangGraph state machine. All nodes share a single `NovelState` and respond to stop signals at any boundary.

```
Director → Planner → ContextBuilder → Writer → Editor
    → Checker ──(issues)──→ RepairAgent ──→ Checker (loop)
              ↘ (pass)
    NarrativeController → FactExtractor → MemoryMCP
    → ForeshadowMCP → (every 10 chapters) Compactor → END
```

| Node | Role |
|------|------|
| **Director** | Macro direction; scene type requirement; scene diversity log |
| **Planner** | Three-act structure; hook count; contradiction activation per scene |
| **ContextBuilder** | Pulls from MCP + vector retrieval; assembles token-budgeted prompt |
| **Writer** | Stateless text generation; receives only ContextBuilder output |
| **Editor** | Polishes text; eliminates exposition dumps; outputs editorial notes |
| **Checker** | 7-dimension validation using the strongest model |
| **RepairAgent** | Generates JSON Patch for surgical fixes; loops back to Checker |
| **NarrativeController** | Pacing assessment; diversity warning; character arc push |
| **FactExtractor** | Extracts structured facts independently of Writer |
| **MemoryMCP** | Commits facts to JSON; updates vector index; auto-advances arc stage |
| **ForeshadowMCP** | Foreshadow state machine: plant / activate / resolve / mark_due |
| **Compactor** | Every 10 chapters: compresses events; extracts arc progress; clears low-priority cache |

**Per-chapter invariants:** at least 1 conflict, 1 plot advance, 1 emotional change, 1 foreshadow operation, 5 change-nodes (hooks), chapter-end suspense ≥ 3.

---

## Quality Enforcement Layers

```
Prompt constraint layer    — hard rules in Writer/Editor/Checker prompts
        ↓
Planning constraint layer  — Planner enforces hook_count / contradiction_activated / ending_hook_level
        ↓
Director constraint layer  — Director enforces scene_type_requirement (prevents repetition)
        ↓
Narrative constraint layer — NarrativeController diversity_warning + character_arc_push (prevents flatness)
        ↓
Validation & repair layer  — Checker 7-dimension check + RepairAgent self-healing (prevents errors)
        ↓
Memory tracking layer      — Compactor character arc tracking (prevents character becoming a plot tool)
        ↓
Outline anchor layer       — global_emotional_anchors planning (prevents emotional collapse)
```

---

## Project Structure

```
novelagentv2/
├── main.py                 # CLI entry point
├── docker-compose.yml      # Infrastructure (MinIO is the only external dependency)
├── requirements.txt
├── config/
│   ├── settings.py         # All config (pydantic-settings, reads .env)
│   └── prompts.py          # Prompt templates (all constraint rules)
├── langgraph_engine/
│   ├── graph.py            # LangGraph main graph definition
│   ├── router.py           # Conditional routing
│   └── state.py            # NovelState definition
├── agents/                 # Director / Planner / Writer / Editor / Checker / RepairAgent / FactExtractor
├── narrative/              # NarrativeController + ArcPlanner
├── mcp/                    # Memory / World / Foreshadow / Style / Reader MCP services
├── context_builder/        # Context assembly (calls MCP + vector retrieval)
├── compactor/              # Every-10-chapter compression + arc tracking
├── pipeline/               # High-level entry points (init_novel / run_volume / run_chapter)
├── db/                     # JSON file storage / in-memory cache / local vector retrieval / MinIO client
└── web/                    # FastAPI web console
```

---

## License

MIT
