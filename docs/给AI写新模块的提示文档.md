# 给「外部 AI / 新同事」写新模块功能的提示文档（PROMPT BRIEFING）

> 用途：当你（另一个 AI 或新接手的人）被要求「在这个项目里加一个新模块 / 新功能」时，
> 把**本文档全文**作为背景知识粘贴给你的对话，再附上具体需求即可。
> 本文档已包含架构、目录地图、必须遵循的规范、核心 API 速查、常见坑、最小可运行样例、交付前自检清单。
> **不要凭空猜测本项目的文件结构或 API**，一切以本文档 + 实际读取的代码为准。

---

## 0. 任务定义（你被期望做什么）

你要在下面这个 **Flask 插件化图书管理系统**（项目根目录 `D:\xianmufile`）里，新增一个业务模块（一个文件夹），或在一个已有模块里加功能。框架会自动把新模块挂到**管理员**侧边栏，**你不需要改 `app.py`**；但非管理员用户能否看到入口取决于 `DEFAULT_PUBLIC_MODULES` 白名单（见 §3 〔侧边栏可见性〕）。

---

## 1. 项目一句话背景

- 一个基于 Flask 的「插件化」图书资料管理系统，左侧栏按模块切换；内置 AI 中心（摘要/检索/问答），首页是 AI 问答。
- **新增功能 = 新增 `modules/<your_module>/` 文件夹**，框架自动发现。
- 运行：端口 `5005`（由 `config/app.yaml` 的 `port` 决定，默认 5000），管理员账号 `admin`（初始密码首次启动随机生成，见 `config/.init_admin_pwd`）。

---

## 2. 目录地图（你只需要关心这些）

```
D:\xianmufile
├─ app.py                 # 入口，别改（除非改全局机制）
├─ config/
│   ├─ app.yaml           # 全局配置（端口/数据库/上传/AI兜底）
│   └─ modules.yaml       # 模块开关：enabled / sort_order / ai_center 参数  ← 启用新模块要改这里
├─ core/                  # 全站共享基础设施（只读复用，勿改内部契约）
│   ├─ plugin_scan.py     # 模块扫描/注册/菜单生成
│   ├─ auth.py            # login_required / permission_required / need_login_response
│   ├─ response.py        # ok() / fail() 统一返回 {code,msg,data}
│   ├─ db_base.py         # 唯一数据库层（用户/图书/分类/权限/向量/日志…）
│   ├─ ai_interface.py    # AI 门面（global_ai_chat / ask_about_book / summarize_text）
│   ├─ config_loader.py   # get_config("modules.x.y") / is_module_enabled(id)
│   └─ exceptions.py      # module_exception_guard("模块id") / 异常类
├─ modules/               # ★ 业务模块都在这里，每个一个文件夹
│   ├─ ai_center/         # AI 实现（internal/），无页面、不进侧边栏（NON_NAV_MODULES）
│   ├─ book_lib/          # 图书馆
│   ├─ chart/             # 图表工具
│   ├─ config_center/     # 配置中心（管理员聚合页）
│   ├─ experience_hub/    # 经验分享（帖/点赞/收藏）
│   ├─ homepage/          # 首页 AI 问答
│   ├─ nav_hub/           # 导航中心
│   ├─ office_tools/      # 办公工具（批量重命名/提取等）
│   ├─ toolbox/           # 工具箱
│   └─ <your_module>/     # ← 你新建的
├─ templates/
│   ├─ layout_base.html   # 基础布局（侧边栏+主区），子模板 extends 它
│   ├─ login.html / need_login.html / _user_menu.html
├─ static/                # 全局 css/js（layout_base.css / layout_base.js）
├─ common/utils.py        # 通用工具（resolve_file_path / get_client_ip / now_str 等）
├─ models/                # AI 模型文件
└─ book_manager.db        # SQLite 主库
```

---

## 3. 必须遵循的规范（违规 = 功能不显示/报错/被拒）

