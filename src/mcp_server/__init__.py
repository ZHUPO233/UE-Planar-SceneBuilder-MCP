"""UEMCP 自建 server 包。

`mcp-server` 这个命令行入口（见 pyproject.toml 的 [project.scripts]）走这里。
"""


def main() -> None:
    """启动 MCP server（stdio 传输）。

    为什么不是原来的 `print("Hello from mcp-server!")`：
      这个入口是给命令行/宿主"拉起 server 进程"用的，打印一行字等于什么都没发生
      —— 进程起来就退了，宿主那边只会看到连接立刻断开。
      真正的 server 实例在 `mcp_server.main` 模块里**模块级**创建
      （`mcp = MCPServer(...)`，见那边的注释：装饰器在定义时就执行，所以
      实例必须先于任何 `@mcp.tool()` 存在），这里 import 它再 `run()` 即可。

    `mcp.run()` 不带参数 = stdio：宿主（DSH）负责启动本进程并通过标准输入输出收发
    JSON-RPC（JSON 远程过程调用）报文。这也是 DSH profile 里 `mcp-mine` 的配置方式。
    """
    from mcp_server.main import mcp

    mcp.run()


if __name__ == "__main__":
    main()
