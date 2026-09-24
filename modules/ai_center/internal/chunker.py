"""文本分块（纯 Python，无需第三方依赖）。"""

import re


# ---------------------------------------------------------------------------
# 表格类整行标记（parser 在每个工作表 / 表格 / 幻灯片前输出，见
# parser._extract_xlsx / _iter_docx_blocks / _extract_pptx）
# ---------------------------------------------------------------------------
# 用于「强制分段」：让工作表 / 表格 / 幻灯片边界 == 分块边界。
_TABLE_MARK_RE = re.compile(r"^【(?:工作表|表格|幻灯片)\s*[:：][^】]*】\s*$")
# 只用于「捕获表头」（P1）：幻灯片没有表头概念，故排除。
_HEADER_TABLE_MARK_RE = re.compile(r"^【(?:工作表|表格)\s*[:：][^】]*】\s*$")
# P1 列名来源修复：解析器显式输出的真实列名标记（如 `【表头：姓名|工号|部门】`）。
# 读取其内容作 cur_header，并丢弃该标记行本身（不进 chunk、不重复前置），
# 从而正确处理「首行是合并标题、第二行才是真实列名」的表。
_HEADER_LINE_RE = re.compile(r"^【表头\s*[:：]\s*(.+?)\s*】\s*$")
# P2 行级原子切片：解析器对明细表逐行输出的原子行标记（如 `【行】姓名：孙天伟 | 工号：…`）。
# chunker 遇到该段时将其作为独立 chunk（不与其他行合并），根治「单行埋进 1200 字混排块
# 被向量稀释」导致的人名/工号类检索失效。
_ROW_ATOMIC_RE = re.compile(r"^【行】\s*(.*)$", re.S)


# 主标题检测：段落以这些模式开头时，强制开启新块（即使总长未达 max_chars）。
# 仅检测「一、/二、/三、」「第X章/节/条」等主标题，不检测子项（1./2.）——
# 子项留在同一块内以保持语义完整，避免碎片化。
# 这解决了「短文档（<1200字）有明确章节结构但被压成 1 块」的问题：
# 1 个 embedding 混合所有主题 → 向量检索时被多块文档稀释 → 召回不到。
_HEADING_START_RES = (
    re.compile(r"^[一二三四五六七八九十]{1,3}[、，]"),
    re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节点篇条]"),
    #补阿拉伯数字主标题（"1. 目的"），与 _CHAPTER_L1 的阿拉伯分支一致。
    # 此前只认"一、/第X章"，导致用阿拉伯编号的短文档（<1200字含多章节）
    # 仍被压成 1 个子块，与强制分段的设计目标相悖。
    re.compile(r"^\d{1,2}[.．、](?!\d)"),
    #表格 / 工作表 / 幻灯片标记处强制分段。
    # 此前该标记只参与 _CHAPTER_L1 的「面包屑」计算，不触发分段，于是 chunker
    # 按 max_chars 把「上一张表的尾行 + 下一张表」拼进同一块，向量被多主题稀释。
    # 实测（人员信息.xlsx）：「花名册」与「劳保领用时间」两张表落入同一块，
    # 「李祥华工号是什么」相似度仅 0.4179（阈值 0.40），险些判为「资料中没有」。
    # 注：PDF 无此标记（pypdf 无表格结构），见 P2（pdfplumber）。
    _TABLE_MARK_RE,
)


def _starts_with_heading(para):
    """判断段落是否以主标题开头（应在此处强制分段）。"""
    s = (para or "").strip()
    if not s:
        return False
    return any(rx.match(s) for rx in _HEADING_START_RES)


