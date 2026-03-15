from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import AgentLog, AgentRunRequest, AgentRunResponse
from app.services.graph import run_agent_team

router = APIRouter(prefix="/agent", tags=["智能体"])


@router.post("/run", response_model=AgentRunResponse, summary="运行多智能体团队", description="输入目标任务，由规划、调研、撰写、审核等多个 Agent 协作完成，返回完整分析结果")
def run_team(req: AgentRunRequest):
    """执行多智能体协作流程并返回汇总结果。"""
    try:
        state = run_agent_team(
            goal=req.goal,
            constraints=req.constraints,
            output_format=req.output_format,
            top_k=req.top_k,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"智能体运行出错：{exc}") from exc

    logs = [AgentLog(**item) for item in state.get("logs", [])]
    return AgentRunResponse(
        goal=req.goal,
        plan=state.get("plan", ""),
        research_notes=state.get("research_notes", ""),
        draft=state.get("draft", ""),
        review_notes=state.get("review_notes", ""),
        final_answer=state.get("final_answer", ""),
        logs=logs,
    )
