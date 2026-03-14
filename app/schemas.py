from __future__ import annotations

from enum import Enum
from typing import Any
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ==================== 管道增强：可引用块 / 条款分类 / 归一化需求 ====================


class ClauseCategory(str, Enum):
    """条款分类枚举 — 9类细分，取代原先笼统的 '技术类'。"""
    technical_requirement = "technical_requirement"
    config_requirement = "config_requirement"
    service_requirement = "service_requirement"
    acceptance_requirement = "acceptance_requirement"
    documentation_requirement = "documentation_requirement"
    commercial_requirement = "commercial_requirement"
    compliance_note = "compliance_note"
    attachment_requirement = "attachment_requirement"
    noise = "noise"


class DocumentMode(str, Enum):
    """文档目标模式 — 决定生成策略。"""
    single_package = "single_package"                          # 单包实装稿（兼容旧值）
    single_package_deep_draft = "single_package_deep_draft"    # 单包深写底稿（允许待补，但不允许缺槽位）
    single_package_rich_draft = "single_package_rich_draft"    # 单包富底稿（必须生成技术表/配置表/服务表/资料表）
    multi_package_master_draft = "multi_package_master_draft"  # 多包总母版底稿
    multi_package_draft = "multi_package_draft"                # 多包底稿（兼容旧值）


class DraftLevel(str, Enum):
    """稿件成熟度等级。"""
    internal_draft = "internal_draft"       # 允许待核实/待补证/占位符
    external_ready = "external_ready"       # 严禁占位符/混包/混类


class DocumentBlock(BaseModel):
    """可引用文档块 — 替代原始 chunk 纯文本。"""
    doc_id: str = Field(default="", description="文档唯一标识，用于溯源")
    text: str = Field(description="块内文本内容")
    package_id: str = Field(default="", description="所属采购包 ID，未识别时为空")
    package_hint: str = Field(default="", description="所属采购包提示，如 '包1'")
    section_title: str = Field(default="", description="所属章节标题")
    clause_no: str = Field(default="", description="条款编号，如 '1.3'")
    block_type: str = Field(default="paragraph", description="块类型：paragraph / table_row / header / list_item")
    page: int = Field(default=0, description="来源页码（0 表示未知）")
    char_start: int = Field(default=0, description="在全文中的起始字符偏移")
    char_end: int = Field(default=0, description="在全文中的结束字符偏移")
    table_id: str = Field(default="", description="所属表格 ID")
    table_row: int = Field(default=-1, description="表格行号（-1 表示非表格）")
    table_col: int = Field(default=-1, description="表格列号（-1 表示非表格）")
    row: int = Field(default=-1, description="表格行号别名（-1 表示非表格）")
    col: int = Field(default=-1, description="表格列号别名（-1 表示非表格）")
    table_header: list[str] = Field(default_factory=list, description="表头文字列表（仅表格行携带）")
    is_noise: bool = Field(default=False, description="是否为噪音块（表头/脚注/说明行），不应进入主抽取链")


class NormalizedRequirement(BaseModel):
    """归一化需求条目 — 原子级、可直接绑定投标事实。

    固定输出字段：param_name / operator / threshold / unit / category / is_material / source_page / source_text
    规则：一句多参数可拆，但拆后必须语义完整；不允许半截条目名进入最终主表。
    """
    package_id: str = Field(description="所属包号")
    requirement_id: str = Field(default="", description="需求唯一 ID，如 'pkg1-req-003'")
    param_name: str = Field(description="参数名称")
    operator: str = Field(default="", description="比较算子：≥ / ≤ / = / 包含 / 满足 / 空")
    threshold: str = Field(default="", description="阈值/指标值，如 '5' / '≥10ml'")
    unit: str = Field(default="", description="单位，如 'ml' / '℃' / '通道'")
    raw_text: str = Field(default="", description="原始条款文本")
    category: ClauseCategory = Field(default=ClauseCategory.technical_requirement, description="条款分类")
    is_material: bool = Field(default=False, description="是否为实质性条款（不可偏离）")
    needs_bid_fact: bool = Field(default=True, description="是否需要投标侧事实来响应")
    needs_manual_confirmation: bool = Field(default=False, description="是否需要人工确认（拆分存疑/跨包/总括）")
    source_page: int = Field(default=0, description="来源页码")
    source_text: str = Field(default="", description="来源原文片段（完整句子，不截断）")
    source_clause_no: str = Field(default="", description="来源条款编号")


class TenderSourceBinding(BaseModel):
    """招标侧溯源绑定 — 记录需求来自招标文件的位置。"""
    package_id: str = Field(description="所属包号")
    requirement_id: str = Field(default="", description="对应 NormalizedRequirement.requirement_id")
    source_page: int = Field(default=0, description="招标文件页码")
    source_section: str = Field(default="", description="招标文件章节")
    source_excerpt: str = Field(default="", description="招标原文片段")
    char_start: int = Field(default=0, description="片段在全文中的起始字符偏移")
    char_end: int = Field(default=0, description="片段在全文中的结束字符偏移")


class BidEvidenceBinding(BaseModel):
    """投标侧证据绑定 — 记录用什么证据来证明满足需求。"""
    package_id: str = Field(description="所属包号")
    requirement_id: str = Field(default="", description="对应 NormalizedRequirement.requirement_id")
    evidence_type: str = Field(
        default="",
        description="证据类型：brochure / manual / registration / test_report / authorization / spec_sheet / capability"
    )
    file_name: str = Field(default="", description="证据文件名")
    file_page: int = Field(default=0, description="证据文件页码")
    snippet: str = Field(default="", description="证据文件相关片段")
    evidence_file: str = Field(default="", description="证据文件名别名")
    evidence_page: int = Field(default=0, description="证据页码别名")
    evidence_snippet: str = Field(default="", description="证据片段别名")
    covers_requirement: bool = Field(default=False, description="该证据是否充分覆盖需求")
    status: str = Field(
        default="missing",
        description="证据绑定状态：missing（未绑定）/ candidate（候选待确认）/ confirmed（已确认）"
    )

    @model_validator(mode="after")
    def _sync_alias_fields(self) -> BidEvidenceBinding:
        if not self.file_name and self.evidence_file:
            self.file_name = self.evidence_file
        if not self.evidence_file and self.file_name:
            self.evidence_file = self.file_name
        if not self.file_page and self.evidence_page:
            self.file_page = self.evidence_page
        if not self.evidence_page and self.file_page:
            self.evidence_page = self.file_page
        if not self.snippet and self.evidence_snippet:
            self.snippet = self.evidence_snippet
        if not self.evidence_snippet and self.snippet:
            self.evidence_snippet = self.snippet
        # 根据 covers_requirement 推断 status（向后兼容）
        if self.status == "missing" and self.covers_requirement:
            self.status = "confirmed"
        elif self.status == "missing" and (self.file_name or self.snippet):
            self.status = "candidate"
        return self


class ProductProfile(BaseModel):
    """产品画像 — writer 的核心输入之一，汇总产品事实。"""
    package_id: str = Field(description="所属包号")
    product_name: str = Field(default="", description="产品名称")
    brand: str = Field(default="", description="品牌")
    model: str = Field(default="", description="型号")
    manufacturer: str = Field(default="", description="生产厂家")
    origin: str = Field(default="", description="产地")
    specifications: dict[str, Any] = Field(default_factory=dict, description="技术参数键值对")
    config_items: list[dict[str, Any]] = Field(default_factory=list, description="配置项列表")
    functional_notes: str = Field(default="", description="功能说明")
    acceptance_notes: str = Field(default="", description="验收说明")
    training_notes: str = Field(default="", description="培训说明")
    has_complete_identity: bool = Field(default=False, description="品牌/型号/厂家是否齐全")
    has_technical_specs: bool = Field(default=False, description="是否有实际技术参数")
    ready_for_external: bool = Field(default=False, description="是否满足外发稿要求")
    evidence_refs: list[BidEvidenceBinding] = Field(default_factory=list, description="产品证据列表")


class WriterContext(BaseModel):
    """Writer 输入上下文 — 将 requirement / package_context / table_type 绑定为一个不可分割的输入单元。

    writer 只能消费同包对象；table_type 决定条目输出到哪个分表。
    """
    package_id: str = Field(description="所属包号（必填）")
    table_type: str = Field(
        description="分表类型：technical_deviation / config_list / service_response / acceptance_doc_response"
    )
    requirements: list[NormalizedRequirement] = Field(default_factory=list, description="该分表下的归一化需求列表")
    product_profile: ProductProfile | None = Field(default=None, description="产品画像（同包）")
    tender_source_bindings: list[TenderSourceBinding] = Field(default_factory=list, description="招标侧溯源")
    bid_evidence_bindings: list[BidEvidenceBinding] = Field(default_factory=list, description="投标侧证据")
    document_mode: DocumentMode = Field(default=DocumentMode.single_package_deep_draft, description="文档模式")

    @model_validator(mode="after")
    def _validate_package_consistency(self) -> WriterContext:
        """校验所有 requirement / binding 的 package_id 必须与 context 一致。"""
        for req in self.requirements:
            if req.package_id and req.package_id != self.package_id:
                raise ValueError(
                    f"WriterContext package_id={self.package_id} 与 requirement "
                    f"package_id={req.package_id} 不一致"
                )
        for b in self.tender_source_bindings:
            if b.package_id and b.package_id != self.package_id:
                raise ValueError(
                    f"WriterContext package_id={self.package_id} 与 TenderSourceBinding "
                    f"package_id={b.package_id} 不一致"
                )
        for b in self.bid_evidence_bindings:
            if b.package_id and b.package_id != self.package_id:
                raise ValueError(
                    f"WriterContext package_id={self.package_id} 与 BidEvidenceBinding "
                    f"package_id={b.package_id} 不一致"
                )
        return self


class ValidationGate(BaseModel):
    """硬校验门 — 9 个硬拦截条件。"""
    package_contamination_detected: bool = Field(default=False, description="是否检出包件污染")
    placeholder_count: int = Field(default=0, description="关键占位符数量")
    bid_evidence_coverage: float = Field(default=0.0, description="投标侧证据覆盖率（0~1）")
    table_category_mixing: bool = Field(default=False, description="是否检出表格分类混装")
    snippet_truncation_count: int = Field(default=0, description="半截条目/截断片段数量")
    anchor_pollution_rate: float = Field(default=0.0, description="锚点污染率（0~1）")
    evidence_blank_rate: float = Field(default=0.0, description="证据页码空白率（0~1）")
    project_meta_anomaly_detected: bool = Field(default=False, description="是否检出项目名称/编号/数量异常")
    nested_placeholder_detected: bool = Field(default=False, description="是否检出嵌套占位符文本")
    snippet_dirty_rate: float = Field(default=0.0, description="证据片段不洁净率（含跨包文本/噪音标记）")
    # 阈值
    placeholder_threshold: int = Field(default=20, description="外发模式允许的最大占位符数")
    evidence_coverage_threshold: float = Field(default=0.6, description="外发模式最低证据覆盖率")
    snippet_truncation_threshold: int = Field(default=0, description="外发模式允许的最大截断片段数")
    anchor_pollution_threshold: float = Field(default=0.05, description="外发模式允许的最大锚点污染率")
    evidence_blank_threshold: float = Field(default=0.3, description="外发模式允许的最大证据空白率")
    snippet_dirty_threshold: float = Field(default=0.15, description="外发模式允许的最大片段不洁净率")

    def passes_external_gate(self) -> bool:
        """外发稿是否通过所有硬校验。"""
        if self.project_meta_anomaly_detected:
            return False
        if self.nested_placeholder_detected:
            return False
        if self.package_contamination_detected:
            return False
        if self.placeholder_count > self.placeholder_threshold:
            return False
        if self.bid_evidence_coverage < self.evidence_coverage_threshold:
            return False
        if self.table_category_mixing:
            return False
        if self.snippet_truncation_count > self.snippet_truncation_threshold:
            return False
        if self.anchor_pollution_rate > self.anchor_pollution_threshold:
            return False
        if self.evidence_blank_rate > self.evidence_blank_threshold:
            return False
        if self.snippet_dirty_rate > self.snippet_dirty_threshold:
            return False
        return True

    def has_fixable_issues(self) -> bool:
        """是否存在可通过自愈修复的问题（混装、污染、截断）。

        占位符和证据覆盖率不属于可自愈问题（需要外部数据补充）。
        """
        return (
            self.table_category_mixing
            or self.package_contamination_detected
            or self.snippet_truncation_count > self.snippet_truncation_threshold
            or self.anchor_pollution_rate > self.anchor_pollution_threshold
        )

    def failure_reasons(self) -> list[str]:
        """返回当前未通过的校验项列表。"""
        reasons: list[str] = []
        if self.project_meta_anomaly_detected:
            reasons.append("项目元信息异常")
        if self.package_contamination_detected:
            reasons.append("包件污染")
        if self.table_category_mixing:
            reasons.append("表格分类混装")
        if self.placeholder_count > self.placeholder_threshold:
            reasons.append(f"占位符过多({self.placeholder_count})")
        if self.bid_evidence_coverage < self.evidence_coverage_threshold:
            reasons.append(f"证据覆盖不足({self.bid_evidence_coverage:.0%})")
        if self.snippet_truncation_count > self.snippet_truncation_threshold:
            reasons.append(f"半截条目({self.snippet_truncation_count})")
        if self.anchor_pollution_rate > self.anchor_pollution_threshold:
            reasons.append(f"锚点污染({self.anchor_pollution_rate:.1%})")
        if self.evidence_blank_rate > self.evidence_blank_threshold:
            reasons.append(f"证据空白({self.evidence_blank_rate:.1%})")
        if self.snippet_dirty_rate > self.snippet_dirty_threshold:
            reasons.append(f"片段不洁净({self.snippet_dirty_rate:.1%})")
        return reasons


