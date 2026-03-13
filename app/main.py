from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers.chat import router as agent_router
from app.routers.kb import router as kb_router
from app.routers.tender import router as tender_router
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

app.include_router(kb_router)
app.include_router(agent_router)
app.include_router(tender_router)


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/api/status", summary="服务状态", description="返回当前服务的基本信息与运行状态")
def api_status():
    return {
        "服务名称": "招投标 AI Agent 服务",
        "版本": settings.app_version,
        "状态": "运行中",
    }


if __name__ == "__main__":

    if settings.app_host == "0.0.0.0":
        print(f"Local access URL: http://127.0.0.1:{settings.app_port}")

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )
