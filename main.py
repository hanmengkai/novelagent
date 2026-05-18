#!/usr/bin/env python3
"""
main.py — CLI entry point for Novel Agent V2

Usage:
  # Create and initialize a new novel
  python main.py init --title "星辰大海" --desc "主角林枫从废柴觉醒..." --volumes 10

  # Write a volume
  python main.py write --novel-id xxx --volume 1

  # Write a single chapter
  python main.py chapter --novel-id xxx --chapter 5 --volume 1

  # Auto-run from volume 1 to max
  python main.py auto --novel-id xxx --start-volume 1

  # List novels
  python main.py list

  # Start web dashboard
  python main.py web --port 9101

  # Health check
  python main.py health
"""
import sys
import os
import argparse
import json
from loguru import logger
from config import get_settings


def _setup_logging():
    s = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=s.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    )
    logger.add(
        "logs/novel_agent.log",
        rotation="50 MB",
        retention="30 days",
        level="DEBUG",
    )


def _use_provider(provider: str | None):
    """Override LLM provider for a command. 设置环境变量 + 清理设置缓存。"""
    if provider:
        os.environ["DEFAULT_PROVIDER"] = provider
        os.environ["DEFAULT_MODEL"] = {
            "deepseek": "deepseek-v4-flash",
            "qwen": "qwen3.6-plus",
            "glm": "glm-5",
            "ollama": "qwen3.6",
        }.get(provider, provider)
        # 清除 get_settings() 的 lru_cache
        from config import get_settings
        get_settings.cache_clear()
        logger.info(f"🔧 临时切换 provider: {provider}")


def cmd_init(args):
    """Initialize a new novel."""
    _use_provider(args.provider)
    from db import repo
    from pipeline import init_novel

    # Create novel record
    novel_id = repo.create_novel(
        title=args.title,
        description=args.desc,
        world_type=args.world_type or "玄幻",
        total_volumes=args.volumes,
    )
    logger.info(f"Novel created: {novel_id}")

    result = init_novel(
        novel_id=novel_id,
        title=args.title,
        description=args.desc,
        world_type=args.world_type or "玄幻",
        total_volumes=args.volumes,
        template=args.template,
    )

    print("\n✅ 小说初始化成功！")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n📌 Novel ID: {novel_id}")
    print("使用以下命令开始写作：")
    print(f"  python main.py write --novel-id {novel_id} --volume 1")


def cmd_complete(args):
    """手动将小说标记为「已完结」，锁定防止误操作"""
    from db import repo
    novel = repo.get_novel(args.novel_id)
    if novel is None:
        print(f"❌ 小说不存在: {args.novel_id}")
        sys.exit(1)
    if novel["status"] == "completed":
        print(f"💡 小说「{novel['title']}」已经是已完结状态")
        return
    repo.update_novel_status(args.novel_id, "completed")
    print(f"✅ 小说「{novel['title']}」已标记为「已完结 🔒」")
    print(f"   所有删除/重置/修改/继续生成操作已被锁定。")


def cmd_uncomplete(args):
    """解除小说的已完结状态"""
    from db import repo
    novel = repo.get_novel(args.novel_id)
    if novel is None:
        print(f"❌ 小说不存在: {args.novel_id}")
        sys.exit(1)
    if novel["status"] != "completed":
        print(f"💡 小说「{novel['title']}」当前状态为 {novel['status']}，无需解除")
        return
    import json
    print(f"⚠️  即将解除小说「{novel['title']}」的已完结状态")
    print(f"   解除后，该小说将恢复为可操作状态。")
    confirm = input(f"   确认解除？(y/N): ").strip().lower()
    if confirm != "y":
        print("❌ 已取消")
        return
    repo.update_novel_status(args.novel_id, "done")
    print(f"✅ 小说「{novel['title']}」已解除完结状态 (状态恢复为 done)")
    print(f"   现在可以对该小说进行操作了。")


