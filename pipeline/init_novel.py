"""
pipeline/init_novel.py — Novel Initialization Pipeline

Orchestrates the one-time setup of a new novel:
  1. World generation (world + power system + characters)
  2. Story outline (4-act structure + foreshadows)
  3. Volume list planning
  4. Writing style config
  5. Author intent + current focus generation + data persistence
"""
import json
from typing import Optional
from loguru import logger
from llm import simple_chat_json, simple_chat
from mcp import memory_mcp, world_mcp, foreshadow_mcp, style_mcp
from db import repo
from config.prompts import (
    WORLD_INIT_SYSTEM, WORLD_INIT_PROMPT,
    STORY_OUTLINE_PROMPT, VOLUME_PLAN_PROMPT,
    AUTHOR_INTENT_PROMPT,
    THEMATIC_CORE_PROMPT, CHARACTER_ALIGNMENT_PROMPT,
    USER_VOLUME_HINT_PROMPT, USER_CHARACTER_EXTRACT_PROMPT,
)


def init_novel(
    novel_id: str,
    title: str,
    description: str,
    world_type: str = "玄幻",
    total_volumes: int = 10,
    template: Optional[str] = None,
) -> dict:
    """
    Full novel initialization. Returns summary dict of what was created.
    Expects the novel record to already exist in DB (created by caller).
    """
    logger.info(f"{'='*60}")
    logger.info(f"🚀 开始初始化小说《{title}》  novel_id={novel_id}")
    logger.info(f"{'='*60}")

    # ── Step 0: Extract user volume hints for downstream use ──
    _extract_user_volume_hints(novel_id, description, total_volumes)

    # ── Step 1: World Generation ─────────────────────────
    logger.info("【1/5】🌍 正在生成世界观、力量体系、角色设定...")
    world_data = simple_chat_json(
        system_prompt=WORLD_INIT_SYSTEM,
        user_prompt=WORLD_INIT_PROMPT.format(user_input=description),
        fallback={"world_name": title, "world_type": world_type, "protagonist": {"name": "主角"}},
    )

    # Store world data in world_memory
    for key in ["world_name", "world_type", "background", "power_system",
                "protagonist", "antagonist", "supporting_characters", "world_setting"]:
        if key in world_data:
            repo.set_world_memory(novel_id, key, world_data[key])

    # Store emotional signature separately for style_mcp access
    if "emotional_signature" in world_data:
        repo.set_world_memory(novel_id, "emotional_signature", world_data["emotional_signature"])

    # Store raw user input (highest priority override)
    repo.set_world_memory(novel_id, "raw_user_input", description)

    # Initialize world rules from world_data
    world_mcp.initialize_world_rules(novel_id, world_data)

    # Store characters in character table
    _store_initial_characters(novel_id, world_data)

    # Step 1b: Ensure all user-named characters are stored
    _ensure_user_characters(novel_id, description)

    logger.info(f"【1/5】✅ 世界观创建完成: 《{world_data.get('world_name', title)}》"
                f"  类型={world_data.get('world_type','?')}"
                f"  主角={world_data.get('protagonist',{}).get('name','?')}")

    # ── Step 2: Story Outline ────────────────────────────
    logger.info("【2/5】📖 正在生成四幕故事大纲、主线伏笔...")

    # Inject genre template if specified
    template_context = ""
    if template:
        from config.genre_templates import get_template, format_template_prompt
        tpl = get_template(template)
        if tpl:
            template_context = format_template_prompt(tpl)
            logger.info(f"  📐 使用类型模板: {tpl['title']}")
        else:
            logger.warning(f"  ⚠️ 未找到模板 '{template}'，使用默认大纲生成")

    volume_hints_for_outline = _build_volume_hints_summary(novel_id, total_volumes)
    outline = simple_chat_json(
        system_prompt="你是故事架构师，为长篇网络小说设计完整的四幕故事大纲，输出JSON。",
        user_prompt=STORY_OUTLINE_PROMPT.format(
            world_data=json.dumps(world_data, ensure_ascii=False)[:2000],
            total_volumes=total_volumes,
            template_context=template_context,
            user_volume_structure=volume_hints_for_outline,
        ),
        fallback={"story_title": title, "core_theme": "热血成长", "act_structure": []},
    )
    repo.set_world_memory(novel_id, "story_outline", outline)
    repo.set_world_memory(novel_id, "novel_title", outline.get("story_title", title))

    # Plant initial foreshadows from outline.
    # due_range_end is estimated from total_volumes * 20 ch/vol (standard webnovel),
    # scaled by resolved_act (1-4 = 25%-100% of total chapters).
    # core/major foreshadows start BURIED so they don't flood the prompt for 200 chapters;
    # they auto-activate when approaching their due window (see foreshadow_mcp.plant).
    _est_total_chapters = total_volumes * 20
    for fshadow in outline.get("main_foreshadows", []):
        importance = fshadow.get("importance", "major")
        resolved_act = fshadow.get("resolved_act", 4)
        due_chapter = max(10, int(resolved_act / 4 * _est_total_chapters))
        foreshadow_mcp.plant(
            novel_id=novel_id,
            chapter_no=1,
            description=fshadow.get("description", ""),
            importance=importance,
            due_range_end=due_chapter,
            extra={"state": "BURIED"} if importance in ("core", "major") else None,
        )

    logger.info(f"【2/5】✅ 故事大纲完成: 《{outline.get('story_title', title)}》"
                f"  主题={outline.get('core_theme','?')}"
                f"  埋伏笔={len(outline.get('main_foreshadows', []))} 处")

    # Step 2a: Thematic core
    thematic_core = _generate_thematic_core(novel_id, world_data, outline)

    # Step 2b: Character-outline alignment validation
    _validate_character_outline_alignment(novel_id, world_data, outline)

    from narrative.arc_planner import init_arc_plan
    try:
        arc_plan = init_arc_plan(novel_id)
        logger.info(f"[InitNovel] Cross-volume arc plan created: {len(arc_plan.get('arc_plan', []))} volumes")
    except Exception as e:
        logger.warning(f"[InitNovel] Arc plan failed (non-fatal): {e}")

    # ── Step 3: Writing Style ────────────────────────────
    logger.info("【3/5】🖊  正在配置写作风格、语言基调...")
    style = _generate_style(world_data, description)
    style_mcp.set_style(novel_id, style)
    # ── Step 4: Volume List Planning ─────────────────────
    logger.info(f"【4/5】📋 正在规划 {total_volumes} 卷目大纲...")
    volumes = _plan_volumes(novel_id, world_data, outline, total_volumes)
    for vol_no, vol_data in enumerate(volumes, 1):
        repo.set_world_memory(novel_id, f"volume_plan_{vol_no}", vol_data)
        from sqlalchemy import text
        from db.json_session import get_db
        with get_db() as db:
            db.execute(text("""
                INSERT INTO volumes (novel_id, volume_no, title, plan_json, status)
                VALUES (:nid, :vno, :title, :plan, 'planned')
                ON DUPLICATE KEY UPDATE title=VALUES(title), plan_json=VALUES(plan_json)
            """), {
                "nid": novel_id,
                "vno": vol_no,
                "title": vol_data.get("volume_title", f"第{vol_no}卷"),
                "plan": json.dumps(vol_data, ensure_ascii=False),
            })

    # ── Step 5: Author Intent + Current Focus ────────────
    logger.info("【5/5】🎯 正在生成作者意图与创作方向...")
    author_intent = simple_chat(
        system_prompt="你是网络小说作者，生成简洁的作者长期意图声明，不超过100字，直接输出文本：",
        user_prompt=AUTHOR_INTENT_PROMPT.format(
            title=title,
            theme=outline.get("core_theme", "热血成长"),
            protagonist=json.dumps(world_data.get("protagonist", {}), ensure_ascii=False)[:200],
            ending=outline.get("ending_direction", "胜利结局"),
            user_input=description[:300],
        ),
    )
    repo.set_world_memory(novel_id, "author_intent", author_intent.strip())

    current_focus = f"第1-2卷重点：建立世界观，主角觉醒，展示主角潜力，埋下主要矛盾伏笔。"
    repo.set_world_memory(novel_id, "current_focus", current_focus)

    # ── Step 6: Update novel status ──────────────────────
    repo.update_novel_status(novel_id, "paused")  # init done, idle/ready to write

    logger.info(f"{'='*60}")
    logger.info(f"🎉 小说《{title}》初始化完成！"
                f"  人物={len(world_data.get('supporting_characters',[]))+2} 个"
                f"  伏笔={len(outline.get('main_foreshadows',[]))} 处"
                f"  共规划 {total_volumes} 卷")
    logger.info(f"{'='*60}")
    return {
        "novel_id": novel_id,
        "title": title,
        "world_name": world_data.get("world_name", title),
        "protagonist": world_data.get("protagonist", {}).get("name", "主角"),
        "total_volumes": total_volumes,
        "foreshadows_planted": len(outline.get("main_foreshadows", [])),
        "characters_created": len(world_data.get("supporting_characters", [])) + 2,
    }


