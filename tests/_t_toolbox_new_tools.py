"""P0/P2 新工具接口冒烟：登录 → 上传 → 逐个调用，断言 body.code==200。

覆盖：pdf_pages / pdf_meta / pdf_grayscale / pdf_to_imagepdf / pdf_to_excel(有表格时)
     excel_match / excel_compare / excel_mask / excel_splitcol / excel_images
     document_convert / archive_pack / archive_unpack / text_zhconv / text_pinyin / text_stats / system_hash
计算换算类是纯前端，不走接口，不在本脚本范围。
"""
import io
import os
import sys
import zipfile

import requests

BASE = "http://127.0.0.1:5005"
# 管理员密码：优先读环境变量（开源版初始密码是随机生成的），未设置时回落旧默认值
ADMIN_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD") or "admin123"
OK, FAIL = [], []


def check(name, resp, expect_files=True):
    try:
        data = resp.json()
    except Exception as e:
        FAIL.append(f"{name}: 非 JSON 响应 {e}")
        return None
    code = data.get("code")
    if code == 200:
        results = (data.get("data") or {}).get("results")
        if expect_files and results is not None:
            errs = [r for r in results if r.get("error")]
            if errs:
                FAIL.append(f"{name}: 结果含错误 {errs}")
                return data
        OK.append(name)
    else:
        FAIL.append(f"{name}: code={code} msg={data.get('msg')}")
    return data


def make_pdf(path, pages=3):
    import fitz
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i+1} 测试文档", fontsize=14)
    doc.save(path)
    doc.close()


def make_xlsx(path, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


def upload(s, paths):
    files = [("files", (os.path.basename(p), open(p, "rb"))) for p in paths]
    r = s.post(f"{BASE}/toolbox/api/upload", files=files,
               headers={"X-Requested-With": "XMLHttpRequest"})
    j = r.json()
    assert j["code"] == 200, j
    return j["data"]["temp_id"]


def call(s, tool, temp_id, params):
    cat = tool.split("_", 1)[0]
    return s.post(f"{BASE}/toolbox/api/{cat}/{tool}",
                  json={"temp_id": temp_id, "params": params},
                  headers={"X-Requested-With": "XMLHttpRequest"})


def main():
    tmp = os.path.dirname(os.path.abspath(__file__))
    pdf = os.path.join(tmp, "_t_smoke.pdf")
    xlsx_a = os.path.join(tmp, "_t_main.xlsx")
    xlsx_b = os.path.join(tmp, "_t_ref.xlsx")
    make_pdf(pdf)
    make_xlsx(xlsx_a, [["工号", "姓名"], ["E01", "张三"], ["E02", "李四"]])
    make_xlsx(xlsx_b, [["工号", "部门", "手机号"], ["E01", "研发", "13812345678"], ["E03", "市场", "13987654321"]])

    s = requests.Session()
    r = s.post(f"{BASE}/api/login", json={"account": "admin", "password": ADMIN_PASSWORD},
               headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.json().get("code") == 200, r.text

    # ---- PDF ----
    t = upload(s, [pdf])
    check("pdf_pages.extract", call(s, "document_pdf_pages", t, {"action": "extract", "pages": "1,3"}))
    t = upload(s, [pdf])
    check("pdf_pages.delete", call(s, "document_pdf_pages", t, {"action": "delete", "pages": "2"}))
    t = upload(s, [pdf])
    check("pdf_pages.reorder", call(s, "document_pdf_pages", t, {"action": "reorder", "order": "3,1,2"}))
    t = upload(s, [pdf])
    check("pdf_pages.crop", call(s, "document_pdf_pages", t, {"action": "crop", "margin": 20}))
    t = upload(s, [pdf])
    check("pdf_meta.view", call(s, "document_pdf_meta", t, {"action": "view"}))
    t = upload(s, [pdf])
    check("pdf_meta.edit", call(s, "document_pdf_meta", t, {"action": "edit", "title": "冒烟", "author": "tb"}))
    t = upload(s, [pdf])
    check("pdf_grayscale", call(s, "document_pdf_grayscale", t, {"dpi": 96}))
    t = upload(s, [pdf])
    check("pdf_to_imagepdf", call(s, "document_pdf_to_imagepdf", t, {"dpi": 96}))
    t = upload(s, [pdf])
    check("pdf_to_ppt", call(s, "document_pdf_to_ppt", t, {"dpi": 72}))
    t = upload(s, [pdf])
    check("pdf_stamp.text", call(s, "document_pdf_stamp", t, {
        "action": "text", "text": "已审核", "x": 60, "y": 80, "fontsize": 16, "color": "#C00000"}))
    t = upload(s, [pdf])
    check("document_convert.pdf2txt", call(s, "document_convert", t, {"dst": "txt"}))

    # ---- Excel（上传顺序：主表在前）----
    t = upload(s, [xlsx_a, xlsx_b])
    check("excel_match", call(s, "document_excel_match", t, {"key": "工号"}))
    t = upload(s, [xlsx_a, xlsx_b])
    check("excel_compare", call(s, "document_excel_compare", t, {"key": "工号"}))
    t = upload(s, [xlsx_b])
    check("excel_mask.auto", call(s, "document_excel_mask", t, {"rule": "auto"}))
    t = upload(s, [xlsx_b])
    check("excel_splitcol.split", call(s, "document_excel_splitcol", t, {
        "action": "split", "column": "手机号", "sep": "1"}))
    t = upload(s, [xlsx_a, xlsx_b])
    check("excel_images", call(s, "document_excel_images", t, {}), expect_files=False)

    # ---- 压缩 ----
    t = upload(s, [xlsx_a, xlsx_b])
    data = check("archive_pack", call(s, "archive_pack", t, {"format": "zip", "name": "smoke"}))
    # 用打出来的包回灌解压（从下载接口取）
    if data:
        rid = s.get(f"{BASE}/toolbox/api/download-zip/{t}",
                    headers={"X-Requested-With": "XMLHttpRequest"})
        zip_path = os.path.join(tmp, "_t_smoke_result.zip")
        open(zip_path, "wb").write(rid.content)
        t2 = upload(s, [zip_path])
        check("archive_unpack", call(s, "archive_unpack", t2, {}))

    # ---- 文本（无文件）----
    check("text_zhconv", call(s, "text_zhconv", None, {"text": "软件工程", "target": "zh-tw"}))
    check("text_pinyin", call(s, "text_pinyin", None, {"text": "你好", "style": "tone"}))
    check("text_stats", call(s, "text_stats", None, {"text": "你好世界 hello"}))
    t = upload(s, [pdf])
    check("system_hash", call(s, "system_hash", t, {"algo": "sha256"}))

    print(f"\n通过 {len(OK)} 项:")
    for n in OK:
        print("  ✅", n)
    if FAIL:
        print(f"失败 {len(FAIL)} 项:")
        for n in FAIL:
            print("  ❌", n)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
