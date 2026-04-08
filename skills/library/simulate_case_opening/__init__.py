import os
from .handler import simulate_case_opening

_cur_dir = os.path.dirname(__file__)
with open(os.path.join(_cur_dir, "readme.md"), "r", encoding="utf-8") as f:
    _description = f.read().strip()

SKILL = {
    "schema": {
        "type": "function",
        "function": {
            "name": "simulate_case_opening",
            "description": _description,
            "parameters": {
                "type": "object",
                "properties": {
                    "case_name": {"type": "string", "description": "武器箱名称，默认为‘武器箱’"}
                }
            }
        }
    },
    "handler": simulate_case_opening
}