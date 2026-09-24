# LibraAI

一个 **插件化（plugin）的 Flask 应用平台**：左边栏按「模块」切换功能，新增功能 = 在 `modules/` 下加一个文件夹，
框架自动发现、自动注册路由、自动挂到侧边栏，不需要改 `app.py`。

目前内置 14 个模块，覆盖 **图书资料管理 + AI 问答/RAG + 在线工具箱 + AI 员工 + 工作自动化框架**。
首页即 AI 问答。

其中管理员侧边栏可见 **10 项**：另有 `ai_center` 为元数据模块（不进侧栏），
`chart` / `toolbox` / `office_tools` 三个已收口进「工具箱」（详见下方模块表）。

---

## 快速开始

```bash
# 1. 安装依赖（AI 相关依赖不装也能启动，只是 AI 功能会降级关闭）
pip install -r requirements.txt

# 2. 复制配置模板（不复制也能跑：程序会自动回退读 example 模板）
cp config/modules.yaml.example config/modules.yaml

# 3. 启动
python app.py
```

打开 <http://127.0.0.1:5005>。

**首次启动**会自动创建超级管理员 `admin`，初始密码**随机生成**，会出现在两个地方：

- 启动日志的 `[INIT]` 行；
- `config/.init_admin_pwd`（该文件已加入 `.gitignore`，不会入库）。

如果想自己指定，启动前设置环境变量即可：

```bash
# Linux / macOS
export ADMIN_INIT_PASSWORD='你自己想设的密码'
# Windows PowerShell
$env:ADMIN_INIT_PASSWORD = '你自己想设的密码'
```

> 本项目**没有内置任何公开的默认口令**。已有 `admin` 账号不会被初始化逻辑重置。

### 用 Docker 启动

```bash
docker compose up -d          # 构建并启动，端口 5005
docker compose logs -f        # 看日志（初始管理员密码打印在这里）
docker compose down           # 停止（数据保留在卷里）
```

镜像默认只装**核心依赖**（`requirements-core.txt`），构建快、体积小，所有功能（除 AI 与 Office 互转外）都可用。
需要 AI 向量检索 / 本地大模型时，构建时打开开关（会额外拉取torch / faiss，多花十几分钟）：

```bash
docker compose build --build-arg INSTALL_AI=true
```

数据落在命名卷 `app_data`（数据库）、`app_uploads`、`app_vector`、`app_ai_vector`；
模型放本地 `./models` 目录即可被读取，**不进镜像**。

> 容器内 `办公工具` 的 Word / Excel / PPT 互转不可用（依赖 Windows COM），
> PDF、图片、表格、文本、归档类工具均正常。

---

## 内置模块

| 模块 | 显示名 | 入口 | 侧栏 | 说明 |
|---|---|---|---|---|
| `homepage` | AI 问答 | `/home` | ✓ | 首页。全局问答，支持发散 / 保守两种模式 |
| `book_lib` | 图书馆 | `/book` | ✓ | 图书浏览 / 搜索 / 借阅 / 笔记 / 收藏 / 分类与权限 |
| `experience_hub` | 笔记本 | `/experience` | ✓ | 经验帖分享（Markdown），支持公开 / 私有 |
| `toolbox_hub` | 工具箱 | `/tools` | ✓ | 工具箱统一入口（聚合壳，本身无业务功能） |
| `workflow` | 工作自动化 | `/workflow` | ✓ | 私有 RPA 流程入口（**流程文件需自备，见下**） |
| `nav_hub` | 网站导航 | `/nav_hub` | ✓ | 分类管理与常用链接收藏 |
| `feedback` | 反馈中心 | `/feedback` | ✓ | 收集使用问题与改进建议 |
| `ai_agent` | AI 任务 | `/agent` | ✓ | 一句话下达任务 → 自动编排工具箱能力生成执行计划 |
| `ai_staff` | AI 员工 | `/staff` | ✓ | 卡片墙式 AI 员工，可检索代码、只读查库、分析日志 |
| `config_center` | 配置中心 | `/config` | ✓ | 管理员聚合：图书管理 / AI 中心 / 用户与权限 |
| `toolbox` | 在线工具 | `/toolbox` | 收口 | PDF / Office / 图片 / 文本 / 识别 / 计算换算等 60+ 工具 |
| `chart` | 图表工具 | `/chart` | 收口 | Excel / CSV 上传 → 多种图表可视化 |
| `office_tools` | 办公工具 | `/office_tools` | 收口 | 批量建文件夹、Excel 分表、批量重命名、转 PDF 等 |
| `ai_center` | AI 中心 | — | 元数据 | 全局 AI 能力开关与配置，不进侧边栏 |

**关于「收口」**：标 `收口` 的三个模块功能完整、路由可直连（`/toolbox`、`/chart`、`/office_tools`），
只是**不在侧边栏单独出现** —— 由 `toolbox_hub`（工具箱）统一归组，从 Hub 页面深链进入。
这是纯导航层收口：由 `core/plugin_scan.py` 的 `HUB_MERGED_MODULES` / `HUB_PARENT_MODULE` 控制，
**不动任何业务代码**。所以这四个模块在 `modules/` 下仍是平级目录，没有父子关系。

