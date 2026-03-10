from __future__ import annotations

from typing import Any
from datetime import datetime
from uuid import UUID

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


# ==================== 招投标系统数据模型 ====================

# --- 招标文件相关 ---

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


class ProductSpecification(BaseModel):
    """产品技术规格"""
    product_id: str | None = Field(default=None, description="产品ID")
    product_name: str = Field(description="产品名称")
    manufacturer: str = Field(description="生产厂家")
    origin: str = Field(default="", description="产地")
    model: str = Field(default="", description="型号")
    specifications: dict[str, Any] = Field(default_factory=dict, description="技术参数")
    price: float = Field(description="参考价格")
    certifications: list[str] = Field(default_factory=list, description="认证证书列表")
    registration_number: str = Field(default="", description="注册证编号（医疗器械）")
    authorization_letter: str = Field(default="", description="授权书路径")


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


class BidDocumentSection(BaseModel):
    """投标文件章节"""
    section_title: str = Field(description="章节标题")
    content: str = Field(description="章节内容（可以是HTML或Markdown）")
    attachments: list[str] = Field(default_factory=list, description="附件路径列表")


class BidGenerateResponse(BaseModel):
    """投标文件生成响应"""
    bid_id: str = Field(description="投标文件ID")
    tender_id: str = Field(description="对应的招标文件ID")
    status: str = Field(default="generated", description="状态：generating/generated/error")
    sections: list[BidDocumentSection] = Field(default_factory=list, description="文档章节列表")
    file_path: str = Field(default="", description="生成的PDF文件路径")
    download_url: str = Field(default="", description="下载链接")
    generated_time: datetime = Field(description="生成时间")


class BidDownloadRequest(BaseModel):
    """投标文件下载请求"""
    bid_id: str = Field(description="投标文件ID")
    format: str = Field(default="pdf", description="文件格式：pdf/docx")


# --- 四阶段工作流相关 ---

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
    """四阶段工作流请求"""
    tender_id: str = Field(description="招标文件ID")
    company_profile_id: str | None = Field(default=None, description="企业信息ID，可为空")
    selected_packages: list[str] = Field(default_factory=list, description="投标包号列表，留空表示全部包")
    product_ids: dict[str, str] = Field(default_factory=dict, description="包号→产品ID映射")
    continue_on_material_gaps: bool = Field(default=False, description="资料不全时是否继续生成标书")
    generate_docx: bool = Field(default=True, description="是否生成docx文件")


class TenderWorkflowResponse(BaseModel):
    """四阶段工作流响应"""
    workflow_id: str = Field(description="工作流ID")
    tender_id: str = Field(description="招标文件ID")
    status: str = Field(description="整体状态：completed/blocked/error")
    analysis: TenderWorkflowStep1Result = Field(description="步骤1结果")
    material_validation: TenderWorkflowStep2Result = Field(description="步骤2结果")
    generation: TenderWorkflowStep3Result = Field(description="步骤3结果")
    review: TenderWorkflowStep4Result = Field(description="步骤4结果")
    generated_time: datetime = Field(description="生成时间")


# --- 通用响应 ---

class ErrorResponse(BaseModel):
    """错误响应"""
    error_code: str = Field(description="错误代码")
    message: str = Field(description="错误信息")
    details: dict[str, Any] = Field(default_factory=dict, description="详细错误信息")
