# 招投标系统 - 快速启动指南

## 🚀 5分钟快速开始

### 1. 启动服务

```powershell
# 在项目根目录
cd C:\Users\lq\文稿\agentproject-main\agentproject-main

# 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 启动FastAPI服务
python -m app.main
```

服务启动后会显示：
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

### 2. 运行测试示例

打开新的终端窗口：

```powershell
# 确保在虚拟环境中
.\.venv\Scripts\Activate.ps1

# 运行测试脚本
python test_tender_system.py
```

测试脚本会自动：
1. ✓ 上传示例招标文件
2. ✓ 解析提取关键信息
3. ✓ 创建企业资料
4. ✓ 添加产品信息
5. ✓ 生成完整投标文件
6. ✓ 下载生成的文件

### 3. 查看生成结果

生成的文件保存在：
- `投标文件_{bid_id}.md` - Markdown格式投标文件

## 📋 系统功能概览

### 核心功能

| 功能 | 说明 | API端点 |
|------|------|---------|
| **招标文件解析** | 上传PDF并提取结构化信息 | `POST /api/tender/upload`<br>`POST /api/tender/parse/{id}` |
| **企业信息管理** | 维护企业资质和人员信息 | `POST /api/tender/company/profile` |
| **产品库管理** | 管理产品技术参数和价格 | `POST /api/tender/products` |
| **投标文件生成** | AI自动生成规范投标文件 | `POST /api/tender/bid/generate` |

### 生成的投标文件内容

```
📄 投标文件
├── 封面（项目信息、企业信息）
├── 目录
├── 第一章：资格性证明文件
│   ├── 政府采购法资格声明
│   ├── 营业执照说明
│   ├── 社保缴纳证明
│   ├── 信用查询说明
│   └── 法定代表人授权书
├── 第二章：符合性承诺
│   ├── 投标报价承诺
│   ├── 商务条款响应
│   └── 技术响应承诺
├── 第三章：商务及技术部分
│   ├── 技术偏离表
│   ├── 详细配置清单
│   └── 报价书
└── 第四章：技术服务和售后服务
    ├── 技术服务方案
    └── 售后服务承诺
```

## 🔧 常用命令

### 查看API文档

修改 `app/main.py`，启用API文档：

```python
app = FastAPI(
    # ...
    docs_url="/docs",      # 启用Swagger UI
    redoc_url="/redoc",    # 启用ReDoc
    openapi_url="/openapi.json"
)
```

然后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 手动测试API

使用 `curl` 或 `Postman`：

```bash
# 检查服务状态
curl http://localhost:8000/api/status

# 获取产品列表
curl http://localhost:8000/api/tender/products

# 上传文件
curl -X POST http://localhost:8000/api/tender/upload \
  -F "file=@招标文件.pdf"
```

### 查看日志

服务运行时会在控制台输出日志：

```
INFO:     127.0.0.1:50000 - "POST /api/tender/upload HTTP/1.1" 200 OK
INFO:app.services.tender_parser:成功从PDF提取文本，共 55970 字符
INFO:app.services.tender_parser:成功解析招标文件: 检验科购置全自动电泳仪等设备
```

## 📝 自定义配置

### 修改LLM模型

编辑 `.env` 文件：

```env
# 使用GPT-4（质量更高但成本较高）
OPENAI_MODEL=gpt-4

# 或使用GPT-3.5-turbo（速度快成本低）
OPENAI_MODEL=gpt-3.5-turbo

# 或使用GPT-4o-mini（推荐：性价比最佳）
OPENAI_MODEL=gpt-4o-mini
```

### 调整文本分块大小

编辑 `app/services/tender_parser.py`：

```python
# 默认限制50000字符
if len(tender_text) > 50000:
    tender_text = tender_text[:50000]

# 可以修改为其他值，但需注意LLM的token限制
```

## 🎯 使用场景

### 场景1：快速响应招标

```
招标截止前24小时:
1. 上传招标文件 (2分钟)
2. AI解析提取要求 (1分钟)
3. 匹配企业产品 (3分钟)
4. 生成投标文件 (5分钟)
5. 人工审核修改 (15分钟)
6. 打印盖章提交 (10分钟)

总计约36分钟完成投标准备！
```

### 场景2：批量项目投标

```python
# 伪代码：批量处理多个项目
projects = ["项目1.pdf", "项目2.pdf", "项目3.pdf"]

for project_pdf in projects:
    # 上传解析
    tender_id = upload_and_parse(project_pdf)

    # 自动匹配产品
    matched_products = auto_match_products(tender_id)

    # 生成投标文件
    bid_id = generate_bid(tender_id, matched_products)

    # 下载审核
    download_for_review(bid_id)
```

### 场景3：知识库积累

每次投标后：
1. 保存中标的投标文件到知识库
2. 记录评分标准和专家意见
3. 下次生成时参考历史成功案例
4. 持续优化提高中标率

## ⚠️ 注意事项

### 1. API密钥配置

确保在 `.env` 中配置了有效的 OpenAI API Key：

```env
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx
```

### 2. PDF文件要求

- ✓ 文本型PDF（可直接提取文字）
- ✗ 扫描件PDF（需要先OCR）
- ✓ 文件大小 < 50MB
- ✓ 格式规范的政府采购文件

### 3. 生成内容审核

**重要：AI生成的投标文件需要人工审核！**

必须检查：
- ✓ 企业信息准确性
- ✓ 技术参数匹配度
- ✓ 报价合理性
- ✓ 资质证明完整性
- ✓ 法律条款合规性

### 4. 数据安全

当前版本使用内存存储，重启后数据会丢失。

生产环境建议：
- 使用数据库（PostgreSQL/MySQL）
- 加密敏感信息
- 定期备份投标文件
- 访问权限控制

## 🐛 故障排查

### 问题1：服务启动失败

```
ModuleNotFoundError: No module named 'xxx'
```

**解决**：安装缺失的依赖
```powershell
pip install -r requirements.txt
```

### 问题2：PDF解析错误

```
ValueError: 无法读取PDF文件
```

**解决**：
1. 检查PDF文件是否损坏
2. 确认是文本型PDF而非扫描件
3. 尝试用其他PDF阅读器打开验证

### 问题3：LLM调用失败

```
openai.error.AuthenticationError
```

**解决**：
1. 检查 `.env` 中的 `OPENAI_API_KEY`
2. 确认API密钥有效且有余额
3. 检查网络连接

### 问题4：生成内容不符合预期

**解决**：
1. 使用更强大的模型（如GPT-4）
2. 调整Prompt（修改 `bid_generator.py`）
3. 完善企业信息和产品库数据
4. 提供更多历史案例参考

## 📚 延伸阅读

- [完整技术文档](docs/project_design.md)
- [详细使用手册](README_TENDER.md)
- [API参考文档](http://localhost:8000/docs) （启用后）

## 💡 小技巧

1. **快速测试单个功能**：使用 `curl` 或 Postman 单独测试API端点

2. **查看详细日志**：在代码中添加更多 `logger.info()` 输出

3. **自定义模板**：修改 `bid_generator.py` 中的 Prompt 来调整输出格式

4. **批量导入产品**：编写脚本批量创建产品信息到系统中

5. **导出报告**：生成后可转换为Word文档，方便编辑和打印

## 🎉 开始使用

```powershell
# 一键启动
python -m app.main

# 另一个终端运行测试
python test_tender_system.py

# 查看生成的投标文件
notepad 投标文件_BID_*.md
```

祝投标顺利！🎊
