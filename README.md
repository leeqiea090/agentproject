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

### 3.2 环境变量

默认不需要配置 `LLM_API_KEY`。

启动服务后，直接在首页页面里粘贴 API Key，前端会在每次请求时通过 `X-LLM-API-Key` 请求头透传给后端。

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
- `.env`：桌面版可选环境变量配置文件

### 桌面版可选环境变量

如需覆盖模型地址、模型名等配置，可在下面这个位置创建配置文件：

```bash
~/Library/Application Support/BidAgent/.env
```

示例：

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

### 实际限制

- 建议使用 Python `3.12` 或 `3.13` 的虚拟环境构建；当前仓库里的 `LangChain/Pydantic v1` 兼容层在 Python `3.14+` 下已有兼容性警告。
- 首次使用嵌入模型时，`sentence-transformers` 可能需要联网下载模型。
- 如果你希望把模型也一起离线封进 App，需要额外预下载 Hugging Face 模型并调整缓存路径。
- 当前桌面壳本质上仍然是“本地 Web 服务 + 内嵌浏览器”，但对最终使用者来说已经是可双击启动的 `.app`。
