# 招投标文件自动生成系统

## 项目简介

这是一个基于 AI 的招投标文件自动生成系统，能够：
- 自动解析招标文件（PDF格式）
- 提取关键信息（项目名称、技术要求、商务条款等）
- 自动生成符合政府采购规范的投标文件
- 支持企业信息和产品库管理

## 技术栈

- **后端框架**: FastAPI
- **AI框架**: LangChain + LangGraph
- **LLM**: OpenAI GPT-4 / GPT-3.5-turbo
- **PDF处理**: PyPDF
- **向量数据库**: ChromaDB
- **嵌入模型**: Sentence Transformers

## 项目结构

```
agentproject-main/
├── app/
│   ├── config.py               # 配置管理
│   ├── main.py                 # FastAPI主程序
│   ├── schemas.py              # 数据模型（Pydantic）
│   ├── routers/
│   │   ├── chat.py             # Agent对话路由
│   │   ├── kb.py               # 知识库路由
│   │   └── tender.py           # 招投标系统路由 ✨新增
│   └── services/
│       ├── llm.py              # LLM服务
│       ├── embeddings.py       # 嵌入模型服务
│       ├── retriever.py        # 检索服务
│       ├── tender_parser.py    # 招标文件解析器 ✨新增
│       └── bid_generator.py    # 投标文件生成Agent ✨新增
├── data/
│   ├── uploads/                # 上传的招标文件
│   └── outputs/                # 生成的投标文件
├── docs/
│   └── project_design.md       # 技术方案文档
├── test_tender_system.py       # 测试脚本
├── extract_samples.py          # PDF提取工具
└── requirements.txt            # 依赖包列表
```

## 快速开始

### 1. 环境准备

```powershell
# 激活虚拟环境（如果已有）
.\.venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

创建 `.env` 文件：

```env
# OpenAI API配置
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini  # 或 gpt-4, gpt-3.5-turbo

# 应用配置
APP_VERSION=1.0.0
```

### 3. 启动服务

```powershell
# 开发模式（带热重载）
python -m app.main

# 或使用uvicorn
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

服务启动后访问：
- **Web界面**: http://localhost:8000
- **API文档**: http://localhost:8000/docs (需配置 `docs_url`)

## 使用指南

### 方式一：使用测试脚本

运行测试脚本体验完整流程：

```powershell
python test_tender_system.py
```

测试脚本会执行以下步骤：
1. 上传招标文件PDF
2. 解析招标文件内容
3. 创建企业信息
4. 添加产品信息
5. 生成投标文件
6. 下载生成的投标文件

### 方式二：使用API

#### 1. 上传招标文件

```bash
curl -X POST "http://localhost:8000/api/tender/upload" \
  -F "file=@招标文件.pdf"
```

响应：
```json
{
  "tender_id": "uuid-string",
  "upload_time": "2026-03-09T12:00:00",
  "status": "uploaded"
}
```

#### 2. 解析招标文件

```bash
curl -X POST "http://localhost:8000/api/tender/parse/{tender_id}"
```

响应：
```json
{
  "tender_id": "uuid-string",
  "parsed_data": {
    "project_name": "检验科购置全自动电泳仪等设备",
    "project_number": "[230001]FDGJ[TP]20250027",
    "budget": 2708000.00,
    "packages": [...]
  }
}
```

#### 3. 创建企业信息

```bash
curl -X POST "http://localhost:8000/api/tender/company/profile" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "企业名称",
    "legal_representative": "法定代表人",
    "address": "详细地址",
    "phone": "联系电话",
    "licenses": [...]
  }'
```

#### 4. 添加产品

```bash
curl -X POST "http://localhost:8000/api/tender/products" \
  -H "Content-Type: application/json" \
  -d '{
    "product_name": "产品名称",
    "manufacturer": "生产厂家",
    "price": 100000.00,
    "specifications": {...}
  }'
```

#### 5. 生成投标文件

```bash
curl -X POST "http://localhost:8000/api/tender/bid/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "tender_id": "uuid-string",
    "company_profile_id": "uuid-string",
    "selected_packages": ["1", "6"],
    "product_ids": {
      "1": "product-uuid-1",
      "6": "product-uuid-6"
    },
    "discount_rate": 0.95
  }'
```

#### 6. 下载投标文件

```bash
curl "http://localhost:8000/api/tender/bid/download/{bid_id}?format=markdown" \
  -o 投标文件.md
```

## API端点列表

### 招标文件管理
- `POST /api/tender/upload` - 上传招标文件
- `POST /api/tender/parse/{tender_id}` - 解析招标文件
- `GET /api/tender/parsed/{tender_id}` - 获取解析结果

### 企业信息管理
- `POST /api/tender/company/profile` - 创建/更新企业信息
- `GET /api/tender/company/profile/{company_id}` - 获取企业信息

