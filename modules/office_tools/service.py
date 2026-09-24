"""
办公工具 - 核心业务逻辑
从全能办公工具_v4.py 迁移改造，去除 GUI 依赖，改为纯函数式服务层
"""
import os
import shutil
import csv
from copy import copy
from openpyxl import load_workbook, Workbook

# 依赖库软加载
try:
    import xlrd
    HAS_XLRD = True
except ImportError:
    xlrd = None
    HAS_XLRD = False

try:
    from docx import Document as docx_Document
except ImportError:
    docx_Document = None

try:
    import win32com.client
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

EXCEL_EXTENSIONS = (".xlsx", ".xls", ".xlsm")

# 安全清洗（防路径穿越）：单元格值/表单值作为路径分量前先过 _safe_dirname
try:
    from common.utils import _safe_dirname, is_safe_path
except Exception:  # 独立脚本场景兑底
    def _safe_dirname(name, default="未命名", max_len=50):
        name = "".join(c for c in (name or default) if c not in '\\/:*?"<>|').strip()
        if name in {".", ".."} or not name:
            name = default
        return name[:max_len]

    def is_safe_path(requested_path, base_path):
        try:
            import os as _os
            from pathlib import Path as _Path
            rq = _Path(requested_path).resolve()
            bs = _Path(base_path).resolve()
            return rq == bs or bs in rq.parents
        except Exception:
            return False


# ======================== 工具函数 ========================

def read_column_values(file_path, column_header):
    """从Excel文件中读取指定列的所有非空值"""
    values = []
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".xlsx":
            wb = load_workbook(file_path, data_only=True)
            ws = wb.active
            headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
            try:
                idx = headers.index(column_header) + 1
            except ValueError:
                clean_headers = [str(h).strip() if h is not None else "" for h in headers]
                clean_col = column_header.strip()
                try:
                    idx = clean_headers.index(clean_col) + 1
                except ValueError:
                    sample = ", ".join([str(h) for h in headers[:5] if h is not None]) + ("..." if len(headers) > 5 else "")
                    raise ValueError(f"列标题 '{column_header}' 未找到。当前表头前几项：{sample}")
            for r in range(2, ws.max_row + 1):
                v = ws.cell(r, idx).value
                if v is not None and str(v).strip():
                    values.append(str(v).strip())
            wb.close()
        elif ext == ".xls":
            if xlrd is None:
                raise ValueError("当前环境缺少 xlrd 库，无法处理 .xls 文件，请改用 .xlsx 或安装 xlrd（pip install xlrd）。")
            wb = xlrd.open_workbook(file_path)
            sh = wb.sheet_by_index(0)
            headers = sh.row_values(0)
            try:
                idx = headers.index(column_header)
            except ValueError:
                clean_headers = [str(h).strip() if h is not None else "" for h in headers]
                clean_col = column_header.strip()
                try:
                    idx = clean_headers.index(clean_col)
                except ValueError:
                    sample = ", ".join([str(h) for h in headers[:5] if h is not None]) + ("..." if len(headers) > 5 else "")
                    raise ValueError(f"列标题 '{column_header}' 未找到。当前表头前几项：{sample}")
            for r in range(1, sh.nrows):
                v = sh.row_values(r)[idx]
                if v is not None and str(v).strip():
                    values.append(str(v).strip())
        else:
            raise ValueError("不支持的文件格式，请使用 .xlsx 或 .xls")
    except Exception as e:
        raise e
    return values


