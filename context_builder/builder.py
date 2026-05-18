"""
context_builder/builder.py — Context Builder (上下文组装器)

Assembles all MCP data into a unified context snapshot
injected into agent prompts.

Key functions:
  - Filter characters by relevance
  - Build compact fact summaries for prompt injection
  - Run as a LangGraph node between Planner and Writer

Note: Uses ChromaDB embedded for semantic search with graceful fallback.
Data layer is JSON files (file_store) + in-memory cache, not MySQL/Redis.
"""
from loguru import logger
from langgraph_engine.state import NovelState
from mcp import memory_mcp
from db import cache as rc
from config import get_settings


def _load_arc_summaries(novel_id: str, chapter_id: int, interval: int = 20) -> dict:
    """Load all existing arc summaries from world_memory into memory_snapshot.
    
    Arc summaries are compressed every N chapters by the Compactor and stored
    in world_memory as 'arc_summary_{start}_{end}'. This loads the last 3
    into the state so Writer/Planner can see long-range story progression.
    """
    from db import repo
    result = {}
    # Find all existing arc summary keys
    for start in range(1, chapter_id, interval):
        end = min(start + interval - 1, chapter_id)
        key = f"arc_summary_{start}_{end}"
        summary = repo.get_world_memory(novel_id, key)
        if summary and isinstance(summary, dict):
            result[key] = summary
    # Also load character arc status
    char_arc = repo.get_world_memory(novel_id, "character_arc_status")
    if char_arc:
        result["character_arc_status"] = char_arc
    return result


def _seed_vector_store(novel_id: str, chapter_id: int):
    """Batch-index existing facts and summaries into the vector store.
    
    Called once on the first chapter to populate the index with historical data.
    Subsequent chapters add data incrementally via add_facts/add_summaries.
    """
    try:
        from db import repo as _repo
        from db import vector_store as vs
        
        # Index existing facts (all of them)
        all_facts = _repo.get_recent_facts(novel_id, 0, limit=9999)
        if all_facts:
            indexed = vs.add_facts(novel_id, all_facts)
            if indexed:
                logger.info(f"[ContextBuilder] Seeded vector store with {indexed} facts")
        
        # Index existing summaries
        all_summaries = _repo.get_recent_summaries(novel_id, limit=9999)
        if all_summaries:
            indexed = vs.add_summaries(novel_id, all_summaries)
            if indexed:
                logger.info(f"[ContextBuilder] Seeded vector store with {indexed} summaries")

        # Index characters
        from mcp import memory_mcp as _mmcp
        chars = _mmcp.get_all_characters(novel_id)
        if chars:
            indexed = vs.add_characters(novel_id, chars)
            if indexed:
                logger.info(f"[ContextBuilder] Seeded vector store with {indexed} characters")

        # Index world rules
        rules_raw = _repo.get_world_memory(novel_id, "world_rules") or []
        if isinstance(rules_raw, list) and rules_raw:
            indexed = vs.add_rules(novel_id, rules_raw)
            if indexed:
                logger.info(f"[ContextBuilder] Seeded vector store with {indexed} rules")
        elif isinstance(rules_raw, dict):
            rule_list = [{"rule_type": k, "rule_text": v} for k, v in rules_raw.items()]
            if rule_list:
                indexed = vs.add_rules(novel_id, rule_list)
                if indexed:
                    logger.info(f"[ContextBuilder] Seeded vector store with {indexed} rules")
    except Exception as e:
        logger.debug(f"[ContextBuilder] Seed vector store skipped: {e}")


