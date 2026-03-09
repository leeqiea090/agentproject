# 招投标文件自动生成系统 - 技术方案

## 1. 项目概述

### 1.1 业务目标
- **输入**：招标文件（PDF格式）
- **输出**：投标文件（PDF格式，符合政府采购规范）
- **核心价值**：自动化投标文件制作，提高效率，减少人工错误

### 1.2 系统架构
```
招标PDF → PDF解析 → LLM分析提取 → 信息结构化 → 模板填充 → PDF生成 → 投标PDF
```

## 2. 技术组件

### 2.1 已有基础设施
- FastAPI Web框架
- LangChain + LangGraph（Agent编排）
- LangChain-OpenAI（LLM调用）
- ChromaDB（向量数据库）
- Sentence Transformers（嵌入模型）

### 2.2 需要新增
- **PyPDF** (已安装) - PDF文本提取
- **python-docx** (已安装) - Word文档处理（中间格式）
- **reportlab** 或 **weasyprint** - PDF生成
- **Jinja2** - 模板引擎

## 3. 核心功能模块

### 3.1 招标文件解析模块 (Tender Parser)
**功能**：
- PDF转文本
- 结构化信息提取
- 关键字段识别

**提取字段**：
```python
{
    "project_name": "项目名称",
    "project_number": "项目编号",
    "budget": "预算金额",
    "purchaser": "采购人",
    "agency": "代理机构",
    "procurement_packages": [
        {
            "package_id": "包号",
            "item_name": "货物/服务名称",
            "quantity": "数量",
            "budget": "预算金额",
            "technical_requirements": "技术参数",
            "delivery_time": "交货期",
            "delivery_place": "交货地点"
        }
    ],
    "commercial_terms": {
        "payment_method": "付款方式",
        "validity_period": "投标有效期",
        "warranty_period": "质保期"
    },
    "evaluation_criteria": "评分标准"
}
```

### 3.2 企业信息管理模块 (Company Profile)
**存储**：
- 企业基本信息（营业执照、资质证件）
- 法定代表人信息
- 项目团队人员信息
- 社保缴纳证明
- 历史业绩
- 产品/服务信息库

**数据结构**：
```python
{
    "company_info": {
        "name": "企业全称",
        "legal_representative": "法定代表人",
        "address": "详细地址",
        "phone": "联系电话",
        "licenses": ["营业执照", "资质证件列表"]
    },
    "products": [
        {
            "product_name": "产品名称",
            "specs": "技术参数",
            "certifications": "认证证书",
            "price_range": "价格范围"
        }
    ]
}
```

### 3.3 投标内容生成模块 (Bid Generator)
**子模块**：

#### 3.3.1 资格性文件生成器
- 自动填充企业资质证明
- 生成各类承诺函（政府采购资格承诺、围标串标承诺等）
- 法定代表人授权书
- 信用查询截图说明

#### 3.3.2 符合性承诺生成器
- 投标报价承诺
- 商务条款响应承诺
- 技术响应承诺

#### 3.3.3 技术响应生成器
- 根据招标技术要求匹配产品参数
- 生成技术偏离表
- 详细配置清单
- 产品彩页附件

#### 3.3.4 商务报价生成器
- 报价一览表
- 详细报价明细表
- 成本分析（可选）

#### 3.3.5 售后服务方案生成器
- 标准化服务承诺
- 培训方案
- 技术支持计划
- 质保承诺

### 3.4 文档生成模块 (Document Generator)
**功能**：
- 根据模板生成Word文档（中间格式）
- 转换为PDF
- 目录自动生成
- 页码、页眉页脚处理

**模板类型**：
- 资格性文件模板
- 符合性承诺模板
- 技术响应模板
- 商务报价模板

### 3.5 知识库与RAG模块
**用途**：
- 存储历史投标文件
- 行业术语知识库
- 政府采购法规知识库
- 产品技术文档库

**检索增强**：
- 根据招标要求检索相似历史案例
- 提取最佳实践
- 规范性文本推荐

## 4. 数据流设计

### 4.1 核心流程
```
1. 用户上传招标PDF
   ↓
2. PDF解析 + 文本提取
   ↓
3. LLM结构化提取关键信息
   ↓
4. RAG检索相关知识（历史案例、产品库、法规）
   ↓
5. LangGraph Agent编排各生成器
   ├─ 资格性文件生成
   ├─ 符合性承诺生成
   ├─ 技术响应生成
   ├─ 商务报价生成
   └─ 售后服务生成
   ↓
6. 汇总内容 + 模板渲染
   ↓
7. 生成Word/PDF
   ↓
8. 返回给用户（可下载）
```

### 4.2 Agent工作流（LangGraph）
```
graph TD
    A[接收招标文件] --> B[信息提取Agent]
    B --> C[匹配产品Agent]
    C --> D[报价计算Agent]
    D --> E[文档生成Agent]
    B --> F[合规检查Agent]
    F --> E
    E --> G[输出投标文件]
```

## 5. API设计

### 5.1 主要端点
```
POST /api/tender/upload          # 上传招标文件
POST /api/tender/parse            # 解析招标文件
POST /api/bid/generate            # 生成投标文件
GET  /api/bid/download/{bid_id}   # 下载投标文件
POST /api/company/profile         # 更新企业信息
GET  /api/company/products        # 获取产品列表
POST /api/company/products        # 添加产品
```