def read_mapping_from_excel(file_path):
    """读取映射Excel，返回字典 {目标子文件夹名: [文件名列表]}"""
    mapping = {}
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".xlsx":
            wb = load_workbook(file_path, data_only=True)
            ws = wb.active
            headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
            for col_idx, header in enumerate(headers, start=1):
                if header is None:
                    continue
                folder_name = str(header).strip()
                if not folder_name:
                    continue
                files = []
                for r in range(2, ws.max_row + 1):
                    val = ws.cell(r, col_idx).value
                    if val is not None:
                        files.append(str(val).strip())
                if files:
                    mapping[folder_name] = files
            wb.close()
        elif ext == ".xls":
            if xlrd is None:
                raise ValueError("当前环境缺少 xlrd 库，无法处理 .xls 文件，请改用 .xlsx 或安装 xlrd（pip install xlrd）。")
            wb = xlrd.open_workbook(file_path)
            sh = wb.sheet_by_index(0)
            headers = sh.row_values(0)
            for col_idx, header in enumerate(headers):
                if header is None:
                    continue
                folder_name = str(header).strip()
                if not folder_name:
                    continue
                files = []
                for r in range(1, sh.nrows):
                    val = sh.row_values(r)[col_idx]
                    if val:
                        files.append(str(val).strip())
                if files:
                    mapping[folder_name] = files
        else:
            raise ValueError("不支持的Excel格式，请使用 .xlsx 或 .xls")
    except Exception as e:
        raise e
    return mapping


def validate_dir(path):
    """校验目录是否存在，若不存在则尝试创建"""
    if not path:
        return False
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


def safe_save_workbook(wb, file_path):
    """保存工作簿，捕获文件占用异常"""
    try:
        wb.save(file_path)
    except PermissionError:
        raise PermissionError(f"文件 '{file_path}' 被其他程序占用，请关闭后重试")


def copy_sheet_full_safe(src_ws, dest_ws):
    """稳健复制工作表内容和样式"""
    try:
        dest_ws.unmerge_cells()
    except:
        pass
    max_r = src_ws.max_row
    max_c = src_ws.max_column
    for r in range(1, max_r + 1):
        for c in range(1, max_c + 1):
            try:
                dest_ws.cell(r, c).value = src_ws.cell(r, c).value
            except:
                pass
    for r in range(1, max_r + 1):
        for c in range(1, max_c + 1):
            scell = src_ws.cell(r, c)
            dcell = dest_ws.cell(r, c)
            for attr in ['font', 'border', 'fill', 'alignment', 'protection']:
                try:
                    setattr(dcell, attr, copy(getattr(scell, attr)))
                except:
                    pass
            try:
                dcell.number_format = scell.number_format
            except:
                pass
    for rng in src_ws.merged_cells.ranges:
        try:
            dest_ws.merge_cells(rng.coord)
        except:
            pass
    for col_key, dim in src_ws.column_dimensions.items():
        if dim.width is not None:
            try:
                dest_ws.column_dimensions[col_key].width = float(dim.width)
            except:
                pass
    for row_key, dim in src_ws.row_dimensions.items():
        if dim.height is not None:
            try:
                dest_ws.row_dimensions[row_key].height = float(dim.height)
            except:
                pass


# ======================== 模块1：批量创建 ========================

def batch_create_folders(excel_path, column_header, target_dirs):
    """批量建文件夹"""
    log = []
    names = read_column_values(excel_path, column_header)
    for d in target_dirs:
        if not validate_dir(d):
            log.append({"status": "error", "msg": f"目录无效或无法创建: {d}"})
            continue
        for name in names:
            # 单元格值作为路径分量前清洗：剔除路径分隔符/".."，防越界
            name = _safe_dirname(str(name or ""))
            full = os.path.join(d, name)
            if not is_safe_path(os.path.normpath(full), d):
                log.append({"status": "error", "msg": f"名称非法，已跳过: {name}"})
                continue
            try:
                if not os.path.exists(full):
                    os.makedirs(full, exist_ok=True)
                    log.append({"status": "success", "msg": f"创建: {full}"})
                else:
                    log.append({"status": "skip", "msg": f"已存在跳过: {full}"})
            except Exception as e:
                log.append({"status": "error", "msg": f"创建失败 {full}: {str(e)}"})
    return {"total": len(names) * len(target_dirs), "names": names, "log": log}


