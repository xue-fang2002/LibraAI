"""文件文本抽取（纯 Python + 可选第三方库，优雅降级）。

PDF 采用两级策略：
  1) 先用 pypdf/PyPDF2 抽取**文本层**（快，几乎无成本）；
  2) 若文本层过短或只有 PDF 元数据（扫描件的典型特征），再用 OCR 兜底。

OCR 很慢（150 DPI 下约 10 秒/页），因此：
  - 只在文本层不可用时才触发；
  - 结果按 file_hash 缓存，同一个文件绝不识别第二次；
  - 默认限制最大页数，避免整本书拖垮重建任务。
"""
import os
import re

# 少于该字符数即认为文本层不可用（疑似扫描件）
_MIN_TEXT_CHARS = 500
# PDF 元数据的典型标记（扫出来的"正文"其实只是这些）
_META_MARKERS = ("[General Information]", "SS号=", "DX号=", "bookDetail.jsp", "URL=http")
# 页数达到该值时，启用「平均每页文本量」判断
_MIN_PAGES_FOR_AVG = 5
# 平均每页文本少于该值，认为多数页面没有文本层（扫描件）
_MIN_CHARS_PER_PAGE = 150


# 页码标记：抽取时按页插入，分块后据此定位页码，入库前再剥掉，
# 既保留位置信息又不污染正文与向量。
_PAGE_MARK = "<<<PAGE:%d>>>"
_PAGE_MARK_RE = re.compile(r"<<<PAGE:(\d+)>>>")

# 条款号识别（按优先级）：「第X条/第X章」→「5.2.1」这类数字编号。
# 不收文档编号（如 Q/IWIPHSE-R-2023-023）：它出现在每页页眉，
# 作为条款号展示会误导，且几乎所有块都会被匹配成同一个值。
_SECTION_PATTERNS = (
    re.compile(r"第\s*[0-9一二三四五六七八九十百]+\s*[条章节]"),
    re.compile(r"(?<![\d.])\d{1,2}(?:\.\d{1,3}){1,3}(?![\d.])"),
)

# 受控文件水印（每页页眉/页脚都会出现，会污染标题与正文开头），抽取标题前先剔除整行
_WATERMARK_RES = (
    re.compile(r"^\s*This document is under control[^\n]*$", re.M),
    re.compile(r"^\s*unauthorized disclosure[^\n]*$", re.M),
    re.compile(r"^\s*此文件为印尼纬达贝工业园区受控文件[^\n]*$", re.M),
    re.compile(r"^\s*Sistem Manajemen[\u4e00-\u9fa5A-Za-z\s]*$", re.M),
)
# 章节/标题识别（按优先级）。目标是把「第X章 / 1.4 保障工作质量 / 车间电工岗位职责 /
# 印尼籍安环员岗位职责」这类层级标题抽出来，供问答时按目录结构组织答案。
_HEADING_PATTERNS = (
    # 编号章节：1.4 / 2.3.1 后跟中文（可夹印尼语，取首个中文片段）
    re.compile(r"(?:^|\n)\s*(?:\d+(?:\.\d+){1,3})\s*([\u4e00-\u9fa5][\u4e00-\u9fa5\w（()）]{1,28})"),
    # 中文「第X章/第X节」+ 标题
    re.compile(r"第\s*[0-9一二三四五六七八九十百零]+\s*[章节点节篇]\s*([\u4e00-\u9fa5][\u4e00-\u9fa5\w（）()]{1,24})"),
    # 岗位职责 / 管理制度 类标题
    re.compile(r"([\u4e00-\u9fa5]{2,18}(?:岗位职责|职责|管理制度|管理规定|管理办法|操作规程|管理规范|实施细则|工作方案))"),
    # 目录条目里的「中文岗位名」：如「4. Tanggung ... 车间电工岗位职责 …21」
    re.compile(r"([\u4e00-\u9fa5]{2,16}岗位职责)"),
    # 短标题 + 冒号（如「保障工作质量：」）
    re.compile(r"(?:^|\n)\s*([\u4e00-\u9fa5]{2,10})[：:]\s*[\u4e00-\u9fa5]"),
)


