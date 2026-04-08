import os
import importlib


def load_all_skills(manager):
    """扫描 library 文件夹并加载插件"""
    # 获取 skills/library 的绝对路径
    lib_path = os.path.join(os.path.dirname(__file__), "library")

    for folder in os.listdir(lib_path):
        folder_path = os.path.join(lib_path, folder)

        # 只要是文件夹且不是 __pycache__
        if os.path.isdir(folder_path) and not folder.startswith("__"):
            try:
                # 动态加载每个插件文件夹下的 __init__.py
                # 注意路径：从项目根目录开始应该是 skills.library.xxx
                module = importlib.import_module(f"skills.library.{folder}")

                if hasattr(module, "SKILL"):
                    manager.register(module.SKILL)
                    print(f"[*] [Plugin System] 已激活技能: {folder}")
            except Exception as e:
                print(f"[!] 插件 {folder} 加载失败: {e}")