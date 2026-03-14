# 招投标 AI Agent 服务

这是一个基于 FastAPI 的招投标辅助生成服务，核心目标是把“招标文件解析、条款归一化、投标资料匹配、章节生成、质量校验、Word 输出”串成一套可落地的后端流程。

项目里实际上并行存在三条能力线：

1. 知识库检索与多 Agent 协作：`/kb/*`、`/agent/run`
2. 正式招投标工作流：`/api/tender/workflow/*`
3. 一键生成投标文件：`/api/tender/one-click/*`

## 技术栈

- Web 框架：FastAPI
- 数据模型：Pydantic
- 大模型接入：LangChain / LangGraph / OpenAI-compatible API
- 向量能力：`sentence-transformers` + 本地 SQLite
- 文档处理：`pypdf`、`python-docx`
- 测试：pytest

## 运行方式

```bash
pip install -r requirements.txt
export LLM_API_KEY=your_key
python -m app.main
```

默认地址：

- 首页：`http://127.0.0.1:8000/`
- 状态检查：`http://127.0.0.1:8000/api/status`

注意：

- OpenAPI 文档被关闭了，接口调试主要靠 `test_main.http`
- 招投标业务状态目前主要保存在进程内内存字典里，服务重启后会丢失

## 架构总览

```text
浏览器 / HTTP Client
        |
        v
    app/main.py
        |
        +-- app/routers/kb.py
        |      -> chunking.py
        |      -> embeddings.py
        |      -> retriever.py
        |
        +-- app/routers/chat.py
        |      -> graph.py
        |      -> llm.py
        |      -> retriever.py
        |
        +-- app/routers/tender/*
               -> tender_parser.py
               -> tender_workflow/*
               -> one_click_generator/*
               -> evidence_binder.py
               -> quality_gate.py
               -> docx_builder.py
```

### 分层理解

| 层级 | 作用 |
| --- | --- |
| `app/main.py` | 组装 FastAPI 应用、挂静态页、注册路由 |
| `app/config.py` | 统一读取环境变量 |
| `app/schemas.py` | 全项目通用数据契约 |
| `app/routers/*` | HTTP 入口，做参数接收、状态组装、调用服务 |
| `app/services/*` | 真正的业务实现和算法逻辑 |
| `tests/*` | 单元测试、增强测试、接口集成测试 |

## 两条主要业务链路

### 1. 正式工作流

入口：`POST /api/tender/workflow/run`

大致步骤：

1. 读取或自动解析招标文件
2. 识别投标包范围
3. 分类条款
4. 归一化需求
5. 提取产品事实
6. 绑定招标侧和投标侧证据
7. 生成章节
8. 做硬校验
9. 产出内部审阅稿 / 外发稿视图
10. 做回归评测和二次校验

这条链路的核心代码主要在 `app/services/tender_workflow/`。

### 2. 一键生成

入口：`POST /api/tender/one-click` 或 `POST /api/tender/one-click/start`

大致步骤：

1. 上传 PDF / DOCX
2. 解析招标文件文本和结构
3. 归一化技术需求
4. 构造投标章节
5. 做质量门禁
6. 输出 Word 文件

这条链路的核心代码主要在 `app/services/one_click_generator/`。

## 运行期数据与状态

| 位置 | 作用 |
| --- | --- |
| `data/chroma/` | 本地知识库 SQLite 文件默认落盘目录 |
| `data/uploads/tenders/` | 上传的招标文件临时存储 |
| `data/outputs/bids/` | 生成出的投标文档输出目录 |
| `app/routers/tender/common.py` 里的内存字典 | 暂存 tender/company/product/bid/workflow/job 状态 |

## 目录速览

```text
app/
  main.py
  config.py
  schemas.py
  static/
  routers/
    chat.py
    kb.py
    tender/
  services/
    llm.py
    graph.py
    retriever.py
    chunking.py
    embeddings.py
    tender_parser.py
    requirement_processor.py
    evidence_binder.py
    quality_gate.py
    docx_builder.py
    bid_generator.py
    tender_workflow/
    one_click_generator/
tests/
```

## 文件职责说明

下面按文件说明“这个文件在管什么”。

### 根目录文件

| 文件 | 职责 |
| --- | --- |
| `requirements.txt` | Python 依赖清单。 |
| `test_main.http` | 手工调试接口的 HTTP 请求样例。 |
| `http-client.env.json` | JetBrains HTTP Client 的环境变量示例。 |
| `test_tender_system.py` | 一个独立的接口调用示例脚本，演示从上传到下载的全流程。 |
| `app.zip` | 项目压缩包/归档产物，不参与运行时逻辑。 |

### `app/` 核心文件

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | FastAPI 入口；注册知识库、Agent、招投标路由；挂载首页静态文件。 |
| `app/config.py` | 读取 `.env` / 环境变量，定义应用、LLM、Embedding、知识库、分块等配置。 |
| `app/schemas.py` | 全量 Pydantic 模型定义；包含招标文件、产品、工作流、质量门、回归指标等核心数据结构。 |
| `app/static/index.html` | 简单前端页面，提供一键生成上传和进度展示。 |

### `app/routers/`

| 文件 | 职责 |
| --- | --- |
| `app/routers/chat.py` | 多 Agent 团队执行入口，调用 `run_agent_team`。 |
| `app/routers/kb.py` | 知识库导入、文件解析入库、语义检索、统计查询接口。 |
| `app/routers/tender/__init__.py` | 招投标路由的聚合导出层，对外暴露统一 `router`。 |
| `app/routers/tender/common.py` | 招投标路由公共区；定义上传/输出目录、内存存储字典、下载名处理、通用视图拼装。 |
| `app/routers/tender/crud.py` | 基础招投标接口：上传招标文件、解析、公司资料、产品资料、生成投标文件、下载结果。 |
| `app/routers/tender/workflow.py` | 正式十层工作流接口；负责把阶段结果组织成完整响应。 |
| `app/routers/tender/one_click.py` | 一键生成接口；支持同步生成、后台任务、进度查询和结果下载。 |

### `app/services/` 通用基础能力

| 文件 | 职责 |
| --- | --- |
| `app/services/llm.py` | 封装 ChatOpenAI 初始化、普通补全调用、带 tools 的调用。 |
| `app/services/graph.py` | 基于 LangGraph 的 Supervisor / Researcher / Writer / Reviewer 多 Agent 流程。 |
| `app/services/embeddings.py` | 加载 sentence-transformers 模型并生成向量。 |
| `app/services/chunking.py` | 文本切块；增强版还能产出带页码、表格坐标、条款编号的 `DocumentBlock`。 |
| `app/services/retriever.py` | 本地知识库实现；把文本切块、向量化并写入 SQLite，再做余弦相似度检索。 |
| `app/services/tender_parser.py` | 招标文件解析器；负责 PDF/DOCX 文本提取、调用 LLM 抽结构化招标信息、补技术参数和数量。 |
| `app/services/requirement_processor.py` | 技术需求抽取与归一化规则中心；负责条款原子化、分类、坏样本过滤、跨包污染规避。 |
| `app/services/evidence_binder.py` | 一键生成链路的证据绑定核心；负责招标侧定位、投标侧证据绑定、产品画像构建、覆盖率计算。 |
| `app/services/quality_gate.py` | 质量门和回归指标中心；负责占位符、串包、混表、证据覆盖、模板污染、自愈与降级。 |
| `app/services/docx_builder.py` | 把章节 Markdown/文本渲染成 Word 文档，包含目录、标题、表格和样式。 |
| `app/services/bid_generator.py` | 较早的一套投标生成 Agent 实现；封装分表生成、模型输出清洗和结构化技术响应逻辑。当前路由层未直接作为主入口使用。 |

### `app/services/tender_workflow/` 正式工作流

| 文件 | 职责 |
| --- | --- |
| `app/services/tender_workflow/__init__.py` | 正式工作流模块聚合导出层。 |
| `app/services/tender_workflow/common.py` | 正式工作流的公共函数库；包含 LLM 调用、引用整理、通用常量、上下文处理和辅助判断。 |
| `app/services/tender_workflow/classification.py` | 第 3-4 层附近的条款分类与需求归一化实现。 |
| `app/services/tender_workflow/product_facts.py` | 产品事实提取器；从产品资料和投标材料里构建 identity/spec/config/evidence 事实。 |
| `app/services/tender_workflow/evidence.py` | 正式工作流的证据绑定与要求-产品匹配逻辑。 |
| `app/services/tender_workflow/materialization.py` | 把要求、证据和产品实参“注入”章节正文，形成更可投递的底稿。 |
| `app/services/tender_workflow/sanitization.py` | 外发净化；检查占位符、串包、混表、证据不足，并决定是否阻断外发。 |
| `app/services/tender_workflow/reporting.py` | 评测与回归报告；汇总阶段状态、覆盖率、聚焦度、配置详细度等指标。 |
| `app/services/tender_workflow/validation.py` | 二次校验；检查章节完整性、映射表、资料覆盖、项目信息一致性、行业证明链等。 |
| `app/services/tender_workflow/agent.py` | 正式工作流门面类 `TenderWorkflowAgent`；把各步骤拼成一套可被路由直接调用的 Agent。 |

### `app/services/one_click_generator/` 一键生成链路

| 文件 | 职责 |
| --- | --- |
| `app/services/one_click_generator/__init__.py` | 一键生成模块聚合导出层。 |
| `app/services/one_click_generator/common.py` | 一键生成公共常量和工具函数；包含模式判定、文本规范化、区域识别、通用模板变量等。 |
| `app/services/one_click_generator/pipeline.py` | 一键生成主管道 `generate_bid_sections`；负责归一化需求、双层证据绑定、生成章节、做质量门和降级。 |
| `app/services/one_click_generator/sections.py` | 章节聚合层，把资格部分和技术部分生成器统一导出。 |
| `app/services/one_click_generator/qualification_sections.py` | 资格性证明、承诺函、授权书、声明类章节生成。 |
| `app/services/one_click_generator/technical_sections.py` | 技术偏离、配置说明、交付/验收/培训、附录类章节生成。 |
| `app/services/one_click_generator/response_tables.py` | 技术响应表、偏离表、响应值推断、证据展示等表格逻辑。 |
| `app/services/one_click_generator/config_tables.py` | 配置表、主参数表、证据映射表、配置项分类与提取。 |
| `app/services/one_click_generator/table_builders.py` | 表格模块聚合层，统一导出响应表和配置表相关构造器。 |
| `app/services/one_click_generator/writer_contexts.py` | 把同一包的需求按 `table_type` 分组成 `WriterContext`，供 rich draft 章节生成使用。 |

### `tests/` 测试文件

| 文件 | 职责 |
| --- | --- |
| `tests/test_chunking_quality_gate.py` | 测试文档块切分、噪音识别、ValidationGate、RegressionMetrics、证据别名同步。 |
| `tests/test_one_click_generator_enhancements.py` | 测试一键生成增强逻辑，如模板污染清理、资格声明分支、技术章节结构化输出、参数回退。 |
| `tests/test_tender_workflow_pipeline.py` | 测试正式工作流的分类、归一化、事实匹配、证据绑定、外发净化和回归报告。 |
| `tests/test_tender_workflow_enhancements.py` | 测试正式工作流增强项，如 citations 去重、二次校验深度规则、串包和模型缺口识别。 |
| `tests/test_tender_workflow_api_integration.py` | 用 FastAPI TestClient 做接口集成测试，验证工作流 API 与 `/bid/generate` 的联动行为。 |

## 关键设计特点

### 1. Schema 很重

这个项目把大量业务含义沉到了 `app/schemas.py` 里。很多服务并不是传原始 dict，而是传：

- `TenderDocument`
- `NormalizedRequirement`
- `TenderSourceBinding`
- `BidEvidenceBinding`
- `ProductProfile`
- `WriterContext`
- `ValidationGate`
- `RegressionMetrics`

这让“条款 -> 证据 -> 章节 -> 质量门”这条链比较清晰。

### 2. 路由层保存了很多状态

当前不是数据库架构，而是：

- 文件落盘到 `data/`
- 元数据保存在 `app/routers/tender/common.py` 的模块级字典里

也就是说它更像“单机原型 / 内部工具服务”，不是多实例生产态持久化设计。

### 3. 项目里有两套生成逻辑

当前主要对外是：

- 正式工作流：`tender_workflow`
- 快速成稿：`one_click_generator`

另外还有一个 `bid_generator.py`，看起来属于较早期或备用的生成 Agent 实现，代码里沉淀了不少响应值推断和模型清洗逻辑。

## 常用开发命令

```bash
python -m app.main
pytest
```

如果要手工调接口，优先看：

- `test_main.http`
- `test_tender_system.py`

## 接手项目时建议先看哪几个文件

如果你是第一次接手，推荐阅读顺序：

1. `app/main.py`
2. `app/schemas.py`
3. `app/routers/tender/common.py`
4. `app/routers/tender/workflow.py`
5. `app/services/tender_workflow/agent.py`
6. `app/services/one_click_generator/pipeline.py`
7. `app/services/requirement_processor.py`
8. `app/services/quality_gate.py`

这样能最快看懂“入口在哪、状态在哪、流程怎么跑、质量怎么卡”。
