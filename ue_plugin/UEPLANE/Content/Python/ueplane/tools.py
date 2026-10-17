# Copyright (c) 2026 ZHUPO233
# UEPLANE 插件 · 工具面（`@toolset_registry.tool_call` 薄包装）
#
# 为什么是"手写薄包装"而不是把 `@mcp.tool()` 换掉：
#   toolset 的参数 schema 靠**类型标注**生成，**不支持嵌套容器**（实测：`list[dict]` 会在
#   注册期直接报 `directly nested containers are not supported`，整个工具集一起死）。
#   ⇒ 两边**面向模型的签名天生不同**，这层适配没法省。
#
# ── 三条写包装的规矩（都是实测定的，别凭感觉改）────────────────────────────────
# ① **复杂入参一律收成 JSON 字符串**：`list[...]` / `dict` → `str`，命名 `<原名>_json`。
#    内部 `_opt()` / `_models()` 解回 Python 对象；**空串 = 没传**。
# ② **`""` 默认值的 str 参数一律写成必填**（不写默认值）：
#    UE 生成 schema 时**空串默认值会被当成"没有默认值"** ⇒ 那个参数变必填、默认值丢失。
#    写成"有默认值"就是 schema 与代码两套口径 —— 索性照实写成必填，文档里写明「留空 = 没传」。
#    ⚠ `bool` / `int` / **非空**默认值的 str（如 `mode="auto"`）实测**是真可选**，保留默认值。
# ③ **返回值统一 JSON 字符串，三态分明**（**不许把"没读到"说成"没问题"**）：
#    `{"ok": true, "result": …}` / `{"ok": false, "refused": "拒收原文"}` / `{"ok": false, "error": …}`
#
# ⚠ **工具描述目前是压缩过的**（不与产品那份 docstring 逐字一致）——
#   产品的原文更长（含大量实测出处）。要做成逐字一致，得让注册读到的 `__doc__` 来自产品函数，
#   这条**待实测**（见 `移植实测记录.md` 第 9 节）。
#
# ⚠ 工具个数 **20**，与服务器版**同一个工具面**（不许在插件里多造一件）。

import asyncio
import json

import toolset_registry
import unreal
from toolset_registry.registration import Registration

from ._shims import ToolError
from . import bridge as B
from . import main as M


def _say(msg):
    unreal.log_warning("[UEPLANE/tools] " + str(msg))


# ── 私有事件循环：驱动那些 `async def` 工具 ────────────────────────────────────
# ⚠ 为什么可以这么干（2026-10-11 实测）：进程内 `execute_tool` **19/19 当场完成**
#   （`is_complete=true`、0.000~0.022 秒）⇒ 这些协程**永远不会真的让出**
#   ⇒ 在 game thread 上同步等它，不会死锁。探针 `probe_asyncio()` 已直接验过。
# ⚠ 循环**留着复用**（每次新建一个 ProactorEventLoop 会反复申请 IOCP 句柄）。
_LOOP = None


def _loop():
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        _say("建了私有事件循环 {}".format(_LOOP))
    return _LOOP


def _payload(result):
    """把工具返回值转成能 `json.dumps` 的东西（模型 → dict）。"""
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return result


def _call(factory):
    """跑一个 `async def` 工具，把三态结果序列化。**不吞异常**：原文照报。"""
    try:
        got = _loop().run_until_complete(factory())
    except ToolError as exc:
        # 业务层的"拒收"：原文照搬（那是给人看的判据说明，一个字都不许改）
        return json.dumps({"ok": False, "refused": str(exc)}, ensure_ascii=False)
    except BaseException as exc:                              # noqa: BLE001
        # ⚠ 兜底也必须是 `BaseException`（同 bridge 里那条实测说明）：UE 那侧抛的错
        #   不一定继承自 `Exception`。宁可这里把原文照报，也不让它穿透成 MCP tool error。
        return json.dumps({"ok": False, "error": repr(exc)}, ensure_ascii=False)
    return json.dumps({"ok": True, "result": _payload(got)},
                      ensure_ascii=False, default=str)


# ── 入参换算（三种，全工程只有这三处口径）────────────────────────────────────
def _opt(text, default=None):
    """JSON 字符串 → Python 对象。**空串 / `null` = 没传**（返回 `default`）。"""
    s = str(text or "").strip()
    if not s or s.lower() == "null":
        return default
    return json.loads(s)


def _models(cls, text, default=None):
    """JSON 数组 → 模型对象数组（已经是模型的就不重复包）。"""
    got = _opt(text, None)
    if got is None:
        return default
    return [x if isinstance(x, cls) else cls(**x) for x in got]


def _bool3(text):
    """三态布尔：`""` → `None`（**没传**） / `"true"` → `True` / 其余 → `False`。

    ⚠ 为什么必须保留"没传"与"显式传 False"的区别：`create_surfaces(focus=…)` 就是靠
      `None` = 没传（tune 档按默认开）、显式传 = 当场拒收 —— 混成一个值就不是那个语义了。
    """
    s = str(text or "").strip().lower()
    if not s or s == "null":
        return None
    return s in ("1", "true", "yes", "y", "on")