def batch_create_files(excel_path, column_header, target_dirs, file_type):
    """批量建文件"""
    log = []
    names = read_column_values(excel_path, column_header)
    for d in target_dirs:
        if not validate_dir(d):
            log.append({"status": "error", "msg": f"目录无效或无法创建: {d}"})
            continue
        for name in names:
            # 单元格值作为文件名前清洗：剔除路径分隔符/".."，防越界
            name = _safe_dirname(str(name or ""))
            if not is_safe_path(os.path.normpath(os.path.join(d, name)), d):
                log.append({"status": "error", "msg": f"名称非法，已跳过: {name}"})
                continue
            try:
                if file_type == "excel":
                    fp = os.path.join(d, f"{name}.xlsx")
                    if not os.path.exists(fp):
                        wb = Workbook()
                        wb.save(fp)
                        log.append({"status": "success", "msg": f"创建: {fp}"})
                    else:
                        log.append({"status": "skip", "msg": f"已存在跳过: {fp}"})
                elif file_type == "txt":
                    fp = os.path.join(d, f"{name}.txt")
                    if not os.path.exists(fp):
                        open(fp, "w", encoding="utf-8").close()
                        log.append({"status": "success", "msg": f"创建: {fp}"})
                    else:
                        log.append({"status": "skip", "msg": f"已存在跳过: {fp}"})
                elif file_type == "word":
                    fp = os.path.join(d, f"{name}.docx")
                    if not os.path.exists(fp):
                        if docx_Document is None:
                            log.append({"status": "error", "msg": f"未安装python-docx库"})
                        else:
                            docx_Document().save(fp)
                            log.append({"status": "success", "msg": f"创建: {fp}"})
                    else:
                        log.append({"status": "skip", "msg": f"已存在跳过: {fp}"})
            except Exception as e:
                log.append({"status": "error", "msg": f"创建失败 {name}: {str(e)}"})
    return {"total": len(names) * len(target_dirs), "log": log}


def batch_create_sheets(excel_path, column_header, target_files, fill=False, template_path=None):
    """批量创建Excel分表"""
    log = []
    sheet_names = read_column_values(excel_path, column_header)
    src_ws = None
    if fill and template_path and os.path.exists(template_path):
        try:
            c_wb = load_workbook(template_path)
            src_ws = c_wb.active
            log.append({"status": "info", "msg": "模板加载成功"})
        except Exception as e:
            log.append({"status": "error", "msg": f"模板读取异常: {str(e)}"})

    for fp in target_files:
        fn = os.path.basename(fp)
        try:
            t_wb = load_workbook(fp)
            add_cnt = 0
            for sn in sheet_names:
                if sn in t_wb.sheetnames:
                    log.append({"status": "skip", "msg": f"[{fn}] 分表 '{sn}' 已存在，跳过"})
                    continue
                try:
                    new_ws = t_wb.create_sheet(title=sn)
                    if fill and src_ws:
                        try:
                            copy_sheet_full_safe(src_ws, new_ws)
                        except Exception as e:
                            log.append({"status": "error", "msg": f"[{fn}] 复制模板样式异常: {str(e)}"})
                    add_cnt += 1
                    log.append({"status": "success", "msg": f"[{fn}] 新建分表: {sn}"})
                except Exception as e:
                    log.append({"status": "error", "msg": f"[{fn}] 创建分表 '{sn}' 失败: {str(e)}"})
            try:
                safe_save_workbook(t_wb, fp)
                log.append({"status": "success", "msg": f"[{fn}] 保存成功，新增 {add_cnt} 张分表"})
            except PermissionError as e:
                log.append({"status": "error", "msg": f"[{fn}] 保存失败: {str(e)}"})
            t_wb.close()
        except Exception as e:
            log.append({"status": "error", "msg": f"[{fn}] 打开失败: {str(e)}"})
    return {"total": len(target_files) * len(sheet_names), "log": log}


# ======================== 模块2：提取清单 ========================