- **〔模块注册〕** 每个模块文件夹必须有 `__init__.py`，定义 `bp = Blueprint(...)` 和 `module_info` 字典（含 `module_id`/`display_name`/`icon`/`route_prefix`/`url_default_endpoint`/`blueprint`）。最后一行 `from . import routes`。**`module_id` 必须等于文件夹名**（扫描器发现不一致会以文件夹名强制覆盖并告警）。`config_entries` / `PERMISSIONS` / `ai_features` 是 **module 级变量**（与 `bp`/`module_info` 同级，扫描器用 `getattr` 收集），**不要**塞进 `module_info` 字典内。
- **〔禁止模块顶层访问应用上下文〕** `__init__.py` 与 `routes.py` 的**模块顶层（import 时执行的作用域）绝对不能**访问 `current_app` / `g` / `db`（Flask-SQLAlchemy 的 `db` 同理）。插件扫描器在启动时用 `importlib.import_module` 加载模块，此时**还没有 Flask 应用上下文**，一旦顶层触碰 `current_app` 就会抛 `RuntimeError: Working outside of application context`，导致**整个模块加载失败、blueprint 不注册**（日志表现为 `[plugin_scan] ⚠️ 加载模块[xxx]失败(import异常): Working outside of application context.`）。凡需应用上下文的值（如 `current_app.root_path`、临时目录路径、数据库连接）一律**延迟到函数内部**取，或在函数内 `from flask import current_app`。参考修法（`modules/toolbox/routes.py` 实战）：把顶层 `TEMP_DIR = os.path.join(... current_app.root_path ...)` 删除，改为复用 `handlers.get_temp_dir()`（该函数内部才 `from flask import current_app`），所有路由里用到临时目录处调用 `get_temp_dir()` 即可。
- **〔启用〕** 新模块必须在 `config/modules.yaml` 加 `enabled: true` + `sort_order`，否则不被扫描注册。
- **〔API 返回〕** 一律用 `from core.response import ok, fail`，返回 `{code,msg,data}`。前端只读 `res.data.xxx`。**不要**返回裸字符串/dict。
- **〔登录字段〕** 是 `account` + `password`（**不是** `username`）。
- **〔未登录处理〕** 用 `need_login_response(next_url)`（提示不跳转）。**禁止**写 `redirect(url_for("login_view"))`。AJAX 会自动返回 `401 {"need_login":true}`。
- **〔权限装饰〕** 路由上务必加 `@module_exception_guard("模块id")`；需要登录加 `@login_required`，需要权限加 `@permission_required("perm_key")`。（`module_enabled_required("模块id")` 一般无需加在自身路由上——模块未启用时蓝图根本不会注册；仅在跨模块调用或"启用态下仍需动态判断"时使用。）
- **〔数据库〕** 只读/写都应 `from core.db_base import <函数>`，**禁止**在业务模块里写裸 `sqlite3`。若确有需要写原始 SQL（如 JOIN `users` 取作者名），注意 `users` 表的显示名列是 **`name`，不是 `nickname`**——用 `u.name`（可 `AS author_nickname` 兼容前端），写 `u.nickname` 会让所有相关接口 500。调用前先 `Grep` 确认函数签名（例如向量映射是 `add_vector_mapping(book_id, file_hash, chunk_index, faiss_index, preview=None)`）。
- **〔调 AI〕** 只通过 `core.ai_interface`（真实函数名，勿凭记忆）：`global_ai_chat(question, history=, mode=)`（首页全局问答）/ `ask_book_question(book_id, question, top_k=5)`（针对单书问答，**需 ai_mode=full 且该书已建索引**）/ `generate_book_summary(book_id, book_text, force=False)`（图书摘要，**light/full 模式**）。另有 `is_ai_enabled()` / `get_ai_mode()` / `get_ai_features()` / `trigger_rebuild_for_book(book_id)` / `get_vector_stats()` / `invalidate_vectors_for_book(book_id)`。详见 §3.1。**禁止**直接用 `ai_center.internal` 调问答/摘要/检索（一律走 `core.ai_interface`）。唯一例外：若要把**自己模块的内容**喂进全局向量库、让「📚保守」全局问答也能检索到（见 〔让模块内容可被 AI 检索〕），才允许 import `ai_center.internal` 的 `vector_store`/`embedder`，且须接受这是最底层契约、可能随 AI 中心重构而变。
- **〔读配置〕** 用 `core.config_loader.get_config("modules.模块id.xxx")`；AI 配置改动用 `core.ai_config_store`。配置有 5 分钟缓存，调试传 `force=True`。
- **〔模板〕** 子模板 `{% extends "layout_base.html" %}`，只填块：`page_title`/`extra_css`/`content`/`extra_js`。**模块自己的 CSS/JS 必须放全局 `static/<模块id>/` 目录**（如 `static/demo/style.css`），模板用 `url_for('static', filename='<模块id>/style.css')` 引用——⚠️ **不要用蓝图 `static_url_path="/static/<模块id>"` + 模板 `filename='style.css'` 的方式**，本项目 Flask 3.x 下会全部 404（文件实际在 `static_folder/<模块id>/` 子目录，Flask 找不到），导致样式/JS 全失效、页面退化成裸 HTML。另外：若模板里有 `window.XXX = {...}` 初始化全局变量，**必须放在 `<script src="...script.js">` 之前**，否则 script.js 顶层 `const CONFIG = window.XXX || {}` 会先执行拿到 `undefined`，fetch 拼出 `/xxx/undefined` 全部 404。
- **〔侧边栏可见性〕** 管理员能看到所有启用模块；**非管理员只能看到 `core/plugin_scan.py` 里硬编码白名单 `DEFAULT_PUBLIC_MODULES` 中的模块**（当前 = homepage / book_lib / chart / experience_hub / office_tools / toolbox / nav_hub）。新模块默认不在白名单 → 普通用户侧边栏**看不到**。要让普通用户也看到，把模块 id 加进 `DEFAULT_PUBLIC_MODULES`（改代码，需重启）。注意：这跟「模块访问权限」（`module_access` 表，4 选 1 公开/登录/管理员/指定用户）是**两回事**——后者控制"点进去能不能访问"，前者控制"侧边栏入口显不显示"，两层不自动同步。
- **〔深色模式与灯光主题〕** 全局 `body` 未设 `color`，文字必须**显式设色且不要用纯黑 `#000`**，否则夜晚模式看不见。
- **〔展厅灯光主题系统〕** 项目有 5 套展厅灯光主题，由 `body[data-light-theme="xxx"]` 切换，黑夜默认 `quantum`（量子青），相关变量挂在 `:root`（默认量子青）与 `body[data-light-theme="..."]` 上：`--theme-primary` / `--theme-secondary` / `--theme-accent` / `--theme-glow-soft` / `--theme-glow-hard` / `--theme-bg-spot-*`。5 套枚举值：`quantum`（量子青）/ `aurora`（极光紫）/ `plasma`（等离子绿）/ `magma`（熔岩橙）/ `silver`（镭射银）。切换逻辑由 `static/js/layout_base.js` 的 `selectLightTheme()` 负责，内容区无需自己写切换 JS。
- **〔内容区主色跟随灯光（仅黑夜生效）〕** 模块在**自己根容器作用域**把模块主色 `--primary` 桥接到主题变量——但**必须限定在 `body.dark` 下**：
  ```css
  /* 白天：保留模块原写死主色（如 #6d5dfc），不跟随灯光 */
  .mymod-page { --primary: #6d5dfc; }
  /* 黑夜：跟随当前展厅灯光 */
  body.dark .mymod-page { --primary: var(--theme-primary); }
  ```
  ⚠️ **千万不要**写成 `body[data-light-theme] .mymod-page { --primary: var(--theme-primary); }`——那样白天模式 `body` 也带 `data-light-theme` 属性，内容区会被改成灯光色、破坏白天原样（已踩过此坑并修复）。图表的悬浮/激活态同样只在 `body.dark` 下把颜色换成 `var(--theme-*)`。
