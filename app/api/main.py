"""FastAPI 接口层：把 pipeline 包成 HTTP 服务，并托管前端页面。

设计取舍：
- 只做薄封装，业务逻辑全在 app/services/pipeline.py，保证命令行/评测/接口三处一致
- 同步 def 而不写 async：pipeline 内部是阻塞的 DuckDB 与 HTTP 调用，
  FastAPI 会把同步处理函数放进线程池，不会卡住事件循环
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.pipeline import answer

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(
    title="智能问数 Agent",
    description="自然语言 → SQL → 安全校验 → 执行 → 图表 → 结论",
    version="0.1.0",
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=200, description="自然语言问题")
    with_conclusion: bool = Field(True, description="是否生成结论")


def _jsonable(value):
    """把 Decimal / date 之类转成前端能用的类型。"""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@app.get("/api/health")
def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "model": settings.llm_model, "semantics": settings.llm_enable_thinking is not None}


@app.post("/api/ask")
def ask(payload: AskRequest) -> dict:
    started = time.perf_counter()
    result = answer(payload.question, with_conclusion=payload.with_conclusion)
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    return {
        "status": result.status,
        "question": result.question,
        "sql": result.sql,
        "columns": result.columns,
        "rows": [[_jsonable(cell) for cell in row] for row in result.rows],
        "row_count": len(result.rows),
        "message": result.message,
        "guard_rule": result.guard_rule,
        "metrics": result.metrics,
        "chart": result.chart,
        "conclusion": result.conclusion,
        "repair_count": len(result.repairs),
        "elapsed_ms": elapsed_ms,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")