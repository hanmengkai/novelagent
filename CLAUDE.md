# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Novel Agent V2 is an industrial-grade long-form novel generation system. The core design philosophy: **LLM is a dumb executor**, driven by a LangGraph state machine, a fact-isolation MCP service layer, and multi-layer narrative rules. This prevents context drift, fact hallucination, and narrative collapse across million-character novels.

## Commands

### Infrastructure

```bash
docker-compose up -d        # Start MinIO (the only external service required)
```

> **Note:** The README mentions MySQL/Redis/Milvus, but the current implementation uses JSON file storage (`db/file_store.py`) and an in-memory cache with file persistence (`db/cache.py`). Only MinIO is needed for full-text archival.

### Development Setup

```bash
python -m venv env
source env/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Fill in LLM API keys
```

Or use the managed start script (handles PID + health check):

```bash
./start.sh              # Start web server (port 9101)
./start.sh stop
./start.sh restart
./start.sh status
```

### CLI Commands

```bash
python main.py health                                       # Verify service connectivity
python main.py list                                         # List all novels
python main.py init --title "书名" --desc "简介" --volumes 10 --world-type 玄幻
python main.py reinit --novel-id <id>                       # Wipe and re-initialize
python main.py write --novel-id <id> --volume 1            # Write a full volume
python main.py write --novel-id <id> --volume 1 --replan   # Force re-plan chapters
python main.py chapter --novel-id <id> --volume 1 --chapter 5
python main.py rewrite --novel-id <id> --volume 1          # Rewrite volume from scratch
python main.py auto --novel-id <id> --start-volume 1       # Continuous generation
python main.py web --port 9101                             # Start web dashboard
```

Web dashboard URL: `http://localhost:9101/<WEB_SECRET_PATH>/` (set `WEB_SECRET_PATH` in `.env`)

### Running Tests

```bash
pytest
```

## Architecture

### Layer Stack

```
LangGraph Chapter Graph  (langgraph_engine/)
        ↓
Narrative Control Layer  (narrative/controller.py)
        ↓
Agent Execution Layer    (agents/)
        ↓
MCP Service Layer        (mcp/)
        ↓
Data Layer               (db/)
```

### LangGraph Chapter Pipeline (`langgraph_engine/`)

The entire per-chapter generation is a compiled `StateGraph` in `graph.py`. The single state object `NovelState` (a dataclass in `state.py`) flows through all nodes:

```
director → planner → context_builder → writer → editor → checker
    checker → (issues?) → repair_agent → checker  [retry loop, max 3]
    checker → (pass) → narrative_controller → fact_extract → memory_commit
    → foreshadow_update → (every N chapters) → compactor → END
```

Routing logic lives entirely in `router.py` — pure functions on `NovelState`. Every node boundary checks for a stop signal via `cache.is_stop_requested()`.

### Agents (`agents/`)

| Agent | Role | Model |
|---|---|---|
| `director.py` | Decides chapter macro direction, scene type, hook level | Default |
| `planner.py` | Designs 3-act structure, assigns foreshadow ops | Default |
| `writer.py` | Generates draft — stateless, only sees ContextBuilder output | Default |
| `editor.py` | Polishes text, eliminates exposition dumps | Default |
| `checker.py` | 7-dimension fact validation | **Strong model** (`deepseek-reasoner`) |
| `repair_agent.py` | Generates JSON Patch for surgical fixes | Strong model |
| `fact_extractor.py` | Extracts structured facts from final text | Default |

### MCP Service Layer (`mcp/`)

MCP services are the **sole source of truth for all facts**. Agents may read via MCP; they cannot write facts directly.

- `memory_mcp.py` — character state commits → JSON files + MinIO archive
- `world_mcp.py` — world rules, author intent, style constraints
- `foreshadow_mcp.py` — foreshadow state machine: `BURIED → ACTIVE → DUE → RESOLVED`
- `style_mcp.py` — style constraint injection per chapter
- `reader_mcp.py` — reader-side metrics

### Data Layer (`db/`)

| Module | Purpose |
|---|---|
| `file_store.py` | Atomic JSON file storage under `data/<novel_id>/` |
| `repository.py` | All data operations (novel, character, volume, foreshadow CRUD) |
| `cache.py` | In-memory dict with file persistence for hot context (`data/_memory_cache.json`) |
| `minio_client.py` | Full-text chapter archive (bucket `novel-texts/<novel_id>/`) |

Data lives under `data/` at the repo root. Each novel gets its own subdirectory keyed by `novel_id` (UUID).

### LLM Client (`llm/client.py`)

Unified OpenAI-compatible client routing to DeepSeek / Qwen / GLM / Kimi. Key functions:

- `chat()` — retries with exponential backoff (tenacity), handles truncation warnings
- `chat_json()` — returns `dict`; tries `json_mode` first, falls back to `extract_json()` text parsing
- `chat_strong()` — forces `deepseek-reasoner` (R1); **no temperature, no JSON mode**
- `extract_json()` — multi-strategy JSON extraction with DeepSeek-specific sanitization

GLM does not support `json_object` format — `chat_json()` automatically degrades to text extraction for GLM.

### Configuration (`config/settings.py`)

All settings via `pydantic-settings` from `.env`. Key tunables:

```
DEFAULT_PROVIDER      # deepseek | qwen | glm | kimi
STRONG_MODEL          # deepseek-reasoner (used by Checker + RepairAgent)
chapter_target_chars  # 5000 (default chapter length)
compaction_interval   # 20 (Compactor runs every N chapters)
max_retry_per_chapter # 3
```

`get_settings()` is `@lru_cache` — settings are loaded once per process.

### Pipeline Entry Points (`pipeline/`)

- `init_novel()` — 5-stage world building: WorldGen → StoryOutline → StyleConfig → VolumePlanning → AuthorIntent
- `run_chapter()` / `run_volume()` / `auto_run()` / `rewrite_volume()` — invoke `langgraph_engine.graph.run_chapter(NovelState)`

### Web Layer (`web/app.py`)

FastAPI app, all routes prefixed with `/<WEB_SECRET_PATH>/`. Provides REST API for novel management and a static dashboard. Started via `python main.py web` or `./start.sh`.

## Key Invariants

- **Never bypass MCP to write facts directly** to the data layer — all character/world state must flow through the MCP service layer.
- **Foreshadow state transitions are one-way**: `BURIED → ACTIVE → DUE → RESOLVED`. Overdue (`DUE` unresolved) foreshadows block chapter progression.
- **Checker uses `chat_strong()`** (DeepSeek R1). Do not downgrade it to the default model.
- **Writer has no memory** — it only receives the context assembled by `context_builder/builder.py`.
- The `compaction_interval` setting (default 20) controls how often `Compactor` runs — it extracts character arc progress and clears low-priority hot context.