### 5.2 请求/响应示例
```json
// POST /api/tender/parse
{
    "file_id": "tender_123",
    "extract_mode": "full"
}

// Response
{
    "tender_id": "tender_123",
    "parsed_data": {
        "project_name": "检验科购置全自动电泳仪等设备",
        "project_number": "[230001]FDGJ[TP]20250027",
        "budget": 2708000.00,
        "packages": [...]
    }
}

// POST /api/bid/generate
{
    "tender_id": "tender_123",
    "company_profile_id": "company_001",
    "selected_packages": [1, 6],
    "custom_options": {
        "add_performance_cases": true,
        "discount_rate": 0.95
    }
}

// Response
{
    "bid_id": "bid_456",
    "status": "generated",
    "download_url": "/api/bid/download/bid_456",
    "preview_available": true
}
```

## 6. 数据存储设计

### 6.1 数据库表（PostgreSQL 或 SQLite）
```sql
-- 招标文件表
CREATE TABLE tenders (
    id UUID PRIMARY KEY,
    project_name VARCHAR(500),
    project_number VARCHAR(100),
    budget DECIMAL(15, 2),
    purchaser VARCHAR(200),
    agency VARCHAR(200),
    upload_time TIMESTAMP,
    parsed_data JSONB,
    original_file_path VARCHAR(500)
);

-- 企业信息表
CREATE TABLE companies (
    id UUID PRIMARY KEY,
    name VARCHAR(200),
    legal_representative VARCHAR(100),
    contact_info JSONB,
    licenses JSONB,
    created_at TIMESTAMP
);

-- 产品库表
CREATE TABLE products (
    id UUID PRIMARY KEY,
    company_id UUID REFERENCES companies(id),
    product_name VARCHAR(200),
    category VARCHAR(100),
    specs JSONB,
    price DECIMAL(15, 2),
    certifications JSONB
);

-- 投标文件表
CREATE TABLE bids (
    id UUID PRIMARY KEY,
    tender_id UUID REFERENCES tenders(id),
    company_id UUID REFERENCES companies(id),
    generated_content JSONB,
    file_path VARCHAR(500),
    status VARCHAR(50),
    created_at TIMESTAMP
);
```

### 6.2 向量数据库（ChromaDB）
```python
# Collection 1: 历史投标文档
collection_historical_bids = chroma.create_collection("historical_bids")

# Collection 2: 产品技术文档
collection_product_docs = chroma.create_collection("product_docs")

# Collection 3: 法规知识库
collection_regulations = chroma.create_collection("regulations")
```

## 7. LLM Prompt设计

### 7.1 信息提取Prompt
```
你是一个专业的招标文件分析专家。请从以下招标文件中提取关键信息：

文件内容：
{tender_text}

请按照以下JSON格式提取信息：
{
    "project_name": "项目名称",
    "project_number": "项目编号",
    ...
}

注意：
1. 确保所有金额数字准确
2. 技术参数完整提取
3. 不要遗漏任何商务条款
```

### 7.2 技术响应生成Prompt
```
根据招标文件的技术要求，生成技术响应内容：

招标技术要求：
{technical_requirements}

我方产品参数：
{product_specs}

请生成：
1. 技术偏离表（逐条对比）
2. 技术优势说明
3. 产品详细配置清单

要求：
- 专业、准确
- 突出产品优势
- 如有偏离需明确说明
```

## 8. 实施计划

### Phase 1: 基础功能 (Week 1-2)
- [ ] PDF解析模块
- [ ] 信息提取Prompt优化
- [ ] 企业信息管理API
- [ ] 基础文档模板

### Phase 2: 核心生成 (Week 3-4)
- [ ] LangGraph Agent设计
- [ ] 各子生成器实现
- [ ] RAG知识库搭建
- [ ] Word文档生成

### Phase 3: 完善与优化 (Week 5-6)
- [ ] PDF生成与排版优化
- [ ] 前端界面（Web UI）
- [ ] 批量处理功能
- [ ] 测试与调试

### Phase 4: 上线与迭代 (Week 7+)
- [ ] 部署上线
- [ ] 用户反馈收集
- [ ] 模型优化
- [ ] 功能扩展

## 9. 技术风险与应对

### 9.1 风险点
1. **PDF解析准确性**：扫描件、图片表格难以提取
   - 应对：引入OCR（Tesseract/PaddleOCR）

2. **LLM幻觉问题**：生成内容不准确
   - 应对：多轮验证、人工审核机制

3. **模板适配性**：不同地区招标格式差异大
   - 应对：模板库扩展、动态模板生成

4. **合规性风险**：生成内容不符合法规
   - 应对：法规知识库、合规检查Agent

## 10. 成本估算

### 10.1 API调用成本（以GPT-4为例）
- 每个招标文件解析：~10K tokens × $0.03/1K ≈ $0.30
- 投标文件生成：~50K tokens × $0.06/1K ≈ $3.00
- **单次完整流程**：约 $3.5

### 10.2 优化方案
- 使用更便宜的模型（GPT-3.5-turbo）处理简单任务
- 本地模型（Llama, Qwen）处理格式化任务
- 缓存常用内容减少API调用

## 11. 后续扩展

1. **多语言支持**：英文标书
2. **智能报价**：基于历史数据的AI报价建议
3. **风险评估**：评估中标概率
4. **协同编辑**：多人协作完善投标文件
5. **移动端**：手机查看和审批

---
**文档版本**: v1.0
**更新时间**: 2026-03-09
**作者**: AI Assistant
