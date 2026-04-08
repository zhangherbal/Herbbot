import os
from .handler import daily_quote

_cur_dir = os.path.dirname(__file__)
with open(os.path.join(_cur_dir, "readme.md"), "r", encoding="utf-8") as f:
    _description = f.read().strip()

SKILL = {
    "schema": {
        "type": "function",
        "function": {
            "name": "daily_quote",
            "description": _description
        }
    },
    "handler": daily_quote
}