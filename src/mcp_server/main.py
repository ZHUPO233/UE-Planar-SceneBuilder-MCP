# =============================================================================
# UEMCP · UE 平面场景搭建（参考图驱动）—— 本 server 是**编排层**，自己不碰 UE
#
# 【已实现】阶段一 资产确认、阶段二 平面布局规划
# 【未实现】阶段三 ~ 八（见 docs/阶段二至八-全流程规划.md）
#
# 【阶段一流程 —— 用户定义，不许改】（原文见 docs/阶段一-资产确认.md）
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

import hashlib
import json
import logging
import math
import re
import sys
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

# ⚠ 阶段二·规划层**不能**在文件顶部 import —— 见 _planning_modules() 里的血账说明：
#   宿主是按**脚本方式**起本文件（python ...\src\mcp_server\main.py），此时相对导入
#   `from .planning import ...` 会抛 "attempted relative import with no known parent package"，
#   整个 server 都起不来。改成用的时候惰性导入。

# --- 常量 ---------------------------------------------------------------------

# 官方 Unreal MCP 地址（Epic 插件默认）
OFFICIAL_URL = "http://127.0.0.1:8000/mcp"

# 状态文件目录：项目根目录下的 catalog/
# parents[0]=mcp_server, [1]=src, [2]=项目根
STATE_DIR = Path(__file__).resolve().parents[2] / "catalog"
ELEMENTS_PATH = STATE_DIR / "elements.json"       # 用户确认过的元素清单
ASSET_LIST_PATH = STATE_DIR / "asset_list.json"   # 交付给用户的资产清单
LIBRARY_PATH = STATE_DIR / "library_snapshot.json"  # 资产库指纹（给清单"签字"用）
"""confirm_assets 落盘时顺手记下"当时的资产库长什么样"：

    { captured_at, hash, counts, total, note }

为什么单独一个文件、而不塞进 asset_list.json：
  asset_list.json 是**交付物**（给人看的清单），指纹是**内部判定依据**。
  混在一起会让交付物变得难读，而且以后想换判定方式（比如改用 GUID）时，
  不用动交付物的结构。
"""

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


# --- [完工-01] 官方连接（由**专用 task** 持有，全程复用一条连接）---
# 实测 2026-09-23 12:07 起本会话持续服务：数十次官方调用，无 cancel-scope 崩溃。
# 实测 2026-09-23 晚（预检 exit=0）：构造改过两处后仍能起来 —— ①脚本方式执行 main.py、③9 个工具注册，都过。
#   ① 服务器名 "UEMCP-AssetConfirm" → "UEMCP-SceneLayout"（原名写死"资产确认"，会误导连上来的 agent）。
#      影响面：只是 `serverInfo.name` 这个自我描述，**不参与工具路由**
#      （宿主按自己的配置键命名工具，本会话实测工具名是 `mcp__mine__*`，前缀取自配置键 `mcp-mine`）。
#   ② 去掉 `instructions=WORKFLOW` —— 理由与证据写在 `mcp = MCPServer(...)` 上方，不在这里重复。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。
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
#
# ⚠ 为什么**没有** instructions=（2026-09-23 实测，别加回来）：
#   本宿主不读 MCP 的 instructions。证据：dsh-mcp-client/lib/index.js 里
#   `await generation.connect(...)` 的返回值（InitializeResult，instructions 就在里面）
#   **连赋值都没有**，全包搜 "instructions" 是 0 次命中 —— 发出去也没人接。
#   真正到模型眼前的是 **tools/list**（工具名 + 描述 + schema）：
#   所以要求写在各工具的 docstring 里，原先那段 75 行 WORKFLOW 常量已删。
mcp = MCPServer("UEMCP-SceneLayout", lifespan=app_lifespan)


# --- [完工-02] 厚函数（纯逻辑，不碰网络，可直接单测）---
# 实测 2026-09-23 12:07–12:24：token_match / num / size_from_box / parse_return /
# to_object_path / load_json / save_json 经 plan_assets、confirm_assets 反复调用。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

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


def library_fingerprint(inventory: dict[str, list[str]]) -> tuple[str, dict[str, int]]:
    """把「资产库快照」压成一个 sha256 指纹 + 每类数量。

    为什么需要它：`delivered_at` 只能说明"什么时候确认的"，回答不了"项目变没变"。
    这里**只对路径负责**：同一批路径（与枚举顺序无关）→ 同一个指纹。

    ⚠ 路径没变、资产**内容**变了（换贴图、改材质参数）**测不出来** —— 这是刻意的取舍：
      要覆盖内容得上资产 GUID / 磁盘时间戳，成本高一个量级。
      所以交付文案里要写明：清单承诺的是**路径有效**，不是**内容没变**。
    """
    parts: list[str] = []          # 每一类压成一段字符串，最后整体哈希
    counts: dict[str, int] = {}    # 每类多少个，用于"58 → 56"这种人话差异
    for type_name in sorted(inventory):          # 排序：让指纹与字典顺序无关
        paths = sorted(inventory[type_name])     # 排序：让指纹与官方返回顺序无关
        counts[type_name] = len(paths)
        parts.append(f"{type_name}:{len(paths)}:" + "|".join(paths))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest, counts


def describe_count_changes(signed: dict[str, int], live: dict[str, int]) -> list[str]:
    """把「签字时」与「现在」的数量差异说成人话，只报变了的那几类。

    例子：`["material_instance: 58 → 56"]` —— 这正是 2026-09-23 那次
    `/Game/Fab/Urban_Street_Pack` 整个包消失时该出现的信号。
    """
    out: list[str] = []
    for type_name in sorted(set(signed) | set(live)):   # 并集：一方没有的类也要比
        before, after = signed.get(type_name, 0), live.get(type_name, 0)
        if before != after:
            out.append(f"{type_name}: {before} → {after}")
    return out


def element_keys_from_items(items: list[dict]) -> tuple[set[str], str]:
    """从交付表的行里取出「已经落位的元素关键词」，以及**用的是哪种判定方式**。

    优先用 `element_key`（显式标注，最可靠）；旧表没有这个字段，
    退回"标签首词"（如 `"house 主屋"` → `house`）—— 那是**推断**，
    所以把用的哪种方式一起返回，别让推断冒充事实。
    """
    keys: set[str] = set()
    explicit = 0
    for it in items:
        key = str(it.get("element_key") or "").strip().lower()
        if key:
            explicit += 1
            keys.add(key)
            continue
        label = str(it.get("element") or "").strip()
        if label:
            keys.add(label.split()[0].lower())   # split()[0] = 以空白切开取第一个词
    if items and explicit == len(items):
        mode = "element_key 显式标注"
    elif explicit:
        mode = f"混合（{explicit}/{len(items)} 行有 element_key，其余按标签首词推断）"
    else:
        mode = "标签首词推断（表里没有 element_key）"
    return keys, mode


# --- [完工-03] 官方调用与枚举/实测（call_official / library_inventory / measure_asset）---
# 实测 2026-09-23 12:24：confirm_assets 用它量到 4 个网格的包围盒，
# 并对 2 个材质如实报"material_instance 没有包围盒"（没编数字）。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。


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