- **〔按钮：激活态 / 主按钮〕** 激活态 tab、pill、主按钮用「灯光色实底 + 深色文字」：`background: var(--primary); color: #0b0e14;`，再叠一层灯光色光晕 `box-shadow: 0 0 14px var(--theme-glow-soft)`（光晕规则同样只放 `body.dark` 块内，白天无光晕）。普通操作按钮若无激活态需求可保持原样。
- **〔图表 canvas 跟随灯光〕** 图表的系列色（JS 色板）**只在 `dark` 跟随 `--theme-primary`**，白天用模块原写死色板（参考 `modules/chart/static/js/chart.js` 的 `getThemePalette()`：`isDark ? [themePrimary, ...] : BASE_PALETTE`）。切换灯光时靠 `MutationObserver` 监听 `body` 的 `data-light-theme` 属性重绘（chart 模块已加，其他含 canvas 的模块照此办理）。
- **〔验收〕** 用 Playwright 无头分别验证 **白天**（内容区=原写死主色、不随灯光变化）与 **黑夜 + 某灯光**（内容区随主题变色），且控制台无 `console` / `pageerror`。改完用截图对比三模块以上确认。
- **〔重启〕** `app.py` 非 debug 模式：改 `routes.py`/`core` 后**手动重启**服务（用 PowerShell `Stop-Process -Force` 清旧进程，避免双实例同占 5005）。模板改动会自动重载，无需重启。
- **〔AI 配置热切换〕** `ai_mode` / `backend` / `model_paths` / `security_lock` 这些 AI 配置经 `core.ai_config_store` 写 `modules.yaml` 后**立即刷新内存缓存、无需重启**；唯一需要重启的是「整个 ai_center 模块 enable/disable 开关」（见 §4 的 `set_ai_*` 与 `toggle_module` 区别）。改 AI 配置调试时缓存有 5 分钟 TTL，热切换接口已强制 `force=True` 刷新。
- **〔让模块内容可被 AI 检索〕** 想让自己模块里的文字（如经验帖/笔记）也能被「📚保守」全局问答检索到，可在「创建/更新」时把内容写进**全局向量库**，「删除」时清除。命名空间用 `book_id = "<前缀>_<id>"`（如经验帖 `exp_<id>`，每篇独立，避免 `query()` 按 book_id 去重时只回一篇）。调用链：`from core.db_base import add_document_chunk, delete_document_chunks, clear_vector_mappings` + `from modules.ai_center.internal import vector_store, embedder`；流程为 ①`clear_vector_mappings(ns); delete_document_chunks(ns)`（编辑先清旧，防脏数据/重复）→ ②拼 `text = 标题 + 标签 + 正文` → ③`add_document_chunk(ns, 0, text)`（存正文，作为重建索引源头，先于向量化）→ ④`vecs = embedder.encode([text])`（**AI 关闭或模型缺失时返回 `None`，务必判空**）→ ⑤`vector_store.store(ns, [text], vecs)`（写向量+映射）。反索引：`clear_vector_mappings(ns); delete_document_chunks(ns)`。关键点：`vector_store.query(book_id=None, ...)` 会搜**全库所有命名空间**，所以保守问答首页能直接命中你写入的内容；全部 `try/except` 包裹、异常打印即可，**绝不让索引失败阻断主业务流程**。这是「〔调 AI〕」里唯一允许 import `ai_center.internal` 的场景。