def _generate_thematic_core(novel_id: str, world_data: dict, outline: dict) -> dict:
    proto = world_data.get("protagonist", {})
    try:
        result = simple_chat_json(
            system_prompt="你是小说主题架构师，提炼全书主题核心，输出JSON。",
            user_prompt=THEMATIC_CORE_PROMPT.format(
                outline_json=json.dumps(outline, ensure_ascii=False)[:2000],
                protagonist_name=proto.get("name", "主角"),
                protagonist_want=proto.get("want", ""),
                protagonist_fear=proto.get("fear", ""),
                protagonist_contradiction=proto.get("contradiction", ""),
            ),
            fallback={},
        )
        if result:
            repo.set_world_memory(novel_id, "thematic_core", result)
            logger.info(f"[InitNovel] Thematic core generated: {result.get('central_question', '')[:60]}")
        return result
    except Exception as e:
        logger.warning(f"[InitNovel] Thematic core generation failed (non-fatal): {e}")
        return {}


def _validate_character_outline_alignment(novel_id: str, world_data: dict, outline: dict) -> None:
    proto = world_data.get("protagonist", {})
    antag = world_data.get("antagonist", {})
    act_structure = outline.get("act_structure", [])
    protagonist_growth_arc = " → ".join(
        a.get("protagonist_growth", "") for a in act_structure if a.get("protagonist_growth")
    )
    try:
        result = simple_chat_json(
            system_prompt="你是小说人设审查专家，检查人设与故事大纲的对齐度，输出JSON。",
            user_prompt=CHARACTER_ALIGNMENT_PROMPT.format(
                core_theme=outline.get("core_theme", ""),
                ending_direction=outline.get("ending_direction", ""),
                protagonist_growth_arc=protagonist_growth_arc[:300],
                protagonist_json=json.dumps({
                    "name": proto.get("name", ""),
                    "want": proto.get("want", ""),
                    "fear": proto.get("fear", ""),
                    "contradiction": proto.get("contradiction", ""),
                }, ensure_ascii=False),
                antagonist_json=json.dumps({
                    "name": antag.get("name", ""),
                    "want": antag.get("want", ""),
                    "fear": antag.get("fear", ""),
                    "contradiction": antag.get("contradiction", ""),
                }, ensure_ascii=False),
            ),
            fallback={"aligned": True},
        )
        if not result:
            return
        logger.info(f"[InitNovel] Character alignment check: aligned={result.get('aligned', True)}")

        proto_adj = result.get("protagonist_adjustments", {})
        if proto_adj and not result.get("aligned", True):
            proto_id = _name_to_id(proto.get("name", "protagonist"))
            existing = repo.get_character(novel_id, proto_id)
            if existing:
                extra = {**existing.get("extra", {})}
                if proto_adj.get("want"):
                    extra["want"] = proto_adj["want"]
                if proto_adj.get("fear"):
                    extra["fear"] = proto_adj["fear"]
                if proto_adj.get("contradiction"):
                    extra["contradiction"] = proto_adj["contradiction"]
                extra["arc_stage"] = "初心"
                repo.upsert_character(novel_id, proto_id, {**existing, "extra": extra})
                logger.info(f"[InitNovel] Protagonist motivations adjusted for outline alignment")
        else:
            proto_id = _name_to_id(proto.get("name", "protagonist"))
            existing = repo.get_character(novel_id, proto_id)
            if existing:
                extra = {**existing.get("extra", {}), "arc_stage": "初心"}
                repo.upsert_character(novel_id, proto_id, {**existing, "extra": extra})

        antag_adj = result.get("antagonist_adjustments", {})
        if antag_adj and not result.get("aligned", True):
            antag_id = _name_to_id(antag.get("name", "antagonist"))
            existing = repo.get_character(novel_id, antag_id)
            if existing:
                extra = {**existing.get("extra", {})}
                if antag_adj.get("want"):
                    extra["want"] = antag_adj["want"]
                if antag_adj.get("fear"):
                    extra["fear"] = antag_adj["fear"]
                if antag_adj.get("contradiction"):
                    extra["contradiction"] = antag_adj["contradiction"]
                repo.upsert_character(novel_id, antag_id, {**existing, "extra": extra})
    except Exception as e:
        logger.warning(f"[InitNovel] Character alignment validation failed (non-fatal): {e}")


