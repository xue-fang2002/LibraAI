# ============================================================
# LibraAI 运行镜像
# ------------------------------------------------------------
# 默认只装核心依赖（requirements-core.txt），镜像小、构建快。
# 需要 AI 能力时：docker build --build-arg INSTALL_AI=true .
#   （会额外装 torch / faiss / transformers 等，镜像体积增加数 GB）
#
# 运行期数据（数据库 / 上传 / 向量 / 模型）全部走卷，见 docker-compose.yml。
# ============================================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 系统依赖：
#   tesseract-ocr(+eng/chi_sim) —— OCR 工具需要引擎本体，不只是 Python 包
#   libgl1 / libglib2.0-0       —— opencv-python 运行所需
#   fonts-noto-cjk              —— 图表、生成图片里的中文不出现方块
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-chi-sim \
        libgl1 \
        libglib2.0-0 \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-core.txt requirements.txt ./

# 核心依赖必定安装；INSTALL_AI=true 时再叠加 AI 全家桶
ARG INSTALL_AI=false
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-core.txt \
    && if [ "$INSTALL_AI" = "true" ]; then \
           pip install --no-cache-dir -r requirements.txt ; \
       fi

COPY . .

# 运行期目录预建并授权（数据库 / 上传 / 向量 / 模型 / 日志 / 临时）
# 说明：office_tools 的 Word/Excel/PPT 互转依赖 Windows COM，容器内不可用；
#       其余功能（PDF / 图片 / 表格 / 文本 / 归档）均正常。
RUN useradd -m -u 10001 appuser \
    && mkdir -p /app/data /app/uploads /app/vector_data \
                /app/ai_vector_store /app/models /app/logs /app/temp \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5005

VOLUME ["/app/data", "/app/uploads", "/app/vector_data", "/app/ai_vector_store", "/app/models"]

# 数据库落在 /app/data（挂卷），避免容器重建后数据丢失
ENV DB_PATH=/app/data/book_manager.db
# 登录失败锁定按「账号+IP」计数，默认不采信 X-Forwarded-For（可被伪造绕过）。
# 若容器前面挂了 Nginx 等反向代理，把它改成 1，否则保持 0。
ENV TRUST_PROXY=0

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5005/api/health', timeout=4)" || exit 1

CMD ["python", "app.py"]
