# FastAPI + LangGraph Multi-Agent (本地知识库)

这是一个可直接运行的 AI Agent 服务骨架：
- FastAPI 提供 API
- LangGraph 编排多 Agent 团队
- SQLite 本地向量库存储你的标书专业资料（embedding + cosine 检索）
- OpenAI 兼容模型 API（支持工具调用）

## 1. 能力概览

- 知识库入库
  - 文本入库：`POST /kb/text`
  - 文件入库：`POST /kb/file`（支持 `txt/md/pdf/docx`）
- 知识库检索
  - 语义检索：`POST /kb/search`
  - 库状态：`GET /kb/stats`
- 多 Agent 团队执行
  - 目标驱动执行：`POST /agent/run`
  - 你只提供最终目标，Supervisor 自动拆解并分配给子 Agent

## 2. 团队角色

- Supervisor（总负责）
  - 拆解目标
  - 分配任务
  - 汇总最终交付
- Researcher
  - 调用知识库检索工具，提炼专业知识
- Writer
  - 基于任务和研究结果生成初稿
- Reviewer
  - 审校完整性、风险和可执行性

## 3. 快速启动

```bash
![img.png](img.png)python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## 4. 典型调用

### 4.1 入库（文本）

```bash
curl -X POST 'http://127.0.0.1:8000/kb/text' \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "招投标法规手册",
    "text": "这里放你的专业资料正文...",
    "metadata": {"domain": "construction"}
  }'
```

### 4.2 入库（文件）

```bash
curl -X POST 'http://127.0.0.1:8000/kb/file' \
  -F 'file=@/absolute/path/to/your_file.pdf'
```

### 4.3 只给最终目标，让团队自动完成

```bash
curl -X POST 'http://127.0.0.1:8000/agent/run' \
  -H 'Content-Type: application/json' \
  -d '{
    "goal": "为某市政道路改造项目生成完整投标技术方案初稿（含实施组织、质量、安全、进度、风险）",
    "constraints": [
      "正文需按章节输出",
      "风险章节至少覆盖技术、工期、合规三类"
    ],
    "output_format": "Markdown"
  }'
```

## 5. 切换任意模型 API

只要你的模型服务兼容 OpenAI Chat Completions 且支持工具调用，改这 3 项即可：

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`

## 6. 注意事项

- 首次加载 embedding 模型可能需要下载。
- 如果你希望完全离线，请把 `EMBEDDING_MODEL_NAME` 改成本地模型路径。
- 多 Agent 默认最大轮次由 `TEAM_MAX_TURNS` 控制。
# agentproject
