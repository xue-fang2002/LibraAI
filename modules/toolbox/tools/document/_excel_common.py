"""Excel 工具共享 helper（供 tools/document/excel_*.py 使用）。

统一用 pandas 读写，屏蔽 csv/tsv/xlsx 差异；.xls 老格式不在此支持范围
（openpyxl/pandas 读 .xls 需额外 xlrd，且新版 xlrd 已停止支持 .xls，故统一
提示用户另存为 .xlsx/.csv）。
"""
import os

SUPPORTED_EXTS = (".xlsx", ".xlsm", ".csv", ".tsv")


def is_supported(name):
    return str(name or "").lower().endswith(SUPPORTED_EXTS)


def list_excels(in_dir):
    """列出输入目录里受支持的表格文件（按名排序）。"""
    if not in_dir or not os.path.isdir(in_dir):
        return []
    return [
        os.path.join(in_dir, f) for f in sorted(os.listdir(in_dir))
        if os.path.isfile(os.path.join(in_dir, f)) and is_supported(f)
    ]


def read_table(fpath, sheet=None):
    """按扩展名读成 pandas.DataFrame；xlsx 可指定 sheet（None=第一个表）。"""
    import pandas as pd
    ext = os.path.splitext(fpath)[1].lower()
    if ext == ".csv":
        return pd.read_csv(fpath)
    if ext == ".tsv":
        return pd.read_csv(fpath, sep="\t")
    return pd.read_excel(fpath, sheet_name=sheet or 0)


def read_all_sheets(fpath):
    """读 xlsx 的全部 sheet，返回 {sheet_name: DataFrame}。csv/tsv 只有一张表。"""
    import pandas as pd
    ext = os.path.splitext(fpath)[1].lower()
    if ext in (".csv", ".tsv"):
        return {"Sheet1": read_table(fpath)}
    xls = pd.ExcelFile(fpath)
    return {name: pd.read_excel(xls, sheet_name=name) for name in xls.sheet_names}


def write_table(df, out_path, sheet_name="Sheet1"):
    """按扩展名写 DataFrame。"""
    ext = os.path.splitext(out_path)[1].lower()
    if ext == ".csv":
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    elif ext == ".tsv":
        df.to_csv(out_path, index=False, sep="\t", encoding="utf-8-sig")
    else:
        df.to_excel(out_path, index=False, sheet_name=sheet_name)