# ---------------------------------------------------------------------------
# F1 章节层级识别 + 面包屑（用户综合优化版 + 严格A：只认 1./1、）
#
# 目的：给每个 chunk 标注它所属的「章节面包屑」（如
#   "一、总则 > 1.1 适用范围 > 1.1.1 报备时限"），
# 让召回层能按「文件名 > 目录章节 > 小章节 > 正文」分层加权（见 qa._rerank_by_lexical）。
#
# 判断顺序必须 L3 → L1 → L2：否则 "1.1.1" 会被 L2 的 "1.1" 前缀误吞成二级。
# 负向规则①：编号之后无剩余标题文字（裸编号，如单独一行 "1."）→ 当正文。
# ---------------------------------------------------------------------------
# L3 三级（最先判）：1.1.1 / 1.1.1（顿号或空白结尾）
#新增「编号后直接跟标题文字」的分支（(?=[^\d]) 零宽断言）。
# 此前要求编号后必须是分隔符或行尾，导致 "1.1.1报备时限"（无空格）匹配不到 L3，
# 会被 L2 的 "1.1" 前缀吞成二级，面包屑层级错位。
_CHAPTER_L3 = re.compile(r'^\d{1,2}[.．]\d{1,3}[.．]\d{1,3}(?:[.．、\s]|$|(?=[^\d]))')
# L1 一级：一、/第X章（不含"节"）/ 1.（负向断言区分 1. 与 1.1）
#新增【工作表：xx】/【幻灯片：N】标记 —— xlsx/pptx 抽取时会输出这类
# 整行标记，此前不匹配任何标题规则，导致表格行/幻灯片正文的 section_path 恒为空。
_CHAPTER_L1 = (
    re.compile(r'^[一二三四五六七八九十百零]{1,3}[、，]'),
    re.compile(r'^第\s*\d{1,3}\s*[篇章点]'),
    re.compile(r'^\d{1,2}[.．、](?!\d)'),
    re.compile(r'^【(?:工作表|表格|幻灯片)\s*[:：][^】]*】'),
)
# 括号标记整行即标题（后面没有"标题文字"），需豁免下方的裸编号负向规则
#P1：补「表格」——docx 抽取现输出【表格：N】标记（见 parser._iter_docx_blocks），
# 该标记应作为章节面包屑（而非被裸编号负向规则判为 0 级正文），故列入括号标记豁免集。
_BRACKET_MARK_RE = re.compile(r'^【(?:工作表|表格|幻灯片)\s*[:：][^】]*】\s*$')
# L2 二级：1.1 / （一）(括号内编号限长防伪) / 第X节 / 第X条
_CHAPTER_L2 = (
    re.compile(r'^\d{1,2}[.．]\d{1,3}(?:[.．、\s]|$)'),
    re.compile(r'^[（(][一二三四五六七八九十1-9]\d{0,1}[）)]'),
    re.compile(r'^第\s*\d+\s*[节条]'),
)


def _detect_heading_level(line):
    """返回 0=正文 / 1=章 / 2=节 / 3=小节。

    标题总在段落首行（parser 已在标题前插空行）；按首行判定，
    避免把「标题 + 紧随其后的正文」整段误判为标题文本。
    """
    s = (line or "").strip()
    if not s:
        return 0
    first = s.split("\n", 1)[0].strip()
    if not first or len(first) > 80:
        return 0
    m = _CHAPTER_L3.match(first)
    if m:
        level = 3
    else:
        level = 0
        m = None
        for rx in _CHAPTER_L1:
            mm = rx.match(first)
            if mm:
                level = 1
                m = mm
                break
        if level == 0:
            for rx in _CHAPTER_L2:
                mm = rx.match(first)
                if mm:
                    level = 2
                    m = mm
                    break
    if m is None:
        return 0
    # 负向规则①：编号之后无剩余标题文字（裸编号）→ 当正文
    # 例外：【工作表：xx】/【幻灯片：N】整行即标题，后面本就没有正文，不算裸编号
    if not first[m.end():].strip() and not _BRACKET_MARK_RE.match(first):
        return 0
    return level


def _compute_para_sections(paragraphs):
    """维护 heading stack，返回每个段落所属章节面包屑（' > ' 连接）。

    例：['一、总则', '1.1 适用范围', '1.1.1 报备时限'] →
        ['一、总则', '一、总则 > 1.1 适用范围', '一、总则 > 1.1 适用范围 > 1.1.1 报备时限']
    普通正文段落继承最近一个标题的面包屑；文档最前面无标题则为 ''。
    标题文本取段落首行（标题与正文可能同行，仅首行作为标题）。
    """
    stack = []  # [(level, title), ...]
    out = []
    for para in paragraphs:
        first = (para or "").strip().split("\n", 1)[0].strip()
        level = _detect_heading_level(para)
        if level > 0:
            title = first
            # 弹出同级及更深层，保持栈为严格祖先链
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        out.append(" > ".join(t for _, t in stack))
    return out


