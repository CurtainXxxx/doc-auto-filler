"""Portable application paths and request context, independent of hosting platform."""
import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)
WORKSPACE = Path(os.getenv("APP_WORKSPACE", str(PROJECT_ROOT))).resolve()
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(WORKSPACE / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

@dataclass
class Context:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    method: str = ""
    session_id: str = ""

request_context = ContextVar("request_context", default=None)

def new_context(method="", headers=None):
    """Create request-local identifiers without forwarding upstream credentials."""
    headers = headers or {}
    return Context(run_id=headers.get("x-run-id") or uuid.uuid4().hex, method=method)