### 产品管理
- `POST /api/tender/products` - 添加产品
- `GET /api/tender/products/{product_id}` - 获取产品信息
- `GET /api/tender/products` - 获取产品列表

### 投标文件生成
- `POST /api/tender/bid/generate` - 生成投标文件
- `GET /api/tender/bid/{bid_id}` - 获取投标文件信息
- `GET /api/tender/bid/download/{bid_id}` - 下载投标文件
- `POST /api/tender/workflow/run` - 运行十层正式流程（文档接入→包件切分→条款分类→需求归一化→规则决策→证据绑定→分章节生成→硬校验→双输出→评测回归）
- `GET /api/tender/workflow/{workflow_id}` - 查询十层流程结果

## 工作原理

### 1. 招标文件解析（tender_parser.py）

```python
TenderParser
  ├─ extract_text_from_pdf()  # PDF文本提取
  ├─ parse_tender_document()  # 使用LLM结构化提取
  └─ extract_technical_requirements()  # 提取技术要求
```

**关键技术：**
- PyPDF提取文本 → LLM分析 → Pydantic验证 → 结构化数据

### 2. 投标文件生成（bid_generator.py）

使用 **LangGraph** 编排多个生成Agent：

```
BidGeneratorAgent (LangGraph)
  ├─ generate_qualification_section()   # 资格性证明
  ├─ generate_compliance_section()      # 符合性承诺
  ├─ generate_technical_section()       # 技术响应
  ├─ generate_commercial_section()      # 商务报价
  ├─ generate_service_section()         # 售后服务
  └─ finalize_bid()                     # 汇总生成
```

**工作流程：**
```
输入状态 → [资格性文件] → [符合性承诺] → [技术响应] → [商务报价] → [售后服务] → [汇总] → 输出文件
```

## 示例输出

生成的投标文件包含以下章节：

1. **封面**
   - 项目名称、编号
   - 供应商信息、授权代表

2. **目录**

3. **第一章：资格性证明文件**
   - 政府采购法资格声明
   - 营业执照说明
   - 社保缴纳证明
   - 信用查询说明
   - 法定代表人授权书
   - 围标串标承诺函

4. **第二章：符合性承诺**
   - 投标报价承诺
   - 商务条款响应
   - 技术响应承诺

5. **第三章：商务及技术部分**
   - 技术偏离表
   - 详细配置清单
   - 报价书和报价一览表

6. **第四章：技术服务和售后服务**
   - 技术服务方案
   - 售后服务承诺

## 配置说明

### LLM模型选择

在 `.env` 文件中配置：

```env
# 推荐：GPT-4o-mini（性价比高）
OPENAI_MODEL=gpt-4o-mini

# 或：GPT-4（质量最高，成本较高）
OPENAI_MODEL=gpt-4

# 或：GPT-3.5-turbo（速度快，成本低）
OPENAI_MODEL=gpt-3.5-turbo
```

### 成本估算

以 GPT-4o-mini 为例：
- 招标文件解析：~$0.30
- 投标文件生成：~$1-2
- **单次完整流程**：约 $1.5-2.5

## 常见问题

### Q1: PDF解析不准确怎么办？

A: 可能原因：
1. PDF是扫描件（需要OCR）
2. PDF包含复杂表格（尝试手动提取关键信息）
3. 文本提取乱码（检查PDF编码）

解决方案：
- 使用 `extract_samples.py` 预先提取查看
- 对于扫描件，先用OCR工具转换
- 手动修正解析结果后再生成投标文件

### Q2: 生成的投标文件不符合要求？

A: 优化方法：
1. 完善企业信息和产品库
2. 调整LLM的Prompt（修改 `bid_generator.py` 中的提示词）
3. 使用更强大的模型（如GPT-4）
4. 提供自定义服务方案

### Q3: 如何添加PDF生成功能？

A: 可以使用以下库：
- `reportlab`：底层PDF生成
- `weasyprint`：HTML转PDF
- `python-docx` + `docx2pdf`：Word转PDF

参考实现位置：`tender.py` 中的 `download_bid_document()` 函数

## 开发计划

- [x] 招标文件PDF解析
- [x] LLM信息提取
- [x] 投标文件生成Agent
- [x] API接口开发
- [x] 基础测试脚本
- [ ] PDF格式输出
- [ ] 前端Web界面
- [ ] 数据库持久化（当前使用内存存储）
- [ ] 文件上传进度显示
- [ ] 批量生成功能
- [ ] 历史记录管理
- [ ] 模板库扩展

## 贡献指南

欢迎提交Issue和Pull Request！

## 许可证

见 LICENSE.md

## 联系方式

如有问题，请提交Issue或联系开发团队。

---

**最后更新**: 2026-03-09
**版本**: v1.0