def run(state: NovelState) -> NovelState:
    """LangGraph node: Build and cache full context for Writer."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id
    s = get_settings()

    # 1. Get full memory snapshot from MemoryMCP (may use Redis cache)
    snapshot = memory_mcp.get_snapshot(novel_id, chapter_id)
    state.memory_snapshot.update(snapshot)

    # 2. Load recent summaries (Redis first, then DB fallback)
    recent = rc.get_recent_summaries(novel_id)
    if not recent:
        from db import repo
        recent = repo.get_recent_summaries(novel_id, limit=s.context_recent_chapters)
        rc.set_recent_summaries(novel_id, recent)
    state.recent_summaries = recent

    # Prop-repetition warning (P1-B): flag overused description props to Writer
    prop_warning = _detect_repeated_props(recent, novel_id, chapter_id)
    if prop_warning:
        state.memory_snapshot["prop_warning"] = prop_warning
        logger.info(f"[ContextBuilder] ch{chapter_id}: {prop_warning}")

    # 3.5 Load arc summaries (P0 anti-drift: long-range story context)
    arc_data = _load_arc_summaries(novel_id, chapter_id)
    state.memory_snapshot.update(arc_data)

    # 4. Vector semantic search (Milvus replaced by ChromaDB embedded)
    #    Uses local Chinese embedding model + ChromaDB for persistent semantic search.
    #    Falls back to keyword/recent-fact search when unavailable.
    try:
        from db import vector_store as vs

        # Build query from chapter context
        query_parts = []
        if state.chapter_plan:
            query_parts.append(state.chapter_plan.goal or "")
            query_parts.append(state.chapter_plan.conflict_setup or "")
            query_parts.extend(state.chapter_plan.must_include or [])
        if state.memory_snapshot.get("director_directive", {}):
            dd = state.memory_snapshot["director_directive"]
            if isinstance(dd, dict):
                query_parts.append(dd.get("chapter_direction", ""))
        query = " ".join(p for p in query_parts if p) or state.memory_snapshot.get("last_ending", "")[:100]

        if query:
            # Search semantically relevant facts
            sem_facts = vs.search_facts(
                novel_id,
                query,
                n_results=10,
                max_chapter=chapter_id - 1,
            )
            if sem_facts:
                state.memory_snapshot["semantic_facts"] = sem_facts
                logger.info(
                    f"[ContextBuilder] ch{chapter_id}: "
                    f"{len(sem_facts)} semantic facts from vector search"
                )
            else:
                logger.debug(
                    f"[ContextBuilder] ch{chapter_id}: "
                    f"vector search returned 0 results (query={query[:60]})"
                )

            # Search semantically relevant summaries
            sem_summaries = vs.search_summaries(novel_id, query, n_results=3)
            if sem_summaries:
                state.memory_snapshot["semantic_summaries"] = sem_summaries

            # Seed the index before searching on chapter 1 so historical data is available
            if chapter_id <= 1 and chapter_id > 0:
                _seed_vector_store(novel_id, chapter_id)

            # Search relevant characters (semantic match to chapter context)
            if query:
                sem_chars = vs.search_characters(novel_id, query, n_results=5)
                if sem_chars:
                    state.memory_snapshot["semantic_characters"] = sem_chars

    except Exception as e:
        logger.debug(f"[ContextBuilder] Vector search skipped: {e}")

    # 5. Cache last chapter ending in state for Writer
    last_ending = snapshot.get("world", {}).get("last_chapter_ending", "")
    if not last_ending:
        from db import repo
        last_ending = repo.get_world_memory(novel_id, "last_chapter_ending") or ""
    state.memory_snapshot["last_ending"] = last_ending

    # 6. Inject period anchoring context for urban/rebirth novels (anti-setting-drift)
    era_context = _build_era_context(novel_id, chapter_id)
    if era_context:
        state.memory_snapshot["era_anchor"] = era_context

    logger.info(
        f"[ContextBuilder] ch{chapter_id}: "
        f"{len(state.active_characters)} chars, "
        f"{len(state.recent_summaries)} recent summaries, "
        f"{len(state.foreshadowing_due)} due foreshadows"
    )
    return state


def _detect_repeated_props(summaries: list[dict], novel_id: str = "", chapter_id: int = 0) -> str:
    """Detect overused description props by scanning recent chapter content openings.

    Chapter summaries record goals/objectives, not prose details, so they miss
    narrative-level props. Instead, scan the first 400 chars of recent chapter
    content (the opening always reveals the dominant prop the writer is leaning on).
    Falls back to summary_text scanning when content is unavailable.
    """
    prop_groups = {
        "右肩伤情(右肩/绷带/渗血/伤口)": ["右肩", "绷带", "渗血", "伤口", "肩胛"],
        "握拳/指节": ["握拳", "指节", "拳头", "指甲扎"],
        "沉默咬牙": ["咬牙", "咬住", "牙关"],
    }

    recent_texts = []
    # Prefer actual chapter content over summary text
    if novel_id and chapter_id > 1:
        try:
            from db import repo as _repo
            chapters = _repo.get_recent_chapters(novel_id, chapter_id - 1, limit=5)
            recent_texts = [c.get("content", "")[:400] for c in chapters if c.get("content")]
        except Exception:
            pass
    # Fall back to summaries if content unavailable
    if not recent_texts and summaries:
        recent_texts = [s.get("summary_text", "") for s in summaries[-5:]]

    if not recent_texts:
        return ""

    warnings = []
    for prop_name, keywords in prop_groups.items():
        hit_count = sum(
            1 for text in recent_texts
            if any(kw in text for kw in keywords)
        )
        if hit_count >= 3:
            warnings.append(f"「{prop_name}」近{len(recent_texts)}章出现{hit_count}次")
    if not warnings:
        return ""
    return "近期重复使用描写道具警告：" + "；".join(warnings) + "——本章请换用其他方式表现角色状态"


# ── Era anchoring (anti-setting-drift) ─────────────────────

_ERA_ANCHOR_2014 = """━━ 场景时代锚定（2014年） ━━
当前故事设定在2014年的中国城市，写作时必须严格遵守以下场景规范：
   
