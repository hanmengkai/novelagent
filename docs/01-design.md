

# 🧠 V5.1 工业级小说生成系统（稳定生产版）

---

# 🎯 一、系统目标（唯一约束）

系统用于持续生成长篇小说，必须满足：

* 📚 支持百万字连续生成
* 🧠 人设长期稳定不漂移
* 🌍 世界观不可逆一致
* 🪝 伏笔可追踪 + 必回收
* ⚔️ 每章必须有冲突推进
* 🎭 文风稳定（可配置）
* 🔁 自动修复逻辑错误
* 📊 每章可评分（仅用于趋势）
* 🔒 不允许事实幻觉写入系统

---

# 🏗 二、整体系统架构

```text id="arch_v51"
┌──────────────────────────────────────────────┐
│              LangGraph Orchestrator          │
│        （流程控制 / 状态机 / 分支路由）      │
└────────────────────┬─────────────────────────┘
                     ↓
┌──────────────────────────────────────────────┐
│         Narrative Control Layer              │
│  叙事控制器 / 节奏控制 / 冲突控制 / 爽点控制  │
└────────────────────┬─────────────────────────┘
                     ↓
┌──────────────────────────────────────────────┐
│            Agent Execution Layer             │
│ Director / Planner / Writer / Editor        │
│ Checker / RepairAgent                       │
└────────────────────┬─────────────────────────┘
                     ↓
┌──────────────────────────────────────────────┐
│                MCP Service Layer            │
│ Memory / World / Foreshadow / Rules         │
│ Retrieval / Style / Reader Metrics          │
└────────────────────┬─────────────────────────┘
                     ↓
┌──────────────────────────────────────────────┐
│                 Data Layer                  │
│ MySQL / Redis / Milvus / MinIO              │
└──────────────────────────────────────────────┘
```

---

# ⚙️ 三、核心运行模型（关键）

系统本质是一个：

> 🧠 状态机驱动 + 事实系统约束 + LLM生成执行器

---

# 🧭 四、LangGraph流程定义

## 📌 State结构（必须固定）

```python id="state_v51"
class NovelState:
    chapter_id: int

    world_id: str
    active_characters: list

    chapter_plan: dict

    draft_text: str
    final_text: str

    memory_snapshot: dict

    foreshadowing_due: list

    issues: list

    retry_count: int

    style_signature: dict
```

---

## 🔁 主流程（固定执行链）

```text id="flow_v51"
Director
  ↓
Planner
  ↓
Context Builder（MCP）
  ↓
Writer（Tool Calling）
  ↓
Editor
  ↓
Checker
  ↓
RepairAgent（必要时）
  ↓
Narrative Controller
  ↓
Memory Commit
  ↓
Foreshadow Update
  ↓
Loop Next Chapter
```

---

# 🧠 五、Narrative Control Layer（叙事控制器）

---

## 🎯 作用

统一控制：

* 节奏
* 冲突密度
* 情绪曲线
* 爽点分布

---

## 📦 输出结构

```json id="narrative"
{
  "arc_phase": "setup | buildup | climax | cooldown",
  "emotion_curve": "low → rising → peak",
  "conflict_intensity": 0.7,
  "next_chapter_goal": "introduce conflict"
}
```

---

# 🧰 六、MCP服务层（核心事实系统）

---

# 🧠 6.1 Memory MCP（唯一事实源）

```text
get_character(id)
update_character(id)

get_world(id)

append_event(event)

get_snapshot(chapter_id)
```

---

## ❗规则

* 所有事实必须来自 MCP
* LLM禁止创造新事实
* 修改必须通过 MCP API

---

# 🌍 6.2 World MCP（规则系统）

```json id="world"
{
  "cultivation_order": ["Qi", "Foundation", "Core", "Soul"],
  "timeline_rule": "strict_increasing",
  "forbidden_zones": {
    "BlackForest": ["no teleport", "no tech"]
  }
}
```

---