def cmd_reinit(args):
    """Reinitialize an existing novel, optionally updating its metadata."""
    from db import repo
    from pipeline import init_novel

    novel = repo.get_novel(args.novel_id)
    if novel is None:
        print(f"❌ 小说不存在: {args.novel_id}")
        sys.exit(1)
    repo.check_completed(args.novel_id)
    if novel["status"] in ("init", "writing"):
        print(f"❌ 小说当前正在处理中 (status={novel['status']}), 无法重新初始化")
        sys.exit(1)

    # Update metadata if new values provided
    repo.update_novel(
        args.novel_id,
        title=args.title,
        description=args.desc,
        world_type=args.world_type,
        total_volumes=args.volumes,
    )

    # Re-fetch with updated values
    novel = repo.get_novel(args.novel_id)

    # Clear all generated content
    repo.reset_novel_data(args.novel_id)
    logger.info(f"已清除旧数据，开始重新初始化: {args.novel_id}")

    result = init_novel(
        novel_id=novel["novel_id"],
        title=novel["title"],
        description=novel["description"],
        world_type=novel["world_type"],
        total_volumes=novel["total_volumes"],
    )

    print("\n✅ 小说重新初始化成功！")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n📌 Novel ID: {args.novel_id}")
    print("使用以下命令开始写作：")
    print(f"  python main.py write --novel-id {args.novel_id} --volume 1")


