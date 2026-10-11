# Copyright (c) 2026 ZHUPO233
# UEPLANE 插件 · 平台替身（**只在插件里用，不进产品仓库**）
#
# 为什么要有这个文件：UE 自带的 Python 3.11 里**没有 pydantic / mcp / anyio**
# （实测：site-packages 只有 pip / setuptools / pkg_resources）。而产品那套业务代码
# （`main.py` / `schemas.py` / `surfaces.py`）是写给"MCP 服务器"的，它们要这三样东西。
#
# 2026-10-11 的重构把这三样收进了 `bridge.py` 这一个接缝 —— 所以插件只要提供一个
# **同名同接口的 bridge** 就行。本文件就是那个 bridge 的底座：两个最小替身。
#
# ⚠ 只实现**实测用得到**的那几样，别扩：
#   · pydantic：`BaseModel`（kwargs 初始化 + 默认值 + 属性访问）/ `Field(default, default_factory,
#     description)` / `model_dump()` / `model_copy(update=…)`。
#     实测依据：全工程只有这 4 个 API（479 处 `Field(`、7 处 `model_dump()`、4 处 `model_copy(`、
#     **校验器 / ConfigDict / model_validate / model_fields 全部为 0**）。
#   · mcp：`ToolError`（真异常）/ `Context`（要能下标）/ `MCPServer`（`.tool()` 是**恒等装饰器**）。
#
# ⚠ **本替身不做类型校验**（pydantic 会做）。这是有意的取舍，代价照实说：
#   以前"漏了 await ⇒ 拿到协程对象"那种错会让 pydantic 当场报 ValidationError（诊断便利），
#   现在不会了。实测产品代码**不依赖**它做控制流（全工程 `ValidationError` 零处）。
#   ⚠ 未知的字段名**按 pydantic 默认行为忽略**（不是报错）—— 别改成报错，那会与真 pydantic 不一致。

import copy


# ══════════════════════════════════════════════════════════════════════════════
# 一、pydantic 替身
# ══════════════════════════════════════════════════════════════════════════════

class _Required:
    """`Field()` 没给默认值 = **必填**（与 pydantic 的 `PydanticUndefined` 同一个意思）。"""

    def __repr__(self):
        return "<REQUIRED>"


REQUIRED = _Required()


class FieldInfo:
    """`Field(...)` 的返回物：把"默认值 / 工厂 / 说明"一起记住，等 `__init__` 时用。"""

    __slots__ = ("default", "default_factory", "description", "extra")

    def __init__(self, default=REQUIRED, default_factory=None, description="", extra=None):
        self.default = default
        self.default_factory = default_factory
        self.description = description
        self.extra = extra or {}          # `ge=` / `le=` 这类约束：**收下但不用**（不校验）

    def __repr__(self):
        if self.default_factory is not None:
            return "Field(default_factory={!r})".format(self.default_factory)
        return "Field(default={!r})".format(self.default)


def Field(default=REQUIRED, *, default_factory=None, description="", **extra):
    """替身版 `pydantic.Field`。参数名与真的一致（至少用到的那几个）。"""
    return FieldInfo(default, default_factory, description, extra)


_FIELDS_CACHE = {}


def _fields_of(cls):
    """把类（含基类）的字段按**定义顺序**收集起来：`{字段名: FieldInfo 或 None}`。

    `None` 表示"只有注解、没有默认值"（裸注解 = 必填 —— 本工程实测没有这种，但替身得兜住）。
    """
    cached = _FIELDS_CACHE.get(cls)
    if cached is not None:
        return cached
    out = {}
    for base in reversed(cls.__mro__):            # 基类在前 ⇒ 子类覆盖基类
        if base is object or base is BaseModel:
            continue
        for name, ann in getattr(base, "__annotations__", {}).items():
            if name.startswith("_"):
                continue
            attr = base.__dict__.get(name, None)
            out[name] = attr if isinstance(attr, FieldInfo) else None
    _FIELDS_CACHE[cls] = out
    return out