async def library_inventory(
    ctx: Context[AppContext]
) -> tuple[dict[str, list[str]], int]:
    """实况枚举项目资产库：**每类一次** find_assets（空名字 = 全都要）。

    返回 `(库存, 调了几次官方)`；库存形如 `{"mesh": ["/Game/..."], ...}`（已排序）。

    ⚠ 这里是**唯一一份**枚举逻辑：`plan_assets`（找资产）和 `get_asset_list`（取清单）
      都调它，不再各留一份拷贝（2026-09-23 用户拍板合并）。
    ⚠ 返回的路径**已排序**：一是让指纹与枚举顺序无关，二是让"截前 N 个"更确定。
      副作用：`plan_assets` 里"同长度候选谁进 max_per_element"从『官方返回顺序』
      变成『路径字典序』——更确定，但不是 100% 逐字节等价。
    """
    inventory: dict[str, list[str]] = {}
    calls = 0
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
        inventory[type_name] = sorted(p for p in (paths or []) if isinstance(p, str))
    return inventory, calls


async def measure_asset(
    ctx: Context[AppContext], asset_path: str, asset_type: str = ""
) -> tuple[list[float] | None, float | None, str]:
    """量一个资产的**包围盒**尺寸与体积，返回 `(尺寸, 体积, 来源说明)`。

    为什么由工具来量、不让调用方填：
      "我以为它多大"和"它真的多大"是两回事 —— 尺寸拿去算缩放倍数，
      填错一个数，摆出来的房子就是错的比例。所以**实测值覆盖**调用方填的值。

    只有网格有包围盒；材质/材质实例一律返回 `(None, None, 原因)`。类别判定三档：
      ① `asset_type` 就在本 server 的类别表 `CLASS_PRESETS` 里 → 直接按它判，省一次调用；
      ② 不在表里（空值或别的写法）→ 问官方 `get_asset_class()` 拿类名再判；
      ③ 量取失败 → **原样带上官方报错，绝不编一个数字**。
    """
    resolved = asset_type
    if resolved not in CLASS_PRESETS:
        # 档 ②：类别未知 → 问官方它到底是什么（官方返回的是**类名字符串**，如 'StaticMesh'）
        try:
            cls_name = await call_official(
                ctx, "get_asset_class", {"asset_path": asset_path}, toolset=TS_ASSET
            )
        except ToolError as exc:
            return None, None, f"类别没查到：{exc}"
        cls_text = str(cls_name or "")
        if "StaticMesh" in cls_text:
            resolved = "mesh"
        else:
            return None, None, f"{cls_text or '类别未知'} 没有包围盒"

    if resolved not in SIZE_QUERY_TYPES:
        # 档 ①：类别已知、且本来就属于"没有包围盒"的那类（材质/材质实例/贴图）
        return None, None, f"{resolved} 没有包围盒"

    try:
        box = await call_official(
            ctx,
            "get_bounds",
            {"mesh": {"refPath": to_object_path(asset_path)}},
            toolset="editor_toolset.toolsets.static_mesh.StaticMeshTools",
        )
    except ToolError as exc:
        return None, None, f"量取失败：{exc}"

    size = size_from_box(box)
    if size is None:
        return None, None, "官方没给出有效包围盒"
    # 包围盒体积 = X×Y×Z（经 num() 归一，免得负零/非有限值让宿主整条拒收）
    volume = num(size[0] * size[1] * size[2])
    return size, volume, "官方 get_bounds 实测（包围盒）"


# --- [完工-04] 模型（工具返回结构 = 模型看到的"参数说明书"）---
# 实测 2026-09-23 12:12 / 12:24：confirm_assets 与 get_asset_list 完整序列化往返，
# 含新增的 size_cm / bbox_volume_cm3 / size_source / items。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

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

    element: str = Field(description="画面元素名（给人看的标签，可带说明，如 'house 主屋'）")
    element_key: str = Field(
        default="",
        description=(
            "**元素关键词**（如 house / sidewalk），必须和 elements.json 里的元素名一致 —— "
            "get_asset_list 靠它判断哪些元素已经落位；"
            "留空会退回『标签首词推断』（不可靠，会明说是推断）"
        ),
    )
    asset_path: str = Field(description="确认使用的资产包路径")
    asset_type: str = Field(default="", description="类别")
    exists: bool = Field(
        # ⚠ `default` 不能删：本字段由工具自己回填（见函数体里的 model_copy），调用方不填。
        #   2026-09-23 曾因缺 default 被 pydantic 判成 required，与下面那句描述**自相矛盾** ——
        #   照描述办事的调用方一律被参数校验拒收，工具连函数体都进不去（check_tools 实测复现）。
        default=False,
        description=(
            "官方 exists() 验证结果 —— **必须为 true**。"
            "⚠ 调用方**不用填**：本工具会逐个调官方 exists() 把它覆盖掉"
        ),
    )
    size_cm: list[float] | None = Field(
        default=None,
        description=(
            "**实测**包围盒尺寸 [X, Y, Z]（厘米）—— 由 confirm_assets 调官方 get_bounds 回填，"
            "调用方填什么都会被覆盖；非网格（材质等）为 null"
        ),
    )
    bbox_volume_cm3: float | None = Field(
        default=None,
        description="**包围盒体积**（厘米³）= X×Y×Z；⚠ 是包围盒，不是网格实际体积",
    )
    size_source: str = Field(
        default="",
        description=(
            "尺寸是怎么来的 / 为什么没有：`官方 get_bounds 实测（包围盒）` / "
            "`<类别> 没有包围盒` / 量取失败原因"
        ),
    )
    note: str = Field(default="", description="备注（谁改的名、为什么选它等）")


class UnresolvedElement(BaseModel):
    """**元素清单里有、交付表里没有**对应资产的元素 —— 免得它静默消失。

    为什么要有这个结构（2026-09-23 实测暴露）：
      `elements.json` 确认了 14 条 / 9 个关键词，交付表只有 6 行 ——
      `window` / `sidewalk` / `driveway` 三个关键词**在交付表里没有任何交代**。
      信息其实没丢（`get_asset_list` 每次都会报 `missing_elements`），
      但**单看交付物本身看不出清单不完整** —— 读表的人会以为一共就 6 个元素。
    """

    element_key: str = Field(description="元素关键词（与 elements.json 里的元素名一致）")
    occurrences: int = Field(default=0, description="该关键词在元素清单里出现几处")
    note: str = Field(
        default="",
        description="元素清单里对它的说明 —— **原样带出，不改写**",
    )


class AssetListDelivery(BaseModel):
    """**最终交付物**：给用户的资产清单。"""

    delivered_at: str = Field(description="交付时间")
    source_image: str = Field(description="参考图路径")
    total: int = Field(description="一共几个元素")
    verified: int = Field(description="路径验证通过几个")
    invalid_paths: list[str] = Field(description="**验证不存在的路径**（必须处理掉）")
    items: list[AssetListItem] = Field(description="资产清单")
    unresolved_elements: list[UnresolvedElement] = Field(
        default_factory=list,
        description=(
            "**元素清单里有、本表里没有对应资产**的元素 —— 不许静默消失。"
            "note 是元素清单原话（可能已写明归属，如『按光照环境处理』）。"
            "⚠ 本工具**不推断**没落位的原因：只说事实，要原因得问用户。"
        ),
    )
    deliverable: str = Field(description="给人看的清单正文（直接贴给用户）")
    note: str = Field(description="说明与下一步")


class AssetListRow(BaseModel):
    """交付表里的**一行**（get_asset_list 用）—— 原样带出表里记的尺寸信息。"""

    element: str = Field(default="", description="画面元素标签")
    element_key: str = Field(default="", description="元素关键词（表里显式记的那个）")
    asset_path: str = Field(default="", description="资产包路径")
    asset_type: str = Field(default="", description="类别：mesh / material_instance / material")
    size_cm: list[float] | None = Field(
        default=None, description="交付时**实测**的包围盒尺寸 [X, Y, Z]（厘米）"
    )
    bbox_volume_cm3: float | None = Field(
        default=None, description="包围盒体积（厘米³）；⚠ 是包围盒，不是网格实际体积"
    )
    size_source: str = Field(default="", description="尺寸来源 / 为什么没有尺寸")
    note: str = Field(default="", description="交付时的备注")


