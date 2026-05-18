"""
pipeline/run_novel.py — Novel Writing Runner

Orchestrates multi-volume, multi-chapter generation.
Provides:
  - run_volume(novel_id, volume_no) — write one full volume
  - run_chapter(novel_id, chapter_no, volume_no) — write one chapter
  - auto_run(novel_id, start_volume, max_volumes) — continuous run with circuit breaker
"""
import json
import time
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from langgraph_engine.state import NovelState
from langgraph_engine.graph import run_chapter as graph_run_chapter, ImmediateStopException
from db import repo
from db import cache as rc
from db.novel_log import log_info, log_warn, log_error
from mcp import memory_mcp
from config import get_settings
from config.prompts import VOLUME_PLAN_PROMPT
from llm.client import set_novel_context, clear_novel_context


class SkipVolumeException(Exception):
    """Raised by interactive volume approval to skip writing the current volume."""


def run_chapter(novel_id: str, chapter_no: int, volume_no: int) -> dict:
    """
    Run the full chapter generation pipeline for one chapter.
    Returns a result dict with status, word_count, issues.
    """
    s = get_settings()
    novel = repo.get_novel(novel_id)
    if not novel:
        raise ValueError(f"Novel {novel_id} not found")

    # ── Ending chapter: boost retry budget ─────────────────
    max_retries = s.max_retry_per_chapter
    total_volumes = novel.get("total_volumes", 10)
    if volume_no == total_volumes:
        volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}")
        outlines = (volume_plan or {}).get("chapter_outlines", [])
        if outlines and chapter_no == outlines[-1].get("chapter_no"):
            max_retries = 5
            logger.info(
                f"🏁 结尾章节检测: 第{chapter_no}章 (第{volume_no}卷最后)"
                f"  重试次数提升至 {max_retries}"
            )

    logger.info(f"── 开始生成 第{chapter_no}章 (第{volume_no}卷) ──")
    log_info(novel_id, f"📝 开始生成 第{chapter_no}章 (第{volume_no}卷)")

    # Build initial state
    state = NovelState(
        novel_id=novel_id,
        chapter_id=chapter_no,
        volume_no=volume_no,
        world_id=novel_id,
        max_retries=max_retries,
    )

    start_time = time.time()

    # Run the LangGraph pipeline
    set_novel_context(novel_id)
    try:
        final_state = graph_run_chapter(state)
    except ImmediateStopException:
        clear_novel_context()
        logger.info(f"🛑 第{chapter_no}章被立即中断（停止信号）")
        log_warn(novel_id, f"🛑 第{chapter_no}章被立即中断（停止信号）")
        # Clean up any partial chapter data — leave status as pending so it can be retried
        repo.upsert_chapter(novel_id, chapter_no, {
            "volume_no": volume_no,
            "status": "pending",
            "content": "",
            "word_count": 0,
        })
        return {"status": "stopped", "chapter_no": chapter_no, "volume_no": volume_no}
    except Exception as e:
        clear_novel_context()
        logger.error(f"❌ 第{chapter_no}章生成致命错误: {e}")
        log_error(novel_id, f"❌ 第{chapter_no}章生成出错: {e}")
        repo.upsert_chapter(novel_id, chapter_no, {
            "volume_no": volume_no,
            "status": "failed",
            "issues": [{"code": "FATAL_ERROR", "desc": str(e)}],
        })
        return {"status": "failed", "error": str(e), "chapter_no": chapter_no}
    finally:
        clear_novel_context()

    elapsed = time.time() - start_time
    final_text = final_state.final_text or final_state.edited_text or final_state.draft_text
    word_count = len(final_text)
    issues_count = len(final_state.issues)

    logger.info(
        f"✅ 第{chapter_no}章完成: {word_count} 字"
        f"  残留问题={issues_count} 个  耗时={elapsed:.1f}s"
    )
    log_info(novel_id, f"✅ 第{chapter_no}章完成: {word_count} 字  ({elapsed:.0f}s)")

    return {
        "status": "done" if not final_state.error else "failed",
        "chapter_no": chapter_no,
        "volume_no": volume_no,
        "word_count": word_count,
        "issues_count": issues_count,
        "elapsed_seconds": round(elapsed, 1),
        "error": final_state.error,
    }


def _show_volume_plan_and_confirm(plan: dict, volume_no: int) -> None:
    """Print the volume plan and prompt the user to confirm, skip, or abort.

    Raises:
        SkipVolumeException: if the user enters 'n'.
        SystemExit(0): if the user enters 'q'.
    """
    title = plan.get("volume_title", f"第{volume_no}卷")
    goal = plan.get("volume_goal", plan.get("goal", "（未设定）"))
    outlines = plan.get("chapter_outlines", [])
    total = len(outlines)

    print(f"\n{'─'*60}")
    print(f"  第{volume_no}卷：{title}")
    print(f"  卷目标：{goal}")
    print(f"  共 {total} 章")
    print(f"{'─'*60}")

    preview = outlines[:5]
    if preview:
        print(f"  {'章节':>4}  {'标题':<20}  关键事件")
        print(f"  {'----':>4}  {'----':<20}  --------")
        for outline in preview:
            ch_no = outline.get("chapter_no", "?")
            ch_title = str(outline.get("title", ""))[:20]
            key_event = str(outline.get("key_event", outline.get("description", "")))[:40]
            print(f"  {ch_no:>4}  {ch_title:<20}  {key_event}")

    print(f"{'─'*60}")
    print("[Enter] 确认开始写作  [n] 跳过此卷  [q] 中止")

    try:
        choice = input("> ").strip().lower()
    except EOFError:
        choice = ""

    if choice == "q":
        raise SystemExit(0)
    if choice == "n":
        raise SkipVolumeException(f"用户跳过第{volume_no}卷")


