### 🛠️ 工具名称：set_reminder
**描述**：用于设置定时提醒、闹钟或倒计时任务。

#### 📥 参数说明：
| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `task` | string | 否 | 提醒的具体内容，默认为"任务" |
| `minutes` | float | 否 | 倒计时的分钟数 |
| `seconds` | float | 否 | 倒计时的秒数 |
| `duration_str` | string | 否 | 自然语言时间，如 "5分钟" 或 "1小时30分" |

#### 📝 使用示例 (JSON)：
**场景 1：简单分钟提醒**
```json
{
  "action": "tool",
  "thought": "用户需要5分钟后提醒泡面。",
  "tool_name": "set_reminder",
  "tool_args": {
    "task": "泡面好了",
    "minutes": 5
  }
}
