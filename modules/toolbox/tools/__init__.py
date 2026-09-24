"""工具箱「工具目录」—— 目录即清单，文件即工具。

结构：
    tools/
    ├── _common.py        共享 helper（in_out / input_files / cjk_font / ocr / office_to_pdf）
    ├── registry.py       自动扫描发现
    └── <分类>/            image / document / text / recognition / system
        └── <工具>.py      每个文件一个工具，含 TOOL_ID + run()

新增工具：在对应分类目录丢一个 .py，定义 TOOL_ID 和 run(temp_id, params) 即可，
无需改动 registry / handlers / 路由。
"""
