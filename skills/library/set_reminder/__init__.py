import os
from .handler import set_reminder

_cur_dir = os.path.dirname(__file__)
with open(os.path.join(_cur_dir, "readme.md"), "r", encoding="utf-8") as f:
    _description = f.read().strip()

SKILL = {
    "schema": {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": _description,
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "多少分钟后提醒"},
                    "task": {"type": "string", "description": "提醒的具体内容"}
                },
                "required": ["minutes", "task"]
            }
        }
    },
    "handler": set_reminder
}