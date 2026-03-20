# 招投标 AI Agent 项目说明

本文基于当前仓库代码结构整理，目标是帮助开发者快速回答三件事：

1. 这个项目能做什么
2. 主要链路从哪里进、到哪里出
3. 每个主要文件负责什么

## 1. 项目概览

这是一个基于 `FastAPI + LangGraph/LangChain + 本地向量检索 + Word 文档生成` 的招投标辅助系统，当前主要包含三类能力：

- 知识库：把文本、PDF、DOCX 导入本地向量库，支持语义检索。
- 多智能体协作：围绕一个目标执行 `规划 -> 调研 -> 写作 -> 审核`。
- 招投标工作流：上传招标文件、解析结构化信息、生成投标底稿/投标文件、执行门禁校验、导出 Word。

项目里又分成两条投标生成主路线：

- `one-click`：更偏“快速出底稿”，适合只有招标文件、没有完整投标侧证据时。
- `workflow`：更偏“正式流程”，强调资料校验、证据绑定、章节实装、二次校验和外发净化。

## 2. 技术栈

- Web 框架：`FastAPI`
- LLM 编排：`LangChain`、`LangGraph`
- 向量模型：`sentence-transformers`
- 本地知识库存储：`SQLite`
- 文档处理：`pypdf`、`python-docx`
- 数据模型：`Pydantic`
- 测试：`pytest`

## 3. 运行方式

### 3.1 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3.2 最低环境变量

至少需要配置：

```bash
export LLM_API_KEY=your_key
```

常用可选项：

```bash
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_MODEL=gpt-4o-mini
export APP_HOST=127.0.0.1
export APP_PORT=8000
export APP_RELOAD=true
```

### 3.3 启动服务

```bash
python -m app.main
```

启动后默认地址：

- 首页：`http://127.0.0.1:8000/`
- 知识库接口：`/kb/*`
- 多智能体接口：`/agent/*`
- 招投标接口：`/api/tender/*`

注意：

- `app/main.py` 里关闭了 OpenAPI/Swagger，所以默认没有 `/docs`。
- 招投标模块当前大量使用内存字典做临时存储，服务重启后会丢失内存态数据。

## 3.4 打包成可双击运行的 macOS App

当前仓库最适合的打包方式不是重写成 Electron，而是在现有 `FastAPI + 静态页面` 外面包一层桌面启动器：

- `desktop_launcher.py`：启动本地 FastAPI 服务，再以内嵌窗口打开首页。
- `BidAgent.spec`：`PyInstaller` 打包配置。
- `scripts/build_macos_app.sh`：一键构建脚本。

### 构建命令

```bash
chmod +x scripts/build_macos_app.sh
PYTHON_BIN=.venv/bin/python ./scripts/build_macos_app.sh
```

构建成功后，产物默认在：

```bash
dist/BidAgent.app
```

如果你要分发给其他 macOS 用户，建议继续打包成 `.dmg`：

```bash
chmod +x scripts/build_macos_dmg.sh
PYTHON_BIN=.venv/bin/python ./scripts/build_macos_dmg.sh
```

默认输出：

```bash
dist/BidAgent.dmg
```

如果已经有现成的 `.app`，可以跳过重复构建：

```bash
SKIP_APP_BUILD=1 ./scripts/build_macos_dmg.sh
```

### App 运行时目录

桌面版不会往 `.app` 包内写数据，而是统一写到：

```bash
~/Library/Application Support/BidAgent/
```

其中包括：

- `data/`：向量库、上传文件、生成文档
- `.env`：桌面版环境变量配置文件

### 桌面版环境变量

建议在下面这个位置创建配置文件：

```bash
~/Library/Application Support/BidAgent/.env
```

最低示例：

```bash
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

### 实际限制

- 建议使用 Python `3.12` 或 `3.13` 的虚拟环境构建；当前仓库里的 `LangChain/Pydantic v1` 兼容层在 Python `3.14+` 下已有兼容性警告。
- 首次使用嵌入模型时，`sentence-transformers` 可能需要联网下载模型。
- 如果你希望把模型也一起离线封进 App，需要额外预下载 Hugging Face 模型并调整缓存路径。
- 当前桌面壳本质上仍然是“本地 Web 服务 + 内嵌浏览器”，但对最终使用者来说已经是可双击启动的 `.app`。

## 4. 关键链路

### 4.1 知识库链路

`app/routers/kb.py`
-> `app/services/retriever.py`
-> `app/services/chunking.py`
-> `app/services/embeddings.py`
-> 本地 `data/chroma/vector_store.sqlite3`

### 4.2 多智能体链路

`app/routers/chat.py`
-> `app/services/graph.py`
-> `app/services/llm.py`
-> `app/services/retriever.py`

### 4.3 招投标正式链路

`app/routers/tender/workflow.py`
-> `app/services/tender_parser.py`
-> `app/services/tender_workflow/*`
-> `app/services/one_click_generator/pipeline.py`
-> `app/services/quality_gate.py`
-> `app/services/docx_builder.py`

### 4.4 一键交互链路

`app/routers/tender/one_click.py`
-> `app/services/tender_parser.py`
-> `app/services/one_click_generator/*`
-> `app/services/interactive_fill.py`
-> `app/services/docx_builder.py`

## 5. 目录理解建议

如果你第一次接手这个项目，建议按下面顺序读：

1. `app/main.py`
2. `app/config.py`
3. `app/schemas.py`
4. `app/routers/tender/workflow.py`
5. `app/services/tender_workflow/agent.py`
6. `app/services/tender_parser.py`
7. `app/services/requirement_processor.py`
8. `app/services/evidence_binder.py`
9. `app/services/one_click_generator/pipeline.py`
10. `app/services/docx_builder.py`

## 6. 文件职责总览

下面按目录分组列出当前仓库里主要源码、脚本和测试文件的职责。

### 6.1 根目录

| 路径 | 作用 |
| --- | --- |
| `README.md` | 当前说明文档，帮助开发者理解项目结构、链路和文件职责。 |
| `requirements.txt` | Python 依赖清单。 |
| `http-client.env.json` | HTTP Client 调试环境配置，给 `.http` 请求文件使用。 |
| `test_main.http` | 手工调试接口的示例请求，覆盖首页、知识库和多智能体接口。 |
| `test_fixes.py` | 偏手工的验证脚本，用真实招标文件跑解析和章节生成，检查“技术实参/配置清单/偏离判断/证明页码”四类问题。 |
| `test_tender_system.py` | 偏演示性质的 API 调用脚本，串行测试上传、解析、企业信息、产品信息、标书生成和下载。 |
| `test_template_repairs.py` | 顶层 pytest 文件，验证模板修复、章节顺序、Markdown 渲染和样稿重排逻辑。 |

### 6.2 `app/`

| 路径 | 作用 |
| --- | --- |
| `app/__init__.py` | 应用包标识文件。 |
| `app/main.py` | FastAPI 入口；创建应用、挂载静态页、注册知识库/多智能体/招投标路由。 |
| `app/config.py` | 统一读取环境变量，构造应用配置和默认数据目录。 |
| `app/schemas.py` | 全项目共享的数据模型与枚举定义，是路由层和服务层的核心契约。 |
| `app/static/index.html` | 单包交互式投标文档生成前端页面。 |

### 6.3 `app/routers/`

| 路径 | 作用 |
| --- | --- |
| `app/routers/chat.py` | 暴露多智能体执行接口，调用 `run_agent_team` 返回计划、调研、初稿和审校结果。 |
| `app/routers/kb.py` | 负责文本/文件入库、知识检索、知识库统计；支持 PDF、DOCX、TXT 等输入。 |
| `app/routers/tender/__init__.py` | 聚合 `common/crud/workflow/one_click` 四个子模块，对外暴露统一 `router`。 |
| `app/routers/tender/common.py` | 招投标路由共享状态中心；维护上传/输出目录、内存存储、下载文件名清洗、外发视图构造等公共逻辑。 |
| `app/routers/tender/crud.py` | 招标文件上传与解析、企业资料维护、产品资料维护、基础投标生成和下载接口。 |
| `app/routers/tender/workflow.py` | 十层正式工作流 API；负责补齐解析、入知识库、执行分阶段流程、写入结果、生成 Word。 |
| `app/routers/tender/one_click.py` | 交互式一键成稿 API；负责上传、解析、按包生成待填写项、回填答案、后台任务状态查询和下载。 |

### 6.4 `app/services/` 通用服务

| 路径 | 作用 |
| --- | --- |
| `app/services/llm.py` | 统一封装模型实例创建、普通补全调用和带工具调用。 |
| `app/services/graph.py` | 多智能体团队编排；定义 Supervisor、Researcher、Writer、Reviewer 四个节点和路由。 |
| `app/services/retriever.py` | 本地知识库实现；负责切块、向量化、写入 SQLite、余弦检索和统计。 |
| `app/services/embeddings.py` | `sentence-transformers` 向量模型的懒加载与文本/查询向量生成。 |
| `app/services/chunking.py` | 文本切块与 `DocumentBlock` 构建；支持章节、条款、表格单元格、页码、包号等元信息。 |
| `app/services/tender_parser.py` | 招标文件解析核心模块；负责 PDF/Word 文本抽取、包件识别、评审表提取、响应文件格式模板提取和结构化输出。 |
| `app/services/requirement_processor.py` | 招标需求抽取与归一化核心模块；负责条款分类、原子化、参数/阈值/单位解析、包件范围截取。 |
| `app/services/evidence_binder.py` | 需求与证据绑定模块；负责判定文档模式、构建招标侧溯源、投标侧证据绑定、产品画像和覆盖率。 |
| `app/services/quality_gate.py` | 质量门禁与回归指标；负责占位符检测、跨包污染检测、混表检测、自愈和 draft/external gate 判定。 |
| `app/services/docx_builder.py` | Word 输出引擎；负责封面、目录、标题层级、Markdown 段落/表格渲染、分页和表宽布局。 |
| `app/services/interactive_fill.py` | 交互式补录模块；负责扫描生成稿占位符、合并同义字段、规划填写项、应用用户答案。 |

### 6.5 `app/services/one_click_generator/`

| 路径 | 作用 |
| --- | --- |
| `app/services/one_click_generator/__init__.py` | 一键生成模块总出口；把公共函数重新导出到统一命名空间。 |
| `app/services/one_click_generator/common.py` | 一键生成共享常量和工具箱；包含地区/采购模式判断、文本清洗、服务方案片段、报价/包件工具等大量公共逻辑。 |
| `app/services/one_click_generator/pipeline.py` | 一键生成主管道；串联文档接入、需求归一化、证据绑定、章节生成、门禁校验、稿件等级判定。 |
| `app/services/one_click_generator/sections.py` | 章节生成聚合出口，主要转发资格章节和技术章节生成函数。 |
| `app/services/one_click_generator/table_builders.py` | 表格生成聚合出口，统一转发响应表和配置表构建函数。 |
| `app/services/one_click_generator/writer_contexts.py` | 把归一化需求按包和分表类型拆成 `WriterContext`，供后续 writer 使用。 |
| `app/services/one_click_generator/qualification_sections.py` | 资格性审查、符合性审查、无效投标自检、企业声明等“资格/承诺类”章节生成。 |
| `app/services/one_click_generator/technical_sections.py` | 技术章节主写手；负责技术偏离、配置清单、服务方案、附录等富内容章节拼装。 |
| `app/services/one_click_generator/response_tables.py` | 技术偏离表与响应表生成；负责从需求和产品规格中拼“招标要求/响应值/偏离/证据”四列内容。 |
| `app/services/one_click_generator/config_tables.py` | 配置清单和主参数表生成；负责配置项分类、数量单位推断、从技术条款反推配置项。 |

### 6.6 `app/services/one_click_generator/format_driven_sections/`

| 路径 | 作用 |
| --- | --- |
| `app/services/one_click_generator/format_driven_sections/__init__.py` | 格式驱动章节入口；根据采购方式切到 `tp/cs/zb` 生成器。 |
| `app/services/one_click_generator/format_driven_sections/common.py` | TP/CS/ZB 共用工具；处理标题规范化、模板块抽取、评审表提取、服务要点、报价附件等。 |
| `app/services/one_click_generator/format_driven_sections/tp.py` | 竞争性谈判格式章节生成器；负责谈判模板识别、服务计划、评审表和附录生成。 |
| `app/services/one_click_generator/format_driven_sections/cs.py` | 竞争性磋商格式章节生成器；负责磋商模板、评审表头复用、技术/服务章节和附录生成。 |
| `app/services/one_click_generator/format_driven_sections/zb.py` | 公开招标格式章节生成器；负责投标函、开标一览表、格式驱动模板、评审索引表、无效投标条款抽取。 |

### 6.7 `app/services/tender_workflow/`

| 路径 | 作用 |
| --- | --- |
| `app/services/tender_workflow/__init__.py` | 正式工作流总出口；把分类、产品事实、证据、实装、净化、报告、校验等函数统一导出。 |
| `app/services/tender_workflow/common.py` | 正式工作流公共底座；包含模型调用、引用准备、片段截取、阶段记录、匹配辅助等共享逻辑。 |
| `app/services/tender_workflow/agent.py` | `TenderWorkflowAgent` 封装；对外暴露 step1~stepN 风格的方法，组织正式流程调用。 |
| `app/services/tender_workflow/classification.py` | 条款分类与需求归一化；把招标信息拆成资格/技术/商务/政策/噪音等类别，并生成规则分支判断。 |
| `app/services/tender_workflow/product_facts.py` | 投标产品事实抽取；从 `bid_materials` 中提品牌、型号、厂家、配置项、证据引用并生成产品画像。 |
| `app/services/tender_workflow/evidence.py` | 正式工作流里的招标侧/投标侧证据绑定模块；负责 requirement 到页码、证据片段、材料类型的映射。 |
| `app/services/tender_workflow/materialization.py` | 章节实装模块；把企业信息、产品参数、证据和包件信息真正写进章节正文和表格。 |
| `app/services/tender_workflow/sanitization.py` | 外发净化模块；负责把内部底稿变成可外发版本，并在污染、缺证、跨包、薄弱偏离表时阻断外发。 |
| `app/services/tender_workflow/reporting.py` | 阶段快照与回归报告模块；输出内部审计视图、覆盖率、阶段统计和 regression checks。 |
| `app/services/tender_workflow/validation.py` | 生成后的第二轮校验模块；检查资料覆盖、占位符、章节完整性、项目基础信息一致性和可追溯性。 |

### 6.8 `tests/`

| 路径 | 作用 |
| --- | --- |
| `tests/test_bid_table_shell_regressions.py` | 回归测试配置表/偏离表的“空壳占位”问题，确保能落真实配置项或至少落可人工审核的待填引导。 |
| `tests/test_chunking_quality_gate.py` | 测试 `DocumentBlock` 切分质量、噪音标记、质量门禁和回归指标。 |
| `tests/test_cs_generator_strictness.py` | 测试竞争性磋商格式生成器的章节顺序、模板复用、评审表和服务章节严格性。 |
| `tests/test_docx_builder_pagination.py` | 测试 Word 构建器的 Markdown 渲染、目录层级、分页、表宽和封面处理。 |
| `tests/test_interactive_fill.py` | 测试交互式补录规划，确保能合并同义字段并保留必须人工填写的字段。 |
| `tests/test_non_zb_response_format_guardrails.py` | 测试非公开招标模式下的格式守卫，防止脏标题、引用章节污染模板识别。 |
| `tests/test_one_click_generator_enhancements.py` | 测试一键生成增强逻辑，包括模板污染清洗、企业声明分支、技术章节 fallback 和结构化输出。 |
| `tests/test_one_click_interactive.py` | 测试单包交互式一键生成流程，关注只生成选中包、提示去重、生成与下载流程。 |
| `tests/test_pdf_sample_scope_regressions.py` | 测试 PDF 样本中的包件范围抽取、多行参数恢复、配置项提取和跨包污染回归。 |
| `tests/test_service_section_detail.py` | 测试服务方案章节的细节密度，避免生成过薄的人工底稿。 |
| `tests/test_tender_workflow_api_integration.py` | 测试正式工作流 API 和基础生成 API 的集成行为，包括十阶段输出、双输出和下载优先级。 |
| `tests/test_tender_workflow_enhancements.py` | 测试正式工作流增强点，如 citations 归一化、二次校验、材料实装和缺陷阻断。 |
| `tests/test_tender_workflow_pipeline.py` | 测试正式工作流核心阶段，包括分类、匹配、证据绑定、净化、回归报告和章节实装。 |
| `tests/test_tp_generator_strictness.py` | 测试竞争性谈判格式生成器的章节、服务方案、评审表和无效项抽取严格性。 |
| `tests/test_zb_format_strictness.py` | 测试公开招标格式解析与章节生成，重点覆盖第六章模板、评审索引表和无效投标条款抽取。 |

### 6.9 `scripts/`

| 路径 | 作用 |
| --- | --- |
| `scripts/fix_bid_doc.py` | 早期离线修复脚本；针对特定 Word 标书样稿补章节、改表格、修页码、插附录。 |
| `scripts/fix_bid_doc_v2.py` | 第二版底稿修复脚本；针对技术偏离表、配置表、评审表、脏段落做更细粒度补丁。 |
| `scripts/repair_sample_drafts.py` | 批量修复样例底稿脚本；整理章节顺序、服务方案、声明模板和测试样稿。 |

### 6.10 运行时数据和样例文件

| 路径 | 作用 |
| --- | --- |
| `data/chroma/` | 本地知识库数据目录；当前实际使用 `vector_store.sqlite3`。 |
| `data/uploads/` | 上传的招标文件存储目录。 |
| `data/outputs/bids/` | 生成出来的 `.docx` 投标底稿/投标文件输出目录。 |
| `textfile/` | 本地样例文档目录，常被脚本和人工排查过程拿来做输入样本。 |

## 7. 当前代码结构里的几个关键事实

### 7.1 路由层不是数据库驱动

招投标接口当前大量使用以下内存字典存状态：

- `tender_storage`
- `company_storage`
- `product_storage`
- `bid_storage`
- `workflow_storage`
- `one_click_job_storage`
- `one_click_session_storage`

这意味着：

- 服务重启后会丢失这些运行态数据
- 目前更像开发/验证版本，而不是持久化生产架构

### 7.2 解析与生成并不是完全分离的

这个项目里“解析器”和“生成器”之间是强耦合的：

- `tender_parser.py` 决定能提取出多少表格模板、响应文件格式和包件信息
- `requirement_processor.py` 决定技术需求是否足够原子化
- `evidence_binder.py` 和 `tender_workflow/product_facts.py` 决定能否从“底稿”升到“可外发稿”

所以如果生成效果不好，通常不要只改 writer，要优先检查：

1. 解析结果是否稀疏
2. 需求归一化是否塌缩
3. 产品事实和证据是否真的被绑定
4. 质量门禁是否正确阻断了不成熟输出

### 7.3 一键生成和正式工作流都在复用同一套底层能力

两条路线并不是两套完全独立系统。它们底层会复用：

- `tender_parser.py`
- `requirement_processor.py`
- `evidence_binder.py`
- `quality_gate.py`
- `docx_builder.py`
- `one_click_generator/*` 中的一部分章节与表格逻辑

差别主要在于：

- `one_click` 更偏“快速出单包底稿 + 交互补录”
- `workflow` 更偏“十阶段审计链路 + 证据与外发治理”

## 8. 开发时最常用的入口

### 看接口定义

- `app/routers/tender/workflow.py`
- `app/routers/tender/one_click.py`
- `app/routers/tender/crud.py`

### 看解析效果

- `app/services/tender_parser.py`
- `tests/test_pdf_sample_scope_regressions.py`
- `tests/test_zb_format_strictness.py`

### 看章节生成效果

- `app/services/one_click_generator/pipeline.py`
- `app/services/one_click_generator/technical_sections.py`
- `app/services/one_click_generator/format_driven_sections/*.py`

### 看为什么被阻断外发

- `app/services/quality_gate.py`
- `app/services/tender_workflow/sanitization.py`
- `app/services/tender_workflow/validation.py`

### 看 Word 输出问题

- `app/services/docx_builder.py`
- `tests/test_docx_builder_pagination.py`
- `scripts/fix_bid_doc_v2.py`

## 9. 一句话总结

把这个项目理解成“招标文件解析器 + 本地知识库 + 两条标书生成管道 + 一套很重的质量门禁/外发治理系统”基本就对了；真正影响效果的核心文件，集中在 `tender_parser.py`、`requirement_processor.py`、`evidence_binder.py`、`one_click_generator/pipeline.py` 和 `tender_workflow/*`。
