class SkillManager:
    def __init__(self):
        self.skills = {}   # 存放函数对象: {"get_weather": <function...>}
        self.schemas = []  # 存放描述字典: [{"type": "function", ...}]

    def register(self, skill_dict):
        """注册技能"""
        name = skill_dict["schema"]["function"]["name"]
        self.skills[name] = skill_dict["handler"]
        self.schemas.append(skill_dict["schema"])

    def get_schemas(self):
        """返回给 LLM 看的工具列表"""
        return self.schemas

    def get_handler(self, name):
        """根据名称获取执行函数"""
        return self.skills.get(name)

    def get_tools_instruction(self):
        """
        核心思想：
        将所有 library 文件夹下 readme.md 的内容动态拼接成一段 Prompt
        """
        if not self.schemas:
            return "当前无可用外部工具。"

        instruction = "当前已加载的插件能力列表：\n"
        for schema in self.schemas:
            name = schema["function"]["name"]
            desc = schema["function"]["description"]  # 这里已经是读取 readme 后的内容
            instruction += f"- 【{name}】: {desc}\n"
        return instruction
