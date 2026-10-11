# Copyright (c) 2026 ZHUPO233
# UEPLANE 插件 · 平台层（**插件版 bridge**）
#
# 与产品仓库里的 `src/mcp_server/bridge.py` **同名、同接口、逐个导出**：
#   `BaseModel` / `Field` / `Client` / `MCPServer` / `Context` / `ToolError` / `anyio` /
#   `OFFICIAL_URL` / `AppContext` / `official_client` / `mcp` / `run` / `_mark_link_state` / `_link_guard`
#
# 于是 `main.py` / `schemas.py` / `surfaces.py` / `planning/plan.py` 这四个文件在
# **服务器与插件两边是同一份，一个字不改**（插件侧只是拷贝）。
#
# 差在哪：服务器那份经 **HTTP** 打 `127.0.0.1:8000/mcp`；
#        这份经 **进程内** `toolset_registry.get_toolset_registry().execute_tool(...)`。
#
# ⚠ 关键一招：**不去改 `call_official`**。它（在 main.py 里，一个字没改）要的只是一个
#   `client`，只要满足 `await client.call_tool(name, payload)` → `result.is_error` /
#   `result.content[0].text`，而 `parse_return()` 只认 `{"returnValue": ...}` 那段文本
#   —— **这正好就是官方 `execute_tool` 的 `value` 原样**（2026-10-11 实测）。
#   所以这里给一个"假客户端"，整条传输就换完了。
#
# ⚠ 实测过的前提（见 `移植实测记录.md`）：
#   · 19 次官方调用**全部 `is_complete=true`、0.000~0.022 秒** ⇒ 进程内这条路**没有异步等待**；
#   · 写（`SelectActors`）与建 Actor（`add_to_scene_from_class` + `add_cube` + 删）都通；
#   · 引用形状 `{"refPath": …}` 与 HTTP 那条路**同形**。

import json
import time

import unreal

from ._shims import (BaseModel, Client, Context, Field, MCPServer, ToolError,   # noqa: F401
                     anyio)
from ._shims import FieldInfo, REQUIRED                                          # noqa: F401


def _say(msg):
    unreal.log_warning("[UEPLANE/bridge] " + str(msg))


# ══════════════════════════════════════════════════════════════════════════════
# 一、官方工具集的名字（**全部来自实测**的日志 `Registering Toolset …`，不是推导的）
# ══════════════════════════════════════════════════════════════════════════════

TOOLSETS = {
    "scene":  "editor_toolset.toolsets.scene.SceneTools",
    "actor":  "editor_toolset.toolsets.actor.ActorTools",
    "prim":   "editor_toolset.toolsets.primitive.PrimitiveTools",
    "object": "editor_toolset.toolsets.object.ObjectTools",
    "asset":  "editor_toolset.toolsets.asset.AssetTools",
    "smesh":  "editor_toolset.toolsets.static_mesh.StaticMeshTools",
    "kmesh":  "editor_toolset.toolsets.skeletal_mesh.SkeletalMeshTools",
    "minst":  "editor_toolset.toolsets.material_instance.MaterialInstanceTools",
    "mat":    "editor_toolset.toolsets.material.MaterialTools",
    "app":    "EditorToolset.EditorAppToolset",
    "logs":   "EditorToolset.LogsToolset",
    "prog":   "editor_toolset.toolsets.programmatic.ProgrammaticToolset",
}

