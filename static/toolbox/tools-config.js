/**
 * 工具箱前端配置文件
 * 所有工具的说明文本、参数定义、文件限制均在此配置
 */
const TOOLBOX_CONFIG = {
  categories: [
    {
      id: "document",
      label: "文档处理",
      icon: "📄",
      color: "#ff6b6b"
    },
    {
      id: "image",
      label: "图片处理",
      icon: "🖼️",
      color: "#6d5dfc"
    },
    {
      id: "text",
      label: "文本工具",
      icon: "📝",
      color: "#51cf66"
    },
    {
      id: "recognition",
      label: "识别工具",
      icon: "🔍",
      color: "#fcc419"
    },
    {
      id: "calc",
      label: "计算换算",
      icon: "🧮",
      color: "#20c997"
    },
    {
      id: "archive",
      label: "压缩打包",
      icon: "📦",
      color: "#e8590c"
    },
    {
      id: "system",
      label: "系统工具",
      icon: "⚙️",
      color: "#845ef7"
    }
  ],

  // 二级分组：工具数上来后（document 已达 30+），一行 pill 排不下也没法扫读，
  // 故在一级分类内再分一组。每类的**第一个分组是默认分组**，未显式登记的工具都归它。
  groups: {
    document: [
      { id: "pdf", label: "PDF 处理" },
      { id: "office", label: "Word / PPT" },
      { id: "excel", label: "Excel 表格" },
      { id: "convert", label: "格式转换" }
    ],
    image: [
      { id: "basic", label: "基础处理" },
      { id: "design", label: "尺寸与版式" }
    ],
    text: [
      { id: "encode", label: "编码与生成" },
      { id: "zh", label: "中文处理" }
    ],
    recognition: [
      { id: "qr", label: "二维码" },
      { id: "ocr", label: "文字识别" }
    ],
    calc: [
      { id: "unit", label: "单位换算" },
      { id: "finance", label: "财务计算" },
      { id: "daily", label: "日常计算" }
    ],
    archive: [
      { id: "main", label: "打包与解压" }
    ],
    system: [
      { id: "file", label: "文件工具" },
      { id: "misc", label: "效率小工具" }
    ]
  },

  // 工具 → 二级分组。只需登记「不在默认分组」的工具，新增工具默认进第一个分组。
  toolGroups: {
    // 文档：Word / PPT
    document_word_tools: "office",
    document_word_template: "office",
    document_ppt_tools: "office",
    document_ppt_replace: "office",
    document_ppt_merge: "office",
    document_ppt_template: "office",
    // 文档：Excel
    document_excel_merge: "excel",
    document_excel_split: "excel",
    document_excel_convert: "excel",
    document_excel_clean: "excel",
    document_excel_columns: "excel",
    document_excel_stats: "excel",
    document_excel_replace: "excel",
    document_excel_match: "excel",
    document_excel_compare: "excel",
    document_excel_mask: "excel",
    document_excel_splitcol: "excel",
    document_excel_images: "excel",
    // 文档：格式转换
    document_convert: "convert",
    // 图片
    image_resize: "design",
    image_decorate: "design",
    image_gif: "design",
    // 文本
    text_zhconv: "zh",
    text_pinyin: "zh",
    text_stats: "zh",
    // 识别
    recognition_ocr_image: "ocr",
    recognition_ocr_screenshot: "ocr",
    recognition_ocr_pdf: "ocr",
    // 计算换算（全部显式登记，避免以后新增时进错组）
    calc_unit: "unit",
    calc_base: "unit",
    calc_loan: "finance",
    calc_invest: "finance",
    calc_social: "finance",
    calc_rmb: "finance",
    calc_bmi: "daily",
    calc_date: "daily",
    calc_expr: "daily",
    // 系统
    system_color_picker: "misc",
    system_clipboard: "misc"
  },

  tools: {
    // ==================== 图片处理 ====================
    "image_compress": {
      category: "image",
      label: "🗜️ 批量压缩",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "批量图片压缩",
        desc: "将图片批量压缩为 JPG 格式，可限制最大宽度。适用于网页展示、邮件发送、存储优化等场景。",
        tips: [
          "质量 90 以上肉眼几乎看不出差异，建议 75-90",
          "如果用于网页展示，建议宽度限制 1920px，质量 80",
          "GIF 动图会被转为静态 JPG",
          "原图小于限制宽度时不会放大，仅压缩质量"
        ],
        input: "JPG, PNG, WEBP, BMP, GIF",
        output: "ZIP 包（内含 JPG 文件）",
        caution: "压缩是不可逆操作，建议保留原图备份"
      },
      params: [
        { name: "quality", label: "压缩质量", type: "range", min: 1, max: 100, value: 85, unit: "%", tip: "建议 75-90，数值越小体积越小" },
        { name: "max_width", label: "最大宽度", type: "number", min: 100, max: 8000, value: 1920, unit: "px", tip: "超过此宽度将等比缩小" }
      ]
    },
    "image_convert": {
      category: "image",
      label: "🔄 格式转换",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "图片格式转换",
        desc: "将图片在 JPG、PNG、WEBP、BMP、ICO 等格式间互相转换。",
        tips: [
          "JPG 适合照片类图像，体积较小但不支持透明",
          "PNG 支持透明通道，适合图标和截图",
          "WEBP 体积更小，但部分旧软件可能不支持",
          "RGBA 转 JPG 时会自动丢弃透明通道（填充白色）"
        ],
        input: "JPG, PNG, WEBP, BMP, GIF, ICO",
        output: "指定格式的图片文件（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "format", label: "目标格式", type: "select", options: [
          {value:"PNG", label:"PNG - 无损+透明"},
          {value:"JPEG", label:"JPG - 有损压缩"},
          {value:"WEBP", label:"WEBP - 高压缩率"},
          {value:"BMP", label:"BMP - 无压缩"},
          {value:"ICO", label:"ICO - 图标格式"}
        ], value: "PNG", tip: "选择输出格式" }
      ]
    },
    "image_thumbnail": {
      category: "image",
      label: "🖼️ 缩略图",
      accept: "image/*",
      maxSize: 50,
      maxCount: 30,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "生成缩略图",
        desc: "保持原图比例生成小尺寸预览图，适合图库展示、列表预览。",
        tips: [
          "缩略图会保持原始宽高比，不会变形",
          "建议缩略图尺寸 128-512px",
          "输出文件名前缀为 thumb_"
        ],
        input: "JPG, PNG, WEBP, BMP, GIF",
        output: "ZIP 包（前缀 thumb_）",
        caution: ""
      },
      params: [
        { name: "size", label: "缩略图尺寸", type: "number", min: 32, max: 1024, value: 256, unit: "px", tip: "缩略图的最大边长" }
      ]
    },
    "image_edit": {
      category: "image",
      label: "✂️ 裁剪旋转",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "图片裁剪与旋转",
        desc: "对图片进行旋转、水平翻转、垂直翻转操作。",
        tips: [
          "旋转角度顺时针计算",
          "翻转操作不可逆，请保留原图"
        ],
        input: "JPG, PNG, WEBP, BMP",
        output: "处理后的图片（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"rotate", label:"旋转"},
          {value:"flip_h", label:"水平翻转"},
          {value:"flip_v", label:"垂直翻转"}
        ], value: "rotate", tip: "选择操作类型" },
        { name: "value", label: "旋转角度", type: "number", min: 0, max: 360, value: 90, unit: "°", tip: "仅旋转时有效", showWhen: {field:"action", value:"rotate"} }
      ]
    },
    "image_filter": {
      category: "image",
      label: "🎨 滤镜效果",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "图片滤镜",
        desc: "为图片添加模糊、锐化、边缘检测、浮雕、灰度等滤镜效果。",
        tips: [
          "模糊滤镜使用高斯模糊，半径 2px",
          "边缘检测可提取图片轮廓",
          "灰度会去除所有颜色信息"
        ],
        input: "JPG, PNG, WEBP, BMP",
        output: "处理后的图片（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "filter", label: "滤镜类型", type: "select", options: [
          {value:"blur", label:"🔮 高斯模糊"},
          {value:"sharpen", label:"⚡ 锐化"},
          {value:"edge", label:"🔲 边缘检测"},
          {value:"emboss", label:"🗿 浮雕"},
          {value:"contour", label:"📐 轮廓"},
          {value:"grayscale", label:"⚫ 灰度"}
        ], value: "blur", tip: "选择滤镜效果" }
      ]
    },
    "image_watermark": {
      category: "image",
      label: "💧 文字水印",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "文字水印",
        desc: "为图片添加自定义文字水印，支持透明度、位置调整。",
        tips: [
          "支持平铺满屏模式，适合防盗图",
          "建议水印颜色与背景形成对比"
        ],
        input: "JPG, PNG, WEBP, BMP",
        output: "带水印的图片",
        caution: "水印添加后不可逆"
      },
      params: [
        { name: "text", label: "水印文字", type: "text", value: "Sample", tip: "输入水印内容" },
        { name: "opacity", label: "透明度", type: "range", min: 10, max: 100, value: 50, unit: "%", tip: "数值越小越透明" }
      ]
    },
    "image_merge": {
      category: "image",
      label: "🧩 图片拼接",
      accept: "image/*",
      maxSize: 50,
      maxCount: 50,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "图片拼接",
        desc: "将多张图片横向或纵向拼接为一张长图。",
        tips: [
          "横向拼接适合对比展示",
          "纵向拼接适合长图/流程图",
          "可设置间隔像素和背景色"
        ],
        input: "JPG, PNG, WEBP, BMP",
        output: "拼接后的单张图片",
        caution: ""
      },
      params: [
        { name: "direction", label: "拼接方向", type: "select", options: [
          {value:"horizontal", label:"➡️ 横向"},
          {value:"vertical", label:"⬇️ 纵向"}
        ], value: "horizontal", tip: "" },
        { name: "gap", label: "间隔", type: "number", min: 0, max: 100, value: 0, unit: "px", tip: "图片之间的间距" }
      ]
    },
    "image_exif": {
      category: "image",
      label: "📷 EXIF 信息",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "EXIF 信息查看与清除",
        desc: "查看图片的拍摄信息（相机型号、GPS、时间等），并可一键清除隐私数据。",
        tips: [
          "只有 JPG/TIFF 格式可能包含 EXIF",
          "清除 EXIF 可保护隐私，减小体积"
        ],
        input: "JPG, TIFF",
        output: "EXIF 报告 或 清除后的图片",
        caution: "清除 EXIF 后无法恢复"
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"view", label:"👁️ 查看"},
          {value:"clear", label:"🧹 清除"}
        ], value: "view", tip: "" }
      ]
    },
    "image_remove_bg": {
      category: "image",
      label: "✨ AI 抠图",
      accept: "image/*",
      maxSize: 20,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "AI 智能抠图",
        desc: "使用 AI 模型自动去除图片背景，输出透明 PNG。",
        tips: [
          "主体清晰的图片效果最佳",
          "模型文件较大，首次使用可能需要加载"
        ],
        input: "JPG, PNG, WEBP",
        output: "透明背景 PNG",
        caution: "需要服务器安装 rembg 依赖"
      },
      params: []
    },

    // ==================== 文档处理 ====================
    "document_pdf_merge": {
      category: "document",
      label: "📑 PDF 合并",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 50,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 合并",
        desc: "将多个 PDF 文件按指定顺序合并为一个文件。",
        tips: [
          "拖拽文件可调整合并顺序",
          "合并后保留原文件的书签信息"
        ],
        input: "PDF",
        output: "合并后的单个 PDF",
        caution: ""
      },
      params: []
    },
    "document_pdf_split": {
      category: "document",
      label: "✂️ PDF 拆分",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 1,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 拆分",
        desc: "按页码范围将 PDF 拆分为多个文件。",
        tips: [
          "页码范围格式：1-3,5,8-10",
          "不填范围则按每页自动拆分"
        ],
        input: "单个 PDF",
        output: "多个 PDF（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "ranges", label: "页码范围", type: "text", value: "", placeholder: "例如: 1-3,5,8-10", tip: "留空则逐页拆分" }
      ]
    },
    "document_pdf_to_image": {
      category: "document",
      label: "🖼️ PDF 转图片",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 转图片",
        desc: "将 PDF 的每一页转换为高清图片。",
        tips: [
          "DPI 越高图片越清晰，体积也越大",
          "300 DPI 适合打印，150 DPI 适合屏幕阅读"
        ],
        input: "PDF",
        output: "ZIP 包（内含 PNG/JPG）",
        caution: ""
      },
      params: [
        { name: "dpi", label: "分辨率", type: "select", options: [
          {value:72, label:"72 DPI - 屏幕"},
          {value:150, label:"150 DPI - 电子文档"},
          {value:300, label:"300 DPI - 打印"}
        ], value: 150, tip: "" }
      ]
    },
    "document_pdf_extract_text": {
      category: "document",
      label: "📄 提取文字",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 文字提取",
        desc: "从原生可复制 PDF 中提取纯文本内容。",
        tips: [
          "仅对文本型 PDF 有效",
          "扫描版 PDF 请使用 OCR 工具"
        ],
        input: "文本型 PDF",
        output: "TXT 文本文件",
        caution: ""
      },
      params: []
    },
    "document_pdf_extract_image": {
      category: "document",
      label: "🖼️ 提取图片",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 内嵌图片提取",
        desc: "导出 PDF 文件中内嵌的所有图片资源。",
        tips: [
          "自动过滤小于 5KB 的图标",
          "保留原始图片格式和质量"
        ],
        input: "PDF",
        output: "ZIP 包（内含提取的图片）",
        caution: ""
      },
      params: []
    },
    "document_pdf_rotate": {
      category: "document",
      label: "🔄 页面旋转",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 页面旋转",
        desc: "将 PDF 的指定页面旋转 90°/180°/270°。",
        tips: [
          "可指定页码范围，不填则全部旋转"
        ],
        input: "PDF",
        output: "旋转后的 PDF",
        caution: ""
      },
      params: [
        { name: "angle", label: "旋转角度", type: "select", options: [
          {value:90, label:"90° 顺时针"},
          {value:180, label:"180°"},
          {value:270, label:"270° 顺时针 / 90° 逆时针"}
        ], value: 90, tip: "" }
      ]
    },
    "document_pdf_watermark": {
      category: "document",
      label: "💧 PDF 水印",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 文字水印",
        desc: "为 PDF 添加页眉页脚文字水印。",
        tips: [
          "支持奇偶页不同水印",
          "水印会覆盖在内容之上"
        ],
        input: "PDF",
        output: "带水印的 PDF",
        caution: "水印添加后难以去除"
      },
      params: [
        { name: "text", label: "水印文字", type: "text", value: "Confidential", tip: "" }
      ]
    },
    "document_word_tools": {
      category: "document",
      label: "📝 Word 工具",
      accept: ".docx",
      maxSize: 50,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Word 批量处理",
        desc: "Word 文档的批量替换、合并、转 PDF。",
        tips: [
          "替换支持正则表达式",
          "合并时可选保留或清除格式"
        ],
        input: "DOCX",
        output: "DOCX 或 PDF",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"replace", label:"🔁 批量替换"},
          {value:"merge", label:"📑 合并文档"},
          {value:"to_pdf", label:"📄 转 PDF"}
        ], value: "replace", tip: "" },
        { name: "find_text", label: "查找文字", type: "text", value: "", placeholder: "要被替换掉的文字", tip: "仅「批量替换」时需要", showWhen: {field:"action", value:"replace"} },
        { name: "replace_text", label: "替换为", type: "text", value: "", placeholder: "新的文字", tip: "仅「批量替换」时需要", showWhen: {field:"action", value:"replace"} }
      ]
    },
    "document_ppt_tools": {
      category: "document",
      label: "🎬 PPT 工具",
      accept: ".pptx",
      maxSize: 50,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PPT 批量处理",
        desc: "提取 PPT 中的图片和文字，或转换为 PDF。",
        tips: [
          "提取图片保留原始分辨率",
          "文字提取按幻灯片分页"
        ],
        input: "PPTX",
        output: "ZIP 包 或 PDF",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"extract_images", label:"🖼️ 提取图片"},
          {value:"extract_text", label:"📄 提取文字"},
          {value:"to_pdf", label:"📄 转 PDF"}
        ], value: "extract_images", tip: "" }
      ]
    },

    "document_excel_merge": {
      category: "document",
      label: "🧩 Excel 合并",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 30,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 合并",
        desc: "把多个结构相同的表格纵向拼接成一个文件，列自动对齐补空。",
        tips: [
          "多个文件将按上传顺序上下拼接",
          "默认读取每个文件的第一个工作表"
        ],
        input: "xlsx / csv / tsv",
        output: "合并后的单个 xlsx/csv",
        caution: ""
      },
      params: [
{ name: "sheet", label: "工作表名（留空=第一个表）", type: "text", value: "", placeholder: "例如: Sheet1", tip: "" },
{ name: "out_format", label: "输出格式", type: "select", options: [{value:"xlsx",label:"xlsx"},{value:"csv",label:"csv"}], value: "xlsx", tip: "" }
      ]
    },

    "document_excel_split": {
      category: "document",
      label: "✂️ Excel 拆分",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 拆分",
        desc: "按工作表或按某列取值，把一个大表拆成多个文件。",
        tips: [
          "按列拆分：该列每个不同取值生成一个文件",
          "按工作表拆分：每个 sheet 存成独立文件"
        ],
        input: "xlsx / csv / tsv",
        output: "多个文件（ZIP 打包）",
        caution: ""
      },
      params: [
{ name: "mode", label: "拆分方式", type: "select", options: [{value:"sheet",label:"按工作表"},{value:"column",label:"按列值"}], value: "sheet", tip: "" },
{ name: "column", label: "按哪一列拆分", type: "text", value: "", placeholder: "仅「按列值」时需要", tip: "", showWhen: {field:"mode", value:"column"} },
{ name: "out_format", label: "输出格式", type: "select", options: [{value:"xlsx",label:"xlsx"},{value:"csv",label:"csv"}], value: "xlsx", tip: "" }
      ]
    },

    "document_excel_convert": {
      category: "document",
      label: "🔄 Excel 格式转换",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 格式转换",
        desc: "xlsx / csv / tsv 三种表格格式互转。",
        tips: [
          "csv 默认 UTF-8 编码，Excel 打开不乱码",
          "可批量转换多个文件"
        ],
        input: "xlsx / csv / tsv",
        output: "转换后的同目录文件",
        caution: ""
      },
      params: [
{ name: "out_format", label: "目标格式", type: "select", options: [{value:"xlsx",label:"xlsx"},{value:"csv",label:"csv"},{value:"tsv",label:"tsv"}], value: "xlsx", tip: "" }
      ]
    },

    "document_excel_clean": {
      category: "document",
      label: "🧹 Excel 数据清洗",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 数据清洗",
        desc: "去重、去首尾空格、空值填充或删除。",
        tips: [
          "去重会删除完全相同的整行",
          "空值处理可填充固定值或删除含空值的行"
        ],
        input: "xlsx / csv / tsv",
        output: "清洗后的 xlsx",
        caution: ""
      },
      params: [
{ name: "dedup", label: "删除重复行", type: "checkbox", value: true, tip: "" },
{ name: "trim", label: "去除文本首尾空格", type: "checkbox", value: true, tip: "" },
{ name: "na_action", label: "空值处理", type: "select", options: [{value:"none",label:"不处理"},{value:"fill",label:"填充固定值"},{value:"drop",label:"删除含空值行"}], value: "none", tip: "" },
{ name: "na_fill", label: "填充值", type: "text", value: "0", placeholder: "空值填充为", tip: "", showWhen: {field:"na_action", value:"fill"} }
      ]
    },

    "document_excel_columns": {
      category: "document",
      label: "🗂️ Excel 提取/删除列",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 提取/删除列",
        desc: "只保留指定列，或删除指定列。",
        tips: [
          "列名用逗号分隔，例如: 姓名,电话",
          "保留模式：仅输出填写的列"
        ],
        input: "xlsx / csv / tsv",
        output: "处理后的 xlsx",
        caution: ""
      },
      params: [
{ name: "action", label: "操作", type: "select", options: [{value:"keep",label:"保留这些列"},{value:"drop",label:"删除这些列"}], value: "keep", tip: "" },
{ name: "columns", label: "列名（逗号分隔）", type: "text", value: "", placeholder: "例如: 姓名,电话,地址", tip: "" }
      ]
    },

    "document_excel_stats": {
      category: "document",
      label: "📊 Excel 汇总统计",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 汇总统计",
        desc: "按某列分组，对数值列做求和/计数/均值/最大/最小。",
        tips: [
          "分组列可以是文本（如部门）",
          "只统计数值型列"
        ],
        input: "xlsx / csv / tsv",
        output: "汇总结果 xlsx",
        caution: ""
      },
      params: [
{ name: "group_by", label: "分组列", type: "text", value: "", placeholder: "例如: 部门", tip: "" },
{ name: "agg", label: "统计方式", type: "select", options: [{value:"sum",label:"求和"},{value:"count",label:"计数"},{value:"mean",label:"均值"},{value:"max",label:"最大值"},{value:"min",label:"最小值"}], value: "sum", tip: "" }
      ]
    },

    "document_excel_replace": {
      category: "document",
      label: "🔁 Excel 批量替换",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 批量替换",
        desc: "对单元格文本做查找替换（支持整表或指定列）。",
        tips: [
          "留空列名=替换整张表",
          "替换区分大小写"
        ],
        input: "xlsx / csv / tsv",
        output: "替换后的 xlsx",
        caution: ""
      },
      params: [
{ name: "find_text", label: "查找文字", type: "text", value: "", placeholder: "要被替换掉的文字", tip: "" },
{ name: "replace_text", label: "替换为", type: "text", value: "", placeholder: "新的文字", tip: "" },
{ name: "columns", label: "仅这些列（留空=整表）", type: "text", value: "", placeholder: "例如: 状态,备注", tip: "" }
      ]
    },

    "document_pdf_compress": {
      category: "document",
      label: "🗜️ PDF 压缩",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 压缩",
        desc: "通过垃圾回收、压缩流与图片降采样减小 PDF 体积。",
        tips: [
          "强度越高体积越小、清晰度越低",
          "light 档不做图片降采样，画质无损"
        ],
        input: "PDF",
        output: "压缩后的 PDF",
        caution: ""
      },
      params: [
{ name: "level", label: "压缩强度", type: "select", options: [{value:"light",label:"轻度（无损）"},{value:"medium",label:"中等"},{value:"strong",label:"强力"}], value: "medium", tip: "" }
      ]
    },

    "document_pdf_pagenumber": {
      category: "document",
      label: "🔢 PDF 加页码",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 加页码",
        desc: "在每页底部居中加页码，可选页眉文字。",
        tips: [
          "页码格式支持 {page} 和 {total}",
          "如: 第 {page} 页 / 共 {total} 页"
        ],
        input: "PDF",
        output: "加页码后的 PDF",
        caution: ""
      },
      params: [
{ name: "format", label: "页码格式", type: "text", value: "{page}/{total}", placeholder: "{page}/{total}", tip: "" },
{ name: "header", label: "页眉文字（可留空）", type: "text", value: "", placeholder: "例如: 机密文件", tip: "" },
{ name: "start", label: "起始页码", type: "number", value: 1, tip: "" }
      ]
    },

    "document_pdf_encrypt": {
      category: "document",
      label: "🔐 PDF 加密/解密",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 加密/解密",
        desc: "给 PDF 加密码保护，或解除密码。",
        tips: [
          "加密后打开需要输入密码",
          "解密需提供原密码"
        ],
        input: "PDF",
        output: "加密/解密后的 PDF",
        caution: ""
      },
      params: [
{ name: "action", label: "操作", type: "select", options: [{value:"encrypt",label:"🔒 加密"},{value:"decrypt",label:"🔓 解密"}], value: "encrypt", tip: "" },
{ name: "password", label: "密码", type: "text", value: "", placeholder: "设置或输入密码", tip: "" }
      ]
    },

    "document_pdf_to_word": {
      category: "document",
      label: "📄 PDF 转 Word",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 转 Word",
        desc: "提取 PDF 每页文本，重组为 Word 文档。",
        tips: [
          "文本型转换，不保留复杂排版/表格/图片",
          "适合文字型 PDF（如扫描件需先 OCR）"
        ],
        input: "PDF",
        output: "Word (.docx)",
        caution: ""
      }
    },

    "document_word_template": {
      category: "document",
      label: "🧾 Word 模板填充",
      accept: ".docx,.xlsx,.csv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Word 模板填充",
        desc: "占位符 {{字段}} 批量替换，数据来自 Excel 表。",
        tips: [
          "上传 1 个 .docx 模板 + 1 个数据表",
          "数据表第一行为字段名，每行生成一个文档"
        ],
        input: "docx 模板 + 数据表",
        output: "多个 Word 文档（ZIP）",
        caution: ""
      },
      params: [
{ name: "name_field", label: "用作文件名的列（留空=行号）", type: "text", value: "", placeholder: "例如: 姓名", tip: "" }
      ]
    },

    "document_ppt_replace": {
      category: "document",
      label: "🔁 PPT 批量替换",
      accept: ".pptx",
      maxSize: 50,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PPT 批量替换",
        desc: "对每个幻灯片的文本框做查找替换。",
        tips: [
          "可批量处理多个 PPT",
          "替换会保留原文字格式"
        ],
        input: "PPTX",
        output: "替换后的 PPTX",
        caution: ""
      },
      params: [
{ name: "find_text", label: "查找文字", type: "text", value: "", placeholder: "要被替换掉的文字", tip: "" },
{ name: "replace_text", label: "替换为", type: "text", value: "", placeholder: "新的文字", tip: "" }
      ]
    },

    "document_ppt_merge": {
      category: "document",
      label: "📑 PPT 合并",
      accept: ".pptx",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PPT 合并",
        desc: "把多个 PPT 合并为一个。",
        tips: [
          "保留文字与形状",
          "内嵌图片可能丢失，请合并后检查"
        ],
        input: "多个 PPTX",
        output: "合并后的单个 PPTX",
        caution: ""
      }
    },

    "document_ppt_template": {
      category: "document",
      label: "🎨 PPT 模板生成",
      accept: ".pptx,.xlsx,.csv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PPT 模板生成",
        desc: "占位符 {{字段}} 批量替换，按 Excel 每行生成一个 PPT。",
        tips: [
          "上传 1 个 .pptx 模板 + 1 个数据表",
          "数据表第一行为字段名"
        ],
        input: "pptx 模板 + 数据表",
        output: "多个 PPT（ZIP）",
        caution: ""
      },
      params: [
{ name: "name_field", label: "用作文件名的列（留空=行号）", type: "text", value: "", placeholder: "例如: 姓名", tip: "" }
      ]
    },

    // ==================== 文本工具（纯前端+后端标准库） ====================
    "text_json_fmt": {
      category: "text",
      label: "📋 JSON 格式化",
      accept: null,
      needUpload: false,
      help: {
        title: "JSON 格式化与压缩",
        desc: "将杂乱的 JSON 文本格式化为易读形式，或压缩为一行。",
        tips: [
          "格式化会自动校验 JSON 语法",
          "压缩后体积更小，适合网络传输"
        ],
        input: "JSON 文本",
        output: "格式化/压缩后的 JSON",
        caution: ""
      },
      params: [
        { name: "mode", label: "模式", type: "select", options: [
          {value:"format", label:"✨ 格式化（美化）"},
          {value:"compress", label:"🗜️ 压缩（单行）"}
        ], value: "format", tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "粘贴 JSON 文本到此处..."
    },
    "text_codec": {
      category: "text",
      label: "🔐 编解码工具",
      accept: null,
      needUpload: false,
      help: {
        title: "文本编解码",
        desc: "Base64、URL、HTML 实体编解码转换。",
        tips: [
          "Base64 常用于数据传输和嵌入",
          "URL 编码会将中文转为 %XX 格式"
        ],
        input: "任意文本",
        output: "编码/解码后的文本",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"base64_encode", label:"Base64 编码"},
          {value:"base64_decode", label:"Base64 解码"},
          {value:"url_encode", label:"URL 编码"},
          {value:"url_decode", label:"URL 解码"},
          {value:"html_encode", label:"HTML 实体编码"},
          {value:"html_decode", label:"HTML 实体解码"}
        ], value: "base64_encode", tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "输入需要编解码的文本..."
    },
    "text_regex": {
      category: "text",
      label: "🔍 正则测试",
      accept: null,
      needUpload: false,
      help: {
        title: "正则表达式测试",
        desc: "实时测试正则表达式匹配结果，显示捕获组。",
        tips: [
          "支持忽略大小写和多行模式",
          "鼠标悬停匹配结果可查看位置"
        ],
        input: "待匹配文本 + 正则表达式",
        output: "匹配结果列表",
        caution: ""
      },
      params: [
        { name: "pattern", label: "正则表达式", type: "text", value: "", placeholder: "输入正则...", tip: "" },
        { name: "ignore_case", label: "忽略大小写", type: "checkbox", value: false, tip: "" },
        { name: "multiline", label: "多行模式", type: "checkbox", value: false, tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "输入待匹配的文本..."
    },
    "text_timestamp": {
      category: "text",
      label: "⏰ 时间转换",
      accept: null,
      needUpload: false,
      help: {
        title: "时间戳与日期互转",
        desc: "在 Unix 时间戳和可读日期时间之间转换，自动识别秒/毫秒/微秒。",
        tips: [
          "自动识别秒级(10位)、毫秒级(13位)、微秒级(16位)时间戳",
          "日期格式：YYYY-MM-DD HH:MM:SS"
        ],
        input: "时间戳 或 日期字符串",
        output: "对应的日期 或 时间戳",
        caution: ""
      },
      params: [
        { name: "action", label: "方向", type: "select", options: [
          {value:"ts_to_date", label:"时间戳 → 日期"},
          {value:"date_to_ts", label:"日期 → 时间戳"}
        ], value: "ts_to_date", tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "输入时间戳，例如 1699123456..."
    },
    "text_diff": {
      category: "text",
      label: "📑 文本对比",
      accept: null,
      needUpload: false,
      help: {
        title: "文本差异对比",
        desc: "对比两段文本的差异，以 Git diff 风格展示。",
        tips: [
          "绿色行表示新增内容",
          "红色行表示删除内容",
          "支持大段文本对比"
        ],
        input: "文本 A 和 文本 B",
        output: "差异结果（Unified Diff）",
        caution: ""
      },
      params: [],
      hasTextArea: true,
      textAreaPlaceholder: "文本 A（左侧）...",
      hasSecondTextArea: true,
      secondTextAreaPlaceholder: "文本 B（右侧）..."
    },
    "text_password": {
      category: "text",
      label: "🔑 密码生成",
      accept: null,
      needUpload: false,
      help: {
        title: "随机密码生成器",
        desc: "生成高强度随机密码，支持自定义长度和字符集。",
        tips: [
          "建议长度至少 12 位",
          "包含符号的密码安全性更高"
        ],
        input: "无",
        output: "随机密码列表",
        caution: "请妥善保存生成的密码"
      },
      params: [
        { name: "length", label: "长度", type: "number", min: 4, max: 64, value: 16, unit: "位", tip: "" },
        { name: "upper", label: "大写字母", type: "checkbox", value: true, tip: "" },
        { name: "lower", label: "小写字母", type: "checkbox", value: true, tip: "" },
        { name: "digit", label: "数字", type: "checkbox", value: true, tip: "" },
        { name: "symbol", label: "特殊符号", type: "checkbox", value: false, tip: "" },
        { name: "count", label: "生成数量", type: "number", min: 1, max: 50, value: 5, unit: "个", tip: "" }
      ]
    },
    "text_uuid": {
      category: "text",
      label: "🆔 UUID 生成",
      accept: null,
      needUpload: false,
      help: {
        title: "UUID 批量生成",
        desc: "生成标准 UUID v4 随机标识符。",
        tips: [
          "UUID 全局唯一，适合作为数据库主键",
          "每次刷新可生成新的 UUID"
        ],
        input: "无",
        output: "UUID 列表",
        caution: ""
      },
      params: [
        { name: "count", label: "生成数量", type: "number", min: 1, max: 100, value: 5, unit: "个", tip: "" }
      ]
    },
    "text_markdown": {
      category: "text",
      label: "📝 MD 预览",
      accept: null,
      needUpload: false,
      help: {
        title: "Markdown 实时预览",
        desc: "左侧编辑 Markdown，右侧实时渲染预览。",
        tips: [
          "支持标准 Markdown 语法",
          "包括表格、代码块、任务列表等"
        ],
        input: "Markdown 文本",
        output: "渲染后的 HTML 预览",
        caution: ""
      },
      params: [],
      hasTextArea: true,
      textAreaPlaceholder: "输入 Markdown...",
      hasMarkdownPreview: true,
      frontendOnly: true
    },

    // ==================== 识别工具 ====================
    "recognition_qrcode_gen": {
      category: "recognition",
      label: "🔳 二维码生成",
      accept: null,
      needUpload: false,
      help: {
        title: "二维码生成",
        desc: "将文本或 URL 转换为二维码图片。",
        tips: [
          "支持中文内容",
          "可设置容错级别和尺寸"
        ],
        input: "文本或 URL",
        output: "PNG 二维码图片",
        caution: ""
      },
      params: [
        { name: "text", label: "内容", type: "text", value: "https://", placeholder: "输入文本或网址...", tip: "" }
      ]
    },
    "recognition_qrcode_scan": {
      category: "recognition",
      label: "📷 二维码识别",
      accept: "image/*",
      maxSize: 20,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "二维码识别",
        desc: "识别图片中的二维码内容，支持批量识别。",
        tips: [
          "图片中的二维码需清晰可见",
          "支持同时识别多张图片"
        ],
        input: "包含二维码的图片",
        output: "二维码内容列表",
        caution: ""
      },
      params: []
    },
    "recognition_ocr_image": {
      category: "recognition",
      label: "📝 图片 OCR",
      accept: "image/*",
      maxSize: 20,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "图片文字识别",
        desc: "识别图片中的印刷体文字。",
        tips: [
          "文字越清晰识别率越高",
          "支持中英文混合"
        ],
        input: "包含文字的图片",
        output: "识别出的纯文本",
        caution: "需要服务器安装 OCR 引擎"
      },
      params: []
    },
    "recognition_ocr_screenshot": {
      category: "recognition",
      label: "📸 截图识别",
      accept: "image/*",
      // 粘贴的截图需要上传到服务端才能 OCR，因此这里保持 needUpload
      needUpload: true,
      help: {
        title: "截图文字识别",
        desc: "粘贴剪贴板中的截图，自动识别其中文字。",
        tips: [
          "使用 Ctrl+V 粘贴截图",
          "支持从 QQ/微信/浏览器直接粘贴"
        ],
        input: "剪贴板截图",
        output: "识别文本",
        caution: "需要浏览器支持剪贴板 API"
      },
      params: [],
      hasPasteArea: true
    },
    "recognition_ocr_pdf": {
      category: "recognition",
      label: "📄 扫描 PDF OCR",
      accept: ".pdf",
      maxSize: 50,
      maxCount: 3,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "扫描版 PDF 文字识别",
        desc: "将扫描版 PDF 逐页转为图片后识别文字。",
        tips: [
          "仅对扫描版/图片型 PDF 有效",
          "文本型 PDF 请使用「提取文字」工具"
        ],
        input: "扫描版 PDF",
        output: "TXT 文本文件",
        caution: "处理时间较长，请耐心等待"
      },
      params: []
    },

    // ==================== 系统工具 ====================
    "system_rename": {
      category: "system",
      label: "📝 批量重命名",
      accept: null,
      needUpload: false,
      help: {
        title: "文件批量重命名",
        desc: "按规则批量生成重命名后的文件名列表。",
        tips: [
          "支持正则替换、序号填充、插入日期",
          "仅生成文件名列表，不实际修改服务器文件"
        ],
        input: "文件名列表（每行一个）",
        output: "重命名后的文件名列表",
        caution: ""
      },
      params: [
        { name: "pattern", label: "查找", type: "text", value: "", placeholder: "正则或文本...", tip: "" },
        { name: "replacement", label: "替换为", type: "text", value: "", placeholder: "替换内容...", tip: "" },
        { name: "prefix", label: "前缀", type: "text", value: "", tip: "" },
        { name: "suffix", label: "后缀", type: "text", value: "", tip: "" },
        { name: "numbering", label: "添加序号", type: "checkbox", value: false, tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "输入文件名，每行一个..."
    },
    // system_file_search（本机文件搜索）已按用户要求移除：
    // 它需要服务端遍历目录，与项目「上传 zip → 临时目录处理 → 打包下载」的安全模型冲突。
    "system_color_picker": {
      category: "system",
      label: "🎨 屏幕取色",
      accept: null,
      needUpload: false,
      help: {
        title: "屏幕取色器",
        desc: "上传图片后点击任意位置获取颜色值。",
        tips: [
          "支持 HEX、RGB、HSL 格式",
          "点击色块可复制颜色值"
        ],
        input: "图片",
        output: "颜色值",
        caution: ""
      },
      params: [],
      hasColorPicker: true,
      frontendOnly: true
    },
    "system_clipboard": {
      category: "system",
      label: "📋 剪贴板历史",
      accept: null,
      needUpload: false,
      help: {
        title: "剪贴板历史",
        desc: "记录最近复制的内容，方便回溯。",
        tips: [
          "仅在当前页面会话内有效",
          "刷新页面后记录会清空"
        ],
        input: "Ctrl+C 复制的内容",
        output: "历史记录列表",
        caution: "不会上传到服务器，纯本地存储"
      },
      params: [],
      frontendOnly: true,
      hasClipboard: true
    },

    // ==================== 文档处理 · PDF（新增） ====================
    "document_pdf_pages": {
      category: "document",
      label: "📐 页面管理",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 页面管理",
        desc: "提取 / 删除 / 重排页面，或裁掉四周白边。四类页面级操作集中在一个入口。",
        tips: [
          "页码范围写法：1-3,5,8-10（从 1 开始计数）",
          "「删除」是删掉填写的页，剩下的按原顺序保留",
          "「重排」必须写全所有页码，例如 4 页写 3,1,2,4",
          "裁剪单位是 pt（约 0.35mm），72pt = 1 英寸"
        ],
        input: "PDF",
        output: "处理后的 PDF",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"extract", label:"📤 提取页面"},
          {value:"delete", label:"🗑️ 删除页面"},
          {value:"reorder", label:"🔀 重排页面"},
          {value:"crop", label:"✂️ 裁剪边距"}
        ], value: "extract", tip: "" },
        { name: "pages", label: "页码范围", type: "text", value: "", placeholder: "例如: 1-3,5,8-10", tip: "提取/删除时必填", showWhen: {field:"action", values:["extract","delete"]} },
        { name: "order", label: "新顺序", type: "text", value: "", placeholder: "例如: 3,1,2", tip: "必须包含全部页码且不重复", showWhen: {field:"action", value:"reorder"} },
        { name: "margin", label: "裁掉边距(pt)", type: "number", min: 1, max: 300, value: 36, unit: "pt", tip: "72pt ≈ 1 英寸", showWhen: {field:"action", value:"crop"} }
      ]
    },
    "document_pdf_meta": {
      category: "document",
      label: "🏷️ 元数据",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 元数据读取 / 编辑",
        desc: "查看标题、作者、关键词、创建工具、加密状态；也可批量改写四个可写字段。",
        tips: [
          "「查看」直接显示文本报告，不产出文件",
          "「编辑」只改 标题/作者/主题/关键词，其余字段由格式本身决定"
        ],
        input: "PDF",
        output: "文本报告 或 修改后的 PDF",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"view", label:"👁️ 查看"},
          {value:"edit", label:"✏️ 编辑"}
        ], value: "view", tip: "" },
        { name: "title", label: "标题", type: "text", value: "", placeholder: "留空表示不改", tip: "", showWhen: {field:"action", value:"edit"} },
        { name: "author", label: "作者", type: "text", value: "", placeholder: "留空表示不改", tip: "", showWhen: {field:"action", value:"edit"} },
        { name: "subject", label: "主题", type: "text", value: "", placeholder: "留空表示不改", tip: "", showWhen: {field:"action", value:"edit"} },
        { name: "keywords", label: "关键词", type: "text", value: "", placeholder: "逗号分隔", tip: "", showWhen: {field:"action", value:"edit"} }
      ]
    },
    "document_pdf_to_excel": {
      category: "document",
      label: "📊 PDF 转 Excel",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 表格转 Excel",
        desc: "逐页提取 PDF 里的表格写入 xlsx。",
        tips: [
          "默认每页一个 sheet，勾选项可合并成一个",
          "仅对有表格线/规整排版的 PDF 有效",
          "扫描件、图片型 PDF 提取不到，需先 OCR"
        ],
        input: "含表格的 PDF",
        output: "xlsx",
        caution: "需要服务器安装 pdfplumber"
      },
      params: [
        { name: "merge", label: "所有页合并到一个 sheet", type: "checkbox", value: false, tip: "" }
      ]
    },
    "document_pdf_to_ppt": {
      category: "document",
      label: "🎬 PDF 转 PPT",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 5,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 转 PPT",
        desc: "把每页渲染成图片插入幻灯片，页码顺序不变，方便搬进汇报稿。",
        tips: [
          "输出是图片型 PPT，文字不可再编辑",
          "只做版式搬运，不做矢量还原"
        ],
        input: "PDF",
        output: "pptx",
        caution: ""
      },
      params: [
        { name: "dpi", label: "渲染分辨率", type: "select", options: [
          {value:96, label:"96 DPI - 体积小"},
          {value:150, label:"150 DPI - 推荐"},
          {value:300, label:"300 DPI - 高清（体积大）"}
        ], value: 150, tip: "" }
      ]
    },
    "document_pdf_grayscale": {
      category: "document",
      label: "⚫ PDF 转灰度",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 转灰度",
        desc: "页面渲染为灰度图后重新合成，省墨打印 / 归档用。",
        tips: [
          "转换后不能再复制文字，请保留原文件",
          "彩色图表转灰度后层次会变弱，建议先看一页效果"
        ],
        input: "PDF",
        output: "灰度 PDF",
        caution: "不可逆，请保留原文件"
      },
      params: [
        { name: "dpi", label: "渲染分辨率", type: "select", options: [
          {value:150, label:"150 DPI - 推荐"},
          {value:300, label:"300 DPI - 高清"}
        ], value: 150, tip: "" }
      ]
    },
    "document_pdf_to_imagepdf": {
      category: "document",
      label: "🖼️ PDF 转纯图",
      accept: ".pdf",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 转纯图 PDF",
        desc: "每页渲染成位图后重新合成，丢弃文本层：定稿归档 / 防改动 / 修字体错乱。",
        tips: [
          "输出后文字不可复制、不可检索",
          "文件体积通常比原文件大"
        ],
        input: "PDF",
        output: "纯图 PDF",
        caution: "不可逆，务必保留原文件"
      },
      params: [
        { name: "dpi", label: "渲染分辨率", type: "select", options: [
          {value:150, label:"150 DPI - 推荐"},
          {value:300, label:"300 DPI - 高清"}
        ], value: 150, tip: "" }
      ]
    },
    "document_pdf_stamp": {
      category: "document",
      label: "🔖 PDF 签章/插图",
      accept: ".pdf,.png,.jpg,.jpeg,.bmp",
      maxSize: 100,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "PDF 签章 / 插图 / 加文字",
        desc: "把签名图、公章或自定义文字按百分比位置盖到指定页面上。",
        tips: [
          "加图片：同时上传 PDF 和一张图片（PNG 透明底效果最好）",
          "位置用百分比：距左边 60 / 距顶部 75 大约是右下角",
          "页码范围留空表示每一页都盖"
        ],
        input: "PDF（+ 图片，选插图时）",
        output: "加章后的 PDF",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"image", label:"🖼️ 插入图片/签章"},
          {value:"text", label:"🔤 添加文字"}
        ], value: "image", tip: "" },
        { name: "text", label: "文字内容", type: "text", value: "", placeholder: "例如: 已审核", tip: "", showWhen: {field:"action", value:"text"} },
        { name: "pages", label: "页码范围（留空=全部）", type: "text", value: "", placeholder: "例如: 1,3-5", tip: "" },
        { name: "x", label: "距左边", type: "number", min: 0, max: 100, value: 60, unit: "%", tip: "" },
        { name: "y", label: "距顶部", type: "number", min: 0, max: 100, value: 75, unit: "%", tip: "" },
        { name: "width", label: "占页宽", type: "number", min: 1, max: 100, value: 25, unit: "%", tip: "" },
        { name: "fontsize", label: "字号", type: "number", min: 6, max: 96, value: 14, tip: "", showWhen: {field:"action", value:"text"} },
        { name: "color", label: "文字颜色", type: "text", value: "#C00000", placeholder: "#RRGGBB", tip: "", showWhen: {field:"action", value:"text"} },
        { name: "opacity", label: "不透明度", type: "number", min: 10, max: 100, value: 100, unit: "%", tip: "" }
      ]
    },

    // ==================== 文档处理 · Excel（新增） ====================
    "document_excel_match": {
      category: "document",
      label: "🔗 两表匹配",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 2,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 两表匹配补全（VLOOKUP）",
        desc: "用第二个表的数据去补全第一个表，按关联列匹配。",
        tips: [
          "**顺序很重要**：先选主表再选参照表，两个一起上传",
          "结果会多一列 _matched，标「否」的行就是没匹配上的",
          "「仅保留匹配上的」= 数据库里的 inner join"
        ],
        input: "主表 + 参照表（各 1 个）",
        output: "补全后的 xlsx",
        caution: ""
      },
      params: [
        { name: "key", label: "主表关联列", type: "text", value: "", placeholder: "例如: 工号", tip: "" },
        { name: "ref_key", label: "参照表关联列（留空=同名）", type: "text", value: "", placeholder: "例如: 员工编号", tip: "" },
        { name: "cols", label: "要带回的列（留空=全部）", type: "text", value: "", placeholder: "例如: 部门,手机号", tip: "" },
        { name: "how", label: "匹配方式", type: "select", options: [
          {value:"left", label:"保留主表全部行"},
          {value:"inner", label:"仅保留匹配上的行"}
        ], value: "left", tip: "" }
      ]
    },
    "document_excel_compare": {
      category: "document",
      label: "🔍 名单比对",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 2,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "两份名单比对",
        desc: "找出「仅在主表 / 仅在参照表 / 两边都有」，各存一个 sheet。",
        tips: [
          "查未提交名单：主表=应到名单，参照表=已交名单，看「仅在主表」",
          "两张表要有同名比对列（如都叫 姓名）"
        ],
        input: "两个名单表",
        output: "3 个 sheet 的 xlsx",
        caution: ""
      },
      params: [
        { name: "key", label: "比对列（留空=第一列）", type: "text", value: "", placeholder: "例如: 姓名", tip: "" },
        { name: "keep_cols", label: "一并带出的列（留空=全部）", type: "text", value: "", placeholder: "例如: 姓名,部门", tip: "" }
      ]
    },
    "document_excel_mask": {
      category: "document",
      label: "🕶️ 数据脱敏",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 数据脱敏",
        desc: "手机号 / 身份证 / 银行卡 / 姓名 / 邮箱按规则打码。",
        tips: [
          "auto 会按内容形态自动识别类型",
          "手机号保留前 3 后 4，身份证保留前 6 后 4",
          "姓名只保留姓，邮箱保留首字符和域名"
        ],
        input: "xlsx / csv / tsv",
        output: "脱敏后的 xlsx",
        caution: "脱敏不可逆，请保留原文件"
      },
      params: [
        { name: "rule", label: "脱敏规则", type: "select", options: [
          {value:"auto", label:"🤖 自动识别"},
          {value:"phone", label:"📱 手机号"},
          {value:"idcard", label:"🪪 身份证"},
          {value:"bank", label:"💳 银行卡"},
          {value:"name", label:"👤 姓名"},
          {value:"email", label:"📧 邮箱"},
          {value:"custom", label:"⚙️ 自定义保留位数"}
        ], value: "auto", tip: "" },
        { name: "columns", label: "仅处理这些列（留空=全部）", type: "text", value: "", placeholder: "例如: 手机号,身份证", tip: "" },
        { name: "keep_head", label: "保留前几位", type: "number", min: 0, max: 20, value: 0, tip: "", showWhen: {field:"rule", value:"custom"} },
        { name: "keep_tail", label: "保留后几位", type: "number", min: 0, max: 20, value: 0, tip: "", showWhen: {field:"rule", value:"custom"} },
        { name: "mask_char", label: "填充字符", type: "text", value: "*", tip: "" }
      ]
    },
    "document_excel_splitcol": {
      category: "document",
      label: "↔️ 拆列/合列",
      accept: ".xlsx,.csv,.tsv",
      maxSize: 50,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 拆列与合列",
        desc: "按分隔符把一列拆成多列，或把多个列拼成一列。",
        tips: [
          "拆分后新列命名为 原列名_1、原列名_2 …",
          "合列时分隔符可填任意字符串，包括空（直接拼接）"
        ],
        input: "xlsx / csv / tsv",
        output: "处理后的 xlsx",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"split", label:"➡️ 拆分一列"},
          {value:"merge", label:"⬅️ 合并多列"}
        ], value: "split", tip: "" },
        { name: "column", label: "要拆的列", type: "text", value: "", placeholder: "例如: 地址", tip: "", showWhen: {field:"action", value:"split"} },
        { name: "columns", label: "要合并的列（逗号分隔）", type: "text", value: "", placeholder: "例如: 省,市,区", tip: "", showWhen: {field:"action", value:"merge"} },
        { name: "sep", label: "分隔符", type: "text", value: ",", tip: "合并时的连接符 / 拆分时的切分符" },
        { name: "new_name", label: "合并后的列名", type: "text", value: "", placeholder: "留空=自动拼", tip: "", showWhen: {field:"action", value:"merge"} },
        { name: "keep_original", label: "保留原列", type: "checkbox", value: false, tip: "" }
      ]
    },
    "document_excel_images": {
      category: "document",
      label: "🖼️ 提取图片",
      accept: ".xlsx,.xlsm",
      maxSize: 50,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "Excel 内嵌图片提取",
        desc: "把工作簿里内嵌的所有图片资源原样导出。",
        tips: [
          "直接读取 xlsx 内部资源，浮动图片与批注背景一并取出",
          "csv 不含图片，无需使用本工具"
        ],
        input: "xlsx / xlsm",
        output: "图片文件（ZIP 打包）",
        caution: ""
      },
      params: []
    },

    // ==================== 文档处理 · 格式转换（聚合入口） ====================
    "document_convert": {
      category: "document",
      label: "🔄 格式转换",
      accept: ".pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.txt,.md,.markdown,.html,.htm,.csv,.tsv,.png,.jpg,.jpeg",
      maxSize: 100,
      maxCount: 20,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "文档格式转换（聚合入口）",
        desc: "选「源格式 + 目标格式」即可，覆盖 PDF / Office / 表格 / 文本 / 网页 / 图片之间的常用组合。",
        tips: [
          "源格式选 auto 即可，系统按扩展名判断",
          "转 PDF 需要服务器有 Microsoft Office 或 LibreOffice",
          "PDF → DOCX / TXT 是文本提取，不还原复杂排版",
          "不支持的组合会明确报错，不会返回空文件"
        ],
        input: "PDF / Office / 表格 / 文本 / 网页 / 图片",
        output: "目标格式文件",
        caution: "转 PDF 依赖本机文档转换引擎"
      },
      params: [
        { name: "src", label: "源格式", type: "select", options: [
          {value:"auto", label:"🤖 自动判断"},
          {value:"pdf", label:"PDF"},
          {value:"docx", label:"DOCX"},
          {value:"xlsx", label:"XLSX"},
          {value:"pptx", label:"PPTX"},
          {value:"txt", label:"TXT"},
          {value:"md", label:"Markdown"},
          {value:"html", label:"HTML"},
          {value:"csv", label:"CSV / TSV"}
        ], value: "auto", tip: "" },
        { name: "dst", label: "目标格式", type: "select", options: [
          {value:"pdf", label:"📄 PDF"},
          {value:"docx", label:"📝 DOCX"},
          {value:"xlsx", label:"📊 XLSX"},
          {value:"csv", label:"📋 CSV"},
          {value:"txt", label:"🔤 TXT"},
          {value:"md", label:"📑 Markdown"},
          {value:"html", label:"🌐 HTML"},
          {value:"pptx", label:"🎬 PPTX（仅 PDF 源）"}
        ], value: "pdf", tip: "" }
      ]
    },

    // ==================== 图片处理（新增） ====================
    "image_resize": {
      category: "image",
      label: "📏 尺寸修改",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "图片尺寸修改",
        desc: "按宽度 / 高度 / 百分比缩放，或强制精确尺寸，并可改写 DPI。",
        tips: [
          "只填一边就是等比缩放，不会变形",
          "精确尺寸会拉伸，除非原图比例刚好一致",
          "投稿要求 300dpi 时，把 DPI 填 300 即可（像素不变）"
        ],
        input: "JPG, PNG, WEBP, BMP",
        output: "缩放后的图片（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "mode", label: "缩放方式", type: "select", options: [
          {value:"width", label:"↔️ 按宽度（等比）"},
          {value:"height", label:"↕️ 按高度（等比）"},
          {value:"percent", label:"📐 按比例"},
          {value:"exact", label:"🎯 精确尺寸（可能变形）"}
        ], value: "width", tip: "" },
        { name: "width", label: "宽度", type: "number", min: 1, max: 8000, value: 1920, unit: "px", tip: "", showWhen: {field:"mode", values:["width","exact"]} },
        { name: "height", label: "高度", type: "number", min: 1, max: 8000, value: 1080, unit: "px", tip: "", showWhen: {field:"mode", values:["height","exact"]} },
        { name: "percent", label: "比例", type: "number", min: 1, max: 500, value: 50, unit: "%", tip: "", showWhen: {field:"mode", value:"percent"} },
        { name: "dpi", label: "输出 DPI", type: "number", min: 0, max: 1200, value: 300, unit: "dpi", tip: "0 = 保持原值" }
      ]
    },
    "image_decorate": {
      category: "image",
      label: "🎀 版式装饰",
      accept: "image/*",
      maxSize: 50,
      maxCount: 20,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "图片版式装饰",
        desc: "圆角、加背景色、四周留白、平铺填充画布。都是「不改内容只改画布」。",
        tips: [
          "圆角会输出 PNG（保留透明）",
          "加背景适合把透明 PNG 转成白底图",
          "留白适合做卡片 / 打印出血"
        ],
        input: "JPG, PNG, WEBP",
        output: "处理后的图片（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"round", label:"🔘 圆角"},
          {value:"background", label:"🎨 加背景色"},
          {value:"padding", label:"⬜ 四周留白"},
          {value:"tile", label:"🧱 平铺填充"}
        ], value: "round", tip: "" },
        { name: "radius", label: "圆角半径", type: "number", min: 0, max: 500, value: 24, unit: "px", tip: "", showWhen: {field:"action", value:"round"} },
        { name: "color", label: "颜色", type: "text", value: "#ffffff", placeholder: "#RRGGBB 或 white", tip: "" },
        { name: "padding", label: "留白", type: "number", min: 0, max: 500, value: 40, unit: "px", tip: "", showWhen: {field:"action", value:"padding"} },
        { name: "canvas_w", label: "画布宽", type: "number", min: 1, max: 8000, value: 1920, unit: "px", tip: "", showWhen: {field:"action", value:"tile"} },
        { name: "canvas_h", label: "画布高", type: "number", min: 1, max: 8000, value: 1080, unit: "px", tip: "", showWhen: {field:"action", value:"tile"} }
      ]
    },
    "image_gif": {
      category: "image",
      label: "🎞️ GIF 拆分/合成",
      accept: "image/*",
      maxSize: 50,
      maxCount: 50,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "GIF 拆分与合成",
        desc: "把动图拆成逐帧 PNG，或把多张图片按顺序合成 GIF。",
        tips: [
          "合成时按**上传顺序**作为帧顺序",
          "每帧时长 300ms 大约是 3.3 帧/秒",
          "循环次数 0 表示无限循环"
        ],
        input: "GIF（拆分）或多张图片（合成）",
        output: "PNG 帧（ZIP）或 GIF",
        caution: ""
      },
      params: [
        { name: "action", label: "操作", type: "select", options: [
          {value:"split", label:"✂️ 拆分动图"},
          {value:"merge", label:"🧩 合成动图"}
        ], value: "split", tip: "" },
        { name: "duration", label: "每帧时长", type: "number", min: 20, max: 5000, value: 300, unit: "ms", tip: "", showWhen: {field:"action", value:"merge"} },
        { name: "loop", label: "循环次数（0=无限）", type: "number", min: 0, max: 100, value: 0, tip: "", showWhen: {field:"action", value:"merge"} }
      ]
    },

    // ==================== 文本工具（新增） ====================
    "text_zhconv": {
      category: "text",
      label: "🔤 简繁转换",
      accept: null,
      needUpload: false,
      help: {
        title: "简体 / 繁体互转",
        desc: "在简体、繁体（台湾）、繁体（香港）之间转换。",
        tips: [
          "按词转换，不是逐字替换，「软件」不会变成「软体」",
          "香港模式会保留部分港式用词"
        ],
        input: "中文文本",
        output: "转换后的文本",
        caution: "需要服务器安装 zhconv"
      },
      params: [
        { name: "target", label: "目标", type: "select", options: [
          {value:"zh-cn", label:"简体中文"},
          {value:"zh-tw", label:"繁体中文（台湾）"},
          {value:"zh-hk", label:"繁体中文（香港）"}
        ], value: "zh-cn", tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "输入要转换的中文文本..."
    },
    "text_pinyin": {
      category: "text",
      label: "🅿️ 汉字转拼音",
      accept: null,
      needUpload: false,
      help: {
        title: "汉字转拼音",
        desc: "把汉字转成拼音，可选带声调、不带声调或仅首字母。",
        tips: [
          "带声调：nǐ hǎo",
          "不带声调：ni hao",
          "首字母：n h（适合做索引）"
        ],
        input: "中文文本",
        output: "拼音",
        caution: "需要服务器安装 pypinyin"
      },
      params: [
        { name: "style", label: "拼音样式", type: "select", options: [
          {value:"tone", label:"🎵 带声调"},
          {value:"normal", label:"🔡 不带声调"},
          {value:"first", label:"🔤 仅首字母"}
        ], value: "tone", tip: "" },
        { name: "sep", label: "分隔符", type: "text", value: " ", placeholder: "默认空格", tip: "" }
      ],
      hasTextArea: true,
      textAreaPlaceholder: "输入汉字..."
    },
    "text_stats": {
      category: "text",
      label: "🔢 字数统计",
      accept: null,
      needUpload: false,
      help: {
        title: "字数统计",
        desc: "统计字符数、中文字数、英文单词数、行数、段落数。",
        tips: [
          "「不含空格」是很多投稿系统的口径",
          "中文字符按单字计数"
        ],
        input: "任意文本",
        output: "统计报告",
        caution: ""
      },
      params: [],
      hasTextArea: true,
      textAreaPlaceholder: "粘贴要统计的文本..."
    },

    // ==================== 压缩打包（新分类） ====================
    "archive_pack": {
      category: "archive",
      label: "🗜️ 打包压缩",
      accept: null,
      maxSize: 200,
      maxCount: 100,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "多文件打包",
        desc: "把上传的多个文件打成一个压缩包。",
        tips: [
          "zip 通用性最好，Windows 直接双击可开",
          "tar.gz 在 Linux / macOS 上更常见",
          "rar / 7z 需要服务器安装 7-Zip，暂不支持"
        ],
        input: "任意文件（可多传）",
        output: "zip / tar / tar.gz",
        caution: ""
      },
      params: [
        { name: "format", label: "打包格式", type: "select", options: [
          {value:"zip", label:"🗜️ zip（推荐）"},
          {value:"tar", label:"📦 tar（不压缩）"},
          {value:"tar.gz", label:"📦 tar.gz"}
        ], value: "zip", tip: "" },
        { name: "name", label: "压缩包名称", type: "text", value: "", placeholder: "留空=archive", tip: "" }
      ]
    },
    "archive_unpack": {
      category: "archive",
      label: "📤 解压",
      accept: ".zip,.tar,.tar.gz,.tgz,.rar,.7z",
      maxSize: 200,
      maxCount: 10,
      allowFolder: false,
      needUpload: true,
      help: {
        title: "压缩包解压",
        desc: "解压 zip / tar / tar.gz，解压出的文件重新打包供下载。",
        tips: [
          "勾选「忽略目录层级」可把所有文件平铺到一层",
          "rar / 7z 需要服务器安装 7-Zip，暂不支持",
          "解压结果仍在服务器临时目录，下载后请及时取走"
        ],
        input: "zip / tar / tar.gz",
        output: "解压后的文件（ZIP 打包）",
        caution: ""
      },
      params: [
        { name: "flatten", label: "忽略目录层级（全部平铺）", type: "checkbox", value: false, tip: "" }
      ]
    },

    // ==================== 计算换算（新分类，全部纯前端） ====================
    "calc_unit": {
      category: "calc",
      label: "📐 单位换算",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "单位换算",
        desc: "长度、重量、温度、面积、体积、字节、时间等常用单位互转。",
        tips: ["所有计算在浏览器本地完成，不上传服务器"],
        input: "数值 + 单位",
        output: "换算结果（全部同类单位）",
        caution: ""
      },
      params: []
    },
    "calc_base": {
      category: "calc",
      label: "🔢 进制转换",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "进制转换",
        desc: "二进制 / 八进制 / 十进制 / 十六进制互转，支持 2-36 任意进制。",
        tips: ["输入带前缀也能识别：0x、0b、0o"],
        input: "数字 + 原进制",
        output: "各进制结果",
        caution: ""
      },
      params: []
    },
    "calc_loan": {
      category: "calc",
      label: "🏠 房贷计算",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "房贷计算",
        desc: "等额本息 / 等额本金两种还款方式的月供、总利息与还款计划。",
        tips: [
          "等额本息：每月还款额固定，前期利息占比高",
          "等额本金：每月递减，总利息更少但前期压力大"
        ],
        input: "贷款金额、年限、利率",
        output: "月供 / 总利息 / 前 12 期计划",
        caution: "结果为估算，实际以银行合同为准"
      },
      params: []
    },
    "calc_invest": {
      category: "calc",
      label: "📈 投资收益",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "投资收益计算",
        desc: "一次性投入的复利终值，或按月定投的累计本息。",
        tips: ["定投按月末投入、月复利估算"],
        input: "金额、年化收益率、年限",
        output: "终值 / 本金 / 收益",
        caution: "仅为数学估算，不构成投资建议"
      },
      params: []
    },
    "calc_social": {
      category: "calc",
      label: "🧾 五险一金",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "五险一金计算",
        desc: "按缴费基数与个人/单位比例，算出个人与单位各自承担金额。",
        tips: [
          "各地比例与基数上下限不同，默认值仅作示例",
          "比例可直接修改"
        ],
        input: "缴费基数 + 各项比例",
        output: "个人 / 单位明细与合计",
        caution: "比例以当地社保政策为准"
      },
      params: []
    },
    "calc_rmb": {
      category: "calc",
      label: "💴 人民币大写",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "数字转人民币大写",
        desc: "把金额转成财务报销用的中文大写，如 1234.56 → 壹仟贰佰叁拾肆元伍角陆分。",
        tips: ["支持到「万亿」量级", "负数、零、整都有正确处理"],
        input: "金额数字",
        output: "人民币大写",
        caution: ""
      },
      params: []
    },
    "calc_bmi": {
      category: "calc",
      label: "⚖️ BMI 计算",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "BMI 身体质量指数",
        desc: "按身高体重算 BMI，并给出中国成人参考区间。",
        tips: ["BMI 只是参考，不替代医学判断"],
        input: "身高(cm) + 体重(kg)",
        output: "BMI 值与体型参考",
        caution: "仅供参考，不构成医疗建议"
      },
      params: []
    },
    "calc_date": {
      category: "calc",
      label: "📅 日期计算",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "日期计算",
        desc: "算两个日期相隔天数，或从某天往前 / 往后推 N 天（含工作日推算）。",
        tips: ["工作日模式只跳过周六周日，不含法定节假日"],
        input: "日期",
        output: "天数 / 推算结果",
        caution: ""
      },
      params: []
    },
    "calc_expr": {
      category: "calc",
      label: "🧮 计算器",
      accept: null,
      needUpload: false,
      frontendOnly: true,
      custom: "calc",
      help: {
        title: "计算器",
        desc: "四则运算、括号、取余、幂运算，支持连续计算。",
        tips: [
          "支持 + - * / % ^ 与括号",
          "不使用 eval，算式在本地解析计算",
          "↑ 光标键可调出历史算式"
        ],
        input: "算式",
        output: "计算结果",
        caution: ""
      },
      params: []
    },

    // ==================== 系统工具（新增） ====================
    "system_hash": {
      category: "system",
      label: "🔐 文件哈希",
      accept: null,
      maxSize: 200,
      maxCount: 50,
      allowFolder: true,
      needUpload: true,
      help: {
        title: "文件哈希计算",
        desc: "计算上传文件的 MD5 / SHA1 / SHA256，用于校验文件完整性。",
        tips: [
          "只计算你上传的文件，不会读取服务器上其它目录",
          "下载大文件后比对官网给的哈希，可确认没被篡改"
        ],
        input: "任意文件（可多传）",
        output: "哈希值列表",
        caution: ""
      },
      params: [
        { name: "algo", label: "算法", type: "select", options: [
          {value:"md5", label:"MD5"},
          {value:"sha1", label:"SHA1"},
          {value:"sha256", label:"SHA256（推荐）"}
        ], value: "sha256", tip: "" }
      ]
    },
  }
};

// 辅助函数：按分类获取工具列表
function getToolsByCategory(catId) {
  return Object.entries(TOOLBOX_CONFIG.tools)
    .filter(([_, t]) => t.category === catId)
    .map(([id, t]) => ({ id, ...t }));
}