class PathCheck(BaseModel):
    """清单里**一条路径**的验活结果（get_asset_list 用）。"""

    element: str = Field(default="", description="这一行对应的元素标签")
    asset_path: str = Field(description="清单里记的资产路径")
    ok: bool = Field(description="官方 exists() 是否通过")
    error: str = Field(default="", description="验证失败时的官方原文")


class AssetListStatus(BaseModel):
    """**「当前这张资产清单还作不作数」**的判定结果（get_asset_list 用）。

    字段分成四组，对应它回答的四个问题：
      有没有表   → has_list / delivered_at / source_image / total
      表变没变   → stale / signed_* / live_* / changed_types
      路径还活吗 → path_check / dead_paths
      缺的有着落吗 → missing_elements / new_candidates / coverage_mode
    """

    has_list: bool = Field(description="有没有交付过清单（false 时其余字段无意义）")
    delivered_at: str = Field(default="", description="清单是什么时候交付的")
    source_image: str = Field(default="", description="当初那张参考图")
    total: int = Field(default=0, description="清单里共几条")
    items: list[AssetListRow] = Field(
        default_factory=list,
        description="表里的**每一行**（含实测尺寸/包围盒体积），原样带出，不做二次加工",
    )
    stale: bool | None = Field(
        default=None,
        description="指纹对不上 = true（表已过期）；没有指纹可比 = null（旧版本落的表）",
    )
    signed_at: str = Field(default="", description="指纹是什么时候记的")
    signed_hash: str = Field(default="", description="签字时的资产库指纹")
    signed_counts: dict[str, int] = Field(
        default_factory=dict, description="签字时每类资产各多少个"
    )
    live_hash: str = Field(default="", description="刚测出来的资产库指纹")
    live_counts: dict[str, int] = Field(
        default_factory=dict, description="刚测出来每类资产各多少个"
    )
    changed_types: list[str] = Field(
        default_factory=list, description="变了的那几类，人话写法，如 'material_instance: 58 → 56'"
    )
    path_check: list[PathCheck] = Field(
        default_factory=list, description="逐条验活结果（verify_paths=false 时为空）"
    )
    dead_paths: list[str] = Field(
        default_factory=list, description="**已经验不存在的路径**（必须处理掉才能往下走）"
    )
    missing_elements: list[str] = Field(
        default_factory=list, description="还没有资产落位的元素关键词"
    )
    new_candidates: dict[str, list[str]] = Field(
        default_factory=dict, description="缺的元素在实况库存里**名字对得上**的新候选（元素 → 路径）"
    )
    coverage_mode: str = Field(
        default="", description="『元素已落位』是怎么判的：element_key 显式标注 / 标签首词推断"
    )
    official_calls: int = Field(default=0, description="本次调了几次官方工具")
    needs_user_confirmation: bool = Field(
        default=True, description="恒为 true —— 用户确认前不许往下走"
    )
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class PlanGate(BaseModel):
    """规划图的**闸门状态**：这张图还作不作数。"""

    confirmed: bool = Field(description="用户/客户是否已确认这张图")
    params_hash_ok: bool = Field(
        description="参数指纹是否仍与出图时一致（false = 参数改过，这张图作废）"
    )
    plan_hash_ok: bool = Field(
        description="几何指纹是否仍与出图时一致（false = 文件被手改过）"
    )
    confirmed_by: str = Field(default="", description="谁确认的")
    confirmed_at: str = Field(default="", description="什么时候确认的（UTC）")
    params_hash: str = Field(default="", description="当前参数指纹")
    plan_hash: str = Field(default="", description="当前几何指纹")


class PlanSummary(BaseModel):
    """规划规模摘要（给人看的几个数）。"""

    roads: int = Field(default=0, description="道路条数")
    lots: int = Field(default=0, description="宅基地块数")
    green_zones: int = Field(default=0, description="绿化区个数（公共区域）")
    no_place_zones: int = Field(default=0, description="禁止放置区域个数")
    checks_total: int = Field(default=0, description="几何自检项数")
    checks_failed: int = Field(default=0, description="没过的项数（必须为 0）")
    failed_names: list[str] = Field(default_factory=list, description="没过的是哪几项")


class PlanResult(BaseModel):
    """出图 / 确认的结果。"""

    stage: str = Field(description="阶段二 · 平面布局规划")
    unit: str = Field(default="m", description="单位（米制）")
    data_path: str = Field(description="几何数据文件 —— **后续计算只读它**")
    image_path: str = Field(description="给客户看的图 —— **仅展示，计算不读它**")
    summary: PlanSummary = Field(description="规模摘要")
    gate: PlanGate = Field(description="闸门状态")
    changed_params: list[str] = Field(
        default_factory=list, description="本次覆盖了哪些参数（点号路径；空 = 没改）"
    )
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class PlanStatus(BaseModel):
    """『当前规划图还作不作数』+ 计划本体（get_plan 用）。"""

    has_plan: bool = Field(description="有没有出过图")
    data_path: str = Field(default="", description="几何数据文件")
    image_path: str = Field(default="", description="给客户看的图")
    summary: PlanSummary = Field(default_factory=PlanSummary, description="规模摘要")
    gate: PlanGate | None = Field(
        default=None, description="闸门状态；**confirmed=false 时不许进第三阶段**"
    )
    plan: dict | None = Field(
        default=None, description="完整计划数据（include_plan=true 时带出）"
    )
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑")


def candidates_for(
    keyword: str, inventory: dict[str, list[str]], limit: int = 5
) -> list[FoundAsset]:
    """在实况库存里按【资产名】+【文件夹名】两条路找候选（与 plan_assets 同一套判据）。

    ⚠ 只返回**名字对得上**的；名字对不上就是空列表 —— **禁止猜"疑似是它"**。
      这是本项目两次实测误报之后定下的硬规则，这里照旧执行。

    ⚠ **为什么这个函数待在「模型」区后面**（2026-09-23 血账，别搬回去）：
      签名里写了 `-> list[FoundAsset]`，而**函数签名上的注解是在 `def` 被执行的那一刻
      求值的**（不是等到调用时）。只要 FoundAsset 还没定义，模块 import 就抛
      `NameError: name 'FoundAsset' is not defined` —— server 直接起不来。
      第一版我把它放在"厚函数"区（模型区之前），实测正是这个报错，
      宿主侧表现为 mcp-client(mine) 重连 10 次全败。
      结论：**返回值里带模型的函数，必须放在模型定义之后。**
    """
    kw = keyword.lower().strip()
    out: list[FoundAsset] = []
    if not kw:
        return out
    for type_name, paths in inventory.items():
        for p in paths:
            name = p.rsplit("/", 1)[-1]          # rsplit(...,1)[-1] = 取最后一段 = 资产名
            matched_by, seg = "", ""
            if token_match(kw, name):
                matched_by = "资产名"
            else:
                for part in p.split("/"):        # 逐段试文件夹名
                    if part and token_match(kw, part):
                        matched_by, seg = "文件夹名", part
                        break
            if matched_by:
                out.append(
                    FoundAsset(name=name, path=p, asset_type=type_name,
                               matched_by=matched_by, matched_segment=seg)
                )
    # 排序：资产名命中的排前（比文件夹名命中更可信），同档按路径短→长
    out.sort(key=lambda a: (a.matched_by != "资产名", len(a.path)))
    return out[:limit]


