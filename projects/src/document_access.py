"""Document-only filesystem boundaries and bounded upload storage."""
import os
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from runtime import WORKSPACE, DATA_DIR

DOCUMENT_SUFFIXES = {".docx", ".txt", ".md", ".csv", ".xlsx", ".pdf"}
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_UPLOAD_FILES = 10
MAX_STORED_UPLOAD_BYTES = 100 * 1024 * 1024
_upload_lock = threading.Lock()


def resolve_document_path(value: str, *, suffixes=None) -> str:
    """Permit assets and managed document directories, never config or global temp."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("文件路径无效")
    if value.startswith(("http://", "https://")):
        raise ValueError("此操作只接受本地文档")
    path = Path(value)
    path = (path if path.is_absolute() else WORKSPACE / path).resolve()
    roots = [(WORKSPACE / "assets").resolve(), UPLOAD_DIR.resolve(),
             (DATA_DIR / "generated").resolve(), (DATA_DIR / "pdf_output").resolve()]
    if not any(path.is_relative_to(root) for root in roots):
        raise PermissionError("文件不在允许的文档目录内")
    if path.suffix.lower() not in (suffixes or DOCUMENT_SUFFIXES):
        raise PermissionError("禁止访问非文档文件")
    return str(path)


def check_archive(path: str):
    """Reject excessive expanded OOXML size before XML parsing."""
    if Path(path).suffix.lower() not in {".docx", ".xlsx"}:
        return
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 2048 or sum(item.file_size for item in members) > 50 * 1024 * 1024:
            raise ValueError("文档解压后超过大小或文件数量限制")


def save_upload(content: bytes, suffix: str) -> str:
    """Store one bounded upload without deleting any existing user documents."""
    if suffix.lower() not in DOCUMENT_SUFFIXES:
        raise ValueError("不支持此文件格式，请将旧版doc/xls转换为docx/xlsx")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("单个文件不能超过10MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with _upload_lock:
        size = sum(p.stat().st_size for p in UPLOAD_DIR.iterdir() if p.is_file())
        if size + len(content) > MAX_STORED_UPLOAD_BYTES:
            raise ValueError("上传目录已满，请先备份并清理不再需要的文件")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix.lower(), dir=UPLOAD_DIR) as output:
            output.write(content)
            path = output.name
    try:
        check_archive(path)
    except zipfile.BadZipFile as exc:
        Path(path).unlink(missing_ok=True)
        raise ValueError("文件不是有效的docx/xlsx压缩文档") from exc
    except Exception:
        Path(path).unlink(missing_ok=True)  # Only this just-created invalid upload.
        raise
    return path


def validate_download_url(value: str) -> str:
    """Allow exact operator-configured storage origins; redirects are disabled by caller."""
    url = urlsplit(value)
    allowed = [x.strip().rstrip("/").lower() for x in os.getenv("DOWNLOAD_ALLOWED_ORIGINS", "").split(",") if x.strip()]
    endpoint = os.getenv("S3_ENDPOINT_URL", "").rstrip("/")
    if endpoint:
        parsed = urlsplit(endpoint)
        allowed.append(f"{parsed.scheme}://{parsed.netloc}".lower())
    origin = f"{url.scheme}://{url.netloc}".lower()
    if url.scheme not in {"http", "https"} or url.username or url.password or origin not in allowed:
        raise PermissionError("下载目标不在已配置的存储来源中")
    return value
