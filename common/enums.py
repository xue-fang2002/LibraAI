"""通用枚举定义。"""
from enum import Enum


class PermissionLevel(str, Enum):
    PUBLIC = "public"       # 所有人可见
    LOGIN = "login"         # 登录用户可见
    ADMIN = "admin"         # 仅管理员可见


class AITaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReadingStatus(str, Enum):
    READING = "reading"
    FINISHED = "finished"
    WISHLIST = "wishlist"
    DROPPED = "dropped"


class AIMode(str, Enum):
    OFF = "off"
    LIGHT = "light"
    FULL = "full"