# --- [完工-05] 工具 1：链路自检 ---
# 实测 2026-09-23 12:07：返回 reachable=true / protocol 2025-11-25 / 52 个工具集。
# 实测 2026-09-23 晚：docstring 增补后复跑 —— check_tools「连上官方」「工具集数量」通过，
#   描述断言 2 条（编排层定位 / 不许存盘）通过。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

@mcp.tool()
async def official_status(ctx: Context[AppContext]) -> OfficialStatus:
    """自检：能不能连上官方 Unreal MCP，并报告协议版本和工具集数量。

    任何依赖官方的操作之前，先用它确认链路。连不上时返回**错误原文**，不猜原因。

    ⚠ 本 server 是**编排层**：自己完全不碰 UE —— 改关卡 / 摆 Actor / 改材质都要经官方
      `unreal` 那侧的工具做。
    ⚠ **没有对应工具的能力 = 还没实现** —— 如实说「还没做」，不许硬凑、不许假装能做。
    ⚠ 讲流程时**以 `docs/` 下的规划文档为准，不许凭印象**。
    ⚠ 硬性禁止：不许寻找/猜测「疑似资产」；不许删资产；**不许存盘**（保存 UE 关卡 / 资产）。
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


# --- [完工-06] 工具 2：确认元素清单 ---
# 实测 2026-09-23T10:26:36Z：catalog/elements.json（14 个元素）由它落盘。
# 实测 2026-09-23 晚：复跑 check_tools ——「记录成功」「元素清单已落盘」通过，
#   描述断言 1 条（元素粒度由资产库定）通过。（此前那句"本会话没复测过"到此消掉。）
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

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
    ⚠ 元素粒度由**资产库**定，不由画面部件数定：屋顶 / 石材基座 / 路缘石这类通常
      **并进主体资产**，拆出来只会给 plan_assets 喂搜不到的词、制造假缺口。
      同一 element 可以**多行**（同关键词在多处出现）；覆盖判定按关键词集合算，不按条数算。
    ⚠ 图上**看不清**的：留在清单 + 写「⚠ 待核实」+ 给备用关键词；**不许凭空添一条，也不许删**。
      清单是给用户**逐条确认**的 —— 塞进猜测会污染他的确认，删掉该留的会丢信息。
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


# --- [完工-07] 工具 3：找资产（阶段一主工具）---
# 实测 2026-09-23 12:07（不传参读元素清单）、12:12（keywords=["grass"]）两次调用。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

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
    # ⚠ 2026-09-23 已把这段枚举**收敛到 library_inventory() 一处**
    #   （原来 plan_assets 和 get_asset_list 各有一份拷贝）。
    #   行为差别只有一处：返回的路径现在**排过序**，于是"同长度候选谁被截进
    #   max_per_element"从『官方返回顺序』变成『路径字典序』——更确定，
    #   但严格说不是 100% 逐字节等价。
    inventory, enum_calls = await library_inventory(ctx)
    calls += enum_calls

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


# --- [完工-10] 工具 4：改名（用户指路之后）----------------------------------------------

# 实测 2026-09-23 晚：check_tools 4 条全绿 —— 默认 dry_run / 一个都没真改 /
#   拦下带斜杠的非法名（**本地就挡住，不发去 UE**）/ 给出了改名前后路径。
#   ⚠ 它**只测预览路径**（confirm=False），绝不真改用户资产。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

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


# --- [完工-08] 工具 5：确认并交付资产清单（阶段一收尾）---
# 实测 2026-09-23 12:12（6/6 exists + 指纹签字）、12:24（4 个网格实测尺寸 + 重签）。
# 实测 2026-09-23 晚：复跑 check_tools —— 原先失败的 3 条（验证计数 / 清单正文 / 先处理提示）全绿，
#   且末尾 `[还原]` 行列出了 ['asset_list.json','elements.json','library_snapshot.json']，
#   证明**函数体真跑到了写盘那一步**（不只是参数校验过了）。
#   ⚠ 本轮修过一个 bug：`AssetListItem.exists` 缺 default —— 原因与影响面写在那个字段上。
# ⚠ 2026-09-23 晚（二）：新增 `unresolved_elements`（未落位元素），**尚无实测证据** ——
#   要验就跑一次 confirm_assets，看返回里有没有把 window / sidewalk / driveway 列出来。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

@mcp.tool()
async def confirm_assets(
    ctx: Context[AppContext],
    items: Annotated[
        list[AssetListItem],
        Field(description="用户确认过的「元素 → 资产」对照表（exists 字段由本工具回填）"),
    ],
) -> AssetListDelivery:
    """**阶段一收尾 + 交付**：验证资产是否真的存在 → 实测尺寸 → 落盘 → 签字 → 输出清单。

    做六件事：
      ① 逐个调官方 `exists()` **验证路径**（不是形式主义，见下）
      ② **实测尺寸**：网格调官方 `get_bounds`，把包围盒 X×Y×Z 与体积**回填进表**
         （调用方填的尺寸一律被实测值覆盖 —— 理由见 measure_asset 的说明）
      ③ 落盘到 `catalog/asset_list.json`（这是清单文件，不是 UE 资产，不违反"不许存盘"）
      ④ **给表签字**：记下当时的资产库指纹到 `catalog/library_snapshot.json`，
         之后 `get_asset_list()` 才判定得了这张表过没过期
      ⑤ 生成 `deliverable` —— 一段**给人看的清单正文**（带尺寸/体积两列），直接贴给用户确认
      ⑥ 列出**未落位元素**（元素清单里有、本表里没有对应资产）到 `unresolved_elements` ——
         免得它静默消失：读表的人必须一眼看出"这份清单不完整"，而不是以为一共就这么多

    ⚠ 每行的 `element_key` 请填**元素关键词**（与 elements.json 里的元素名一致）：
      `get_asset_list()` 靠它算"还有哪些元素没落位"。不填就只能按标签首词推断，
      而那只是推断 —— 返回值里会明说，不会假装是事实。

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

    measured = 0        # 量到包围盒的网格数
    no_size = 0         # 本来就没有包围盒的（材质/材质实例等）
    size_failed = 0     # 想量却没量到的

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

        # --- 尺寸：**实测**（覆盖调用方填的值）---
        # 路径不存在时不去量：那必定失败，白花一次调用，还多一条噪音报错。
        size: list[float] | None = None
        volume: float | None = None
        size_src = "路径不存在，没量"
        if ok:
            size, volume, size_src = await measure_asset(
                ctx, item.asset_path, item.asset_type
            )
        if size:
            measured += 1
        elif "没有包围盒" in size_src:
            no_size += 1
        else:
            size_failed += 1

        results.append(item.model_copy(update={
            "exists": ok,
            "size_cm": size,
            "bbox_volume_cm3": volume,
            "size_source": size_src,
        }))

    # --- 未落位元素：元素清单里有、交付表里没有的 —— 不许静默消失 ---
    # ⚠ 只报**事实**，不推断原因：为什么没资产要问用户，不许猜（"禁猜"是硬规则）。
    #   note 原样带出元素清单里那一句 —— 那里往往已写明归属
    #   （例：sky 写着"按【光照环境】处理…不按资产找（用户已拍板）"）。
    delivered_keys, _cov_mode = element_keys_from_items([r.model_dump() for r in results])
    _notes_by_key: dict[str, list[str]] = {}
    for _e in (stored.get("elements") or []):
        if not isinstance(_e, dict):
            continue
        _k = str(_e.get("element") or "").strip().lower()
        if _k:
            _notes_by_key.setdefault(_k, []).append(str(_e.get("note") or ""))
    unresolved = [
        UnresolvedElement(
            element_key=_k,
            occurrences=len(_ns),
            note=" / ".join(n for n in _ns if n),
        )
        for _k, _ns in sorted(_notes_by_key.items())
        if _k not in delivered_keys
    ]

    # 生成给人看的清单正文
    lines = ["资产清单（阶段一交付）", ""]
    lines.append(f"参考图：{stored.get('source_image') or '(未记录)'}")
    lines.append(f"元素数：{len(results)}    路径验证通过：{sum(1 for r in results if r.exists)}")
    lines.append(
        f"尺寸实测：{measured} 个网格量到包围盒，{no_size} 个非网格本来就没有，"
        f"{size_failed} 个没量到（原因见该行下方）"
    )
    lines.append("")
    lines.append(f"{'元素':<16} {'状态':<6} {'尺寸 X×Y×Z (cm)':<28} {'包围盒体积 (cm³)':>18}  资产路径")
    lines.append("-" * 122)
    for r in results:
        size_txt = "×".join(f"{v:.1f}" for v in r.size_cm) if r.size_cm else "—"
        vol_txt = f"{r.bbox_volume_cm3:,.0f}" if r.bbox_volume_cm3 is not None else "—"
        lines.append(
            f"{r.element:<16} {'✓ 存在' if r.exists else '✗ 不存在':<6} "
            f"{size_txt:<28} {vol_txt:>18}  {r.asset_path}"
        )
        if r.note:
            lines.append(f"{'':<16} {'':<6} └ {r.note}")
        if r.size_cm is None:
            lines.append(f"{'':<16} {'':<6} └ 无尺寸原因：{r.size_source or '(未记录)'}")
    lines.append("")
    lines.append(
        "⚠ 口径：尺寸/体积都是**官方 get_bounds 实测的包围盒**（体积 = X×Y×Z），"
        "**不是网格实际体积**；材质/材质实例没有包围盒，记 —。"
    )
    if invalid:
        lines.append("")
        lines.append("⚠ 以下路径验证不通过，必须先处理（让用户重新给路径，或确认跳过）：")
        for p in invalid:
            lines.append(f"  - {p}")

    if unresolved:
        lines.append("")
        lines.append(
            "⚠ 以下元素**在元素清单里有、但本表里没有对应资产** —— 不是被删了，是没落位："
        )
        for u in unresolved:
            lines.append(f"  - {u.element_key}（元素清单里 {u.occurrences} 处）")
            if u.note:
                lines.append(f"      └ 元素清单原话：{u.note}")
        lines.append(
            "  ⚠ 本工具**不推断**原因。可能是：找不到名字对得上的资产 / 路径验证不通过 / "
            "本来就按光照环境处理不走资产。**要原因，得问用户。**"
        )

    delivery = AssetListDelivery(
        delivered_at=datetime.now(timezone.utc).isoformat(),
        source_image=stored.get("source_image", ""),
        total=len(results),
        verified=sum(1 for r in results if r.exists),
        invalid_paths=invalid,
        items=results,
        unresolved_elements=unresolved,
        deliverable="\n".join(lines),
        note=(
            "资产清单已生成并落盘（catalog/asset_list.json）。"
            "**请把 deliverable 贴给用户做最终确认。**"
            if not invalid
            else f"⚠ 有 {len(invalid)} 个路径验证不通过 —— "
            "**先让用户处理掉**（重新给路径或确认跳过），再交付。"
        ),
    )
    # --- 给这张清单「签字」：记下当时的资产库指纹（best-effort）---
    # 为什么 best-effort：交付本身已经完成了。指纹只影响"下次判定得了判定不了过期"，
    # 不该因为它失败就把整次交付判死 —— 但失败**必须说出来**，不许默默吞掉。
    fingerprint_note = ""
    try:
        inventory, fp_calls = await library_inventory(ctx)
        digest, counts = library_fingerprint(inventory)
        save_json(LIBRARY_PATH, {
            "captured_at": delivery.delivered_at,
            "hash": digest,
            "counts": counts,
            "total": sum(counts.values()),
            "note": "confirm_assets 落盘时记的资产库指纹；只覆盖路径层面（哪些资产存在）",
        })
        fingerprint_note = (
            f"资产库指纹已记录（{sum(counts.values())} 个资产 / {fp_calls} 次枚举）—— "
            "之后 get_asset_list() 才判定得了这张表过没过期。"
        )
    except Exception as exc:
        # ⚠ 这里刻意接 `Exception`（比 `ToolError` 宽一档）：
        #   交付本身已经落盘了，指纹只是"下次判定过期"的依据，
        #   不该因为它出任何意外就把整次交付判失败。
        #   `Exception` 接不住 `CancelledError`（那是 BaseException 子类）——
        #   这正是我们要的：关机/取消要能正常往外抛。
        fingerprint_note = (
            f"⚠ 资产库指纹记录失败（{type(exc).__name__}: {exc}）—— "
            "这张表下次取用时会报『没有指纹』。"
        )

    delivery = delivery.model_copy(
        update={"note": f"{delivery.note} {fingerprint_note}"}
    )
    save_json(ASSET_LIST_PATH, delivery.model_dump())
    return delivery


# --- [完工-09] 工具 6：取清单（读）---
# 实测 2026-09-23 12:12（stale=false；bush 从"还缺的元素"里消失）、12:24（items 带尺寸）。
# 实测 2026-09-23 晚：docstring 增补后，描述断言「开场先查状态」绿；
#   另一条「不许用上下文里记得的旧表」当时**假红** —— 是断言串跨过了 `**`（断言的锅，不是描述的），
#   已修断言，**这条还没复跑**。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。
#
# 为什么需要它（用户 2026-09-23 拍板加）：
#   工具 5（confirm_assets）是"**写**表"，本工具是"**读**表 + 判定这张表还作不作数"。
#   没有它的时候，宿主只能自己去读 catalog/asset_list.json 那个文件 —— 那就**绕过了 MCP**，
#   等于把"用哪张表"交回给模型自觉。有了它，"表"这件事只有这一个入口。
#
# 它只读：不改 asset_list.json、不碰关卡、不猜。

@mcp.tool()
async def get_asset_list(
    ctx: Context[AppContext],
    verify_paths: Annotated[
        bool,
        Field(description="true = 逐条调官方 exists() 验活（默认）；false = 只比指纹，省调用"),
    ] = True,
    max_per_element: Annotated[
        int,
        Field(ge=1, le=20, description="每个还没落位的元素最多列几个新候选（默认 5）"),
    ] = 5,
) -> AssetListStatus:
    """取当前**权威资产清单**，并判定它**还作不作数**。动任何东西之前先调它。

    它回答四个问题：
      ① 有没有交付过清单 —— 没有就别往下走，先用 confirm_assets 落一份；
      ② 这张表还是不是签字时的那张 —— 用**资产库指纹**比（哈希 + 每类数量）；
      ③ 表里的路径**逐条**还活着吗 —— verify_paths=True 时调官方 exists() 验；
      ④ 还没落位的元素，现在出现候选了吗 —— 实况枚举后按【资产名/文件夹名】两条路匹。

    顺带把**整张表原样带出**（`items` 字段）：每行的元素、路径、类别，以及交付时
    **实测的包围盒尺寸与体积** —— "列出表"用这一个调用就够，不必去读 catalog 里的文件。

    ⚠ **只读**：不改 asset_list.json、不碰关卡、**不猜**。第 ④ 步只报"名字对得上"的候选，
      找不到就是空 —— 禁止拿别的资产顶替。
    ⚠ 指纹只覆盖**路径层面**（哪些资产存在），覆盖不了"同一个路径内容变了"
      （你把某个材质实例的贴图换了，路径没变、exists() 仍是 true）。
    ⚠ **开场先查状态**：讲流程 / 给方案 / 报进度**之前**先调本工具 —— 不是「动东西之前」，
      是「**开口之前**」。状态只认本工具的**返回值**，不认文件里叙述性的旧措辞
      （例：plan_params 里那句「待用户确认」早已过期）。
    ⚠ 宿主**不许**用上下文里记得的旧表；每次开工都必须重新调本工具。
    """
    # --- 读两份状态：交付表（工具 5 写的）+ 元素清单（工具 2 写的）---
    delivery = load_json(ASSET_LIST_PATH)
    elements_doc = load_json(ELEMENTS_PATH) or {}
    elements = [
        e.get("element", "")
        for e in elements_doc.get("elements", [])
        if isinstance(e, dict) and e.get("element")
    ]
    warnings: list[str] = []
    calls = 0

    # --- 情况 0：压根没交付过清单。这不是错误，是一种明确状态 ---
    if not delivery:
        return AssetListStatus(
            has_list=False,
            stale=None,                        # 没有表，就无所谓"过没过期"
            missing_elements=elements,
            next_step=(
                "还没有交付过资产清单：先 confirm_elements 记元素、plan_assets 找资产，"
                "再用 confirm_assets 落一份表（它会顺手给表记上资产库指纹）。"
                "**在这之前没有任何『权威清单』可用 —— 禁止凭上下文里的旧表干活。**"
            ),
            warnings=["catalog/asset_list.json 不存在。"],
        )

    items = delivery.get("items", []) or []
    # 表里的每一行**原样带出**（含交付时实测的尺寸/体积）—— 不做二次加工，读到什么给什么。
    # 这样"列出表"这一个动作就够用了，不需要再去读 catalog/asset_list.json 那个文件。
    rows = [
        AssetListRow(
            element=str(it.get("element") or ""),
            element_key=str(it.get("element_key") or ""),
            asset_path=str(it.get("asset_path") or ""),
            asset_type=str(it.get("asset_type") or ""),
            size_cm=it.get("size_cm"),
            bbox_volume_cm3=it.get("bbox_volume_cm3"),
            size_source=str(it.get("size_source") or ""),
            note=str(it.get("note") or ""),
        )
        for it in items
    ]

    # --- ① 实况枚举 → 当前指纹 ---
    inventory, calls = await library_inventory(ctx)
    live_hash, live_counts = library_fingerprint(inventory)

    # --- ② 和签字时的指纹比 ---
    signed = load_json(LIBRARY_PATH)
    if signed:
        signed_hash = signed.get("hash", "")
        signed_counts = signed.get("counts", {}) or {}
        stale = signed_hash != live_hash        # 哈希不等 = 资产库变过 = 这张表过期
    else:
        signed_hash, signed_counts, stale = "", {}, None
        warnings.append(
            "这张清单**没有指纹**（是旧版本 confirm_assets 落的，或指纹文件被删了）。"
            "重跑一次 confirm_assets 就能给它签字，之后才判定得了『还作不作数』。"
        )

    changed = describe_count_changes(signed_counts, live_counts) if signed else []

    # --- ③ 逐条验活（可关）---
    checks: list[PathCheck] = []
    if verify_paths:
        for it in items:
            path = it.get("asset_path", "")
            entry = PathCheck(element=it.get("element", ""), asset_path=path, ok=False)
            try:
                exists = await call_official(
                    ctx, "exists", {"path": path}, toolset=TS_ASSET
                )
                calls += 1
                # 官方可能回 True / "True"，两种都认
                entry.ok = exists is True or (
                    isinstance(exists, str) and exists.lower() == "true"
                )
            except ToolError as exc:
                calls += 1
                entry.error = str(exc)
            checks.append(entry)
    dead = [c.asset_path for c in checks if not c.ok]

    # --- ④ 还缺哪些元素 + 现在有没有名字对得上的新候选 ---
    covered, mode = element_keys_from_items(items)
    missing = [e for e in elements if e.lower().strip() not in covered]
    found: dict[str, list[str]] = {}
    for kw in missing:
        cands = candidates_for(kw, inventory, max_per_element)
        if cands:
            found[kw] = [c.path for c in cands]

    # --- 按优先级给出下一步（死路径 > 过期 > 有新候选 > 可以用）---
    if dead:
        next_step = (
            f"⚠ 表里有 {len(dead)} 条路径**已经不存在**（见 dead_paths）。"
            "**先让用户处理掉**（重新给路径，或明确确认跳过），再往下走 —— "
            "否则下一阶段会挂上一个解析不到的材质/网格。"
        )
    elif stale:
        next_step = (
            f"资产库指纹变了（{'；'.join(changed) or '数量未变，路径集合变了'}），"
            "**这张表已不是签字时的那张**。把差异摆给用户，让他决定："
            "是重跑 confirm_assets 出新表，还是先查清这次变化。"
        )
    elif found:
        names = "、".join(found)
        next_step = (
            f"清单本身是活的，但发现了**新候选**（{names}）。"
            "把候选原样报给用户确认用哪一个，确认后再 confirm_assets 更新表 —— **不许自己挑**。"
        )
    else:
        next_step = (
            "清单 fresh、路径全部存活，可以用。"
            "⚠ 照实说明：指纹只保证**路径有效**，不保证**同名资产的内容没被改过**。"
        )

    return AssetListStatus(
        has_list=True,
        delivered_at=delivery.get("delivered_at", ""),
        source_image=delivery.get("source_image", ""),
        total=len(items),
        items=rows,
        stale=stale,
        signed_at=signed.get("captured_at", "") if signed else "",
        signed_hash=signed_hash,
        signed_counts=signed_counts,
        live_hash=live_hash,
        live_counts=live_counts,
        changed_types=changed,
        path_check=checks,
        dead_paths=dead,
        missing_elements=missing,
        new_candidates=found,
        coverage_mode=mode,
        official_calls=calls,
        next_step=next_step,
        warnings=warnings,
    )


# --- 阶段二辅助：参数合并 / 摘要 / 闸门取数 -------------------------------------

_PLANNING = None
_PLAN_RENDER = None


def _planning_modules():
    """取规划层的两个模块（plan / plan_render）—— **惰性导入**，带两种启动方式的兜底。

    为什么不在文件顶部 import（2026-09-23 血账，两次炸 server 的第二次）：
      ① **启动方式不兼容**：宿主是按**脚本**方式起这个文件的
         （`python F:\\MCP_Server\\src\\mcp_server\\main.py`），这时 `__package__` 是空的，
         顶部写 `from .planning import ...` 会直接抛
         `ImportError: attempted relative import with no known parent package`
         —— **整个 server 起不来**（宿主侧表现为 mcp-client(mine) 重连 7 次全败）。
         所以这里先试相对导入（模块方式 `python -m mcp_server`），失败再退回顶层导入（脚本方式）。
      ② **不该连坐**：规划层（阶段二）出任何问题，都不该把阶段一那 6 个工具一起带崩。
         放进函数里，导入失败只在**调用阶段二工具时**报错，别的工具照常能用。
    """
    global _PLANNING, _PLAN_RENDER
    if _PLANNING is not None:
        return _PLANNING, _PLAN_RENDER

    errors: list[str] = []
    try:
        # ① 模块方式（python -m mcp_server）：此时 __package__ 有值，相对导入成立
        from .planning import plan as p, plan_render as r
    except ImportError as exc:
        errors.append(f"相对导入失败：{exc}")
        # ② 脚本方式（python ...\src\mcp_server\main.py）：__package__ 为空，相对导入必失败。
        #    这时**自己**把本文件所在目录放进 sys.path，再按顶层包导入 planning。
        #    ⚠ 别依赖"宿主会把脚本目录加进 sys.path" —— runpy 起的时候就不加（实测）。
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            from planning import plan as p, plan_render as r
        except ImportError as exc2:
            errors.append(f"顶层导入失败：{exc2}")
            # ⚠ 两个错误都带上：只报第一个会让人查错方向（这行是踩过坑才加的）
            raise ToolError("阶段二规划层导入失败（阶段一工具不受影响）："
                            + "；".join(errors)) from exc2
    _PLANNING, _PLAN_RENDER = p, r
    return _PLANNING, _PLAN_RENDER