def extract_items(path, mode, recursive=False, suffix=None, remove_suffix=False):
    """提取文件/文件夹清单"""
    items = []
    res = []
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    suffix = suffix.lower() if suffix else None

    try:
        if recursive:
            for root, dirs, files in os.walk(path):
                if mode in ("files", "filter"):
                    for f in files:
                        full = os.path.join(root, f)
                        if mode == "filter" and suffix and not f.lower().endswith(suffix):
                            continue
                        items.append(full)
                        res.append(f if not remove_suffix else os.path.splitext(f)[0])
                if mode == "all":
                    for f in files:
                        items.append(os.path.join(root, f))
                        res.append(f)
                    for d in dirs:
                        items.append(os.path.join(root, d))
                        res.append(d)
        else:
            for item in os.listdir(path):
                full = os.path.join(path, item)
                if os.path.isfile(full):
                    if mode in ("files", "filter"):
                        if mode == "filter" and suffix and not item.lower().endswith(suffix):
                            continue
                        items.append(full)
                        res.append(item if not remove_suffix else os.path.splitext(item)[0])
                    elif mode == "all":
                        items.append(full)
                        res.append(item)
                elif os.path.isdir(full) and mode == "all":
                    items.append(full)
                    res.append(item)
    except Exception as e:
        raise e
    return {"items": items, "names": res, "count": len(res)}


def extract_detailed_data(items, root_dir, remove_suffix=False):
    """生成详细清单数据"""
    data = []
    for abs_path in items:
        rel_path = os.path.relpath(abs_path, root_dir) if root_dir else abs_path
        if rel_path == '.':
            rel_path = ''
        parts = rel_path.split(os.sep) if rel_path else []
        if os.path.isdir(abs_path):
            item_type = "文件夹"
            basename = parts[-1] if parts else os.path.basename(abs_path)
            dirs = parts[:-1] if parts else []
        else:
            basename = parts[-1] if parts else os.path.basename(abs_path)
            dirs = parts[:-1] if parts else []
            ext = os.path.splitext(basename)[1]
            item_type = ext[1:] if ext else "文件"
        filename_disp = os.path.splitext(basename)[0] if remove_suffix else basename
        row = [filename_disp, item_type] + dirs
        data.append(row)
    max_level = max((len(row) - 2) for row in data) if data else 0
    headers = ["文件名", "类型"] + [f"层级{i+1}" for i in range(max_level)]
    for row in data:
        while len(row) < len(headers):
            row.append("")
    return headers, data


# ======================== 模块3：批量改名 ========================

def batch_rename_simple(path, old_text, new_text):
    """简单文件名替换"""
    log = []
    if not old_text:
        raise ValueError("查找文字不能为空")
    cnt = 0
    for fn in os.listdir(path):
        if old_text in fn:
            old_p = os.path.join(path, fn)
            new_fn = fn.replace(old_text, new_text)
            new_p = os.path.join(path, new_fn)
            if os.path.exists(new_p):
                base, ext = os.path.splitext(new_fn)
                i = 1
                while os.path.exists(os.path.join(path, f"{base}_{i}{ext}")):
                    i += 1
                new_fn = f"{base}_{i}{ext}"
                new_p = os.path.join(path, new_fn)
            try:
                os.rename(old_p, new_p)
                log.append({"status": "success", "msg": f"{fn} → {new_fn}"})
                cnt += 1
            except Exception as e:
                log.append({"status": "error", "msg": f"重命名失败 {fn}: {str(e)}"})
    return {"count": cnt, "log": log}


