# THIRD PARTY NOTICES

本项目（LibraAI）包含并依赖若干第三方开源软件。本文件汇总这些组件的许可证与版权声明。

This project (LibraAI) bundles and depends on third-party open-source software.
This file summarizes the licenses and copyright notices for those components.

---

## A. 仓库内直接打包的前端库（vendored / bundled）

以下文件已提交进本仓库，位于 `static/` 与 `modules/chart/static/` 目录下。
其原始版权声明已在对应 `.min.js` 文件头部保留。

### 1. marked v12.0.2 — MIT License
- 路径 Path: `static/vendor/ai-md/marked.min.js`
- Copyright (c) 2011-2024, Christopher Jeffrey.
- 仓库 / Repo: https://github.com/markedjs/marked

```
MIT License

Copyright (c) 2011-2024, Christopher Jeffrey

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 2. highlight.js v11.9.0 — BSD 3-Clause License
- 路径 Path: `static/vendor/ai-md/highlight.min.js`
- (c) 2006-2023 highlight.js and other contributors.
- 仓库 / Repo: https://github.com/highlightjs/highlight.js

```
Copyright (c) 2006-2023, the highlight.js contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### 3. DOMPurify 3.1.6 — Apache-2.0 AND MPL-2.0 (dual licensed)
- 路径 Path: `static/vendor/ai-md/purify.min.js`
- (c) Cure53 and other contributors.
- 仓库 / Repo: https://github.com/cure53/DOMPurify
- 说明 / Note: Released under the Apache License 2.0 and the Mozilla Public
  License 2.0. 完整许可证文本见 / Full license texts:
  - https://www.apache.org/licenses/LICENSE-2.0
  - https://www.mozilla.org/MPL/2.0/

### 4. Apache ECharts — Apache License 2.0
- 路径 Path: `modules/chart/static/js/echarts.min.js`
- Licensed to the Apache Software Foundation (ASF) under the Apache License,
  Version 2.0.
- 官网 / Site: https://echarts.apache.org/
- 完整许可证 / Full license: https://www.apache.org/licenses/LICENSE-2.0

### 5. ECharts-GL — Apache License 2.0
- 路径 Path: `modules/chart/static/js/echarts-gl.min.js`
- 基于 Apache ECharts 构建，随 Apache ECharts 以 Apache License 2.0 分发。
- Built on Apache ECharts; distributed under the Apache License 2.0.

---

## B. Python 运行时依赖（通过 requirements.txt 安装）

以下开源包在运行时被使用。其完整许可证文本可在各包的分发渠道
（PyPI / 项目仓库）获取。

The following open-source packages are used at runtime. Their full license
texts are available in each package's distribution (PyPI / project repository).

| 包 Package            | 许可证 License            |
| --------------------- | ------------------------- |
| Flask                 | BSD-3-Clause              |
| Werkzeug              | BSD-3-Clause              |
| PyYAML                | MIT                       |
| requests              | Apache-2.0                |
| numpy                 | BSD-3-Clause              |
| Flask-Cors            | MIT                       |
| openpyxl              | MIT                       |
| python-docx           | MIT                       |
| python-pptx           | MIT                       |
| xlrd                  | BSD-3-Clause              |
| PyMuPDF (fitz)        | AGPL-3.0 (含商用授权选项) |
| pypdf                 | BSD-3-Clause              |
| PyPDF2                | BSD-3-Clause              |
| Pillow                | MIT-CMU / HPND            |
| opencv-python         | Apache-2.0                |
| matplotlib            | PSF License (BSD 兼容)    |
| pandas                | BSD-3-Clause              |
| pywin32               | PSF-2.0                   |
| pytesseract           | MIT                       |
| zhconv                | MIT                       |
| pypinyin              | MIT                       |
| lunar_python          | MIT                       |
| pdfplumber            | MIT                       |
| llama-cpp-python      | MIT                       |
| sentence-transformers | Apache-2.0                |
| faiss-cpu             | MIT                       |
| transformers          | Apache-2.0                |
| torch                 | BSD-3-Clause              |
| dashscope             | Apache-2.0                |

> 注意 / Note: PyMuPDF 以 AGPL-3.0 分发。若以触发 AGPL 义务的方式再分发本软件，
> 请向 PyMuPDF 作者获取商业授权，或移除 PDF 相关工具链。
> PyMuPDF is distributed under AGPL-3.0. If you redistribute in a way that
> triggers AGPL obligations, obtain a commercial license or remove the PDF tooling.

---

## C. 字体 / 模型权重 Fonts / Model weights

本仓库不提交任何专有字体或模型权重。AI 模型文件（如 GGUF）与大型二进制资源
已通过 `.gitignore` 排除，需由部署方在部署时自行提供。

No proprietary fonts or model weights are committed to this repository.
AI model files (e.g. GGUF) and large binary assets are excluded via
`.gitignore` and must be supplied by the operator at deployment time.
