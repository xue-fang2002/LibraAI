"""识别工具分类。

二维码生成/识别依赖 OpenCV；OCR 三项依赖 pytesseract + Tesseract 引擎
（引擎路径可用环境变量 TESSERACT_CMD 指定），缺失时返回 503 + 安装提示。
目录下每个 .py 就是一个工具，由 tools/registry.py 自动扫描发现。
"""