# 🪝 6.3 Foreshadow MCP（伏笔状态机）

```text id="foreshadow"
BURIED → ACTIVE → DUE → RESOLVED
```

---

## 数据结构

```sql
id
description
buried_chapter
due_range_start
due_range_end
state
importance
```

---

# 🎨 6.4 Style MCP（文风控制）

```text id="style"
dialogue_ratio: 0.35
emotion_density: medium
action_speed: fast
narration_type: immersive
```

---

# 📊 6.5 Reader MCP（趋势评分）

```json id="reader"
{
  "engagement": 0.72,
  "tension": 0.81,
  "drop_risk": 0.18
}
```

---

# ⚙️ 七、Agent层设计

---

# 🧭 7.1 Director

职责：

* 控主线方向
* 控冲突结构
* 控剧情阶段

---

# 📚 7.2 Planner

职责：

* 生成章节结构
* 分配伏笔
* 定义冲突点

---

# ✍️ 7.3 Writer（无记忆）

输入：

* MCP数据
* chapter_plan
* style_signature

输出：

* draft_text
* tool_calls

---

# 🎨 7.4 Editor

职责：

* 优化表达
* 保持节奏
* 不改剧情

---

# 🔍 7.5 Checker（强约束）

检查：

* 人设一致
* 世界规则
* 时间线
* 伏笔是否满足

输出：

```json
{
  "issues": [],
  "severity": "low | medium | high"
}
```

---

# 🔧 7.6 RepairAgent（结构化修复）

只允许输出 JSON Patch：

```json id="repair"
{
  "op": "insert | modify",
  "target": "chapter.section",
  "content": "...",
  "constraint": {
    "max_tokens": 120
  }
}
```

---

# 📦 八、数据层设计

---

# 🧠 MySQL（事实层）

* character
* world
* event
* foreshadowing

---

# ⚡ Redis（运行态）

* 当前章节上下文
* 最近3章摘要

---

# 🧠 Milvus（语义层）

* 章节摘要向量
* 人物变化向量
* 伏笔语义向量

---

# 📦 MinIO（存档层）

* 全量小说文本
* 历史版本

---

# 🔁 九、Compaction策略（关键）

---

## 每10章执行：

```text
1. 生成章节摘要
2. 更新 Milvus 向量
3. 压缩 Redis context
4. 保留 MySQL 事实
5. 不修改 MinIO 原文
```

---

# ⚙️ 十、核心约束规则（必须执行）

---

## 📌 每章必须满足：

```text
✔ 至少1个冲突
✔ 至少1个推进
✔ 至少1个情绪变化
✔ 至少1个伏笔操作
```

---

## ❌ 禁止：

* 直接修改世界规则
* 绕过 MCP 修改事实
* 时间线倒退
* 无依据新增人物设定

---

# 🔁 十一、单章执行闭环

```text id="loop"
1. Director制定方向
2. Planner生成结构
3. MCP加载事实
4. Writer生成草稿
5. Editor润色
6. Checker校验
7. RepairAgent修复
8. Narrative Controller调整节奏
9. Memory MCP提交事实
10. Foreshadow更新状态
11. Compactor（每10章）
```

---

# 🧠 十二、系统本质定义

---

> 🧠 一个由状态机驱动、MCP事实约束、LLM执行生成、规则系统校验、轻量叙事控制组成的长篇小说工业生成系统

---

# 🚀 十三、工程拆分建议（可直接开仓库）

建议拆成 5 个服务：

```
/langgraph-engine
/mcp-memory-service
/mcp-world-service
/mcp-foreshadow-service
/mcp-rule-service
/story-agents
```

---

# 📊 十四、系统能力边界

---

## ✔ 能力

* 百万字稳定生成
* 人设长期不漂移
* 世界规则一致
* 伏笔可控回收
* 自动修复逻辑错误

---

## ✔ 本质能力

> 从“文本生成”升级为“叙事状态机系统”

