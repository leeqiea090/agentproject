from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestTextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="待导入的原始文本内容")
    source: str = Field(default="manual_input", description="来源标识符，用于追踪数据来源")
    metadata: dict[str, Any] = Field(default_factory=dict, description="自定义元数据（键值对）")
    chunk_size: int | None = Field(default=None, ge=200, le=3000, description="分块大小（字符数），范围 200~3000")
    chunk_overlap: int | None = Field(default=None, ge=0, le=800, description="分块重叠长度（字符数），范围 0~800")


class IngestResponse(BaseModel):
    source: str = Field(description="来源标识符")
    chunks_indexed: int = Field(description="成功写入知识库的分块数量")
    total_characters: int = Field(description="文本总字符数")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="检索查询文本")
    top_k: int = Field(default=5, ge=1, le=20, description="返回最相关结果的数量，范围 1~20")


class SearchHit(BaseModel):
    text: str = Field(description="匹配到的文本片段")
    score: float | None = Field(default=None, description="相似度得分（越高越相关）")
    metadata: dict[str, Any] = Field(default_factory=dict, description="该片段的元数据")


class SearchResponse(BaseModel):
    query: str = Field(description="原始查询文本")
    hits: list[SearchHit] = Field(description="检索结果列表")


class KnowledgeBaseStatsResponse(BaseModel):
    collection: str = Field(description="向量集合名称")
    path: str = Field(description="向量数据库存储路径")
    count: int = Field(description="知识库中的文档块总数")


class AgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="用户的最终任务目标，例如：分析此招标文件的评分标准")
    constraints: list[str] = Field(default_factory=list, description="约束条件列表，例如：['字数不超过500字', '使用正式语气']")
    output_format: str = Field(default="", description="期望的输出格式模板，留空则由 Agent 自动决定")
    top_k: int | None = Field(default=None, ge=1, le=20, description="知识库检索时返回的最大相关片段数")


class AgentLog(BaseModel):
    agent: str = Field(description="执行该步骤的 Agent 名称")
    content: str = Field(description="该 Agent 的输出内容")


class AgentRunResponse(BaseModel):
    goal: str = Field(description="原始任务目标")
    plan: str = Field(description="规划 Agent 制定的执行计划")
    research_notes: str = Field(description="调研 Agent 收集的相关信息")
    draft: str = Field(description="撰写 Agent 生成的初稿")
    review_notes: str = Field(description="审核 Agent 的审阅意见")
    final_answer: str = Field(description="最终输出结果")
    logs: list[AgentLog] = Field(default_factory=list, description="各 Agent 的执行日志")
