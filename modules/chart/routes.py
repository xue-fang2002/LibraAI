"""
图表模块路由
"""
from flask import request, render_template, jsonify, current_app, g, url_for, Response
from . import bp
from .services import parse_excel, df_to_json_data, is_allowed_file
from .ai_chart import render_spec, parse_chart_spec, build_chart_prompt
from core.auth import login_required
from core.response import fail, ok
import os


def _safe_err(prefix, e):
    """异常文案门控：只在 debug 下带原文（与 core/exceptions.py 保持一致）。

原先 `fail(f'出图失败：{e}')` 会把服务器绝对路径、字体名、
    依赖版本等一并回给浏览器，详情应只落在服务端日志里。
    """
    try:
        _debug = bool(current_app.debug)
    except Exception:
        _debug = False
    import traceback as _tb
    _tb.print_exc()
    return f'{prefix}：{e}' if _debug else f'{prefix}，请查看服务端日志'

@bp.route('/')
@login_required
def index():
    """图表工具主页"""
    return render_template('chart/index.html', 
                          page_title='图表工具',
                          module_id='chart')

@bp.route('/upload', methods=['POST'])
@login_required
def upload():
    """
    接收上传的 Excel/CSV，解析并返回数据 JSON
    """
    if 'file' not in request.files:
        return fail('没有上传文件')
    
    file = request.files['file']
    if file.filename == '':
        return fail('未选择文件')
    
    if not is_allowed_file(file.filename):
        return fail('不支持的文件格式，请上传 .xlsx, .xls 或 .csv')
    
    df, error = parse_excel(file)
    if error:
        return fail(error)
    
    # 转换为前端数据
    result = df_to_json_data(df)
    
    # 添加文件名信息
    result['filename'] = file.filename
    
    return ok(data=result)


# ============================================================
# AI 图表引擎（独立通道，不耦合 ECharts 手动工具）
# ============================================================
@bp.route('/ai/render', methods=['POST'])
@login_required
def ai_render():
    """
    接收结构化图表 spec，用 Matplotlib 服务端出 PNG 返回。
    body: {"spec": {...}, "theme": "quantum", "dark": false}
    """
    payload = request.get_json(silent=True) or {}
    spec = payload.get('spec')
    theme = payload.get('theme') or 'quantum'
    dark = bool(payload.get('dark', False))
    if not spec:
        return fail('缺少 spec')
    try:
        png = render_spec(spec, theme=theme, dark=dark)
    except Exception as e:
        return fail(_safe_err('出图失败', e))
    return Response(png, mimetype='image/png')


@bp.route('/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    """
    自然语言 -> 结构化 spec。
    body: {"text": "...", "theme": "quantum"}
    """
    payload = request.get_json(silent=True) or {}
    text = (payload.get('text') or '').strip()
    theme = payload.get('theme') or 'quantum'
    if not text:
        return fail('请输入自然语言描述')

    try:
        from modules.ai_center.internal.qa import llm_generate
    except Exception as e:
        return fail(_safe_err('AI 后端不可用', e))

    prompt = build_chart_prompt(text)
    raw = llm_generate(prompt, max_tokens=700, temperature=0.2)
    if not raw:
        return fail('模型未响应，请检查 AI 中心后端配置')

    spec = parse_chart_spec(raw)
    if not spec or not isinstance(spec, dict):
        return fail('模型未返回可解析的图表结构', extra={'raw': raw[:600]})
    spec['_theme'] = theme
    return ok(data={'spec': spec, 'raw': raw[:600]})