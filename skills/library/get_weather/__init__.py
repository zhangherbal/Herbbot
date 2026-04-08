import os
from .handler import get_weather

_cur_dir = os.path.dirname(__file__)
with open(os.path.join(_cur_dir, "readme.md"), "r", encoding="utf-8") as f:
    _description = f.read().strip()

SKILL = {
    "schema": {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": _description,
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，如：北京、日照"}
                },
                "required": ["city"]
            }
        }
    },
    "handler": get_weather
}