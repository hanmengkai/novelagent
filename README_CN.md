# Novel Agent V2

[English](README.md) | **中文**

[![GitHub](https://img.shields.io/badge/GitHub-hanmengkai%2Fnovеlagent-181717?logo=github)](https://github.com/hanmengkai/novelagent)
[![Gitee](https://img.shields.io/badge/Gitee-hmk__855__admin%2Fnovеlagent-C71D23?logo=gitee)](https://gitee.com/hmk_855_admin/novelagent)

工业级长篇小说自动生成系统，基于 LangGraph 状态机驱动，支持百万字连续生成且人设/世界观长期稳定不漂移。

---

## 为什么需要这套架构

直接用 ChatGPT / 单一 LLM 写长篇小说会遇到三个无法绕过的墙：

| 问题 | 常见做法的失败原因 | 本系统的解法 |
|------|-------------------|-------------|
| **上下文窗口** | 超过 128k token 后模型开始遗忘，人设/情节出现矛盾 | JSON 文件存结构化事实，本地向量检索按需语义召回，LLM 每次只看精确裁剪的上下文，与小说总长无关 |
| **事实幻觉** | LLM 会"发明"新设定覆盖旧设定，角色性格/能力随机漂移 | MCP 服务层是唯一事实源，LLM 只能读取不能创造，所有变更必须经 Checker 校验后才写入 |
| **叙事失控** | 没有全局结构，越写越平淡，伏笔开了不回收，情绪曲线塌陷 | Director 控主线、Narrative Controller 管节奏、伏笔状态机强制回收，章节冲突由规则保底 |

> 本质上：这不是"让 LLM 写小说"，而是**把 LLM 降级为执行器**，由状态机、事实系统和规则层共同驱动叙事。

---

## 特性

- **无上下文限制** — LangGraph 状态机 + 四层存储（内存缓存热上下文 / 本地向量检索 / JSON 文件事实 / MinIO 全文存档），理论上可无限生成
- **零事实漂移** — MCP 服务层强隔离，Checker 每章强校验，RepairAgent JSON Patch 精准修复，不合格章节不会进入存档
- **伏笔闭环保证** — `BURIED → ACTIVE → DUE → RESOLVED` 状态机，到期未回收的伏笔会阻塞后续章节生成；Checker 新增逾期伏笔专项审计
- **角色三要素驱动** — 每个角色在初始化时强制生成 `want`（外部目标）/ `fear`（内在恐惧）/ `contradiction`（两者之间的矛盾张力），情节推进必须由角色在这三者间的决策驱动，而非外部事件推着角色走
- **钩子密度保障** — Planner 强制每 800~1000 字设置一个变化节点（信息反转/决策冲突/危机/情感破防），章末悬念强度 1-5 分级管控
- **全程场景多样性检测** — Director 读取近 5 章场景类型分布，禁止同类场景连续主导；Narrative Controller 输出 `diversity_warning` 强制差异化
- **角色弧线专用追踪** — Compactor 每 10 章提取每个角色的 `want/fear/矛盾阶段/里程碑`，写入 `character_arc_status`，供 Narrative Controller 规划下一阶段角色推进
- **强情绪锚点规划** — 大纲层强制为每幕规划 2~3 个强情绪锚点场景（背叛/牺牲/极限抉择等），卷规划时必须将锚点分配到具体章节，防止全书情绪曲线平坦
- **禁止作者说明段落** — Writer 和 Editor 双重拦截：任何世界设定必须通过角色行动/对话/感官呈现，独立背景说明段落超过 2 句即报 `EXPOSITION_DUMP` 错误
- **专职 Agent 分工** — Writer 无记忆只写文，Checker 用最强模型做推理验证，各角色无上下文污染
- **自愈闭环** — Checker → RepairAgent → 重新校验，无需人工干预即可修复逻辑错误
- **多模型路由** — DeepSeek / 通义千问 / GLM / Kimi 按角色分配，推理重任给强模型，生成任务给快模型，兼顾质量与成本
- **结构化事实提取** — FactExtractor 每章自动从正文提取角色状态变更、地点、新伏笔，独立于 Writer 避免自我污染
- **主题核心系统** — 初始化时提炼全书 `central_question` / `emotional_contract` / 四幕主题节拍，存入 Director 上下文，防止叙事漂移为"爽文套路"
- **用户规划忠实度** — 用户在描述中提供的每卷核心情节自动提取并注入卷规划，生成内容与用户预期高度对齐
- **人设-大纲对齐校验** — 初始化时自动检查主角/反派的 want/fear/contradiction 是否能驱动大纲弧线，若错位则自动修正
- **角色弧阶自动推进** — MemoryMCP commit 时根据卷进度（0-25%: 初心, 26-50%: 动摇, 51-75%: 抉择, 76-100%: 蜕变）自动推进主角 `arc_stage`，与主题节拍同步

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 流程编排 | LangGraph 0.2 / LangChain 0.3 |
| Web 服务 | FastAPI + Uvicorn |
| 事实存储 | JSON 文件（data/<novel_id>/） |
| 缓存 | 内存字典 + 文件持久化（data/_memory_cache.json） |
| 向量检索 | 本地 sentence-transformers + ChromaDB |
| 对象存储 | MinIO（全量文本存档） |
| LLM | DeepSeek / Qwen / GLM / Kimi（OpenAI 兼容接口） |

---

## 系统架构

```
LangGraph Orchestrator（流程控制 / 状态机 / 分支路由）
        ↓
Narrative Control Layer（节奏 / 冲突密度 / 情绪曲线 / 场景多样性 / 角色弧线追踪）
        ↓
Agent Execution Layer
  Director → Planner → Writer → Editor → Checker → RepairAgent
        ↓
Fact & Memory Layer
  FactExtractor → MemoryMCP → ForeshadowMCP → Compactor
        ↓
MCP Service Layer（Memory / World / Foreshadow / Style / Reader）
        ↓
Data Layer（JSON 文件 / 内存缓存 / 向量检索 / MinIO）
```

---

## 快速开始

### 最简配置（5 分钟启动）

不需要 Docker，不需要向量数据库，只需要**一个 LLM API Key** 即可运行。

**`.env` 最小配置示例（以 DeepSeek 为例）：**

```ini
# 只填你要用的那个 provider 的 key，其余留空即可
DEFAULT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# 向量搜索（可选）：设为 false 跳过本地 embedding 模型下载
# 禁用后自动降级为关键词搜索，不影响核心生成功能
VECTOR_SEARCH_ENABLED=false

# MinIO 全文存档（可选）：不填则跳过归档，不影响生成
# 若不启动 MinIO，保持以下默认值即可（系统会跳过归档步骤）

# Web 访问密钥（必填）
WEB_SECRET_PATH=mynovel
```

**四步启动：**

```bash
# 1. 安装依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 复制并编辑配置
cp .env.example .env
# 填写 DEEPSEEK_API_KEY 和 WEB_SECRET_PATH，其余保持默认

# 3. 验证连接
python main.py health

# 4. 启动 Web 控制台
./start.sh
# 访问 http://localhost:9101/mynovel/
```

> **各组件可选性说明：**
> | 组件 | 是否必须 | 说明 |
> |------|---------|------|
> | LLM API Key（任意一个） | **必须** | 选 deepseek / qwen / glm / kimi / ollama 之一 |
> | 向量搜索（ChromaDB + embedding 模型） | 可选 | `VECTOR_SEARCH_ENABLED=false` 禁用，降级为关键词搜索 |
> | MinIO | 可选 | 不启动则跳过全文归档，不影响生成和 Web 控制台 |
> | Docker | 可选 | 仅 MinIO 需要，禁用向量+不用归档则完全不需要 Docker |

---

### 完整启动流程

### 1. 启动基础服务

```bash
docker-compose up -d
```

只需启动 MinIO（唯一外部依赖）。其他数据存储均为本地文件或内存实现。

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写 LLM API Key 等配置
```

### 4. 配置 Web 访问密钥

Web 控制台通过路径前缀保护，必须在 `.env` 中设置：

```ini
WEB_SECRET_PATH=your_custom_path   # 例如: WEB_SECRET_PATH=mynovel
```

访问地址：`http://localhost:9101/mynovel/`

> 留空则 Web 控制台无法启动。

### 5. 验证连接

```bash
python main.py health
```

---

## 配置说明

复制 `.env.example` 为 `.env`，按需填写。

### 方案一：云端 API（推荐）

| 提供商 | KEY 变量 | 获取地址 |
|--------|----------|---------|
| DeepSeek | `DEEPSEEK_API_KEY` | platform.deepseek.com |
| 通义千问 | `QWEN_API_KEY` | dashscope.aliyuncs.com |
| GLM | `GLM_API_KEY` | open.bigmodel.cn |
| Kimi | `KIMI_API_KEY` | platform.moonshot.cn |

```ini
DEFAULT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
STRONG_MODEL=deepseek-reasoner   # Checker 使用，推理模型，不要降级
```

### 方案二：Ollama 本地模型（免费，无需 API Key）

**前置条件：** 安装 [Ollama](https://ollama.com) 并拉取模型

```bash
ollama pull qwen2.5:14b        # 推荐：写作质量好，14B 可跑在 16G 显存
ollama pull qwq:32b            # 可选：用作 STRONG_MODEL（推理能力强）
```

**`.env` 配置：**

```ini
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen2.5:14b
STRONG_MODEL=qwq:32b           # 若无 qwq 可设为 qwen2.5:14b（校验质量会下降）
JSON_MODEL=qwen2.5:14b
```

> **注意：** Ollama 不支持 `json_object` 格式，系统会自动降级为文本解析。

### STRONG_MODEL 说明

`STRONG_MODEL` 专用于 **Checker**（7 维度事实校验）和 **RepairAgent**（修复生成）。  
建议使用具备推理能力的模型：
- 云端：`deepseek-reasoner`、`qwq-32b`
- 本地：`qwq:32b`（需 ~20G 显存）

降级为普通模型不会导致崩溃，但事实校验质量会明显下降。

---

## CLI 用法

### 初始化小说

```bash
python main.py init \
  --title "星辰大海" \
  --desc "主角林枫从废柴觉醒，踏上修炼之路..." \
  --volumes 10 \
  --world-type 玄幻
```

输出 `novel_id`，后续命令均需此 ID。

### 写一卷

```bash
python main.py write --novel-id <id> --volume 1
```

### 写单章

```bash
python main.py chapter --novel-id <id> --volume 1 --chapter 5
```

### 全自动连续生成

```bash
python main.py auto --novel-id <id> --start-volume 1
```

### 查看所有小说

```bash
python main.py list
```

### 启动 Web 控制台

**方式一：托管进程（推荐，后台运行 + PID 管理）**

```bash
./start.sh              # 启动（日志写入 logs/web.log）
./start.sh stop         # 停止
./start.sh restart      # 重启
./start.sh status       # 查看状态
```

**方式二：前台直接运行**

```bash
python main.py web --port 9101
```

访问地址：`http://localhost:9101/<WEB_SECRET_PATH>/`（需在 `.env` 中设置 `WEB_SECRET_PATH`）

---

## 初始化流程（`init` 命令执行的全部模块）

执行 `init` 时，系统按顺序调用以下模块，完成一次性世界构建：

```
【0/5】提取用户卷规划提示（LLM）
  ├─ LLM 从用户描述中提取每卷的核心信息（标题提示/核心目标/关键情节/高潮/结尾/基调）
  └─ 存储为 user_volume_hint_N，供后续卷规划使用

【1/5】WorldGen（LLM + WorldMCP + MemoryMCP）
  ├─ LLM 生成：世界名、世界类型、背景设定、力量体系
  ├─ LLM 生成：主角（名字、性格、初始实力、目标、外貌、口头禅）
  │   └─ 强制生成三要素：want（外部目标）/ fear（内在恐惧）/ contradiction（矛盾张力）
  ├─ LLM 生成：主要反派（动机、势力、实力、外貌 + 三要素）
  ├─ LLM 生成：配角列表（角色关系网 + 各自三要素）
  ├─ LLM 生成：情绪签名（core_emotion / emotional_range / rhythm / forbidden_tones）
  ├─ world_mcp.initialize_world_rules() → 写入世界规则约束
  ├─ repo.upsert_character() × N → 角色表初始化（含 want/fear/contradiction）
  └─ 【1b】从用户描述提取所有命名角色，自动创建表中缺失的人物

【2/5】StoryOutline（LLM + ForeshadowMCP）
  ├─ LLM 生成：四幕结构大纲（起/承/转/合），以用户卷规划为权威参考
  ├─ LLM 生成：每幕情绪锚点（2~3个强情绪场景，含类型/触发矛盾/铺垫要求）
  ├─ LLM 生成：全书级强情绪锚点列表（global_emotional_anchors）
  ├─ LLM 生成：核心主题、矛盾弧线、结局方向
  ├─ LLM 生成：主线伏笔列表（含回收章节预估）
  ├─ foreshadow_mcp.plant() × N → 伏笔状态机初始化（BURIED）
  ├─ 【2a】提炼全书主题核心：central_question / protagonist_answer / emotional_contract / thematic_beats / anti_themes
  └─ 【2b】检查主角/反派 want/fear/contradiction 是否能驱动大纲弧线，若错位则自动修正

【3/5】StyleConfig（StyleMCP）
  ├─ 按世界类型配置语言基调（热血/悬疑/言情…）
  ├─ 设定对话比例、情绪密度、叙事节奏
  ├─ 配置禁止词表（副本/BOSS/血条…）
  └─ style_mcp.set_style() → 写入风格约束

【4/5】VolumePlanning（LLM + DB）
  ├─ 按四幕结构拆分卷目，融合用户卷规划提示，分配主线目标与冲突弧
  ├─ LLM 批量生成卷名（一次调用，避免重复风格）
  └─ 卷表初始化（status=planned）

【5/5】AuthorIntent（LLM）
  ├─ LLM 生成作者长期意图声明（≤100字）
  └─ 写入 current_focus（第1-2卷写作方向）
```

**初始化产物一览：**

| 产物 | 存储位置 | 说明 |
|------|---------|------|
| 世界观 / 力量体系 | JSON `world_memory` | WorldMCP 唯一事实源 |
| 角色档案（含三要素） | JSON `characters` | 含主角/反派/配角，extra 字段存 want/fear/contradiction/appearance/catchphrases |
| 四幕大纲 + 情绪锚点 | JSON `world_memory` | 全局叙事骨架，含强情绪场景规划 |
| 用户卷规划提取 | JSON `world_memory` | user_volume_hint_N：每卷 core_goal / key_plots / climax / ending / tone |
| 主题核心（thematic_core） | JSON `world_memory` | central_question + emotional_contract + thematic_beats + anti_themes |
| 主线伏笔 | JSON `foreshadows` | 初始状态 BURIED |
| 风格约束 | JSON `world_memory` | 每章生成时注入 |
| 卷目规划 | JSON `volumes` | status=planned |
| 作者意图 | JSON `world_memory` | 防止叙事漂移 |

---

## 章节生成流程（每章执行的全部动作）

每章生成由 LangGraph 状态机驱动，节点间通过 `NovelState` 传递状态，任意节点均可响应停止信号立即中断。

### 生成前（准备阶段）

```
① Director（主线决策）
  ├─ 读取 author_intent + current_focus + volume_plan
  ├─ 读取近 5 章场景类型分布，生成多样性日志
  ├─ 评估当前叙事位置（冲突密度 / 情绪曲线 / 伏笔压力）
  ├─ 输出本章主线方向（推进 / 高潮 / 转折 / 喘息）
  ├─ 指定场景类型要求（战斗/对话/调查/突破/逃亡/情感/政治，强制与近期不同）
  └─ 指定章末悬念强度要求（1-5，默认≥3）

② Planner（章节结构设计）
  ├─ 接收 Director 方向
  ├─ 设计本章三幕结构：开场锚点 → 冲突升级 → 结尾钩子
  ├─ 每个场景标注 contradiction_activated（激活哪个角色的 want/fear 矛盾）
  ├─ 每个场景标注 hook_type（变化节点类型）
  ├─ 声明 hook_count（每 800~1000 字至少 1 个，5000 字章节至少 5 个）
  ├─ 声明 ending_hook_level（章末悬念强度 1-5）
  ├─ 分配本章必须触碰的伏笔（ACTIVE/DUE 状态）
  └─ 输出场景列表 + 出场角色 + 情绪弧

③ ContextBuilder（上下文精确裁剪）
  ├─ memory_mcp：拉取角色当前状态（位置/情绪/实力/want/fear/contradiction）
  ├─ world_mcp：注入本章相关世界规则约束
  ├─ foreshadow_mcp：注入待激活/到期伏笔列表
  ├─ style_mcp：注入风格约束与禁用词
  ├─ 本地向量检索：召回最相关的历史段落（非全文）
  ├─ 内存缓存：拉取最近 N 章热上下文摘要
  └─ 组装最终 prompt（严格控制 token 预算）
```

### 生成中（写作与校验）

```
④ Writer（草稿生成）
  ├─ 无历史记忆，只接收 ContextBuilder 裁剪好的上下文
  ├─ 按 Planner 结构生成正文
  ├─ 情节转折必须由角色在 want/fear 之间做出决策驱动
  ├─ 世界设定必须通过角色行动/对话/感官呈现，禁止独立说明段落
  └─ 输出原始草稿

⑤ Editor（润色 + 深化）
  ├─ 将叙述型情绪（"他感到…"）改写为内心独白/身体感知/环境折射
  ├─ 扫描并消除独立背景说明段落（超过 2 句的设定解释段）
  ├─ 检查情节驱动力，发现纯外力推动的转折时输出【编辑建议】
  ├─ 优化语言流畅度、消除重复词语
  └─ 输出润色稿（附编辑建议，供下章 Director 参考）

⑥ Checker（事实校验，最强模型）
  ├─ 人设一致性：角色性格/能力是否与档案矛盾
  ├─ 世界规则：是否违反力量体系或地理设定
  ├─ 时间线：事件顺序是否逻辑自洽
  ├─ 伏笔合规：逾期伏笔专项审计（overdue_still_missing 非空 → 高优先级 issue）
  ├─ 说明段落检查：EXPOSITION_DUMP（独立背景说明段落）
  ├─ 角色主动性检查：PASSIVE_PROTAGONIST（关键转折无角色决策）
  └─ 输出 issues 列表（通过 → 继续；有问题 → 触发修复）

⑦ RepairAgent（自愈修复，仅在 Checker 发现问题时触发）
  ├─ 接收 issues 列表
  ├─ 生成 JSON Patch（精准最小化修改，不重写全文）
  ├─ 应用 patch 后重回 Checker 校验
  └─ 最多重试 N 次，超限后强制放行并记录警告
```

### 生成后（事实沉淀）

```
⑧ NarrativeController（叙事节奏调整）
  ├─ 读取近 5 章场景类型分布与悬念强度
  ├─ 读取 character_arc_status（各角色弧线当前阶段）
  ├─ 评估本章冲突密度 / 爽点分布 / 情绪曲线
  ├─ 输出 diversity_warning（若近期存在重复模式则强制下章差异化）
  ├─ 输出 character_arc_push（指定下章需推进哪个角色弧线的哪个阶段）
  └─ 更新全局叙事节奏状态，供下一章 Director 参考

⑨ FactExtractor（结构化事实提取）
  ├─ 从正文独立提取（不依赖 Writer 自报）：
  │   ├─ 角色状态变更（位置移动 / 情绪变化 / 实力突破）
  │   ├─ 新出现的地点 / 物品 / 势力
  │   └─ 本章新埋伏笔（隐式或显式）
  └─ 输出结构化 diff，供后续节点写入

⑩ MemoryMCP（事实提交）
  ├─ 将 FactExtractor 输出写入 JSON 角色档案
  ├─ 更新角色当前状态（位置/情绪/实力/关系）
  ├─ 写入本地向量索引（本章语义存档）
  ├─ 写入 MinIO 全文存档
  ├─ 更新内存缓存热上下文摘要
  └─ 根据卷进度自动推进主角 arc_stage（初心→动摇→抉择→蜕变），与主题节拍同步

⑪ ForeshadowMCP（伏笔状态推进）
  ├─ 将本章新埋伏笔 plant()（BURIED）
  ├─ 将本章激活的伏笔 activate()（BURIED → ACTIVE）
  ├─ 将本章回收的伏笔 resolve()（DUE → RESOLVED）
  ├─ mark_due()：检查是否有伏笔进入到期窗口
  └─ get_overdue()：若存在逾期未回收伏笔，写入警告状态

⑫ Compactor（每 10 章触发一次）
  ├─ 将过去 10 章事件压缩为结构化摘要
  ├─ 提取每个角色的弧线进度（want_status / fear_status / contradiction_phase / arc_milestone）
  ├─ 更新 character_arc_status → world_memory（供 NarrativeController 读取）
  ├─ 清理内存缓存低优先级热上下文
  └─ 为后续章节释放上下文空间
```

**流程总览：**

```
Director → Planner → ContextBuilder → Writer → Editor
    → Checker ──(有问题)──→ RepairAgent ──→ Checker（循环）
              ↘(通过)
    NarrativeController → FactExtractor → MemoryMCP
    → ForeshadowMCP → (每10章) Compactor → END
```

---

## 核心约束

每章生成必须满足：至少 1 个冲突、1 个推进、1 个情绪变化、1 个伏笔操作、5 个变化节点（钩子）、章末悬念强度 ≥ 3。

禁止行为：绕过 MCP 直接修改事实、时间线倒退、无依据新增人设、使用独立背景说明段落、情节由外力而非角色决策推动。

---

## 质量保障层级

```
提示词约束层  ── Writer/Editor/Checker prompt 中的硬性规则
      ↓
规划约束层    ── Planner 强制 hook_count / contradiction_activated / ending_hook_level
      ↓
导演约束层    ── Director 强制 scene_type_requirement / chapter_end_hook_level（防重复）
      ↓
叙事约束层    ── NarrativeController diversity_warning + character_arc_push（防平坦）
      ↓
校验修复层    ── Checker 7 维度检查 + RepairAgent 自愈（防错误）
      ↓
记忆追踪层    ── Compactor 角色弧线专项追踪（防角色工具化）
      ↓
大纲锚点层    ── global_emotional_anchors 强情绪场景规划（防情绪曲线塌陷）
```

---

## 项目结构

```
novelagentv2/
├── main.py                 # CLI 入口
├── docker-compose.yml      # 基础设施（MinIO 唯一外部依赖）
├── requirements.txt
├── config/
│   ├── settings.py         # 所有配置（pydantic-settings，读取 .env）
│   └── prompts.py          # Prompt 模板（含全部约束规则）
├── langgraph_engine/
│   ├── graph.py            # LangGraph 主图定义
│   ├── router.py           # 条件路由
│   └── state.py            # NovelState 定义
├── agents/
│   ├── director.py         # 主线决策（含场景多样性日志）
│   ├── planner.py          # 章节结构设计（含钩子密度/矛盾激活规划）
│   ├── writer.py           # 正文生成（无记忆执行器）
│   ├── editor.py           # 润色（含说明段落清除 / 编辑建议输出）
│   ├── checker.py          # 事实校验（最强模型，7 维度）
│   ├── repair_agent.py     # JSON Patch 自愈修复
│   └── fact_extractor.py   # 结构化事实提取（独立于 Writer）
├── narrative/
│   └── controller.py       # 叙事控制器（节奏/多样性/角色弧线推进）
├── mcp/
│   ├── memory_mcp.py       # 角色事实提交 + 向量存档
│   ├── world_mcp.py        # 世界规则约束
│   ├── foreshadow_mcp.py   # 伏笔状态机（BURIED→ACTIVE→DUE→RESOLVED）
│   ├── style_mcp.py        # 风格约束注入
│   └── reader_mcp.py       # 读者视角反馈
├── context_builder/        # 上下文组装（调用 MCP + 向量检索）
├── compactor/              # 每 10 章压缩（含角色弧线专项追踪）
├── pipeline/               # 高层入口（init_novel / run_volume / run_chapter）
├── db/                     # JSON 文件存储 / 内存缓存 / 本地向量检索 / MinIO 客户端
└── web/                    # FastAPI Web 控制台
```

---

## License

MIT
