# =============================================================================
# UEMCP · 阶段一：资产确认（本 server 目前**只做这一件事**）
#
# 【流程 —— 用户定义，不许改】（原文见 docs/阶段一-资产确认.md）
#   ① 根据上传图片提取所需资产 → **让用户确认**
#   ② 寻找资产：**名字对得上就是找到了**
#   ③ 没找到 → **禁止寻找疑似的**，直接告知用户缺少什么资产
#   ④ 要求用户把"存在但是 agent 没找到"的资产的【路径 + 名字】告诉 agent
#   ⑤ 让 agent 重命名
#   ⑥ 确认资产列表，**并向用户确认** → 交付资产清单
#
# 【为什么"禁止猜"是硬规则】（两次实测误报）
#   我给"名字是否规范"做过两版判据，两版都在实测里误报：
#     v1 长度+字符集 → 把 M_Weapon / SM_Pistol / GlassJar 判成"不规范"，
#        给 bush 报了 44 个候选，还建议把 SM_Pistol 改名成 SM_Bush。
#     v2 连续辅音 ≥4 → 把 SM_QuarterCylinder / FlashlightBeam 判错。
#   根因：**没有词典时，随机哈希和英文单词拼接在字符层面无法区分**
#   （ucynedrfa 和 flashlightbeam 是同一类字符串）。
#   既然猜不准，就不猜 —— 交给认识这个项目的人（用户指路，模型改名）。
#
# 【为什么必须支持"按文件夹名找"】
#   官方 find_assets 的名字过滤**只匹配资产名，不匹配文件夹名**（实测）。
#   所以这个"美国公路"材质：
#       /Game/Fab/Megascans/Surfaces/Fine_American_Road_sjfnch0a/.../MI_sjfnch0a
#   用关键词 "road" **永远搜不到** —— 它的资产名是哈希 MI_sjfnch0a。
#   但它的**文件夹名 Fine_American_Road 是可读的**。
#   所以本 server 按两条路找：① 资产名 ② **文件夹路径逐段匹配**。
#   ②很便宜：每类资产只要 1 次枚举调用，之后所有关键词都在本地匹配。
#
# 【架构】本 server 自己**不碰 UE**，它是编排层：
#   宿主(agent) → 本 server → 官方 Unreal MCP（HTTP 127.0.0.1:8000）→ UE 编辑器
# =============================================================================

import json
import logging
import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import anyio
from pydantic import BaseModel, Field

from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

# --- 常量 ---------------------------------------------------------------------

# 官方 Unreal MCP 地址（Epic 插件默认）
OFFICIAL_URL = "http://127.0.0.1:8000/mcp"

# 状态文件目录：项目根目录下的 catalog/
# parents[0]=mcp_server, [1]=src, [2]=项目根
STATE_DIR = Path(__file__).resolve().parents[2] / "catalog"
ELEMENTS_PATH = STATE_DIR / "elements.json"       # 用户确认过的元素清单
ASSET_LIST_PATH = STATE_DIR / "asset_list.json"   # 交付给用户的资产清单

# 官方工具的"地址" = 工具集名 + 工具名
TS_ASSET = "editor_toolset.toolsets.asset.AssetTools"    # 找资产 / 改名 / 验证存在
TS_SCENE = "editor_toolset.toolsets.scene.SceneTools"    # 关卡信息

# 资产类别 → 官方的类路径（find_assets 的 asset_type 要的是类路径）
CLASS_PRESETS: dict[str, str] = {
    "mesh": "/Script/Engine.StaticMesh",
    "material": "/Script/Engine.Material",
    "material_instance": "/Script/Engine.MaterialInstanceConstant",
    "texture": "/Script/Engine.Texture2D",
}
# 默认查哪几类（网格、材质实例、材质 —— 场景搭建主要用这三类）
DEFAULT_ASSET_TYPES = ["mesh", "material_instance", "material"]
# 只有网格有包围盒，材质没有尺寸
SIZE_QUERY_TYPES = {"mesh"}

# instructions = 服务器一连上就下发给宿主的流程约束（软约束，见文末说明）
WORKFLOW = """你是"参考图驱动的场景搭建"助手。当前**只做阶段一：资产确认**。

【阶段一 · 资产确认】必须按这个顺序，做完停下来等用户确认
1. 【提取元素】读用户给的参考图，列出画面里需要哪些元素，
   **先把这份元素清单给用户确认**，确认后用 confirm_elements 记录下来。
2. 【找资产】调 plan_assets()（不传参数就用刚确认的元素清单）。
   它按**两条路**找：按【资产名】，以及按【文件夹名】
   —— 官方只匹配资产名、不匹配文件夹名，哈希名资产只能靠文件夹名找到。
   **名字对得上就是找到了。**
3. 【报告缺失】**没找到就是没找到 —— 禁止猜"疑似是它"**，
   不许列候选让用户挑，不许拿别的资产顶替。
   直接告诉用户缺什么，并请用户把"存在但你没找到"的资产的【路径 + 名字】告诉你。
4. 【改名】用户给了路径 → 用 rename_assets 把它改成规范名
   （默认只预览，会先查引用者，用户确认后再真改）。
5. 【确认并交付】用 confirm_assets 逐个验证资产是否真的存在，
   落盘「元素 → 资产」对照表，并**向用户确认这份资产清单**。

硬性禁止：
- **不许寻找或猜测"疑似资产"**
- 不许在用户确认前替换或顶替任何资产
- 不许删资产、不许存盘（保存 UE 关卡/资产）
"""


# --- 官方连接（由**专用 task** 持有，全程复用一条连接）--------------------------
#
# ⚠ 为什么必须是"专用 task"（2026-09-23 实测定案，别改回去）：
#   `mcp.Client.__aenter__` 会把 streamable-http 传输压进一个 AsyncExitStack
#   （.venv/.../mcp/client/client.py:453-475），而这个传输内部会
#   `async with anyio.create_task_group()` —— 也就是**进入一个 anyio 取消域**。
#   anyio 的铁律：**取消域只能由进入它的那个 task 退出**。
#   旧写法是在【工具调用的 task】里建连接（懒连接），却在【lifespan 的 task】里
#   关连接（`exit_stack.aclose()`）—— 跨 task 退出，必然抛：
#       RuntimeError: Attempted to exit a cancel scope that isn't the current
#                     tasks's current cancel scope
#   这个异常穿透 SDK 派发器的 task group → ExceptionGroup → `mcp.run()` →
#   **整个 server 进程退出**；宿主侧只看到 "connection lost; reconnecting"。
#   原始证据（harness.log，2026-09-23 当天同类崩溃 10 次）：
#       10:26:46.264  INFO  Received session ID: 8e69195a...   ← 刚连上官方
#       10:26:46.617        RuntimeError: Attempted to exit a cancel scope ...
#       10:27:44.507  Exception Group Traceback → main.py:853 mcp.run()  ← 进程死亡
#       10:27:44.649  mcp-client(mine): connection lost; reconnecting in 500ms
#
#   ⚠ 别再往 `asyncio.Lock` 上找了：旧注释把它当成元凶（"加了锁才报"），
#     但**去掉锁之后它照样发生** —— 真凶是上面这个"跨 task 关闭"。
#
#   修法：**建和关都收进同一个 task**（`_connection_loop`）。工具调用只"借用"
#   已有连接、不负责建/关，于是取消域进出同源，问题根除。
#
#   备选方案（更简单，但每次官方调用都要重新握手，故未采用）：
#   在 `call_official` 里直接 `async with Client(OFFICIAL_URL) as ue:` ——
#   进和出都在工具调用的同一个 task 内，同样正确；代价是每次多一次 initialize
#   和一次会话终止（官方回 202，客户端会打 "Session termination failed" 警告）。
#   将来若"复用一条连接"反而出了问题，退到这条路上即可。

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
        app.stop = anyio.Event()        # 见上面 ⚠：每轮换新信号
        app.ue = None                   # 先摘掉旧连接，免得工具调用拿到已关的对象
        client = Client(OFFICIAL_URL)
        backoff = RETRY_SECONDS
        try:
            async with client:          # ← 进和出都在**本 task** 内，这是修复的核心
                app.ue = client
                app.error = ""
                log.info("已连上官方 Unreal MCP：%s", OFFICIAL_URL)
                # ⚠ 连上就**立刻**表态放行等待者，不能等退出 async with 才表态：
                #   下面要挂在这里把连接借出去、可能挂很久；若只在 finally 里 set，
                #   工具调用会一直等 ready 直到 READY_TIMEOUT 超时 —— 而日志里明明
                #   已经打了"已连上官方 Unreal MCP"（这个坑我实测踩到过）。
                if app.ready is not None:
                    app.ready.set()
                await app.stop.wait()   # 借出去，直到有人要求重连
                backoff = 0.5           # 主动重连：不必等满 RETRY_SECONDS
        except Exception as exc:
            # 连不上是**可预期**的（编辑器没开），不是我们的 bug：记下来给工具调用看
            app.error = f"{type(exc).__name__}: {exc}"
            log.warning("连不上官方 Unreal MCP（%s）：%s", OFFICIAL_URL, exc)
        finally:
            app.ue = None
            if app.ready is not None:
                app.ready.set()         # 首次表态：放行正在等的工具调用
        await anyio.sleep(backoff)


async def official_client(ctx: Context[AppContext]) -> Any:
    """取当前可用的官方客户端；没连上就抛 ToolError（给模型看人话，而不是崩栈）。

    连不上是**可预期**的（编辑器没开），所以这里一律转成 ToolError 原文说明，
    不把底层异常抛给协议层。
    """
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
            # 让连接 task 收尾（它在自己的 task 里关官方客户端），
            # 再由 task group 等它真正结束 —— 关机路径也不留尾巴。
            tg.cancel_scope.cancel()


# ⚠ 顺序很关键：app_lifespan 必须在 MCPServer(...) 之前定义；
#   mcp 必须在任何 @mcp.tool() 之前存在（装饰器在"定义时"就执行）。
mcp = MCPServer("UEMCP-AssetConfirm", instructions=WORKFLOW, lifespan=app_lifespan)


# --- 厚函数（纯逻辑，不碰网络，可直接单测）-------------------------------------

def parse_return(text: str) -> Any:
    """把官方返回的那段文本解析成 Python 对象。

    官方返回值包在 {"returnValue": ...} 里。解析失败就把原文当字符串返回 ——
    免得因为格式差异整个工具崩掉。
    """
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(data, dict) and "returnValue" in data:
        return data["returnValue"]
    return data


def to_object_path(package_path: str) -> str:
    """包路径 → 对象路径。

    官方有些接口不接受 "/Game/A/B/house1"，要 "/Game/A/B/house1.house1"
    （对象路径 = 包路径 + "." + 资产名）。这条是实测踩出来的。
    """
    name = package_path.rsplit("/", 1)[-1]
    return f"{package_path}.{name}"


def token_match(keyword: str, text: str) -> bool:
    """词边界匹配 —— 专门对冲官方"纯子串"的缺陷。

    官方 find_assets 的名字过滤是纯子串：搜 "Tree" 会命中 "s**tree**t_lamp_01_4k"（路灯）。
    这里把文本切成"词"（按 _ - 空格 以及小写转大写的边界切），
    要求关键词作为**一个完整的词**出现才认；也允许"词 + 数字后缀"（house → house1）。
    """
    kw = keyword.lower().strip()
    if not kw:
        return False

    words: list[str] = []
    for part in text.replace("-", "_").replace(" ", "_").split("_"):
        chunk = ""
        prev_lower = False
        for ch in part:
            # 小写后面跟大写 → 驼峰边界（streetLamp → street / Lamp）
            if ch.isupper() and prev_lower:
                if chunk:
                    words.append(chunk)
                chunk = ch
            else:
                chunk += ch
            prev_lower = ch.islower()
        if chunk:
            words.append(chunk)

    for word in words:
        low = word.lower()
        if low == kw:
            return True
        # house → house1：允许关键词后面直接跟数字
        if low.startswith(kw) and low[len(kw):].isdigit():
            return True
    return False


def num(value: Any, digits: int = 2) -> float:
    """把官方给的数字变成"能安全穿过 JSON 的数字"。三件事缺一不可：

      ① float()  —— 官方有时把数字包成字符串
      ② round()  —— 去掉浮点噪声
      ③ 负零归一 + 非有限值兜底 —— **这条最隐蔽，实测踩出来的**

    ⚠ 为什么必须管负零（-0.0）：
      Python 把 -0.0 序列化成 "-0.0"；而 JS 的 JSON.stringify(-0) 得到 "0"，
      **往返不无损**。宿主会据此判定整个工具返回值 "value is not lossless JSON"
      并**整条拒绝** —— 不是丢一个字段，是工具直接不可用。
      实测证据（2026-09-23）：get_scene_context(limit=6) 结果里 0 个负零 → 成功；
      limit=15/40 各含 1 个负零（actors[7].center_cm[0] = -0.0）→ 宿主报错整条拒收。
      触发条件：某个物体的中心坐标恰好压在某根轴上。
    教训：**工具返回的数字，不只是"算得对"，还得"传得过去"。**
    """
    v = round(float(value), digits)
    if not math.isfinite(v):
        # inf/nan 会让 JSON 写成 Infinity/NaN，同样被宿主整条拒收。
        # 归一成 0.0 是**有代价的取舍**：宁可报一个可能不准的 0，也不要工具不可用。
        return 0.0
    return 0.0 if v == 0 else v


def size_from_box(box: Any) -> list[float] | None:
    """把官方包围盒换算成 [X, Y, Z] 尺寸（厘米）。"""
    if not isinstance(box, dict) or not box.get("isValid"):
        return None
    mn, mx = box.get("min"), box.get("max")
    if not isinstance(mn, dict) or not isinstance(mx, dict):
        return None
    return [
        num(float(mx["x"]) - float(mn["x"])),
        num(float(mx["y"]) - float(mn["y"])),
        num(float(mx["z"]) - float(mn["z"])),
    ]


def load_json(path: Path) -> Any:
    """读状态文件；不存在返回 None（第一次跑本来就没有，不是错误）。"""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    """写状态文件（目录不存在就先建整条路径）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def call_official(
    ctx: Context[AppContext], tool_name: str, arguments: dict, toolset: str | None = None
) -> Any:
    """调用官方的一个能力，并把结果解析成 Python 对象。

    两种调用方式（实测）：
      - toolset=None   → 直接调官方**元工具**（list_toolsets / describe_toolset / call_tool）
      - toolset=某工具集 → 经元工具 call_tool 转发到具体工具
        （官方 Tool Search 默认开着，具体工具不出现在 tools/list 里，只能这样转发）

    ⚠ 连接由 `_connection_loop` 持有：这里**只借用，不建也不关**
      （原因见本文件顶部"官方连接"那段）。
    """
    if toolset is None:
        mcp_tool, payload = tool_name, arguments
    else:
        mcp_tool = "call_tool"
        payload = {"toolset_name": toolset, "tool_name": tool_name, "arguments": arguments}

    app = ctx.request_context.lifespan_context
    client = await official_client(ctx)

    async def once(target: Any) -> Any:
        result = await target.call_tool(mcp_tool, payload)
        if result.is_error:
            text = result.content[0].text if result.content else "(无内容)"
            raise ToolError(f"官方工具 {tool_name} 返回错误：{text}")
        text = result.content[0].text if result.content else ""
        return parse_return(text)

    try:
        return await once(client)
    except ToolError:
        raise
    except Exception as first_error:
        # 连接可能已经死了（编辑器关了、会话被踢）。**不能在这里关连接** ——
        # 只发一个"请重连"的信号，让连接 task 在它自己的 task 里收尾并重建。
        app.stop.set()
        new_client = None
        for _ in range(60):                     # 最多等 6 秒（60 × 0.1s）
            await anyio.sleep(0.1)
            if app.ue is not None and app.ue is not client:
                new_client = app.ue
                break
        if new_client is None:
            raise ToolError(
                f"调用官方 {tool_name} 失败，且自动重连没成功：{first_error}"
            ) from first_error
        try:
            return await once(new_client)
        except Exception as second_error:
            raise ToolError(
                f"调用官方 {tool_name} 失败（已重试一次）："
                f"第一次 {first_error}；第二次 {second_error}"
            ) from first_error


# --- 模型（工具返回结构 = 模型看到的"参数说明书"）------------------------------

class OfficialStatus(BaseModel):
    """官方链路自检结果。"""

    reachable: bool = Field(description="能不能连上官方 Unreal MCP")
    protocol_version: str | None = Field(default=None, description="协商出的 MCP 协议版本")
    toolset_count: int = Field(default=0, description="官方一共有多少个工具集")
    error: str | None = Field(default=None, description="连不上时的错误原文")


class ElementItem(BaseModel):
    """参考图里需要的一个元素。"""

    element: str = Field(description="元素名，如 house / road / tree / sidewalk")
    note: str = Field(default="", description="备注：在画面里的位置、大概数量等")


class ElementList(BaseModel):
    """**用户确认过**的元素清单。"""

    confirmed_at: str = Field(description="确认时间")
    source_image: str = Field(description="参考图路径（留档）")
    total: int = Field(description="几个元素")
    elements: list[ElementItem] = Field(description="元素清单")
    next_step: str = Field(description="下一步做什么")


class FoundAsset(BaseModel):
    """按某个元素找到的一个候选资产。"""

    name: str = Field(description="资产名（可能没有语义，如 MI_sjfnch0a）")
    path: str = Field(description="包路径")
    asset_type: str = Field(description="类别：mesh / material / material_instance")
    size_cm: list[float] | None = Field(
        default=None, description="实测尺寸 [X,Y,Z]（厘米）。只有网格有，材质是 null"
    )
    matched_by: str = Field(description="怎么命中的：资产名 / 文件夹名")
    matched_segment: str = Field(
        default="", description="按文件夹命中时，命中的那一段（这就是它的语义来源）"
    )


class ElementPlan(BaseModel):
    """一个元素的查找结果。"""

    element: str = Field(description="元素名")
    status: str = Field(description="found = 名字对得上；missing = 没找到")
    assets: list[FoundAsset] = Field(description="找到的候选（缺失时为空）")
    question: str = Field(description="**要原样问用户的话**")


class PlanReport(BaseModel):
    """阶段一的查找报告。"""

    stage: str = Field(description="当前阶段")
    needs_user_confirmation: bool = Field(description="恒为 true —— 用户确认前不许往下走")
    source: str = Field(description="元素清单从哪来的：用户确认过的清单 / 本次传入的关键词")
    total_elements: int = Field(description="一共查了几个元素")
    found: int = Field(description="找到了几个")
    missing: int = Field(description="缺了几个")
    elements: list[ElementPlan] = Field(description="逐元素结果")
    official_calls: int = Field(description="调了几次官方工具")
    next_step: str = Field(description="下一步做什么")
    warnings: list[str] = Field(description="要提醒的坑（不藏着）")


class RenameItem(BaseModel):
    """要改名的一个资产（路径和名字由**用户**提供）。"""

    path: str = Field(description="现在的完整包路径")
    new_name: str = Field(description="新的资产名（**只给名字**，不要带路径和斜杠）")


class RenameResult(BaseModel):
    """一个资产的改名结果。"""

    old_path: str = Field(description="改名前路径")
    new_path: str = Field(description="改名后路径")
    referencers: list[str] = Field(description="谁引用了它（改名前查的，看影响面）")
    ok: bool = Field(description="成功没有")
    error: str = Field(default="", description="失败原因（官方原文）")


class RenameReport(BaseModel):
    """一批改名的结果。"""

    dry_run: bool = Field(description="true = 只预览，**没有真改**")
    requested: int = Field(description="要求改几个")
    renamed: int = Field(description="真改成功几个（dry_run 时恒为 0）")
    failed: int = Field(description="几个不合法或失败")
    items: list[RenameResult] = Field(description="逐个结果")
    note: str = Field(description="说明与风险提示")


class AssetListItem(BaseModel):
    """交付清单里的一行：一个元素用哪个资产。"""

    element: str = Field(description="画面元素名")
    asset_path: str = Field(description="确认使用的资产包路径")
    asset_type: str = Field(default="", description="类别")
    exists: bool = Field(description="官方 exists() 验证结果 —— **必须为 true**")
    note: str = Field(default="", description="备注（谁改的名、为什么选它等）")


class AssetListDelivery(BaseModel):
    """**最终交付物**：给用户的资产清单。"""

    delivered_at: str = Field(description="交付时间")
    source_image: str = Field(description="参考图路径")
    total: int = Field(description="一共几个元素")
    verified: int = Field(description="路径验证通过几个")
    invalid_paths: list[str] = Field(description="**验证不存在的路径**（必须处理掉）")
    items: list[AssetListItem] = Field(description="资产清单")
    deliverable: str = Field(description="给人看的清单正文（直接贴给用户）")
    note: str = Field(description="说明与下一步")


# --- 工具 1：链路自检 ---------------------------------------------------------

@mcp.tool()
async def official_status(ctx: Context[AppContext]) -> OfficialStatus:
    """自检：能不能连上官方 Unreal MCP，并报告协议版本和工具集数量。

    任何依赖官方的操作之前，先用它确认链路。连不上时返回**错误原文**，不猜原因。
    """
    try:
        catalog = await call_official(ctx, "list_toolsets", {})
        text = catalog if isinstance(catalog, str) else json.dumps(catalog, ensure_ascii=False)
        # 官方把工具集列成 "- 工具集名: 说明"。只数形如 "- xxx.yyy:" 的行，
        # 别把说明里的项目符号也算进去（实测：那样会数出 67，真实值是 52）。
        count = len(re.findall(r"^-\s+\S*\.\S*:", text, flags=re.MULTILINE))
    except ToolError as exc:
        return OfficialStatus(reachable=False, error=str(exc))

    # 协商出的协议版本：上面那次 list_toolsets 调用已经把连接建起来了，
    # 所以此刻读 client.protocol_version 是安全的（它是只读属性，
    # 由 __aenter__ 的握手填好；未连接时抛 RuntimeError）。
    # ⚠ 读不到**不算自检失败**：版本只是报告项，不该把整条结果判死。
    #    所以这里单独 try，失败降级成 None，并记一行日志。
    version: str | None = None
    try:
        app = ctx.request_context.lifespan_context
        version = app.ue.protocol_version if app.ue is not None else None
    except (RuntimeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("读协议版本失败（已忽略）：%r", exc)

    return OfficialStatus(reachable=True, protocol_version=version, toolset_count=count)


# --- 工具 2：确认元素清单 -----------------------------------------------------

@mcp.tool()
async def confirm_elements(
    elements: Annotated[
        list[ElementItem], Field(description="从参考图提取的、**已经过用户确认**的元素清单")
    ],
    source_image: Annotated[str, Field(description="参考图路径（留档用）")] = "",
) -> ElementList:
    """记录**用户已确认**的元素清单 —— 阶段一的第一步。

    为什么要有这一步：用户的要求是"根据上传图片提取所需资产**让用户确认**"。
    把确认结果落盘有两个好处：
      ① 后面 plan_assets() 不用再传一遍关键词，直接读这份清单
      ② 有据可查：到底确认过哪些元素，不会中途被悄悄改掉

    ⚠ 调用前必须先让用户确认过元素清单。**没确认就别调**。
    """
    result = ElementList(
        confirmed_at=datetime.now(timezone.utc).isoformat(),
        source_image=source_image,
        total=len(elements),
        elements=elements,
        next_step=(
            "元素清单已记录。下一步调 plan_assets()（不传参数即可）去找资产；"
            "找到就是找到，没找到就直接告诉用户缺什么，"
            "并请用户给出【路径 + 名字】—— 禁止猜疑似的。"
        ),
    )
    save_json(ELEMENTS_PATH, result.model_dump())
    return result


# --- 工具 3：找资产（阶段一主工具）--------------------------------------------

@mcp.tool()
async def plan_assets(
    ctx: Context[AppContext],
    keywords: Annotated[
        list[str] | None,
        Field(description="要查的元素名。不传 = 用 confirm_elements 记录的那份清单"),
    ] = None,
    max_per_element: Annotated[
        int, Field(ge=1, le=20, description="每个元素最多列几个候选（默认 4）")
    ] = 4,
) -> PlanReport:
    """按名字找资产 → 报告**找到 / 没找到**，并对每个元素给一句要问用户的话。

    查两条路（缺一不可）：
      ① **按资产名** —— 官方 find_assets 的常规能力
      ② **按文件夹名** —— ⚠ 官方只匹配资产名、**不匹配文件夹名**。所以哈希名资产
         （MI_sjfnch0a）靠①永远搜不到，但它所在文件夹叫 Fine_American_Road。
    ②很便宜：每类资产只要 1 次枚举调用，之后所有关键词都在本地匹配。

    ⚠ **本工具不猜、不找"疑似"资产**（用户明确要求的硬规则）。
      名字对得上就是找到；对不上就是没找到。
      **命中多个时，把命中的逐个列出来（最多 max_per_element 个）交给用户确认用哪一个**；
      用户也可以明确让模型挑最匹配的 —— 但**绝不拿名字对不上的资产顶替**。
      为什么：我给"名字是否规范"做过两版判据，两版都在实测里误报
      （v1 把 M_Weapon / SM_Pistol / GlassJar 判错，还建议把 SM_Pistol 改名成 SM_Bush；
        v2 把 QuarterCylinder / FlashlightBeam 判错）。
      根因是**没有词典时，随机哈希和英文单词拼接在字符层面无法区分**。猜不准就不猜。

    没找到时：直接告知缺什么，并请用户把"存在但没被找到"的资产的【路径 + 名字】给你。

    ⚠ 本工具**不改动任何东西**，`needs_user_confirmation` 恒为 true。
    """
    calls = 0
    warnings: list[str] = []

    # 元素从哪来：优先用用户确认过的清单
    stored = load_json(ELEMENTS_PATH)
    if keywords:
        elements_kw = [k for k in keywords if k and k.strip()]
        source = "本次传入的关键词"
    elif stored:
        elements_kw = [e["element"] for e in stored.get("elements", []) if e.get("element")]
        source = f"用户确认过的元素清单（{stored.get('confirmed_at', '?')}）"
    else:
        raise ToolError(
            "没有元素清单：既没传 keywords，也没有 confirm_elements 记录过。"
            "请先读参考图列出元素、让用户确认，然后调 confirm_elements。"
        )
    if not elements_kw:
        raise ToolError("元素清单是空的，没什么可查。")

    # --- 每类资产枚举一次，之后都在本地匹配（便宜且不会漏）---
    inventory: dict[str, list[str]] = {}
    for type_name in DEFAULT_ASSET_TYPES:
        class_path = CLASS_PRESETS.get(type_name)
        if class_path is None:
            continue
        paths = await call_official(
            ctx,
            "find_assets",
            {
                "folder_path": "/Game",
                "name": "",                     # 空名字 = 全都要（这就是"枚举"）
                "asset_type": {"refPath": class_path},
                "recursive": True,
            },
            toolset=TS_ASSET,
        )
        calls += 1
        inventory[type_name] = [p for p in (paths or []) if isinstance(p, str)]

    if sum(len(v) for v in inventory.values()) == 0:
        warnings.append(
            "资产库枚举返回 0 条 —— 可能官方 find_assets 的用法变了，"
            "先用 official_status 确认链路再重试。"
        )

    elements: list[ElementPlan] = []
    found_count = 0

    for kw in elements_kw:
        kw_l = kw.lower().strip()
        found: list[FoundAsset] = []

        for type_name, paths in inventory.items():
            for p in paths:
                name = p.rsplit("/", 1)[-1]
                matched_by, seg = "", ""
                if token_match(kw_l, name):
                    matched_by = "资产名"
                else:
                    # 逐段匹配文件夹名：专治"资产名是哈希、文件夹名才可读"
                    for part in p.split("/"):
                        if part and token_match(kw_l, part):
                            matched_by, seg = "文件夹名", part
                            break
                if matched_by:
                    found.append(
                        FoundAsset(
                            name=name, path=p, asset_type=type_name,
                            matched_by=matched_by, matched_segment=seg,
                        )
                    )

        # 尺寸：只有网格有包围盒，且只查前 max_per_element 个
        for f in [x for x in found if x.asset_type in SIZE_QUERY_TYPES][:max_per_element]:
            try:
                box = await call_official(
                    ctx, "get_bounds", {"mesh": {"refPath": to_object_path(f.path)}},
                    toolset="editor_toolset.toolsets.static_mesh.StaticMeshTools",
                )
                calls += 1
                f.size_cm = size_from_box(box)
            except ToolError as exc:
                calls += 1
                warnings.append(f"查 {f.name} 尺寸失败：{exc}")

        # --- 判定 + 提问 ---
        found.sort(key=lambda a: (a.matched_by != "资产名", len(a.path)))
        if found:
            found_count += 1
            names = "、".join(f.name for f in found[:max_per_element])
            question = (
                f"「{kw}」找到 {len(found)} 个候选：{names}。"
                f"请确认用哪一个（或明确让我挑最匹配的）。"
            )
        else:
            # ⛔ 不猜、不找疑似的 —— 直接说缺，请用户给路径
            question = (
                f"「{kw}」按名字没找到（资产名和文件夹名都试过了）。"
                f"**如果它其实存在、只是名字对不上，请把它的【路径 + 名字】告诉我，我来改名。**"
                f"如果确实没有，请告诉我：跳过这个元素。"
            )

        elements.append(
            ElementPlan(
                element=kw, status="found" if found else "missing",
                assets=found[:max_per_element], question=question,
            )
        )

    missing = len(elements) - found_count
    if missing:
        warnings.append(
            f"有 {missing} 个元素没找到。**禁止猜疑似的** —— "
            f"把缺的如实告诉用户，请用户给出【路径 + 名字】；拿到答复前不要往下走。"
        )

    return PlanReport(
        stage="阶段一 · 资产确认",
        needs_user_confirmation=True,
        source=source,
        total_elements=len(elements),
        found=found_count,
        missing=missing,
        elements=elements,
        official_calls=calls,
        next_step=(
            "把每个元素的 question **原样逐条问用户**："
            "找到的问用哪个；**缺的请用户给出【路径 + 名字】**。"
            "拿到用户答复后：路径 → rename_assets 改名；"
            "全部齐了 → confirm_assets 交付清单。"
        ),
        warnings=warnings,
    )


# --- 工具 4：改名（用户指路之后）----------------------------------------------

@mcp.tool()
async def rename_assets(
    ctx: Context[AppContext],
    items: Annotated[
        list[RenameItem], Field(description="要改名的资产（路径来自用户提供）")
    ],
    confirm: Annotated[
        bool, Field(description="false = 只预览改名前后路径（默认，安全）；true = 真的改")
    ] = False,
) -> RenameReport:
    """把用户指出的资产改成规范名 —— **默认只预览，不真改**。

    用在阶段一第 5 步：用户说了"存在但你没找到的那个资产在 XX 路径"，就用它改名，
    以后不会再有人认不出它。

    为什么必须两步（预览 → 确认）：
      - 改的是**用户工程里的资产**，`move` 会改变资产的包路径
      - UE 一般会自动更新引用，但那是"一般"，不是保证
      - 所以每次都先查 `get_referencers`（谁引用了它），把影响面摆给用户看

    走官方 `AssetTools.move(path, new_path)` —— 同目录内换个名字就是"重命名"。
    """
    report = RenameReport(
        dry_run=not confirm, requested=len(items), renamed=0, failed=0, items=[],
        note=(
            "改名前已查引用者。UE 通常会跟随更新引用，但**这里不做保证** —— "
            "若该资产被蓝图/关卡引用，改名后请重新打开确认一次。"
        ),
    )

    for item in items:
        old_path, new_name = item.path.strip(), item.new_name.strip()
        entry = RenameResult(old_path=old_path, new_path="", referencers=[], ok=False)

        # 本地合法性检查 —— 不合法就不必去打扰官方
        if "/" in new_name or not new_name:
            entry.error = f"new_name 只能是纯名字（不能有斜杠），收到 {new_name!r}"
            entry.new_path = "(未计算)"
            report.failed += 1
            report.items.append(entry)
            continue
        if old_path.rsplit("/", 1)[-1] == new_name:
            entry.error = "新名字和旧名字一样，没必要改"
            entry.new_path = old_path
            report.failed += 1
            report.items.append(entry)
            continue

        entry.new_path = f"{old_path.rsplit('/', 1)[0]}/{new_name}"

        # 查引用者（预览和真改都查 —— 这是给用户看的风险面）
        # 两种路径形式都试：实测裸包路径会报 "Asset does not exist"
        refs, last_err = None, ""
        for candidate in (old_path, to_object_path(old_path)):
            try:
                refs = await call_official(
                    ctx, "get_referencers", {"asset_path": candidate}, toolset=TS_ASSET
                )
                break
            except ToolError as exc:
                last_err = str(exc)
        entry.referencers = (
            [r for r in (refs or []) if isinstance(r, str)]
            if refs is not None
            else [f"(查引用失败：{last_err})"]
        )

        if not confirm:
            entry.ok = True          # 预览：能改就算通过
            report.items.append(entry)
            continue

        try:
            await call_official(
                ctx, "move", {"path": old_path, "new_path": entry.new_path}, toolset=TS_ASSET
            )
            entry.ok = True
            report.renamed += 1
        except ToolError as exc:
            entry.error = f"改名失败：{exc}"
        report.items.append(entry)

    return report


# --- 工具 5：确认并交付资产清单（阶段一收尾）----------------------------------

@mcp.tool()
async def confirm_assets(
    ctx: Context[AppContext],
    items: Annotated[
        list[AssetListItem],
        Field(description="用户确认过的「元素 → 资产」对照表（exists 字段由本工具回填）"),
    ],
) -> AssetListDelivery:
    """**阶段一收尾 + 交付**：验证资产是否真的存在 → 落盘 → 输出给用户的清单。

    做三件事：
      ① 逐个调官方 `exists()` **验证路径**（不是形式主义，见下）
      ② 落盘到 `catalog/asset_list.json`（这是清单文件，不是 UE 资产，不违反"不许存盘"）
      ③ 生成 `deliverable` —— 一段**给人看的清单正文**，直接贴给用户确认

    ⚠ 为什么必须逐个验证存在性（实测价值）：
      2026-09-23 实测：`/Game/Fab/Urban_Street_Pack` 整个文件夹在我工作期间消失了
      （材质实例总数 58 → 56，exists() = False），而我此前一直以为人行道材质还在用它的
      Material_002。有这一步就会立刻暴露。
      **"我以为它在"和"它真的在"是两回事。**

    ⚠ 有路径验证不通过时，`note` 会要求先处理掉再往下走 ——
    否则下一阶段会挂上一个解析不到的材质/网格。
    """
    results: list[AssetListItem] = []
    invalid: list[str] = []
    stored = load_json(ELEMENTS_PATH) or {}

    for item in items:
        ok = False
        err = ""
        try:
            exists = await call_official(
                ctx, "exists", {"path": item.asset_path}, toolset=TS_ASSET
            )
            # 官方可能回 True / "True" / {"returnValue": true} 已由 parse_return 拆过
            ok = exists is True or (isinstance(exists, str) and exists.lower() == "true")
        except ToolError as exc:
            err = str(exc)
        if not ok:
            invalid.append(item.asset_path + (f"（验证失败：{err}）" if err else ""))
        results.append(item.model_copy(update={"exists": ok}))

    # 生成给人看的清单正文
    lines = ["资产清单（阶段一交付）", ""]
    lines.append(f"参考图：{stored.get('source_image') or '(未记录)'}")
    lines.append(f"元素数：{len(results)}    路径验证通过：{sum(1 for r in results if r.exists)}")
    lines.append("")
    lines.append(f"{'元素':<16} {'状态':<6} 资产路径")
    lines.append("-" * 88)
    for r in results:
        lines.append(f"{r.element:<16} {'✓ 存在' if r.exists else '✗ 不存在':<6} {r.asset_path}")
        if r.note:
            lines.append(f"{'':<16} {'':<6} └ {r.note}")
    if invalid:
        lines.append("")
        lines.append("⚠ 以下路径验证不通过，必须先处理（让用户重新给路径，或确认跳过）：")
        for p in invalid:
            lines.append(f"  - {p}")

    delivery = AssetListDelivery(
        delivered_at=datetime.now(timezone.utc).isoformat(),
        source_image=stored.get("source_image", ""),
        total=len(results),
        verified=sum(1 for r in results if r.exists),
        invalid_paths=invalid,
        items=results,
        deliverable="\n".join(lines),
        note=(
            "资产清单已生成并落盘（catalog/asset_list.json）。"
            "**请把 deliverable 贴给用户做最终确认。**"
            if not invalid
            else f"⚠ 有 {len(invalid)} 个路径验证不通过 —— "
            "**先让用户处理掉**（重新给路径或确认跳过），再交付。"
        ),
    )
    save_json(ASSET_LIST_PATH, delivery.model_dump())
    return delivery


# --- 启动区 -------------------------------------------------------------------
# 只有"直接运行这个文件"时才启动；被 mcp dev / mcp run / 宿主导入时不启动。
if __name__ == "__main__":
    # 不带参数 = stdio（进程直连），由宿主负责启动这个进程
    mcp.run()
