from skills.local_tools import LOCAL_SKILLS_MAP, SKILL_SCHEMAS

class SkillManager:
    def __init__(self):
        self.skills = LOCAL_SKILLS_MAP
        self.schemas = SKILL_SCHEMAS

    def get_schemas(self):
        return self.schemas

    def execute(self, name, args, user_id=None):
        if name in self.skills:
            try:
                # 使用 **args 将字典解包成函数的命名参数
                # 比如 args 是 {"city": "上海"}，解包后等同于 get_weather(city="上海")
                return self.skills[name](**args)
            except TypeError as e:
                return f"参数错误: 技能 {name} 无法接受这些参数 {args}"
            except Exception as e:
                return f"执行技能 {name} 时出错: {str(e)}"
        return f"本地技能 {name} 未定义"