# 表格行检测：≥3 个 tab 分隔 cell 的单行即视为表行（xlsx 数据行的形态）。
#xlsx 长行不应被按句子硬切——会切掉某一列的字段值，导致
# 召回后 LLM 看不到完整列；故把「整行当一个语义单位」对待，绕过句子级切分。
_TABULAR_LINE_MIN_CELLS = 3


def _looks_tabular_paragraph(para):
    """判断段落是否像 xlsx 表行（≥3 个 tab 分隔 cell 的单行为多），是则不让 _split_by_sentence 按句子硬切。"""
    if not para:
        return False
    # 取最长的单行（xlsx 行通常是一行；带标题/备注的多行场景里选 token 数最多那行）
    lines = [ln for ln in (para.split("\n")) if ln.strip()]
    if not lines:
        return False
    longest = max(lines, key=len)
    cells = [c.strip() for c in longest.split("\t") if c.strip()]
    return len(cells) >= _TABULAR_LINE_MIN_CELLS


def _split_by_sentence(para, max_chars, overlap):
    """将超长段落按句子边界切分，块间保留 overlap 重叠。

    返回子块列表；若单个"句子"（几乎无标点的大段）超过 max_chars，则硬切。

表格行（tab 分隔 ≥3 cell）作为整体保留，不按句子切——
    切完会把同一行的字段值拆散，召回后拼不回列对齐。每行即语义单位，
    宁可超过 max_chars 也要保留行完整。
    """
    #xlsx 表行整段当一个语义块保留
    if _looks_tabular_paragraph(para):
        return [para]
    # 以中英文句末标点 / 换行作为句子边界，并保留分隔符
    parts = re.split(r"(?<=[。！？!?；;\.\n])", para)
    parts = [p for p in parts if p]
    if not parts:
        return [para] if para else []

    chunks = []
    buf = ""
    for part in parts:
        # 单句超长（几乎无标点的大段）：直接按字符硬切，块间带 overlap
        if len(part) > max_chars:
            if buf:
                chunks.append(buf)
                buf = buf[-overlap:] if overlap > 0 else ""
            i = 0
            while i < len(part):
                piece = part[i:i + max_chars]
                chunks.append(piece)
                if i + max_chars >= len(part):
                    break
                # 下一片从当前位置往前 overlap 处开始，形成重叠
                i += max(1, max_chars - overlap)
            continue

        # 普通句：加入缓冲会超限则先落盘
        if buf and len(buf) + len(part) > max_chars:
            chunks.append(buf)
            buf = buf[-overlap:] if overlap > 0 else ""
        buf += part

    if buf:
        chunks.append(buf)
    return chunks