【2014年标志性场景】
- 通讯：诺基亚功能机/小米1代/iPhone4s（不是全面屏！）、短信为主微信刚兴起、2G/3G信号
- 支付：现金为主、银行卡刷卡、淘宝刚普及支付宝、没有微信支付
- 交通：绿皮火车/老式K字头、城市公交投币、出租车可招手停、摩的满街跑
- 消费：网吧5-8元/小时、路边摊10元吃饱、城中村房租300-800/月
- 地标：报刊亭（卖手机充值卡）、CD/DVD店、小卖部门口有公用电话
- 网络：宽带2-4M、WiFi刚普及、电脑还是大屁股显示器+主机箱
- 社会：4G刚开始铺开、共享单车还没出现、短视频在萌芽期、比特币500元

【严禁出现的场景元素】（这些属于末世/废墟/科幻设定，禁止使用）
- 废墟、残骸、废塔、地下掩体、废弃工业设施
- 清剿网、守墓人、追踪协议、红外扫描线
- 脊髓直连、神经接口、后颈备用接口、皮下脉络
- 熔渣、冷却矩阵、散热竖井、冷凝液
- 任何形式的"废弃/破败/坍塌"城市景观（这是2014年，不是战后废墟）

【场景选择优先级】
1. 出租屋/宿舍/客厅 ← 优先
2. 办公室/公司/学校
3. 街边/餐馆/网吧/地铁
4. 公园/广场/小区
5. 禁止：废墟/地下/废弃设施
"""


def _build_era_context(novel_id: str, chapter_id: int) -> str:
    """Build period-anchoring context for urban/rebirth novels.

    For novels set in urban China (2010-2020 era), injects specific era details
    so the writer doesn't default to post-apocalyptic or sci-fi settings.
    Returns empty string for fantasy/xianxia/wuxia novels.
    """
    try:
        novel = _repo_getter()(novel_id) if callable(_repo_getter) else None
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            return ""
        world_type = novel.get("world_type", "")
        desc = novel.get("description", "")
        combined = world_type + " " + desc[:500]

        # Only activate for urban/rebirth/modern type novels
        urban_signals = ["都市", "重生", "现代", "校园", "现实"]
        if not any(s in combined for s in urban_signals):
            return ""

        # Detect approximate era from the description
        # Look for year patterns like "2014年"
        import re
        year_matches = re.findall(r'(20[0-9]{2})年', combined)
        year_hint = year_matches[0] + "年" if year_matches else "2010年代"

        # Return era-specific anchor
        if "2014" in combined or "2014" in desc:
            return _ERA_ANCHOR_2014

        return f"""━━ 场景时代锚定（{year_hint}中国城市） ━━
当前故事设定在{year_hint}的中国城市。写作时必须反映该年代的现实生活场景：
- 使用当时主流的通讯/支付/交通方式
- 场景在普通城市环境（住宅/办公室/街道），不是废墟或科幻设施
- 禁止使用末世/废墟/赛博朋克类场景描写
"""
    except Exception:
        return ""


def _repo_getter():
    """Lazy import of repo module to avoid circular imports."""
    try:
        from db import repo
        return repo.get_novel
    except Exception:
        return lambda x: None
