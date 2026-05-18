"""
web/app.py — FastAPI web dashboard

Endpoints:
  GET  /                         → dashboard HTML
  GET  /api/novels               → list novels
  POST /api/novels               → create + init novel
  GET  /api/novels/{id}          → novel detail
  GET  /api/novels/{id}/chapters → list chapters
  GET  /api/novels/{id}/chapters/{no} → chapter content
  GET  /api/novels/{id}/characters    → characters
  GET  /api/novels/{id}/foreshadows   → foreshadow list
  GET  /api/novels/{id}/metrics       → reader metrics trend
  POST /api/novels/{id}/write         → start writing (background)
  PUT  /api/novels/{id}/control       → update author_intent / current_focus
  GET  /api/health                    → service health
"""
import asyncio
import threading
import time
import collections
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from loguru import logger
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from pydantic import BaseModel
import os

# 全局线程池：用于运行耗时任务（初始化、写作等）
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="novel-bg-")



class CreateNovelRequest(BaseModel):
    title: str
    description: str
    world_type: str = "玄幻"
    total_volumes: int = 10
    provider: Optional[str] = None  # deepseek | qwen | glm | ollama


class WriteRequest(BaseModel):
    volume_no: Optional[int] = None  # None = auto-detect next unwritten volume
    mode: str = "volume"  # volume | auto
    max_volumes: Optional[int] = None
    provider: Optional[str] = None  # deepseek | qwen | glm | ollama


class RewriteVolumeRequest(BaseModel):
    confirm: bool = False  # Safety flag: must be True to proceed


class ReinitRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    world_type: Optional[str] = None
    total_volumes: Optional[int] = None
    provider: Optional[str] = None  # deepseek | qwen | glm | ollama


class ControlRequest(BaseModel):
    author_intent: Optional[str] = None
    current_focus: Optional[str] = None


