"""Excel 数据脱敏：手机号 / 身份证 / 银行卡 / 姓名 / 邮箱 按规则打码。

rule=auto 时按内容形态自动识别（手机号 11 位 1 开头、身份证 18 位、卡号 16-19 位、
邮箱带 @），识别不出就按「保留前 keep_head 后 keep_tail」处理。
"""
import os
import re

from core.response import ok, fail
from .._common import in_out, ordered_files
from ._excel_common import is_supported, read_table, write_table

TOOL_ID = "document_excel_mask"
CATEGORY = "document"
LABEL = "Excel 数据脱敏"
NEEDS_FILES = True
PARAMS = [
    {"name": "rule", "label": "脱敏规则", "type": "select"},
    {"name": "columns", "label": "仅处理这些列（逗号分隔，留空=全部）", "type": "text"},
    {"name": "keep_head", "label": "保留前几位", "type": "number"},
    {"name": "keep_tail", "label": "保留后几位", "type": "number"},
    {"name": "mask_char", "label": "填充字符", "type": "text"},
]

_RE_PHONE = re.compile(r"^1[3-9]\d{9}$")
_RE_ID = re.compile(r"^(\d{17}[\dXx]|\d{15})$")
_RE_BANK = re.compile(r"^\d{16,19}$")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_PRESET = {
    "phone": (3, 4),
    "idcard": (6, 4),
    "bank": (0, 4),
    "name": (1, 0),
}


def _mask(s, rule, keep_head, keep_tail, ch):
    s = str(s)
    if not s or s.lower() == "nan":
        return s

    if rule == "auto":
        t = s.strip()
        if _RE_PHONE.match(t):
            rule = "phone"
        elif _RE_ID.match(t):
            rule = "idcard"
        elif _RE_EMAIL.match(t):
            rule = "email"
        elif _RE_BANK.match(t):
            rule = "bank"
        else:
            rule = "custom"

    if rule == "email":
        name, _, domain = s.partition("@")
        if not domain:
            return s
        head = name[:1] if name else ""
        return f"{head}{ch * max(1, len(name) - 1)}@{domain}"

    if rule in _PRESET:
        keep_head, keep_tail = _PRESET[rule]

    n = len(s)
    if keep_head + keep_tail >= n:
        # 字符串太短，全打码反而更安全（避免变相原样输出）
        return ch * n
    return s[:keep_head] + ch * (n - keep_head - keep_tail) + (s[n - keep_tail:] if keep_tail else "")


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    tables = [f for f in ordered_files(in_dir) if is_supported(f)]
    if not tables:
        return fail("未找到表格文件（xlsx / csv / tsv）", code=400)

    rule = (params.get("rule") or "auto").strip()
    if rule not in ("auto", "phone", "idcard", "bank", "name", "email", "custom"):
        rule = "auto"

    def _int(name, default):
        try:
            return int(float(params.get(name) if params.get(name) not in (None, "") else default))
        except (TypeError, ValueError):
            return default

    keep_head = max(0, _int("keep_head", 0))
    keep_tail = max(0, _int("keep_tail", 0))
    ch = (params.get("mask_char") or "*").strip()[:1] or "*"
    only = [c.strip() for c in str(params.get("columns") or "").split(",") if c.strip()]

    results = []
    for fpath in tables:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}_masked.xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)
            targets = [c for c in (only or list(df.columns)) if c in df.columns]
            if only:
                missing = [c for c in only if c not in df.columns]
                if missing:
                    results.append({"name": out_name, "error": f"缺少列：{', '.join(missing)}"})
                    continue
            for c in targets:
                df[c] = df[c].map(lambda v: _mask(v, rule, keep_head, keep_tail, ch))
            write_table(df, out_path)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})
    return ok(data={"results": results})