- **〔CSRF 防护〕** 项目已全局启用同源校验：所有 `POST`/`PUT`/`DELETE` 必须带 `X-Requested-With: XMLHttpRequest` 请求头，或同源 `Referer`，否则返回 `403 {"code":403,"msg":"CSRF校验失败：缺少请求头"}`。**每个状态变更请求都要在 `fetch` 头里加 `X-Requested-With`**（最小样例已带）；跨站/无头调用务必带同源 `Referer`。
- **〔数据库异常〕** `core.db_base` 的 `db_query` / `db_query_one` / `db_execute` / `db_execute_many` 出错会**抛异常**（已带 traceback，不再静默返回空列表/0）。路由内务必配 `@module_exception_guard("模块id")` 兜底；在定时任务、独立脚本、蓝图 `before_request` 等**非请求上下文**调用时，必须自行 `try/except`，否则异常会直接冒泡。
- **〔日志〕** 调试与报错用 `from core.logger import get_logger; logger = get_logger(__name__)`，异常用 `logger.exception(...)`；日志写 `logs/app.log` 并带堆栈、按 5MB×3 滚动。不要再用裸 `print`（不进日志文件、无级别）。
- **〔安全：XSS〕** 用户生成内容（经验帖/评论/笔记正文等）**禁止**用 `|safe` 渲染；用 `|linebreaks` 或前端 `white-space:pre-wrap` + `textContent`。本项目经验帖曾因 `|safe` 导致存储型 XSS。
- **〔安全：上传〕** 上传文件落盘必须 `os.path.basename(filename)` + 重命名（如 uuid）后保存，**绝不用原始 `filename` 直接拼路径**（含 `../../` 会路径穿越写任意文件）。参考 `office_tools` 模块的上传落盘做法。

