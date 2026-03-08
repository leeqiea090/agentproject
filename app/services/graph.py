from __future__ import annotations

import operator
import threading
from typing import Any

from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict

from app.config import get_settings
from app.services.llm import run_completion, run_with_tools
from app.services.retriever import knowledge_base_stats, search_knowledge


class TeamState(TypedDict):
    goal: str
    constraints: list[str]
    output_format: str
    top_k: int

    plan: str
    instruction: str
    research_notes: str
    draft: str
    review_notes: str
    final_answer: str

    next_agent: str
    turns: int
    logs: Annotated[list[dict[str, str]], operator.add]


SUPERVISOR_PROMPT = """
你是总负责人（Supervisor）。
你的职责：
1) 把用户最终目标拆解成可执行任务。
2) 按阶段把任务分配给 Researcher、Writer、Reviewer。
3) 最终汇总为可以直接交付给用户的结果。
请输出专业、可执行、条理清晰的内容。
""".strip()

RESEARCHER_PROMPT = """
你是 Researcher Agent，负责基于本地知识库做专业研究。
要求：
1) 优先使用工具检索本地知识库。
2) 提炼与目标最相关的事实、条款、方法和注意事项。
3) 输出时标注来源（source/chunk_index）。
4) 如果知识库信息不足，要明确指出缺口。
""".strip()

WRITER_PROMPT = """
你是 Writer Agent，负责把目标和研究结论写成可执行方案或成稿。
要求：
1) 结构清晰，直接面向交付。
2) 不编造事实；缺失信息用“待补充”标注。
3) 输出与用户要求的格式一致。
""".strip()

REVIEWER_PROMPT = """
你是 Reviewer Agent，负责审校质量。
要求：
1) 检查完整性、逻辑一致性、可执行性。
2) 标注风险和缺失项。
3) 给出可落地修订建议。
""".strip()


@tool("kb_search")
def kb_search_tool(query: str, top_k: int = 5) -> str:
    """Search the local knowledge base by semantic similarity."""
    hits = search_knowledge(query=query, top_k=top_k)
    if not hits:
        return "知识库没有检索到相关内容。"

    lines: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        metadata = hit.get("metadata", {})
        source = metadata.get("source", "unknown")
        chunk_index = metadata.get("chunk_index", "?")
        score = hit.get("score")
        score_text = "n/a" if score is None else f"{score:.4f}"
        snippet = (hit.get("text") or "").replace("\n", " ").strip()
        if len(snippet) > 260:
            snippet = snippet[:260] + "..."

        lines.append(
            f"{idx}. source={source}, chunk={chunk_index}, score={score_text}, text={snippet}"
        )

    return "\n".join(lines)


@tool("kb_stats")
def kb_stats_tool() -> str:
    """Return collection stats of the local knowledge base."""
    stats = knowledge_base_stats()
    return (
        f"collection={stats['collection']}, path={stats['path']}, "
        f"count={stats['count']}"
    )


def _constraints_to_text(constraints: list[str]) -> str:
    if not constraints:
        return "无"
    return "\n".join(f"- {item}" for item in constraints)


def _supervisor_node(state: TeamState) -> dict[str, Any]:
    turns = state.get("turns", 0) + 1
    settings = get_settings()

    if turns > settings.team_max_turns:
        forced_final = run_completion(
            system_prompt=SUPERVISOR_PROMPT,
            user_prompt=(
                "已达到最大迭代轮次，请直接给出当前最优可交付结果。\n"
                f"目标：{state['goal']}\n"
                f"计划：{state.get('plan', '')}\n"
                f"研究：{state.get('research_notes', '')}\n"
                f"初稿：{state.get('draft', '')}\n"
                f"审校：{state.get('review_notes', '')}"
            ),
        )
        return {
            "turns": turns,
            "final_answer": forced_final,
            "next_agent": "finish",
            "logs": [{"agent": "supervisor", "content": "达到轮次上限，已强制汇总输出。"}],
        }

    if not state.get("plan"):
        plan = run_completion(
            system_prompt=SUPERVISOR_PROMPT,
            user_prompt=(
                "请把目标拆解成 4-7 个任务，并标注建议负责人（Researcher/Writer/Reviewer）。\n"
                f"最终目标：{state['goal']}\n"
                f"约束：\n{_constraints_to_text(state.get('constraints', []))}\n"
                f"期望输出格式：{state.get('output_format') or '未指定'}"
            ),
        )
        return {
            "turns": turns,
            "plan": plan,
            "instruction": "先基于本地知识库完成关键事实和专业知识研究，再提交给 Writer。",
            "next_agent": "researcher",
            "logs": [{"agent": "supervisor", "content": plan}],
        }

    if not state.get("research_notes"):
        return {
            "turns": turns,
            "instruction": "继续补齐研究结论，优先检索本地知识库中与标书相关的专业内容。",
            "next_agent": "researcher",
            "logs": [{"agent": "supervisor", "content": "任务分配给 Researcher。"}],
        }

    if not state.get("draft"):
        return {
            "turns": turns,
            "instruction": "基于计划和研究结论输出完整初稿，结构化呈现。",
            "next_agent": "writer",
            "logs": [{"agent": "supervisor", "content": "任务分配给 Writer。"}],
        }

    if not state.get("review_notes"):
        return {
            "turns": turns,
            "instruction": "对初稿做审校，给出风险和修订建议。",
            "next_agent": "reviewer",
            "logs": [{"agent": "supervisor", "content": "任务分配给 Reviewer。"}],
        }

    final_answer = run_completion(
        system_prompt=SUPERVISOR_PROMPT,
        user_prompt=(
            "请基于以下内容，输出最终交付版本。\n"
            f"最终目标：{state['goal']}\n"
            f"约束：\n{_constraints_to_text(state.get('constraints', []))}\n"
            f"输出格式：{state.get('output_format') or '未指定'}\n\n"
            f"任务拆解：\n{state.get('plan', '')}\n\n"
            f"研究结论：\n{state.get('research_notes', '')}\n\n"
            f"初稿：\n{state.get('draft', '')}\n\n"
            f"审校意见：\n{state.get('review_notes', '')}\n"
            "请先给最终稿，再列出仍需人工补充的信息。"
        ),
    )

    return {
        "turns": turns,
        "final_answer": final_answer,
        "next_agent": "finish",
        "logs": [{"agent": "supervisor", "content": "已汇总完成最终结果。"}],
    }