_DOCED = [0, 0]          # [装上的个数, 总个数] —— 注册时打一行日志，别靠猜


def _doc(product_name):
    """把产品那份同名函数的 docstring 装到这个方法上 —— **必须是最内层的装饰器**。

    ⚠ 三层实测（2026-10-11，两轮失败换来的）：
      ① "注册前给 `getattr(UeplaneTools, n).__doc__` 赋值" → **成了 0 个**：
         那时方法已是 UE builtin，实测原文 `attribute '__doc__' of
         'builtin_function_or_method_with_closure' objects is not writable`。
      ② "类装饰器 `@_with_product_docs` 排在 `@unreal.uclass()` 里面" → **成了 0 个**，
         日志原文 `装了 0 个 / 本类共 0 个方法` —— **连数都没数到**：
         说明 `vars(cls)` 里根本看不到我们写的那些方法
         （`unreal.ToolsetDefinition` 那一侧在**类创建时**就把它们收走了，不用等 `unreal.uclass()`）。
      ③ ⇒ 只有**最内层**这条路：装饰器贴着 `def` 跑，那时它还是个普通 Python 函数、
         `__doc__` 可写，而且**早于** `staticmethod` / `tool_call` / 类创建。
    """
    def _wrap(fn):
        _DOCED[1] += 1
        doc = (getattr(getattr(M, product_name, None), "__doc__", "") or "").strip()
        if doc:
            # 🔴 **一处覆盖**（2026-10-12 全修）：工具描述是**静态文本**，没法按纪元动态改 ——
            #   而它们里面写着「重落盘 → 让用户看图确认 → confirm_plan()」这种**只属于纪元 1** 的流程
            #   （共十几处）。逐条改描述的风险是**改漏一处就是一处诱饵**，所以在这里**统一追加一句覆盖**：
            #   任何客户端拿到的**每一条**工具描述都会带上它 —— 一处生效、不会漏。
            doc = (
                doc
                + "\n\n🔴 **纪元 2（阶段三落地之后）—— 本文若写了相反的流程，以本行为准**："
                "①②③ 的写工具（`confirm_elements` / `confirm_assets` / `rename_assets` / "
                "`check_build_target` / `confirm_plan`）**一律拒收**，`generate_plan` 只放行 `patch`。"
                "**凡本文写「重落盘（页由代码自动重生成）→ 让用户看图确认 → `confirm_plan()`」的，"
                "都只属于纪元 1。** 纪元 2 的改动只有一条路：`request_plan_change`（记用户原话）→ "
                "`generate_plan(patch=…)` → `execute_build(only_labels=[…])` —— **不画图、不重确认**；"
                "验收 = 用户在 UE 里看，**要报数字**给他。状态只认 `get_plan()` 的 `stages` + `handoff`。"
                "\n\n🧭 **换图 = 换场景**（2026-10-14 起）：每个场景各记各的（元素 / 资产 / 规划 / "
                "台账 / 材质 / 环境 / 对账 / 验收）—— 换一张关卡图就**自动**换一套状态，"
                "**不需要任何重置动作**；新图天然是**纪元 1**，可以直接从 `confirm_elements()` 从头搭。"
                "要在**同一张图**上推倒重来 ⇒ `reset_scene()`（要**用户本人原话**）。"
                "\n\n📐 **场景规范（硬约束）**：见本 toolset 说明末尾「场景规范」一节 —— 数据一物一行 / "
                "`z_m` 是底面 / 资产高 = 实测包围盒 × scale；预览页由代码强制生成（居中 · 可点选 · "
                "树不是方块 · three 内联 · 失败写画面上），**不许手画图顶替**；搭建真网格优先、满覆盖率；"
                "**收尾必须报数字**。"
            )
            try:
                fn.__doc__ = doc
                _DOCED[0] += 1
            except Exception as exc:                          # noqa: BLE001
                _say("装描述失败 {}: {!r}".format(product_name, exc))
        return fn
    return _wrap


@unreal.uclass()
class UeplaneTools(unreal.ToolsetDefinition):
    """UEPLANE —— 用参考图快速复刻场景的那套**流程工具**（资产确认 → 平面放置规划 → 资产布局生成 → 微调 → 表面材质 → 环境搭建 → 评估与闭环迭代 → 导出/交换）。

    这八项就是 `main.py::STAGE_NAMES_8`（现行八阶段编号）；**"现在走到哪一步"以 `get_plan().stages` 的现算为准**，别拿这句话当状态。

    本插件版与服务器版**同一套业务代码**（`main.py` / `schemas.py` / `surfaces.py` / `planning/` 一字未改），
    只是把"官方调用"从 HTTP 换成了**进程内** toolset 调用。

    ⚠ 每轮开场先调 `official_status()`（链路闸的钥匙），再调 `get_plan()` 看走到哪一步。

    🔴 **纪元 2（阶段三落地之后）只走这一条路**：`request_plan_change`（记用户原话）→
    `generate_plan(patch=…)` → `execute_build(...)` —— **不画图、不重确认**，验收 = 用户在 UE 里
    自己看，**要报数字**给他。
    ⚠ **前三阶段的写工具在纪元 2 一律拒收**（`confirm_elements` / `confirm_assets` /
    `rename_assets` / `check_build_target` / `confirm_plan`；`generate_plan` 的**整表**形式也拒收）——
    「不可回到前三阶段」不只是冻结文件，**工具也封**；往回走没有路，别试。
    ⚠ **状态只认 `get_plan()` 的返回值**（`stages` 八阶段总览 + `handoff` 交接段）：**新开的会话**
    开场先调它就知道全貌 —— 别凭记忆、别读文件里的叙述性字段。

    📐 **场景规范（硬约束 · 2026-10-17 用户定案，不是建议）** —— 完整正文在插件根
    `STANDARD.md`；**但下面这段就是可执行的那份，别去找文件也能照做**：

    · **数据**：一物一行（**没有 count**）；`z_m` = 该行**底面**绝对标高；资产行高 = **实测包围盒 Z × scale**；
      查不到包围盒 ⇒ 高 0、页面画薄片并写明（**不许猜尺寸**）；预估值必须标「预估待核实」，不许伪装成实测。
      ⚠ **建筑必须逐栋成行、按参考图详细分布**：一排建筑 = 一行一栋（各带 pos / footprint / 高 / 朝向），
      **禁止**用一条「××建筑带」「××大墙 120 m」代表一排建筑或整条街的立面 —— 那等于把分布压成一个数字。
      连续的一件（一道长墙 / 一条绿篱 / 一条铺装）可以一行，**多栋建筑不行**。
    · **阶段二那张 three.js 页**：默认**居中于世界中心**（不许把相机丢到世界外面）+ 画**中心轴与世界边界框** +
      **右侧元素清单可点选** + **三维单击拾取** + **详情面板**（位置 / 占地 / 底面 / 高 / 朝向 / scale / 资产路径 / note）+
      **树 = 树干 + 半透明树冠**（**不许实心方块**：8 m 冠幅 + 12 m 株距会变成两堵墙）+
      **示意放场景外**（如「原图视角」箭头）+ three.js **必须内联**（外挂 src 会被沙箱 CSP 静默拦掉 ⇒ 黑屏且不报错）+
      **任何失败写到画面上**。
      ⚠ 这些**由代码强制**：页 = `planning/figure3d.py` + `planning/web/preview3d.html`（three 已内联），
      落盘那一刻自动生成 —— agent 不需要"记得画好"，只需要把它交给用户看；**也不许手画图顶替**。
    · **搭建**：真网格优先；白膜**只在查不到资产时**登记（`is_placeholder` + `placeholder_shape` + 预估尺寸）；
      plan 的每一行都必须落地（满覆盖率）；改东西只走 `request_plan_change` → `generate_plan(patch=…)` → `execute_build`。
      ⚠ **禁止快速搭建**：不许「先搭个大概、回头再补」、不许跳过闸门、不许用一块大地块凑覆盖率、
      不许留临时占位 Actor；落完立刻 `evaluate_layout()` 三方对账并报差异。
    · **严格还原参考图**：**参考图是唯一判据** —— 图上看得见的元素一个都不能少；尺寸 / 间距 / 朝向 / 层次
      （步道宽、树离路缘多远、墙多高、建筑退线、哪侧更密）都按图还原，**不许"差不多就行"**；
      看不清的标「待核实」+ 备用方案，**不许省略、也不许凭空加**；交付前顶视 / 原图视角 / 侧视各过一遍。
    · **收尾必须报数字**：改了几行、哪几行、前后值（例 `Y −12.5 → −17.5`）、搭了几个 Actor、每类几个、
      坐标范围、还有几行没落地。**"改好了"不算报告。**
    """;

    # ══ ① 开场先看 ═══════════════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("official_status")
    def official_status() -> str:
        """链路自检 —— **钥匙**：没先调它，任何碰 UE 的工具都会被拒收。

        【场景① 开场先看】每轮开场、动手之前调一次。报能不能连上官方工具集、已注册多少个。

        ⚠ 它是**闸的钥匙**：`_link_guard()` 认它 —— 它**报过连上**之后，别的碰 UE 的工具才放行。
        ⚠ 插件环境下"链路"= UE 的 ToolsetRegistry 在不在、官方工具调不调得通；
          `toolset_count` 是**我们认识的那几个**（进程内注册表），不是官方的 52 个。
        """
        return _call(lambda: M.official_status(B.CTX))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("get_plan")
    def get_plan(include_plan: bool = True) -> str:
        """读当前规划 + 闸门/验收状态 —— 【场景① 开场先看】每轮开场、以及每次写完放置表之后。

        **动第三阶段之前必须先调它**（回读闸：这一版 plan 写盘之后没调它，后面会拒收）。

        Args:
            include_plan: true（默认）= 连完整计划数据一起带出；false = 只给闸门状态与摘要。

        Returns:
            JSON 字符串：`ok` + `result`（含 `stages` 八阶段总览 / `acceptance` / `gate`）。
        """
        return _call(lambda: M.get_plan(B.CTX, include_plan=include_plan))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("get_asset_list")
    def get_asset_list(verify_paths: bool = True, max_per_element: int = 5) -> str:
        """取当前**权威资产清单**，并判定它**还作不作数**（开场三件之一）。动任何东西之前先调它。

        Args:
            verify_paths: true = 逐条调官方 exists() 验活（默认）；false = 只比指纹，省调用。
            max_per_element: 每个还没落位的元素最多列几个新候选（默认 5）。

        Returns:
            JSON 字符串：`ok` + `result`（清单原样 + 有没有过期 + 谁还没落位）。
        """
        return _call(lambda: M.get_asset_list(B.CTX, verify_paths=verify_paths,
                                              max_per_element=max_per_element))

    # ══ ② 阶段一：元素与资产 ═════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("confirm_elements")
    def confirm_elements(elements_json: str, source_image: str) -> str:
        """记录**用户已确认**的元素清单 —— 阶段一的第一步。

        ⚠ 调用前必须先让用户确认过这张表。**没确认就别调**。开工第一件事是**请用户上传参考图**：
          顶视图（平面：摆在哪 / 占地多大）+ 左右视图（立面：高度 / 体量）；只有一张也能做。

        Args:
            elements_json: 元素清单数组的 JSON 串。每行 `{element, category, count, note, …}`。
                ⚠ 表里那两个**场景项**（地图大小 / 地图对应时间）也要填，否则拒收。
            source_image: 参考图路径（留档用）。**空串 = 没有图**。

        Returns:
            JSON 字符串：`ok` + `result`（元素清单落盘结果）。
        """
        return _call(lambda: M.confirm_elements(B.CTX, 
            _models(M.ElementItem, elements_json, []), source_image=source_image))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("find_assets")
    def find_assets(keywords_json: str, max_per_element: int = 4) -> str:
        """按名字找资产 → 报告**找到 / 没找到**，并对每个元素给一句要问用户的话。

        ⚠ 名字对不上就是**没找到**（不许猜、不许拿别的顶替）。

        Args:
            keywords_json: 要查的元素名数组的 JSON 串。**空串 = 用 confirm_elements 记的那份清单**。
            max_per_element: 每个元素最多列几个候选（默认 4）。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.find_assets(B.CTX, keywords=_opt(keywords_json, None),
                                           max_per_element=max_per_element))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("rename_assets")
    def rename_assets(items_json: str, confirm: bool = False) -> str:
        """把用户指出的资产改成规范名 —— **默认只预览，不真改**（改的是他工程里的资产）。

        Args:
            items_json: 数组的 JSON 串，每行 `{path, new_name}`（**路径与名字都由用户提供**）。
            confirm: false = 只预览改名前后路径 + 谁引用了它（默认，安全）；true = 真的改。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.rename_assets(B.CTX, _models(M.RenameItem, items_json, []),
                                             confirm=confirm))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("confirm_assets")
    def confirm_assets(items_json: str) -> str:
        """逐条 exists() 验证资产 → 实测尺寸 → 落盘 → 签字 → 输出清单。**停下等用户确认**。

        ⚠ 白膜占位行（`is_placeholder=true`）要填 `placeholder_shape` + **预估** `size_cm`，
          且**不许带路径**；资产行必须有能验过的 `asset_path`。任一条不过**整批拒收**。

        Args:
            items_json: 「元素 → 资产」对照表的 JSON 串（`exists` 由本工具回填，不用填）。

        Returns:
            JSON 字符串：`ok` + `result`（含给人看的清单正文）。
        """
        return _call(lambda: M.confirm_assets(B.CTX,
                                              _models(M.AssetListItem, items_json, [])))

    # ══ ③ 阶段二：平面放置规划 ═══════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("generate_plan")
    def generate_plan(assets_json: str, whiteboxes_json: str, world_json: str,
                      params_json: str, patch_json: str) -> str:
        """把**平面放置表**落盘成活动那份 plan（阶段二主工具；**记录，不是规划**）。

        坐标由**你**看图 + 按用户要求规划好再传进来。本工具**在落盘那一刻自动生成一页 three.js**
        （`views/<plan 名>_view.html`；渲染层 `planning/figure3d.py` + `planning/web/preview3d.html`）
        —— **你不用去画任何东西**，也不许手画（2026-10-12 起；旧的"两张手绘矢量图"那套已删）。

        Args:
            assets_json: 资产行数组的 JSON 串。每行含 `element_key` / `label` / `asset_path` /
                `pos`（[X, Y] 米）/ `footprint_m` / `rot_deg` / `scale` / **`z_m`（底面绝对标高，米，必填）**。
            whiteboxes_json: 白膜行数组的 JSON 串。每行含 `shape` / **`height_m`（必填）** /
                `pos` / `footprint_m` / `size_source`（**必须写明是「预估」**）/ **`z_m`**。
            world_json: 世界信息对象 —— **必须给 `size`**，如 `{"center":[0,0],"size":[70,54]}`。
            params_json: 参数留痕对象（"这一版是怎么定的"）。**空串 = 不记**。
            patch_json: **局部改**（推荐用于只改几行）：`[{"op":"set","label":"…","set":{…}}]`，
                四种 `op`：`set` / `add_asset` / `add_whitebox` / `remove`。
                ⚠ **纪元 2 里改一份已确认的 plan 必须用 patch**（整表重发当场拒收）。
                ⚠ 整表与 patch **不能同时给**；第一版必须整表。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.generate_plan(B.CTX, 
            _opt(assets_json, None), _opt(whiteboxes_json, None), _opt(world_json, None),
            params=_opt(params_json, None), patch=_opt(patch_json, None)))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("request_plan_change")
    def request_plan_change(items_json: str, reason: str, by: str = "用户") -> str:
        """记下「用户要改什么」—— **不改几何**，它是「改动窗口」的钥匙。

        ⚠ 一份规划**被确认过之后**要再改内容，**必须先走它**；否则 `generate_plan` 直接拒收。

        ⚠ **参数顺序与产品那份不一样**（`reason` 提到 `by` 前面）：Python 不许"必填"排在
          "有默认值"后面。按名字传就无所谓 —— 客户端本来就是按名字传。

        Args:
            items_json: 字符串数组的 JSON 串（**一条一项、尽量用他的原话**），至少一条。
            reason: 为什么改 / 上下文。**空串 = 不记**。
            by: 谁提的（默认「用户」）。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.request_plan_change(B.CTX, _opt(items_json, []), by=by, reason=reason))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("confirm_plan")
    def confirm_plan(confirmed_by: str, user_quote: str) -> str:
        """把「这份规划被确认了」写回活动那份 plan —— 谁 / 何时 / 哪一版几何。

        ⚠ 确认是**人的动作**：只有用户**看过图并点头**之后才准调它。三道闸：空规划不许确认 /
          几何指纹要对得上 / **图要认这份数据**（要出哪几张见 `get_plan().acceptance.change_set`）。

        Args:
            confirmed_by: 谁确认的（用户 / 客户的称呼）。
            user_quote: **他本人点头的原话**（拿不出原话 = 没人看过）。**不许自己编**。

        Returns:
            JSON 字符串：`ok` + `result`，或 `refused`（闸门拒收原因）。
        """
        return _call(lambda: M.confirm_plan(B.CTX, confirmed_by=confirmed_by, user_quote=user_quote))

    # ══ ④ 阶段三：落关卡 ═════════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("check_build_target")
    def check_build_target(decision: str, target_level: str, user_quote: str) -> str:
        """【场景④ 落关卡】搭之前先问清楚「**搭在哪张图**」—— 只读，不碰关卡。

        阶段三第 1 步。**别动用户的 UE**：开搭之前必须问清楚目标是新图 / 他指定的既有图 / 当前这张图。

        Args:
            decision: 用户的答复 —— `current`（就用当前这张）/ `existing`（他指定的既有图，
                同时给 `target_level`）/ `new`（他自己在 UE 里建好并打开）。
                ⚠ **空串 = 还没问过用户** —— 那就只是问一遍，不记答复（`execute_build` 也不放行）。
            target_level: `decision="existing"` 时他指定的关卡路径（如 `/Game/Maps/Street`）。
            user_quote: **用户答复的原话**（给了 `decision` 就必填）—— 拿不出原话 = 你还没问过他。

        Returns:
            JSON 字符串：`ok` + `result`（含 `current_level` / `question` / `options` / `next_step`）。
        """
        return _call(lambda: M.check_build_target(
            B.CTX, decision=decision, target_level=target_level, user_quote=user_quote))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("execute_build")
    def execute_build(mode: str = "auto", force_full: bool = False, adopt: bool = False,
                      verify: str = "full", batch: bool = False, user_quote: str = "",
                      only_labels_json: str = "", confirm_stage3: str = "") -> str:
        """把放置表落进关卡 —— 默认**只动改过的行（增量）**，不推倒重来。

        ⚠ **绝不存盘**（不调任何 save 类工具）。
        ⚠ 落关卡前有两道"先看过现场"的闸（G15/G16）：得有一份**当版**的 `evaluate_layout()` 报告。

        Args:
            mode: `auto`（默认，有台账就增量）/ `incremental`（强制增量）/
                `full`（**全量重摆，会删掉 `UEMCP/` 下全部 Actor**，真跑还要 `force_full`）。
            force_full: 只有 `mode="full"` 真跑时才需要（确认你**确实**要把它们全删了重摆）。
            adopt: 只与 `mode="incremental"` 合用：**不改任何 Actor**，把关卡现状读成台账（基线）。
            verify: `full`（默认，连白膜尺寸一起验）/ `transform`（只验位置朝向，省调用）/
                `off`（完全不读回）。⚠ 省的是**确认能力**。
            batch: ⚠ **默认 false = 逐行老路**（一行一次调用，失败只影响那一行）—— `true` 那条
                （官方 `execute_tool_script`）在**从编辑器事件循环里发起**时会**整体失败**：
                官方原文 `Cannot run the event loop while another loop is running`（实测复现）。
                ⇒ 默认走**已被实测证明能用**的那条；`true` 留给你自己确认环境允许时再试。
            user_quote: 用户同意「按 plan 覆盖他手改的东西」的**原话**。**不许自己编**。
                ⚠ 要按 plan 覆盖他手改的东西，就把它填进来 —— 空着 = 没有任何凭据，一律拒收。
                两步闸：先调**默认参数**的 `execute_build()` 列清单（只读那几行的现状 + **留痕**）
                → **把清单原样交给用户、停下等他打字** → 再带**他本人的原话**调第二次。
            only_labels_json: 字符串数组的 JSON 串 —— **这一轮只把这几行挪回 plan 的位置**
                （点名的行**本来就在 plan 位置上**时会跳过）。空串 = 走默认增量差异。
            confirm_stage3: **手工旁路：提前换纪元**（⚠ 正常**不需要**它）。
                正常路径是**阶段三整批落地自动触发** —— `mode` 解析为 `full` 的那一次落地
                ⇒ 调用里**先在落位前冻结纪元 1、开纪元 2、资产表照抄成 v2，再当场落位**，
                并在报文里**告知**（用户口径：「一进批量落位就切」）。**增量微调绝不触发。**
                给这个参数 = 手工提前换（或台账补完后手工收尾），**不可逆**（落完回不到前三阶段）。
                ⚠ 它与 `only_labels` / `adopt` / `user_quote` **互斥**；走它时 `mode` **只许 `auto`**
                （⚠ 与 `main.py` 的实现口径一致：`full` / `force_full` 会当场拒收）。
                ⚠ 它那句「原话」由**调用方声明**，工具**无法核实**是不是人打的 ——
                `plan_v2.json` 里会如实记成 `promoted_quote_attested: false`。

        Returns:
            JSON 字符串：`ok` + `result`（逐行结果 / 人工痕迹 / `official_calls` / `next_step`）。
        """
        return _call(lambda: M.execute_build(
            B.CTX, mode=mode, force_full=force_full, adopt=adopt,
            verify=verify, batch=batch,
            user_quote=user_quote, only_labels=_opt(only_labels_json, []),
            confirm_stage3=confirm_stage3))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("adopt_user_edits")
    def adopt_user_edits(user_quote: str, only_json: str) -> str:
        """【场景④ 落关卡 · 认领手改】把「人工改过」的那几行，按**关卡现状**写回 plan 与台账。

        **不摆 Actor、不出图、不走阶段二循环**（省掉"重出一页 three.js + 重确认"那一整轮）。

        ⚠ 只管"人工改过"的行；plan 自己改过的行不归它（那是 `execute_build` 的活）。
        ⚠ 白膜行的 `height_m` 要读组件尺寸：**读不回来就跳过那一行**（不猜）。

        Args:
            user_quote: **用户本人的原话**（例「就用我手改的」）。**不许自己编**。
            only_json: 字符串数组的 JSON 串 —— 只认领这几个 `label`。**空串 = 全部**。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.adopt_user_edits(B.CTX, only=_opt(only_json, []),
                                                user_quote=user_quote))

    # ══ ⑤ 阶段五：表面材质 ═══════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("apply_surfaces")
    def apply_surfaces(mode: str = "apply", only_json: str = "", items_json: str = "",
                       user_quote: str = "", asset_slots_json: str = "",
                       focus: bool = False, focus_max: int = 3, save: bool = False) -> str:
        """【场景⑤ 表面与环境】白膜还是灰的、要把材质贴上去时 —— 按**材质清单**贴。

        ⚠ **本档没有演练**（`mode="apply"` **一调就是真写**）；唯一天然只读的是 `mode="probe"`。

        Args:
            mode: `apply`（默认，按材质清单贴**组件级覆盖**）/ `probe`（**只读**：找材质、出草稿清单）/
                `confirm`（把草稿 + 用户答复定稿成材质清单并签字，**必须带用户原话**）/
                `asset_slots`（改**网格资产**的材质槽 —— 影响所有没覆盖该槽的实例，**必须点名**）。
            only_json: `mode="apply"` 时只处理这几个 `element_key`（数组 JSON）。空串 = 全部。
            items_json: **只给 `confirm` 用**：定稿的材质清单行（数组 JSON）。空 = 拒收。
            user_quote: **只给 `confirm` 用**：用户本人的原话。拿不出 = 拒收。
            asset_slots_json: **只给 `asset_slots` 用**：`[{mesh, slot_name, material[, note]}]`。
            focus: 贴完把 UE 视口聚焦到刚贴的那些物体（免得不知道贴哪了）。
            focus_max: 最多聚焦几个（默认 3 —— 一次聚焦几十个用户只看得见最后一个）。
            save: 贴完**存盘**（用户满意那一步才打开）。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.apply_surfaces(
            B.CTX, only=_opt(only_json, None), mode=mode,
            items=_models(M.MaterialConfirmItem, items_json, None), user_quote=user_quote,
            asset_slots=_opt(asset_slots_json, None), focus=focus, focus_max=focus_max,
            save=save))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("create_surfaces")
    def create_surfaces(only_json: str, mat_mode: str = "create", tune_json: str = "",
                        focus: str = "", focus_max: int = 0, ops_json: str = "",
                        mat_target: str = "") -> str:
        """【场景⑤】缺材质就建、已有材质就读-改-写参数、要改材质图就用原语。

        ⚠ **没有演练这一步**（用户指令：`dry_run` 已整段删除）—— 稳当的用法是**缩小范围**：
          `only=[…]` 点名几条、或 `tune={…}` 只给一个参数。

        Args:
            only_json: `mode="create"`：只建这几个**包路径**（数组 JSON）。空 = 建清单里待自建的 + 配方表全部。
            mat_mode: `create`（默认）/ `tune`（对**已存在**的材质读-改-写参数）/ `ops`（材质图编辑**原语**）。
            tune_json: **只给 `tune`**：`{"<包路径>": {"<参数名>": 值}}`。
            focus: **只给 `tune`**：`""` = **没传**（tune 档默认开）/ `"true"` / `"false"`。
                ⚠ 必须保留"没传"与"显式传"的区别 —— 别的档显式传会**当场拒收**。
            focus_max: **只给 `tune`**：最多聚焦几个（`0` = 没传，按默认 3）。
            ops_json: **只给 `ops`**：材质图编辑原语数组（**每次调用 = 一批，整批校验不过一个都不执行**）。
            mat_target: **只给 `ops`**：要编辑的材质 / 材质函数的**包路径**。空串 = 没给。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.create_surfaces(
            B.CTX, only=_opt(only_json, []), mat_mode=mat_mode, tune=_opt(tune_json, None),
            focus=_bool3(focus), focus_max=(focus_max or None), ops=_opt(ops_json, None),
            mat_target=mat_target))

    # ══ ⑥ 阶段六：环境搭建 ═══════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("setup_environment")
    def setup_environment(preset: str, factors_json: str = "", actors_json: str = "",
                          overrides_json: str = "",
                          restore: bool = False, save: bool = True) -> str:
        """【场景⑥】按时段配**整套环境** —— 不只灯光：天空 / 大气 / 天光 / 高度雾 / 体积云 / 后处理 / 时段。

        ⚠ **本阶段是唯一允许存盘的阶段**（真跑成功后自动存盘）。
        ⚠ 本阶段**不出图**；验收方式 = 用户在 UE 里自己看。

        Args:
            preset: 用哪一档时段预设（如 `sunset` / `blue_hour` / `noon` / `night` / `overcast`）。
                **空串 = 用阶段一记录的那个时段**（再空就退到 `noon`）。
            factors_json: 只配这几个环境因素（数组 JSON）。空 = 预设里有什么配什么。
            actors_json: 点名要改哪一个 Actor，形如 `{"fog": "局部雾_1"}`。空 = 按类找第一个。
            overrides_json: 临时微调（四个保留键：`props` / `settings` / `transform` / `props`）。
            restore: true = **回滚**（把台账里记的现值写回去）。此时别的参数都不用给。
            save: 真跑成功后自动存盘（默认 true）。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.setup_environment(
            B.CTX, preset=preset, factors=_opt(factors_json, None),
            actors=_opt(actors_json, None), overrides=_opt(overrides_json, None),
            restore=restore, save=save))

    # ══ ⑦ 阶段七：评估与出图 ═════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("evaluate_layout")
    def evaluate_layout(include_materials: bool = True, all_rows: bool = False) -> str:
        """【场景⑥ 导出·对账·出图】三方逐行对账（**只读**）：`plan` ↔ 搭建台账 ↔ **关卡现状**。

        回答一句话：**「现在关卡里的东西，跟当初确认的那一版还对得上吗」**。

        ⚠ **只报数、不判合格不合格、不改任何东西**。
        ⚠ UE 连不上时**不报错**：只出 `plan ↔ 台账` 那半，并点名「关卡那一维这次没查」——
          **不许把「没报」当成「没问题」**。

        Args:
            include_materials: true（默认）= 白膜行再读一次材质覆盖（每行约 2 次官方调用）。
            all_rows: true = 连**一致**的行也列出来（默认只列不一致的）。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.evaluate_layout(
            B.CTX, include_materials=include_materials, all_rows=all_rows))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("capture_preview")
    def capture_preview(shots_json: str, annotations: bool = False) -> str:
        """【场景⑥】按**世界坐标**摆机位出图 —— **不动用户的视口相机**。

        ⚠ 图**不回传给模型**（PNG 落 `views/preview/`）；要看图去读那个文件。
        ⚠ 官方**画不出中文**（标签在图上渲染成乱码，JSON 里回报的字符串是对的）。

        Args:
            shots_json: 机位数组的 JSON 串，每项 `{name, pos_m:[X,Y,Z], look_at_m:[X,Y,Z], fov}`
                （**都是米**、世界坐标）。**空串 = 按 plan 的世界范围自动出三个**
                （俯视 3/4 + 两端人视）。
            annotations: true = 图上叠**世界网格 + Actor 标签**（核对构图用）；false（默认）= 干净图。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.capture_preview(
            B.CTX, shots=_models(M.PreviewShot, shots_json, None),
            annotations=annotations))

    # ══ ⑧ 阶段八：导出 / 交换 ════════════════════════════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("export_layout")
    def export_layout(ue_dir: str) -> str:
        """【场景⑥】把当前布局导成一份**与 DCC 无关**的场景描述（给 Blender 等用）。

        ⚠ **导不了几何**（官方没有 glTF / FBX / 场景导出工具）—— 消费端是照着这份描述**重摆一遍**。
        ⚠ **只读导出**：权威几何永远是**活动那份 plan**。

        Args:
            ue_dir: 要同时写进 UE 工程时，给它 **`Saved` 目录的绝对路径**。
                **空串 = 只写我们自己的 `views/exchange/`**。

        Returns:
            JSON 字符串：`ok` + `result`。
        """
        return _call(lambda: M.export_layout(B.CTX, ue_dir=ue_dir))

    @toolset_registry.tool_call
    @staticmethod
    @_doc("check_exchange")
    def check_exchange(path: str) -> str:
        """【场景⑥】把（可能被 Blender 那边改过的）交换文件与**当前 plan** 逐条比 —— **只出报告**。

        ⚠ **本工具不改 plan、不写任何文件**；它连"用户看过"都算不上，**别拿它当放行凭据**。

        Args:
            path: 要比的交换文件路径。**空串 = 我们自己的 `views/exchange/scene_v1.json`**。

        Returns:
            JSON 字符串：`ok` + `result`（逐条 `same` / `changed` / `added` / `removed`）。
        """
        return _call(lambda: M.check_exchange(B.CTX, path=path))

    # ══ ③ 阶段三：一键重置（**同一张图上从头再来**） ═══════════════════════════

    @toolset_registry.tool_call
    @staticmethod
    @_doc("reset_scene")
    def reset_scene(reason: str, user_quote: str = "", purge_actors: bool = False,
                    adopt_foreign: bool = False) -> str:
        """【场景④ 落关卡】**同一张关卡图上从头再来** —— 把这张图的场景状态整批归档、回到纪元 1。

        ⚠ **换图不需要它**：换一张关卡图 = 自动换场景（各场景各记各的）——
        它管的是「**就在这张图上**推倒重来」。
        ⚠ **它只归档、不删**；**默认一个 Actor 都不碰**。
        ⚠ **两段式**：先 `reset_scene(reason="…")`（不带 `user_quote`）→ 把那份现状清单
        **原样交给用户、停下等他打字** → 再带**他本人的原话**调第二次。

        Args:
            reason: **为什么重来**（必填）—— 例：上一版搭废了 / 元素清单要重列。
            user_quote: **用户本人的原话**。⚠ **空串 = 第一步：只问、不执行**（留痕后拒收）。
            purge_actors: ⚠ **默认 false**：只归档我们自己的使用物，**一个 Actor 都不碰**。
                true 才连 `UEMCP/` 与 `UEMCP_env/` 下的 Actor 一起清（只清这两个文件夹）。
            adopt_foreign: 确认要在当前工程里用一份「属于另一个工程」的场景。

        Returns:
            JSON 字符串：`ok` + `result`，或 `refused`（闸门拒收原因）。
        """
        return _call(lambda: M.reset_scene(
            B.CTX, reason=reason, user_quote=user_quote,
            purge_actors=purge_actors, adopt_foreign=adopt_foreign))


# 注册入口：init_unreal.py 调 `_registration.register()`
# ⚠ 工具描述已经由**每个方法最内层的 `@_doc("名字")`** 装好了（贴着 `def`，所以早于
#   `staticmethod` / `tool_call` / 类创建）—— 这里**不需要**、也**不能**再补一次
#   （实测：`unreal.uclass()` 之后方法变成 UE builtin，`__doc__` 不可写）。
_registration = Registration([UeplaneTools])
_say("工具描述同步：装了 {} 个 / {} 个方法".format(_DOCED[0], _DOCED[1]))
if _DOCED[0] < _DOCED[1]:
    _say("  ⚠ 没全装上 —— 那几件的描述会是插件源码里那些**压缩版**（兜底），不是产品的原文")

