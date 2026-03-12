import asyncio
from contextlib import AsyncExitStack 
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPManager:
    def __init__(self):
        self.session = None
        self.exit_stack = None

    async def connect_to_server(self, command: str, args: list = None):

        if isinstance(command, list):
            real_args = command
            real_command = "npx"
        else:
            real_command = command
            real_args = args if args is not None else []

        print(f"[*] 最终确认启动参数:")
        print(f"    - Command: {real_command}")
        print(f"    - Args: {real_args}")

        try:
            server_params = StdioServerParameters(
                command=str(real_command),
                args=list(real_args),
                env=None
            )


            self.exit_stack = AsyncExitStack()

            client_lowlevel = await self.exit_stack.enter_async_context(stdio_client(server_params))
            read, write = client_lowlevel

            self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()

            print(f"[*] MCP 服务器连接成功！")

        except Exception as e:
            print(f"[!] MCPManager 连接失败详细原因: {str(e)}")
            if self.exit_stack:
                await self.exit_stack.aclose()
            raise e

    async def get_tool_schemas(self):
        if not self.session:
            return []
        result = await self.session.list_tools()
        openai_tools = []
        for tool in result.tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
            })
        return openai_tools

    async def call_tool(self, name: str, args: dict):
        if not self.session:
            return "错误：MCP 未连接"
        result = await self.session.call_tool(name, args)
        if hasattr(result, 'content') and len(result.content) > 0:
            return result.content[0].text
        return str(result)