def _researcher_node(state: TeamState) -> dict[str, Any]:
    top_k = state.get("top_k") or get_settings().default_top_k
    research_notes = run_with_tools(
        system_prompt=RESEARCHER_PROMPT,
        user_prompt=(
            f"最终目标：{state['goal']}\n"
            f"主管拆解任务：\n{state.get('plan', '')}\n"
            f"主管当前指令：{state.get('instruction', '')}\n"
            f"约束：\n{_constraints_to_text(state.get('constraints', []))}\n\n"
            f"请先调用工具检索，建议 top_k={top_k}，然后输出研究报告。"
        ),
        tools=[kb_stats_tool, kb_search_tool],
        max_rounds=5,
    )

    return {
        "research_notes": research_notes,
        "logs": [{"agent": "researcher", "content": research_notes}],
    }


def _writer_node(state: TeamState) -> dict[str, Any]:
    draft = run_completion(
        system_prompt=WRITER_PROMPT,
        user_prompt=(
            f"最终目标：{state['goal']}\n"
            f"主管任务拆解：\n{state.get('plan', '')}\n"
            f"研究结论：\n{state.get('research_notes', '')}\n"
            f"约束：\n{_constraints_to_text(state.get('constraints', []))}\n"
            f"输出格式要求：{state.get('output_format') or '未指定'}\n"
            f"主管当前指令：{state.get('instruction', '')}"
        ),
    )

    return {
        "draft": draft,
        "logs": [{"agent": "writer", "content": draft}],
    }


def _reviewer_node(state: TeamState) -> dict[str, Any]:
    review_notes = run_completion(
        system_prompt=REVIEWER_PROMPT,
        user_prompt=(
            f"最终目标：{state['goal']}\n"
            f"主管任务拆解：\n{state.get('plan', '')}\n"
            f"初稿：\n{state.get('draft', '')}\n"
            f"约束：\n{_constraints_to_text(state.get('constraints', []))}\n"
            f"主管当前指令：{state.get('instruction', '')}"
        ),
    )

    return {
        "review_notes": review_notes,
        "logs": [{"agent": "reviewer", "content": review_notes}],
    }


def _route_from_supervisor(state: TeamState) -> str:
    next_agent = state.get("next_agent", "finish")
    if next_agent in {"researcher", "writer", "reviewer"}:
        return next_agent
    return "finish"


def build_team_graph():
    graph_builder = StateGraph(TeamState)

    graph_builder.add_node("supervisor", _supervisor_node)
    graph_builder.add_node("researcher", _researcher_node)
    graph_builder.add_node("writer", _writer_node)
    graph_builder.add_node("reviewer", _reviewer_node)

    graph_builder.add_edge(START, "supervisor")
    graph_builder.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {
            "researcher": "researcher",
            "writer": "writer",
            "reviewer": "reviewer",
            "finish": END,
        },
    )

    graph_builder.add_edge("researcher", "supervisor")
    graph_builder.add_edge("writer", "supervisor")
    graph_builder.add_edge("reviewer", "supervisor")

    return graph_builder.compile()


_GRAPH = None
_GRAPH_LOCK = threading.Lock()


def get_team_graph():
    global _GRAPH
    if _GRAPH is None:
        with _GRAPH_LOCK:
            if _GRAPH is None:
                _GRAPH = build_team_graph()
    return _GRAPH


def run_agent_team(
    goal: str,
    constraints: list[str] | None = None,
    output_format: str = "",
    top_k: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    initial_state: TeamState = {
        "goal": goal,
        "constraints": constraints or [],
        "output_format": output_format,
        "top_k": top_k or settings.default_top_k,
        "plan": "",
        "instruction": "",
        "research_notes": "",
        "draft": "",
        "review_notes": "",
        "final_answer": "",
        "next_agent": "supervisor",
        "turns": 0,
        "logs": [],
    }

    graph = get_team_graph()
    return graph.invoke(initial_state)