def batch_rename_by_excel(path, excel_path, old_col, new_col):
    """Excel对照表批量重命名"""
    log = []
    old_names = read_column_values(excel_path, old_col)
    new_names = read_column_values(excel_path, new_col)
    if len(old_names) != len(new_names):
        raise ValueError(f"原文件名数量({len(old_names)})与新文件名数量({len(new_names)})不一致")
    suc, skip, fail = 0, 0, 0
    for idx in range(len(old_names)):
        old_fn = old_names[idx].strip()
        new_fn = new_names[idx].strip()
        if not old_fn or not new_fn:
            skip += 1
            log.append({"status": "skip", "msg": f"行{idx+2} 名称为空"})
            continue
        # 只允许文件名（不允许路径分量），防 "..\" / 绝对路径越界重命名
        if (any(t in old_fn for t in ("/", "\\", ".."))
                or any(t in new_fn for t in ("/", "\\", ".."))
                or os.path.isabs(new_fn)):
            fail += 1
            log.append({"status": "error", "msg": f"名称非法，已跳过: {old_fn} -> {new_fn}"})
            continue
        old_full = os.path.join(path, old_fn)
        if not os.path.exists(old_full):
            skip += 1
            log.append({"status": "skip", "msg": f"{old_fn} 文件不存在"})
            continue
        if old_fn == new_fn:
            skip += 1
            log.append({"status": "skip", "msg": f"{old_fn} 新旧名称一致"})
            continue
        new_full = os.path.join(path, new_fn)
        final_fn = new_fn
        if os.path.exists(new_full):
            base, ext = os.path.splitext(new_fn)
            num = 1
            while os.path.exists(os.path.join(path, f"{base}_{num}{ext}")):
                num += 1
            final_fn = f"{base}_{num}{ext}"
            new_full = os.path.join(path, final_fn)
            log.append({"status": "info", "msg": f"自动重命名避免冲突: {new_fn} → {final_fn}"})
        try:
            os.rename(old_full, new_full)
            suc += 1
            log.append({"status": "success", "msg": f"{old_fn} → {final_fn}"})
        except Exception as e:
            fail += 1
            log.append({"status": "error", "msg": f"重命名失败 {old_fn}: {str(e)}"})
    return {"success": suc, "skip": skip, "fail": fail, "log": log}


# ======================== 模块4：Excel 专项 ========================

def check_win32com():
    if not HAS_WIN32COM:
        raise RuntimeError("当前服务器未安装 pywin32 或 Microsoft Office，此功能不可用")


def _replace_cell_openpyxl(fp, cell_addr, old_text, new_text):
    """用 openpyxl 替换指定单元格文字（.xlsx/.xlsm，不需要任何办公软件）。

    返回 (status, msg)，status 为 success / skip。
    """
    ext = os.path.splitext(fp)[1].lower()
    wb = load_workbook(fp, keep_vba=(ext == ".xlsm"))
    try:
        ws = wb.active
        try:
            target = ws[cell_addr]
        except Exception:
            raise ValueError(f"无效的单元格地址：{cell_addr}")

        # 单个单元格返回 Cell；区域（如 A1:B2）返回元组的元组
        rows = target if isinstance(target, tuple) else ((target,),)

        hit = False
        empty = True
        for row in rows:
            for cell in row:
                if cell.value is None:
                    continue
                empty = False
                s = str(cell.value)
                if old_text in s:
                    cell.value = s.replace(old_text, new_text)
                    hit = True

        if empty:
            return "skip", "单元格为空"
        if not hit:
            return "skip", "无匹配文字"
        safe_save_workbook(wb, fp)
        return "success", f"单元格 {cell_addr} 已更新"
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _replace_cell_com(fp, cell_addr, old_text, new_text):
    """老格式 .xls 走 Office COM（openpyxl 只能读不能写 .xls）。"""
    check_win32com()
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb = excel.Workbooks.Open(fp)
        ws = wb.ActiveSheet
        val = ws.Range(cell_addr).Value
        if val is None:
            wb.Close(SaveChanges=False)
            return "skip", "单元格为空"
        s = str(val)
        if old_text in s:
            ws.Range(cell_addr).Value = s.replace(old_text, new_text)
            wb.Save()
            wb.Close()
            return "success", f"单元格 {cell_addr} 已更新"
        wb.Close(SaveChanges=False)
        return "skip", "无匹配文字"
    finally:
        excel.Quit()


def replace_cell_content(path, cell_addr, old_text, new_text):
    """批量替换Excel指定单元格内容。

    .xlsx / .xlsm 走 openpyxl（纯 Python，服务器无需装 Office / LibreOffice）；
    .xls 老格式 openpyxl 无法写入，仍需 Office COM。
    """
    log = []
    for fn in sorted(os.listdir(path)):
        if not fn.lower().endswith(EXCEL_EXTENSIONS):
            continue
        fp = os.path.join(path, fn)
        ext = os.path.splitext(fn)[1].lower()
        try:
            if ext == ".xls":
                status, msg = _replace_cell_com(fp, cell_addr, old_text, new_text)
            else:
                status, msg = _replace_cell_openpyxl(fp, cell_addr, old_text, new_text)
            log.append({"status": status, "msg": f"{fn}: {msg}"})
        except PermissionError as e:
            log.append({"status": "error", "msg": f"{fn}: 文件被占用: {str(e)}"})
        except Exception as e:
            log.append({"status": "error", "msg": f"{fn}: 处理失败: {str(e)}"})
    return {"log": log}