def run_volume(novel_id: str, volume_no: int, force_replan: bool = False, interactive: bool = False) -> dict:
    """
    Run a full volume. Generates chapter outlines if needed, then writes chapter by chapter.
    """
    s = get_settings()
    logger.info(f"{'█'*10} 开始写 第{volume_no}卷 {'█'*10}")
    log_info(novel_id, f"📚 {'='*10} 开始写 第{volume_no}卷 {'='*10}")

    # ── 结尾检测：如果已标记故事完结，跳过此卷 ────────
    previous_ending = repo.get_world_memory(novel_id, f"ending_check_v{volume_no - 1}")
    if previous_ending and previous_ending.get("is_ending"):
        logger.info(
            f"🏁 第{volume_no - 1}卷已检测到故事完结，跳过第{volume_no}卷写入 "
            f"(confidence={previous_ending.get('confidence','?')})"
        )
        repo.update_novel_status(novel_id, "done")
        return {
            "status": "story_ended",
            "volume_no": volume_no,
            "reason": previous_ending.get("reason", "上一卷已判定故事完结"),
        }

    # Ensure volume plan exists
    plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}")
    if not plan or force_replan or not plan.get("chapter_outlines"):
        logger.info(f"📋 正在规划第{volume_no}卷章节大纲...")
        log_info(novel_id, f"📋 正在规划第{volume_no}卷章节大纲...")
        novel_record = repo.get_novel(novel_id)
        total_volumes = novel_record.get("total_volumes", 10) if novel_record else 10
        plan = _plan_volume(novel_id, volume_no, total_volumes)

    if interactive:
        try:
            _show_volume_plan_and_confirm(plan, volume_no)
        except SkipVolumeException:
            logger.info(f"⏭  用户跳过第{volume_no}卷")
            return {"status": "skipped", "volume_no": volume_no}

    chapter_outlines = plan.get("chapter_outlines", [])
    if not chapter_outlines:
        if not force_replan:
            # Plan was cached but empty — retry with forced replan once
            logger.warning(f"⚠️  第{volume_no}卷章节大纲为空，强制重新规划...")
            log_info(novel_id, f"⚠️  第{volume_no}卷章节大纲为空，强制重新规划...")
            novel_record = repo.get_novel(novel_id)
            total_volumes = novel_record.get("total_volumes", 10) if novel_record else 10
            plan = _plan_volume(novel_id, volume_no, total_volumes)
            chapter_outlines = plan.get("chapter_outlines", [])
        if not chapter_outlines:
            logger.error(f"❌ 第{volume_no}卷章节大纲重新规划后仍为空，放弃写作")
            return {"status": "failed", "error": "no chapter outlines after replan"}

    # Determine starting chapter
    start_chapter = _get_volume_start_chapter(novel_id, volume_no)
    total = len(chapter_outlines)
    done = 0
    failed = 0
    total_words = 0
    was_stopped = False

    for outline in chapter_outlines:
        chapter_no = outline.get("chapter_no", start_chapter + done)
        actual_chapter_no = start_chapter + (done + failed)

        # Check stop flag before each chapter.
        # NOTE: do NOT clear the flag here — let the outermost caller (auto_run or
        # _bg_write_volume) clear it, so that auto_run can also detect the stop.
        if rc.is_stop_requested(novel_id):
            logger.info(f"🛑 停止信号已收到，中断第{volume_no}卷写作 (在第{actual_chapter_no}章前)")
            log_warn(novel_id, f"🛑 用户中断第{volume_no}卷 (第{actual_chapter_no}章前)")
            rc.clear_current_task(novel_id)
            was_stopped = True
            break

        # Skip if already done
        existing = repo.get_chapter(novel_id, actual_chapter_no)
        if existing and existing.get("status") in ("done", "human_edited"):
            logger.info(f"⏭  第{actual_chapter_no}章已完成(human_edited={existing.get('status')=='human_edited'})，跳过")
            log_info(novel_id, f"⏭  第{actual_chapter_no}章已存在（跳过）")
            done += 1
            total_words += existing.get("word_count", 0)
            continue

        # Track current task in Redis for the status API
        rc.set_current_task(novel_id, {
            "type": "write_volume",
            "volume_no": volume_no,
            "chapter_no": actual_chapter_no,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

        result = run_chapter(novel_id, actual_chapter_no, volume_no)

        if result["status"] == "stopped":
            logger.info(f"🛑 第{actual_chapter_no}章被立即中断，停止第{volume_no}卷写作")
            rc.clear_current_task(novel_id)
            was_stopped = True
            break
        elif result["status"] == "done":
            done += 1
            total_words += result.get("word_count", 0)
            log_info(novel_id, f"📊 第{volume_no}卷进度: {done}/{total}章")
        else:
            failed += 1
            logger.warning(f"⚠️  第{actual_chapter_no}章生成失败: {result.get('error')}")
            log_warn(novel_id, f"⚠️  第{actual_chapter_no}章生成失败")

    # Post-volume: update volume status.
    # Skip _update_current_focus when stopped early — avoid an extra slow LLM call.
    rc.clear_current_task(novel_id)
    from sqlalchemy import text
    from db.json_session import get_db
    with get_db() as db:
        db.execute(text(
            "UPDATE volumes SET status='done' WHERE novel_id=:nid AND volume_no=:vno"
        ), {"nid": novel_id, "vno": volume_no})

    if not was_stopped:
        _update_current_focus(novel_id, volume_no)

    logger.info(
        f"{'█'*10} 第{volume_no}卷写作{'被中断' if was_stopped else '完成'}: "
        f"成功={done}/{total} 章  失败={failed} 章  共{total_words}字 {'█'*10}"
    )

    if not was_stopped and done > 0:
        try:
            review = _volume_literary_review(novel_id, volume_no)
            logger.info(
                f"📖 [卷级终审] 第{volume_no}卷: "
                f"{len(review.get('critiques', []))} 个批评，"
                f"{len(review.get('next_volume_guidance', []))} 条下卷建议"
            )
        except Exception as _rev_err:
            logger.warning(f"[run_volume] Literary review failed (non-fatal): {_rev_err}")

        try:
            from eval.constory_bench import evaluate_volume as _eval_vol
            bench = _eval_vol(novel_id, volume_no)
            _bench_lines = (bench.get("summary") or "").splitlines()
            logger.info(f"[ConStoryBench] 第{volume_no}卷: {_bench_lines[0] if _bench_lines else '完成'}")
        except Exception as _bench_err:
            logger.debug(f"[run_volume] ConStory-Bench skipped: {_bench_err}")

    return {
        "status": "stopped" if was_stopped else "done",
        "volume_no": volume_no,
        "chapters_done": done,
        "chapters_failed": failed,
        "total_words": total_words,
    }


def rewrite_volume(novel_id: str, volume_no: int) -> dict:
    """
    Rewrite an entire volume from scratch.

    Cleans up all JSON file and cache data for the volume's chapters,
    resets related foreshadow states, then re-runs the full writing pipeline.

    Returns the same result dict as run_volume(), plus 'cleanup' info.
    """
    novel = repo.get_novel(novel_id)
    if not novel:
        raise ValueError(f"Novel {novel_id} not found")

    logger.info(f"{'█'*10} 整卷重写: 第{volume_no}卷 {'█'*10}")

    # ── 1. Determine actual chapter range from DB ──────────────────────
    from sqlalchemy import text
    from db.json_session import get_db
    with get_db() as db:
        row = db.execute(text(
            "SELECT MIN(chapter_no) as min_ch, MAX(chapter_no) as max_ch "
            "FROM chapters WHERE novel_id=:nid AND volume_no=:vno"
        ), {"nid": novel_id, "vno": volume_no}).mappings().first()

    if row and row["min_ch"] is not None:
        chapter_start = int(row["min_ch"])
        chapter_end = int(row["max_ch"])
        logger.info(f"发现现有章节范围: 第{chapter_start}-{chapter_end}章")
    else:
        # No chapters yet; compute range from plan or default
        chapter_start = _get_volume_start_chapter(novel_id, volume_no)
        plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}")
        num_chapters = len(plan.get("chapter_outlines", [])) if plan else 10
        chapter_end = chapter_start + num_chapters - 1
        logger.info(f"无现有章节，预计范围: 第{chapter_start}-{chapter_end}章")

    # ── 2. Check for later volumes (warn but proceed) ─────────────────
    with get_db() as db:
        later = db.execute(text(
            "SELECT COUNT(*) FROM chapters WHERE novel_id=:nid AND volume_no > :vno"
        ), {"nid": novel_id, "vno": volume_no}).scalar() or 0

    if later:
        logger.warning(
            f"⚠️  存在后续卷的章节({later}章)，重写第{volume_no}卷后这些章节将与新内容不一致"
        )

    # ── 3. Clean up all data for this volume ──────────────────────────
    cleanup_result = _cleanup_volume_data(novel_id, volume_no, chapter_start, chapter_end)

    # ── 4. Re-run volume writing ──────────────────────────────────────
    result = run_volume(novel_id, volume_no, force_replan=True)
    result["cleanup"] = cleanup_result
    result["later_volumes_warning"] = later > 0

    return result


def _cleanup_volume_data(novel_id: str, volume_no: int,
                          chapter_start: int, chapter_end: int) -> dict:
    """
    Delete all data associated with volume_no's chapters and reset related state.
    Returns a summary of what was cleaned.
    """
    from sqlalchemy import text
    from db.json_session import get_db
    from db import cache

    chapter_nos = list(range(chapter_start, chapter_end + 1))
    summary = {
        "volume_no": volume_no,
        "chapter_range": [chapter_start, chapter_end],
        "chapters_deleted": 0,
        "summaries_deleted": 0,
        "facts_deleted": 0,
        "metrics_deleted": 0,
        "foreshadows_deleted": 0,
        "foreshadows_reset": 0,
        "vector_facts_deleted": 0,
        "vector_summaries_deleted": 0,
    }

    with get_db() as db:
        # Delete chapters for this volume
        r = db.execute(text(
            "DELETE FROM chapters WHERE novel_id=:nid AND volume_no=:vno"
        ), {"nid": novel_id, "vno": volume_no})
        summary["chapters_deleted"] = r.rowcount

        # Delete chapter summaries
        r = db.execute(text(
            "DELETE FROM chapter_summaries WHERE novel_id=:nid AND volume_no=:vno"
        ), {"nid": novel_id, "vno": volume_no})
        summary["summaries_deleted"] = r.rowcount

        # Delete chapter facts
        r = db.execute(text(
            "DELETE FROM chapter_facts WHERE novel_id=:nid AND chapter_no BETWEEN :cs AND :ce"
        ), {"nid": novel_id, "cs": chapter_start, "ce": chapter_end})
        summary["facts_deleted"] = r.rowcount

        # Delete reader metrics
        r = db.execute(text(
            "DELETE FROM reader_metrics WHERE novel_id=:nid AND chapter_no BETWEEN :cs AND :ce"
        ), {"nid": novel_id, "cs": chapter_start, "ce": chapter_end})
        summary["metrics_deleted"] = r.rowcount

        # Delete foreshadows planted during this volume (they'll be re-created)
        r = db.execute(text(
            "DELETE FROM foreshadowing WHERE novel_id=:nid "
            "AND buried_chapter BETWEEN :cs AND :ce"
        ), {"nid": novel_id, "cs": chapter_start, "ce": chapter_end})
        summary["foreshadows_deleted"] = r.rowcount

        # Reset foreshadows that were resolved/activated during this volume
        r = db.execute(text(
            "UPDATE foreshadowing SET state='BURIED', resolve_chapter=NULL "
            "WHERE novel_id=:nid AND resolve_chapter BETWEEN :cs AND :ce"
        ), {"nid": novel_id, "cs": chapter_start, "ce": chapter_end})
        summary["foreshadows_reset"] += r.rowcount

        # Reset volume status to planned
        db.execute(text(
            "UPDATE volumes SET status='planned' WHERE novel_id=:nid AND volume_no=:vno"
        ), {"nid": novel_id, "vno": volume_no})

    # ── Reset last_chapter_ending ──────────────────────────────────
    if volume_no == 1:
        repo.set_world_memory(novel_id, "last_chapter_ending", "故事开端")
    else:
        with get_db() as db:
            prev_summary_text = db.execute(text(
                "SELECT summary_text FROM chapter_summaries "
                "WHERE novel_id=:nid AND volume_no=:prev ORDER BY chapter_no DESC LIMIT 1"
            ), {"nid": novel_id, "prev": volume_no - 1}).scalar()
        if prev_summary_text:
            repo.set_world_memory(novel_id, "last_chapter_ending", prev_summary_text[:500])

    # ── Redis cache cleanup ────────────────────────────────────────
    for chapter_no in chapter_nos:
        cache.rdel(cache.novel_key(novel_id, f"ctx:{chapter_no}"))
    cache.rdel(cache.novel_key(novel_id, "recent_summaries"))
    cache.rdel(cache.novel_key(novel_id, "narrative_state"))

    # ── Vector store cleanup ───────────────────────────────────────
    try:
        from db import vector_store as _vs
        vec_facts = _vs.delete_facts_by_chapter_range(novel_id, chapter_start, chapter_end)
        vec_summaries = _vs.delete_summaries_by_chapter_range(novel_id, chapter_start, chapter_end)
        summary["vector_facts_deleted"] = vec_facts
        summary["vector_summaries_deleted"] = vec_summaries
    except Exception as _ve:
        logger.warning(f"[Cleanup] Vector cleanup failed (non-fatal): {_ve}")

    logger.info(
        f"清理完成: 删除章节={summary['chapters_deleted']} "
        f"摘要={summary['summaries_deleted']} "
        f"事实={summary['facts_deleted']} "
        f"伏笔删除={summary['foreshadows_deleted']} "
        f"伏笔重置={summary['foreshadows_reset']} "
    )
    return summary


def auto_run(
    novel_id: str,
    start_volume: int = 1,
    max_volumes: Optional[int] = None,
    circuit_breaker_limit: int = 3,
) -> dict:
    """
    Continuous multi-volume run with circuit breaker and ending detection.
    Stops if:
      - consecutive_failures >= circuit_breaker_limit
      - stop signal received
      - story ending detected by LLM evaluation
      - max_volumes limit reached AND story ending confirmed
    """
    novel = repo.get_novel(novel_id)
    if not novel:
        raise ValueError(f"Novel {novel_id} not found")

    total_volumes = max_volumes or novel["total_volumes"]
    # Allow up to 50% extra volumes beyond the plan if story isn't done
    max_iterations = int(total_volumes * 1.5)
    consecutive_failures = 0
    results = []
    volumes_written = 0

    for vol_no in range(start_volume, start_volume + max_iterations):
        if consecutive_failures >= circuit_breaker_limit:
            logger.error(f"🛑 连续失败次数触发熔断，停止自动写作 (当前卷={vol_no})")
            break

        # ── Dynamic volume limit: if we've written all planned volumes,
        #     check if story is really done before continuing ──
        if volumes_written >= total_volumes:
            # Beyond planned: do an extra ending check
            extra_check = _check_story_ending(novel_id, vol_no)
            if extra_check["is_ending"]:
                logger.info(
                    f"🏁 已写满规划{total_volumes}卷，故事已自然完结，停止生成 "
                    f"(confidence={extra_check['confidence']})"
                )
                repo.update_novel_status(novel_id, "done")
                break
            else:
                logger.info(
                    f"📖 已写满规划{total_volumes}卷，但故事尚未完结 "
                    f"(原因: {extra_check['reason']})，继续生成第{vol_no}卷"
                )

        if rc.is_stop_requested(novel_id):
            logger.info(f"🛑 停止信号已收到，中断自动写作 (在第{vol_no}卷前)")
            rc.clear_stop(novel_id)
            rc.clear_current_task(novel_id)
            break

        # ── 结尾检测：每卷之前检查故事是否已完结 ──────────
        ending_check = _check_story_ending(novel_id, vol_no)
        if ending_check["is_ending"]:
            logger.info(
                f"🏁 检测到故事已自然完结，提前终止自动写作 "
                f"(confidence={ending_check['confidence']})"
            )
            logger.info(f"   原因: {ending_check['reason']}")
            repo.update_novel_status(novel_id, "done")
            rc.clear_current_task(novel_id)
            results.append({
                "status": "story_ended",
                "volume_no": vol_no,
                "reason": ending_check["reason"],
                "confidence": ending_check["confidence"],
            })
            return {"volumes": results, "total": len(results), "story_ended": True}

        rc.set_current_task(novel_id, {
            "type": "auto",
            "volume_no": vol_no,
            "chapter_no": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

        result = run_volume(novel_id, vol_no)
        results.append(result)

        if result["status"] == "stopped":
            # run_volume detected stop flag mid-volume; clear and exit
            logger.info(f"🛑 自动写作因停止信号退出 (第{vol_no}卷中断)")
            rc.clear_stop(novel_id)
            rc.clear_current_task(novel_id)
            repo.update_novel_status(novel_id, "paused")
            return {"volumes": results, "total": len(results), "stopped": True}
        elif result["status"] == "done":
            consecutive_failures = 0
            volumes_written += 1
        elif result["status"] == "skipped":
            # User-initiated skip in interactive mode — advance without counting as failure
            pass
        else:
            # On first failure of a volume, retry it once with force_replan before
            # counting as a consecutive failure and moving on.
            logger.warning(f"⚠️  第{vol_no}卷失败 ({result.get('error','')}), 重试一次...")
            retry = run_volume(novel_id, vol_no, force_replan=True)
            results.append(retry)
            if retry["status"] == "done":
                consecutive_failures = 0
                volumes_written += 1
            else:
                consecutive_failures += 1
                logger.warning(
                    f"⚠️  第{vol_no}卷重试仍失败 (连续失败={consecutive_failures}/{circuit_breaker_limit})"
                )

    repo.update_novel_status(novel_id, "done")
    rc.clear_current_task(novel_id)
    logger.info(f"🎉 自动写作全部完成，共完成 {len(results)} 卷")
    return {"volumes": results, "total": len(results)}


# ── helpers ─────────────────────────────────────────────────────────

def _volume_literary_review(novel_id: str, volume_no: int) -> dict:
    """
    Dual-perspective literary review of a completed volume.

    Round 1: Critic perspective — identify concrete problems.
    Round 2: Synthesis — distill top-3 actionable suggestions for the next volume.

    Result is saved to world_memory key "volume_review_{volume_no}" and returned.
    """
    from llm import simple_chat_json

    # ── Gather context ──────────────────────────────────────
    recent_summaries = repo.get_recent_summaries(novel_id, limit=5)
    recent_str_parts = []
    word_count = 0
    for s in recent_summaries:
        ch = s.get("chapter_no", "?")
        txt = s.get("summary_text", "")[:150]
        recent_str_parts.append(f"第{ch}章: {txt}")
        word_count += s.get("word_count", 0)
    recent_summaries_str = "\n".join(recent_str_parts) or "（暂无摘要）"

    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}") or {}
    volume_goal = volume_plan.get("volume_goal", "（未记录）")

    from mcp import foreshadow_mcp
    foreshadow_status = foreshadow_mcp.format_for_prompt(novel_id, volume_no * 20)

    # ── Round 1: Critic ─────────────────────────────────────
    critic_prompt = (
        f"你是严苛的文学编辑，刚读完第{volume_no}卷（约{word_count}字）。\n\n"
        f"【本卷摘要（最后5章）】\n{recent_summaries_str}\n\n"
        f"【本卷大纲目标】\n{volume_goal}\n\n"
        f"【伏笔完成情况】\n{foreshadow_status[:500] if foreshadow_status else '（无）'}\n\n"
        "请从以下维度提出3-5个具体批评：\n"
        "1. 人物弧线是否有实质性成长？\n"
        "2. 本卷目标是否真正达成，还是敷衍收场？\n"
        "3. 情绪节奏是否过于均匀（缺乏起伏）？\n"
        "4. 是否有剧情拖沓或突兀跳跃的段落？\n"
        "5. 伏笔处理是否草率？\n\n"
        "每个批评给出具体改进建议。输出JSON：\n"
        '{"critiques": [{"dimension": "...", "problem": "...", "suggestion": "..."}]}'
    )

    critic_result = simple_chat_json(
        system_prompt="你是严苛的文学编辑，对小说卷进行专业批评，输出JSON。",
        user_prompt=critic_prompt,
        fallback={"critiques": []},
    )

    # ── Round 2: Synthesis ──────────────────────────────────
    synthesis_prompt = (
        f"基于以下批评报告，为第{volume_no + 1}卷规划师提供3条最重要的修正建议（30字/条）。\n\n"
        f"批评：{json.dumps(critic_result, ensure_ascii=False)}\n\n"
        '输出JSON：{"next_volume_guidance": ["建议1", "建议2", "建议3"]}'
    )

    synthesis_result = simple_chat_json(
        system_prompt="你是小说编辑，提炼关键改进建议，输出JSON。",
        user_prompt=synthesis_prompt,
        fallback={"next_volume_guidance": []},
    )

    review = {
        "critiques": critic_result.get("critiques", []),
        "next_volume_guidance": synthesis_result.get("next_volume_guidance", []),
    }
    repo.set_world_memory(novel_id, f"volume_review_{volume_no}", review)
    return review


def _plan_volume(novel_id: str, volume_no: int, total_volumes: int = 10) -> dict:
    """Generate detailed chapter outlines for a volume."""
    from llm import simple_chat_json
    from narrative.arc_planner import get_arc_guidance

    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    current_focus = repo.get_world_memory(novel_id, "current_focus") or ""
    author_intent = repo.get_world_memory(novel_id, "author_intent") or ""
    last_ending = repo.get_world_memory(novel_id, "last_chapter_ending") or "故事开端"

    from mcp import foreshadow_mcp
    pending = foreshadow_mcp.format_for_prompt(novel_id, _get_volume_start_chapter(novel_id, volume_no))

    arc_guidance = get_arc_guidance(novel_id, volume_no)

    prev_review = (repo.get_world_memory(novel_id, f"volume_review_{volume_no - 1}") or {}) if volume_no > 1 else {}
    prev_guidance = prev_review.get("next_volume_guidance", [])
    prev_guidance_str = "\n".join(f"- {g}" for g in prev_guidance) if prev_guidance else "（无）"

    user_hint = repo.get_world_memory(novel_id, f"user_volume_hint_{volume_no}") or {}
    user_volume_hints = _format_user_volume_hint(user_hint)

    plan = simple_chat_json(
        system_prompt="你是长篇小说卷级规划师，为当前卷生成详细的20章大纲，输出JSON。",
        user_prompt=VOLUME_PLAN_PROMPT.format(
            volume_no=volume_no,
            total_volumes=total_volumes,
            story_outline=json.dumps(story_outline, ensure_ascii=False)[:2000],
            current_focus=current_focus,
            author_intent=author_intent,
            prev_ending=str(last_ending)[:300],
            pending_foreshadows=pending,
            volume_emotional_anchors=_get_volume_anchors(story_outline, volume_no),
            outline_correction=_build_outline_correction(story_outline, volume_no, total_volumes),
            arc_guidance=arc_guidance,
            prev_volume_guidance=prev_guidance_str,
            user_volume_hints=user_volume_hints,
        ),
        fallback={"volume_title": f"第{volume_no}卷", "chapter_outlines": []},
    )

    # Ensure chapter outlines have proper numbering
    start = _get_volume_start_chapter(novel_id, volume_no)
    for i, outline in enumerate(plan.get("chapter_outlines", [])):
        outline["chapter_no"] = start + i

    # Don't persist an empty plan — a zero-outline plan would cause auto_run to skip
    # this volume permanently on the next run.
    if plan.get("chapter_outlines"):
        repo.set_world_memory(novel_id, f"volume_plan_{volume_no}", plan)
    else:
        logger.warning(f"⚠️  第{volume_no}卷大纲规划返回0章，不保存空计划")

    # Sync the LLM-generated volume title back to the volumes table
    real_title = plan.get("volume_title", "").strip()
    if real_title and real_title != f"第{volume_no}卷":
        from sqlalchemy import text
        from db.json_session import get_db
        with get_db() as db:
            db.execute(text(
                "UPDATE volumes SET title=:title WHERE novel_id=:nid AND volume_no=:vno"
            ), {"title": real_title, "nid": novel_id, "vno": volume_no})
        logger.info(f"📌 第{volume_no}卷卷名已更新: 《{real_title}》")

    logger.info(f"📋 第{volume_no}卷大纲规划完成，共{len(plan.get('chapter_outlines',[]))}章")
    return plan


def _format_user_volume_hint(hint: dict) -> str:
    """Format a stored user volume hint dict into a prompt-ready string."""
    if not hint:
        return "（无用户指定参考）"
    parts = []
    if hint.get("core_goal"):
        parts.append(f"核心目标：{hint['core_goal']}")
    if hint.get("key_plots"):
        parts.append("关键情节（必须体现）：\n" + "\n".join(f"  - {p}" for p in hint["key_plots"]))
    if hint.get("climax_hint"):
        parts.append(f"高潮/转折：{hint['climax_hint']}")
    if hint.get("ending_hint"):
        parts.append(f"卷末悬念：{hint['ending_hint']}")
    if hint.get("tone_hint"):
        parts.append(f"本卷基调：{hint['tone_hint']}")
    return "\n".join(parts) if parts else "（无用户指定参考）"


def _get_volume_anchors(story_outline: dict, volume_no: int) -> str:
    """Extract emotional anchor scenes assigned to this volume from the story outline."""
    import json as _json
    anchors = story_outline.get("global_emotional_anchors", [])
    # Also collect act-level anchors
    for act in story_outline.get("act_structure", []):
        anchors.extend(act.get("emotional_anchors", []))

    volume_anchors = [
        a for a in anchors
        if str(a.get("approximate_volume", "")).strip() == str(volume_no)
    ]
    if not volume_anchors:
        return f"（本卷无预设强情绪锚点，可自行设计1-2个情绪高点）"

    lines = []
    for a in volume_anchors:
        lines.append(
            f"【锚点】{a.get('name', '')} | 类型:{a.get('type', '')} "
            f"| 矛盾:{a.get('character_contradiction', '')} "
            f"| 情绪目标:{a.get('emotion_target', '')} "
            f"| 铺垫要求:{a.get('setup_required', '无')}"
        )
    return "\n".join(lines)


def _build_outline_correction(story_outline: dict, volume_no: int, total_volumes: int) -> str:
    """Build a correction string that re-anchors the volume plan to the original outline.
    
    This prevents cold-start drift where each new volume is planned based
    on the already-drifted current_focus rather than the original story outline.
    """
    parts = []
    
    # Protagonist ultimate goal
    protagonist = story_outline.get("protagonist", {})
    if isinstance(protagonist, dict):
        goal = protagonist.get("goal", "")
        if goal:
            parts.append(f"主角终极目标（不可偏离）：{goal}")
    
    # Ending direction
    ending = story_outline.get("ending_direction", "")
    if ending:
        parts.append(f"全书结局方向（必须朝此推进）：{ending}")
    
    # Current act goal
    act_structure = story_outline.get("act_structure", [])
    for act in act_structure:
        if _vol_in_act(volume_no, act.get("volumes", "")):
            parts.append(f"当前幕《{act.get('name','')}》目标：{act.get('goal','')}")
            key_events = act.get("key_events", [])[:3]
            if key_events:
                parts.append(f"本幕关键事件：{'、'.join(key_events)}")
            break
    
    # Remaining power milestones
    power_milestones = story_outline.get("power_milestones", [])
    remaining = [m for m in power_milestones if m.get("volume", 999) >= volume_no]
    if remaining:
        milestone_strs = []
        for m in remaining[:3]:
            milestone_strs.append(f"第{m.get('volume','?')}卷：{m.get('level','')} — {m.get('event','')[:30]}")
        parts.append("后续力量突破规划：" + " → ".join(milestone_strs))
    
    if not parts:
        return "（无纠偏参考数据）"
    
    return "\n".join(parts)


def _get_volume_start_chapter(novel_id: str, volume_no: int) -> int:
    """Get the starting chapter number for a volume (assumes ~50 chapters/volume)."""
    from config import get_settings
    # Try to find from existing chapters
    from sqlalchemy import text
    from db.json_session import get_db
    with get_db() as db:
        row = db.execute(text(
            "SELECT MAX(chapter_no) as max_ch FROM chapters "
            "WHERE novel_id=:nid AND volume_no=:vno"
        ), {"nid": novel_id, "vno": volume_no - 1}).mappings().first()
    if row and row["max_ch"]:
        return row["max_ch"] + 1
    return (volume_no - 1) * 20 + 1


def _update_current_focus(novel_id: str, volume_no: int):
    """Update current_focus after a volume completes."""
    from llm import simple_chat
    from config.prompts import CURRENT_FOCUS_PROMPT
    from mcp import foreshadow_mcp

    # Get total volumes info
    novel_record = repo.get_novel(novel_id)
    total_volumes = novel_record.get("total_volumes", 10) if novel_record else 10
    remaining_volumes = max(0, total_volumes - volume_no)
    progress_pct = round(volume_no / total_volumes * 100)

    recent = repo.get_recent_summaries(novel_id, limit=3)
    recent_str = "\n".join(f"第{s['chapter_no']}章: {s.get('summary_text','')[:100]}" for s in recent)
    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    act_structure = story_outline.get("act_structure", [])
    current_act = next((a for a in act_structure if _vol_in_act(volume_no, a.get("volumes",""))), {})
    proto_id = _get_protagonist_id(novel_id)
    proto_state = ""
    if proto_id:
        char = repo.get_character(novel_id, proto_id)
        if char:
            proto_state = f"境界={char.get('power_level','?')}, 位置={char.get('location','?')}"

    pending = foreshadow_mcp.format_for_prompt(novel_id, volume_no * 10)

    focus = simple_chat(
        system_prompt="你是网络小说作者，根据当前进展更新创作聚焦方向，100字以内，直接输出文本：",
        user_prompt=CURRENT_FOCUS_PROMPT.format(
            volume_no=volume_no,
            total_volumes=total_volumes,
            remaining_volumes=remaining_volumes,
            progress_pct=progress_pct,
            recent_summary=recent_str,
            protagonist_state=proto_state,
            pending_foreshadows=pending[:200],
            current_act=current_act.get("name", ""),
        ),
    )
    repo.set_world_memory(novel_id, "current_focus", focus.strip())
    logger.info(f"🔄 当前聚焦方向已更新 (第{volume_no}/{total_volumes}卷完成)")

    from narrative.arc_planner import update_arc_progress
    try:
        update_arc_progress(novel_id, volume_no)
    except Exception:
        pass


def _get_protagonist_id(novel_id: str) -> Optional[str]:
    protagonist = repo.get_world_memory(novel_id, "protagonist") or {}
    if isinstance(protagonist, dict):
        name = protagonist.get("name", "")
        if name:
            import re
            return re.sub(r"[^\w]", "_", name.strip().lower()) or None
    return None


def _vol_in_act(vol_no: int, vol_range: str) -> bool:
    import re
    try:
        nums = re.findall(r"\d+", str(vol_range))
        if len(nums) >= 2:
            return int(nums[0]) <= vol_no <= int(nums[1])
        elif len(nums) == 1:
            return vol_no == int(nums[0])
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════
#  结尾检测
# ═══════════════════════════════════════════════════════

ENDING_CHECK_PROMPT = """判断小说是否已到达自然结局：

【故事进度】第{volume_no}/{total_volumes}卷
【当前幕】{current_act_name}
【伏笔回收】{resolved_fs}/{total_fs} 处（{resolution_pct}）
【主角状态】{protagonist_state}
【未解决伏笔】{pending_foreshadows}

【全书剧情锚点（对照检查）】
核心主题：{core_theme}
结局方向：{ending_direction}
主角终极目标：{protagonist_goal}

【最近章节摘要】
{recent_summaries}

请严格评估故事是否已到达自然结局，标准如下：
1. 🔑 **核心矛盾是否解决**：全书开篇埋下的主要冲突（反派/终极危机/核心谜团）是否已尘埃落定？
2. 📜 **主线伏笔是否回收**：未解决的伏笔中是否有"核心级别"的关键伏笔？
3. 🎭 **主角弧线是否完成**：主角的终极目标是否已达成？成长是否走到终点？
4. 🏁 **章节是否有结束感**：最近几章的叙事节奏是否在收尾？是否有类似"尾声""大结局""多年后"等信号？

⚠️ 严格原则：宁可多写一卷也不要提前误判完结。
如果还有未解决的核心矛盾或重要伏笔，is_ending 必须为 false。

输出JSON：
{{
  "is_ending": true/false,
  "confidence": "high/medium/low",
  "reason": "判断依据说明（中文，50字以内）",
  "unresolved_keys": ["列出仍未解决的核心伏笔名（如果有）"]
}}"""


def _check_story_ending(novel_id: str, volume_no: int) -> dict:
    """
    Evaluate whether the story has reached its natural ending.
    Called before each volume in auto_run() to detect story completion.

    Returns dict:
      {"is_ending": bool, "confidence": str, "reason": str, "unresolved_keys": list}
    """
    from llm import simple_chat_json
    from mcp import foreshadow_mcp

    novel = repo.get_novel(novel_id)
    total_volumes = novel["total_volumes"] if novel else 10

    # ── 1. 伏笔回收率 ──────────────────────────────────
    all_fs = repo.list_foreshadows(novel_id, state=None)
    resolved_fs = repo.list_foreshadows(novel_id, state="RESOLVED")
    total_fs = len(all_fs)
    resolved_count = len(resolved_fs)
    resolution_pct = f"{resolved_count}/{total_fs}" if total_fs > 0 else "0/0"

    # ── 2. 当前幕阶段 ──────────────────────────────────
    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    act_structure = story_outline.get("act_structure", [])
    current_act = next(
        (a for a in act_structure if _vol_in_act(volume_no, a.get("volumes", ""))),
        {},
    )
    current_act_name = current_act.get("name", f"第{volume_no}卷阶段")

    # ── 3. 主角状态 ────────────────────────────────────
    proto_state = ""
    proto_id = _get_protagonist_id(novel_id)
    if proto_id:
        char = repo.get_character(novel_id, proto_id)
        if char:
            proto_state = (
                f"位置={char.get('location','?')}, "
                f"状态={char.get('emotion_state','?')}, "
                f"实力={char.get('power_level','?')}"
            )

    # ── 4. 近期章节摘要 ────────────────────────────────
    summaries = repo.get_recent_summaries(novel_id, limit=5)
    recent_lines = []
    for s in summaries:
        ch = s.get("chapter_no", "?")
        txt = s.get("summary_text", "")[:120]
        recent_lines.append(f"第{ch}章: {txt}")
    recent_summaries = "\n".join(recent_lines) or "（暂无章节摘要）"

    # ── 5. 未解决伏笔 ──────────────────────────────────
    pending_raw = foreshadow_mcp.format_for_prompt(novel_id, volume_no * 50)
    pending_foreshadows = pending_raw[:300] if pending_raw else "（无）"

    # ── 6. LLM 评估 ────────────────────────────────────
    if total_fs == 0:
        # 没有伏笔系统时，用更轻量的判断
        logger.info("📊 结尾检测: 无伏笔系统，改用进度+内容判断")
    else:
        logger.info(
            f"📊 结尾检测: 伏笔 {resolved_count}/{total_fs} "
            f"已回收={resolution_pct}  当前幕={current_act_name}"
        )

    # Load story outline data for enhanced check
    core_theme = story_outline.get("core_theme", "（未设定）")
    ending_direction = story_outline.get("ending_direction", "（未设定）")
    protagonist_goal = story_outline.get("protagonist", {}).get("goal", "（未设定）")
    if not protagonist_goal:
        protagonist_goal = (repo.get_world_memory(novel_id, "protagonist") or {}).get("goal", "（未设定）")

    result = simple_chat_json(
        system_prompt="你是专业小说编辑，根据故事进展判断是否已到达自然结局。严格评估，宁缺毋滥。直接输出JSON。",
        user_prompt=ENDING_CHECK_PROMPT.format(
            volume_no=volume_no,
            total_volumes=total_volumes,
            current_act_name=current_act_name,
            resolved_fs=resolved_count,
            total_fs=total_fs,
            resolution_pct=resolution_pct,
            protagonist_state=proto_state or "未知",
            pending_foreshadows=pending_foreshadows,
            recent_summaries=recent_summaries,
            core_theme=core_theme,
            ending_direction=ending_direction,
            protagonist_goal=protagonist_goal,
        ),
        fallback={"is_ending": False, "confidence": "low", "reason": "LLM评估失败，安全模式：继续写作"},
    )

    # 保存评估结果到 world_memory 供追溯
    repo.set_world_memory(
        novel_id,
        f"ending_check_v{volume_no}",
        {
            "volume_no": volume_no,
            "total_volumes": total_volumes,
            "is_ending": result["is_ending"],
            "confidence": result.get("confidence", "low"),
            "reason": result.get("reason", ""),
            "foreshadows_resolved": resolved_count,
            "foreshadows_total": total_fs,
        },
    )

    logger.info(
        f"📊 结尾检测结果: is_ending={result['is_ending']}, "
        f"confidence={result.get('confidence','?')}, "
        f"reason={result.get('reason','')}"
    )
    return result