def chunk_text(text, max_chars: int = 500, overlap: int = 120, min_chunk_chars: int = 50,
               para_sections=None):
    """将长文本在段落边界切分为若干块，并给每块标注所属章节面包屑。

    返回 List[(chunk_text, section_path)]。section_path 来自 para_sections
    （与输入段落一一对应），为空字符串表示无章节上下文（如文档最前导言）。
    para_sections 为 None 时（独立调用），section_path 全为空。

    - 先按空行分段；
    - 短段落累积到同一块，直到加入下一段会超过 max_chars；
    - 超长段落按句子再切分；
    - 过滤掉过短（< min_chunk_chars）的碎片（但保留非空内容以防丢数据）。
    """
    if not text:
        return []
    text = text.replace("\r\n", "\n")
    # 1. 按空行分段
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []
    if para_sections is None or len(para_sections) != len(paragraphs):
        para_sections = [""] * len(paragraphs)

    chunks = []
    sections = []
    buf = ""
    buf_section = ""

    def _flush(keep_overlap=True):
        nonlocal buf, buf_section
        if buf:
            chunks.append(buf)
            sections.append(buf_section)
            # 落盘后，末尾 overlap 字符作为下一块的开头，形成块间重叠。
            # 但在**标题处**分段时（keep_overlap=False）不保留重叠 ——
            # 否则上一章节的尾句会被带进新章节块，面包屑标的是新章节、
            # 正文却夹着旧章节残句，对「仅依据该章节」类 prompt 造成干扰。
            buf = buf[-overlap:] if (overlap > 0 and keep_overlap) else ""
            buf_section = ""

    for idx, para in enumerate(paragraphs):
        sec = para_sections[idx]
        # 标题感知分段：遇到主标题（一、/第X章）时强制落盘当前缓冲，
        # 开启新块——即使总长未达 max_chars。这确保短文档（如制度通知
        # 800 字含 5 个章节）也能按章节切成多块，每块 embedding 聚焦单一
        # 主题，大幅提升检索命中率。
        # 子标题（L2/L3：2.1 / 2.4.1 / （一） / 第X条）也强制分段：使每个编号子条款
        # 自成一块、embedding 聚焦单一主题、块 section 取其自身标题，纠正「块末标题盖掉
        # 块内答案」的错标与向量稀释。Excel 的【行】/【表头】/【工作表】路径不受影响
        # （原子行在 flush 前已 emit_atomic 独立成块，且不会被判成编号标题）。
        if buf and (_starts_with_heading(para) or _detect_heading_level(para) >= 2):
            # 标题处强制分段：跳过 overlap，避免跨章节污染（见 _flush 说明）
            _flush(keep_overlap=False)
        # 超长段落：先把缓冲落盘，再独立按句子切分
        if len(para) > max_chars:
            _flush()
            subs = _split_by_sentence(para, max_chars, overlap)
            if subs:
                chunks.extend(subs[:-1])
                sections.extend([sec] * (len(subs) - 1))
                buf = subs[-1][-overlap:] if overlap > 0 else ""
            continue

        # 普通段落：加入缓冲会超限则先落盘
        if buf and len(buf) + 1 + len(para) > max_chars:
            _flush()

        buf = (buf + "\n" + para) if buf else para
        buf_section = sec

    _flush()

    # 后处理：把过短碎片并入相邻块，既避免丢内容，也避免检索噪声。
    # 并入时若会超过 max_chars 则保留为独立小块，不丢内容。
    merged = []
    merged_sec = []
    for c, sec in zip(chunks, sections):
        if merged and len(c) < min_chunk_chars and len(merged[-1]) + len(c) + 1 <= max_chars:
            merged[-1] = merged[-1] + "\n" + c
            # 合并时保留更具体的章节（更长面包屑）
            if len(sec) > len(merged_sec[-1]):
                merged_sec[-1] = sec
        else:
            merged.append(c)
            merged_sec.append(sec)
    # 首块仍过短：尝试并入下一块（不超限才并入）
    if (len(merged) > 1 and len(merged[0]) < min_chunk_chars
            and len(merged[0]) + len(merged[1]) + 1 <= max_chars):
        merged[1] = merged[0] + "\n" + merged[1]
        if len(merged_sec[0]) > len(merged_sec[1]):
            merged_sec[1] = merged_sec[0]
        merged = merged[1:]
        merged_sec = merged_sec[1:]
    # 丢弃空块
    result = [(c, s) for c, s in zip(merged, merged_sec) if c and c.strip()]
    # 防御：若全部被丢弃（极端情况），回退保留非空块，避免丢数据
    if not result:
        result = [(c, s) for c, s in zip(chunks, sections) if c and c.strip()]
    return result