def batch_excel_to_pdf(path):
    """Excel批量转PDF（引擎自动选择：Office COM / LibreOffice headless）"""
    from core.office_engine import convert_to_pdf, engine_status

    status = engine_status()
    if not status.get("engine"):
        raise RuntimeError(status.get("message") or "无可用文档转换引擎")

    log = []
    for fn in sorted(os.listdir(path)):
        if fn.lower().endswith(EXCEL_EXTENSIONS):
            fp = os.path.join(path, fn)
            try:
                pdf_path = convert_to_pdf(fp, path)
                log.append({"status": "success",
                            "msg": f"{fn} → {os.path.basename(pdf_path)}"})
            except Exception as e:
                log.append({"status": "error", "msg": f"转PDF失败 {fn}: {str(e)}"})
    return {"log": log}


def delete_sheets(file_list, sheet_names):
    """批量删除Excel指定分表"""
    log = []
    total_del = 0
    for fp in file_list:
        fn = os.path.basename(fp)
        try:
            ext = os.path.splitext(fp)[1].lower()
            if ext in (".xlsx", ".xlsm"):
                wb = load_workbook(fp, keep_vba=(ext == ".xlsm"))
                deleted = []
                for sn in sheet_names:
                    if sn in wb.sheetnames:
                        if len(wb.sheetnames) <= 1:
                            log.append({"status": "skip", "msg": f"[{fn}] 无法删除 '{sn}'（仅剩一个工作表）"})
                        else:
                            wb.remove(wb[sn])
                            deleted.append(sn)
                if deleted:
                    safe_save_workbook(wb, fp)
                    total_del += len(deleted)
                    log.append({"status": "success", "msg": f"[{fn}] 删除 {len(deleted)} 个分表: {', '.join(deleted)}"})
                else:
                    log.append({"status": "skip", "msg": f"[{fn}] 无匹配分表"})
                wb.close()
            elif ext == ".xls":
                check_win32com()
                excel = win32com.client.DispatchEx("Excel.Application")
                excel.Visible = False
                excel.DisplayAlerts = False
                wb = None
                try:
                    wb = excel.Workbooks.Open(fp)
                    deleted = []
                    for sn in sheet_names:
                        found = False
                        for sh in wb.Sheets:
                            if sh.Name == sn:
                                found = True
                                if wb.Sheets.Count <= 1:
                                    log.append({"status": "skip", "msg": f"[{fn}] 无法删除 '{sn}'（仅剩一个工作表）"})
                                else:
                                    sh.Delete()
                                    deleted.append(sn)
                                break
                        if not found:
                            log.append({"status": "skip", "msg": f"[{fn}] 未找到分表 '{sn}'"})
                    if deleted:
                        wb.Save()
                        total_del += len(deleted)
                        log.append({"status": "success", "msg": f"[{fn}] 删除 {len(deleted)} 个分表: {', '.join(deleted)}"})
                    else:
                        log.append({"status": "skip", "msg": f"[{fn}] 无匹配分表"})
                    wb.Close()
                except Exception as e:
                    if wb:
                        wb.Close(SaveChanges=False)
                    raise e
                finally:
                    excel.Quit()
            else:
                log.append({"status": "skip", "msg": f"[{fn}] 不支持的格式"})
        except PermissionError as e:
            log.append({"status": "error", "msg": f"[{fn}] 文件被占用: {str(e)}"})
        except Exception as e:
            log.append({"status": "error", "msg": f"[{fn}] 处理失败: {str(e)}"})
    return {"total_deleted": total_del, "log": log}


# ======================== 模块5：文件搬运 ========================

