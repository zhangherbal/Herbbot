import os
from .handler import get_weibo_hot_search

_cur_dir = os.path.dirname(__file__)
with open(os.path.join(_cur_dir, "readme.md"), "r", encoding="utf-8") as f:
    _description = f.read().strip()

SKILL = {
    "schema": {
        "type": "function",
        "function": {
            "name": "get_weibo_hot_search",
            "description": _description
        }
    },
    "handler": get_weibo_hot_search
}