---

## 3.1 AI 能力边界与配置 API（补充，模块编写者必读）

本项目的 AI 不是开关一开就全开，而是**两套独立的概念**，别混：

1. **系统模式 `ai_mode`：`off` / `light` / `full`** —— 决定"哪些 AI 功能可用"：
   - `off`：AI 整体关闭，前端隐藏 AI UI（首页显示"AI 已关闭"）。
   - `light`：仅摘要/标签/检索（**无**深度问答）。
   - `full`：问答 + 深度分析 + 向量检索。
   - 业务代码调用前务必用 `is_ai_enabled()` / `get_ai_mode()` 判断；例如 `ask_book_question` 在 `off`/`light` 下会直接返回失败。
2. **思维模式 `chat_thinking_mode`：`divergent`（发散）/ `conservative`（保守）** —— 只影响"回答方式"，不控制功能可用性：
   - 发散：模型用自己的知识自由回答。
   - 保守：先检索已索引资料，仅依据资料回答（无资料时提示先建索引或切发散）。
   - 配置位置：`config/modules.yaml` 的 `ai_center.chat_thinking_mode`；首页问答界面也能每次点选「🌀发散 / 📚保守」临时切换（作为 `mode` 参数下传，`global_ai_chat(question, mode=...)`）。

**完整 AI 接口清单（core.ai_interface，业务模块统一调用）：**

```python
from core.ai_interface import (
    get_ai_mode,          # -> "off"/"light"/"full"
    is_ai_enabled,        # -> bool
    get_ai_features,      # -> {"mode","summary","vector_search","qa","rebuild"}
    get_available_backends,
    generate_book_summary(book_id, book_text, force=False),  # light/full
    ask_book_question(book_id, question, top_k=5),           # full only
    global_ai_chat(question, history=[], mode=None),         # 首页问答
    invalidate_vectors_for_book(book_id),
    trigger_rebuild_for_book(book_id),                       # 异步重建索引
    get_vector_stats,
    ensure_ai_scheduler,                                    # light/full 启动后台调度
)
```

**AI 配置读写（core.ai_config_store，热切换，写回 modules.yaml 并刷新缓存）：**

```python
from core.ai_config_store import (
    read_ai_config, set_ai_mode, set_ai_backend,
    set_ai_model_paths, set_security_lock,
)
set_ai_mode("full")                       # off/light/full，立即生效
set_ai_backend("dashscope", {"url":..., "model":"qwen-plus", "api_key":"sk-xxx"})  # off/llama_cpp/ollama/dashscope/transformers
set_ai_model_paths({"light_emb":"models/bge-small-zh-v1.5", "full_emb":"models/bge-large-zh-v1.5", "local_llm":"models/xxx.gguf"})
set_security_lock(True)                   # 涉密安全锁：禁止 AI 读取图书正文
```

**推理后端（可热切换，无需重启）：** `llama_cpp`（本地 GGUF，默认）/ `ollama`（本地 HTTP 服务）/ `dashscope`（阿里云百炼云端 API）/ `transformers`（HuggingFace 原生）。切换后**首次推理**才真正加载新模型。注意：依赖包不同（llama_cpp 需 `llama-cpp-python`；ollama/dashscope 需 `requests`+`openai`；transformers 需 `torch`+`sentence_transformers`+`faiss`），UI 的依赖探测会提示缺哪些。