class RegressionMetrics(BaseModel):
    """评测回归指标 — 衡量生成稿与标准标书的质量差距。

    新增指标：snippet_cleanliness_score / draft_usability_score /
    package_contamination_rate / table_category_mixing_rate 已存在，
    额外追加 config_detail_score 细化。
    每次改流程后，能量化看到"更好补了没有"。
    """
    single_package_focus_score: float = Field(default=0.0, description="单包聚焦度（0~1）")
    package_contamination_rate: float = Field(default=0.0, description="包件污染率（0~1）")
    table_category_mixing_rate: float = Field(default=0.0, description="表格分类混装率（0~1）")
    bid_evidence_coverage: float = Field(default=0.0, description="投标侧证据覆盖率（0~1）")
    placeholder_leakage: float = Field(default=0.0, description="占位符泄漏率（0~1）")
    config_detail_score: float = Field(default=0.0, description="配置详细度得分（0~1）")
    fact_density_per_page: float = Field(default=0.0, description="每页事实密度")
    snippet_cleanliness_score: float = Field(default=0.0, description="原文片段清洁度（0~1，无拖尾/串邻=1）")
    draft_usability_score: float = Field(default=0.0, description="底稿可用性得分（0~1，越接近人工底稿=1）")
    project_meta_consistency_score: float = Field(default=0.0, description="项目名称/编号/数量一致性得分（0~1）")
    quality_warnings: list[str] = Field(default_factory=list, description="质量告警列表（超出阈值时自动生成）")


class BidGenerationResult(BaseModel):
    """投标生成完整结果 — 包含章节、校验门和回归指标。"""
    sections: list[BidDocumentSection] = Field(default_factory=list, description="文档章节列表")
    validation_gate: ValidationGate = Field(default_factory=ValidationGate, description="硬校验门结果")
    regression_metrics: RegressionMetrics = Field(default_factory=RegressionMetrics, description="回归指标")
    draft_level: DraftLevel = Field(default=DraftLevel.internal_draft, description="稿件等级")
    document_mode: DocumentMode = Field(default=DocumentMode.single_package_deep_draft, description="文档模式")


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


# ==================== 招投标系统数据模型 ====================

# --- 招标文件相关 ---、、

class TenderTableColumn(BaseModel):
    """招标文件表格列模板。"""
    key: str = Field(description="列键名，如 seq / requirement / response")
    title: str = Field(description="原始列表头")
    required: bool = Field(default=False, description="是否为必填列")


class TenderTableRowTemplate(BaseModel):
    """招标文件表格行模板。"""
    seq: str = Field(default="", description="序号")
    cells: dict[str, str] = Field(default_factory=dict, description="列键名 -> 单元格文字")
    source_text: str = Field(default="", description="来源原文")
    is_material: bool = Field(default=False, description="是否为实质性条款")
    package_id: str = Field(default="", description="适用包号，空表示全项目")


class TenderTableTemplate(BaseModel):
    """招标文件表格模板。"""
    table_name: str = Field(default="", description="表格名称")
    section_title: str = Field(default="", description="所属章节标题")
    source_title: str = Field(default="", description="来源标题")
    columns: list[TenderTableColumn] = Field(default_factory=list, description="原始列表头模板")
    rows: list[TenderTableRowTemplate] = Field(default_factory=list, description="原始行模板")
    package_id: str = Field(default="", description="适用包号，空表示全项目")
    raw_block: str = Field(default="", description="来源原始文本块")