def _inject_table_header(chunks):
    """P1：让同一表格的数据子块携带表头/首行上下文，避免「数据块无列名」导致检索失准。

    背景（实测：人员信息.xlsx）：解析器在每个工作表/表格前输出
    `【工作表：名】`/`【表格：N】` 整行标记，其后第一行为表头（如
    `公司\\t部门\\t车间\\t姓名\\t工号`）。表格超 max_chars 被切成多块时，仅首块含表头，
    后续数据块只剩 `LTL\\t电气运维部\\t技术科\\t李祥华\\t5826012874` 这类纯数据，
    向量化后无法把 `5826012874` 与「工号」列对应 → 人名/工号类检索相似度仅 ~0.22（远低于阈值）。

    做法：
    - **优先（P1 列名来源修复）**：读取解析器显式输出的 `【表头：列1|列2|...】` 标记，
      将其内容作为 cur_header，并**丢弃该标记行本身**（不进 chunk、不重复前置）。
      这能正确处理「首行是合并标题、第二行才是真实列名」的表：合并标题行只是普通内容，
      真实列名由 `【表头：...】` 精确给出，不再被「取标记后首行当表头」的盲猜污染。
    - **回退**：若无 `【表头：...】` 标记（旧索引 / 无标记来源，如 docx `【表格：N】`），
      沿用「标记后第一个非标记行当表头」的启发式（兼容既有路径）。
    - 其后同表的数据块若不含该表头，自动前置一行 `表头：<列名>`。
      幻灯片（【幻灯片：N】）无表头概念，遇之清空当前表头，避免跨表泄漏。

    注意：仅改写子块文本，不改变块数/顺序，child_to_parent 映射保持对齐。
    """
    out = []
    cur_header = None
    for c in chunks:
        lines = (c or "").split("\n")
        kept = []                 # 重新组装：去掉【表头：...】标记行
        header_from_marker = None
        table_mark_seen = False
        for ln in lines:
            s = ln.strip()
            m = _HEADER_LINE_RE.match(s)
            if m:
                # 解析器显式表头标记：取列名、丢弃本行（不进 chunk）
                header_from_marker = m.group(1).strip()
                continue
            if _TABLE_MARK_RE.match(s):
                table_mark_seen = True
            kept.append(ln)
        text = "\n".join(kept)
        if header_from_marker:
            # 显式标记优先（正确处理合并标题行）
            cur_header = header_from_marker
        elif table_mark_seen:
            # 回退（鲁棒化：表格标记可能不在首行，扫描整块所有行定位）
            mark_line = None
            for ln in lines:
                if _TABLE_MARK_RE.match(ln.strip()):
                    mark_line = ln
                    break
            if mark_line is not None and _HEADER_TABLE_MARK_RE.match(mark_line.strip()):
                idx = lines.index(mark_line)
                for ln in lines[idx + 1:]:
                    s = ln.strip()
                    if (s and not _HEADER_TABLE_MARK_RE.match(s)
                            and not _HEADER_LINE_RE.match(s)):
                        cur_header = s
                        break
            else:
                # 幻灯片：无表头概念，清空，避免泄漏给后续块
                cur_header = None
        # 前置表头（若本块未含，避免重复）
        if cur_header and cur_header not in text:
            out.append("表头：" + cur_header + "\n" + text)
        else:
            out.append(text)
    return out