def _store_initial_characters(novel_id: str, world_data: dict):
    """Store protagonist, antagonist, and supporting characters."""
    proto = world_data.get("protagonist", {})
    if proto:
        char_id = _name_to_id(proto.get("name", "protagonist"))
        repo.upsert_character(novel_id, char_id, {
            "name": proto.get("name", "主角"),
            "char_id": char_id,
            "status": "alive",
            "personality": [proto.get("core_trait", "热血")],
            "power_level": proto.get("initial_power", ""),
            "location": "",
            "emotion_state": "坚定",
            "physical_state": "健康",
            "appearance": proto.get("appearance", ""),
            "backstory": proto.get("background", ""),
            "relationships": {},
            "aliases": [],
            "chapters_active": [1],
            "emotion_expression": proto.get("emotion_expression", {}),
            "extra": {
                "goal": proto.get("goal", ""),
                "special_ability": proto.get("special_ability", ""),
                "gender": proto.get("gender", "男"),
                "want": proto.get("want", ""),
                "fear": proto.get("fear", ""),
                "contradiction": proto.get("contradiction", ""),
            },
        })

    antag = world_data.get("antagonist", {})
    if antag:
        char_id = _name_to_id(antag.get("name", "antagonist"))
        repo.upsert_character(novel_id, char_id, {
            "name": antag.get("name", "反派"),
            "char_id": char_id,
            "status": "alive",
            "personality": ["阴险", "强大"],
            "power_level": antag.get("power", ""),
            "location": "",
            "emotion_state": "冷酷",
            "physical_state": "健康",
            "appearance": antag.get("appearance", ""),
            "backstory": antag.get("background", ""),
            "relationships": {},
            "aliases": [],
            "chapters_active": [],
            "emotion_expression": antag.get("emotion_expression", {}),
            "extra": {
                "motivation": antag.get("motivation", ""),
                "want": antag.get("want", ""),
                "fear": antag.get("fear", ""),
                "contradiction": antag.get("contradiction", ""),
            },
        })

    for sc in world_data.get("supporting_characters", []):
        char_id = _name_to_id(sc.get("name", "npc"))
        proto_name = world_data.get("protagonist", {}).get("name", "")
        repo.upsert_character(novel_id, char_id, {
            "name": sc.get("name", ""),
            "char_id": char_id,
            "status": "alive",
            "personality": [sc.get("trait", "")],
            "power_level": "",
            "location": "",
            "emotion_state": "平静",
            "physical_state": "健康",
            "appearance": sc.get("appearance", ""),
            "backstory": "",
            "relationships": {proto_name: sc.get("relationship", sc.get("role", ""))} if proto_name else {},
            "aliases": [],
            "chapters_active": [],
            "emotion_expression": sc.get("emotion_expression", {}),
            "extra": {
                "role": sc.get("role", ""),
                "want": sc.get("want", ""),
                "fear": sc.get("fear", ""),
                "contradiction": sc.get("contradiction", ""),
            },
        })