class ResponseSectionTemplate(BaseModel):
    """响应文件格式章节模板。"""
    order_no: str = Field(default="", description="章序号，如 一 / 二 / 三")
    title: str = Field(description="章节标题")
    required: bool = Field(default=True, description="是否为招标文件明确要求的必备章节")
    raw_block: str = Field(default="", description="章节来源原文")
    package_id: str = Field(default="", description="适用包号，空表示全项目")
    table_templates: list[TenderTableTemplate] = Field(default_factory=list, description="本章节内附带的表格模板")

class ProcurementPackage(BaseModel):
    """采购包信息"""
    package_id: str = Field(description="包号")
    item_name: str = Field(description="货物/服务名称")
    quantity: int = Field(description="数量")
    budget: float = Field(description="预算金额（元）")
    technical_requirements: dict[str, Any] = Field(default_factory=dict, description="技术参数要求")
    delivery_time: str = Field(default="", description="交货期限")
    delivery_place: str = Field(default="", description="交货地点")


class CommercialTerms(BaseModel):
    """商务条款"""
    payment_method: str = Field(default="", description="付款方式")
    validity_period: str = Field(default="90日历天", description="投标有效期")
    warranty_period: str = Field(default="", description="质保期")
    performance_bond: str = Field(default="不收取", description="履约保证金")


class TenderDocument(BaseModel):
    """招标文件结构化数据"""
    project_name: str = Field(description="项目名称")
    project_number: str = Field(description="项目编号")
    budget: float = Field(description="总预算金额")
    purchaser: str = Field(description="采购人")
    agency: str = Field(default="", description="代理机构")
    procurement_type: str = Field(default="竞争性谈判", description="采购方式")
    packages: list[ProcurementPackage] = Field(default_factory=list, description="采购包列表")
    commercial_terms: CommercialTerms = Field(default_factory=CommercialTerms, description="商务条款")
    evaluation_criteria: dict[str, Any] = Field(default_factory=dict, description="评分标准")
    special_requirements: str = Field(default="", description="特殊要求说明")

    # ===== 新增：响应文件格式 / 审查表 / 评分表模板 =====
    response_section_titles: list[str] = Field(default_factory=list, description="响应文件格式章节标题顺序")
    response_section_templates: list[ResponseSectionTemplate] = Field(default_factory=list, description="响应文件格式章节模板")

    qualification_review_table: TenderTableTemplate | None = Field(default=None, description="资格性审查表模板")
    compliance_review_table: TenderTableTemplate | None = Field(default=None, description="符合性审查表模板")
    detailed_review_table: TenderTableTemplate | None = Field(default=None, description="详细评审/评分表模板")
    invalid_bid_table: TenderTableTemplate | None = Field(default=None, description="投标无效情形表模板")



class TenderUploadRequest(BaseModel):
    """招标文件上传请求"""
    file_name: str = Field(description="文件名")
    file_size: int = Field(description="文件大小（字节）")


class TenderUploadResponse(BaseModel):
    """招标文件上传响应"""
    tender_id: str = Field(description="招标文件唯一标识")
    upload_time: datetime = Field(description="上传时间")
    status: str = Field(default="uploaded", description="状态：uploaded/parsing/parsed/error")


class TenderParseResponse(BaseModel):
    """招标文件解析响应"""
    tender_id: str = Field(description="招标文件ID")
    parsed_data: TenderDocument = Field(description="解析后的结构化数据")
    raw_text_length: int = Field(description="原始文本长度")
    parse_time: datetime = Field(description="解析时间")


# --- 企业信息相关 ---

class CompanyLicense(BaseModel):
    """企业证照信息"""
    license_type: str = Field(description="证照类型，如：营业执照、医疗器械经营许可证")
    license_number: str = Field(description="证照编号")
    valid_until: str = Field(default="长期", description="有效期至")
    file_path: str = Field(default="", description="证照文件路径")


class CompanyStaff(BaseModel):
    """企业人员信息"""
    name: str = Field(description="姓名")
    position: str = Field(description="职务")
    education: str = Field(default="", description="学历")
    id_number: str = Field(default="", description="身份证号（部分脱敏）")
    phone: str = Field(default="", description="联系电话")


