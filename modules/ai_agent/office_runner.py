"""办公异步处理器：把 office_tools.service 的函数包装成「异步 + 进度」可调用单元。

约定（与 executor 协同）：
  - executor 已把用户选定的工作区文件复制到 work_dir/work/ 下
  - 每个 handler 直接对 work/ 目录操作（service 层本就是路径驱动的）
  - handler 结束后，work/ 里的全部内容被收进 work_dir/out/，由 executor 打包下载
  - 需要「控制用 Excel」的能力（建文件夹/建文件/对照改名/搬运等）自动识别
    work/ 里的第一个 .xlsx/.xls/.xlsm 作为控制表，其余文件作为处理对象
  - progress(current, total, message) 由 executor 注入，用于前端轮询

不依赖页面内存（OTState.uploadedFiles），因此能在后端线程里跑，
也不触碰 office_tools 内部——只是复用它的 service 函数。
"""
import os
import csv
import shutil

EXCEL_EXT = (".xlsx", ".xls", ".xlsm")


def _find_excel(work: str, exclude=None):
    """在 work 目录里找第一个 Excel，作为控制表。找不到返回 None。"""
    exclude = set(exclude or [])
    for fn in sorted(os.listdir(work)):
        low = fn.lower()
        if low.endswith(EXCEL_EXT) and fn not in exclude:
            return os.path.join(work, fn)
    return None


def _collect(work: str, out: str):
    """把 work/ 全部内容（含子目录）收进 out/，作为最终产物。"""
    if not os.path.isdir(work):
        return
    for name in os.listdir(work):
        src = os.path.join(work, name)
        dst = os.path.join(out, name)
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _write_text(path: str, lines):
    with open(path, "w", encoding="utf-8", newline="") as f:
        if isinstance(lines, str):
            f.write(lines)
        else:
            for ln in lines:
                f.write(ln + "\n")


# ---------------------------------------------------------------- handlers
def _h_excel_to_pdf(work, params, progress):
    from modules.office_tools import service as ofs
    excels = [f for f in os.listdir(work) if f.lower().endswith(EXCEL_EXT)]
    if not excels:
        raise ValueError("工作区里没有 Excel 文件可供转 PDF")
    progress(0, len(excels), f"开始把 {len(excels)} 个 Excel 转成 PDF…")
    try:
        ofs.batch_excel_to_pdf(work)
    except RuntimeError as e:
        raise RuntimeError(f"Excel 转 PDF 失败：{e}（此功能需要 Windows + 已装 Office）")
    progress(len(excels), len(excels), "转换完成")


def _h_replace_cell(work, params, progress):
    from modules.office_tools import service as ofs
    cell_addr = (params.get("cell_addr") or "").strip()
    old_text = params.get("old_text") or ""
    new_text = params.get("new_text") or ""
    if not cell_addr:
        raise ValueError("缺少单元格地址")
    progress(0, 1, f"正在替换单元格 {cell_addr} 的内容…")
    try:
        ofs.replace_cell_content(work, cell_addr, old_text, new_text)
    except RuntimeError as e:
        raise RuntimeError(f"单元格替换失败：{e}（此功能需要 Windows + 已装 Office）")
    progress(1, 1, "替换完成")


def _h_rename_simple(work, params, progress):
    from modules.office_tools import service as ofs
    old_text = (params.get("old_text") or "").strip()
    new_text = params.get("new_text") or ""
    if not old_text:
        raise ValueError("缺少查找文字")
    progress(0, 1, "正在批量重命名…")
    ofs.batch_rename_simple(work, old_text, new_text)
    progress(1, 1, "重命名完成")


def _h_rename_by_excel(work, params, progress):
    from modules.office_tools import service as ofs
    old_col = (params.get("old_col") or "").strip()
    new_col = (params.get("new_col") or "").strip()
    if not old_col or not new_col:
        raise ValueError("缺少原文件名列 / 新文件名列")
    excel = _find_excel(work)
    if not excel:
        raise ValueError("工作区里没有可作为对照表的 Excel")
    progress(0, 1, "正在按 Excel 对照表改名…")
    ofs.batch_rename_by_excel(work, excel, old_col, new_col)
    progress(1, 1, "改名完成")


def _h_delete_sheets(work, params, progress):
    from modules.office_tools import service as ofs
    names = [s.strip() for s in str(params.get("sheet_names") or "").split(",") if s.strip()]
    if not names:
        raise ValueError("缺少待删除分表名")
    files = [os.path.join(work, f) for f in os.listdir(work)
             if f.lower().endswith(EXCEL_EXT)]
    if not files:
        raise ValueError("工作区里没有可处理的 Excel")
    progress(0, len(files), f"正在删除分表 {', '.join(names)}…")
    ofs.delete_sheets(files, names)
    progress(len(files), len(files), "分表删除完成")