def _dump_value(value):
    """`model_dump()` 用的递归转换：模型 / 列表 / 字典 / 时间 → 能 `json.dumps` 的东西。"""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, (list, tuple)):
        return [_dump_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _dump_value(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    iso = getattr(value, "isoformat", None)       # datetime / date
    if callable(iso):
        try:
            return iso()
        except Exception:                          # noqa: BLE001
            pass
    return value


class BaseModel:
    """替身版 `pydantic.BaseModel`（**只做"装值 / 取字段"，不做类型校验**）。"""

    def __init__(self, **kwargs):
        fields = _fields_of(type(self))
        unknown = [k for k in kwargs if k not in fields]
        for name, info in fields.items():
            if name in kwargs:
                value = kwargs[name]
            elif info is None:
                raise TypeError("{}.{} 必填（替身 BaseModel）".format(
                    type(self).__name__, name))
            elif info.default_factory is not None:
                value = info.default_factory()
            elif info.default is not REQUIRED:
                # ⚠ 与 pydantic 一致：**深拷一份**，免得两个实例共享同一个默认 list
                value = copy.deepcopy(info.default)
            else:
                raise TypeError("{}.{} 必填（替身 BaseModel）".format(
                    type(self).__name__, name))
            object.__setattr__(self, name, value)
        # ⚠ 未知字段**忽略**（pydantic 的默认行为是 ignore，不是报错）——
        #   但留个记号，排障时看得见（不改变行为）。
        object.__setattr__(self, "_ueplane_unknown_fields", unknown)

    # ---- pydantic v2 的那几个方法（本工程实测只用到这两个）----
    def model_dump(self, *args, **kwargs):        # noqa: D401 —— 参数收下不用（没人传）
        return {name: _dump_value(getattr(self, name)) for name in _fields_of(type(self))}

    def model_copy(self, *, update=None, deep=False):
        data = {name: getattr(self, name) for name in _fields_of(type(self))}
        if update:
            data.update(update)
        return type(self)(**data)

    def __repr__(self):
        bits = ", ".join("{}={!r}".format(n, getattr(self, n, None))
                         for n in _fields_of(type(self)))
        return "{}({})".format(type(self).__name__, bits)

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self.model_dump() == other.model_dump()


# ══════════════════════════════════════════════════════════════════════════════
# 二、mcp / anyio 替身
# ══════════════════════════════════════════════════════════════════════════════

class ToolError(Exception):
    """与 `mcp.server.mcpserver.exceptions.ToolError` 同一个角色：**给模型看的人话错误**。

    ⚠ 业务代码里 `raise ToolError("拒收：…")` 有近三百处 —— 插件侧靠**捕获它**把拒收原因
      原样回给客户端（见 `tools.py` 的包装），所以它必须是个真异常类。
    """


class _ToolDecorator:
    """`mcp.tool()` 的替身：**恒等装饰器** —— 原样放行，不做任何注册。

    为什么可以什么都不做：插件里的注册走 `@toolset_registry.tool_call`（见 `tools.py`），
    业务层那 20 个 `@mcp.tool()` 只是**服务器侧的挂号**，在这里必须无害。
    """

    def __call__(self, fn=None, **kwargs):
        if fn is None:                     # 有人写成 `@mcp.tool` 不带括号
            return lambda f: f
        return fn


class MCPServer:
    """`mcp.server.MCPServer` 的替身（插件里**不会真的起服务器**）。"""

    def __init__(self, name="", *args, **kwargs):
        self.name = name

    def tool(self, *args, **kwargs):
        return _ToolDecorator()

    def run(self, *args, **kwargs):
        raise RuntimeError(
            "插件里没有 `run()` —— 这不是一个独立的 MCP 服务器；"
            "工具走 UE 的 ToolsetRegistry 注册（见 tools.py）。"
        )

    def list_tools(self, *args, **kwargs):
        return []


class Context:
    """`mcp.server.mcpserver.Context` 的替身。

    ⚠ 业务层的签名里有 `ctx: Context[AppContext]`，而 `main.py` **没有**
      `from __future__ import annotations` ⇒ 这个下标是**定义时求值**的，
      所以替身必须支持 `__class_getitem__`（否则 import 阶段就炸）。
    ⚠ 插件里我们**传 None 当 ctx** —— 已核过：全工程只有 3 处真的用 `ctx`，都在
      服务器侧的传输层，插件那份 bridge 不碰它。
    """

    @classmethod
    def __class_getitem__(cls, item):
        return cls


class Client:
    """`mcp.Client` 的替身（插件里不用 —— 走进程内 `execute_tool`）。"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("插件里不用 mcp.Client —— 官方调用走进程内 execute_tool")


class _AnyioShim:
    """`anyio` 的替身。

    ⚠ 实测 `main.py` 里**只剩一处** anyio 用法：`call_official()` 重连分支里的
      `await anyio.sleep(0.1)`（那个分支在插件里基本走不到，但**必须能跑**，否则就是
      NameError/AttributeError 而不是"如实报错"）。
    ⚠ `Event` / `fail_after` / `create_task_group` 只被**服务器侧**的连接 task 用到，
      插件里不会再走那条路 —— 但为了"名字齐全"也给上，省得以后有人 import 就炸。
    """

    @staticmethod
    async def sleep(seconds):
        import asyncio
        await asyncio.sleep(seconds)

    class Event:
        def __init__(self):
            import asyncio
            self._ev = asyncio.Event()

        def set(self):
            self._ev.set()

        def is_set(self):
            return self._ev.is_set()

        async def wait(self):
            await self._ev.wait()

    @staticmethod
    def fail_after(seconds):
        raise RuntimeError("插件里不该用 anyio.fail_after（那是服务器侧连接 task 的东西）")

    @staticmethod
    def create_task_group():
        raise RuntimeError("插件里不该用 anyio.create_task_group（那是服务器侧连接 task 的东西）")


anyio = _AnyioShim()
