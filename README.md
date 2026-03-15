# 招投标 AI Agent 项目说明

## 1. 项目定位

这是一个基于 **FastAPI + LangChain/LangGraph + 本地知识库检索 + Word 文档生成** 的招投标辅助系统。  
项目主要能力分成四块：

1. **招标文件解析**：上传 PDF / Word 后提取项目、包件、技术要求、评审信息等结构化内容。
2. **知识库检索**：把文本切块、向量化、入库，支持相似度检索和工作流引用。
3. **标书生成**：根据招标信息、企业资料、产品资料生成内部底稿、外发稿和一键成稿结果。
4. **正式工作流**：按照“解析 -> 分类 -> 归一化 -> 证据绑定 -> 章节生成 -> 校验 -> 双输出”的链路执行。

## 2. 总体架构

```text
app/main.py
  ├─ routers/
  │   ├─ kb.py                 知识库接口
  │   ├─ chat.py               多智能体接口
  │   └─ tender/               招投标主接口
  │       ├─ crud.py           上传/解析/生成/下载
  │       ├─ workflow.py       十层正式工作流
  │       ├─ one_click.py      一键成稿
  │       └─ common.py         路由共享状态与存储
  ├─ schemas.py                Pydantic 数据模型
  ├─ config.py                 环境变量与路径配置
  └─ services/
      ├─ tender_parser.py      招标文件解析
      ├─ requirement_processor.py 需求归一化/分类
      ├─ evidence_binder.py    招标侧/投标侧证据绑定
      ├─ quality_gate.py       质量门禁与回归指标
      ├─ docx_builder.py       Word 文档输出
      ├─ retriever.py          知识库入库/检索
      ├─ embeddings.py         向量生成
      ├─ graph.py              多智能体编排
      ├─ bid_generator.py      旧版/通用生成链路
      ├─ one_click_generator/  一键生成章节管道
      └─ tender_workflow/      正式工作流子模块
```

## 3. 核心流程

### 3.1 API 请求流

1. 请求从 `app/main.py` 进入 FastAPI 应用。
2. `app/routers/` 根据业务类型分发到知识库、多智能体或招投标接口。
3. `app/services/tender_parser.py` 负责解析招标文件。
4. `app/services/requirement_processor.py`、`app/services/evidence_binder.py`、`app/services/quality_gate.py` 负责需求整理、证据绑定和质量校验。
5. `app/services/one_click_generator/` 或 `app/services/tender_workflow/` 负责章节生成与正式流程编排。
6. `app/services/docx_builder.py` 把章节写成 `.docx` 文件，路由层返回下载信息。

### 3.2 数据层特点

- 结构化对象统一定义在 `app/schemas.py`
- 运行配置统一来自 `app/config.py`
- 路由层当前使用 **内存字典** 作为临时存储
- 知识库通过 `app/services/retriever.py` 使用本地 SQLite + 向量检索
- 上传文件和输出文件路径由环境变量配置

## 4. 目录与文件职责

### 4.1 根目录文件

| 文件 | 说明 |
| --- | --- |
| `requirements.txt` | 项目依赖清单，包含 FastAPI、LangGraph、向量模型、PDF/Word 处理等依赖。 |
| `http-client.env.json` | HTTP 调试环境变量，方便本地接口联调。 |
| `test_main.http` | HTTP 请求样例文件，用于手工调用接口。 |
| `app.zip` | 项目打包产物或归档文件。 |
| `test_tender_system.py` | 端到端风格的系统级测试，覆盖静态页、工作流和投标生成接口。 |
| `README.md` | 当前文档，说明架构、流程和文件职责。 |

### 4.2 `scripts/`

| 文件 | 说明 |
| --- | --- |
| `scripts/fix_bid_doc.py` | 离线修复现有投标 Word 文档内容、页码和附表结构的脚本。 |

### 4.3 `app/` 核心层