---

## 4. 核心 API 速查（直接抄）

```python
# 装饰器（core.auth）
from core.auth import login_required, permission_required, need_login_response, module_enabled_required

# 统一返回（core.response）
from core.response import ok, fail, paginate
return ok(data={...})                 # {code:200,msg,data}
return fail("错误信息", code=400)       # {code:400,msg,data:null}

# 异常兜底（core.exceptions）—— 每个路由都加
from core.exceptions import module_exception_guard
@bp.route("/")
@module_exception_guard("your_module")
def index(): ...

# 数据库（core.db_base）—— 先 Grep 确认签名
from core.db_base import get_all_books, get_book_by_id, add_log, user_can_view_book, user_can_access_module

# 配置（core.config_loader）
from core.config_loader import get_config, is_module_enabled
val = get_config("modules.your_module.enabled", False)

# AI（core.ai_interface）—— 函数名以实际代码为准，勿用旧名
from core.ai_interface import (
    global_ai_chat, ask_book_question, generate_book_summary,
    is_ai_enabled, get_ai_mode, get_ai_features, trigger_rebuild_for_book,
)
ok_, result = global_ai_chat("问题", history=[], mode="divergent")  # mode: divergent/conservative（这是"思维模式"，非系统开关）
# 单书问答（需 ai_mode=full 且该书已建索引）
ok_, res = ask_book_question(book_id, "这本书讲了什么？", top_k=5)
# 图书摘要（light/full 模式）
ok_, summary = generate_book_summary(book_id, book_text, force=False)

# 通用工具（common.utils）
from common.utils import get_client_ip, now_str, resolve_file_path, allowed_file
```

---

## 5. 最小可运行新模块样例（复制改名字即可）

**`modules/demo/__init__.py`**
```python
from flask import Blueprint
import os
bp = Blueprint("demo", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"))
# 注意：模块的 CSS/JS 不要靠蓝图 static_url_path（本 Flask 3.x 下会 404），
# 直接放全局 static/demo/，模板用 url_for('static', filename='demo/style.css') 引用。

module_info = {
    "module_id": "demo",
    "display_name": "示例模块",
    "icon": "🧩",
    "route_prefix": "/demo",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "演示模块",
    "version": "1.0.0",
    "blueprint": bp,
}
config_entries = []
PERMISSIONS = []
ai_features = []
from . import routes   # noqa: E402
```

**`modules/demo/routes.py`**
```python
from flask import render_template, request
from modules.demo import bp
from core.auth import login_required
from core.response import ok, fail
from core.exceptions import module_exception_guard

@bp.route("/")
@module_exception_guard("demo")
def index():
    return render_template("demo/index.html", current_module_id="demo")

@bp.route("/api/hello", methods=["POST"])
@module_exception_guard("demo")
@login_required
def api_hello():
    q = (request.get_json(silent=True) or {}).get("name", "")
    return ok(data={"msg": f"你好, {q or '匿名'}"})
```

**`modules/demo/templates/demo/index.html`**
```html
{% extends "layout_base.html" %}
{% block page_title %}示例模块{% endblock %}
{% block content %}
  <h2>🧩 示例模块</h2>
  <button id="btn">打招呼</button>
  <pre id="out"></pre>
{% endblock %}
{% block extra_js %}
<script>
document.getElementById("btn").onclick = async () => {
  const r = await fetch("{{ url_for('demo.api_hello') }}", {
    method:"POST",
    headers:{"Content-Type":"application/json","X-Requested-With":"XMLHttpRequest"},
    body: JSON.stringify({name:"世界"})
  });
  const j = await r.json();
  document.getElementById("out").textContent = JSON.stringify(j.data);
};
</script>
{% endblock %}
```