def _generate_style(world_data: dict, description: str) -> dict:
    """Generate writing style config based on world type and emotional signature."""
    world_type = world_data.get("world_type", "玄幻")
    emotional_sig = world_data.get("emotional_signature", {})

    # Use LLM-generated core emotion as overall_tone if available
    default_tone = "热血成长" if "玄幻" in world_type or "修仙" in world_type else "悬疑紧张"
    overall_tone = emotional_sig.get("core_emotion") or default_tone

    style = {
        "overall_tone": overall_tone,
        "emotional_signature": emotional_sig,
        "dialogue_ratio": 0.35,
        "emotion_density": "medium",
        "action_speed": "medium",
        "narration_type": "immersive",
        "sentence_length": "mixed",
        "forbidden_expressions": [
            "副本", "BOSS", "掉落", "刷怪", "血条", "技能CD", "经验值",
            "如果你", "欲知后事如何",
        ],
        "preferred_expressions": {
            "秘境": ["副本"],
            "强敌": ["BOSS"],
            "战利品": ["掉落"],
        },
        "chapter_requirements": {
            "min_conflicts": 1,
            "min_advances": 1,
            "min_emotion_changes": 1,
            "min_foreshadow_ops": 1,
        },
    }
    return style


def _plan_volumes(novel_id: str, world_data: dict, outline: dict, total_volumes: int) -> list[dict]:
    """Generate basic volume list (light planning, full detail per volume when writing)."""
    act_structure = outline.get("act_structure", [])
    volumes = []
    for i in range(1, total_volumes + 1):
        act = next((a for a in act_structure if _vol_in_act(i, a.get("volumes", ""))), {})
        user_hint = repo.get_world_memory(novel_id, f"user_volume_hint_{i}") or {}
        volume_goal = (
            user_hint.get("core_goal")
            or act.get("goal", f"推进第{i}卷主线")
        )
        arc_notes = (
            user_hint.get("tone_hint")
            or user_hint.get("climax_hint")
            or act.get("conflict", "")
        )
        title_hint = user_hint.get("title_hint", "")
        volumes.append({
            "volume_no": i,
            "volume_title": title_hint or f"第{i}卷",
            "volume_goal": volume_goal,
            "power_cap": _get_power_cap(outline, i),
            "arc_notes": arc_notes,
            "ending_hook": user_hint.get("ending_hint", ""),
            "chapter_outlines": [],  # filled lazily before writing
        })

    # Generate meaningful titles for all volumes in one LLM call
    volumes = _name_volumes(volumes, outline, world_data)
    return volumes