class CompanyProfile(BaseModel):
    """企业基本信息"""
    company_id: str | None = Field(default=None, description="企业ID")
    name: str = Field(description="企业全称")
    legal_representative: str = Field(description="法定代表人")
    address: str = Field(description="详细地址")
    phone: str = Field(description="联系电话")
    licenses: list[CompanyLicense] = Field(default_factory=list, description="资质证照列表")
    staff: list[CompanyStaff] = Field(default_factory=list, description="项目团队人员")
    social_insurance_proof: str = Field(default="", description="社保缴纳证明路径")
    credit_check_time: datetime | None = Field(default=None, description="最近信用查询时间")


class BidMaterialInput(BaseModel):
    """投标材料输入 - 用于 Product Profile Builder 从真实材料提取产品事实"""
    file_name: str = Field(description="文件名称，如：彩页.pdf、说明书.pdf")
    file_type: str = Field(
        description="文件类型：brochure(彩页)、manual(说明书)、registration(注册证)、"
                    "test_report(检测/质评报告)、spec_sheet(厂家参数页)"
    )
    file_path: str = Field(default="", description="文件路径")
    page_count: int = Field(default=0, description="总页数")
    extracted_text: str = Field(default="", description="已提取的文本内容")
    extracted_specs: dict[str, Any] = Field(default_factory=dict, description="已提取的参数键值对")
    key_pages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="关键页码列表，如 [{'page': 3, 'content': '技术参数表'}]"
    )


class ProductSpecification(BaseModel):
    """产品技术规格 - 增强版，包含完整的投标产品事实"""
    product_id: str | None = Field(default=None, description="产品ID")
    product_name: str = Field(description="产品名称")

    # 基础信息
    brand: str = Field(default="", description="品牌名称（新增）")
    manufacturer: str = Field(description="生产厂家")
    origin: str = Field(default="", description="产地")
    model: str = Field(default="", description="型号")

    # 技术规格
    specifications: dict[str, Any] = Field(default_factory=dict, description="技术参数")
    technical_specs: dict[str, Any] = Field(default_factory=dict, description="详细技术规格（新增，与specifications同步）")

    # 配置与功能
    config_items: list[dict[str, Any]] = Field(default_factory=list, description="配置项清单（新增）")
    functional_notes: str = Field(default="", description="功能说明（新增）")

    # 交付与验收
    acceptance_notes: str = Field(default="", description="验收说明（新增）")
    training_notes: str = Field(default="", description="培训说明（新增）")

    # 证据与证照
    price: float = Field(description="参考价格")
    certifications: list[str] = Field(default_factory=list, description="认证证书列表")
    registration_number: str = Field(default="", description="注册证编号（医疗器械）")
    authorization_letter: str = Field(default="", description="授权书路径")
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, description="证据引用列表（新增）,包含文件名、页码等")

    # 投标材料输入（用于 Product Profile Builder）
    bid_materials: list[BidMaterialInput] = Field(
        default_factory=list,
        description="投标材料列表：彩页、说明书、注册证、检测报告、厂家参数页"
    )


# --- 投标文件生成相关 ---

class BidGenerateRequest(BaseModel):
    """投标文件生成请求"""
    tender_id: str = Field(description="招标文件ID")
    company_profile_id: str = Field(description="企业信息ID")
    selected_packages: list[str] = Field(description="投标包号列表")
    product_ids: dict[str, str] = Field(default_factory=dict, description="包号→产品ID映射")
    discount_rate: float = Field(default=1.0, ge=0.5, le=1.0, description="报价折扣率，默认1.0（不打折）")
    add_performance_cases: bool = Field(default=False, description="是否添加业绩案例")
    custom_service_plan: str = Field(default="", description="自定义售后服务方案")
    document_mode: DocumentMode | None = Field(default=None, description="文档模式：single_package / multi_package_draft，不传则自动判定")


class BidDocumentSection(BaseModel):
    """投标文件章节"""
    section_title: str = Field(description="章节标题")
    content: str = Field(description="章节内容（可以是HTML或Markdown）")
    attachments: list[str] = Field(default_factory=list, description="附件路径列表")


class BidMaterializeReport(BaseModel):
    """投标底稿深注入报告"""
    changed_sections: list[str] = Field(default_factory=list, description="已完成深注入的章节列表")
    unresolved_sections: list[str] = Field(default_factory=list, description="仍含待人工补录项的章节列表")
    summary: str = Field(default="", description="深注入执行摘要")