# 工具名 → 工具集键（main.py 一般会显式传 toolset；这张表是**兜底**，没登记的如实报）
TOOL_TO_TOOLSET = {
    "get_current_level": "scene", "get_folders": "scene", "get_actors_in_folder": "scene",
    "find_actors": "scene", "add_to_scene_from_asset": "scene", "add_to_scene_from_class": "scene",
    "remove_from_scene": "scene", "set_actor_folder": "scene", "list_levels": "scene",
    "get_collision_channels": "scene",
    "get_actor_transform": "actor", "get_actor_bounds": "actor", "get_label": "actor",
    "get_components": "actor", "set_actor_transform": "actor", "get_tags": "actor",
    "get_root_component": "actor",
    "add_cube": "prim", "add_cone": "prim", "add_cylinder": "prim", "add_sphere": "prim",
    "get_properties": "object", "set_properties": "object", "list_properties": "object",
    "get_class": "object",
    "exists": "asset", "get_asset_class": "asset", "save_assets": "asset",
    "list_folders": "asset", "find_assets": "asset",
    "get_material_slots": "smesh", "get_material": "smesh", "set_material": "smesh",
    "get_bounds": "smesh",
    "list_parameters": "minst",
    "get_scalar_parameter": "minst", "set_scalar_parameter": "minst",
    "get_vector_parameter": "minst", "set_vector_parameter": "minst",
    "get_texture_parameter": "minst", "set_texture_parameter": "minst",
    "get_static_switch_parameter": "minst", "set_static_switch_parameter": "minst",
    "FocusOnActors": "app", "SelectActors": "app",
    "GetSelectedActors": "app", "GetVisibleActors": "app",
    "CaptureViewport": "app", "IsPIERunning": "app",
    "create_material": "mat", "add_expression": "mat", "connect_to_output": "mat",
    "recompile": "mat", "list_expression_classes": "mat", "get_property_input": "mat",
    "delete_unused_expressions": "mat", "layout_expressions": "mat", "get_expressions": "mat",
    "execute_tool_script": "prog",
}


def _registry():
    """拿 toolset registry（官方那侧的唯一入口）。路径来自实测探针。"""
    from toolset_registry._registry_interface import get_toolset_registry
    return get_toolset_registry()


def _resolve_toolset(toolset, tool):
    key = str(toolset or "").strip() or TOOL_TO_TOOLSET.get(str(tool), "")
    return TOOLSETS.get(key, key if "." in str(key) else "")


# ── 「文件夹不存在」这件事，**必须由我们替官方答**（2026-10-11 实测，两轮才定位到）──────
# ⚠ 现象：`setup_environment(restore=true)` **回滚真的做完了**（环境台账 `history` 里写着
#   "写回原来那组值"），但整次调用却以 tool error 收场：
#       !! TOOL ERROR:  Folder does not exist: 'UEMCP_env'
# ⚠ 根因（证据链）：官方 `get_actors_in_folder` 对**不存在的文件夹**不是回一个 `is_error`
#   结果，而是在 UE 那侧记一条 `LogScript: Warning: Folder does not exist: 'xxx'`
#   **（日志实测原文）**，随后 **MCP 层把这条当成整次调用的结果** —— 我们方法里已经拿到的
#   成功返回值被**覆盖**成 `isError`。
# ⚠ 为什么产品那边没兜住：产品的容错是 `except ToolError` + `"does not exist" in str(exc)`，
#   而这条路上**根本没有 ToolError 抛出来**。判据：返回文本里**没有**
#   `官方工具 … 返回错误：` 那个前缀（那个前缀是 `call_official()` 里 `once()` 加的）；
#   我一度把 `except Exception` 改成 `except BaseException` 想兜住它 —— **没兜住，因为不在
#   我们的 Python 帧里**。详见 `移植实测记录.md`。
# ⇒ 修法只能是**预防**：文件夹不存在时，**我们自己合成同一个错误文本、不去调官方**
#   （文本一模一样 ⇒ 产品那边的判据**一个字不用改**，`except ToolError` 分支照旧生效）。
_FOLDERS = {"at": 0.0, "names": None}
_FOLDERS_TTL = 2.0


def _known_folders(reg):
    """outliner 顶层文件夹名集合。

    ⚠ **问不出来就返回 `None`** —— 那表示"我不知道"，调用方**照旧去调官方**
      （宁可回到旧行为，也不拿"没问到"当"不存在"）。
    """
    now = time.time()
    if _FOLDERS["names"] is not None and (now - _FOLDERS["at"]) < _FOLDERS_TTL:
        return _FOLDERS["names"]
    try:
        got = reg.execute_tool(TOOLSETS["scene"], "get_folders", "{}")
        raw = getattr(got, "value", None)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            data = data.get("returnValue")
        names = {str(x) for x in (data or [])}
    except BaseException as exc:                              # noqa: BLE001
        _say("读文件夹列表失败（**不拦**，照旧去调官方）：{!r}".format(exc))
        return None
    _FOLDERS["names"] = names
    _FOLDERS["at"] = now
    return names