def cmd_write(args):
    """Write a full volume."""
    _use_provider(args.provider)
    from db import repo
    repo.check_completed(args.novel_id)
    from pipeline import run_volume
    result = run_volume(
        novel_id=args.novel_id,
        volume_no=args.volume,
        force_replan=args.replan,
        interactive=args.interactive,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_chapter(args):
    """Write a single chapter."""
    _use_provider(args.provider)
    from db import repo
    repo.check_completed(args.novel_id)
    from pipeline import run_chapter
    result = run_chapter(
        novel_id=args.novel_id,
        chapter_no=args.chapter,
        volume_no=args.volume,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_rewrite(args):
    """Rewrite an entire volume from scratch."""
    _use_provider(args.provider)
    from db import repo
    repo.check_completed(args.novel_id)
    from pipeline import rewrite_volume
    logger.info(f"整卷重写: 小说={args.novel_id}  第{args.volume}卷")
    result = rewrite_volume(
        novel_id=args.novel_id,
        volume_no=args.volume,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_auto(args):
    """Auto-run multiple volumes."""
    from db import repo
    repo.check_completed(args.novel_id)
    from pipeline import auto_run
    result = auto_run(
        novel_id=args.novel_id,
        start_volume=args.start_volume,
        max_volumes=args.max_volumes,
        circuit_breaker_limit=args.circuit_breaker,
    )
    print(f"\n✅ Auto-run complete: {result['total']} volumes")


def cmd_list(args):
    """List all novels."""
    from db import repo
    novels = repo.list_novels()
    if not novels:
        print("No novels found.")
        return
    print(f"\n{'ID':<38} {'Title':<20} {'Status':<10} {'Volumes':<8} {'Created'}")
    print("-" * 90)
    for n in novels:
        print(
            f"{n['novel_id']:<38} "
            f"{str(n['title']):<20} "
            f"{n['status']:<10} "
            f"{n['total_volumes']:<8} "
            f"{str(n['created_at'])[:16]}"
        )


def cmd_web(args):
    """Start the web dashboard."""
    import uvicorn
    from web.app import create_app
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=args.port or get_settings().web_port)


def cmd_health(args):
    """Check connectivity to all services."""
    from db.json_session import ping as session_ping
    from db.cache import ping as cache_ping
    from db.minio_client import ping as minio_ping

    print("Health Check:")
    ok = True
    for name, fn in [("Storage", session_ping), ("Cache", cache_ping), ("MinIO", minio_ping)]:
        try:
            status = fn()
            emoji = "✅" if status else "❌"
            print(f"  {emoji} {name}")
            if not status:
                ok = False
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            ok = False

    sys.exit(0 if ok else 1)


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="novel-agent-v2",
        description="🧠 Industrial Novel Generation System V2",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Initialize a new novel")
    p_init.add_argument("--title", required=True, help="Novel title")
    p_init.add_argument("--desc", required=True, help="Novel description / user prompt")
    p_init.add_argument("--volumes", type=int, default=10, help="Total volumes (default: 10)")
    p_init.add_argument("--world-type", default="玄幻", help="World type (default: 玄幻)")
    p_init.add_argument("--template", default=None,
                        help="Genre template name (末世重生/种田日常/修仙/科幻/玄幻/都市)")
    p_init.add_argument("--provider", default=None,
                        help="LLM provider for init: deepseek | qwen | glm | ollama (default: from .env)")

    # reinit
    p_reinit = sub.add_parser("reinit", help="Reinitialize an existing novel (optionally update metadata)")
    p_reinit.add_argument("--novel-id", required=True)
    p_reinit.add_argument("--title", default=None, help="New title (optional, keeps existing if omitted)")
    p_reinit.add_argument("--desc", default=None, help="New description (optional)")
    p_reinit.add_argument("--volumes", type=int, default=None, help="New total volumes (optional)")
    p_reinit.add_argument("--world-type", default=None, help="New world type (optional)")

    # write
    p_write = sub.add_parser("write", help="Write a full volume")
    p_write.add_argument("--novel-id", required=True)
    p_write.add_argument("--volume", type=int, required=True)
    p_write.add_argument("--replan", action="store_true", help="Force re-plan chapters")
    p_write.add_argument("--interactive", action="store_true", help="Pause for plan approval before each volume starts")

    # chapter
    p_ch = sub.add_parser("chapter", help="Write a single chapter")
    p_ch.add_argument("--novel-id", required=True)
    p_ch.add_argument("--chapter", type=int, required=True)
    p_ch.add_argument("--volume", type=int, required=True)
    p_ch.add_argument("--provider", default=None,
                      help="LLM provider override: deepseek | qwen | glm | ollama")

    # rewrite
    p_rewrite = sub.add_parser("rewrite", help="Rewrite an entire volume from scratch")
    p_rewrite.add_argument("--novel-id", required=True)
    p_rewrite.add_argument("--volume", type=int, required=True, help="Volume number to rewrite")
    p_rewrite.add_argument("--provider", default=None,
                           help="LLM provider override: deepseek | qwen | glm | ollama")

    # auto
    p_auto = sub.add_parser("auto", help="Auto-run multiple volumes")
    p_auto.add_argument("--novel-id", required=True)
    p_auto.add_argument("--start-volume", type=int, default=1)
    p_auto.add_argument("--max-volumes", type=int, default=None)
    p_auto.add_argument("--circuit-breaker", type=int, default=3)

    # complete
    p_complete = sub.add_parser("complete", help="Mark a novel as completed (lock against modifications)")
    p_complete.add_argument("--novel-id", required=True)

    # uncomplete
    p_uncomplete = sub.add_parser("uncomplete", help="Unlock a completed novel")
    p_uncomplete.add_argument("--novel-id", required=True)

    # list
    sub.add_parser("list", help="List all novels")

    # web
    p_web = sub.add_parser("web", help="Start web dashboard")
    p_web.add_argument("--port", type=int, default=None)

    # health
    sub.add_parser("health", help="Check service connectivity")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "reinit": cmd_reinit,
        "write": cmd_write,
        "rewrite": cmd_rewrite,
        "chapter": cmd_chapter,
        "auto": cmd_auto,
        "list": cmd_list,
        "web": cmd_web,
        "health": cmd_health,
        "complete": cmd_complete,
        "uncomplete": cmd_uncomplete,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
