# =============================================================================
# 把同一个 MCP server 以 **Streamable HTTP** 起起来 —— 给"只认 HTTP + URL"的客户端用
#
# 【为什么需要它】有些客户端（例如**豆包 PC** 的「技能·连接器·伙伴 → 新建自定义连接器」）
#   只让你填 **传输类型 + 服务器 URL**，**没有 command/args 可填** —— 也就是说它**起不了
#   本机的 stdio 进程**。而 `src/mcp_server/main.py` 末尾是 `mcp.run()`（**stdio**，给 DSH
#   这类"宿主直接拉起进程"的用法）。两边对不上，所以另开一个启动方式：
#   **import main.py 已经建好的那个 `mcp` 对象，换一种 transport 起** —— 工具定义一份都不重复，
#   `main.py` **一个字都不改**。
#
# 【怎么跑】
#   & "<仓库根>\.venv\Scripts\python.exe" "<仓库根>\serve_http.py"
#   & "<仓库根>\.venv\Scripts\python.exe" "<仓库根>\serve_http.py" --port 9000   # 换端口
#   客户端（豆包 PC）里填：
#       服务器名称 = scene-stage       传输类型 = HTTP      服务器 URL = http://127.0.0.1:8770/mcp
#
# ⚠ **端口不要用 8000**：那是**官方 Unreal MCP** 的端口（`main.py` 的 `OFFICIAL_URL`）。
# ⚠ 本 server **没有任何鉴权**：默认只绑 `127.0.0.1`，**别把它映射到公网**。
#   真要给云端客户端用，请在它前面加一层带令牌的网关，别裸暴露。
# ⚠ 依赖与 stdio 一样（`mcp[cli]` / `anyio`）—— 所以**先 `uv sync`**，否则这里起不来。
# ⚠ **尚未实测**（2026-09-24 新写）：第一次起来后，先在浏览器里打一下
#   `http://127.0.0.1:8770/mcp`（预期是 400/406 这类"这不是合法 MCP 请求"的应答 ——
#   有应答就说明端口通了），再去客户端里点连接。
# =============================================================================

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mcp_server.main as server  # noqa: E402  —— 复用同一套工具定义（不重复实现）


def main() -> int:
    """起 HTTP 服务；Ctrl+C 停。返回进程退出码。"""
    ap = argparse.ArgumentParser(
        description="把 UEMCP-SceneLayout 以 Streamable HTTP 起起来（给只认 URL 的客户端用）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认只绑本机；要给别人连才改成 0.0.0.0，且务必先想清楚鉴权）")
    ap.add_argument("--port", type=int, default=8770,
                    help="监听端口（默认 8770 —— 8000 被官方 Unreal MCP 占着）")
    ap.add_argument("--path", default="/mcp", help="HTTP 路径（默认 /mcp）")
    args = ap.parse_args()

    print(f"[serve_http] Streamable HTTP 已就绪： http://{args.host}:{args.port}{args.path}")
    print("[serve_http] 客户端里填：传输类型 = HTTP，服务器 URL = 上面这行；Ctrl+C 停止。")
    server.mcp.run(transport="streamable-http", host=args.host, port=args.port,
                   streamable_http_path=args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
