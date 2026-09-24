"""PDF 页码解析 helper（供 tools/document/ 下需要页码参数的工具复用）。"""


def parse_pages(text, total, one_based=True):
    """把 "1-3,5,8-10" 解析成 0-based 页码列表。

    - 去重保序（先写先出），越界与非法片段直接忽略，不抛异常；
    - 返回空列表表示「用户没填 / 全不合法」，调用方自行决定是全选还是报错。
    """
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        off = 1 if one_based else 0
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            lo, hi = max(1, min(a, b)), min(total, max(a, b))
            for i in range(lo, hi + 1):
                idx = i - off
                if 0 <= idx < total and idx not in out:
                    out.append(idx)
        else:
            try:
                i = int(part)
            except ValueError:
                continue
            idx = i - off
            if 0 <= idx < total and idx not in out:
                out.append(idx)
    return out