def batch_move_copy(source_dir, target_dir, excel_path, action, recursive=True):
    """基于Excel映射表批量移动/复制文件"""
    log = []
    mapping = read_mapping_from_excel(excel_path)
    if not mapping:
        raise ValueError("Excel中未读取到有效映射数据")

    # 建立文件索引
    file_index = {}
    if recursive:
        for root, _, files in os.walk(source_dir):
            for f in files:
                full = os.path.join(root, f)
                file_index.setdefault(f, []).append(full)
    else:
        for item in os.listdir(source_dir):
            full = os.path.join(source_dir, item)
            if os.path.isfile(full):
                file_index.setdefault(item, []).append(full)

    log.append({"status": "info", "msg": f"索引完成，共 {len(file_index)} 个不同文件名"})
    suc, fail, missing = 0, 0, 0

    for sub_folder, file_list in mapping.items():
        target_sub = os.path.join(target_dir, sub_folder)
        try:
            os.makedirs(target_sub, exist_ok=True)
        except Exception as e:
            log.append({"status": "error", "msg": f"创建子文件夹失败 {sub_folder}: {str(e)}"})
            fail += len(file_list)
            continue

        for filename in file_list:
            if filename not in file_index:
                log.append({"status": "error", "msg": f"未找到文件: {filename}"})
                missing += 1
                continue
            src_paths = file_index[filename]
            if len(src_paths) > 1:
                log.append({"status": "info", "msg": f"同名文件({len(src_paths)}个)，使用第一个: {src_paths[0]}"})
            src_path = src_paths[0]
            dst_path = os.path.join(target_sub, filename)
            try:
                if action == "copy":
                    shutil.copy2(src_path, dst_path)
                    log.append({"status": "success", "msg": f"复制成功: {filename} → {sub_folder}/"})
                else:
                    shutil.move(src_path, dst_path)
                    log.append({"status": "success", "msg": f"移动成功: {filename} → {sub_folder}/"})
                suc += 1
            except Exception as e:
                log.append({"status": "error", "msg": f"操作失败 {filename}: {str(e)}"})
                fail += 1
    return {"success": suc, "fail": fail, "missing": missing, "log": log}


def extract_file_paths(path, path_type, recursive):
    """提取文件路径"""
    result = []
    try:
        if recursive:
            for root, _, files in os.walk(path):
                for f in files:
                    full = os.path.join(root, f)
                    disp = os.path.relpath(full, path) if path_type == "relative" else full
                    result.append({
                        "filename": f,
                        "path": disp,
                        "type": os.path.splitext(f)[1] or "无扩展名",
                        "abs_path": full
                    })
        else:
            for item in os.listdir(path):
                full = os.path.join(path, item)
                if os.path.isfile(full):
                    disp = os.path.relpath(full, path) if path_type == "relative" else full
                    result.append({
                        "filename": item,
                        "path": disp,
                        "type": os.path.splitext(item)[1] or "无扩展名",
                        "abs_path": full
                    })
    except Exception as e:
        raise e
    return result


# ======================== 模板导出 ========================

def generate_template(template_type):
    """生成模板Excel并返回文件路径"""
    wb = Workbook()
    ws = wb.active
    if template_type == "folder":
        ws.append(["文件夹名称"])
        ws.append(["项目A/2024/Q1"])
        ws.append(["项目A/2025"])
        ws.append(["项目B/财务/发票"])
    elif template_type == "file":
        ws.append(["文件名称"])
        ws.append(["报告1"])
        ws.append(["报告2"])
        ws.append(["报告3"])
    elif template_type == "sheet":
        ws.append(["分表名称"])
        ws.append(["一月数据"])
        ws.append(["二月数据"])
        ws.append(["三月数据"])
    elif template_type == "rename":
        ws.append(["原文件名", "新文件名"])
        ws.append(["文件1.docx", "文件2.docx"])
    elif template_type == "mapping":
        ws.append(["子文件夹A", "子文件夹B", "子文件夹C"])
        ws.append(["文件1.docx", "文件2.xlsx", "文件3.pdf"])
        ws.append(["文件4.txt", "", ""])
    else:
        ws.append(["请根据实际需求填写"])
    return wb