def _h_extract_items(work, params, progress):
    from modules.office_tools import service as ofs
    mode = params.get("mode") or "all"
    suffix = params.get("suffix") or None
    progress(0, 1, "正在提取文件清单…")
    res = ofs.extract_items(work, mode, recursive=False, suffix=suffix)
    headers, data = ofs.extract_detailed_data(res.get("items", []), work, remove_suffix=False)
    out_path = os.path.join(work, "清单.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(data)
    progress(1, 1, f"已生成 {res.get('count', 0)} 条清单")


def _h_extract_paths(work, params, progress):
    from modules.office_tools import service as ofs
    path_type = params.get("path_type") or "relative"
    progress(0, 1, "正在提取文件路径…")
    res = ofs.extract_file_paths(work, path_type, recursive=True)
    # extract_file_paths 返回结构与 extract_items 类似（含 names 列表）
    names = res.get("names") if isinstance(res, dict) else res
    _write_text(os.path.join(work, "路径清单.txt"), names or ["（无文件）"])
    progress(1, 1, f"已提取 {len(names) if isinstance(names, list) else 0} 条路径")


def _h_create_folders(work, params, progress):
    from modules.office_tools import service as ofs
    col = (params.get("column_header") or "").strip()
    if not col:
        raise ValueError("缺少名称所在列标题")
    excel = _find_excel(work)
    if not excel:
        raise ValueError("工作区里没有可作为名称来源的 Excel")
    progress(0, 1, "正在按 Excel 批量建文件夹…")
    ofs.batch_create_folders(excel, col, [work])
    progress(1, 1, "文件夹已创建")


def _h_create_files(work, params, progress):
    from modules.office_tools import service as ofs
    col = (params.get("column_header") or "").strip()
    file_type = params.get("file_type") or "excel"
    if not col:
        raise ValueError("缺少名称所在列标题")
    excel = _find_excel(work)
    if not excel:
        raise ValueError("工作区里没有可作为名称来源的 Excel")
    progress(0, 1, "正在按 Excel 批量建文件…")
    ofs.batch_create_files(excel, col, [work], file_type)
    progress(1, 1, "文件已创建")


def _h_create_sheets(work, params, progress):
    from modules.office_tools import service as ofs
    col = (params.get("column_header") or "").strip()
    fill = bool(params.get("fill"))
    if not col:
        raise ValueError("缺少分表名称列标题")
    excel = _find_excel(work)
    if not excel:
        raise ValueError("工作区里没有可作为名称来源的 Excel")
    targets = [os.path.join(work, f) for f in os.listdir(work)
               if f.lower().endswith(EXCEL_EXT) and f != os.path.basename(excel)]
    if not targets:
        targets = [excel]  # 没有别的 excel 就给自己加分表
    progress(0, 1, "正在批量建分表…")
    ofs.batch_create_sheets(excel, col, targets, fill=fill)
    progress(1, 1, "分表已创建")


def _h_move_copy(work, params, progress):
    from modules.office_tools import service as ofs
    action = params.get("action") or "copy"
    excel = _find_excel(work)
    if not excel:
        raise ValueError("工作区里没有可作为搬运映射表的 Excel")
    progress(0, 1, "正在按映射表搬运文件…")
    ofs.batch_move_copy(work, work, excel, action, recursive=True)
    progress(1, 1, "搬运完成")


HANDLERS = {
    "office.mod4_pdf": _h_excel_to_pdf,
    "office.mod4_cell": _h_replace_cell,
    "office.mod3_simple": _h_rename_simple,
    "office.mod3_excel": _h_rename_by_excel,
    "office.mod4_delete_sheets": _h_delete_sheets,
    "office.mod2_extract": _h_extract_items,
    "office.mod5_paths": _h_extract_paths,
    "office.mod1_folders": _h_create_folders,
    "office.mod1_files": _h_create_files,
    "office.mod1_sheets": _h_create_sheets,
    "office.mod5_movecopy": _h_move_copy,
}


def dispatch(handler_key: str, work_dir: str, params: dict, progress):
    """执行某个办公异步能力。work_dir 下须有 work/（输入）与 out/（产物）。"""
    fn = HANDLERS.get(handler_key)
    if not fn:
        raise ValueError(f"未知的办公处理器：{handler_key}")
    work = os.path.join(work_dir, "work")
    out = os.path.join(work_dir, "out")
    os.makedirs(out, exist_ok=True)
    fn(work, params or {}, progress)
    _collect(work, out)