> 注意命名：`modules/toolbox` 的显示名是「在线工具」，而显示名「工具箱」的目录是 `modules/toolbox_hub`
> —— 目录名与显示名不对应，这是历史原因保留的，看显示名即可。

`ai_center`（AI 中心）是**元数据模块**：不进侧边栏，只提供全局 AI 能力（摘要 / 检索 / 问答）的开关与配置，
真正的实现在 `modules/ai_center/internal/`。

---

## 关于「工作自动化」模块

仓库开源的是 **RPA 框架**，不含任何具体业务流程：

- 框架代码：`modules/toolbox/automation/`（`base.py` / `runner.py` / `registry.py` / `launcher.py`）
- 示例流程：`modules/toolbox/automation/examples/example_flow.py`
- 你自己的流程：放到 `modules/toolbox/automation/flows/` 下即可被自动发现

`flows/*.py` 已在 `.gitignore` 中忽略（保留 `__init__.py` 与 `example_flow.py`），
所以私有业务流程不会进仓库，克隆后目录是空的 —— 照着 `example_flow.py` 写自己的即可。

---

## 目录结构

```
app.py                     应用入口：create_app() → 初始化库 → 扫描注册模块
core/                      全站基础设施（配置 / 权限 / 响应 / 数据库 / 异常 / AI 门面）
  ├─ plugin_scan.py        插件扫描器：自动发现 modules/* 并注册 Blueprint
  └─ db_base.py            唯一数据库访问层（业务模块不要写裸 SQL）
modules/                   业务模块，一个文件夹一个模块
templates/                 全局模板（layout_base 等）
static/                    全局静态资源（含 vendor 第三方库）
config/
  ├─ app.yaml             全局配置（端口 / 数据库 / 上传 / AI 默认档）
  ├─ modules.yaml         模块开关与参数（含内网地址与密钥，**不入库**）
  └─ modules.yaml.example  安全模板（入库）
docs/                      项目文档
tests/                     回归脚本（_t_*.py）
```

以下目录是运行产物，**不会入库**：`models/`、`uploads/`、`temp/`、`logs/`、`vector_data/`、
`ai_vector_store/`、`db_export/`、`book_manager.db`。

---

## AI 能力（可选）

AI 全部可选：模型缺失时应用照常启动，只是 AI 中心降级为关闭状态。

- 本地 GGUF 推理：`llama_cpp`
- 远程推理：`ollama` / 阿里百炼 `dashscope` / `transformers`
- 向量检索：`bge` 嵌入 + `faiss`

模型放到 `models/` 目录（该目录不入库），在 **配置中心 → AI 中心** 里切换后端与模型路径，热生效无需重启。

文档解析支持 `pdf / txt / docx / doc / xlsx / pptx`。

---

## 新增一个模块

在 `modules/<你的模块>/__init__.py` 里自报元数据即可，框架负责其余一切：

```python
from flask import Blueprint
import os

bp = Blueprint("bulletin", __name__,
               template_folder=os.path.join(os.path.dirname(__file__), "templates"),
               static_folder=os.path.join(os.path.dirname(__file__), "static"),
               static_url_path="/static/bulletin")

module_info = {
    "module_id": "bulletin",
    "display_name": "公告栏",
    "icon": "📌",
    "route_prefix": "/bulletin",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "系统公告展示",
    "version": "1.0.0",
    "blueprint": bp,
}

from . import routes   # 必须在 blueprint 定义之后
```

然后在 `config/modules.yaml` 中启用（不写也能被发现，只是排序靠后）：

```yaml
modules:
  bulletin:
    enabled: true
    sort_order: 4
```

更完整的规范见 [`docs/给AI写新模块的提示文档.md`](docs/给AI写新模块的提示文档.md)。

---

## 部署安全提示

- **密钥**：生产环境用环境变量 `SECRET_KEY` 注入，不要写进 `config/app.yaml`。
- **管理员密码**：用 `ADMIN_INIT_PASSWORD` 指定，启动后尽快在「配置中心 → 用户与权限」修改。
- **反向代理**：登录失败锁定按「账号 + IP」计数，程序默认**不采信** `X-Forwarded-For`
  （否则可被伪造绕过）。确需信任代理时显式设置 `TRUST_PROXY=1`。

---

## 测试

回归脚本在 `tests/` 下（`_t_*.py`），多数需要服务已在 5005 端口运行：

```bash
python app.py                 # 另开一个终端
python tests/_t_module_routes.py
```

需要真实浏览器 / 明文密码的脚本会从环境变量 `ADMIN_INIT_PASSWORD` 读取管理员密码。

---

## 许可

本项目使用 **MIT License**，见 [`LICENSE`](LICENSE)。

前端随源码附带的第三方库（ECharts、marked、highlight.js、DOMPurify）许可不同，
声明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