def strip_watermark(text):
    """剔除受控文件水印整行，避免污染标题与来源预览。"""
    if not text:
        return text or ""
    try:
        for rx in _WATERMARK_RES:
            text = rx.sub("", text)
        return text.strip()
    except Exception:
        return text


# 标题候选拒收：以动词/介词开头的句子片段（"执行本岗位操作规程"等）不当标题。
_HEADING_DROP_HEAD = ("确保", "拟定", "负责", "组织", "开展", "落实", "协助", "参与",
                      "配合", "建立", "制定", "完善", "监督", "检查", "执行", "完成",
                      "做好", "加强", "推进", "统筹", "协调", "根据", "按照", "通过",
                      "对", "为", "在", "是", "由", "将", "把", "使", "让", "需", "应", "要")


def extract_heading(text):
    """从分块文本抽取章节/条目标题（层级结构的一环）。

    返回最可能代表该块所属章节的标题字符串；抽不到返回空串。
    仅做轻量正则，不依赖重新 OCR/嵌入，可离线给已有分块补元数据。

    - 改为逐行扫描，标题须呈「行级形态」，避免把正文句子当标题；
    - 所有模式以行首 ^ 锚定，配合中文词边界，杜绝从词中部截断
      （如把"岗位安全管理制度"截成"位安全管理制度"）；
    - 拒收以动词/介词开头的句子片段。
    """
    if not text:
        return ""
    body = strip_watermark(text)
    if not body:
        return ""
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln or len(ln) > 40:
            continue
        # 明显正文整句（含句末标点或叙述连词）：仅「短标题 + 冒号」例外
        if any(p in ln for p in ("。", "；", "！", "？", "，")):
            if not (len(ln) <= 12 and ln.endswith(("：", ":"))):
                continue
        cand = _heading_from_line(ln)
        if cand:
            return cand
    return ""


def _heading_from_line(ln):
    """判断一行是否像标题并返回其标题文本（已去编号/标点残留）。行级锚定，不截断。"""
    if not ln:
        return ""
    # 1) 编号章节：1.4 / 2.3.1 后跟中文标题（取首个中文片段）
    m = re.match(r"^(?:\d+(?:\.\d+){1,3})[．.\u3001\s]+([\u4e00-\u9fa5][\u4e00-\u9fa5\w\uff08\uff09\uff08\uff09]{1,28})", ln)
    if m:
        return _clean_heading_text(m.group(1))
    # 2) 第X章/节/条/篇 + 标题
    m = re.match(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节点篇条]\s*([\u4e00-\u9fa5][\u4e00-\u9fa5\w\uff08\uff09]{1,24})", ln)
    if m:
        return _clean_heading_text(m.group(1))
    # 3) 括号编号：（一）/ (1) + 标题
    m = re.match(r"^[（(][一二三四五六七八九十\d]+[）)]\s*([\u4e00-\u9fa5][\u4e00-\u9fa5\w\uff08\uff09]{1,24})", ln)
    if m:
        return _clean_heading_text(m.group(1))
    # 4) 岗位职责 / 管理制度 类标题（行级锚定；不以动词开头才收）
    m = re.match(r"^([\u4e00-\u9fa5]{2,18}(?:岗位职责|职责|管理制度|管理规定|管理办法|操作规程|管理规范|实施细则|工作方案))", ln)
    if m:
        cand = m.group(1)
        if cand[:2] in _HEADING_DROP_HEAD:
            return ""
        return _clean_heading_text(cand)
    # 5) 短标题 + 冒号（如"保障工作质量："）
    m = re.match(r"^([\u4e00-\u9fa5]{2,10})[：:]\s*[\u4e00-\u9fa5]", ln)
    if m:
        return _clean_heading_text(m.group(1))
    return ""