def chunk_with_parents(text, child_max: int = 500, parent_max: int = 1000,
                       overlap: int = 120, min_chunk_chars: int = 50):
    """父子两级分块（阶梯2：父文档检索）+ F1 章节面包屑。

    设计：
    - 先按空行分段，将相邻段落累积成**父块**（约 parent_max 字，语义连贯的大段）。
    - 再把每个父块用句子级逻辑切成**子块**（约 child_max 字）。子块用于建索引/检索（更精准），
      检索命中后回退到其所属**父块**作为上下文喂给 LLM（更丰富、不截断）。
    - F1：每个子块附带所属章节面包屑 section_path（跨父块连续，由文档级 heading
      stack 统一计算），供召回层按「书名>章>节>正文」分层加权（见 qa._rerank_by_lexical）。
    返回: (parents, children, child_to_parent, child_sections, parent_sections)
      - parents: List[str]            父块文本
      - children: List[str]           子块文本（即实际被索引的单位）
      - child_to_parent: List[int]    与 children 等长，值为该子块所属父块下标
      - child_sections: List[str]     与 children 等长，子块章节面包屑（可能为空）
      - parent_sections: List[str]    与 parents 等长，父块章节面包屑（首个段落的）
    """
    if not text:
        return [], [], [], [], []
    text = text.replace("\r\n", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [], [], [], [], []

    # F1：文档级章节面包屑（跨父块连续，单一 heading stack 保证层级不断裂）
    para_sections = _compute_para_sections(paragraphs)

    parents = []
    children = []
    child_to_parent = []
    child_sections = []
    parent_sections = []

    pbuf = ""
    pstart = 0  # 当前父块起始段落下标
    cur_header = None  # P2：明细表原子行前置所需的列名（来自【表头：】标记）

    def flush_parent(start_idx, end_idx):
        nonlocal pbuf
        if not pbuf:
            return
        pidx = len(parents)
        parents.append(pbuf)
        # 父块章节 = 其首个非空段落的面包屑（最顶层上下文）
        parent_sections.append(para_sections[start_idx] if start_idx < len(para_sections) else "")
        # 复用 chunk_text 的句子级切分把父块切成子块，并带入章节面包屑
        subs = chunk_text(pbuf, max_chars=child_max, overlap=overlap,
                          min_chunk_chars=min_chunk_chars,
                          para_sections=para_sections[start_idx:end_idx])
        for s, sec in subs:
            children.append(s)
            child_to_parent.append(pidx)
            child_sections.append(sec)
        pbuf = ""

    def emit_atomic(row_text, sec):
        """P2：明细表原子行 → 独立 parent+child，前置列名上下文。"""
        atomic = ("表头：" + cur_header + "\n" + row_text) if cur_header else row_text
        pidx = len(parents)
        parents.append(atomic)
        parent_sections.append(sec)
        children.append(atomic)
        child_to_parent.append(pidx)
        child_sections.append(sec)

    for i, para in enumerate(paragraphs):
        sec = para_sections[i]
        s = para.strip()
        # P2：新表/新幻灯片 → 重置列名上下文（避免跨表泄漏给后续原子行）
        if _TABLE_MARK_RE.match(s):
            cur_header = None
        # 捕获解析器显式表头标记（保留于 pbuf，供 _inject_table_header 后续消费；
        # 同时记到 cur_header 供紧随其后的原子行前置）
        m_hdr = _HEADER_LINE_RE.match(s)
        if m_hdr:
            cur_header = m_hdr.group(1).strip()
        # P2：原子行（明细表逐行切片）→ 先落盘当前父块，再独立成块，绝不合并
        m_row = _ROW_ATOMIC_RE.match(para)
        if m_row:
            if pbuf:
                flush_parent(pstart, i)
            # ⚠️ 差一修正：para[i] 已由 emit_atomic 独立成块并被 continue 跳过，
            # **不会进 pbuf**。pstart 必须指向下一段（i+1）；留在 i 的话，下一个
            # 父块 flush 时 para_sections[pstart:end] 比 pbuf 实际段落数多 1，
            # chunk_text 的 len 保护（239-240 行）会把整段面包屑清空成 ""，
            # 导致其后所有子块丢失章节路径（分层加权与章节定位一并失效）。
            pstart = i + 1
            emit_atomic(m_row.group(1).strip(), sec)
            continue
        # 当前父块已存在且加入本段会超上限 → 先落盘父块，再开新父块
        if pbuf and len(pbuf) + 2 + len(para) > parent_max:
            flush_parent(pstart, i)
            pstart = i
        # 用空行连接段落（而非单 \n），使 chunk_text 能识别段落边界并在标题处分段。
        # 旧实现用单 \n 导致 chunk_text 把整个父块当作单一段落按句子切，短文档永远只产 1 块。
        pbuf = (pbuf + "\n\n" + para) if pbuf else para
    flush_parent(pstart, len(paragraphs))
    # P1：数据子块携带表头上下文（见 _inject_table_header）。仅改写文本，不改块数/顺序。
    children = _inject_table_header(children)
    return parents, children, child_to_parent, child_sections, parent_sections