class BidGenerateResponse(BaseModel):
    """投标文件生成响应"""
    bid_id: str = Field(description="投标文件ID")
    tender_id: str = Field(description="对应的招标文件ID")
    status: str = Field(default="generated", description="状态：generating/generated/error")
    sections: list[BidDocumentSection] = Field(default_factory=list, description="文档章节列表")
    materialize_report: BidMaterializeReport = Field(default_factory=BidMaterializeReport, description="深注入报告")
    consistency_report: dict[str, Any] = Field(default_factory=dict, description="一致性校验报告")
    outbound_report: dict[str, Any] = Field(default_factory=dict, description="外发净化报告")
    file_path: str = Field(default="", description="生成的PDF文件路径")
    download_url: str = Field(default="", description="下载链接")
    generated_time: datetime = Field(description="生成时间")
    validation_gate: ValidationGate | None = Field(default=None, description="硬校验门结果")
    regression_metrics: RegressionMetrics | None = Field(default=None, description="回归指标")
    draft_level: str = Field(default="", description="稿件等级：internal_draft / external_ready")
    document_mode: str = Field(default="", description="文档模式：single_package / multi_package_draft")


class BidDownloadRequest(BaseModel):
    """投标文件下载请求"""
    bid_id: str = Field(description="投标文件ID")
    format: str = Field(default="pdf", description="文件格式：pdf/docx")


# --- 正式工作流相关 ---

class WorkflowMaterialCheckItem(BaseModel):
    """资料校验项"""
    item: str = Field(description="校验项名称")
    status: str = Field(description="校验状态：通过/缺失/待确认")
    evidence: str = Field(default="", description="依据或证据说明")
    suggestion: str = Field(default="", description="补充建议")


class WorkflowCitation(BaseModel):
    """检索引用项"""
    source: str = Field(default="unknown", description="引用来源标识")
    chunk_index: int | None = Field(default=None, description="来源分块序号")
    score: float | None = Field(default=None, description="检索相关度得分")
    quote: str = Field(default="", description="引用片段摘要")


class WorkflowStageRecord(BaseModel):
    """工作流阶段记录"""
    stage_code: str = Field(description="阶段编码")
    stage_name: str = Field(description="阶段名称")
    status: str = Field(description="阶段状态：completed/warning/blocked/skipped")
    summary: str = Field(default="", description="阶段摘要")
    data: dict[str, Any] = Field(default_factory=dict, description="阶段结构化输出")
    issues: list[str] = Field(default_factory=list, description="阶段发现的问题")


class TenderWorkflowStep1Result(BaseModel):
    """第一步：招标解析输出"""
    key_information: dict[str, Any] = Field(default_factory=dict, description="关键项目信息")
    required_materials: list[str] = Field(default_factory=list, description="需准备资料清单")
    scoring_rules: list[str] = Field(default_factory=list, description="评分规则与权重")
    risk_alerts: list[str] = Field(default_factory=list, description="风险提示")
    citations: list[WorkflowCitation] = Field(default_factory=list, description="检索引用列表")
    summary: str = Field(default="", description="步骤总结")


class TenderWorkflowStep2Result(BaseModel):
    """第二步：资料校验输出"""
    overall_status: str = Field(default="待补充", description="总体状态：通过/需补充")
    checklist: list[WorkflowMaterialCheckItem] = Field(default_factory=list, description="逐项校验结果")
    missing_items: list[str] = Field(default_factory=list, description="缺失项清单")
    next_actions: list[str] = Field(default_factory=list, description="下一步建议动作")
    summary: str = Field(default="", description="步骤总结")


class TenderWorkflowStep3Result(BaseModel):
    """第三步：标书整合输出"""
    generated: bool = Field(default=False, description="是否已生成标书")
    bid_id: str = Field(default="", description="标书ID")
    section_titles: list[str] = Field(default_factory=list, description="生成章节标题列表")
    citations: list[WorkflowCitation] = Field(default_factory=list, description="整合阶段检索引用")
    download_url: str = Field(default="", description="下载地址")
    file_path: str = Field(default="", description="文件路径")
    integration_notes: str = Field(default="", description="整合Agent说明")
    summary: str = Field(default="", description="步骤总结")