def _name_volumes(volumes: list[dict], outline: dict, world_data: dict) -> list[dict]:
    """
    Use a single LLM call to assign concise, evocative titles to all volumes.
    Falls back to generic placeholders gracefully.
    """
    if not volumes:
        return volumes

    vol_summaries = "\n".join(
        f"第{v['volume_no']}卷 | 目标：{v['volume_goal']} | 冲突：{v['arc_notes'][:60]}"
        for v in volumes
    )

    result = simple_chat_json(
        system_prompt=(
            "你是网络小说策划，负责为各卷起简洁有力的卷名。"
            "卷名风格：4-12个汉字，意境强、概括本卷核心矛盾或主角状态。"
            "禁止使用'第X卷'格式作为卷名。只输出JSON。"
        ),
        user_prompt=(
            f"小说类型：{world_data.get('world_type', '玄幻')}\n"
            f"核心主题：{outline.get('core_theme', '')}\n\n"
            f"各卷信息：\n{vol_summaries}\n\n"
            "请为每卷生成卷名，输出JSON格式：\n"
            '{"titles": ["第1卷卷名", "第2卷卷名", ...]}'
        ),
        fallback={"titles": []},
    )

    titles = result.get("titles", [])
    for i, v in enumerate(volumes):
        if i < len(titles) and isinstance(titles[i], str) and titles[i].strip():
            v["volume_title"] = titles[i].strip()
        # else keep the "第X卷" placeholder
    return volumes


