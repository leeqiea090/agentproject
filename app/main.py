from __future__ import annotations

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers.chat import router as agent_router
from app.routers.kb import router as kb_router
from app.routers.tender import router as tender_router
from app.services.llm import reset_request_api_key, set_request_api_key
import uvicorn

settings = get_settings()

app = FastAPI(
    title="招投标 AI Agent 服务",
    version=settings.app_version,
    description="",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.middleware("http")
async def bind_request_llm_api_key(request: Request, call_next):
    """将页面透传的 API Key 绑定到当前请求上下文。"""
    token = set_request_api_key(request.headers.get("X-LLM-API-Key"))
    try:
        return await call_next(request)
    finally:
        reset_request_api_key(token)


app.include_router(kb_router)
app.include_router(agent_router)
app.include_router(tender_router)


if __name__ == "__main__":

    if settings.app_host == "0.0.0.0":
        print(f"Local access URL: http://127.0.0.1:{settings.app_port}")

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )
