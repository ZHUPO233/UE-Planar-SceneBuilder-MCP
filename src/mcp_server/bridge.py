# Copyright (c) 2026 ZHUPO233
# =============================================================================
# bridge.py —— 平台层（**业务层与 MCP 之间的唯一接缝**）
#
# 为什么要这一层（2026-10-11 用户拍板的重构，方案「core/shell 两层」）：
#   `main.py` / `schemas.py` / `surfaces.py` 里装的全是**业务**，可它们过去直接从
#   `mcp` / `anyio` / `pydantic` 三个第三方包取东西 —— 于是这套业务**只能在装了这三个包的
#   Python 里跑**（本仓库的 `.venv` 就是），换到别处就起不来。
#   现在把所有"跟平台有关的东西"收进这一个文件：
#     · MCP 服务器外壳：`MCPServer` / `Context` / `ToolError`
#     · 数据模型基类：`BaseModel` / `Field`
#     · 官方连接：`Client` + 专用连接 task + 生命周期（`app_lifespan`）
#     · 链路状态与那道「动 UE 之前先确认链路」的闸（`_link_guard`）
#   插件（UE 编辑器内）只要提供**一个同名同接口的 bridge**，业务层一个字都不用改。
#
# ⚠ **本文件不许 import `main.py` / `schemas.py` / `surfaces.py`** —— 否则循环导入。
#   已核过：从这里搬进来的代码只用到下面这些依赖，一个都不缺。
# ⚠ Agent 是按**脚本方式**起 main.py 的（`python <仓库根>\src\mcp_server\main.py`），
#   那时 `__package__` 为空 ⇒ **那边**要用「先相对、失败再顶层」两段式导入本模块
#   （见 main.py 顶部那段说明；本文件自己是被导入的一方，不涉及）。
# =============================================================================

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio

# --- 数据模型基类：**再导出**给 schemas.py / surfaces.py / main.py ---------------------
# ⚠ 服务器侧就是真的 pydantic；插件侧那份 bridge 会换成最小替身（只实现用得到的那几样）。
from pydantic import BaseModel, Field                                 # noqa: F401

# --- MCP 外壳：**再导出**给 main.py / surfaces.py --------------------------------------
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

__all__ = [
    "BaseModel", "Field", "Client", "MCPServer", "Context", "ToolError", "anyio",
    "OFFICIAL_URL", "RETRY_SECONDS", "READY_TIMEOUT",
    "AppContext", "official_client", "app_lifespan",
    "mcp", "run", "_mark_link_state", "_link_guard",
]


# --- 官方地址（2026-10-11 从 main.py 的「常量」那一段搬来，定义**只有这一处**）----------
OFFICIAL_URL = "http://127.0.0.1:8000/mcp"


# --- [完工-01] 官方连接（由专用 task 持有，全程复用一条连接）---  【模块：ue_adapter】
# 实测 2026-09-23 12:07 起本会话持续服务：数十次官方调用，无 cancel-scope 崩溃。
#
# 为什么必须是"专用 task"（2026-09-23 实测定案，别改回去）：
#   `mcp.Client.__aenter__` 会把 streamable-http 传输压进一个 AsyncExitStack，
#   而这个传输内部会 `async with anyio.create_task_group()` —— 即进入一个取消域。
#   anyio 的铁律：取消域只能由进入它的那个 task 退出。旧写法在【工具调用的 task】里
#   建连接、却在【lifespan 的 task】里关，跨 task 退出必抛
#   RuntimeError: Attempted to exit a cancel scope that isn't the current tasks's
#   current cancel scope —— 它穿透 SDK 派发器的 task group，整个 server 进程退出。
#   修法：建和关都收进同一个 task（`_connection_loop`），取消域进出同源。
#   ⚠ 别再往 `asyncio.Lock` 上找：去掉锁之后它照样发生，真凶是上面这个"跨 task 关闭"。
#
RETRY_SECONDS = 3.0
"""连不上官方时的重试间隔（秒）。编辑器没开时会一直按这个间隔重试。"""

READY_TIMEOUT = 15.0
"""工具调用等"连接 task 首次表态"的上限（秒）；超时就抛 ToolError，而不是干等。"""


@dataclass
class AppContext:
    """服务器生命周期内共享的状态：一条官方连接 + 两个同步信号。

    所有权约定（**改这个文件时请守住**）：
      - `ue` 只有 `_connection_loop` 这一个 task 会写，其它 task 只读；
      - 谁都不许在工具调用的 task 里创建或关闭官方客户端 —— 那正是炸进程的原因。
    """

    ue: Any = None
    """官方 MCP 客户端。**只能由连接 task 创建和关闭**；工具调用只读它、用它发请求。"""

    ready: Any = None
    """连接 task 首次表态（连上 or 失败）后 set，让工具调用不必干等。"""

    stop: Any = None
    """要求连接 task 断开重连的信号。工具调用发现连接死了就 set 它。"""

    error: str = "尚未连上（连接 task 还在重试）"
    """最近一次连接失败的原因原文，拼进 ToolError 给用户看。"""


async def _connection_loop(app: AppContext) -> None:
    """专用连接 task：**唯一**允许创建/关闭官方客户端的地方。

    循环做三件事：
      ① 建连接（进入 `async with client`）；
      ② 把连接借出去 —— 挂在 `app.stop.wait()` 上不动；
      ③ 被要求重连（`app.stop` 被 set）或连不上时，退出 `async with`
         （**在同一个 task 里关**），歇一会儿回到 ①。

    ⚠ `app.stop` 每轮都换一个新的 `anyio.Event`：Event 一旦 set 就不会自动复位，
      不换的话第二轮会立刻又"被要求重连"，变成死循环。
    ⚠ `except Exception` 接不住 `CancelledError`（它是 `BaseException` 的子类）——
      这正是我们要的：关机时 task group 取消本 task，取消要能正常往外抛；
      而 `finally` 照样会执行。
    """
    log = logging.getLogger(__name__)
    while True:
        app.stop = anyio.Event()
        app.ue = None
        client = Client(OFFICIAL_URL)
        backoff = RETRY_SECONDS
        try:
            async with client:
                app.ue = client
                app.error = ""
                log.info("已连上官方 Unreal MCP：%s", OFFICIAL_URL)
                # 连上就**立刻**表态放行等待者，不能等退出 async with 才表态：
                # 下面要挂在这里把连接借出去、可能挂很久；若只在 finally 里 set，
                # 工具调用会一直等 ready 直到 READY_TIMEOUT 超时 —— 而日志里明明
                # 已经打了"已连上官方 Unreal MCP"（这个坑我实测踩到过）。
                if app.ready is not None:
                    app.ready.set()
                await app.stop.wait()
                backoff = 0.5
        except Exception as exc:
            app.error = f"{type(exc).__name__}: {exc}"
            log.warning("连不上官方 Unreal MCP（%s）：%s", OFFICIAL_URL, exc)
        finally:
            app.ue = None
            if app.ready is not None:
                app.ready.set()
        await anyio.sleep(backoff)


async def official_client(ctx: Context[AppContext], check_link: bool = True) -> Any:
    """取当前可用的官方客户端；没连上就抛 ToolError（给模型看人话，而不是崩栈）。

    连不上是**可预期**的（编辑器没开），所以这里一律转成 ToolError 原文说明，
    不把底层异常抛给协议层。

    ⚠ `check_link=True`（默认，2026-09-30 加）：**动 UE 之前必须先确认链路**（见 `_link_guard()`）。
      本函数是**所有碰 UE 的路径的唯一咽喉**，所以这道闸只在这一处实现。
      ⚠ 只有 `official_status()` 自己传 `False` —— 它就是那把"检查链路的钥匙"，不能自己拦自己。
    """
    if check_link:
        _link_guard()
    app = ctx.request_context.lifespan_context
    try:
        # 等连接 task 首次表态：server 刚起来时它可能还在握手，直接判失败会误报
        with anyio.fail_after(READY_TIMEOUT):
            await app.ready.wait()
    except TimeoutError:
        raise ToolError(
            f"等官方连接就绪超时（{READY_TIMEOUT:.0f} 秒）：{app.error or '连接仍无结果'}"
        ) from None
    if app.ue is None:
        raise ToolError(
            f"连不上官方 Unreal MCP（{OFFICIAL_URL}）："
            f"{app.error or '连接正在重建，请稍后重试'}。"
            f"请确认 ① UE 编辑器已打开 ② ModelContextProtocol 插件已启用 "
            f"③ 端口 8000 在监听。"
        )
    return app.ue


@asynccontextmanager
async def app_lifespan(server: MCPServer):
    """服务器启动时拉起连接 task，关闭时收回（不留孤儿 task）。

    ⚠ 两处必须"同源"，这是修复的全部要点：
      - task group 在**本 lifespan 的 task** 里进、也在本 task 里出；
      - 官方客户端的进/出都发生在**子 task**（`_connection_loop`）内部。
      两者互不越界，所以不会再出现"取消域跨 task 退出"的 RuntimeError。

    ⚠ 这里**不再吞异常**：旧版在 finally 里 `except BaseException` 把关闭连接的
      错误咽掉，结果只是把崩溃推迟到了 SDK 派发器的 task group（那里没人接），
      照样炸进程。现在关闭动作归连接 task 所有，它自己的收尾由 anyio 的
      task group 负责，不需要这里兜。
    """
    app = AppContext(ready=anyio.Event(), stop=anyio.Event())
    async with anyio.create_task_group() as tg:
        tg.start_soon(_connection_loop, app)
        try:
            yield app
        finally:
            tg.cancel_scope.cancel()


# 顺序很关键：app_lifespan 必须在 MCPServer(...) 之前定义；
# mcp 必须在任何 @mcp.tool() 之前存在（装饰器在"定义时"就执行）。
#
# 为什么没有 instructions=（2026-09-23 实测，别加回来）：本Agent不读 MCP 的
# instructions（dsh-mcp-client 的 connect() 返回值连赋值都没有）。
# 真正到模型眼前的是 tools/list（工具名 + 描述 + schema），
# 所以要求写在各工具的 docstring 里。
mcp = MCPServer("UEPLANE-SceneLayout", lifespan=app_lifespan)


def run() -> None:
    """启动 server（stdio：进程直连，由 Agent 负责起这个进程）。

    ⚠ 这是**启动区唯一入口** —— main.py 末尾那句 `if __name__ == "__main__":` 调它。
      `mcp.run()` 仍然直接可用（预检就是这么读 `mod["mcp"]` 的），这里只是给它一个名字。
    """
    mcp.run()


# --- 链路状态（2026-09-30 加：用户要求「每次要动 UE 必须检查连接状态」）--------------  【模块：gate】
# 记的是 `official_status()` **这次的返回值**，不是"调过没有" —— 只记"调过"等于没检查：
#   2026-09-30 实测踩到：开机后客户端与 MCP 断连（stdio 子进程没被拉起 / 会话工作区没恢复），
#   Agent 一上来就调工具，一路以**最难懂的方式**失败，排查花很久。
# `None` = 本进程里**还没检查过**；`True` = 上次检查**连上了**；`False` = 上次检查没连上（原因见 `_SESSION_LINK_ERR`）。
# ⚠ 2026-10-11 从 main.py 搬到本文件（它属于"链路/传输"，不属于业务）；判据一个字没改。
_SESSION_LINK_OK: bool | None = None
_SESSION_LINK_ERR: str = ""


def _mark_link_state(ok: bool, err: str = "") -> None:
    """记下**链路检查的结果**（只有 `official_status()` 会调它）。

    ⚠ 它与 `_mark_session_prereq` 的区别，就是这道闸的全部意义：
      那个只记"**调过**"，这个记"**通的还是不通的**" —— 见 `_link_guard()`。
    """
    global _SESSION_LINK_OK, _SESSION_LINK_ERR
    _SESSION_LINK_OK = bool(ok)
    _SESSION_LINK_ERR = str(err or "")


def _link_guard() -> None:
    """**动 UE 之前必须先确认链路**（2026-09-30 用户要求：「每次要动 UE 必须检查连接状态」）。

    谁调它：**所有碰 UE 的路径** —— 就一个咽喉：`official_client()`（官方客户端唯一的取用口，
    任何要 UE 的工具都得从它这儿过），所以**一处改、全覆盖**。

    判据是"**本进程里 `official_status()` 报过连上**"，不是"调过"：
      · 还没检查过 → 拒收，让它先调 `official_status()`；
      · 上次检查没连上 → 拒收，并把**当时的错误原文**摆出来（UE 没开 / 插件没启 / 8000 没听 /
        **客户端侧 stdio 子进程没起来或工作区没切到本包** —— 这时连工具都看不到）。
    ⚠ 为什么这么严：2026-09-30 实测，断连时一路以最难懂的方式失败；这条闸把"链路不通"
      提前到**第一个动作**就说清，代价只是"先调一次只读的 `official_status()`"。
    """
    if _SESSION_LINK_OK is True:
        return
    if _SESSION_LINK_OK is None:
        raise ToolError(
            "拒收：**动 UE 之前必须先确认链路** —— 本 server 进程里还没做过链路检查。\n"
            "请先调一次 `official_status()`（只读、一次就够）：它报协议版本与工具集数量，"
            "连不上时给**错误原文**；它通过之后，这一步以及后面每一步才放行。"
        )
    raise ToolError(
        "拒收：**链路没通**（本进程上一次 `official_status()` 报的是连不上）—— 先修好再动 UE：\n"
        f"· 上次的错误原文：{_SESSION_LINK_ERR or '(没有留下原文)'}\n"
        "· 常见原因：① UE 编辑器没打开 ② `ModelContextProtocol` 插件没启用 "
        "③ 端口 8000 没在监听；④ **客户端侧的 stdio 子进程没起来 / 会话工作区没切到本包**"
        "（这种连 19 个工具都看不到）。\n"
        "修好后**再调一次 `official_status()`** 确认 —— 这道闸只认它这次的返回值。"
    )