def _clean_heading_text(s):
    s = (s or "").strip()
    # 去掉可能夹带的尾随标点/括号残留
    s = re.sub(r"[\uff08(][^）)]*$", "", s).strip()
    return s


def strip_page_marks(text):
    """去掉页码标记，返回干净正文（入库与向量化前调用）。"""
    if not text:
        return text or ""
    try:
        return _PAGE_MARK_RE.sub("", text).strip()
    except Exception:
        return text





# ---------------------------------------------------------------------------
# 段落边界检测
#
# 问题：pypdf/PyPDF2 抽取 PDF 文本时，行间只有单 \n，没有空行。
# chunker（re.split(r"\n\s*\n", text)）依赖空行识别段落边界——
# 没有空行，整本文档被压成 1 个 chunk，embedding 混合所有主题，
# 向量检索时被多 chunk 文档的封面页稀释 → 召回不到。
#
# 修复：抽取后自动检测标题/章节边界，在边界处插入空行。
# 所有已有 + 未来的文档都受益，无需 per-book 配置。
# ---------------------------------------------------------------------------

# 标题行检测：行首匹配以下任一模式即判定为结构边界。
# 1) 中文序号：一、二、三、（含 十一、二十等）
# 2) 第X章/节/条/篇
# 3) 阿拉伯数字编号：1. / 1.1 / 2.3.1 / 1．（全角/半角句号均可）
# 4) 括号编号：（一）/ (1) / （二）
_HEADING_LINE_RES = (
    re.compile(r"^[一二三四五六七八九十]{1,3}[、，]"),
    re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节点篇条]"),
    re.compile(r"^\d{1,2}(?:\.\d{1,3}){0,3}[．.、\s]+[\u4e00-\u9fff]"),
    re.compile(r"^[（(][一二三四五六七八九十\d]+[）)]"),
)


def _is_heading_line(line):
    """判断一行是否像结构标题（段落边界起始）。"""
    s = (line or "").strip()
    if not s or len(s) > 80:
        return False
    for rx in _HEADING_LINE_RES:
        if rx.match(s):
            return True
    return False


def _insert_paragraph_breaks(text):
    """在结构边界（标题行、页码标记）前插入空行。

    PDF 抽取的文本行间只有单 \\n，chunker 按 \\n\\s*\\n（空行）分段，
    没有空行时整本被压成 1 个 chunk。此函数检测标题行和页码标记，
    在它们前面插入空行，让 chunker 天然切成多块。

    所有文件类型（PDF/DOCX）在 extract_text_from_file 返回前调用。
    xlsx/pptx 已在各自抽取函数内手动插入空行，无需重复调用。
    """
    if not text:
        return text
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        # 页码标记：前后都加空行
        if _PAGE_MARK_RE.match(stripped):
            if result and result[-1].strip():
                result.append("")
            result.append(line)
            result.append("")
            continue
        # 标题行：前面加空行
        if _is_heading_line(stripped):
            if result and result[-1].strip():
                result.append("")
            result.append(line)
            continue
        result.append(line)
    return "\n".join(result)