def _registry_value(toolset, tool, arguments):
    """调一次官方工具，返回 `(value 原文, 错误文本)`。

    ⚠ 返回的是 `execute_tool` 的 **`value` 原样**（那是一串 JSON，里面还包着 `returnValue`）
      —— **不解包**，因为 main.py 的 `parse_return()` 就是干这个的（与 HTTP 那条路同形）。
    """
    target = _resolve_toolset(toolset, tool)
    if not target:
        return None, "没有为工具 {!r} 登记 toolset —— 映射表（TOOL_TO_TOOLSET）要补一行".format(tool)
    try:
        reg = _registry()
    except BaseException as exc:                              # noqa: BLE001
        return None, "拿不到 toolset registry: {!r}".format(exc)
    try:
        # ① **预防**：文件夹不存在 ⇒ 自己合成错误，**不去调官方**（见上面那段说明）
        if str(tool) == "get_actors_in_folder":
            folder = str((arguments or {}).get("folder_path") or "")
            if folder:
                names = _known_folders(reg)
                if names is not None and folder not in names:
                    return None, "Folder does not exist: {!r}".format(folder)
        # ② 真正调官方
        result = reg.execute_tool(target, str(tool), json.dumps(arguments or {}))
    except BaseException as exc:                              # noqa: BLE001
        # ⚠ 兜底写 `BaseException`（不写 `Exception`）：UE 那侧抛的东西不保证继承自 Exception。
        #   ⚠ 但**别指望它接住"官方工具报错"** —— 实测那种错在 Python 帧**之外**冒出来
        #     （返回文本不带 `官方工具 … 返回错误：` 前缀就是证据），`call_official()` 不抛
        #     ToolError。所以靠的是 ① 那种**预防**，不是靠这里兜。
        return None, "{}".format(exc)
    # ③ 分组刚建过 ⇒ 文件夹列表已过期，**作废缓存**（否则会把刚建的分组误判成"不存在"）
    if str(tool) == "set_actor_folder":
        _FOLDERS["names"] = None
    err = getattr(result, "error", None)
    if err:
        return None, str(err)[:600]
    if not bool(getattr(result, "is_complete", True)):
        # ⚠ 实测从没出现过（19 次全 true）；真出现就**如实报**，不假装拿到了
        return None, "官方调用没有同步完成（is_complete=False）—— 进程内这条路第一次见"
    value = getattr(result, "value", None)
    return ("" if value is None else value), ""


_LIST_METHODS = ("get_toolset_names", "list_toolsets", "list_toolset_names",
                 "get_registered_toolsets", "get_all_toolsets", "toolset_names")
_CATALOG_NOTE = [""]          # 第一次探测到的"怎么列工具集"，留痕用


def _toolset_names(reg):
    """列已注册的工具集名 —— **先问 registry，问不出来才退回我们那张实测表**。

    ⚠ 为什么必须能列：`official_status()` 要调 `list_toolsets`（**MCP 的 meta-tool，
      不属于任何工具集** ⇒ `execute_tool` 调不到它），它是**链路闸的钥匙**。
      这里替它答：只要答案**如实**就行（有多少报多少，不假装是官方的 52 个）。
    """
    for name in _LIST_METHODS:
        fn = getattr(reg, name, None)
        if not callable(fn):
            continue
        try:
            got = fn()
        except Exception as exc:                              # noqa: BLE001
            _say("列工具集：reg.{}() 抛错 {}".format(name, repr(exc)))
            continue
        if isinstance(got, (list, tuple)) and got:
            names = [str(x) for x in got]
            if not _CATALOG_NOTE[0]:
                _CATALOG_NOTE[0] = "reg.{}()".format(name)
                _say("列工具集：用 reg.{}() 拿到 {} 个".format(name, len(names)))
            return names
        if got:
            _say("列工具集：reg.{}() 返回了非列表 {!r}".format(name, type(got).__name__))
    # 退路：拿我们那张**实测**表逐个问"注册了没有"
    names = [n for n in sorted(set(TOOLSETS.values()))
             if _is_registered(reg, n)]
    if not _CATALOG_NOTE[0]:
        _CATALOG_NOTE[0] = "实测表 + is_toolset_registered"
        _say("列工具集：registry 没有可用的列法，退回实测表 —— 认出 {} 个".format(len(names)))
    return names


