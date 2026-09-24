"""文档处理分类。

PDF 工具依赖 PyMuPDF（import fitz）；Word 依赖 python-docx；PPT 依赖 python-pptx。
其中「转 PDF」走 Office COM（Windows + 已装 Office），见 _common.office_to_pdf。
目录下每个 .py 就是一个工具，由 tools/registry.py 自动扫描发现。
"""