def create_app() -> FastAPI:
    from config import get_settings
    secret = get_settings().web_secret_path

    app = FastAPI(
        title="Novel Agent V2",
        description="🧠 Industrial Novel Generation System",
        version="2.0.0",
    )

    # ── 路径 Token 中间件 ────────────────────────────────────────
    # 所有请求必须以 /{secret}/ 开头，否则返回 404。
    # 匹配后透明剥除前缀，下游路由无感知。
    if secret:
        class _SecretPathMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.scope["path"]
                prefix = f"/{secret}"
                if path == prefix:
                    # 裸前缀重定向到 /{secret}/ 首页
                    return Response(status_code=301, headers={"Location": prefix + "/"})
                if not path.startswith(prefix + "/"):
                    return Response(status_code=404)
                request.scope["path"] = path[len(prefix):] or "/"
                return await call_next(request)

        app.add_middleware(_SecretPathMiddleware)
        logger.info(f"[web] 路径 Token 已启用，访问地址: http://host:port/{secret}/")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 写操作速率限制中间件 ─────────────────────────────────────
    # POST /api/novels* 限 10次/分钟/IP，防止意外触发大量 LLM 调用
    _WRITE_RATE_LIMIT = 10       # max requests
    _WRITE_RATE_WINDOW = 60      # seconds
    _write_counters: dict = collections.defaultdict(collections.deque)
    _write_lock = threading.Lock()

    class _WriteRateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method == "POST" and "/api/" in request.scope["path"]:
                client_ip = request.client.host if request.client else "unknown"
                now = time.monotonic()
                with _write_lock:
                    dq = _write_counters[client_ip]
                    while dq and now - dq[0] > _WRITE_RATE_WINDOW:
                        dq.popleft()
                    if len(dq) >= _WRITE_RATE_LIMIT:
                        logger.warning(f"[rate-limit] POST from {client_ip} rejected ({len(dq)} in {_WRITE_RATE_WINDOW}s)")
                        return JSONResponse(
                            status_code=429,
                            content={"detail": f"Too many requests. Limit: {_WRITE_RATE_LIMIT} per {_WRITE_RATE_WINDOW}s."},
                        )
                    dq.append(now)
            return await call_next(request)

    app.add_middleware(_WriteRateLimitMiddleware)

    # Serve static files
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.on_event("startup")
    async def _recover_stale_writing_status():
        """
        服务启动时恢复孤儿状态：
        若 DB 中小说状态为 writing/init，但没有任何任务实际运行（服务刚启动），
        则将其重置为 paused，并清除 Redis 中的停止标志。
        防止强杀服务后状态永久卡在 writing。
        """
        try:
            from db import repo, cache
            from db.json_session import get_db
            from sqlalchemy import text
            with get_db() as db:
                rows = db.execute(text(
                    "SELECT novel_id FROM novels WHERE status IN ('writing', 'init')"
                )).fetchall()
            for row in rows:
                nid = row[0]
                # 额外检查：跳过已完结的小说
                from db import repo as _r
                novel = _r.get_novel(nid)
                if novel and novel.get("status") in ("completed", "done"):
                    continue
                repo.update_novel_status(nid, "paused")
                cache.clear_stop(nid)
                cache.clear_current_task(nid)
                logger.warning(f"[startup] 恢复孤儿状态: {nid} writing→paused")
        except Exception as e:
            logger.error(f"[startup] 状态恢复失败: {e}")

        # ── 初始化向量搜索引擎（可选，失败不影响主功能） ──────
        try:
            from db import vector_store as _vs
            if _vs.init_vector_store():
                logger.info("[startup] ✅ 向量搜索引擎已就绪")
            else:
                logger.info("[startup] ⚠️ 向量搜索引擎未完全加载（使用文本兜底）")
        except Exception:
            logger.info("[startup] ⚠️ 向量搜索引擎未启用（使用文本兜底模式）")

    # ── Routes ──────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>Novel Agent V2</h1><p>Static files not found.</p>")

    @app.get("/api/health")
    async def health():
        from db.json_session import ping as session_ping
        from db.cache import ping as cache_ping
        return {
            "storage": session_ping(),
            "cache": cache_ping(),
            "status": "ok",
        }

    @app.get("/api/novels")
    async def list_novels():
        from db import repo
        return repo.list_novels()

    @app.post("/api/novels", status_code=201)
    async def create_novel(req: CreateNovelRequest, background: BackgroundTasks):
        from db import repo
        from pipeline import init_novel

        novel_id = repo.create_novel(
            title=req.title,
            description=req.description,
            world_type=req.world_type,
            total_volumes=req.total_volumes,
        )

        # Run init in background
        background.add_task(
            _bg_init,
            novel_id=novel_id,
            title=req.title,
            description=req.description,
            world_type=req.world_type,
            total_volumes=req.total_volumes,
            provider=req.provider,
        )
        return {"novel_id": novel_id, "status": "initializing"}

    @app.get("/api/novels/{novel_id}")
    async def get_novel(novel_id: str):
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        world = repo.get_world_memory(novel_id, "background")
        protagonist = repo.get_world_memory(novel_id, "protagonist")
        return {
            **novel,
            "background": world,
            "protagonist": protagonist,
            "author_intent": repo.get_world_memory(novel_id, "author_intent"),
            "current_focus": repo.get_world_memory(novel_id, "current_focus"),
        }

    @app.put("/api/novels/{novel_id}/chapters/{chapter_no}")
    async def update_chapter(novel_id: str, chapter_no: int, req: dict):
        """
        手动编辑章节内容。
        保存编辑后的正文，标记 chapter 为 human_edited 状态，
        使管线可以从该章之后继续写（代替重新生成该章）。
        """
        from db import repo
        ch = repo.get_chapter(novel_id, chapter_no)
        if not ch:
            raise HTTPException(404, "Chapter not found")

        content = req.get("content", "")
        if not content or len(content) < 10:
            raise HTTPException(400, "Content too short (min 10 chars)")

        # Preserve original content as original_content, update with new content
        if ch.get("status") == "done" and not ch.get("original_content"):
            ch["original_content"] = ch.get("content", "")

        ch["content"] = content
        ch["word_count"] = len(content)
        ch["status"] = "human_edited"
        ch["edited_at"] = str(datetime.utcnow())

        repo.upsert_chapter(novel_id, chapter_no, ch)
        return {"status": "saved", "chapter_no": chapter_no, "word_count": len(content)}

    @app.post("/api/novels/{novel_id}/chapters/{chapter_no}/rewrite")
    async def rewrite_chapter(novel_id: str, chapter_no: int, req: dict):
        """
        章节改写：扩写/压缩/改基调。异步执行，返回任务ID。
        """
        from db import repo
        ch = repo.get_chapter(novel_id, chapter_no)
        if not ch:
            raise HTTPException(404, "Chapter not found")

        action = req.get("action", "expand")  # expand | compress | change_tone
        target_chars = req.get("target_chars", 0)
        target_tone = req.get("target_tone", "")

        # Run in thread pool
        future = _executor.submit(
            _rewrite_chapter,
            novel_id=novel_id, chapter_no=chapter_no,
            content=ch.get("content", ""),
            action=action,
            target_chars=target_chars,
            target_tone=target_tone,
        )
        try:
            result = future.result(timeout=120)
            return result
        except Exception as e:
            raise HTTPException(500, f"Rewrite failed: {e}")

    @app.get("/api/novels/{novel_id}/chapters")
    async def list_chapters(novel_id: str, volume: Optional[int] = None, limit: int = 500):
        from db import repo
        return repo.list_json_chapters(novel_id, volume=volume, limit=limit)

    @app.get("/api/novels/{novel_id}/chapters/{chapter_no}")
    async def get_chapter(novel_id: str, chapter_no: int):
        from db import repo
        ch = repo.get_chapter(novel_id, chapter_no)
        if not ch:
            raise HTTPException(404, "Chapter not found")
        return ch

    @app.get("/api/novels/{novel_id}/volumes")
    async def list_volumes(novel_id: str):
        """返回所有卷的信息（卷号、卷名、状态），并从 world_memory 补充 volume_plan 标题"""
        from db import repo
        volumes = list(repo.get_volumes(novel_id))
        if not volumes:
            # core.json volumes 为空时，从 volume_plan 重建
            novel = repo.get_novel(novel_id)
            total = (novel or {}).get("total_volumes", 0)
            for vno in range(1, total + 1):
                plan = repo.get_world_memory(novel_id, f"volume_plan_{vno}") or {}
                volumes.append({
                    "volume_no": vno,
                    "title": plan.get("volume_title", f"第{vno}卷"),
                    "status": "init",
                    "volume_goal": plan.get("volume_goal", ""),
                    "arc_notes": plan.get("arc_notes", ""),
                })
            volumes.sort(key=lambda x: x.get("volume_no", 0))
            return volumes
        for v in volumes:
            plan = repo.get_world_memory(novel_id, f"volume_plan_{v['volume_no']}") or {}
            if not v.get("title") and plan.get("volume_title"):
                v["title"] = plan["volume_title"]
            if not v.get("title"):
                v["title"] = f"第{v['volume_no']}卷"
            v["volume_goal"] = plan.get("volume_goal", "")
            v["arc_notes"] = plan.get("arc_notes", "")
        volumes.sort(key=lambda x: x.get("volume_no", 0))
        return volumes

    @app.get("/api/novels/{novel_id}/characters")
    async def list_characters(novel_id: str):
        from db import repo
        return repo.get_all_characters(novel_id)

    @app.get("/api/novels/{novel_id}/foreshadows")
    async def list_foreshadows(novel_id: str, state: Optional[str] = None):
        from db import repo
        return repo.list_foreshadows(novel_id, state=state)

    @app.get("/api/novels/{novel_id}/metrics")
    async def get_metrics(novel_id: str, last_n: int = 20):
        from mcp import reader_mcp
        return {
            "trend": reader_mcp.get_trend(novel_id, last_n=last_n),
        }

    @app.post("/api/novels/{novel_id}/write")
    async def write_novel(novel_id: str, req: WriteRequest, background: BackgroundTasks):
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        try:
            repo.check_completed(novel_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if novel["status"] in ("init", "writing"):
            raise HTTPException(409, f"Novel is currently busy (status: {novel['status']}), please wait")

        # Auto-detect next volume if not specified
        volume_no = req.volume_no
        if volume_no is None:
            volume_no = _detect_next_volume(novel_id)
            logger.info(f"[写] 未指定起始卷，自动检测为第{volume_no}卷")

        if req.mode == "auto":
            background.add_task(
                _bg_auto_run,
                novel_id=novel_id,
                start_volume=volume_no,
                max_volumes=req.max_volumes,
                provider=req.provider,
            )
        else:
            background.add_task(_bg_write_volume, novel_id=novel_id, volume_no=volume_no,
                                provider=req.provider)

        return {"status": "started", "mode": req.mode, "volume_no": volume_no}


    def _detect_next_volume(novel_id: str) -> int:
        """Find the first volume that needs to be written.

        Scans volume plans and checks which volume needs more chapters.
        Returns the first volume where:
        - plan is missing or has no outlines, OR
        - not all planned chapters are written yet (incomplete volume)

        Falls back to volume 1.
        """
        from db import repo
        novel = repo.get_novel(novel_id)
        total_volumes = novel.get("total_volumes", 10) if novel else 10

        for vol_no in range(1, total_volumes + 3):  # allow 2 extra beyond plan
            plan = repo.get_world_memory(novel_id, f"volume_plan_{vol_no}")
            if not plan:
                return vol_no
            outlines = plan.get("chapter_outlines", [])
            if not outlines:
                return vol_no
            # Count chapters that are actually completed
            existing_chapters = repo.get_chapters_in_volume(novel_id, vol_no)
            done_count = sum(
                1 for c in existing_chapters
                if c.get("status") in ("done", "human_edited")
            )
            planned_count = len(outlines)
            if done_count < planned_count:
                # Volume is not fully written — return it so writing continues
                return vol_no
            # All planned chapters are completed, move to next volume
        return 1  # fallback

    @app.put("/api/novels/{novel_id}/control")
    async def update_control(novel_id: str, req: ControlRequest):
        from db import repo
        try:
            repo.check_completed(novel_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if req.author_intent is not None:
            repo.set_world_memory(novel_id, "author_intent", req.author_intent)
        if req.current_focus is not None:
            repo.set_world_memory(novel_id, "current_focus", req.current_focus)
        return {"status": "updated"}

    @app.post("/api/novels/{novel_id}/volumes/{volume_no}/rewrite", status_code=202)
    async def rewrite_volume_endpoint(
        novel_id: str, volume_no: int,
        req: RewriteVolumeRequest,
        background: BackgroundTasks,
    ):
        """
        整卷重写：清除指定卷的所有章节、摘要、向量及相关伏笔数据，然后重新生成整卷。
        需要传 {"confirm": true} 才会执行（防误触）。
        """
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        try:
            repo.check_completed(novel_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if not req.confirm:
            raise HTTPException(400, 'Must set {"confirm": true} to rewrite a volume')
        if novel["status"] in ("init", "writing"):
            raise HTTPException(409, f"Novel is currently busy (status: {novel['status']}), please wait")

        background.add_task(_bg_rewrite_volume, novel_id=novel_id, volume_no=volume_no)
        return {"status": "rewriting", "novel_id": novel_id, "volume_no": volume_no}

    @app.delete("/api/novels/{novel_id}", status_code=200)
    async def delete_novel(novel_id: str):
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        try:
            repo.check_completed(novel_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        repo.delete_novel(novel_id)
        return {"status": "deleted", "novel_id": novel_id}

    @app.post("/api/novels/{novel_id}/reinit", status_code=202)
    async def reinit_novel(novel_id: str, body: ReinitRequest = None, background: BackgroundTasks = None):
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        try:
            repo.check_completed(novel_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if novel["status"] in ("init", "writing"):
            raise HTTPException(409, f"Novel is currently busy (status: {novel['status']}). Stop it first via POST /api/novels/{novel_id}/stop")
        if body:
            repo.update_novel(
                novel_id,
                title=body.title,
                description=body.description,
                world_type=body.world_type,
                total_volumes=body.total_volumes,
            )
            novel = repo.get_novel(novel_id)
        repo.reset_novel_data(novel_id)
        background.add_task(
            _bg_init,
            novel_id=novel_id,
            title=novel["title"],
            description=novel["description"],
            world_type=novel["world_type"],
            total_volumes=novel["total_volumes"],
            provider=body.provider if body else None,
        )
        return {"status": "reinitializing", "novel_id": novel_id}

    @app.post("/api/novels/{novel_id}/chapters/{chapter_no}/regenerate", status_code=202)
    async def regenerate_chapter(novel_id: str, chapter_no: int, background: BackgroundTasks):
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        try:
            repo.check_completed(novel_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        chapter = repo.get_chapter(novel_id, chapter_no)
        volume_no = chapter["volume_no"] if chapter else 1
        # 重置章节为 pending 状态（JSON file store）
        repo.upsert_chapter(novel_id, chapter_no, {
            "status": "pending", "content": "", "word_count": 0,
            "volume_no": volume_no,
        })
        background.add_task(_bg_regen_chapter, novel_id=novel_id,
                            chapter_no=chapter_no, volume_no=volume_no)
        return {"status": "regenerating", "chapter_no": chapter_no}

    @app.post("/api/novels/{novel_id}/complete", status_code=200)
    async def complete_novel(novel_id: str):
        """手动将小说标记为「已完结」，锁定防止误操作"""
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        if novel["status"] == "completed":
            return {"status": "already_completed", "novel_id": novel_id,
                    "message": f"小说「{novel['title']}」已经是已完结状态"}
        repo.update_novel_status(novel_id, "completed")
        logger.info(f"[API] 小说已标记为已完结: {novel_id} 「{novel['title']}」")
        return {"status": "completed", "novel_id": novel_id,
                "message": f"小说「{novel['title']}」已标记为已完结 🔒"}

    @app.post("/api/novels/{novel_id}/uncomplete", status_code=200)
    async def uncomplete_novel(novel_id: str):
        """解除小说的已完结状态"""
        from db import repo
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        if novel["status"] != "completed":
            raise HTTPException(400, f"小说当前状态为 {novel['status']}，不是已完结状态")
        repo.update_novel_status(novel_id, "done")
        logger.info(f"[API] 小说已解除完结状态: {novel_id} 「{novel['title']}」")
        return {"status": "uncompleted", "novel_id": novel_id,
                "message": f"小说「{novel['title']}」已解除完结状态，恢复为可操作"}

    @app.post("/api/novels/{novel_id}/stop", status_code=200)
    async def stop_novel(novel_id: str):
        """
        立即停止正在运行的写作或初始化任务。
        设置停止标志后，LangGraph 在每个节点边界检查该标志并抛出异常，实现立即中断（不等待当前章节完成）。
        """
        from db import repo
        from db import cache
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        if novel["status"] not in ("writing", "init"):
            raise HTTPException(400, f"No running task (status: {novel['status']})")
        # If status is writing but no current_task exists, the process is a ghost
        # (e.g. server was hard-killed). Fix the state instead of setting a stop flag.
        if cache.get_current_task(novel_id) is None:
            repo.update_novel_status(novel_id, "paused")
            cache.clear_stop(novel_id)
            logger.warning(f"[API] 检测到孤儿 writing 状态，已自动修复: {novel_id}")
            return {"status": "fixed", "novel_id": novel_id, "message": "Ghost writing state cleared"}
        cache.request_stop(novel_id)
        logger.info(f"[API] 立即停止请求已设置: {novel_id}")
        return {"status": "stop_requested", "novel_id": novel_id}

    @app.get("/api/novels/{novel_id}/status")
    async def get_novel_status(novel_id: str):
        """轻量轮询接口：返回 novel 状态 + 最新章节进度，供前端轮询用"""
        from db import repo
        from db import cache
        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")
        stats = repo.get_chapter_stats(novel_id)
        last_ch = stats["last_chapter"]
        return {
            "novel_id": novel_id,
            "status": novel["status"],
            "title": novel["title"],
            "last_chapter": {
                "chapter_no": last_ch["chapter_no"],
                "title": last_ch.get("title", ""),
                "status": last_ch.get("status", "done"),
            } if last_ch else None,
            "total_chapters": stats["total"],
            "done_chapters": stats["done"],
            "failed_chapters": stats["failed"],
            "total_words": stats["words"],
            "current_task": cache.get_current_task(novel_id),
            "stop_requested": cache.is_stop_requested(novel_id),
        }

    @app.get("/api/novels/{novel_id}/stats")
    async def get_stats(novel_id: str):
        from db import repo
        stats = repo.get_chapter_stats(novel_id)
        return {
            "total_words": stats["words"],
            "total_chapters": stats["done"],
            "pending_foreshadows": stats["pending_foreshadows"],
        }

    @app.get("/api/novels/{novel_id}/export")
    async def export_novel(novel_id: str):
        """一键导出小说全部内容为 TXT 文件（含小说名、章节标题、章节正文）"""
        from db import repo
        from urllib.parse import quote

        novel = repo.get_novel(novel_id)
        if not novel:
            raise HTTPException(404, "Novel not found")

        chapters_dict = repo._get_chapters(novel_id)
        chapters = [c for c in chapters_dict.values() if c.get("status") == "done"]
        chapters.sort(key=lambda x: x.get("chapter_no", 0))

        lines = [
            f"《{novel['title']}》",
            f"类型：{novel.get('world_type', '')}",
            "=" * 60,
            "",
        ]

        current_volume = None
        for ch in chapters:
            if ch["volume_no"] != current_volume:
                current_volume = ch["volume_no"]
                lines.append(f"\n{'─' * 40}")
                lines.append(f"第{current_volume}卷")
                lines.append(f"{'─' * 40}\n")
            lines.append(f"第{ch['chapter_no']}章  {ch['title'] or ''}")
            lines.append("")
            lines.append(ch["content"] or "")
            lines.append("")

        content = "\n".join(lines)
        filename = f"{novel['title']}_完整版.txt"
        encoded_filename = quote(filename)
        return Response(
            content=content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            },
        )

    @app.get("/api/novels/{novel_id}/logs")
    async def get_logs(novel_id: str, last_n: int = 20, level: str = ""):
        """获取小说最近的日志行（用于前端进度展示）

        优先从 per-novel 内存缓冲区读取（快速），
        若缓冲区为空则回退到日志文件并过滤 novel_id。
        level: 为空则全部, 'error' 仅错误, 'warn' 仅警告
        """
        try:
            from db.novel_log import get_logs as buf_get

            level_f = level if level in ("error", "warn") else None
            lines = buf_get(novel_id, last_n=last_n, level_filter=level_f)
            if lines:
                return {"novel_id": novel_id, "logs": [f"{l['time']} | {l['msg']}" for l in lines]}

            # Fallback: read from file (backward compat)
            lines = []
            with open("logs/novel_agent.log", "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                filtered = [l for l in all_lines if novel_id in l][-last_n:]
                lines = [l.rstrip() for l in filtered]
            return {"novel_id": novel_id, "logs": lines}
        except Exception as e:
            logger.warning(f"Failed to read logs: {e}")
            return {"novel_id": novel_id, "logs": []}

    return app


# ── Background tasks ──────────────────────────────────────────────
# 所有 pipeline 函数都是同步阻塞的（内部调 LLM 需要数分钟）。
# 必须用 run_in_executor 放到线程池执行，避免阻塞 uvicorn event loop，
# 否则写作期间所有 HTTP 请求（包括轮询）全部卡住。

async def _bg_init(novel_id, title, description, world_type, total_volumes, provider=None):
    loop = asyncio.get_running_loop()
    def _run():
        try:
            if provider:
                os.environ["DEFAULT_PROVIDER"] = provider
                os.environ["DEFAULT_MODEL"] = {
                    "deepseek": "deepseek-v4-flash",
                    "qwen": "qwen3.6-plus",
                    "ollama": "qwen3.6",
                }.get(provider, provider)
                from config import get_settings
                get_settings.cache_clear()
            from pipeline import init_novel
            from db import cache
            cache.clear_stop(novel_id)  # clear any stale stop flag
            logger.info(f"[后台] 初始化任务开始: 《{title}》 ({novel_id}), provider={provider or '默认'}")
            init_novel(novel_id, title, description, world_type, total_volumes)
            logger.info(f"[后台] 初始化任务完成: 《{title}》 ({novel_id})")
        except Exception as e:
            logger.error(f"[后台] 初始化任务失败 ({novel_id}): {e}", exc_info=True)
            from db import repo
            repo.update_novel_status(novel_id, "init")  # 保持 init 状态，让用户知道失败
    await loop.run_in_executor(_executor, _run)


async def _bg_write_volume(novel_id, volume_no, provider=None):
    loop = asyncio.get_running_loop()
    def _run():
        try:
            if provider:
                os.environ["DEFAULT_PROVIDER"] = provider
                os.environ["DEFAULT_MODEL"] = {
                    "deepseek": "deepseek-v4-flash",
                    "qwen": "qwen3.6-plus",
                    "ollama": "qwen3.6",
                }.get(provider, provider)
                from config import get_settings
                get_settings.cache_clear()
            from pipeline import run_volume
            from db import repo
            from db import cache
            cache.clear_stop(novel_id)  # clear any stale stop flag
            repo.update_novel_status(novel_id, "writing")
            logger.info(f"[后台] 卷写作任务开始: 第{volume_no}卷 ({novel_id})")
            result = run_volume(novel_id, volume_no)
            cache.clear_stop(novel_id)  # clear stop flag regardless of how we exited
            repo.update_novel_status(novel_id, "paused")
            label = "中断" if result.get("status") == "stopped" else "完成"
            logger.info(f"[后台] 卷写作任务{label}: 第{volume_no}卷 ({novel_id})"
                        f"  成功={result.get('chapters_done')}章  共{result.get('total_words')}字")
        except Exception as e:
            logger.error(f"[后台] 卷写作任务失败: 第{volume_no}卷 ({novel_id}): {e}", exc_info=True)
            from db import repo as _repo
            from db import cache as _rc
            _rc.clear_stop(novel_id)
            _repo.update_novel_status(novel_id, "paused")  # 出错也恢复为 paused，允许重试
    await loop.run_in_executor(_executor, _run)


async def _bg_regen_chapter(novel_id, chapter_no, volume_no):
    loop = asyncio.get_running_loop()
    def _run():
        try:
            from pipeline import run_chapter
            logger.info(f"[后台] 重新生成章节: 第{chapter_no}章 ({novel_id})")
            result = run_chapter(novel_id, chapter_no, volume_no)
            logger.info(f"[后台] 章节重新生成完成: 第{chapter_no}章 ({novel_id})"
                        f"  状态={result.get('status')}  字数={result.get('word_count')}")
        except Exception as e:
            logger.error(f"[后台] 章节重新生成失败: 第{chapter_no}章 ({novel_id}): {e}", exc_info=True)
    await loop.run_in_executor(_executor, _run)


async def _bg_rewrite_volume(novel_id, volume_no):
    loop = asyncio.get_running_loop()
    def _run():
        try:
            from pipeline import rewrite_volume
            from db import repo
            from db import cache
            cache.clear_stop(novel_id)  # clear any stale stop flag
            repo.update_novel_status(novel_id, "writing")
            logger.info(f"[后台] 整卷重写任务开始: 第{volume_no}卷 ({novel_id})")
            result = rewrite_volume(novel_id, volume_no)
            cache.clear_stop(novel_id)  # clear stop flag regardless of how we exited
            repo.update_novel_status(novel_id, "paused")
            logger.info(
                f"[后台] 整卷重写任务完成: 第{volume_no}卷 ({novel_id})"
                f"  成功={result.get('chapters_done')}章  共{result.get('total_words')}字"
            )
        except Exception as e:
            logger.error(f"[后台] 整卷重写任务失败: 第{volume_no}卷 ({novel_id}): {e}", exc_info=True)
            from db import repo as _repo
            from db import cache as _rc
            _rc.clear_stop(novel_id)
            _repo.update_novel_status(novel_id, "paused")
    await loop.run_in_executor(_executor, _run)


async def _bg_auto_run(novel_id, start_volume, max_volumes, provider=None):
    loop = asyncio.get_running_loop()
    def _run():
        try:
            if provider:
                os.environ["DEFAULT_PROVIDER"] = provider
                os.environ["DEFAULT_MODEL"] = {
                    "deepseek": "deepseek-v4-flash",
                    "qwen": "qwen3.6-plus",
                    "ollama": "qwen3.6",
                }.get(provider, provider)
                from config import get_settings
                get_settings.cache_clear()
            from pipeline import auto_run
            from db import repo, cache
            cache.clear_stop(novel_id)
            repo.update_novel_status(novel_id, "writing")
            logger.info(f"[后台] 自动写作任务开始: 从第{start_volume}卷 ({novel_id})")
            result = auto_run(novel_id, start_volume, max_volumes)
            # auto_run already sets status to "done" internally
            logger.info(f"[后台] 自动写作任务完成: 共{result.get('total')}卷 ({novel_id})")
        except Exception as e:
            logger.error(f"[后台] 自动写作任务失败 ({novel_id}): {e}", exc_info=True)
            from db import repo
            from db import cache as _cache
            _cache.clear_current_task(novel_id)
            repo.update_novel_status(novel_id, "paused")
    await loop.run_in_executor(_executor, _run)


def _rewrite_chapter(novel_id, chapter_no, content, action, target_chars, target_tone):
    """
    章节改写（同步函数，在线程池执行）。
    支持 expand / compress / change_tone。
    返回改写后的章节文本。
    """
    from llm import chat
    from mcp import style_mcp
    from config.prompts import (
        CHAPTER_EXPAND_PROMPT, CHAPTER_COMPRESS_PROMPT, CHAPTER_TONE_PROMPT
    )
    from db import repo
    from loguru import logger

    if action == "expand":
        current_chars = len(content)
        tgt = target_chars or int(current_chars * 1.5)
        if tgt <= current_chars:
            tgt = int(current_chars * 1.3)
        prompt = CHAPTER_EXPAND_PROMPT.format(
            chapter_text=content[:8000],
            target_chars=tgt,
            current_chars=current_chars,
        )
    elif action == "compress":
        current_chars = len(content)
        tgt = target_chars or int(current_chars * 0.6)
        if tgt >= current_chars:
            tgt = int(current_chars * 0.7)
        prompt = CHAPTER_COMPRESS_PROMPT.format(
            chapter_text=content[:8000],
            target_chars=tgt,
            current_chars=current_chars,
        )
    elif action == "change_tone" and target_tone:
        style_sig = style_mcp.get_style_signature(novel_id)
        style_context = (
            f"当前风格：基调={style_sig.get('overall_tone','?')}, "
            f"对话比例≈{int(style_sig.get('dialogue_ratio',0.35)*100)}%, "
            f"情感密度={style_sig.get('emotion_density','?')}"
        ) if style_sig else "无"
        prompt = CHAPTER_TONE_PROMPT.format(
            chapter_text=content[:8000],
            target_tone=target_tone,
            style_context=style_context,
        )
    else:
        raise ValueError(f"Unknown rewrite action: {action} (tone={target_tone})")

    logger.info(f"✏️  [改写] 第{chapter_no}章: action={action}")
    result, usage = chat(
        [{"role": "system", "content": "你是一个小说改写专家。"},
         {"role": "user", "content": prompt}],
        max_tokens=8192,
    )
    edited = result.strip()

    # Save the rewritten chapter
    ch = repo.get_chapter(novel_id, chapter_no)
    if ch:
        if not ch.get("original_content") and ch.get("status") == "done":
            ch["original_content"] = ch.get("content", "")
        ch["content"] = edited
        ch["word_count"] = len(edited)
        ch["status"] = "human_edited"
        ch["rewrite_action"] = action
        ch["edited_at"] = str(datetime.utcnow())
        repo.upsert_chapter(novel_id, chapter_no, ch)

    logger.info(f"✅ [改写] 第{chapter_no}章完成: {len(content)} → {len(edited)} 字")
    return {
        "status": "ok",
        "chapter_no": chapter_no,
        "original_words": len(content),
        "new_words": len(edited),
        "content": edited,
    }