def extract_page(text):
    """从分块文本取页码；取不到返回 None（不抛异常）。"""
    if not text:
        return None
    try:
        m = _PAGE_MARK_RE.search(text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def extract_section(text):
    """从分块文本取条款号；取不到返回 None（不抛异常）。"""
    if not text:
        return None
    try:
        head = text.strip()[:150]
        for pat in _SECTION_PATTERNS:
            m = pat.search(head)
            if m:
                s = (m.group(0) or "").strip()
                if s:
                    return s
    except Exception:
        pass
    return None


def _get_ocr_max_pages():
    """OCR 最大页数，可在 modules.yaml 的 ai_center.ocr_max_pages 配置，默认 20。"""
    try:
        from core.config_loader import get_config
        v = get_config("modules.ai_center.ocr_max_pages")
        if v:
            return max(1, int(v))
    except Exception:
        pass
    return 20


def _looks_scanned(text, page_count=0):
    """判断文本层是否可用（即可否跳过 OCR）。命中任一条件即视为扫描件：

    1) 全文过少；
    2) 只剩 PDF 元数据（书名/SS号 等），正文根本没抽出来；
    3) 页数不少但平均每页文本极少 —— 说明大量页面没有文本层。
       这条能覆盖「没有元数据标记」的纯扫描件。
    """
    t = (text or "").strip()
    if len(t) < _MIN_TEXT_CHARS:
        return True
    if page_count >= _MIN_PAGES_FOR_AVG:
        try:
            if len(t) / float(page_count) < _MIN_CHARS_PER_PAGE:
                return True
        except Exception:
            pass
    if not any(m in t for m in _META_MARKERS):
        return False
    # 去掉元数据块后如果所剩无几，说明正文没有被抽出来
    body = re.sub(r"\[General Information\][\s\S]{0,500}", "", t)
    return len(body.strip()) < _MIN_TEXT_CHARS


def _ocr_pdf(path, max_pages=None):
    """用 OCR 识别 PDF（渲染页面为图片后逐页识别）。返回 (文本, 已识别页数, 引擎名)。"""
    try:
        from . import deps
    except Exception:
        return "", 0, None

    engine, engine_type = deps.get_ocr_engine()
    fitz = deps.get_fitz()
    if not engine or not fitz:
        return "", 0, engine_type
    if max_pages is None:
        max_pages = _get_ocr_max_pages()
    # max_pages <= 0 表示不限页数（整本全扫，精确档使用）
    limit = max_pages if (max_pages and max_pages > 0) else 0

    try:
        doc = fitz.open(path)
    except Exception as e:
        print(f"⚠️ [OCR] 打开 PDF 失败: {e}")
        return "", 0, engine_type

    texts = []
    done = 0
    try:
        for i, page in enumerate(doc):
            if limit and i >= limit:
                break
            try:
                pix = page.get_pixmap(dpi=150)
                result, _ = engine(pix.tobytes("png"))
            except Exception as e:
                print(f"⚠️ [OCR] 第 {i + 1} 页识别失败: {e}")
                continue
            if result:
                seg = "\n".join(r[1] for r in result if len(r) > 1)
                if seg.strip():
                    # 同样插入页码标记（OCR 的是第 i+1 页）
                    texts.append(_PAGE_MARK % (i + 1) + "\n" + seg)
                    done += 1
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return "\n".join(texts), done, engine_type


def _finalize_pdf_text(text):
    """PDF 文本后处理：去水印 + 插入段落边界空行。

    在 _extract_pdf 的每个返回点调用，确保所有路径（pypdf / OCR / 缓存）
    产出的文本都经过统一后处理。
    """
    if not text:
        return text
    text = strip_watermark(text)
    return _insert_paragraph_breaks(text)


def _ocr_cache_key(file_hash, max_pages=None):
    """OCR 缓存键 = 文件哈希 + 扫描范围。

此前缓存只按 file_hash 存取，**不区分扫描范围**。
    公共/一般档只扫前若干页（默认 12 页），这 12 页的结果会按哈希缓存下来；
    之后同一本书调成「精确档」（整本全扫）重建时，会先命中这份 12 页缓存直接返回，
    「整本全扫」永远不会发生 —— 精确档形同虚设。
    因此把扫描范围并入缓存键：不同档位各自缓存、互不覆盖，也互不污染。

    注：不改动 ocr_cache 表结构与 get_ocr_cache/save_ocr_cache 的签名，
    仅把「键」变成复合串，历史遗留的纯哈希条目自然失效（不会被误命中）。
    """
    if not file_hash:
        return None
    # max_pages 为 None/0/负数 均视为整本全扫
    scope = "full" if not (max_pages and max_pages > 0) else "p%d" % int(max_pages)
    return "%s|%s" % (file_hash, scope)


def _extract_pdf(path, file_hash=None, use_ocr=True, max_pages=None):
    """抽取 PDF 文本：文本层优先，不足则 OCR 兜底（结果按 file_hash + 扫描范围 缓存）。

    max_pages: OCR 页数上限，0 或 None 处理为「不限/默认」。
               由调用方按文档档位传入（公共档少量页，精确档整本）。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            PdfReader = None

    text = ""
    page_count = 0
    if PdfReader is not None:
        try:
            reader = PdfReader(path)
            # 按页拼接并插入页码标记，供后续定位来源
            parts = []
            for i, p in enumerate(reader.pages):
                parts.append(_PAGE_MARK % (i + 1) + "\n" + (p.extract_text() or ""))
            text = "\n".join(parts)
            page_count = len(reader.pages)
        except Exception:
            text = ""

    # 判定是否扫描件要用「去掉页码标记」的正文，否则标记会虚增字符数
    if not use_ocr or not _looks_scanned(strip_page_marks(text), page_count=page_count):
        return _finalize_pdf_text(text)

    scope = "整本全扫" if not (max_pages and max_pages > 0) else f"最多 {max_pages} 页"
    # 疑似扫描件 → OCR；先看缓存（缓存键含扫描范围，见 _ocr_cache_key）
    cache_key = _ocr_cache_key(file_hash, max_pages)
    if cache_key:
        try:
            from core.db_base import get_ocr_cache
            cached = get_ocr_cache(cache_key)
            if cached:
                print(f"♻️ [OCR] 命中缓存，跳过识别（{scope}，hash={str(file_hash)[:12]}）")
                return _finalize_pdf_text(cached)
        except Exception:
            pass

    print(f"🔎 [OCR] 文本层不可用，开始识别扫描件（{scope}）…")
    ocr_text, pages, engine_type = _ocr_pdf(path, max_pages=max_pages)
    if pages:
        print(f"✅ [OCR] 完成 {pages} 页，得到 {len(ocr_text)} 字符（引擎: {engine_type}）")
    if len(ocr_text) > len(text):
        if cache_key:
            try:
                from core.db_base import save_ocr_cache
                save_ocr_cache(cache_key, ocr_text, pages=pages, engine=engine_type)
            except Exception:
                pass
        return _finalize_pdf_text(ocr_text)
    return _finalize_pdf_text(text)


# 合并标题行识别（P1 列名来源修复）：read_only 模式下合并单元格仅左上角有值，
# 塌缩成 1 个单元格；真实列名行通常有 >=2 列。故仅当整行恰 1 个单元格且内容像标题时判为标题行。
_XLSX_TITLE_RE = re.compile(r"[（(].*[)）]|年\s*.*月|报表$|总表$|统计表$|名单$|名册$|汇总$")

# 明细表（实体表）识别（P2 行级原子切片）：表头含任一「实体列」关键词即判为明细表，
# 其数据行逐行切成独立 chunk（列名内联进每个单元格），用于「姓名+工号/职务」等
# 点查类问题精准命中。汇总/报表类（纯数值、无实体列）仍走原 tab 行行为，不回归。
_XLSX_ENTITY_KEYWORDS = (
    "姓名", "工号", "身份证", "手机", "岗位", "职务", "员工", "学号",
    "地址", "邮箱", "职级", "学历", "编号", "卡号",
)


def _xlsx_row_looks_like_title(cells):
    """判断一行是否「合并标题行」而非真实列名行。

    返回 True 当且仅当：整行只有 1 个单元格（合并单元格在 read_only 下塌缩），
    且内容像标题（含括号 / 年月 / 表·名单·名册·汇总 等后缀）。"""
    if len(cells) != 1:
        return False
    return bool(_XLSX_TITLE_RE.search(cells[0]))


def _xlsx_is_detail_table(header_cells):
    """判定工作表是否为「明细/实体表」：表头含任一实体列关键词即判为明细表。"""
    if not header_cells:
        return False
    h = " ".join(header_cells)
    return any(k in h for k in _XLSX_ENTITY_KEYWORDS)


def _extract_xlsx(path):
    """抽取 Excel(.xlsx/.xlsm) 文本：逐工作表、逐行拼接，表头带【工作表：名】标记，
    并以 <<<PAGE:N>>> 记录工作表序号（N 为 1 起的工作表序号，便于来源定位）。

    关键：行与行之间用「空行」分隔，使下游分块器把每一行切成独立子块/可识别边界，
    避免整张表被压成一整段导致向量被稀释、检索命中率骤降。

    P1 列名来源修复：
    - 扫描工作表首行区域，跳过「合并标题行」（read_only 下塌缩成 1 单元格且像标题），
      取第一个 >=2 列、非标题式的行作为**真实表头**，在【工作表：名】后输出一行
      `【表头：列1|列2|...】` 标记（列名用 `|` 连接）。
    - 表头行本身转为元数据（不再当数据行重复输出）。
    - 下游 chunker._inject_table_header 读取该标记作 cur_header（并丢弃标记行），
      使每个数据块都携带正确列名，合并标题不再污染「首行即表头」的盲猜。
    缺 openpyxl 或解析失败时返回空串（不抛异常）。"""
    try:
        import openpyxl
    except ImportError:
        return ""
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return ""
    out = []
    try:
        for idx, ws in enumerate(wb.worksheets, start=1):
            out.append(_PAGE_MARK % idx)
            out.append("")  # 空行：页码标记与表标记分段（P0 让【工作表：…】独立成段触发强制分段）
            out.append("【工作表：%s】" % (ws.title or ("Sheet%d" % idx)))
            out.append("")  # 空行：与后续内容分段
            # ---- P1：确定真实表头行（跳过合并标题行）----
            # rows      = 非空单元格视图（原行为，汇总表沿用）
            # full_rows = 全列视图（保留中间空占位、仅去尾部空列）——明细表按列对齐必需：
            #   原实现丢弃所有空单元格，数据行一旦有空单元格就会整体左移，导致
            #   「列名：值」按索引配对时错位（实测：孙天伟行把「运维部」配到转正时间）。
            rows = []
            full_rows = []
            for row in ws.iter_rows(values_only=True):
                vals = [("" if v is None else str(v).strip()) for v in row]
                while vals and vals[-1] == "":
                    vals.pop()  # 仅去尾部空列，保留中间空列以维持列对齐
                full_rows.append(vals)
                rows.append([v for v in vals if v != ""])
            header_idx = None
            header_cells = None   # 非空表头（用于【表头：】标记）
            header_full = None    # 全列表头（用于明细行按列对齐取列名）
            for i, cells in enumerate(rows):
                if len(cells) >= 2 and not _xlsx_row_looks_like_title(cells):
                    header_idx = i
                    header_cells = cells
                    header_full = full_rows[i]
                    break
            if header_cells:
                # 表头单元格可能含换行（如「入职（青山）\n时间」），若不压平会让
                # 【表头：…】标记跨行 → chunker 正则匹配失败 → 回退把带【表头：
                # 的首行当表头 → 出现「表头：【表头：…」双前缀。故此处压平换行。
                hdr = "|".join(c.replace("\r", " ").replace("\n", " ").strip()
                               for c in header_cells)
                out.append("【表头：%s】" % hdr)
                out.append("")  # 空行：表头标记与数据分段
            # ---- P2 行级原子切片：明细表 vs 汇总表分流 ----
            # 明细表（表头含实体列）：每行 = 1 个原子 chunk，列名内联进每个单元格
            # （方法1：结构化行切片）。格式 `列名：值 | 列名：值`，前缀 `【行】` 告知
            # chunker 该段为原子行、不与其他行合并，根治「单人记录埋进 1200 字混排块
            # 被向量稀释」导致的人名/工号类检索失效。
            # 汇总/报表表（无实体列）：维持原 tab 行行为（后续可接方法2 markdown 块）。
            detail = _xlsx_is_detail_table(header_cells)
            # ---- 输出数据行：跳过表头行本身（已转为【表头：】元数据），其余行照常输出 ----
            # 表头之前的标题行（如合并标题「电气运维部花名册（08月）」）作为普通内容保留，
            # 不污染表头判定，同时保留可追溯的上下文。
            for i, cells in enumerate(rows):
                if not cells:
                    continue
                if i == header_idx:
                    continue  # 表头行不重复输出
                if detail:
                    if len(cells) >= 2:
                        # 明细表数据行：逐行 = 1 个原子 chunk，列名内联进每个单元格
                        # （方法1：结构化行切片）。格式 `列名：值 | 列名：值`，前缀
                        # `【行】` 告知 chunker 该段为原子行、不与其他行合并。
                        # 按**真实列索引**配对（full_rows / header_full），跳过空值，
                        # 保证「有空单元格的行」列名不错位。
                        parts = []
                        fr = full_rows[i]
                        for j, v in enumerate(fr):
                            if not v:
                                continue
                            if header_full and j < len(header_full) and header_full[j]:
                                col = header_full[j]
                            else:
                                col = "列%d" % (j + 1)
                            col = col.replace("\r", " ").replace("\n", " ").strip()
                            parts.append("%s：%s" % (col, v))
                        out.append("【行】" + " | ".join(parts))
                    else:
                        # 合并标题行 / 小节标题等（read_only 下塌成 1 单元格）当普通内容，
                        # 不输出为原子行，避免把标题当成人名记录污染检索。
                        out.append("\t".join(cells))
                else:
                    out.append("\t".join(cells))
                out.append("")  # 空行：每行独立成块
    except Exception:
        return ""
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return "\n".join(out)


def _extract_pptx(path):
    """抽取 PowerPoint(.pptx) 文本：逐幻灯片提取文本框与表格内容，带【幻灯片：N】标记，
    并以 <<<PAGE:N>>> 记录幻灯片序号（N 为 1 起的幻灯片号，便于来源定位）。

    关键：幻灯片之间、页内文本块之间用「空行」分隔，使下游分块器把每页/每段切成独立子块。
    缺 python-pptx 或解析失败时返回空串（不抛异常）。"""
    try:
        from pptx import Presentation
    except ImportError:
        return ""
    try:
        prs = Presentation(path)
    except Exception:
        return ""
    out = []
    try:
        for idx, slide in enumerate(prs.slides, start=1):
            out.append(_PAGE_MARK % idx)
            out.append("")  # 空行：页码标记与幻灯片标记分段（P0 让【幻灯片：N】独立成段触发强制分段）
            out.append("【幻灯片：%d】" % idx)
            out.append("")  # 空行：与内容分段
            for shape in slide.shapes:
                # 表格：逐行逐单元格
                if getattr(shape, "has_table", False):
                    tbl = shape.table
                    for r in tbl.rows:
                        cells = [c.text.strip() for c in r.cells if c.text and c.text.strip()]
                        if cells:
                            out.append("\t".join(cells))
                            out.append("")  # 空行：表格行独立成块
                    continue
                # 文本框
                if getattr(shape, "has_text_frame", False):
                    txt = shape.text_frame.text
                    if txt and txt.strip():
                        out.append(txt.strip())
                        out.append("")  # 空行：文本块独立成块
                # 备注页（notesSlide）
            try:
                notes = slide.notes_slide
                if notes and notes.notes_text_frame and notes.notes_text_frame.text.strip():
                    out.append("【备注】" + notes.notes_text_frame.text.strip())
                    out.append("")
            except Exception:
                pass
            out.append("")  # 幻灯片之间空行
    except Exception:
        return ""
    return "\n".join(out)


def _iter_docx_blocks(doc):
    """按文档顺序产出 DOCX 的段落文本与表格行文本。

此前 extract_text_from_file 的 docx 分支只取 doc.paragraphs，
    表格单元格里的文字全部丢失 —— 而表单类制度文件（岗位表、职责矩阵、流程表）
    的核心内容恰恰写在表格里，导致这类文档索引后正文缺失、问答答非所问。

    处理约定：
      - 按 body 子元素顺序遍历，保持「段落/表格」在原文中的相对位置；
      - 表格按行输出，行内单元格用制表符连接（与 _extract_xlsx 的行式处理一致）；
      - 每张表后补一个空串，使表格与后续内容之间出现空行，
        避免整张表被压成一整段（向量被稀释、命中率骤降，见 _extract_xlsx 的说明）。

    健壮性：任一步失败（python-docx 版本差异、内部结构异常）都退化为
    「仅段落」列表 —— 即本次改动之前的行为，绝不因为表格解析失败而丢掉正文。
    """
    try:
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
    except Exception:
        return [p.text for p in doc.paragraphs]

    out = []
    tbl_no = 0
    try:
        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                out.append(Paragraph(child, doc).text)
            elif isinstance(child, CT_Tbl):
                tbl_no += 1
                #P1：输出【表格：N】标记，与 xlsx 的【工作表：名】一致，
                # 使下游 chunker 的 P0（表格边界强制分段）与 P1（首行作表头上下文）也能覆盖 docx 表格。
                out.append("")  # 空行：与前文分段，使【表格：N】独立成段（P0 触发）
                out.append("【表格：%d】" % tbl_no)
                out.append("")  # 空行：与表头分段
                tbl = Table(child, doc)
                for row in tbl.rows:
                    cells = []
                    for c in row.cells:
                        t = (c.text or "").strip().replace("\n", " ")
                        if t:
                            cells.append(t)
                    if cells:
                        out.append("\t".join(cells))
                        out.append("")  # 空行：表格行独立成块
                out.append("")   # 表后空行，便于下游分块
    except Exception:
        return [p.text for p in doc.paragraphs]
    return out


def extract_text_from_file(path, file_hash=None, use_ocr=True, max_pages=None):
    """
    从文件中抽取纯文本。
    支持：.txt（原生）、.pdf（pypdf / PyPDF2，扫描件自动 OCR 兜底）、
          .docx / .doc（python-docx）、.xlsx（openpyxl）、.pptx（python-pptx）。
    不支持的类型或缺少依赖时，尝试按 UTF-8 文本读取；全部失败则返回空字符串（不抛异常）。

    Args:
        file_hash: 图书的文件哈希；传入后启用 OCR 结果缓存。
        use_ocr:   PDF 文本层不可用时是否用 OCR 兜底。
        max_pages: OCR 页数上限（0/None = 不限或取默认）。由调用方按文档档位传入。
    """
    if not path or not os.path.exists(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".txt":
            #txt 原先直接 read 返回，不经过段落边界修复，
            # 单换行标题的 txt 切片效果明显差于 pdf/docx（整篇容易糊成一块）。
            # 这里与 docx 分支保持一致，统一过一遍 _insert_paragraph_breaks。
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return _insert_paragraph_breaks(f.read())
        if ext == ".pdf":
            return _extract_pdf(path, file_hash=file_hash, use_ocr=use_ocr,
                                max_pages=max_pages)
        if ext in (".docx", ".doc"):
            try:
                import docx
            except ImportError:
                return ""
            try:
                d = docx.Document(path)
                #原先只取 d.paragraphs，表格里的文字全部丢失。
                # 表单类制度文件的核心内容（岗位/职责/流程）常写在表格中。
                # 改为按文档顺序同时遍历段落与表格（见 _iter_docx_blocks），
                # 该函数在任一步失败时会退回「仅段落」，即改动前的行为。
                return _insert_paragraph_breaks(
                    "\n".join(p for p in _iter_docx_blocks(d) if p is not None)
                )
            except Exception:
                return ""
        if ext in (".xlsx", ".xlsm"):
            return _extract_xlsx(path)
        if ext == ".pptx":
            return _extract_pptx(path)
        # 其他类型：退化为按文本读取
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    except Exception:
        return ""