def _is_registered(reg, name):
    fn = getattr(reg, "is_toolset_registered", None)
    if not callable(fn):
        return True                    # 问不出来就当它在（**宁可多列，不假装少**）—— 会在 note 里说明
    try:
        return bool(fn(name))
    except Exception:                                     # noqa: BLE001
        return True


def _catalog_text():
    """把工具集列成官方那种 `- <名字>: <说明>` 文本（`official_status()` 靠这个数个数）。"""
    try:
        reg = _registry()
        names = _toolset_names(reg)
    except Exception as exc:                              # noqa: BLE001
        raise ToolError("拿不到 toolset registry：{!r}".format(exc)) from None
    if not names:
        raise ToolError("toolset registry 里**一个工具集都没列出来** —— 插件没加载好？")
    head = "# 进程内注册表（不是官方的 HTTP 服务器）——列法：{}".format(
        _CATALOG_NOTE[0] or "（未知）")
    return "\n".join([head] + ["- {}: 进程内已注册".format(n) for n in names])


# ══════════════════════════════════════════════════════════════════════════════
# 二、假客户端：让 main.py 的 `call_official()` **一个字不改**地换掉传输
# ══════════════════════════════════════════════════════════════════════════════

class _TextContent:
    __slots__ = ("type", "text")

    def __init__(self, text):
        self.type = "text"
        self.text = text


class _CallResult:
    __slots__ = ("is_error", "content")

    def __init__(self, text, is_error=False):
        self.is_error = bool(is_error)
        self.content = [_TextContent(text)]


class _InProcessClient:
    """冒充 `mcp.Client`：`call_tool` 直接在**本进程**里派给 toolset registry。

    ⚠ 与 HTTP 那条路**同形**：`is_error` / `content[0].text` / `{"returnValue": …}` 三层一致。
    """

    protocol_version = "in-process(ToolsetRegistry)"

    async def call_tool(self, name, payload=None):
        payload = payload if isinstance(payload, dict) else {}
        if str(name) == "call_tool":
            raw, err = _registry_value(payload.get("toolset_name") or "",
                                       payload.get("tool_name") or "",
                                       payload.get("arguments") or {})
            if err:
                return _CallResult(err, is_error=True)
            return _CallResult(raw, is_error=False)
        if str(name) == "list_toolsets":
            # ⚠ 它是 MCP 的 meta-tool，本来不在任何工具集里 —— 这里**替它答**，
            #   并如实说明"这是进程内注册表，不是官方的 HTTP 服务器"。
            try:
                return _CallResult(_catalog_text(), is_error=False)
            except ToolError as exc:
                return _CallResult(str(exc), is_error=True)
        return _CallResult(
            "插件里没有顶层 MCP 工具 {!r} —— 只有工具集工具（经 call_tool 派发）".format(name),
            is_error=True)


_CLIENT = _InProcessClient()


# ══════════════════════════════════════════════════════════════════════════════
# 三、接口：与产品那份 bridge **逐个同名**
# ══════════════════════════════════════════════════════════════════════════════

OFFICIAL_URL = "in-process://ToolsetRegistry"
"""插件里没有 HTTP 地址 —— 留个说明串，免得有人以为它在连 8000 端口。"""

RETRY_SECONDS = 3.0
READY_TIMEOUT = 15.0


class _NoopFlag:
    """`app.stop` / `app.ready` 的替身：插件里没有"重连"这回事，收下就行。"""

    def set(self):
        pass

    def is_set(self):
        return False

    async def wait(self):
        return None