def _merge_params(base: dict, over: dict, prefix: str = "") -> list[str]:
    """把 over **深合并**进 base（就地改 base），返回改了哪些键（点号路径）。

    ⚠ 只允许改 base 里**已经存在**的键：免得 agent 手一滑写出个拼错的参数名，
      结果"改了但没生效"还查不出来 —— 宁可当场报错。
    """
    changed: list[str] = []
    for key, value in (over or {}).items():
        path = f"{prefix}{key}"
        if key not in base:
            raise ToolError(
                f"参数里没有 {path!r} 这一项 —— 别凭空造参数，先看 config/plan_params.json"
            )
        if isinstance(value, dict):
            if not isinstance(base[key], dict):
                raise ToolError(f"{path!r} 不是一组参数，不能这样改")
            changed += _merge_params(base[key], value, prefix=f"{path}.")
        elif base[key] != value:
            base[key] = value
            changed.append(path)
    return changed


def _summarize(plan: dict) -> PlanSummary:
    """从计划数据里数几个数出来。"""
    checks = plan.get("checks", [])
    failed = [c.get("name", "?") for c in checks if not c.get("ok")]
    return PlanSummary(
        roads=len(plan.get("roads", [])),
        lots=len(plan.get("lots", [])),
        green_zones=len(plan.get("green_zones", [])),
        no_place_zones=len(plan.get("no_place_zones", [])),
        checks_total=len(checks),
        checks_failed=len(failed),
        failed_names=failed,
    )


def _gate_model(g: dict) -> PlanGate:
    """把 planning.gate_check() 的字典转成模型（显式取字段，不吃多余键）。"""
    return PlanGate(
        confirmed=bool(g.get("confirmed")),
        params_hash_ok=bool(g.get("params_hash_ok")),
        plan_hash_ok=bool(g.get("plan_hash_ok")),
        confirmed_by=g.get("confirmed_by", "") or "",
        confirmed_at=g.get("confirmed_at", "") or "",
        params_hash=g.get("params_hash", "") or "",
        plan_hash=g.get("plan_hash", "") or "",
    )


def _write_plan(plan: dict) -> None:
    """落盘计划数据：净化数字（-0.0 → 0.0）+ 禁用 NaN/Infinity（非法 JSON 当场炸）。"""
    planning, _ = _planning_modules()
    planning.OUT_JSON.write_text(
        json.dumps(planning.clean_numbers(plan), ensure_ascii=False, indent=2,
                   allow_nan=False),
        encoding="utf-8",
    )


# --- [完工-12] 工具 7：generate_plan（阶段二 · 出图；写）---
# 实测 2026-09-23 晚：docstring 增补后 —— 预检 ④（几何自检 / 出图）绿、描述断言「单位是米」绿。
# 实测 2026-09-23（**经 MCP 调用**，不是 CLI）：
#   · {"lot":{"frontage":12.0}} → changed_params=["lot.frontage"]、lots 4→5、14 项自检 0 失败；
#   · 随后 {"lot":{"frontage":15.0}} → lots 回 4、两个指纹 ok、
#     plan_hash 与上一次 15m 出图**逐字符相同**（同参数→同几何→同指纹，可复现）。
# ⚠ 这轮还经 MCP 抓出并修掉一个真 bug：指纹计算没净化 -0.0，导致对刚出的图误报"文件被改过"。
#   预检已加回归检查（第 ⑤ 项）。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。
#
# --- 工具 9（confirm_plan）的实测见「工具 9」那一节（[完工-13]）

# --- 工具 7：生成规划图（阶段二 · 出图；写）------------------------------------
#
# ⚠ 本工具**不依赖 UE**：纯 2D 几何 + 出图，编辑器关着也能跑。
#   它是「图 = 搭建的闸门」里的**出图**那一半；确认那一半是工具 9。

@mcp.tool()
async def generate_plan(
    overrides: Annotated[
        dict | None,
        Field(description=(
            '要改的参数（点号结构），如 {"lot": {"frontage": 12.0}}。'
            "只许改 config/plan_params.json 里已有的键；不传 = 按当前参数出图"
        )),
    ] = None,
) -> PlanResult:
    """按参数算出**平面布局规划**（几何 + 出图），落盘并返回闸门状态 —— 阶段二主工具。

    做四件事：
      ① 把 `overrides` 合并进 `config/plan_params.json`（**改参数不改代码**，这是通用化的前提）
      ② 纯几何算出：道路多边形 / 地块包围盒与朝向 / 公共区域绿化区 / 禁放区 + 几何自检
      ③ 落盘 `views/plan_v1.json`（**后续计算只读它**）+ 出 `views/plan_wire_v1.png`（给客户看）
      ④ 钉两个指纹（参数指纹 + 几何指纹），为"确认"做准备

    ⚠ **单位是米**（米制世界坐标；到 UE / Blender 那一层才 ×100 转厘米）。
      改参数前**先看该键的实际值、类型照抄** —— 如 `road.lot_sides` 是 `["left"]`，不是 `2`。
    ⚠ **阶段二的几何只有一个来源：`config/plan_params.json`。**
      **全程纯几何计算，不做任何图像识别推理**；参考图**只**用来人工确定两条参数
      （路网走向、地块密度）。**不许从参考图里估尺寸 / 读走向，也不许把那张 PNG 当数据源。**
    ⚠ 排一侧还是两侧由参数决定（`road.lot_sides` / `road.sidewalk_sides`）：
      **「参考图里只看得见一侧」不是限制 —— 对面同样是算出来的，改参数就能排。**
    ⚠ 本工具**不涉及任何具体房屋资产**：只切**地块**与地块内的**房前草坪区 / 建筑区**。
      「哪栋房子用哪个模型」属于第三阶段。
    ⚠ 出的是**全新的未确认图**：确认状态会清空（除非几何与参数都没变，才沿用上次确认）。
    ⚠ 图上带**示意**的房屋/树（只为让客户看懂），**不进 plan_v1.json、不参与任何计算**。
    ⚠ 客户不懂参数：他说"太稀 / 太挤 / 路太偏"，由你翻译成 overrides 重出图，**不用问客户术语**。
    """
    planning, plan_render = _planning_modules()
    params = planning.load_params()
    changed = _merge_params(params, overrides or {})
    if changed:
        planning.PARAMS_PATH.write_text(
            json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    plan = planning.build_plan(params)

    warnings: list[str] = []
    if planning.OUT_JSON.exists():
        old_conf = (json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
                    .get("confirmation", {}) or {})
        same = (old_conf.get("plan_hash") == plan["confirmation"]["plan_hash"]
                and old_conf.get("params_hash") == plan["confirmation"]["params_hash"])
        if old_conf.get("confirmed") and same:
            # 几何与参数都没变 → 沿用上次确认（重出一张一模一样的图，不该把确认弄丢）
            plan["confirmation"].update({
                "confirmed": True,
                "confirmed_by": old_conf.get("confirmed_by", ""),
                "confirmed_at": old_conf.get("confirmed_at", ""),
                "note": "几何与参数均未变，沿用上一次确认。",
            })
        elif old_conf.get("confirmed"):
            warnings.append(
                "这张图与上一次确认的**不是同一张**（参数或几何变了）：上次确认**已作废**，"
                "必须重新拿给用户/客户确认。"
            )

    planning.VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    _write_plan(plan)
    plan_render.render(plan, params, planning.OUT_PNG)

    return PlanResult(
        stage="阶段二 · 平面布局规划",
        data_path=str(planning.OUT_JSON),
        image_path=str(planning.OUT_PNG),
        summary=_summarize(plan),
        gate=_gate_model(planning.gate_check(params, plan)),
        changed_params=changed,
        next_step=(
            "把 image_path 那张图发给用户/客户看。客户只看图：他说不行，你改参数重出图；"
            "他点头后用 confirm_plan(确认人) 落痕。**确认之前不许进第三阶段。**"
        ),
        warnings=warnings,
    )


# --- [完工-11] 工具 8：get_plan（阶段二 · 读）---
# 实测 2026-09-23 晚：docstring 增补后 —— 预检 ⑤（闸门指纹自洽）绿，
#   描述断言 2 条（开场先查状态 / 报自检必须报 failed_names）绿。
# 实测 2026-09-23（**重启后经 MCP 调用**）：has_plan=true、自检 14 项 0 失败、
#   gate.confirmed=false（闸门正确拦着）、参数指纹与几何指纹两侧一致、
#   warnings 正确写出"这张规划图还没被确认 —— 不许进第三阶段"。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

# --- 工具 8：取规划图（阶段二 · 读）--------------------------------------------

@mcp.tool()
async def get_plan(
    include_plan: Annotated[
        bool, Field(description="true = 连完整计划数据一起带出（默认）；false = 只给闸门状态与摘要")
    ] = True,
) -> PlanStatus:
    """取当前规划图 + 判定**它还作不作数**。**动第三阶段之前必须先调它。**

    回答五件事：
      ① 有没有出过图；
      ② 这张图**确认了没有**；
      ③ 确认之后**参数或几何改过没有**（两个指纹对不对得上）；
      ④ 几何自检过没过（`checks_failed` 必须为 0）；
      ⑤ 这张图的几何**只来自参数**，不从参考图读、也不从那 PNG 读（图仅展示，计算不读它）。

    ⚠ **开场先查状态**：任何「走到哪了 / 下一步干什么 / 怎么搭」的回答**之前**先调本工具
      （与 get_asset_list 配套）。报「自检过了」必须报 `checks_failed` 与 `failed_names`，
      不许只说「几何自检通过」。
    ⚠ **只读**：不改参数、不出图、不确认、不猜。
    ⚠ `gate.confirmed = false` → **一律不许进第三阶段**（AGENTS.md「规划图闸门」）。
    """
    planning, _ = _planning_modules()
    if not planning.OUT_JSON.exists():
        return PlanStatus(
            has_plan=False,
            next_step=(
                "还没出过规划图：先 generate_plan() 出图，再让用户/客户看图确认。"
                "**没有确认过的规划图之前，不许往关卡里摆任何东西。**"
            ),
            warnings=["views/plan_v1.json 不存在。"],
        )

    params = planning.load_params()
    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    gate = _gate_model(planning.gate_check(params, plan))
    summary = _summarize(plan)

    warnings: list[str] = []
    if not gate.confirmed:
        warnings.append("这张规划图**还没被确认** —— 不许进第三阶段。")
    if not gate.params_hash_ok:
        warnings.append("参数已改过：这张图作废，必须重新出图并重新确认。")
    if not gate.plan_hash_ok:
        warnings.append("plan_v1.json 的几何被手改过，与出图时不一致。")
    if summary.checks_failed:
        warnings.append(f"几何自检有 {summary.checks_failed} 项没过：{summary.failed_names}")

    ok = gate.confirmed and gate.params_hash_ok and gate.plan_hash_ok and not summary.checks_failed
    return PlanStatus(
        has_plan=True,
        data_path=str(planning.OUT_JSON),
        image_path=str(planning.OUT_PNG),
        summary=summary,
        gate=gate,
        plan=plan if include_plan else None,
        next_step=(
            "闸门通过：可以进第三阶段（把资产按规则摆到地块上）。"
            if ok else
            "把 image_path 那张图发给用户/客户看；不行就改参数重出图，"
            "点头后用 confirm_plan(确认人) 落痕。"
        ),
        warnings=warnings,
    )


# --- [完工-13] 工具 9：confirm_plan（阶段二 · 闸门）---
# 实测 2026-09-23T13:56:45Z（**经 MCP 调用**，用户本人确认）：
#   gate.confirmed=true、confirmed_by="用户本人"、两个指纹一致、
#   next_step 明确写出"阶段二闭环 —— 可以进第三阶段"；
#   并已核实**写进了磁盘**（plan_v1.json 的 confirmation 段，required/confirmed/confirmed_by/
#   confirmed_at/params_hash/plan_hash 齐全）。
# ⚠ 这一条是用**真实确认**验的，不是伪造的 —— 闸门的意义就在于没人能绕过它。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

# --- 工具 9：确认规划图（阶段二 · 闸门）----------------------------------------

@mcp.tool()
async def confirm_plan(
    confirmed_by: Annotated[str, Field(description="谁确认的（用户/客户的称呼，必填）")],
) -> PlanResult:
    """把「这张规划图被确认了」写回 `plan_v1.json`：**谁 / 何时 / 哪份参数 / 哪份几何**。

    两道闸，任一不过就**拒绝确认**（这就是闸门的意义）：
      ① 参数指纹对不上 → 参数改过，这张图作废，必须重新出图；
      ② 几何指纹对不上 → `plan_v1.json` 被手改过，与出图时不一致。

    ⚠ 确认是**人的动作**：只有用户/客户**看过图并点头**之后，才准调它。
      你自己看着"差不多"就确认 = 把闸门作废。
    ⚠ 确认之后参数或几何一改，两个指纹就对不上，本次确认**自动作废**。
    """
    planning, _ = _planning_modules()
    params = planning.load_params()
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划图：先 generate_plan() 出图，让用户看过之后再确认。")

    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    gate = planning.gate_check(params, plan)
    if not gate["params_hash_ok"]:
        raise ToolError(
            "拒绝确认：config/plan_params.json 改过了，这张图作废 —— 先 generate_plan() 重新出图。"
        )
    if not gate["plan_hash_ok"]:
        raise ToolError("拒绝确认：plan_v1.json 的几何与出图时不一致（被改过）。")

    plan = planning.mark_confirmed(plan, confirmed_by, params)
    _write_plan(plan)
    return PlanResult(
        stage="阶段二 · 平面布局规划",
        data_path=str(planning.OUT_JSON),
        image_path=str(planning.OUT_PNG),
        summary=_summarize(plan),
        gate=_gate_model(planning.gate_check(params, plan)),
        changed_params=[],
        next_step=(
            f"已确认并留痕（确认人：{confirmed_by}）。**阶段二闭环** —— "
            "可以进第三阶段：把资产按规则摆到地块上。"
        ),
        warnings=[],
    )


# --- 启动区 -------------------------------------------------------------------
# 只有"直接运行这个文件"时才启动；被 mcp dev / mcp run / 宿主导入时不启动。
if __name__ == "__main__":
    # 不带参数 = stdio（进程直连），由宿主负责启动这个进程
    mcp.run()
