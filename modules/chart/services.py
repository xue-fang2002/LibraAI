"""
数据读取、解析、列推荐服务
"""
import pandas as pd
import io
import re
from typing import Dict, List, Any, Tuple, Optional

ALLOWED_EXTENSIONS = {'.xlsx', '.xls', '.csv'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def is_allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    ext = '.' + filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in ALLOWED_EXTENSIONS

def parse_excel(file_storage) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    解析上传的文件，返回 DataFrame 和错误信息（如果有）
    只读取第一个 sheet
    """
    try:
        # 限流读取：边读边判断，超过上限立即拒绝，避免一次性读入内存造成 OOM
        file_bytes = b""
        while True:
            chunk = file_storage.read(1024 * 1024)
            if not chunk:
                break
            file_bytes += chunk
            if len(file_bytes) > MAX_FILE_SIZE:
                return None, f"文件过大，限制 {MAX_FILE_SIZE//1024//1024}MB"

        # 根据扩展名选择读取方式
        filename = file_storage.filename or 'temp'
        ext = '.' + filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        
        if ext == '.csv':
            # 尝试多种编码
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='gbk')
        else:
            # Excel 文件
            df = pd.read_excel(io.BytesIO(file_bytes), engine='openpyxl' if ext == '.xlsx' else 'xlrd')
        
        if df.empty:
            return None, "文件为空或无有效数据"
        
        # 清理列名（去除首尾空格）
        df.columns = [str(col).strip() for col in df.columns]
        
        return df, None
    except Exception as e:
        return None, f"解析文件失败：{str(e)}"

def auto_recommend_columns(df: pd.DataFrame) -> Dict[str, Any]:
    """
    自动推荐 X 轴和 Y 轴列
    策略：X轴选第一列（通常是索引或日期），Y轴选第一个数值型列
    返回包含 x_col, y_col, x_options, y_options 的字典
    """
    columns = df.columns.tolist()
    if not columns:
        return {'x_col': None, 'y_col': None, 'x_options': [], 'y_options': []}
    
    # 数值型列判断
    numeric_cols = []
    for col in columns:
        # 尝试转换为数值，如果成功则认为是数值列
        try:
            pd.to_numeric(df[col], errors='raise')
            numeric_cols.append(col)
        except:
            pass
    
    # 推荐 X 轴：第一列
    recommended_x = columns[0]
    # 推荐 Y 轴：第一个数值列（如果存在），否则取第二列（如果有）
    recommended_y = numeric_cols[0] if numeric_cols else (columns[1] if len(columns) > 1 else columns[0])
    
    return {
        'x_col': recommended_x,
        'y_col': recommended_y,
        'x_options': columns,
        'y_options': columns,
        'numeric_columns': numeric_cols
    }

def df_to_json_data(df: pd.DataFrame) -> Dict[str, Any]:
    """
    将 DataFrame 转换为前端可用的 JSON 格式
    - 列名列表
    - 数据数组（每行一个对象）
    - 推荐列信息
    """
    # 处理 NaN 值，转为 None
    data = df.where(pd.notnull(df), None).to_dict(orient='records')
    
    # 获取列名
    columns = df.columns.tolist()
    
    # 推荐列
    rec = auto_recommend_columns(df)
    
    return {
        'columns': columns,
        'data': data,
        'recommendations': {
            'x_col': rec['x_col'],
            'y_col': rec['y_col'],
            'x_options': rec['x_options'],
            'y_options': rec['y_options'],
            'numeric_columns': rec['numeric_columns']
        }
    }