class WorkflowSecondaryCheckItem(BaseModel):
    """二次校验明细项"""
    name: str = Field(description="校验项名称")
    status: str = Field(description="校验结果：通过/需修订")
    detail: str = Field(default="", description="校验详情")


class WorkflowSecondaryValidationResult(BaseModel):
    """二次校验输出"""
    executed: bool = Field(default=True, description="是否执行了二次校验")
    overall_status: str = Field(default="通过", description="二次校验总体状态：通过/需修订")
    check_items: list[WorkflowSecondaryCheckItem] = Field(default_factory=list, description="二次校验明细")
    issues: list[str] = Field(default_factory=list, description="二次校验发现的问题")
    suggestions: list[str] = Field(default_factory=list, description="二次校验修订建议")
    summary: str = Field(default="", description="二次校验总结")


class TenderWorkflowStep4Result(BaseModel):
    """第四步：审核输出"""
    ready_for_submission: bool = Field(default=False, description="是否可直接提交")
    risk_level: str = Field(default="medium", description="风险等级：low/medium/high")
    compliance_score: float = Field(default=0.0, description="合规评分（0~100）")
    major_issues: list[str] = Field(default_factory=list, description="主要问题")
    recommendations: list[str] = Field(default_factory=list, description="修订建议")
    secondary_validation: WorkflowSecondaryValidationResult = Field(
        default_factory=WorkflowSecondaryValidationResult,
        description="二次校验结果（规则校验）",
    )
    conclusion: str = Field(default="", description="审核结论")


class TenderWorkflowRequest(BaseModel):
    """正式工作流请求"""
    tender_id: str = Field(description="招标文件ID")
    company_profile_id: str | None = Field(default=None, description="企业信息ID，可为空")
    selected_packages: list[str] = Field(default_factory=list, description="投标包号列表，留空表示全部包")
    product_ids: dict[str, str] = Field(default_factory=dict, description="包号→产品ID映射")
    continue_on_material_gaps: bool = Field(default=False, description="资料不全时是否继续生成标书")
    generate_docx: bool = Field(default=True, description="是否生成docx文件")


class TenderWorkflowResponse(BaseModel):
    """十层工作流响应（兼容原四阶段摘要字段）"""
    workflow_id: str = Field(description="工作流ID")
    tender_id: str = Field(description="招标文件ID")
    status: str = Field(description="整体状态：completed/blocked/error")
    stages: list[WorkflowStageRecord] = Field(default_factory=list, description="十层流程结果")
    analysis: TenderWorkflowStep1Result = Field(description="步骤1结果")
    material_validation: TenderWorkflowStep2Result = Field(description="步骤2结果")
    generation: TenderWorkflowStep3Result = Field(description="步骤3结果")
    review: TenderWorkflowStep4Result = Field(description="步骤4结果")
    generated_time: datetime = Field(description="生成时间")


class OneClickJobStartResponse(BaseModel):
    """一键生成任务启动响应"""
    job_id: str = Field(description="任务ID")
    status: str = Field(description="任务状态：queued/running/completed/error")
    step_code: str = Field(description="当前步骤编码")
    step_label: str = Field(description="当前步骤名称")
    message: str = Field(description="当前步骤文案")
    progress: int = Field(description="进度百分比 0~100")


class OneClickJobStatusResponse(BaseModel):
    """一键生成任务状态响应"""
    job_id: str = Field(description="任务ID")
    status: str = Field(description="任务状态：queued/running/completed/error")
    step_code: str = Field(description="当前步骤编码")
    step_label: str = Field(description="当前步骤名称")
    message: str = Field(description="当前步骤文案")
    progress: int = Field(description="进度百分比 0~100")
    filename: str = Field(default="", description="生成完成后的下载文件名")
    download_url: str = Field(default="", description="下载地址")
    error: str = Field(default="", description="错误信息")
    updated_time: datetime = Field(description="状态更新时间")
    validation_gate: ValidationGate | None = Field(default=None, description="硬校验门结果")
    regression_metrics: RegressionMetrics | None = Field(default=None, description="回归指标")
    draft_level: str = Field(default="", description="稿件等级")


# --- 通用响应 ---

class ErrorResponse(BaseModel):
    """错误响应"""
    error_code: str = Field(description="错误代码")
    message: str = Field(description="错误信息")
    details: dict[str, Any] = Field(default_factory=dict, description="详细错误信息")