| 文件 | 说明 |
| --- | --- |
| `app/__init__.py` | 应用包初始化文件。 |
| `app/main.py` | FastAPI 应用入口，挂载静态资源并注册所有路由。 |
| `app/config.py` | 读取环境变量、构建全局配置对象、统一管理数据路径。 |
| `app/schemas.py` | 所有 Pydantic 数据模型和枚举定义，是路由层和服务层共享的数据契约。 |
| `app/static/index.html` | 前端入口静态页。 |

### 4.4 `app/routers/`

| 文件 | 说明 |
| --- | --- |
| `app/routers/chat.py` | 暴露多智能体运行接口，调用 `graph.py` 执行协作链路。 |
| `app/routers/kb.py` | 文本/文件入库、知识检索、知识库统计接口。 |
| `app/routers/tender/__init__.py` | 聚合招投标子路由并对外导出。 |
| `app/routers/tender/common.py` | 招投标模块共享状态、上传目录、输出目录和内存存储。 |
| `app/routers/tender/crud.py` | 招标文件上传、解析、企业资料、产品资料、标书生成、下载等基础接口。 |
| `app/routers/tender/workflow.py` | 十层正式工作流接口，负责串联解析、归一化、证据、生成、校验和双输出。 |
| `app/routers/tender/one_click.py` | 一键上传并生成底稿文档的接口，支持后台任务和下载。 |

### 4.5 `app/services/` 通用服务层

| 文件 | 说明 |
| --- | --- |
| `app/services/bid_generator.py` | 较早期的投标文件生成链路，包含 LangGraph 编排和章节生成逻辑。 |
| `app/services/chunking.py` | 文本切块与可引用文档块构建，为检索和溯源提供基础数据。 |
| `app/services/docx_builder.py` | 把章节内容、目录、封面、表格写入 Word 文档。 |
| `app/services/embeddings.py` | 统一封装文本向量生成。 |
| `app/services/evidence_binder.py` | 把需求与招标原文、投标证据、产品画像进行绑定。 |
| `app/services/graph.py` | 多智能体团队图的构建、路由和执行。 |
| `app/services/llm.py` | 大模型实例创建、普通调用和工具调用封装。 |
| `app/services/quality_gate.py` | 质量门禁、占位符治理、污染检测、回归指标计算、自愈逻辑。 |
| `app/services/requirement_processor.py` | 招标需求抽取、原子化、归一化、分类、匹配辅助逻辑。 |
| `app/services/retriever.py` | 知识库文本入库、向量检索、SQLite 结构初始化和统计。 |
| `app/services/tender_parser.py` | 招标文件解析主服务，负责 PDF/Word 文本抽取、包件分析、模板识别和表格解析。 |

### 4.6 `app/services/one_click_generator/`

| 文件 | 说明 |
| --- | --- |
| `app/services/one_click_generator/__init__.py` | 聚合一键生成模块对外导出。 |
| `app/services/one_click_generator/common.py` | 一键生成通用工具函数，如地区判断、范围文本、报价概览等。 |
| `app/services/one_click_generator/config_tables.py` | 配置清单、主参数表、证据映射表等配置类表格生成。 |
| `app/services/one_click_generator/pipeline.py` | 一键生成主管道，串联需求归一化、证据绑定、章节生成、门禁校验和双输出。 |
| `app/services/one_click_generator/qualification_sections.py` | 资格审查、符合性审查、无效投标检查等章节生成。 |
| `app/services/one_click_generator/response_tables.py` | 技术响应表、偏离表、结构化响应值和投标侧证据展示。 |
| `app/services/one_click_generator/sections.py` | 一键生成章节模块聚合导出。 |
| `app/services/one_click_generator/table_builders.py` | 表格构建工具聚合导出。 |
| `app/services/one_click_generator/technical_sections.py` | 技术章节、附录、服务说明等富内容章节生成。 |
| `app/services/one_click_generator/writer_contexts.py` | 为 Writer 构造包件级上下文输入。 |

### 4.7 `app/services/one_click_generator/format_driven_sections/`

| 文件 | 说明 |
| --- | --- |
| `app/services/one_click_generator/format_driven_sections/__init__.py` | 根据采购模式选择 TP / CS / ZB 对应章节生成器。 |
| `app/services/one_click_generator/format_driven_sections/common.py` | TP/CS/ZB 共用的抽取、表格、章节拼装辅助函数。 |
| `app/services/one_click_generator/format_driven_sections/cs.py` | 竞争性磋商（CS）格式的章节、评审表、服务方案和无效投标内容生成。 |
| `app/services/one_click_generator/format_driven_sections/tp.py` | 谈判/谈判类（TP）格式的章节、服务计划、评审表和技术响应生成。 |
| `app/services/one_click_generator/format_driven_sections/zb.py` | 公开招标（ZB）格式的模板章节、评审表、投标函、附表和服务要点生成。 |

### 4.8 `app/services/tender_workflow/`

| 文件 | 说明 |
| --- | --- |
| `app/services/tender_workflow/__init__.py` | 聚合正式工作流模块对外导出。 |
| `app/services/tender_workflow/agent.py` | 正式工作流 Agent 封装，对外提供分步方法。 |
| `app/services/tender_workflow/classification.py` | 条款分类和规则分支判断相关逻辑。 |
| `app/services/tender_workflow/common.py` | 正式工作流通用函数，如模型调用、匹配、片段截取、阶段记录。 |
| `app/services/tender_workflow/evidence.py` | 正式工作流里的证据绑定和匹配结果整理。 |
| `app/services/tender_workflow/materialization.py` | 把企业、产品、证据、评审定位等真实数据实装到章节内容中。 |
| `app/services/tender_workflow/product_facts.py` | 产品事实抽取、产品画像构建、需求与产品事实匹配。 |
| `app/services/tender_workflow/reporting.py` | 工作流阶段报告、引用追踪、覆盖率和回归报告生成。 |
| `app/services/tender_workflow/sanitization.py` | 外发稿清洗、敏感占位符剔除和外发阻断判断。 |
| `app/services/tender_workflow/validation.py` | 资料完整性校验、二次校验、默认步骤结果构造。 |

### 4.9 `tests/` 测试目录

| 文件 | 说明 |
| --- | --- |
| `tests/test_chunking_quality_gate.py` | 切块质量、门禁指标、证据字段兼容性测试。 |
| `tests/test_cs_generator_strictness.py` | CS 格式生成器严格性测试。 |
| `tests/test_docx_builder_pagination.py` | Word 构建、分页、目录、表格布局相关测试。 |
| `tests/test_non_zb_response_format_guardrails.py` | 非 ZB 模式章节标题和格式污染防护测试。 |
| `tests/test_one_click_generator_enhancements.py` | 一键生成增强逻辑测试，覆盖需求归一化、配置表、证据绑定等。 |
| `tests/test_pdf_sample_scope_regressions.py` | PDF 样本范围抽取和跨包污染回归测试。 |
| `tests/test_tender_workflow_api_integration.py` | 工作流 API 与投标生成 API 的集成测试。 |
| `tests/test_tender_workflow_enhancements.py` | 正式工作流增强逻辑测试。 |
| `tests/test_tender_workflow_pipeline.py` | 正式工作流各阶段主链路测试。 |
| `tests/test_tp_generator_strictness.py` | TP 格式生成器严格性测试。 |
| `tests/test_zb_format_strictness.py` | ZB 格式模板和响应章节生成严格性测试。 |

## 5. 你后续看代码时的建议顺序

如果你要快速熟悉项目，建议按下面顺序读：

1. `app/main.py`
2. `app/routers/tender/workflow.py`
3. `app/services/tender_workflow/agent.py`
4. `app/services/tender_workflow/common.py`
5. `app/services/requirement_processor.py`
6. `app/services/evidence_binder.py`
7. `app/services/one_click_generator/pipeline.py`
8. `app/services/docx_builder.py`

这样能先看懂入口和主链路，再回头看格式化生成器和细节工具函数。
