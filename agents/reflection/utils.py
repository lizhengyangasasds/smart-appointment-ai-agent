"""
反思模块公共工具

- _make_json_safe: 递归把对象转成 json.dumps 可处理的类型
- _safe_dumps: json.dumps 的安全封装，自动处理 datetime / set / tuple 等
"""

from typing import Any
from datetime import datetime
import json


def _make_json_safe(obj: Any) -> Any:
    """递归把对象转成 json.dumps 可处理的类型

    处理 datetime / set / tuple / 自定义对象等不可直接序列化对象。
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_make_json_safe(v) for v in obj]
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return _make_json_safe(obj.__dict__)
    return str(obj)


def _safe_dumps(obj: Any, **kwargs) -> str:
    """json.dumps 的安全版本，自动处理 datetime 等不可序列化对象"""
    return json.dumps(_make_json_safe(obj), **kwargs)