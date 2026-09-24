"""验证 tools/ 目录化重构：registry 自动发现 + dispatch 双查回落。"""
import sys
sys.path.insert(0, r"D:\xianmufile")

from flask import Flask

app = Flask(__name__, root_path=r"D:\xianmufile\app")

checks = []


def chk(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- " + str(extra)) if extra and not cond else ""))


with app.app_context():
    import modules.toolbox.handlers as H
    from modules.toolbox.tools import registry as R
    from modules.toolbox.tools import _common as C

    print("=== 1. 共享 helper 别名生效（无循环导入）===")
    chk("_in_out 指向 _common.in_out", H._in_out is C.in_out)
    chk("_input_files 指向 _common.input_files", H._input_files is C.input_files)
    chk("_cjk_font 指向 _common.cjk_font", H._cjk_font is C.cjk_font)
    chk("_office_to_pdf 指向 _common.office_to_pdf", H._office_to_pdf is C.office_to_pdf)
    chk("_ocr_with_tesseract 指向 _common.ocr_with_tesseract", H._ocr_with_tesseract is C.ocr_with_tesseract)
    chk("get_temp_dir 可用", bool(H.get_temp_dir()))

    print("\n=== 2. registry 自动发现 text 分类 ===")
    tools = R.all_tools()
    print("   发现:", sorted(tools.keys()))
    for tid in ["text_json_fmt", "text_codec", "text_regex",
                "text_timestamp", "text_diff", "text_password", "text_uuid"]:
        chk(f"发现 {tid}", R.get(tid) is not None)
    # （全部工具已迁移完成，此处不再有「未迁移」样本）

    print("\n=== 3. dispatch 走新目录（7 个 text 工具真跑）===")

    def call(tid, params):
        r = H.dispatch("text", tid.replace("text_", ""), "__t_%s__" % tid, params)
        return r.get_json()

    d = call("text_json_fmt", {"text": '{"a":1,"b":[1,2]}', "mode": "format"})
    chk("json_fmt 格式化", d.get("code") == 200 and "\n" in str(d.get("data", {}).get("result", "")), d)

    d = call("text_codec", {"text": "hi", "action": "base64_encode"})
    chk("codec base64", d.get("code") == 200 and d["data"]["result"] == "aGk=", d)

    d = call("text_regex", {"text": "abc123def456", "pattern": r"\d+"})
    chk("regex 匹配 2 处", d.get("code") == 200 and d["data"]["count"] == 2, d)

    d = call("text_timestamp", {"action": "ts_to_date", "value": "1700000000"})
    chk("timestamp 转换", d.get("code") == 200 and "2023" in str(d["data"]["result"]), d)

    d = call("text_diff", {"text_a": "a\nb", "text_b": "a\nc"})
    chk("diff 产出差异", d.get("code") == 200 and "b" in d["data"]["diff"], d)

    d = call("text_password", {"length": 12, "count": 2})
    chk("password 生成 2 个", d.get("code") == 200 and len(d["data"]["passwords"]) == 2, d)

    d = call("text_uuid", {"count": 3})
    chk("uuid 生成 3 个", d.get("code") == 200 and len(d["data"]["uuids"]) == 3, d)

    print("\n=== 4. 回落旧表 + 未知工具 ===")
    # 全部工具迁移完成后，handler_map 已清空：这里改为验证「空输入有友好提示而非崩溃」
    # （旧 handle_pdf_merge 会抛 ValueError('cannot save with zero pages')，已在 tools/document 修复）
    j2 = H.dispatch("document", "pdf_merge", "__t_fallback__", {}).get_json()
    chk("pdf_merge 空输入返回友好 400 而非抛异常", j2.get("code") == 400, j2)

    # 任意工具都不应再回落到旧表：registry 应覆盖全部 31 个
    all_ids = ["image_compress", "image_convert", "image_thumbnail", "image_edit",
               "image_filter", "image_watermark", "image_merge", "image_exif",
               "image_remove_bg", "document_pdf_merge", "document_pdf_split",
               "document_pdf_to_image", "document_pdf_extract_text",
               "document_pdf_extract_image", "document_pdf_rotate",
               "document_pdf_watermark", "document_word_tools", "document_ppt_tools",
               "text_json_fmt", "text_codec", "text_regex", "text_timestamp",
               "text_diff", "text_password", "text_uuid",
               "recognition_qrcode_gen", "recognition_qrcode_scan",
               "recognition_ocr_image", "recognition_ocr_screenshot",
               "recognition_ocr_pdf", "system_rename"]
    missing = [t for t in all_ids if R.get(t) is None]
    chk("31 个工具全部被 registry 覆盖", not missing, missing)

    r3 = H.dispatch("image", "definitely_not_a_tool", "x", {})
    chk("未知工具仍返回 400", r3.get_json().get("code") == 400)

    # automation 仍走独立契约
    chk("automation 分类保留", callable(getattr(H, "handle_automation_run", None)))

    print("\n=== 5. image 分类（批2）自动发现 + 真跑压缩 ===")
    img_ids = ["image_compress", "image_convert", "image_thumbnail", "image_edit",
               "image_filter", "image_watermark", "image_merge", "image_exif",
               "image_remove_bg"]
    for tid in img_ids:
        chk(f"发现 {tid}", R.get(tid) is not None)

    # 造两张测试图，跑一次真实压缩，验证产物确实落在 out/
    import os
    from PIL import Image
    tdir = H.get_temp_dir()
    tid_dir = os.path.join(tdir, "__t_img__")
    os.makedirs(tid_dir, exist_ok=True)
    for n, color in (("a.png", (255, 0, 0)), ("b.png", (0, 128, 255))):
        Image.new("RGB", (600, 400), color).save(os.path.join(tid_dir, n))

    r = H.dispatch("image", "compress", "__t_img__", {"quality": 60, "max_width": 300})
    d = r.get_json()
    chk("image_compress 执行成功", d.get("code") == 200, d)
    out_files = os.listdir(os.path.join(tid_dir, "out")) if os.path.isdir(os.path.join(tid_dir, "out")) else []
    chk("压缩产物落在 out/（2 个 jpg）",
        len([f for f in out_files if f.endswith(".jpg")]) == 2, out_files)

    # image_exif（view 模式，纯读）也应走新链路
    r2 = H.dispatch("image", "exif", "__t_img__", {"action": "view"})
    chk("image_exif 执行成功", r2.get_json().get("code") == 200)

    print("\n=== 6. document 分类（批3）真跑 PDF 合并 ===")
    import fitz
    tdir2 = os.path.join(tdir, "__t_doc__")
    os.makedirs(tdir2, exist_ok=True)
    for n in ("a.pdf", "b.pdf"):
        doc = fitz.open()
        doc.new_page()
        doc.save(os.path.join(tdir2, n))
        doc.close()
    r = H.dispatch("document", "pdf_merge", "__t_doc__", {})
    d = r.get_json()
    chk("pdf_merge 合并 2 个 PDF", d.get("code") == 200, d)
    chk("产物 merged.pdf 存在",
        os.path.isfile(os.path.join(tdir2, "out", "merged.pdf")))

    # system_rename（纯规则，无需文件）
    r2 = H.dispatch("system", "rename", "__t_sys__",
                    {"text": "a.txt\nb.txt", "prefix": "pre_"})
    d2 = r2.get_json()
    chk("system_rename 加前缀", d2.get("code") == 200 and "pre_a.txt" in d2["data"]["result"], d2)

print()
fails = [n for n, c in checks if not c]
print("通过 %d / %d" % (len(checks) - len(fails), len(checks)))
if fails:
    print("失败项:", fails)