def _vol_in_act(vol_no: int, vol_range: str) -> bool:
    """Check if volume number falls within an act's volume range string like '第1-3卷'."""
    try:
        import re
        nums = re.findall(r"\d+", str(vol_range))
        if len(nums) >= 2:
            return int(nums[0]) <= vol_no <= int(nums[1])
        elif len(nums) == 1:
            return vol_no == int(nums[0])
    except Exception:
        pass
    return False


def _get_power_cap(outline: dict, vol_no: int) -> str:
    for milestone in outline.get("power_milestones", []):
        if milestone.get("volume") == vol_no:
            return milestone.get("level", "")
    return ""


def _extract_user_volume_hints(novel_id: str, description: str, total_volumes: int) -> None:
    """Extract and store per-volume hints from user description (non-fatal)."""
    try:
        result = simple_chat_json(
            system_prompt="你是小说策划助手，从用户描述中提取每卷的核心规划信息，输出JSON。",
            user_prompt=USER_VOLUME_HINT_PROMPT.format(
                user_input=description[:6000],
                total_volumes=total_volumes,
            ),
            fallback={"volume_hints": []},
        )
        for vh in result.get("volume_hints", []):
            vno = vh.get("volume_no")
            if vno:
                repo.set_world_memory(novel_id, f"user_volume_hint_{vno}", vh)
        logger.info(f"[InitNovel] User volume hints extracted: {len(result.get('volume_hints', []))} volumes")
    except Exception as e:
        logger.warning(f"[InitNovel] Volume hint extraction failed (non-fatal): {e}")


def _build_volume_hints_summary(novel_id: str, total_volumes: int) -> str:
    """Build a text summary of per-volume user hints for prompt injection."""
    parts = []
    for i in range(1, min(total_volumes + 1, 11)):
        vh = repo.get_world_memory(novel_id, f"user_volume_hint_{i}") or {}
        if vh:
            goal = vh.get("core_goal", "")
            title = vh.get("title_hint", "")
            if goal or title:
                parts.append(f"第{i}卷 {title}: {goal}")
    return "\n".join(parts) if parts else "（用户未提供详细卷结构）"


def _ensure_user_characters(novel_id: str, description: str) -> None:
    """Ensure all named characters from user description are stored (non-fatal)."""
    try:
        existing = repo.get_all_characters(novel_id)
        existing_names = [c.get("name", "") for c in existing]
        existing_str = "、".join(existing_names)

        result = simple_chat_json(
            system_prompt="你是角色提取助手，从用户描述中找出所有命名角色，输出JSON。",
            user_prompt=USER_CHARACTER_EXTRACT_PROMPT.format(
                user_input=description[:5000],
                existing_chars=existing_str,
            ),
            fallback={"missing_characters": []},
        )

        added = 0
        for char in result.get("missing_characters", []):
            name = char.get("name", "").strip()
            char_id = char.get("char_id") or _name_to_id(name)
            if not name:
                continue
            if repo.get_character(novel_id, char_id):
                continue
            repo.upsert_character(novel_id, char_id, {
                "char_id": char_id,
                "name": name,
                "status": char.get("status", "alive"),
                "personality": char.get("personality", []),
                "power_level": "",
                "location": "",
                "emotion_state": "平静",
                "physical_state": "健康",
                "backstory": char.get("backstory", ""),
                "relationships": {
                    "主角": char.get("relationship_to_protagonist", char.get("role", ""))
                },
                "aliases": [],
                "chapters_active": [],
                "emotion_expression": {},
                "extra": {
                    "role": char.get("role", ""),
                    "want": char.get("want", ""),
                    "fear": char.get("fear", ""),
                    "contradiction": char.get("contradiction", ""),
                },
            })
            added += 1
        logger.info(f"[InitNovel] User character extraction: added {added} missing characters")
    except Exception as e:
        logger.warning(f"[InitNovel] Character extraction failed (non-fatal): {e}")


def _name_to_id(name: str) -> str:
    """Convert Chinese name to safe ID."""
    import re
    return re.sub(r"[^\w]", "_", name.strip().lower()) or "char"
