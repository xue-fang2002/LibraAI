"""Common 公共层：DTO、枚举、通用工具函数。"""
from .utils import (
    get_client_ip,
    sanitize_text,
    validate_account,
    compute_file_hash,
    is_safe_path,
    get_upload_save_path,
    resolve_file_path,
    extract_original_filename,
    lighten_color,
    allowed_file,
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    BASE_DIR,
    UPLOAD_FOLDER,
)
from .enums import (
    PermissionLevel,
    AITaskStatus,
    ReadingStatus,
)
from .dto import (
    PaginationDTO,
    ApiResult,
)

__all__ = [
    "get_client_ip", "sanitize_text", "validate_account", "compute_file_hash",
    "is_safe_path", "get_upload_save_path", "resolve_file_path",
    "extract_original_filename", "lighten_color", "allowed_file",
    "ALLOWED_EXTENSIONS", "MAX_FILE_SIZE", "BASE_DIR", "UPLOAD_FOLDER",
    "PermissionLevel", "AITaskStatus", "ReadingStatus",
    "PaginationDTO", "ApiResult",
]
