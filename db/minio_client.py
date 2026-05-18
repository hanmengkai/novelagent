"""
db/minio_client.py — MinIO object storage for novel text archive
Bucket layout:
  novel-texts/
    {novel_id}/
      chapter_{chapter_no:05d}_v{version}.txt
      chapter_{chapter_no:05d}_final.txt
"""
import io
from loguru import logger
from minio import Minio
from minio.error import S3Error
from config import get_settings

_client = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        s = get_settings()
        _client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        _ensure_bucket(s.minio_bucket)
        logger.info(f"MinIO client ready: {s.minio_endpoint}/{s.minio_bucket}")
    return _client


def _ensure_bucket(bucket: str):
    client = get_minio()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info(f"MinIO bucket created: {bucket}")


def save_chapter(novel_id: str, chapter_no: int, content: str, version: str = "final") -> str:
    """Upload chapter text; returns the object path."""
    s = get_settings()
    object_name = f"{novel_id}/chapter_{chapter_no:05d}_{version}.txt"
    data = content.encode("utf-8")
    get_minio().put_object(
        s.minio_bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type="text/plain; charset=utf-8",
    )
    return object_name


def load_chapter(novel_id: str, chapter_no: int, version: str = "final") -> str | None:
    """Download chapter text; returns None if not found."""
    s = get_settings()
    object_name = f"{novel_id}/chapter_{chapter_no:05d}_{version}.txt"
    try:
        resp = get_minio().get_object(s.minio_bucket, object_name)
        return resp.read().decode("utf-8")
    except S3Error as e:
        if e.code == "NoSuchKey":
            return None
        raise
    finally:
        try:
            resp.close()
            resp.release_conn()
        except Exception:
            pass


def list_chapters(novel_id: str) -> list[str]:
    s = get_settings()
    objs = get_minio().list_objects(s.minio_bucket, prefix=f"{novel_id}/", recursive=True)
    return [o.object_name for o in objs]


def ping() -> bool:
    try:
        get_minio()
        return True
    except Exception as e:
        logger.error(f"MinIO ping failed: {e}")
        return False