class AppContext:
    """服务器侧的生命周期状态。插件里**没有**连接 task —— 这里给一个"收下不报错"的替身。

    ⚠ 为什么不能图省事传 `None`（2026-10-11 **实测踩到**，第一轮就撞了）：
      `main.py::call_official()` 的**第一句**就是
        `app = ctx.request_context.lifespan_context`
      （它给**失败重连**那段逻辑用）—— 传 `None` 当场
      `AttributeError: 'NoneType' object has no attribute 'request_context'`，
      `official_status()` 直接失败 ⇒ **链路闸的钥匙拿不到**，后面全被拒。
      「全工程只有 3 处用 ctx」这个测量**本身没错**，但那 3 处**全在必经之路上**。
    """

    def __init__(self, *args, **kwargs):
        self.ue = None
        self.error = "插件里没有连接 task（进程内调用不需要）"
        self.stop = _NoopFlag()
        self.ready = _NoopFlag()


class _RequestContext:
    def __init__(self):
        self.lifespan_context = AppContext()


class _PluginContext:
    """给业务层当 `ctx` 用的替身。

    ⚠ `Context`（`_shims` 里那个）只用于**类型标注**、不会被实例化 —— 真传进去的是本对象。
    """

    def __init__(self):
        self.request_context = _RequestContext()


CTX = _PluginContext()
"""业务层 20 个工具都收 `ctx` —— 插件里统一传这一个（它什么都不做，只求"读得动"）。"""


async def official_client(ctx, check_link=True):
    """取"当前可用的官方客户端" —— 插件里恒等于那个**进程内**的假客户端。

    ⚠ 本函数是**所有碰 UE 的路径的唯一咽喉**（与服务器侧同一个位置），
      所以链路闸也只在这一处实现 —— 一处改、全覆盖。
    ⚠ `ctx` **收下不用**：服务器侧要用它去拿 lifespan 里的连接；插件里连接就是本进程。
    """
    if check_link:
        _link_guard()
    try:
        reg = _registry()
        if not bool(reg.is_available()):
            raise ToolError("toolset registry 报告不可用（is_available() = False）")
    except ToolError:
        raise
    except Exception as exc:                              # noqa: BLE001
        raise ToolError("拿不到 toolset registry：{!r}".format(exc)) from None
    return _CLIENT


# `main.py` / `schemas.py` / `surfaces.py` 会从这里取这几个名字（与服务器那份同名）
mcp = MCPServer("UEPLANE-SceneLayout")


def run():
    raise RuntimeError(
        "插件里没有 run() —— 工具走 UE 的 ToolsetRegistry 注册（见 tools.py），"
        "这里没有『启动一个 server』这回事。"
    )


# --- 链路状态与闸：与产品那份 bridge **同一条判据**（只把"连不上"的说法换成插件版）------
_SESSION_LINK_OK = None
_SESSION_LINK_ERR = ""


def _mark_link_state(ok, err=""):
    """记下**链路检查的结果**（只有 `official_status()` 会调它）。"""
    global _SESSION_LINK_OK, _SESSION_LINK_ERR
    _SESSION_LINK_OK = bool(ok)
    _SESSION_LINK_ERR = str(err or "")


def _link_guard():
    """**动 UE 之前必须先确认链路** —— 与产品那份 bridge 同一条纪律、同一处咽喉。

    ⚠ 判据一点没放松：本进程里 `official_status()` **报过连上**才放行。
      插件的"链路"= toolset registry 在不在、官方工具调不调得通。
    """
    if _SESSION_LINK_OK is True:
        return
    if _SESSION_LINK_OK is None:
        raise ToolError(
            "拒收：**动 UE 之前必须先确认链路** —— 本进程里还没做过链路检查。\n"
            "请先调一次 `official_status()`（只读、一次就够）：它报已注册的工具集数，"
            "连不上时给**错误原文**；它通过之后，这一步以及后面每一步才放行。"
        )
    raise ToolError(
        "拒收：**链路没通**（本进程上一次 `official_status()` 报的是连不上）—— 先修好再动 UE：\n"
        f"· 上次的错误原文：{_SESSION_LINK_ERR or '(没有留下原文)'}\n"
        "· 插件环境下常见原因：① UE 编辑器没起全（ToolsetRegistry 还没注册完）；"
        "② UEPLANE 插件自身加载失败（看 Output Log 里的 `[UEPLANE]` 行）。\n"
        "修好后**再调一次 `official_status()`** 确认 —— 这道闸只认它这次的返回值。"
    )