**`config/modules.yaml` 追加**（必须放在顶层 `modules:` 之下、与现有模块同级缩进；直接写顶层 `demo:` 扫描器读不到，模块不会注册）
```yaml
modules:
  demo:
    enabled: true
    sort_order: 50
```

> 重启服务后，侧边栏出现「🧩 示例模块」（管理员一定可见；若要让普通用户也看到，需把它加入 `core/plugin_scan.py` 的 `DEFAULT_PUBLIC_MODULES` 白名单）。若模块需要 CSS/JS，放全局 `static/demo/` 并用 `url_for('static', filename='demo/xxx')` 引用，勿用蓝图 `static_url_path`。

---

## 6. 在「已有模块」里加功能（而非新建）

- 找到目标模块的 `modules/<m>/routes.py`，照葫芦加 `@bp.route(...)` + `@module_exception_guard("<m>")` + 权限装饰器。
- 模板放 `modules/<m>/templates/<m>/`，在对应页面 `{% block extra_js %}` 里写交互。
- 若该功能是「新权限点」，在 `modules/<m>/__init__.py` 的 `PERMISSIONS` 里加 `("key","名称")`，框架会在用户管理页出现勾选框；**但必须同时在对应路由加 `@permission_required("key")` 才会真正拦截**。只声明不挂装饰器 = 勾选框是摆设（无实际效果）；只挂装饰器不声明 = 路由会拦截但后台无勾选项可分配。两者配套才有意义。
- 若想进「配置中心」分组，加 `config_entries = [{"name":..., "desc":..., "url":...}]`。
- **绝不删除已有功能、绝不改 `app.py` 路由注册机制。**

---

## 7. 交付前自检清单（务必逐条确认）

- [ ] 新模块在 `config/modules.yaml` 已 `enabled: true` 且 `sort_order` 合理
- [ ] `__init__.py` 有 `bp` + `module_info`，末尾 `from . import routes`
- [ ] `__init__.py`/`routes.py` **顶层无** `current_app`/`g`/`db` 访问（否则插件扫描 import 阶段直接失败，启动日志报 `Working outside of application context`）
- [ ] 所有路由都带 `@module_exception_guard("模块id")`
- [ ] 需要登录/权限的路由分别带 `@login_required` / `@permission_required`
- [ ] 所有 JSON 接口用 `ok()`/`fail()` 返回 `{code,msg,data}`
- [ ] 未登录分支用 `need_login_response`（无 `redirect(...login...)`）
- [ ] 数据库操作全部经 `core.db_base`（无裸 SQL）
- [ ] 调 AI 只经 `core.ai_interface`
- [ ] 模板 `extends "layout_base.html"`，文字显式设色且非纯黑
- [ ] 内容区主色随**黑夜**展厅灯光主题切换（桥接限定 `body.dark`，规则 `body.dark .mymod-page { --primary: var(--theme-primary); }`）；**白天模式保持原样**（实测确认 `body.dark` 未生效时仍是模块原写死主色，无 `body[data-light-theme]` 误伤）
- [ ] 含 canvas 图表的模块：系列色调色板仅 `dark` 跟随 `--theme-primary`，白天用原写死色板，且切换灯光时重绘（无残留旧色）
- [ ] 改动 `routes.py`/`core` 后**已重启服务**（旧进程已清除，5005 仅一个监听）
- [ ] 用 Playwright/浏览器实测：页面渲染、菜单出现、接口返回、控制台无报错
- [ ] 未删除任何既有功能

---

## 8. 一句话给外部 AI 的「系统提示」

> 你正在维护一个 Flask 插件化图书管理系统（根 `D:\xianmufile`）。新增功能=在 `modules/` 加文件夹并声明 `__init__.py` 的 `module_info`+`bp`，在 `config/modules.yaml` 启用，模板 `extends layout_base.html`，接口用 `core.response.ok/fail` 返回 `{code,msg,data}`，登录字段是 `account`，未登录用 `need_login_response` 提示不跳转，数据库经 `core.db_base`，AI 经 `core.ai_interface`。改完 routes/core 要手动重启（清旧进程防 5005 双监听）。不要改 `app.py`、不要删功能。先读相关文件再动手。
