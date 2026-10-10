# -*- coding: utf-8 -*-
"""阶段五 · 表面材质（整层）—— 按用户 2026-10-04 更正后的口径**翻新重写**。

═══ 一句话说清这一层干什么 ═══
对着**阶段一签过字的资产清单**逐条找**材质实例（MaterialInstanceConstant，下称 MI）**，
找不到就问用户；确实没有的登记成"待自建"；把清单交给用户逐条确认（签字）之后，
**先建**（只建待自建的那种，建完再给它派生一份实例），**再去关卡里一个物体一个物体地贴**、
切视角让用户看、反复调，**用户满意了才把实例路径回写清单并存盘**，
最后按整张清单**批量贴一遍**，再对**清单里那些实例**调参数，直到用户说没有要调的了。

═══ 用户 2026-10-04 的原话（本层的权威口径）═══
> 把查实能做的但是说成做不了的全部删掉，阶段五也是有流程的，首先就是对资产清单里的东西找材质记下路径，
> 就像第一阶段一样，没有找到的就问是不是有，但名字不对说路径，真没有的再标注为待自建，
> 最后像第一阶段一样拿到材质清单，然后把待自建的材质先建出来然后指定物体在UE上贴上去和用户反复确认调试，
> 别忘了要切到那个目标物体视角去，免得贴上去用户不知道贴哪了，知道用户满意就保存创建材质实例填回材质清单，
> 最后整理出最终材质清单然后批量全部贴上，最后就是调正已有的材质实例的参数，
> 直到用户没有要调整的就结束第五阶段。
> （两处笔误照录：`知道用户满意` ＝ 直到用户满意；`调正` ＝ 调整。）

═══ 2026-10-04 第二次更正（用户在审代码时当场纠正，本层按它重写）═══
> 第五步应该把材质和材质实例都建出来，到时候第六步贴上去再继续调整材质和材质实例，
> 直到用户满意为止才写回清单，**清单里都只记下材质实例，也只找材质实例**。

由此定下四条**硬口径**（本文件所有判据都从这四条来）：

  ① **只找材质实例**：`probe` 找到的如果是"裸材质"（Material，不是 MI），
     不算找到了实例 —— 那一行标 `from_material`，意思是"**有材质、缺实例**，第 ⑤ 步去派生一份"。
  ② **只有待自建的才建材质**：`missing_self_build`（用户确认"确实没有"）才 `create_material`；
     建完**立刻给它派生实例**（材质是"母版"，实例才是贴上去、能调参的那一块）。
  ③ **有材质没实例的 ⇒ 只派生实例**：父级 = **拿到的那个资产**（Material 或 MI 都行）。
     派生之后把父级的**参数现值读出来、写进新实例当覆盖**（"数值提升为实例参数"）——
     这样那个值从此可以在实例上调，不用回头改母材质。
  ④ **清单整表只记实例**；`tune`（第 ⑨ 步）**只能调清单里的实例**（硬拦）。

═══ 为什么整层搬出 main.py（不是"顺手重构"）═══
  · `main.py` 原本 14807 行，阶段五占了近 3000 行，且分两段散在文件中部；
  · 本包已有先例：阶段二~四的规划层在 `planning/` 里；
  · 搬出来之后这一层**不依赖 server**：官方调用经 `SurfacesRepo` 注入（见下面那个 dataclass），
    所以它能在预检里离线断言，也能在没有 UE 的情况下被读。
  ⚠ **两件工具本身仍注册在 `main.py`**（`@mcp.tool()` 那里）—— 工具面仍是 20 个，
    本文件只提供实现，不自己注册任何工具（避免"第 21 件"）。

═══ 本层不许做的事（与全局纪律一致）═══
  · **不存盘**：除了第 ⑦ 步 `save=true` 那一次（用户满意之后），一律不调 `save_assets`。
  · **不猜**：参数名只认官方 `list_parameters` 的返回；槽名只认 `get_material_slots` 的返回；
    属性名只认 `ObjectTools.list_properties` 的返回；名字对不上就是"没找到"。
  · **不许把"没读到"说成"没问题"**：写后一律读回核对，读不回**如实报**。
  · **整批校验不过 ⇒ 一个字节都不写**（与 `confirm_assets` / `execute_build` 同一条纪律）。
  · **没有演练档**（用户原话：「把演练这一步删掉，不需要演练」）—— 本层一调就是真做；
    想小范围先试只能缩小输入（`only=[…]` / `tune={…}` 只给一个参数）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

# ⚠ 这里**只**从 mcp 包拿"跨层共用"的东西：错误类型与上下文类型。
#   其余（官方调用 / 读写 JSON / 路径转换 / toolset 常量）一律经 `SurfacesRepo` 注入 ——
#   本文件**绝不 import main**（那会形成循环 import，规划层当年就是被这个逼着改成惰性加载的）。
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError


# ══════════════════════════════════════════════════════════════════════════════
# 一、常量与枚举（全部集中在这里 —— 不许在函数里再拼一遍路径或状态字符串）
# ══════════════════════════════════════════════════════════════════════════════

# --- 使用物的落点（阶段五的"唯一权威清单"与它的草稿 / 签字指纹 / 台账）--------------
# ⚠ 包目录（`src/mcp_server/surfaces.py` → parents[0]=mcp_server / [1]=src / [2]=仓库根）
REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "catalog"
VIEWS_DIR = REPO_ROOT / "views"

MATERIAL_LIST_PATH = CATALOG_DIR / "material_list.json"          # 权威清单（`confirm` 落盘）
MATERIAL_LIST_DRAFT_PATH = CATALOG_DIR / "material_list_draft.json"  # `probe` 的草稿（给人确认）
MATERIAL_SNAPSHOT_PATH = CATALOG_DIR / "material_snapshot.json"  # 清单的签字指纹
SURFACE_LEDGER_PATH = VIEWS_DIR / "surface_materials_v1.json"    # 建 / 派生 / 调参的台账（回滚依据）
# ⚠ 阶段一的**资产清单**（`probe` 对着它逐条找材质）—— 单独列成常量，**不在函数里现拼路径**：
#   预检要把它指到一个不存在的路径上、断言"没有清单就拒收"（可测性），现拼路径那种写法测不到。
ASSET_LIST_PATH = CATALOG_DIR / "asset_list.json"

# --- 官方 `get_asset_class()` 报出来的三档（`_classify()` 的第三格）-------------------
#   用途只有一个：**分栏**（材质栏 / 实例栏）—— 所以它是"分类"，不是"母级路径"。
KIND_INSTANCE = "instance"   # `MaterialInstanceConstant` ⇒ 记进**材质实例**那一栏
KIND_MATERIAL = "material"   # `Material` ⇒ 记进**材质**那一栏（第 ⑤ 步照它派生实例）
KIND_OTHER = "other"         # 两者都不是（贴图 / 蓝图 / …）⇒ 等于没搜到

# --- 行状态 -------------------------------------------------------------------
# ⚠ **2026-10-04 晚用户口径更正**（原话见 `docs/阶段五-表面材质.md` 第〇节之三）：
#   · **白膜行：先搜**（保留原来的做法），**材质和材质实例都搜**；两者都搜不到 ⇒ `MAT_SELF_BUILD`；
#   · **非白膜（别人建好的资产）：搜不到就问用户要路径**，问完还是没有 ⇒ `MAT_NONE`（**写"无"**）——
#     别人的资产我们**不去给它造材质 / 实例**；
#   · **清单里两列都记**：`material`（材质）+ `instance`（材质实例）。
MAT_FOUND = "found"                    # 找到了（有实例，或"有材质待派生"）
MAT_PENDING = "pending_user"           # 还没问完（`confirm` 见到它就拒收）
MAT_SELF_BUILD = "missing_self_build"  # **白膜**搜不到 ⇒ 待自建（第 ⑤ 步建材质 + 实例）
MAT_NONE = "none"                      # **非白膜**问完"确实没有" ⇒ **无**（不贴、不自建）

# 自建的目标落点（`probe` 给白膜行**提议**的包路径 = `<这个目录>/M_<元素>`）
SELF_BUILD_FOLDER = "/Game/UEMCP/Materials"

# --- 实例状态（`instance_status`）—— 这三档决定第 ⑤ 步怎么走 -------------------------
INST_IS_INSTANCE = "is_instance"       # 拿到的**本来就是实例** ⇒ 第 ⑤ 步跳过（已有的东西不重建）
INST_FROM_MATERIAL = "from_material"   # **有材质、缺实例** ⇒ 第 ⑤ 步派生一份（父级=那块材质）
INST_TO_CREATE = "to_create"           # 待自建 ⇒ 第 ⑤ 步先建材质、再派生实例

# --- 目标类型（`target`）-------------------------------------------------------
TARGET_WHITEBOX = "whitebox"           # 白膜行（组件级覆盖贴的就是它）
TARGET_SLOT = "slot"                   # 网格资产的某个材质槽（走 `asset_slots` 那一条腿）

# --- 官方 toolset 名（**字符串常量，不是拼出来的** —— 拼错就是运行时缺工具）-------------
TS_ASSET = "editor_toolset.toolsets.asset.AssetTools"
TS_OBJECT = "editor_toolset.toolsets.object.ObjectTools"
TS_MATINST = "editor_toolset.toolsets.material_instance.MaterialInstanceTools"
TS_MATERIAL = "editor_toolset.toolsets.material.MaterialTools"
TS_ACTOR = "editor_toolset.toolsets.actor.ActorTools"
TS_SCENE = "editor_toolset.toolsets.scene.SceneTools"
TS_APP = "EditorToolset.EditorAppToolset"

# --- 建材质时用的表达式类与属性名（节点的类名 / 属性名**只认官方返回**，这里是"要问什么"）----
MAT_EXPR_VECTOR = "/Script/Engine.MaterialExpressionVectorParameter"
MAT_EXPR_SCALAR = "/Script/Engine.MaterialExpressionScalarParameter"
MAT_PARAM_NAME_PROP = "parameterName"       # 参数节点的"参数名"属性（官方常规命名，见 `_material_ops`）
MAT_PARAM_VALUE_PROP = "defaultValue"       # 参数节点的"默认值"属性

# 允许的参数类型（官方 `list_parameters` 的 `type` 落成小写后出现的词）
MAT_KINDS = ("scalar", "vector", "texture", "static_switch")

# 实例命名后缀：`M_Path` → `MI_M_Path`；`MI_sjfnch0a` → `MI_sjfnch0a_1`
INSTANCE_PREFIX = "MI_"
INSTANCE_SUFFIX_LATER = "_1"

# 白膜组件名：阶段三 `add_cube` 给图元组件起的名字（贴材质认的就是它）
CUBE_COMPONENT_NAME = "cube"

# 视口聚焦的默认上限（聚焦几十个，用户只看得见最后一个 —— 那等于没切）
DEFAULT_FOCUS_MAX = 3

# 清单签字要用的"材质库指纹"里，我们关心哪几类资产（与阶段一的 `library_fingerprint` 同源）
FINGERPRINT_TYPES = ("material", "material_instance")


# ══════════════════════════════════════════════════════════════════════════════
# 二、注入面（SurfacesRepo）—— 本层与 server 的唯一接口
# ══════════════════════════════════════════════════════════════════════════════

class OfficialCaller(Protocol):
    """官方调用的形状：`await official("exists", {...}, toolset="…")`。

    ⚠ 只要这一个方法，本层就能调遍官方工具面 —— 也让预检能拿一个**假 caller** 离线跑逻辑。
    """

    async def __call__(self, tool_name: str, arguments: dict, toolset: str = "") -> Any: ...


@dataclass
class SurfacesRepo:
    """本层需要的**全部外部能力**，由 `main.py` 在调用时构造并注入。

    为什么用注入而不是 `import main`（血账）：规划层当年就是因为 main ↔ planning 互相 import，
    被迫改成 `_planning_modules()` 惰性加载；本层一开始就不走那条路。

    字段说明（每一项都对应 main.py 里一个既有实现，行为不许变）：
      · `official`      —— 官方调用咽喉（main.py 的 `call_official`，含链路闸 / 错误包装）
      · `level`         —— 当前关卡包路径（本层不自己查；调用方查一次传进来）
      · `load_json` / `save_json` / `archive_json` —— 状态文件读写（`archive_json` 会把上一版留档）
      · `to_object_path`—— 包路径 → 对象路径（`/Game/A/B` → `/Game/A/B.B`，官方属性写入要这个）
      · `normalize_path`—— 对象路径 → 包路径（读回来的引用要规整）
      · `library_inventory` —— 枚举材质库（`{类型: [路径…]}`）+"用了几次官方调用"
      · `ref_path`      —— 官方返回值 → 引用字符串（形状不固定，统一在这里剥）
      · `now`           —— 时间戳（注入是为了留痕一致、也方便离线断言）
    """

    official: OfficialCaller
    # ⚠ **按名字找资产的那份唯一实现由外面注入**（`main.py::match_assets_by_keyword` ——
    #   阶段一 `plan_assets` 与阶段五 `probe` **共用它**）。本层**不许再写一份匹配循环**：
    #   写第二份，同一个关键词在两处会给出两套答案（本项目最忌讳的那种）。
    #   ⚠ **必填**（不给默认值）—— 忘了注入就**当场炸**，不退回老写法、也不悄悄回空表
    #   （那等于把"没接上"说成"没搜到"）。用户 2026-10-04 晚：「能复用就复用」。
    match_assets: Any
    # ⚠ **签字指纹也只认外面注入的那一份**（`main.py::library_fingerprint` —— 阶段一那份
    #   **sha256（把每条路径都算进去）**）。本层**不许再写一份**：第三轮对比时发现我原来只算
    #   "每类的数量"，**同类里一增一删、数量不变 ⇒ 判不出过期**（比阶段一弱了一档）。
    #   **必填**（不给默认值）：忘了注入当场炸，不悄悄退化成"数量指纹"。
    #   ⚠ **它必须和 `match_assets` 一样排在所有"有默认值"的字段前面** —— dataclass 里
    #     "无默认值字段跟在有默认值字段后面"**在类创建时就炸**（2026-10-04 晚真跑预检栽过一次：
    #     `TypeError: non-default argument 'fingerprint' follows default argument 'category_cn'`）。
    fingerprint: Any
    # ⚠ **大类中文名也由外面注入**（`main.py` 的 `category_cn(k, load_categories())`）——
    #   阶段一的 `deliverable` 是按**大类中文名**分组的，阶段五的清单正文照它同一套。
    #   本层**不另存一份大类表**（判据只有一处）。
    #   ⚠ `None` 时正文退回显示**大类 key**（如实、不冒充中文名）—— 不是判据，不影响任何闸门。
    category_cn: Any = None
    level: str = ""
    load_json: Callable[[Path], Any] = lambda _p: None
    save_json: Callable[[Path, Any], None] = lambda _p, _d: None
    archive_json: Callable[[Path, str], str] = lambda _p, _x: ""
    to_object_path: Callable[[str], str] = lambda s: s
    normalize_path: Callable[[Any], str] = lambda v: str(v or "")
    library_inventory: Any = None
    ref_path: Callable[[Any], str] = lambda v: ""
    now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat()


def _ref(repo: SurfacesRepo, value: Any) -> str:
    """官方返回的引用 → 字符串（读不到给空串，**不编**）。

    ⚠ 单独包一层：`repo.ref_path` 在离线（预检）里可能是简化实现，
      而"取不到就空串"这条**判据**必须只有一份。
    """
    try:
        return str(repo.ref_path(value) or "")
    except Exception:                                  # noqa: BLE001 —— 取不到就是取不到
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# 三、数据模型（清单 / 草稿 / 台账 / 报表）
#    ⚠ 模型的**字段**就是这一层的"合同"：`instance` 是唯一权威（贴的就是它）。
# ══════════════════════════════════════════════════════════════════════════════

class MaterialRow(BaseModel):
    """**材质清单的一行**（第 ④ 步交用户逐条确认；第 ⑥⑧ 步贴的就是它）。

    ⚠ 权威字段是 `instance`（**必须是材质实例**）：`probe`/`confirm`/`create` 都只负责
      把这一格填成一块真实存在、可调参的实例；`material` 只用于"我们建了什么母材质"留痕。
    """

    element_key: str = Field(description="元素关键词（与阶段一资产清单里的一致 —— 贴哪一行靠它）")
    label: str = Field(default="", description="这一行的标签（同类多个要能区分）")
    target: str = Field(
        default=TARGET_WHITEBOX,
        description="`whitebox`（白膜行，贴组件级覆盖）/ `slot`（网格资产的材质槽）",
    )
    mesh_path: str = Field(default="", description="`target=slot` 时：网格资产包路径")
    slot_name: str = Field(default="", description="`target=slot` 时：槽名（必须是官方返回过的）")

    instance: str = Field(
        default="",
        description="**唯一权威**：这一行最终要用的**材质实例**包路径（贴 / 调参都用它）",
    )
    instance_status: str = Field(
        default="",
        description=("`is_instance`（本来就是实例）/ `from_material`（有材质缺实例，待派生）/ "
                     "`to_create`（待自建：先建材质再派生实例）"),
    )
    parent: str = Field(
        default="",
        description="那份实例**派生自谁**（Material 或 MI 的包路径）—— 第 ⑤ 步派生时用它",
    )
    parameters: list[str] = Field(
        default_factory=list, description="该实例**可调**的参数名（来自官方 `list_parameters`）"
    )
    material: str = Field(
        default="",
        description="母材质包路径（`to_create` 行 = 我们自己建的那块；其余行 = 实例的父级，供留痕）",
    )
    status: str = Field(
        default="",
        description=f"`{MAT_FOUND}` / `{MAT_PENDING}`（`confirm` 见到就拒收）/ `{MAT_SELF_BUILD}`",
    )
    source: str = Field(default="", description="这条路径从哪来：`mesh_slot` / `user` / `search`")
    note: str = Field(default="", description="备注（用户怎么答复的、为什么选它）")
    # --- 照阶段一补的两样（2026-10-04 晚）------------------------------------------------
    question: str = Field(
        default="",
        description="**要原样问用户的话**（`probe` 落草稿时给；`confirm` 收他的答复后这就是留痕）",
    )
    category: str = Field(
        default="", description="**大类 key**（从阶段一资产表那一行抄来的）—— 报文按它分组"
    )
    exists: bool = Field(
        default=False, description="官方 `exists()` 的验证结果（调用方不用填，`confirm` 会覆盖）"
    )


class ProbeRow(BaseModel):
    """**`probe` 的逐条结果**（草稿行）—— 第 ①~③ 步只读找材质的产物。"""

    element_key: str = Field(default="", description="元素关键词")
    label: str = Field(default="", description="这一行的标签")
    target: str = Field(default="", description="看的是谁：`whitebox` 或网格资产包路径")
    slot_name: str = Field(default="", description="网格资产上的槽名（官方返回的原文）")
    instance: str = Field(default="", description="找到的**实例**路径；拿到的是裸材质时留空")
    instance_status: str = Field(
        default="",
        description="`is_instance` / `from_material`（有材质缺实例 ⇒ 第 ⑤ 步派生）/ `to_create`",
    )
    parent: str = Field(default="", description="待派生时：父级 = 那块裸材质")
    material: str = Field(default="", description="找到的东西（可能是材质，也可能是实例）")
    candidates: list[str] = Field(default_factory=list, description="候选路径（多个时交用户挑）")
    # ⚠ 照阶段一 `FoundAsset`：**"为什么命中"是结构化字段，不是散文** ——
    #   每条 = `{path, name, asset_type, matched_by, matched_segment}`（`match_assets_by_keyword()`
    #   的原样返回）；`main.py` 用它直接拼 `FoundAsset` 交给用户看（与阶段一同一套字段）。
    candidates_detail: list[dict] = Field(
        default_factory=list, description="候选的**结构化**明细（含「按资产名 / 按文件夹名命中」）"
    )
    status: str = Field(default="", description=f"`{MAT_FOUND}` / `{MAT_PENDING}` / `{MAT_SELF_BUILD}`")
    source: str = Field(default="", description="`mesh_slot` / `search` / `user`")
    note: str = Field(default="", description="给人看的说明（含「要问用户什么」）")
    # --- 照阶段一 `ElementPlan` 补的两样（2026-10-04 晚）-----------------------------------
    question: str = Field(
        default="",
        description=("**要原样问用户的话**（照阶段一 `ElementPlan.question`）—— "
                     "agent 把这一句逐条转达给用户，**不许自己改写成「确认吗」**"),
    )
    category: str = Field(
        default="",
        description="**大类 key**（从阶段一资产表那一行抄来的，如 `building` / `nature`）—— 报文按它分组",
    )


class CreatedRow(BaseModel):
    """**第 ⑤ 步建出来的一条**（材质或实例）—— 台账里逐条记，给回滚用。"""

    path: str = Field(description="建出来的包路径")
    kind: str = Field(default="", description="`material`（我们自己建的母材质）/ `instance`（派生的 MI）")
    row_uid: str = Field(default="", description="这一条是**为清单里哪一行**建的（`element_key|label`）")
    parent: str = Field(default="", description="实例的父级（材质那条为空）")
    status: str = Field(default="", description="`ok` / `created` / `already` / `failed`")
    promoted: dict = Field(default_factory=dict, description="从父级提升上来的参数覆盖（名 → 值）")
    error: str = Field(default="", description="没成的原因（官方原文）")
    note: str = Field(default="", description="给人看的备注")


class SurfaceApplyRow(BaseModel):
    """**`apply` 的逐行结果**。"""

    label: str = Field(default="", description="台账里那一行的标签")
    element_key: str = Field(default="", description="元素关键词")
    instance: str = Field(default="", description="贴上去的**材质实例**路径")
    actor: str = Field(default="", description="贴到哪个 Actor 的组件上")
    status: str = Field(default="", description="`applied` / `already` / `failed`")
    error: str = Field(default="", description="没成的原因（官方原文）")


class SurfaceReport(BaseModel):
    """**`apply_surfaces` 的报文**（四档共用：probe / confirm / apply / asset_slots）。"""

    stage: str = Field(default="阶段五 · 表面材质", description="阶段名")
    mode: str = Field(default="apply", description="这一趟做的是哪一档")
    level: str = Field(default="", description="在哪张图上做的")
    planned: int = Field(default=0, description="这次要处理几行")
    applied: int = Field(default=0, description="真做成了几行")
    already: int = Field(default=0, description="本来就对、跳过了几行（幂等）")
    failed: int = Field(default=0, description="几行没成（看 rows 里的 error）")
    asset_rows_untouched: int = Field(default=0, description="资产行**没碰**几个（它们自带材质）")
    rows: list[SurfaceApplyRow] = Field(default_factory=list, description="逐行结果")
    probe_rows: list[ProbeRow] = Field(default_factory=list, description="`probe` 的逐条结果")
    confirm_rows: list[MaterialRow] = Field(default_factory=list, description="`confirm` 入库的行")
    slot_rows: list[dict] = Field(default_factory=list, description="`asset_slots` 的逐条结果")
    focused: list[str] = Field(default_factory=list, description="`apply`：聚焦过哪些 Actor")
    saved: str = Field(default="", description="`apply`+`save=true` 时：`saved` / `failed` / `off`")
    wrote_back: str = Field(default="", description="`apply`+`save=true` 时：清单回写结果（路径或原因）")
    material_list_path: str = Field(default="", description="权威清单的落点（没有就是空）")
    snapshot_path: str = Field(default="", description="签字指纹文件的落点")
    draft_path: str = Field(default="", description="`probe` 落的草稿路径")
    snapshot_stale: bool | None = Field(
        default=None,
        description="这份清单**过没过期**（指纹对不上 ⇒ true）；`None` = 判不了（**不是没问题**）",
    )
    official_calls: int = Field(default=0, description="这次调了官方几次（留痕，方便复核）")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class CreateReport(BaseModel):
    """**`create_surfaces` 的报文**（建材质 / 派生实例 / 调参 / 材质图原语）。"""

    stage: str = Field(default="阶段五 · 第 ⑤ 步", description="阶段名")
    mode: str = Field(default="create", description="`create` / `tune` / `ops`")
    target: str = Field(default="", description="这一趟的目标（路径或参数点名）")
    planned: int = Field(default=0, description="要做几件事")
    created: int = Field(default=0, description="建成/派生成功几条")
    materials_created: int = Field(default=0, description="建了几块**材质**（只有待自建才建）")
    instances_created: int = Field(default=0, description="派生/建了几个**材质实例**")
    already: int = Field(default=0, description="已经存在、跳过了几条（幂等）")
    failed: int = Field(default=0, description="几条没成")
    tuned: int = Field(default=0, description="`tune`：改成几个参数")
    rows: list[CreatedRow] = Field(default_factory=list, description="逐条结果")
    tune_rows: list[dict] = Field(default_factory=list, description="`tune` 的逐参数结果（含旧值）")
    op_rows: list[dict] = Field(default_factory=list, description="`ops` 的逐 op 结果")
    read_back: str = Field(default="", description="`ops` 的读回结论")
    focused: list[str] = Field(
        default_factory=list,
        description=("`tune`：调完**切到那个物体视角并选中高亮**过哪些 Actor"
                     "（第 ⑨ 步「用户得看得见改了哪儿」；走官方 `FocusOnActors` + `SelectActors`）"))
    ledger_path: str = Field(default="", description="台账落点")
    official_calls: int = Field(default=0, description="这次调了官方几次")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


# ══════════════════════════════════════════════════════════════════════════════
# 四、纯工具（不碰官方、不碰磁盘）
# ══════════════════════════════════════════════════════════════════════════════

def pkg_path(value: Any) -> str:
    """把任意写法**规整成包路径**（去掉对象名后缀、统一斜杠）。

    为什么必须有它（2026-09-27 实测踩过）：`overrideMaterials` 要的是**对象路径**
    （`/Game/X/MI_A.MI_A`），而清单 / 配置里写的是**包路径**（`/Game/X/MI_A`）——
    两边混着来的时候，读回核对会**永远对不上**（明明贴上了却报没贴上）。
    """
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    head, _, tail = raw.rpartition("/")
    if not head:
        return raw
    if "." in tail:                     # `/Game/A/B.B` → `/Game/A/B`
        tail = tail.split(".", 1)[0]
    return f"{head}/{tail}" if tail else raw


def row_uid(row: dict) -> str:
    """清单行的身份键：`element_key|label`（**不许重** —— `confirm` 会拿它查重）。"""
    return f"{str(row.get('element_key') or '').strip().lower()}|{str(row.get('label') or '').strip()}"


def instance_name_for(found_path: str, existing: set[str]) -> str:
    """给"要派生的实例"起个资产名（重名时加后缀，**绝不覆盖已有资产**）。

    规则（简单、可读、可预测 —— 用户一眼能看出它从谁来）：
      · 母材质叫 `M_Path`      → `MI_M_Path`
      · 已经是实例 `MI_sjfnch0a` → `MI_sjfnch0a_1`
      · 重名 → 再往后加 `_2` `_3`…
    """
    base = pkg_path(found_path).rsplit("/", 1)[-1]
    if not base:
        return ""
    name = f"{INSTANCE_PREFIX}{base}" if not base.startswith(INSTANCE_PREFIX) else f"{base}{INSTANCE_SUFFIX_LATER}"
    if name not in existing:
        return name
    i = 2
    while f"{name}_{i}" in existing:
        i += 1
    return f"{name}_{i}"


def proposed_self_build_path(key: str) -> str:
    """**白膜行"待自建"时提议建到哪** → `<SELF_BUILD_FOLDER>/M_<元素>`（纯函数）。

    例：`road` → `/Game/UEMCP/Materials/M_Road`；`sidewalk` → `…/M_Sidewalk`。

    ⚠ 这只是**提议**（用户口径：白膜搜不到 ⇒ 标待自建，不是"跳过"）——它**不改任何东西**。
      第 ⑤ 步真建的时候要照这个键去 `config/surface_materials.json` 的 `create` 段找配方
      （**配方表里没有就不建、点名**，绝不猜怎么建）。
    """
    token = "".join(ch for ch in str(key or "").strip().title() if ch.isalnum())
    return f"{SELF_BUILD_FOLDER}/M_{token}" if token else ""


def value_text(value: Any) -> str:
    """把参数值规整成一段文本（报文与台账里存它，便于人核对与回滚）。

    ⚠ 它是**留痕格式**，不是判据 —— 判据是 `value_same()`（同一个函数对同一份值给同一段文本）。
    """
    if isinstance(value, dict) and "refPath" in value:
        return pkg_path(value.get("refPath"))
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{float(value):g}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(value_text(v) for v in value) + "]"
    if isinstance(value, dict):
        # LinearColor 形状（`{"r":…,"g":…,"b":…,"a":…}`）单独认一下 —— 官方的 get_* 就回这个
        keys = ("r", "g", "b", "a")
        if all(k in value for k in keys):
            return "[" + ", ".join(value_text(value[k]) for k in keys) + "]"
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value if value is not None else "")


def value_same(a: Any, b: Any) -> bool:
    """两个参数值是不是同一个（幂等判断用它 —— 同值就不写）。

    ⚠ 走"先规整成文本再比"，是为了让 `1` / `1.0` / `"1"` 这类写法**判成同一个值**；
      这也是本项目"实测 1 与 1.0 来回横跳会刷出假差异"的教训。
    """
    return value_text(a) == value_text(b)


def linear_color_value(raw: Any) -> Any:
    """把**我们给的值**换成官方 `LinearColor` 认的形状 —— `[r,g,b,a]` → `{"r","g","b","a"}`。

    ⚠ **这是「向量参数写不进去」那个 BUG 的根因 A**（2026-10-07 实测复现，全部原始输出见
      `docs/阶段五-表面材质.md` 末尾那一节）：4 元**数组**交给官方 ⇒ **静默失败**
      （不报错、值也不变，读回来还是垃圾），**同一块材质上标量却是好的**；同一个节点改用
      对象形状写 ⇒ `get_properties` 读回 `{r:0.65, g:0.06, b:0.06, a:1}` **正确**。
      受害者：樱木街那 6 块自建材质的颜色（`M_Car` / `M_Railing` / `M_Planter` /
      `M_UtilityPole` / `M_Sign` / `M_Van`）全是垃圾值。

    ⚠ 只换**恰好 4 个元素的 list/tuple**（那正是"向量"的形状，见 `_kind_of()`）；别的形状
      **原样返回** —— 在这儿猜类型是另一条错路（猜错就是把标量写成向量）。
    ⚠ 值非数字时**也原样返回**：让官方去报它自己的错，别在这儿吞掉。
    """
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return {"r": float(raw[0]), "g": float(raw[1]), "b": float(raw[2]), "a": float(raw[3])}
        except (TypeError, ValueError):
            return raw
    return raw


def prop_of(raw: Any, key: str) -> Any:
    """在官方 `ObjectTools.get_properties` 的返回里**按字段名**找 `key` 的值（找不到给 `None`）。

    ⚠ 为什么要"递归找字段名"而不是写死层级：那个返回**套了几层**（`returnValue` / 列表 /
    每项 `{"name": …, "value": …}` 都可能），而文档里手工读出来的就是 `parameterName` /
    `defaultValue` 这两个键（`docs/阶段五-表面材质.md` 现象 2）—— 所以按**键名**找，不猜层级。
    ⚠ 两种形状都认：`{"parameterName": …}` 与 `{"name": "parameterName", "value": …}`。
    ⚠ **字符串也认**（2026-10-07 补）：官方 `get_properties` 回来的**就是一段 JSON 字符串**
      （`call_official` 的 `parse_return()` 只解外层那个 `returnValue`）—— 原来这里只认
      dict / list ⇒ **拿到字符串一律给 `None`** ⇒ 调用方只能报"找不到键 / 没核成"：
      那**看着像"读不回来"，其实是"我们没解"**（`surfaces.create()` 的参数节点读回一直是这个状态）。
    ⚠ **找不到 ≠ 没问题**：调用方必须把 `None` 当"没核成"如实报（本项目最忌"没读到说成没问题"）。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, dict):
        if key in raw:
            return raw[key]
        if str(raw.get("name") or "") == key and "value" in raw:
            return raw.get("value")
        for v in raw.values():
            got = prop_of(v, key)
            if got is not None:
                return got
    elif isinstance(raw, (list, tuple)):
        for v in raw:
            got = prop_of(v, key)
            if got is not None:
                return got
    return None


def _finite_number(value: Any) -> float | None:
    """取一个有限数字；取不到给 `None`（**不抛** —— 调用方按"这个值不合法"处理）。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


# ══════════════════════════════════════════════════════════════════════════════
# 五、清单 / 草稿 / 台账的读写
# ══════════════════════════════════════════════════════════════════════════════

def render_manifest(rows: list[Any], quote: str, stamp: str, gaps: list[str] | None = None,
                    cat_cn: Any = None) -> str:
    """把清单拼成**给人看的正文** —— 交用户逐条确认用（与阶段一 `deliverable` 同性质）。

    ⚠ 它是**使用物的一部分**（用户要照着它逐条点头），不是装饰：所以**两列都列** ——
      **材质**（搜到的材质资产）与**材质实例**（贴的就是它），外加**可调的参数名**（第 ⑨ 步的靶子）。
      用户 2026-10-04 晚口径：「清单现在列两个，不只收材质实例，还收材质」
      + 「那就别找母级，搜到啥就是哪个」。

    ⚠ **排版照阶段一 `_render_asset_list`**（2026-10-04 晚再对比之后补的）：
      统计行 → **按大类分组的小节**（`## 大类（N 项）` + 每类一张小表）→ 逐行（带**状态符号**）
      → 口径提示 → **分段点名**（待自建 / 有材质缺实例 / 无）→ **缺口段**（并写明"**不推断原因**"）。
      `cat_cn` = 大类中文名的查表函数（由 `main.py` 注入）——
      **不传就显示大类 key**（如实，不冒充中文名）。
    """
    def _cn(key: str) -> str:
        k = str(key or "").strip().lower()
        if not k:
            return "未分类"
        if cat_cn is None:
            return k
        try:
            return str(cat_cn(k) or k) or k
        except Exception:               # noqa: BLE001 —— 查表失败**不许**把正文搞崩
            return k

    have = sum(1 for r in rows if str(getattr(r, "instance", "") or "").strip())
    mats = sum(1 for r in rows if str(getattr(r, "material", "") or "").strip())
    okrows = sum(1 for r in rows if bool(getattr(r, "exists", False)))
    self_build = [r for r in rows
                  if str(getattr(r, "status", "") or "").strip().lower() == MAT_SELF_BUILD]
    nones = [r for r in rows
             if str(getattr(r, "status", "") or "").strip().lower() == MAT_NONE]
    derive = [r for r in rows if not str(getattr(r, "instance", "") or "").strip()
              and str(getattr(r, "status", "") or "").strip().lower() == MAT_FOUND]

    lines = ["材质清单（阶段五使用）", ""]
    lines.append(f"确认时间：{stamp}")
    lines.append(f"用户原话：{quote}")
    # ⚠ **两列都报**（用户 2026-10-04 晚口径：「清单现在列两个」）：材质一列、材质实例一列。
    lines.append(f"行数：{len(rows)}    路径验活通过：{okrows}    有材质：{mats}    有实例：{have}    "
                 f"待自建：{len(self_build)}    无：{len(nones)}")
    lines.append("")

    def _emit(batch: list[Any]) -> None:
        lines.append(f"{'元素':<12} {'标签':<26} {'状态':<22} 材质 → 材质实例")
        lines.append("-" * 140)
        for r in batch:
            inst = str(getattr(r, "instance", "") or "")
            mat = str(getattr(r, "material", "") or "")
            status = str(getattr(r, "status", "") or "").strip().lower()
            if status == MAT_NONE:
                mark, where = "✗ 无", "**无**（问过用户：没有材质 / 实例 —— 我们不自建）"
            elif status == MAT_SELF_BUILD:
                mark, where = "◻ 待自建", f"{mat or '(没写落点)'} → (第 ⑤ 步建材质 + 派生实例）"
            elif inst:
                mark, where = "✓ 有实例", f"{mat or '—'} → {inst}"
            else:
                mark, where = "◻ 待派生", f"{mat or '—'} → (实例待第 ⑤ 步派生)"
            lines.append(f"{str(getattr(r, 'element_key', '') or ''):<12} "
                         f"{str(getattr(r, 'label', '') or ''):<26} "
                         f"{mark:<22} {where}")
            bits = []
            parent = str(getattr(r, "parent", "") or "")
            if parent:
                bits.append(f"父级 = {parent}")
            istatus = str(getattr(r, "instance_status", "") or "")
            if istatus:
                bits.append(f"实例状态 = {istatus}")
            params = list(getattr(r, "parameters", None) or [])
            if params:
                bits.append("可调参数 = " + "、".join(params[:8])
                            + ("…" if len(params) > 8 else ""))
            q = str(getattr(r, "question", "") or "")
            if q:
                bits.append(f"问过：{q}")
            note = str(getattr(r, "note", "") or "")
            if note:
                bits.append(note)
            if bits:
                lines.append(f"{'':<12} {'':<26} └ " + " ｜ ".join(bits))
        lines.append("")

    # ---- 按大类分组打印（照阶段一：`## 大类（N 项）` + 每类一张小表）-------------------
    grouped: dict[str, list[Any]] = {}
    for r in rows:
        grouped.setdefault(_cn(str(getattr(r, "category", "") or "")), []).append(r)
    for name, batch in grouped.items():
        lines.append(f"## {name}（{len(batch)} 项）")
        _emit(batch)

    lines.append("⚠ 口径（2026-10-04 晚用户更正）：**清单两列都记** —— `材质` + `材质实例`（贴的就是它）。"
                 "**搜到啥就是哪个**（不去找母级）；**搜到多个候选 ⇒ 问用户自己确认**。"
                 "白膜行**先搜**，材质和实例都搜不到 ⇒ **待自建**（第 ⑤ 步建材质 + 派生实例）；"
                 "**非白膜**（别人建好的资产）搜不到就问用户要路径，问完还是没有 ⇒ 记**无**"
                 "（我们**不去给它造材质 / 实例**）。")
    lines.append("⚠ 有材质 / 实例的行在入库时已逐个 `exists()` 验过；待自建与「无」的行**没有路径可验**。")

    # ---- 分段点名（照阶段一 deliverable 末尾那几段）---------------------------------
    if self_build:
        lines.append("")
        lines.append("⚠ 下面这些是**待自建**（白膜搜不到材质和材质实例）—— 第 ⑤ 步由 `create_surfaces()` "
                     "建材质 + 建实例；建法从 `config/surface_materials.json` 的 `create` 段读"
                     "（**没有配方就不建、点名**）：")
        for r in self_build:
            lines.append(f"  - {getattr(r, 'label', '') or getattr(r, 'element_key', '')}"
                         f"（落点 {getattr(r, 'material', '') or '(没写)'}）")
    if derive:
        lines.append("")
        lines.append("⚠ 下面这些**只有材质、还没有实例** —— 第 ⑤ 步照那块材质**派生一份实例**再贴"
                     "（母材质不动）：")
        for r in derive:
            lines.append(f"  - {getattr(r, 'label', '') or getattr(r, 'element_key', '')}"
                         f"（材质 {getattr(r, 'material', '') or '(没写)'}）")
    if nones:
        lines.append("")
        lines.append("✗ 下面这些按你的答复记成**无** —— 那是**别人建好的资产**、没有材质 / 实例，"
                     "我们**不去给它造**；第 ⑥ 步贴的时候**跳过**：")
        for r in nones:
            lines.append(f"  - {getattr(r, 'label', '') or getattr(r, 'element_key', '')}")

    if gaps:
        lines.append("")
        lines.append("⚠ **缺口**（阶段一资产表里有、这张单里没有）：" + "、".join(gaps))
        lines.append("  ⚠ 本工具**不推断原因**（照阶段一 `unresolved_elements` 的纪律）—— "
                     "可能是没搜到、也可能是用户说没有；**要原因得问用户**。"
                     "它绝不能**静默消失**：读这张表的人要一眼看出「这份清单不完整」。")
    return "\n".join(lines)


def manifest_rows_of(doc: Any) -> list[dict]:
    """`items` → 行列表（**纯函数**，不碰磁盘 —— 供 `main.py` 那一侧复用同一份判据）。"""
    rows = doc.get("items") if isinstance(doc, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def strict_rows_of(rows: list[dict]) -> list[dict]:
    """**权威行**：带 `instance_status` 的那些（旧格式行一律不算）。

    ⚠ 「哪一行算数」只有这一份判据 —— `main.py` 的 `_manifest_rows_strict()` 是它的转发，
      不许在别处再判一遍（本项目最忌讳两套口径）。
    """
    return [r for r in (rows or []) if str(r.get("instance_status") or "").strip()]


def legacy_manifest_note(doc: Any, path_hint: str = "") -> str:
    """清单**过没过期/合不合格式** → 提示字符串（空串 = 没问题）。**纯函数**。

    判据只有这一份：`load_manifest()`（本层）与 `main.py` 的 `_load_material_doc()`
    都转发到这里 —— 以前两处各写一遍，细节已经不一样了（一处空表算"读不到"、另一处不算）。
    """
    name = path_hint or MATERIAL_LIST_PATH.name
    rows = manifest_rows_of(doc)
    if not rows:
        return f"材质清单 `{name}` 里一行都没有。"
    legacy = [r for r in rows if not str(r.get("instance_status") or "").strip()]
    if len(legacy) == len(rows):
        return (f"材质清单 `{name}` 是**旧格式**（{len(rows)} 行全都没有 `instance_status`）—— "
                "这一版的口径是「清单只记材质实例」，"
                "请重跑 `apply_surfaces(mode=\"probe\")` → `mode=\"confirm\"` 定稿一份新的。")
    if legacy:
        return (f"材质清单 `{name}` **新旧混着**（{len(legacy)}/{len(rows)} 行没有 `instance_status`）"
                "—— 不猜怎么补，请重跑 `probe` → `confirm`。")
    return ""


def instance_of(row: dict) -> str:
    """这一行要用的**材质实例**包路径（没有 ⇒ 空串）。**纯函数**，`main.py` 转发它。"""
    return pkg_path((row or {}).get("instance"))


def surface_target(row: dict) -> str:
    """这一行**现在就能碰的那个对象**的包路径 —— 有实例用实例，没有就用母材质。

    ⚠ 为什么要有它（2026-10-04 晚真跑撞到）：第 ④ 步的清单里**本来就允许**只有母材质的行
      （`from_material` = **有材质、缺实例**，实例要等第 ⑤ 步才派生）。`confirm()` 当时拿
      `instance` 去逐个 `exists()`，那些行拿到的是**空串** ⇒ 官方 `exists("")` 返回否 ⇒
      **整批被拒收**，而报文里只剩一串空路径（人看不懂拒的是什么）。
      **判据只有一处**：`confirm()` 验活 / 读参数都走本函数；`from_material` 行的靶子就是那块母材质。
    ⚠ 实例优先（有实例时母材质路径可能已经不被引用）；`parent` 只是 `validate_rows()` 允许的退路。
    """
    raw = row or {}
    return pkg_path(raw.get("instance")) or pkg_path(raw.get("material")) or pkg_path(raw.get("parent"))


def _recipe_target_for(parent: str, recipes: dict) -> str:
    """配方表里以 `parent` 为父级的那一条 —— **它的键就是实例该落的包路径**（取不到给空串）。

    ⚠ 多条都指同一个父级时**取文件中更早的那条**并照实返回（这里不猜"哪条更对" —— 猜就是替人做决定）。
    ⚠ **`create` 段里混着说明条目**（`_doc_2` / `_doc_3`，值是字符串或数组）⇒ 必须 `isinstance(spec, dict)`
      才算配方；**下划线开头的条目一律跳过**。
      ⚠ 2026-10-07 **第一次真跑 `tune` 就栽在这儿**（`'list' object has no attribute 'get'`，
      报文里只有一句没有正文的 `Error executing tool`）—— 教训：**遍历用户写的表之前先按类型筛**。
    """
    want = pkg_path(parent)
    for path, spec in (recipes or {}).items():
        if not isinstance(spec, dict) or str(path).startswith("_"):
            continue
        if want and pkg_path(spec.get("parent")) == want:
            return pkg_path(path)
    return ""


def derived_instance_of(row: dict, recipes: dict | None = None) -> str:
    """**缺实例**的行（`from_material`）第 ⑤ 步会派生出来的那份实例落在哪 —— **纯函数、可复算**。

    ⚠ 存在的理由（2026-10-04 晚发现的口径缺口）：第 ⑤ 步派生出来的实例路径，按用户口径
      **要到第 ⑦ 步（满意才回写）才写进清单**；可第 ⑥ 步"贴上去"就要知道贴哪一块。
      解法不是提前写清单（那违背"满意才写回"），而是**这一行派生到哪儿是可复算的** ——
      第 ⑤ 步（建）与第 ⑥ 步（贴）**共用本函数**，两处不会算出两个不同的名字。

    ⚠ **2026-10-07 加 `recipes`（可选）**：修「追加实测」第 6 条的后半 —— 配方表里那条
      "**键 = 要建的包路径**、`parent` = 父级"，那条的派生实例**就该落在那个键上**。
      以前只按"父级所在目录 + `MI_<父级名>`"起名 ⇒ 清单 / 配方点名的那条路径**根本不存在**
      （实测：配方写 `/Game/UEMCP/Materials/MI_SakuraLeaves`，实例却建到
      `/Game/Fab/Tree/realistic_tree/Materials/MI_normal_leaves`）。
      ⚠ **不给 `recipes` 时行为与以前逐字相同**；`create`（⑤）与贴（⑥）**传同一张表** ⇒ 同源。
    """
    if instance_of(row):
        return instance_of(row)
    parent = pkg_path((row or {}).get("parent")) or pkg_path((row or {}).get("material"))
    if parent and isinstance(recipes, dict):
        target = _recipe_target_for(parent, recipes)
        if target:
            return target
    return _target_instance_path(parent, None) if parent else ""


def surface_instance(row: dict, recipes: dict | None = None) -> str:
    """这一行**最终要贴的那块实例**：已有实例就是它；只有母材质的用**派生路径**。

    ⚠ 判据只有这一处。`apply_surfaces()` 选靶子 / 验活 / 写入都用它 —— 直接读 `instance`
      当靶子会让"有材质缺实例"那一类在贴的时候被静默跳过（贴不上而没人说清为什么）。
    ⚠ `recipes`（可选）透传给 `derived_instance_of()` —— 第 ⑥ 步要**传与第 ⑤ 步同一张配方表**，
      否则"建在哪"和"贴哪"又会分叉（那正是 2026-10-07 修掉的那条）。
    """
    return instance_of(row) or derived_instance_of(row, recipes)


def load_manifest(repo: SurfacesRepo) -> tuple[dict, str]:
    """读权威材质清单 → `(文档, 读不到的原因)`。

    ⚠ **不迁就旧数据**（用户 2026-10-04：「别为了兼容老代码去做，能翻新直接翻新」）：
      旧清单（没有 `instance_status` 那种）会被判成"**这份清单是旧格式，重跑 probe + confirm**"，
      而不是被当成能用的清单 —— 免得拿着半旧的数据往下走。
    """
    # ⚠ 2026-10-10（本次改动）：**先问注入面，再问磁盘** —— 与同文件 `snapshot_stale()` 的写法对齐。
    # 由来：原来第一句就是磁盘检查 `if not MATERIAL_LIST_PATH.exists(): return {}, "还没有材质清单…"`，
    #   **绕过了 `repo.load_json()` 那个注入面**。而 `tests/preflight.py` 的各段正是靠注入一份假清单
    #   来**离线**验收闸门的 —— 于是预检被挡在门外：`tune()` 报"一个可调的实例都没有"、
    #   `snapshot_stale()` 早退回 `None` ⇒ 把 `catalog/` 清空之后 ⑫d / ⑫o / ⑫ / ㉑ 四项变红
    #   （那是**假红**：磁盘上那份数据是**有意删掉**的，而注入面明明给了清单）。
    # 改法：**先读**（拿到的可能正是注入面给的那一份），读不到 / 不是 dict 时，
    #   再按"**磁盘上到底有没有**"分两种原因报出去 —— 真实运行时的行为逐字不变：
    #     · 文件不在 ⇒ `load_json` 抛 ⇒ 吞掉 ⇒ 报"还没有材质清单（…）—— 先 `probe` → `confirm` 定稿。"
    #     · 文件在、但读不动 ⇒ 报"材质清单 `…` 读不动（不是一份 JSON 对象）。"
    #   ⚠ **唯一的行为差异（如实记着，不假装等价）**：原来"文件在、但 `load_json` 自己抛异常"会把
    #     那个异常**向上抛**给调用方；现在它被吞掉、按"读不动"报出来。这更宽容，也更符合本函数的
    #     签名承诺（它本来就声明返回 `(文档, 读不到的原因)`），但**它是一处差异**。
    try:
        doc = repo.load_json(MATERIAL_LIST_PATH)
    except Exception:                                    # noqa: BLE001
        doc = None
    if not isinstance(doc, dict):
        if not MATERIAL_LIST_PATH.exists():
            return {}, (f"还没有材质清单（`{MATERIAL_LIST_PATH}`）—— 先 `probe` → `confirm` 定稿。")
        return {}, f"材质清单 `{MATERIAL_LIST_PATH.name}` 读不动（不是一份 JSON 对象）。"
    return doc, legacy_manifest_note(doc, MATERIAL_LIST_PATH.name)


def manifest_rows(repo: SurfacesRepo) -> list[dict]:
    """权威清单的行（读不到就是空表 —— 调用方自己决定要不要拒收）。"""
    doc, _why = load_manifest(repo)
    return manifest_rows_of(doc)


def _rows_by_key(repo: SurfacesRepo) -> dict[str, list[dict]]:
    """清单 → `{element_key: [行…]}`（贴的时候按它匹配台账行）。"""
    out: dict[str, list[dict]] = {}
    for r in manifest_rows(repo):
        key = str(r.get("element_key") or "").strip().lower()
        if key:
            out.setdefault(key, []).append(r)
    return out


def save_manifest(repo: SurfacesRepo, items: list[dict], quote: str,
                  extra: dict | None = None, deliverable: str = "") -> str:
    """把清单落盘（先留档上一版）→ 返回路径。**这是"使用物"，不是 UE 资产**。

    ⚠ `deliverable` = 给人看的那份正文（`render_manifest()` 拼的）—— 它与阶段一的
      `deliverable` 同性质：用户是照着它**逐条点头**的，所以一并落盘。

    ⚠ **两种调用，语义不同（2026-10-07 补，真跑前读代码抓到的坑）**：
      · `quote` **非空** = **`confirm()` 的重新签字** ⇒ `signed_at` 用现在、原话用这一句、
        `deliverable` 用新渲染的那份（**旧的一概不继承**）；
      · `quote`（或 `deliverable`）**为空** = **第 ⑦ 步的回写**（`apply(save=True)`：
        把这次真贴上的实例路径写进清单）—— 那**不是**一次新的确认 ⇒ 必须**继承**旧清单的
        `confirmed_by_quote` / `deliverable` / `signed_at`，只换 `items` + 记 `written_back_at`。
        否则一次回写就会**抹掉用户签字时的那句原话**、**丢掉他点头看的那份正文** ——
        那正是"凭据被自己人删了"，比不跑这一步坏得多。
    """
    stamp = repo.now()
    old = load_manifest(repo)[0] or {}
    if not str(quote or "").strip():                      # 空原话 = 第 ⑦ 步回写（不是重新签字）
        quote = str(old.get("confirmed_by_quote") or "")
        deliverable = deliverable or str(old.get("deliverable") or "")
        stamp = str(old.get("signed_at") or stamp)        # 签字时刻不动（那是"用户确认那一刻"）
    repo.archive_json(MATERIAL_LIST_PATH, "material_list")
    doc = {
        "stage": "阶段五 · 材质清单（权威使用物：贴什么、调什么，只有这一个来源）",
        "signed_at": stamp,
        "confirmed_by_quote": str(quote or "").strip(),
        "note": ("每一行只记**材质实例**（`instance`）；`parent` 是它的父级（Material 或 MI）；"
                 "`parameters` 是这块实例可调的参数名（官方 `list_parameters` 的原样返回）。"
                 f"⚠ 待自建的行（`{MAT_SELF_BUILD}`）第 ⑤ 步先建材质再派生实例，但**这两格是"
                 "**第 ⑦ 步才回写的** —— `apply_surfaces(save=True)`（「用户满意 ⇒ 先回写清单、"
                 "再存盘」）跑完才会填上 `instance` / `instance_status`；在那之前它们一直空着"
                 "（**那不是「没建」**）。"),
        "items": items,
    }
    if deliverable:
        doc["deliverable"] = deliverable
    if extra:
        doc.update(extra)
    repo.save_json(MATERIAL_LIST_PATH, doc)
    return str(MATERIAL_LIST_PATH)


def save_snapshot(repo: SurfacesRepo, counts: dict, fingerprint: str, quote: str,
                  total: int = 0) -> str:
    """给清单**签字**：记下签字当时的材质库指纹（**形状照阶段一 `library_snapshot.json`**）。

    ⚠ 指纹由**外面注入的那一份**（`main.py::library_fingerprint`）算出来：**sha256，把每条路径都算进去**
      —— 与阶段一同一支（本轮对比时改的：原来我只算"每类的数量"，**同类里一增一删、数量不变就判不出过期**）。
    """
    repo.archive_json(MATERIAL_SNAPSHOT_PATH, "material_snapshot")
    repo.save_json(MATERIAL_SNAPSHOT_PATH, {
        "stage": "阶段五 · 材质清单的签字指纹",
        "captured_at": repo.now(),
        "confirmed_by_quote": str(quote or "").strip(),
        "hash": str(fingerprint or ""),
        "counts": counts,
        "total": int(total or sum(int(v or 0) for v in (counts or {}).values())),
        "note": ("签字时材质库长什么样（照阶段一 `library_snapshot.json`）。"
                 "⚠ 指纹只覆盖**路径层面**（哪些资产存在、叫什么）——"
                 "同一个路径内容变了（换了贴图）它查不出来。"),
    })
    return str(MATERIAL_SNAPSHOT_PATH)


def load_snapshot(repo: SurfacesRepo) -> dict:
    doc = repo.load_json(MATERIAL_SNAPSHOT_PATH)
    return doc if isinstance(doc, dict) else {}


async def snapshot_stale(repo: SurfacesRepo) -> bool | None:
    """现在这份清单**过没过期**：`True` 过期 / `False` 没过期 / `None` **判不了**（不是没问题）。

    ⚠ 判不了的三类（都回 `None`，**不许**当成"没问题"）：没有清单、没有指纹、取不到实况。
    ⚠ 比的是**注入进来的那一份指纹**（阶段一 `library_fingerprint` 的 sha256）——
      `repo.fingerprint` 是**必填**注入（没注入就当场炸，不悄悄放过）。
    """
    rows = manifest_rows(repo)
    signed = load_snapshot(repo)
    if not rows:
        return None
    if not str(signed.get("hash") or ""):
        return None
    if repo.library_inventory is None:
        return None
    try:
        inv, _used = await repo.library_inventory()
    except ToolError:
        return None
    # ⚠ 指纹**只有一处实现**（注入进来的阶段一 `library_fingerprint`：sha256，把每条路径都算进去）
    live, _counts = repo.fingerprint(inv)
    return str(signed.get("hash") or "") != str(live or "")


def ledger_merge(repo: SurfacesRepo, section: str, entries: list[dict]) -> str:
    """把这一趟的结果**追加**进台账的某一段（读-改-写，只动自己那一段）。

    ⚠ 台账是**回滚依据**：`created` 记我们建了什么、`derived` 记从谁派生、
      `tuned` 记**旧值**、`focused` 记聚焦过谁。写不动不算任务失败（如实报进 warnings）。
    """
    doc = repo.load_json(SURFACE_LEDGER_PATH)
    doc = doc if isinstance(doc, dict) else {}
    doc.setdefault("stage", "阶段五 · 表面材质台账（我们自己的 JSON，不是 UE 资产）")
    doc["updated_at"] = repo.now()
    doc[section] = list(doc.get(section) or []) + entries
    repo.archive_json(SURFACE_LEDGER_PATH, "surface_materials")
    repo.save_json(SURFACE_LEDGER_PATH, doc)
    return str(SURFACE_LEDGER_PATH)


def known_created_paths(repo: SurfacesRepo) -> set[str]:
    """台账 `created` 段里**我们建过的**那些包路径（读不动 ⇒ 空集）。

    ⚠ 用途只有一个：第 ⑤ 步要派生的实例**已经存在时**，用来回答"**是不是我们建的**"——
      是 ⇒ 幂等跳过；不是 ⇒ 也跳过，但**点名报出来**（绝不覆盖别人的东西，也不假装它是我们的）。
    """
    doc = repo.load_json(SURFACE_LEDGER_PATH)
    items = doc.get("created") if isinstance(doc, dict) else None
    return {pkg_path(r.get("path")) for r in (items or []) if isinstance(r, dict) and pkg_path(r.get("path"))}


# ══════════════════════════════════════════════════════════════════════════════
# 六、官方调用的薄封装（**每一种读法只有一份实现**）
# ══════════════════════════════════════════════════════════════════════════════

async def asset_exists(repo: SurfacesRepo, path: str) -> bool:
    """官方 `exists()` —— 资产在不在（拿不到就抛，调用方决定拒收还是如实报）。

    ⚠ **判据照阶段一 `_asset_row`（2026-10-04 晚第三轮对比时改的 —— 原来写错了）**：
      阶段一是 `exists is True or (isinstance(exists, str) and exists.lower() == "true")`；
      我原来写的是 `bool(await ...)` —— ⚠ **`bool("false")` 在 Python 里是 `True`**！
      官方那侧完全可能回**字符串** `"false"` ⇒ 一个**不存在的资产**会被判成"在"，
      而这道闸正是"路径验不过就整批拒收"（`confirm` / `apply` 都靠它）—— **闸门失守**。
      阶段一早就踩过这个坑，这里是**同一支判据**。
    """
    got = await repo.official("exists", {"path": pkg_path(path)}, toolset=TS_ASSET)
    if got is True:
        return True
    if isinstance(got, str):
        return got.strip().lower() == "true"
    return got == 1                 # 官方偶尔回 1；其余（None / dict / "false" / 0）一律当"不在"


async def asset_class(repo: SurfacesRepo, path: str) -> str:
    """官方 `get_asset_class()` —— 这块资产是 `Material` 还是 `MaterialInstanceConstant`。

    ⚠ 判"实例还是裸材质"**只认这个**（不靠命名猜 —— 名字里带 MI_ 的也可能是别的类）。
    """
    got = await repo.official("get_asset_class", {"asset_path": pkg_path(path)}, toolset=TS_ASSET)
    return str(got or "").strip()


async def list_parameters(repo: SurfacesRepo, path: str) -> list[dict]:
    """官方 `list_parameters()` —— 这块材质/实例**可调**的参数（名 + 类型）。

    ⚠ 参数名**只认这个返回**（官方在 UMG 工具集里写死：跳过它 `set_properties` 会静默失败）。
    """
    got = await repo.official(
        "list_parameters", {"material": {"refPath": repo.to_object_path(pkg_path(path))}},
        toolset=TS_MATINST)
    out: list[dict] = []
    for x in (got or []):
        if isinstance(x, dict) and x.get("name"):
            out.append({"name": str(x["name"]).strip(),
                        "type": str(x.get("type") or "").strip().lower()})
    return out


async def mesh_slots(repo: SurfacesRepo, mesh: str) -> tuple[list[tuple[str, str]], str]:
    """官方 `get_material_slots` + `get_material` —— 网格资产每个槽现在用的是哪块材质。

    返回 `([(槽名, 材质包路径), …], 出错原文)`；槽名**原样带回**（不许自己拼槽名）。
    """
    try:
        slots = await repo.official(
            "get_material_slots", {"mesh": {"refPath": repo.to_object_path(pkg_path(mesh))}},
            toolset="editor_toolset.toolsets.static_mesh.StaticMeshTools")
    except ToolError as exc:
        return [], str(exc)
    out: list[tuple[str, str]] = []
    for item in (slots or []):
        name = ""
        if isinstance(item, dict):
            name = str(item.get("slot_name") or item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if not name:
            continue
        try:
            got = await repo.official(
                "get_material",
                {"mesh": {"refPath": repo.to_object_path(pkg_path(mesh))}, "slot_name": name},
                toolset="editor_toolset.toolsets.static_mesh.StaticMeshTools")
        except ToolError:
            out.append((name, ""))
            continue
        out.append((name, pkg_path(_ref(repo, got))))
    return out, ""


async def components_of(repo: SurfacesRepo, actor: str) -> list[str]:
    """官方 `get_components` —— 这个 Actor 上有哪些组件（贴材质要按引用找，不按名字猜）。"""
    got = await repo.official(
        "get_components", {"actor": {"refPath": actor}}, toolset=TS_ACTOR)
    return [r for r in (_ref(repo, x) for x in (got or [])) if r]


async def get_component_props(repo: SurfacesRepo, component: str, props: list[str]) -> str:
    """官方 `get_properties` —— 读组件属性（它返回的是**JSON 字符串**，实测）。"""
    got = await repo.official(
        "get_properties", {"instance": {"refPath": component}, "properties": list(props)},
        toolset=TS_OBJECT)
    return got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)


def override_first(raw: Any) -> str:
    """从 `get_properties(组件, ["overrideMaterials"])` 的返回里取**第一块**覆盖材质的包路径。

    ⚠ 解不动 / 形状不认识 → 空串（= 不匹配）—— 安全侧：宁可多写一遍，也不假装已经贴好了。
    """
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return ""
    if not isinstance(doc, dict):
        return ""
    arr = doc.get("overrideMaterials")
    if not isinstance(arr, list) or not arr:
        return ""
    first = arr[0]
    if isinstance(first, dict):
        return pkg_path(first.get("refPath"))
    return pkg_path(first)


# ══════════════════════════════════════════════════════════════════════════════
# 七、第 ①~③ 步：probe（只读，找材质实例和材质）
# ══════════════════════════════════════════════════════════════════════════════

def _hit_text(hit: Any) -> str:
    """一条命中写成给人看的一句话（**含"为什么命中"** —— 用户照它判断用哪一块）。

    ⚠ 命中的对象是 `main.py` 阶段一那份 `FoundAsset`（`match_assets_by_keyword()` 的返回）
      —— 阶段一用它给用户看"按资产名命中 / 按文件夹名命中（哪一段）"，这里**照它同一套字段**。
    """
    return (f"`{getattr(hit, 'path', '')}`（{getattr(hit, 'matched_by', '')}"
            + (f"：{getattr(hit, 'matched_segment', '')}"
               if getattr(hit, "matched_segment", "") else "") + "）")


def manifest_gaps(delivered_keys: set[str], source_keys: set[str]) -> list[str]:
    """**缺口**：来源表里有、产出表里没有的元素关键词 —— **不许静默消失**。

    照阶段一 `confirm_assets` 的 `unresolved_elements`（"元素清单里有、本表里没有的"）：
    读表的人必须一眼看出"这份表不完整"，而不是以为一共就这么多。
    ⚠ **不推断原因**（阶段一同一条纪律）—— 只说"差哪几个"。
    """
    return sorted(k for k in (source_keys or set()) if k and k not in (delivered_keys or set()))


def row_question(row: Any) -> str:
    """这一行**要原样问用户的话**（照阶段一 `ElementPlan.question` 的做法）。**纯函数**。

    ⚠ 判据只有这一处：`probe` 出草稿时给每行填它，agent 把这一句**逐条转达**给用户
      （阶段一的原话是"把每个元素的 question **原样逐条问用户**"）——
      **不许改写成"确认吗"**，也不许把「确认吗」塞进问题框（AGENTS 的沟通纪律）。
    """
    label = str(getattr(row, "label", "") or getattr(row, "element_key", "") or "这一行")
    status = str(getattr(row, "status", "") or "").strip().lower()
    if status == MAT_NONE:
        return ""                      # 「无」是**他答过的结论**，没什么可问的
    if status == MAT_SELF_BUILD:
        where = str(getattr(row, "material", "") or "")
        return (f"「{label}」按名字**材质和材质实例都没搜到** —— 我给它建一块"
                + (f" `{where}`" if where else "（落点还没定）")
                + "（材质 + 材质实例都由我们建），行吗？")
    if status == MAT_PENDING:
        cands = list(getattr(row, "candidates", None) or [])
        slot = str(getattr(row, "slot_name", "") or "")
        # ⚠ 照阶段一 `plan_assets` 的话术：**"存在但名字对不上"也是出路之一** ——
        #   让他给【路径 + 名字】，我们**可以改名**（`rename_assets`；改名前先 `get_referencers`）。
        esc = "**如果它其实存在、只是名字对不上，请把它的【路径 + 名字】告诉我，我来改名。**"
        if cands:
            return (f"「{label}」搜到多个候选：{'、'.join(f'`{c}`' for c in cands)}"
                    f" —— **用哪一个？**（他说都不是 ⇒ 白膜标待自建 / 非白膜标无）。{esc}")
        return (f"「{label}」"
                + (f"的槽 `{slot}` " if slot else " ")
                + f"该用什么材质实例？{esc}（有就给路径；他说确实没有 ⇒ 白膜标待自建 / 非白膜标无）")
    inst = str(getattr(row, "instance", "") or "")
    mat = str(getattr(row, "material", "") or "")
    if inst:
        return f"「{label}」就用这块实例 `{inst}` 吗？"
    if mat:
        return (f"「{label}」只找到**材质** `{mat}`（没有现成实例）—— "
                "第 ⑤ 步照它**派生一份实例**再贴，行吗？")
    return f"「{label}」这一行还没有可用的材质 / 实例 —— 怎么办？"


async def probe(repo: SurfacesRepo, max_per_element: int = 5) -> SurfaceReport:
    """**第 ①~③ 步（只读）**：对着资产清单逐条找**材质实例和材质**、出草稿。**不碰关卡。**

    逐条怎么判（这是本层的核心判据，只有这一份）：
      1. 资产清单那一行有网格（且不是白膜占位） ⇒ 读它的材质槽
         （官方 `get_material_slots`+`get_material`）—— **别人建好的资产**；
      2. 白膜行 / 没有网格的目标 ⇒ 按关键词在材质库实况里本地匹候选（**保留原来的做法**）；
      3. 拿到路径之后**逐个问官方它是哪一类**：
         · `MaterialInstanceConstant` ⇒ 记进 **`instance`** 那一格（`is_instance`）；
         · `Material` ⇒ 记进 **`material`** 那一格（`from_material` = 有材质、缺实例）；
         ⚠ **两列都记**（用户 2026-10-04 晚口径：「清单现在列两个，不只收材质实例，还收材质」）。
      4. **搜不到时按"是不是白膜"分两条路**（用户口径，**不许混**）：
         · **是白膜** ⇒ **`missing_self_build`（待自建）** + 给一个**提议的落点**
           （`proposed_self_build_path()`）—— 材质和实例都由我们建；
         · **不是白膜** ⇒ `pending_user`（**要问用户要路径**）；他说"确实没有" ⇒ 改成
           **`none`（写"无"）** —— 别人的资产我们**不去给它造材质 / 实例**。
    """
    warns: list[str] = []
    calls = 0
    delivery = repo.load_json(ASSET_LIST_PATH)
    delivery = delivery if isinstance(delivery, dict) else {}
    if not (delivery.get("items") or []):
        raise ToolError(
            f"**没有资产清单**（`{ASSET_LIST_PATH}` 读不到 / 一行都没有）—— "
            "「对着资产清单找材质」这件事根本不成立，**一个字节都没写**。"
            "先把阶段一走完（`confirm_assets` 落一份签过字的清单）。")

    inventory: dict[str, list[str]] = {}
    if repo.library_inventory is not None:
        try:
            inventory, used = await repo.library_inventory()
            calls += int(used or 0)
        except ToolError as exc:
            warns.append(f"⚠ 材质库枚举失败（{exc}）—— 只能靠网格槽那条路找，"
                         "白膜行会**全部**落到「要问用户」。")
    if not inventory:
        warns.append("⚠ 材质库实况是空的（枚举没回东西）—— 白膜行的候选只能靠网格槽 / 用户给路径。"
                     "**别把它当成「库里没有材质」**。")

    rows: list[ProbeRow] = []
    for item in (delivery.get("items") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("element_key") or "").strip().lower()
        label = str(item.get("element") or "").strip()
        mesh = pkg_path(item.get("asset_path"))
        is_placeholder = bool(item.get("is_placeholder"))
        if not key and not label:
            continue

        # ---- 1) 有网格的资产行：读它的材质槽 --------------------------------------
        if mesh and not is_placeholder:
            slots, err = await mesh_slots(repo, mesh)
            calls += 2 if slots else 1
            if not slots:
                rows.append(ProbeRow(
                    element_key=key, label=label, target=mesh,
                    instance_status=INST_FROM_MATERIAL, status=MAT_PENDING,
                    note=(f"网格资产 `{mesh}` 的材质槽读不回来（{err}）—— **要问用户**："
                          "这块资产该用什么材质实例（让他给路径）。")))
                continue
            multi = len(slots) > 1
            for slot_name, mat in slots:
                row_label = f"{label}·槽 {slot_name}" if multi else label
                if not mat:
                    # 槽是空的：**要问用户**（候选也照阶段一那条路子匹，展示截前 N 个）
                    hits0 = repo.match_assets(key, inventory, FINGERPRINT_TYPES)
                    rows.append(ProbeRow(
                        element_key=key, label=row_label, target=mesh, slot_name=slot_name,
                        instance_status=INST_FROM_MATERIAL, status=MAT_PENDING,
                        candidates=[getattr(h, "path", "") for h in hits0[:max_per_element]],
                        note=(f"槽 `{slot_name}` 是空的 —— **要问用户**：这一槽该用什么实例"
                              "（有就给路径；他说确实没有 ⇒ 这行记**无**）。"
                              + (f"本地同名候选（共 {len(hits0)} 条）："
                                 + "、".join(_hit_text(h) for h in hits0[:max_per_element])
                                 if hits0 else "本地没有同名候选。"))))
                    continue
                inst, status, kind, kind_note, used = await _classify(repo, mat)
                calls += used
                if kind == KIND_OTHER:
                    # 槽里的东西**既不是材质也不是材质实例** ⇒ 等于没读到（**要问用户**）
                    rows.append(ProbeRow(
                        element_key=key, label=row_label, target=mesh, slot_name=slot_name,
                        instance_status=INST_FROM_MATERIAL, status=MAT_PENDING,
                        note=(f"槽 `{slot_name}` 里现在是 `{mat}`，但{kind_note}"
                              "**要问用户**：这一槽该用什么材质实例（有就给路径；"
                              "他说确实没有 ⇒ 这行记**无**）。")))
                    continue
                # ⚠ **两栏按"搜到啥就是啥"记**（用户 2026-10-04 晚：「那就别找母级，搜到啥就是哪个」）：
                #   槽里是**实例** ⇒ 只填 `instance`（`material` 留空 —— 它就是实例，不是材质）；
                #   槽里是**材质** ⇒ 填 `material` + `parent`（第 ⑤ 步照它派生实例），`instance` 留空。
                rows.append(ProbeRow(
                    element_key=key, label=row_label, target=mesh, slot_name=slot_name,
                    instance=inst, instance_status=status,
                    parent=("" if inst else pkg_path(mat)),
                    material=("" if inst else pkg_path(mat)),
                    candidates=[inst or mat], status=MAT_FOUND, source="mesh_slot",
                    note=f"读自 `{mesh}` 的槽 `{slot_name}`。{kind_note}"))
            continue

        # ---- 2) 白膜行 / 没有网格的目标：**照阶段一那条路子**在材质库实况里匹 ------------
        # ⚠ **2026-10-04 晚（用户口径）**：白膜行**保留"先搜"这条老路**（搜到就用）；
        #   **材质和材质实例都搜不到 ⇒ 直接标「待自建」**（**不是"问用户"**）——
        #   用户原话：「是白膜还是保留原来的，搜不到材质或者材质实例就标记待自建」。
        #   同时给它一个**提议的落点**（`/Game/UEMCP/Materials/M_<元素>`）：第 ⑤ 步照这个键去
        #   `config/surface_materials.json` 的 `create` 段找配方（**配方表里没有就不建、点名**）。
        # ⚠ 匹配走**注入进来的那份唯一实现**（`main.py::match_assets_by_keyword`，
        #   与阶段一 `plan_assets` **共用**）：词边界 + 逐段文件夹名 + 记"为什么命中" + 同一支排序；
        #   **全部命中都拿回来**，展示才截前 N 个（阶段一的做法：问题里报**真总数**）。
        hits = repo.match_assets(key, inventory, FINGERPRINT_TYPES)
        if not hits:
            proposal = proposed_self_build_path(key)
            rows.append(ProbeRow(
                element_key=key, label=label, target=TARGET_WHITEBOX,
                instance_status=INST_TO_CREATE, status=MAT_SELF_BUILD,
                material=proposal, parent=proposal,
                note=("白膜行；按关键词**材质和材质实例都没找到** ⇒ 标为**待自建**"
                      "（材质 + 材质实例都由我们建）。"
                      + (f"提议建到 `{proposal}` —— 第 ⑤ 步照这个键去 "
                         "`config/surface_materials.json` 的 `create` 段找配方；"
                         "**配方表里没有就不建、点名**（不猜怎么建）。"
                         if proposal else
                         "⚠ `element_key` 是空的，**算不出提议落点** —— 补上再重跑。"))))
            continue
        # ---- 命中的**逐个问官方它是哪一类**，再**按两栏分开** ---------------------------
        # ⚠ **清单是两栏**（用户 2026-10-04 晚：「材质清单是两栏，一个是材质，一个是材质实例」）——
        #   所以"匹到一个材质 + 一个实例"**不是二选一**，是**两栏各填一个**（不用问用户）。
        #   要问的只有一种情况：**同一栏里搜到多个**（比如两块实例），那才交用户确认。
        kinds: list[str] = []
        used_calls = 0
        for h in hits:
            _inst, _st, kind, _note, used = await _classify(repo, h.path)
            used_calls += used
            kinds.append(kind)
        calls += used_calls
        mats = [h for h, k in zip(hits, kinds) if k == KIND_MATERIAL]
        insts = [h for h, k in zip(hits, kinds) if k == KIND_INSTANCE]
        shown_hits = (mats + insts)[:max(1, int(max_per_element))]
        shown = [h.path for h in shown_hits]
        # ⚠ **结构化明细**照阶段一 `FoundAsset` 的字段带出来（含"为什么命中"）——
        #   与 `shown`（给人看的路径串）**同一批**，免得两处口径不一样。
        detail = [{"path": h.path, "name": getattr(h, "name", ""),
                   "asset_type": getattr(h, "asset_type", ""),
                   "matched_by": getattr(h, "matched_by", ""),
                   "matched_segment": getattr(h, "matched_segment", "")} for h in shown_hits]
        if not mats and not insts:
            # 匹到的**全都不是材质也不是实例**（比如贴图 / 别的类）⇒ 等于没搜到
            proposal = proposed_self_build_path(key)
            rows.append(ProbeRow(
                element_key=key, label=label, target=TARGET_WHITEBOX,
                instance_status=INST_TO_CREATE, status=MAT_SELF_BUILD,
                material=proposal, parent=proposal, candidates=shown, candidates_detail=detail,
                note=("白膜行；按关键词匹到的 " + str(len(hits)) + " 条候选"
                      "**既不是材质也不是材质实例**"
                      f"（{'、'.join(_hit_text(h) for h in hits[:max_per_element])}）"
                      "⇒ 按「没搜到」处理、标**待自建**（材质 + 实例都由我们建）。"
                      + (f"提议建到 `{proposal}`。" if proposal else "⚠ `element_key` 空着，算不出提议落点。"))))
            continue
        if len(mats) > 1 or len(insts) > 1:
            # **同一栏里多个** ⇒ 交用户自己确认（不许替他挑）
            parts = []
            if len(mats) > 1:
                parts.append("**材质栏 " + str(len(mats)) + " 块**（"
                             + "、".join(_hit_text(h) for h in mats[:max_per_element]) + "）")
            if len(insts) > 1:
                parts.append("**实例栏 " + str(len(insts)) + " 块**（"
                             + "、".join(_hit_text(h) for h in insts[:max_per_element]) + "）")
            if len(mats) == 1:
                parts.append(f"材质栏只有 1 块（{_hit_text(mats[0])}）")
            if len(insts) == 1:
                parts.append(f"实例栏只有 1 块（{_hit_text(insts[0])}）")
            rows.append(ProbeRow(
                element_key=key, label=label, target=TARGET_WHITEBOX,
                instance_status=INST_FROM_MATERIAL, status=MAT_PENDING,
                candidates=shown, candidates_detail=detail,
                note=("白膜行；命中 " + str(len(hits)) + " 条：" + "；".join(parts)
                      + " ⇒ **同一栏里多块，要问用户挑哪一块**（不替他挑）。"
                      "他说都不是 ⇒ 标**待自建**（材质 + 实例都由我们建）。")))
            continue
        # ---- 每一栏最多 1 个 ⇒ **有就填，两栏各填各的**（这才是"清单列两个"的样子）--------
        mat_one = mats[0].path if mats else ""
        inst_one = insts[0].path if insts else ""
        if inst_one:
            rows.append(ProbeRow(
                element_key=key, label=label, target=TARGET_WHITEBOX, status=MAT_FOUND,
                instance=inst_one, instance_status=INST_IS_INSTANCE,
                material=mat_one, parent="", candidates=shown, candidates_detail=detail, source="search",
                note=(f"白膜行；命中 {len(hits)} 条 —— 实例栏：{_hit_text(insts[0])}"
                      + (f"；材质栏：{_hit_text(mats[0])}（两栏各记一个）" if mat_one
                         else "（没搜到材质）")
                      + "。**直接用这块实例**（不再找它的母级）。")))
            continue
        rows.append(ProbeRow(
            element_key=key, label=label, target=TARGET_WHITEBOX, status=MAT_FOUND,
            instance="", instance_status=INST_FROM_MATERIAL,
            material=mat_one, parent=mat_one, candidates=shown, candidates_detail=detail, source="search",
            note=(f"白膜行；命中 {len(hits)} 条 —— 只搜到**材质** {_hit_text(mats[0])}"
                  "（没搜到现成实例）—— 第 ⑤ 步会照它**派生一份实例**再贴（父级 = 它）。")))
    # ---- 照阶段一收尾（2026-10-04 晚对比之后补的两样）---------------------------------
    #   ① **大类**：从阶段一资产表那一行抄过来（报文里按它分组、逐类核对有没有漏）；
    #   ② **要原样问用户的话**：照阶段一 `ElementPlan.question` —— agent 逐条转达，不许改写。
    cat_by_key: dict[str, str] = {}
    for item in (delivery.get("items") or []):
        if isinstance(item, dict):
            _k = str(item.get("element_key") or "").strip().lower()
            if _k:
                cat_by_key[_k] = str(item.get("category") or "").strip().lower()
    for r in rows:
        if not r.category:
            r.category = cat_by_key.get(str(r.element_key or "").strip().lower(), "")
        r.question = row_question(r)
    # ---- **缺口**：资产表里有、本次一行都没出的元素关键词 ⇒ 不许静默消失 ------------------
    gaps = manifest_gaps({str(r.element_key or "").strip().lower() for r in rows if r.element_key},
                         set(cat_by_key.keys()))
    if gaps:
        warns.append("⚠ **缺口**：阶段一资产表里有这几个元素，本次**一行都没出**："
                     + "、".join(gaps) + " —— 读这张表的人要一眼看出"
                     "「这份草稿不完整」，而不是以为一共就这么多。")

    pending = [r.label or r.element_key for r in rows if r.status == MAT_PENDING]
    self_build = [r.label or r.element_key for r in rows if r.status == MAT_SELF_BUILD]
    if pending:
        warns.append("⚠ 有 " + str(len(pending)) + " 行**要问用户**（" + "、".join(pending) + "）—— "
                     "每行的 `question` 就是**要原样转达的话**（照阶段一）；"
                     "**搜到多个候选**时不许替他挑（用户 2026-10-04 晚原话：「搜到多个让用户自己确认」）；"
                     "白膜的槽读不到、或只有空槽时也要问。问完把他的答复写进 `confirm` 的 `items`。")
    if self_build:
        warns.append("⚠ 有 " + str(len(self_build)) + " 行**白膜·搜不到 ⇒ 待自建**（"
                     + "、".join(self_build) + "）—— 第 ⑤ 步建材质 + 建实例；"
                     "配方从 `config/surface_materials.json` 的 `create` 段读（键 = 这一行的 `material`）。")
    return SurfaceReport(stage="阶段五 · 第 ①~③ 步（对着资产清单找**材质和材质实例** · 只读）",
                         mode="probe", level="", planned=len(rows), probe_rows=rows,
                         official_calls=calls, material_list_path=str(MATERIAL_LIST_PATH),
                         draft_path=str(MATERIAL_LIST_DRAFT_PATH),
                         snapshot_path=str(MATERIAL_SNAPSHOT_PATH),
                         gaps=gaps,
                         next_step=("逐条看草稿：**每行的 `question` 原样问用户**（照阶段一）；"
                                    "`found` 的直接进清单；`pending_user` 的等他答复；"
                                    "他说都没有 ⇒ **白膜标待自建、非白膜标无**；"
                                    "然后带**他的原话**调 "
                                    "`apply_surfaces(mode=\"confirm\", items=[…], user_quote=\"他的原话\")` 定稿。"),
                         warnings=warns)


async def _classify(repo: SurfacesRepo, path: str) -> tuple[str, str, str, str, int]:
    """官方判一条路径是"实例"还是"裸材质" →
    `(instance, instance_status, kind, 给人看的话, 调用数)`；`kind ∈ {instance, material, other}`。

    ⚠ **只认官方 `get_asset_class()`**，不靠命名猜（名字里带 `MI_` 的不一定是实例，
      不带 `MI_` 的也可能是实例 —— 实测：`/demo/Item/ItemMesh/House/Material` 是实例）。
    ⚠ **2026-10-04 晚（用户指令「那就别找母级，搜到啥就是哪个」+「能整改就整改」）**：
      **不再读实例的母级**（原来会为每个实例多调一次 `get_properties(parent)`）。
      第三格原来放的就是那个"母级"—— 现在**换成"它是哪一类"**（`kind`），
      因为调用方真正需要的是"分栏"（材质栏 / 实例栏），而不是一个会把通用主材质
      误当成"这一行的材质"的母级路径。理由另两条：
      ① **第 ⑤ 步要用的父级 = 拿到的那个资产本身**（口径③），不需要再往上问一层；
      ② 省掉每行一次官方调用。
    """
    p = pkg_path(path)
    if not p:
        return "", INST_FROM_MATERIAL, KIND_OTHER, "路径是空的。", 0
    try:
        cls = await asset_class(repo, p)
    except ToolError as exc:
        return "", INST_FROM_MATERIAL, KIND_OTHER, f"问官方它是哪一类时失败（{exc}）—— 按「待派生」处理。", 1
    if cls == "MaterialInstanceConstant":
        return p, INST_IS_INSTANCE, KIND_INSTANCE, "它**本身就是一个材质实例**（直接用它）。", 1
    if cls == "Material":
        return "", INST_FROM_MATERIAL, KIND_MATERIAL, "它是一块**材质**（不是实例）—— 贴之前要派生成实例。", 1
    return "", INST_FROM_MATERIAL, KIND_OTHER, f"它既不是材质也不是材质实例（官方报的类是 `{cls}`）—— 要问用户。", 1


# ══════════════════════════════════════════════════════════════════════════════
# 八、第 ④ 步：confirm（定稿 + 签字）
# ══════════════════════════════════════════════════════════════════════════════

def validate_rows(items: list[Any] | None, quote: str) -> tuple[list[MaterialRow], list[str]]:
    """**定稿前的行校验（纯函数，不碰官方、不碰磁盘）** → `(清单行, 拒收原因)`。

    ⚠ **为什么把它从 `confirm()` 里抽出来**（2026-10-04 晚）：
      这几条闸（空表 / 缺用户原话 / `pending_user` / **每行都要"有主"** / `none` 的两条 /
      唯一性）是"拒收型"判据 —— **拒收错了方向 = 悄悄写坏东西**，所以必须能被**离线钉住**。
      抽成纯函数之后 `confirm()` 与预检**调的是同一份实现**（判据只有一处）。
    ⚠ `confirm()` 用它的返回值决定"要不要 raise"；预检直接用它的返回值断言。
    """
    bad: list[str] = []
    rows: list[MaterialRow] = []
    if not items:
        bad.append("`items` 是空的 —— 「确认清单」必须有一张**明明白白的表**。")
    if not str(quote or "").strip():
        bad.append("**没有用户原话** —— 拿不出他本人的话，就说明你还没问过他"
                   "（与 `confirm_plan` / `confirm_assets` 同一条纪律：**不许自己编**）。")
    seen: set[str] = set()
    for item in (items or []):
        raw = item.model_dump() if hasattr(item, "model_dump") else dict(item or {})
        key = str(raw.get("element_key") or "").strip().lower()
        label = str(raw.get("label") or "").strip()
        status = str(raw.get("status") or "").strip().lower()
        target = str(raw.get("target") or "").strip() or TARGET_WHITEBOX
        inst = pkg_path(raw.get("instance"))
        parent = pkg_path(raw.get("parent"))
        material = pkg_path(raw.get("material"))
        mesh = pkg_path(raw.get("mesh_path"))
        slot = str(raw.get("slot_name") or "").strip()
        src = str(raw.get("source") or "").strip().lower()
        istatus = str(raw.get("instance_status") or "").strip().lower()

        if not key:
            bad.append(f"{label or '(没写标签)'}：`element_key` 空着 —— 它是「贴哪一行」的判据。")
        if status not in (MAT_FOUND, MAT_PENDING, MAT_SELF_BUILD, MAT_NONE):
            bad.append(f"{label or key}：`status` 只能是 `{MAT_FOUND}` / `{MAT_PENDING}` / "
                       f"`{MAT_SELF_BUILD}`（白膜搜不到 ⇒ 待自建）/ `{MAT_NONE}`（非白膜问完 ⇒ 无），"
                       f"收到 {status!r}（**不许自己造状态**）。")
            continue
        if status == MAT_PENDING:
            bad.append(f"{label or key}：还是 `pending_user` —— **这一条还没问完**："
                       "要么让用户给路径，要么他确认确实没有（改成待自建 / 无）。")
            continue
        # ---- `none`（无）：**非白膜**问完"确实没有"的那一档（用户 2026-10-04 晚口径）-------
        #   ⚠ 两条硬判据（**自相矛盾就拒收**）：
        #     ① **只许非白膜行**用 —— 白膜搜不到是「待自建」，不是「无」（用户的资产表里
        #        白膜本来就等于"我们自己造的形体"，它必须有表面）；
        #     ② **不许带任何路径** —— "无"就是没有，带 `instance`/`material` 就是自相矛盾。
        if status == MAT_NONE:
            if target.lower() == TARGET_WHITEBOX:
                bad.append(f"{label or key}：**白膜行不许标「无」** —— 白膜搜不到材质/实例时"
                           f"标的是**待自建**（`{MAT_SELF_BUILD}`）：形体是我们自己造的，"
                           "它必须有表面（第 ⑤ 步我们去建）。")
            if inst or material or parent:
                bad.append(f"{label or key}：标了**无**却带着路径（"
                           f"{inst or material or parent}）—— 自相矛盾（「无」就是没有）。")
            uid = row_uid({"element_key": key, "label": label})
            if uid in seen:
                bad.append(f"{label or key}：清单里**重了**（`element_key|label` 必须唯一 —— "
                           "同一元素多处出现时，标签要能区分开）。")
            seen.add(uid)
            rows.append(MaterialRow(
                element_key=key, label=label, target=target, mesh_path=mesh, slot_name=slot,
                instance="", instance_status="", parent="",
                material="", status=status, source=src, note=str(raw.get("note") or ""),
                question=str(raw.get("question") or ""),
                category=str(raw.get("category") or "").strip().lower()))
            continue
        if target.lower() == TARGET_SLOT and not (mesh and slot):
            bad.append(f"{label or key}：`target=slot` 必须同时给 `mesh_path` 与 `slot_name`"
                       "（槽名必须来自官方 `get_material_slots` 的返回，**不许自己拼**）。")
        if status == MAT_SELF_BUILD:
            # 待自建：本来就没有实例（第 ⑤ 步去建）—— 这一档**允许** instance 为空
            istatus = INST_TO_CREATE
            if inst:
                bad.append(f"{label or key}：标了**待自建**却带着 `instance`（{inst}）—— "
                           "自相矛盾（待自建就是**还没有**它）。")
            # ⚠ **必须有"要建成哪块材质"的目标路径**（`material`，或退一步 `parent`）——
            #   否则第 ⑤ 步 `create_surfaces()` 会走到"算不出实例路径"那一条（用户看不到原因）。
            #   在这里问清楚，比让他跑到第 ⑤ 步才撞墙好（**闸要闸在用户正看着的那一步**）。
            if not (material or parent):
                bad.append(f"{label or key}：标了**待自建**，却**没给「要建的那块材质落在哪个包路径」**"
                           "（`material` 或 `parent` 都空着）—— 第 ⑤ 步照 `create` 段的键去建，"
                           "**没有路径就无从下手**。请把目标路径填进 `material`"
                           "（例：`/Game/UEMCP/Materials/M_Shrub`），且 `config/surface_materials.json` "
                           "的 `create` 段里要有同一个键的配方。")
        elif not inst:
            # ⚠ **2026-10-04 晚：这条闸原来写错了，按用户口径改正**（真跑当场撞到）——
            #   原判据是"没有 `instance` 就拒收"，于是把**第 ④ 步本来合法的行**也拦了：
            #   `from_material`（**有材质、缺实例**）正是第 ⑤ 步要派生的那一类，第 ④ 步的清单里
            #   **当然**可以只有母材质路径（用户流程：④ 拿清单 → ⑤ 才派生实例）。
            #   正确判据：**要么已有一块实例，要么给得出母材质 / 父级**（第 ⑤ 步照它派生）；
            #   **两样都没有**才拒收 —— 那种行既贴不了、也派生不出，放进清单只会静默烂掉。
            if not (material or parent):
                bad.append(
                    f"{label or key}：既没有 `instance`，也没有 `material` / `parent` —— "
                    "这一行**既贴不了、也派生不出**。两条出路：给它一块现成的**材质实例**路径"
                    "（`instance`），或者给它**母材质**路径（`material`；第 ⑤ 步 "
                    "`create_surfaces()` 会照它派生一份实例）。")
            else:
                istatus = istatus or INST_FROM_MATERIAL
        uid = row_uid({"element_key": key, "label": label})
        if uid in seen:
            bad.append(f"{label or key}：清单里**重了**（`element_key|label` 必须唯一 —— "
                       "同一元素多处出现时，标签要能区分开）。")
        seen.add(uid)
        rows.append(MaterialRow(
            element_key=key, label=label, target=target, mesh_path=mesh, slot_name=slot,
            instance=inst, instance_status=istatus or (INST_IS_INSTANCE if inst else ""),
            parent=parent, material=material or parent, status=status, source=src,
            note=str(raw.get("note") or ""),
            question=str(raw.get("question") or ""),
            category=str(raw.get("category") or "").strip().lower()))
    return rows, bad


async def confirm(repo: SurfacesRepo, items: list[Any] | None, quote: str) -> SurfaceReport:
    """**第 ④ 步**：把用户过目过的行定稿成权威清单并签字。**整批校验不过 ⇒ 一个字节都不写。**

    闸门（任一条不过 ⇒ 拒收）：
      ① `items` 不许为空（"确认"必须有一张明明白白的表）；
      ② **必须有用户原话**（拿不出原话 = 没问过人 —— 不许自己编）；
      ③ 不许有 `pending_user`（那一条是"还没问完"）；
      ④ **每一行都要"有主"**：要么有一块现成的材质实例（`instance`），要么给得出**母材质 / 父级**
         （`material` / `parent`，第 ⑤ 步照它派生）—— 用户口径：「清单里都只记下材质实例」；
         ⚠ **`from_material`（有材质、缺实例）在第 ④ 步是合法的** —— 它的实例要等第 ⑤ 步
         `create_surfaces()` 派生；**两样都没有**才拒收（那种行既贴不了、也派生不出）；
         ⚠ **两个例外**（用户 2026-10-04 晚口径）：**白膜**搜不到 ⇒ `missing_self_build`
         （待自建）；**非白膜**问完"确实没有" ⇒ `none`（写"无"，**别人的资产我们不去造**）——
         `none` 的两条硬判据见 `validate_rows()`；
      ⑤ 路径逐个官方 `exists()`：**有实例验实例、`from_material` 行验母材质**
         （靶子 = `surface_target()`，判据只有一处），有一条不过 ⇒ 整批拒收。
    ⚠ ①②③④ 的实现在 **`validate_rows()` 里（纯函数）** —— 本函数只负责"不过就拒收"+ ⑤ + 落盘签字。
    """
    warns: list[str] = []
    calls = 0
    rows, bad = validate_rows(items, quote)

    # ---- **待自建行的配方闸**（照阶段一 `placeholder_errors()` 的做法：**闸在用户正看着的那一步**）--
    #   ⚠ 为什么放在第 ④ 步而不是等第 ⑤ 步：这张单正是用户当下在点头的东西 ——
    #     "缺配方"当场说清，比让他点完头、走到第 ⑤ 步才撞墙好（阶段一的纪律：
    #     白膜行填错**当场拒收**，不放一行"看着像资产"的进去）。
    table, table_note = create_recipes(repo)
    if table_note:
        warns.append(table_note)
    for r in rows:
        if r.status != MAT_SELF_BUILD:
            continue
        key_path = _self_build_path(r.model_dump())
        if key_path and key_path not in table:
            bad.append(
                f"「{r.label or r.element_key}」标了**待自建**，目标 `{key_path}`，"
                "但 `config/surface_materials.json` 的 `create` 段里**没有它的配方** —— "
                "我们**不猜怎么建**。往 `create` 段补一条（键就是上面这个路径）、"
                "或把这一行改成别的落点，再重跑。")
    if bad:
        raise ToolError("材质清单**拒收**：有 " + str(len(bad)) + " 条不合格 —— **一个字节都没写**"
                        f"（`{MATERIAL_LIST_PATH.name}` 没动，上一版也没被覆盖）：\n  - "
                        + "\n  - ".join(bad))

    # ---- 路径逐个 exists()：有一条不过就整批拒收（与 confirm_assets 同一条纪律）----------
    # ⚠ 验的是 `surface_target()`（有实例验实例，`from_material` 行验**母材质**）——
    #   判据只有一处，别再在这里写 `r.instance`（那样 `from_material` 行会拿空串去问官方）。
    dead: list[str] = []
    for r in rows:
        if r.status in (MAT_SELF_BUILD, MAT_NONE):
            continue                     # 待自建 = 还没有东西可验；**无** = 本来就没有
        target = surface_target(r.model_dump())
        calls += 1
        try:
            ok = await asset_exists(repo, target)
        except ToolError as exc:
            dead.append(f"{target}（调用失败：{exc}）")
            continue
        if not ok:
            dead.append(target)
        else:
            r.exists = True
    if dead:
        raise ToolError("拒收：材质路径验不过（官方 `exists()` 返回否）："
                        + "、".join(dead) + " —— **一个字节都没写**。先确认这几个路径还在不在"
                        "（有实例的行验的是**实例**，`from_material` 的行验的是**母材质**）。")

    # ---- 把每个靶子**可调的参数**读回来填进行里（第 ⑨ 步 tuner 的靶子清单）--------------
    for r in rows:
        target = surface_target(r.model_dump())
        if not target:
            continue
        calls += 1
        try:
            params = await list_parameters(repo, target)
        except ToolError as exc:
            warns.append(f"⚠ 「{r.label or r.element_key}」的参数名没读回来（{exc}）—— "
                         "这一行的 `parameters` 留空，第 ⑨ 步调它之前得再读一次。")
            continue
        r.parameters = [p["name"] for p in params]

    to_derive = [r.label or r.element_key for r in rows
                 if not r.instance and r.status == MAT_FOUND]
    if to_derive:
        warns.append("⚠ 还有 " + str(len(to_derive)) + " 行**只有材质、实例还没派生**（"
                     + "、".join(to_derive) + "）—— 那是第 ⑤ 步的活："
                     "`create_surfaces()` 会照母材质各派生一份实例并**把父级参数现值提升为覆盖**；"
                     "此后要贴的就是那些派生出来的实例。")
    self_build_rows = [r.label or r.element_key for r in rows if r.status == MAT_SELF_BUILD]
    if self_build_rows:
        warns.append("⚠ 还有 " + str(len(self_build_rows)) + " 行是**白膜·待自建**（"
                     + "、".join(self_build_rows) + "）—— 第 ⑤ 步要**我们建材质 + 建实例**："
                     "建法从 `config/surface_materials.json` 的 `create` 段读（键 = 这一行的 "
                     "`material` 路径）；**配方表里没有就不建、点名**（不猜怎么建）。")
    none_rows = [r.label or r.element_key for r in rows if r.status == MAT_NONE]
    if none_rows:
        warns.append("✅ 有 " + str(len(none_rows)) + " 行按你的答复记成**无**（"
                     + "、".join(none_rows) + "）—— 那是**别人建好的资产**，我们**不建材质 / 实例**；"
                     "第 ⑥ 步贴的时候**跳过**它们。")

    # ---- **缺口**：阶段一资产表里有、这份材质单里没有的元素 ⇒ 不许静默消失 -----------------
    #   照阶段一 `confirm_assets` 的 `unresolved_elements`（"元素清单里有、本表里没有的"）。
    gaps: list[str] = []
    _doc = repo.load_json(ASSET_LIST_PATH)
    if isinstance(_doc, dict):
        src_keys = {str(it.get("element_key") or "").strip().lower()
                    for it in (_doc.get("items") or []) if isinstance(it, dict)}
        gaps = manifest_gaps({str(r.element_key or "").strip().lower() for r in rows}, src_keys)
    else:
        warns.append("⚠ **没读到阶段一资产表**，所以这次**算不出缺口清单**（判不了 ≠ 没问题）。")
    if gaps:
        warns.append("⚠ **缺口**：阶段一资产表里有这几个元素，材质单里**没有**："
                     + "、".join(gaps) + " —— 读这张表的人要一眼看出「这份清单不完整」，"
                     "而不是以为一共就这么多（照阶段一 `unresolved_elements` 的纪律）。")

    payload = [r.model_dump() for r in rows]
    save_manifest(repo, payload, quote,
                  extra={"gaps": gaps},
                  deliverable=render_manifest(rows, str(quote or "").strip(),
                                              repo.now(), gaps=gaps,
                                              cat_cn=repo.category_cn))

    # ---- 签字：材质库指纹 -----------------------------------------------------------
    fingerprint, counts = "", {}
    if repo.library_inventory is not None:
        try:
            inv, used = await repo.library_inventory()
            calls += int(used or 0)
            # ⚠ 指纹**只有一处实现**：注入进来的阶段一 `library_fingerprint`
            #   （sha256，把每条路径都算进去）—— `snapshot_stale()` 用同一支比对。
            fingerprint, counts = repo.fingerprint(inv)
        except ToolError as exc:
            warns.append(f"⚠ 材质库指纹没取到（{exc}）—— 这一次**没有签字指纹**，"
                         "之后判不了「这份清单过没过期」。")
    if fingerprint:
        save_snapshot(repo, counts, fingerprint, quote)
    else:
        warns.append("⚠ 没有落签字指纹文件 —— 之后 `probe` 判不了这份清单过没过期"
                     "（= 判不了，**不是**没问题）。")

    self_build = [r.label or r.element_key for r in rows if r.status == MAT_SELF_BUILD]
    return SurfaceReport(
        stage="阶段五 · 第 ④ 步（材质清单定稿 + 签字）", mode="confirm", level="",
        planned=len(rows), applied=0, already=0, failed=0,
        confirm_rows=rows, official_calls=calls, gaps=gaps,
        material_list_path=str(MATERIAL_LIST_PATH), snapshot_path=str(MATERIAL_SNAPSHOT_PATH),
        next_step=("✅ 清单已定稿（" + str(len(rows)) + " 行，"
                   + ("待自建 " + str(len(self_build)) + "）→ 先 `create_surfaces()` 把待自建 / "
                      "缺实例的那些建出来 → `apply_surfaces()` 贴。"
                      if self_build else
                      ("有实例的 " + str(sum(1 for r in rows if r.instance)) + " 行、"
                       "有材质缺实例的 " + str(sum(1 for r in rows if not r.instance and r.material))
                       + " 行、无 " + str(sum(1 for r in rows if r.status == MAT_NONE)) + " 行）"
                       "→ 下一步 `create_surfaces()`（缺实例的先派生）→ `apply_surfaces()` 贴。"))),
        warnings=warns)


# ══════════════════════════════════════════════════════════════════════════════
# 九、第 ⑤ 步：create（建材质 / 派生实例 / 调参 / 材质图原语）
# ══════════════════════════════════════════════════════════════════════════════

def create_recipes(repo: SurfacesRepo) -> tuple[dict, str]:
    """读**建法配方表** `config/surface_materials.json` 的 `create` 段（**每次调用都重读**）。

    ⚠ 只读 `create` 段了（用户口径「待自建的才建材质」）：`materials` 段**已退役**，
      这一版**连兼容读取都不留**（用户原话：「别为了兼容老代码去做，能翻新直接翻新」）。
    """
    doc = repo.load_json(REPO_ROOT / "config" / "surface_materials.json")
    if not isinstance(doc, dict):
        return {}, ("读不到建法配方表 `config/surface_materials.json` —— "
                    "待自建的行会**没配方可建**（我们**不猜怎么建**）。")
    table = doc.get("create")
    if isinstance(doc.get("materials"), dict) and doc.get("materials"):
        return (table if isinstance(table, dict) else {}), (
            "⚠ `config/surface_materials.json` 里还留着**已退役**的 `materials` 段 —— "
            "这一版**不再读它**（贴什么只认材质清单）。请把那一段删掉。")
    return (table if isinstance(table, dict) else {}), ""


def _validate_recipe(path: str, spec: dict) -> tuple[dict, list[str]]:
    """校验一条配方 → `(规整后的配方, 错误列表)`。**不合法就不许建**（整批拒收）。"""
    errs: list[str] = []
    if not path.startswith("/"):
        errs.append(f"`{path}`：键要写成**包路径**（以 `/` 开头）。")
    # ⚠ 输出的合法键（官方 EMaterialProperty 去掉 MP_ 前缀）——写了不在集合里的 → 拒收
    outputs = {"BaseColor", "Metallic", "Specular", "Roughness", "EmissiveColor", "Opacity",
               "OpacityMask", "AmbientOcclusion", "Anisotropy", "Normal", "Refraction"}
    params: dict[str, Any] = {}
    flags: dict[str, Any] = {}
    parent = pkg_path(spec.get("parent"))
    for k, v in (spec or {}).items():
        if k == "_note":
            continue
        # ⚠ **2026-10-07 修（真跑树冠时撞到）**：`parent` **不是"要覆盖的参数"** —— 它是这条配方的
        #   **父级**（上面已经单独取过了）。原来这一支没排除它 ⇒ `params["parent"] = <父级路径>`
        #   ⇒ 下游那道"要覆盖的参数名必须在父级参数表里"的闸**必然拒收**
        #   （报文：`要覆盖的参数 ['parent'] 不在父级 … 的参数表里`）。
        #   ⚠ **为什么一直没暴露**：清单行指向的那几条配方**都没带 `parent`** ⇒ "带 parent 的配方"
        #     这条支路**从来没跑到过**（它一直躲在我刚补的那个洞后面，见 `create()` 里那段注释）。
        if k == "parent":
            continue
        if k == "_flags":
            if not isinstance(v, dict):
                errs.append(f"`{path}`：`_flags` 要是个对象。")
                continue
            flags = dict(v)
            continue
        if k.startswith("_"):
            continue
        if not parent and k not in outputs:
            errs.append(f"`{path}`：`{k}` 不是合法的材质输出名（只能是 {' / '.join(sorted(outputs))}）—— "
                        "**不猜它连哪儿**。")
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)) or isinstance(v, list) or isinstance(v, str):
            params[k] = v
        else:
            errs.append(f"`{path}` 的 `{k}`：值只能是数字（标量）/ 4 元数组（向量）/ 字符串（纹理）/ true|false。")
    if parent and flags:
        errs.append(f"`{path}`：这是**派生实例**（给了 `parent`）却带了 `_flags` —— "
                    "`_flags`（双面 / 混合模式）是**材质自己的开关**，MI 那条给了它会被静默忽略。")
    if parent:
        material_out = [k for k in params if k in outputs]
        if material_out:
            errs.append(f"`{path}`：同时给了 `parent` 与材质输出键（{'、'.join(material_out)}）—— "
                        "MI 没有材质图，它只能**覆盖父级已有的参数**；要连输出就建材质（去掉 `parent`）。")
    return {"path": pkg_path(path), "params": params, "flags": flags, "parent": parent}, errs


def _target_instance_path(parent_path: str, specified: str | None) -> str:
    """要派生的实例落在哪：`<父级所在目录>/<实例名>`（父级是 MI 时加 `_1` 后缀）。"""
    parent = pkg_path(parent_path)
    if not parent:
        return ""
    folder = parent.rsplit("/", 1)[0]
    name = pkg_path(specified) if specified else ""
    if name:
        return name if name.startswith("/") else f"{folder}/{name}"
    base = parent.rsplit("/", 1)[-1]
    inst = f"{INSTANCE_PREFIX}{base}" if not base.startswith(INSTANCE_PREFIX) else f"{base}{INSTANCE_SUFFIX_LATER}"
    return f"{folder}/{inst}"


async def create(repo: SurfacesRepo, only: list[str] | None = None) -> CreateReport:
    """**第 ⑤ 步**：把缺的东西建出来。三件事，顺序不许改：

      ① **待自建**（`missing_self_build`）⇒ 建**材质**（`create_material` + 参数节点 + 连线），
         配方来自 `config/surface_materials.json` 的 `create` 段（**我们替不了你猜怎么建**）；
      ② **有材质没实例**（`from_material`）⇒ 用官方 `MaterialInstanceTools.create`
         **派生一份实例**，父级 = **拿到的那个资产**（Material 或 MI 都行）；
      ③ **数值提升**：派生完把父级参数**现值读出来写进新实例当覆盖** ——
         这样"材料里那个数"从此可以在实例上调，不用回头改母材质（用户口径）。

    ⚠ **已有的东西一律跳过**（幂等）—— 绝不覆盖用户已有的资产。
    ⚠ **本工具不存盘**：第 ⑦ 步「用户满意」那一次才存（`apply_surfaces(save=true)`）。
    """
    warns: list[str] = []
    calls = 0
    rows_doc, why = load_manifest(repo)
    if why and not rows_doc:
        raise ToolError(f"建不了：{why} **一个字节都没写**。")
    if why:
        warns.append(f"⚠ {why}")
    rows = [r for r in (rows_doc.get("items") or []) if isinstance(r, dict)]
    if not rows:
        raise ToolError("材质清单里一行都没有 —— 先 `apply_surfaces(mode=\"probe\")` → "
                        "`mode=\"confirm\"` 定稿清单。**一个字节都没写**。")

    table, table_note = create_recipes(repo)
    if table_note:
        warns.append(table_note)

    only_set = {pkg_path(x) for x in (only or []) if str(x).strip()}

    # ---- 收集这一趟要建的东西（**先整批校验，再动手**）------------------------------
    to_create: list[dict] = []       # 待自建的母材质（配方）
    to_derive: list[dict] = []       # 要派生的实例
    problems: list[str] = []
    # ⚠ **`only` 的"命中过没有"**（2026-10-05 加）：这一趟**本来会**建 / 会派生的那些目标路径。
    #   它**不受 `only` 影响**（先记候选、再按 `only` 筛）—— 为的是能回答"你点名的路径到底有没有对上"。
    cand_paths: set[str] = set()
    # ⚠ **只装"真会被建 / 被派生"的目标**（2026-10-05 加，**真跑当场发现报文不实**）：拒收报文要列给
    #   它看的是"你还能点哪些**还没建**的"—— 若把"已经有实例"的行也塞进去，报文就会说
    #   "待建的目标有：`MI_xxx`"，而那块实例其实早就在（说明与事实不符，正是本项目最忌的那种）。
    #   所以**两个集合分开**：`cand_paths` 判"点名有没有命中"，`build_paths` 只决定"报文里列什么"。
    build_paths: set[str] = set()

    for r in rows:
        key = str(r.get("element_key") or "")
        label = str(r.get("label") or key)
        istatus = str(r.get("instance_status") or "").strip().lower()
        inst = pkg_path(r.get("instance"))
        parent = pkg_path(r.get("parent")) or pkg_path(r.get("material"))

        # ⚠ **`无` 行直接跳过**（用户 2026-10-04 晚口径）：那是**别人建好的资产**上没有材质 /
        #   实例的元素 —— 我们**不去给它造**（造了就是替别人的资产做决定）。它不自建、也不报错。
        if str(r.get("status") or "").strip().lower() == MAT_NONE:
            continue

        if istatus == INST_TO_CREATE or str(r.get("status") or "") == MAT_SELF_BUILD:
            # ① 待自建：必须有配方。清单里有、配方表里没有 ⇒ **不猜**，点名。
            key_path = _self_build_path(r)
            if not key_path:
                problems.append(f"「{label}」标了待自建，却没有可用的**目标包路径**"
                                "（清单那一行的 `material` 或 `parent` 是空的）—— 补上再跑。")
                continue
            cand_paths.add(key_path)
            build_paths.add(key_path)
            # ⚠ **点名（`only`）在这里也必须生效**（2026-10-05 修）：以前 `only_set` 只在
            #   "已经有实例"那一支被用，于是**待自建 / 待派生这两条真正要动手的路从不看它**
            #   ⇒ `create_surfaces(only=["/Game/…/M_X"])` 会把清单里**所有**待建的一起建出来。
            #   而阶段五删掉演练之后，`only` 正是唯一"先小范围试"的手段（见文件头的口径）。
            if only_set and key_path not in only_set:
                continue
            if key_path not in table:
                problems.append(
                    f"「{label}」待自建，目标 `{key_path}`，但 `config/surface_materials.json` 的 "
                    "`create` 段里**没有它的配方** —— 我们**不猜怎么建**。"
                    "往 `create` 段补一条（键就是上面这个包路径），再重跑。")
                continue
            # ⚠ **先按类型筛**（2026-10-07 教训：遍历用户写的表之前必须筛类型 ——
            #   `create` 段里混着 `_doc_*` 说明条目，第一次真跑 `tune` 就撞出一句没有正文的
            #   `Error executing tool`）。不是配方对象 ⇒ **不猜、当场点名**，别让裸异常冒上去。
            _raw_spec = table[key_path]
            if not isinstance(_raw_spec, dict):
                problems.append(
                    f"「{label}」：`create` 段里 `{key_path}` 那条**不是一份配方对象**"
                    f"（读到的是 {type(_raw_spec).__name__}）—— 先改配置再跑。")
                continue
            spec, errs = _validate_recipe(key_path, _raw_spec)
            problems += [f"「{label}」：{e}" for e in errs]
            if not errs:
                to_create.append({"row": r, "spec": spec, "parent": spec["parent"]})
            continue

        if not inst:
            # 有 parent（或 material）但还没有实例 ⇒ 要派生
            if not parent:
                problems.append(f"「{label}」既没有 `instance` 也没有 `parent`/`material` —— "
                                "不知道从谁派生，**不猜**。")
                continue
            # ⚠ 同一道点名筛子（2026-10-05 修，理由见上面那段）：靶子可以是**父级**，
            #   也可以是它**要派生的那个实例路径**（`derived_instance_of()` —— 与第 ⑤⑥ 步同一算法）。
            _inst_todo = derived_instance_of({"parent": parent}, table)
            cand_paths.add(parent)
            if _inst_todo:
                cand_paths.add(_inst_todo)
            build_paths.add(_inst_todo or parent)   # 报文里列"会被建出来的那个实例路径"
            if only_set and parent not in only_set and (not _inst_todo or _inst_todo not in only_set):
                continue
            to_derive.append({"row": r, "parent": parent, "specified": None})
            continue
        # 已经有实例：幂等跳过（除非点名要求重建 —— 那也不重建，避免覆盖用户的东西）
        cand_paths.add(inst)      # 点到"已经有实例"的行 = 命中（没什么要建，但不算点错）
        if only_set and inst not in only_set:
            continue

    # ---- 待自建的配方里若给了 `parent`，那一条本来就是"建实例"（配方表的历史写法）----
    #      ⚠ 新口径下待自建那条应该是"建材质"（不带 parent）；带了 parent 的按派生处理，
    #        并在报文里点名（避免"你以为在建材质、其实在建实例"）。
    deriving_from_recipe: list[dict] = []
    for it in list(to_create):
        if it["spec"]["parent"]:
            to_create.remove(it)
            deriving_from_recipe.append(it)

    # ---- ⚠ **2026-10-07 补：`create` 段里"没有清单行指向"的那些配方也要建** ----------------
    # 由来（用户指令「1.2也做了」→ 做树冠时**真跑撞出来**）：工具描述与
    # `docs/阶段五-表面材质.md` 写的是「留空 = 建清单里待自建的 **＋ `create` 段里的全部**」，
    # 而上面那个循环**只遍历清单行** ⇒ **配方表里那条没有任何清单行指向的配方永远不会被建**：
    #   · 树冠那条 `/Game/UEMCP/Materials/MI_SakuraLeaves`（`parent` = 库里 `normal_leaves`）
    #     就是这种 —— 清单里 `tree·normal_leaves` 那一行指向的是 `M_Sakura`，
    #     所以它**永远轮不到**；`only` 点名它还判"一条都没命中"（报文说"没命中"，人却明明写了它）。
    # ⚠ 这是"**文档承诺了、代码没做**"那一类（与本项目最忌的"两套口径"同源）。
    # 判据（与 `_recipe_target_for` 用**同一条筛子**，不另写一份）：
    #   · 只认 `isinstance(spec, dict)`、键不以 `_` 开头；
    #   · **已经被清单行指向的**（`cand_paths` 里有）不重复收 —— 那是上面那些支路的活；
    #   · **点名（`only`）照样生效**：认**目标包路径**（= 配方那个键）或**它的父级**；
    #   · 带 `parent` 的走**派生实例**（与"配方表的历史写法"同一条路，含参数名那道闸）；
    #     不带 `parent` 的走**建材质**（与待自建同一条路，建完照口径再派生一份实例）。
    for _path, _raw in (table or {}).items():
        _p = pkg_path(_path)
        if (not _p) or str(_path).startswith("_") or not isinstance(_raw, dict):
            continue
        if _p in cand_paths:                     # 已经有清单行指向它 ⇒ 归上面那几条支路
            continue
        _spec0, _errs0 = _validate_recipe(_p, _raw)
        _par0 = pkg_path(_spec0.get("parent")) if not _errs0 else pkg_path(_raw.get("parent"))
        if only_set and _p not in only_set and (not _par0 or _par0 not in only_set):
            continue
        cand_paths.add(_p)
        build_paths.add(_p)
        if _errs0:
            problems += [f"`create` 段 `{_p}`（没有清单行指向它）：{e}" for e in _errs0]
            continue
        _note_extra = ("（这一条**没有清单行指向它** —— 按 `create` 段建出来）")
        if _spec0["parent"]:
            deriving_from_recipe.append({
                "row": {"instance": _p, "parent": _spec0["parent"], "label": _p, "note": _note_extra},
                "spec": _spec0, "parent": _spec0["parent"]})
        else:
            to_create.append({
                "row": {"label": _p, "material": _p, "note": _note_extra,
                        "status": MAT_SELF_BUILD, "instance_status": INST_TO_CREATE},
                "spec": _spec0, "parent": _spec0["parent"]})

    # ⚠ **配方里那些"要覆盖的参数"必须先核对**（2026-10-07 加 · 与硬规则 6 同源）：
    #   MI 只能覆盖**父级已有的参数** —— 名字不在官方 `list_parameters(父级)` 的返回里 ⇒
    #   **不猜**，整批拒收（一个字节都不写）。为什么值得单独一道：这条同时把
    #   「配方写错了参数名」与「父级真没这个参数」分开报 —— 两种都不是偶发，都该当场说清。
    #   ⚠ 官方**读不到**参数表时也拒收（读不到 ≠ 没有 —— 把"没读到"当"没问题"是本项目最忌的）。
    for it in deriving_from_recipe:
        _spec = it["spec"]
        _wanted = dict(_spec.get("params") or {})
        if not _wanted:
            continue
        _par = pkg_path(_spec["parent"])
        try:
            calls += 1
            _known = {str(p["name"]) for p in await list_parameters(repo, _par) if p.get("name")}
        except ToolError as exc:
            problems.append(f"`{_spec['path']}`：父级 `{_par}` 的参数表**读不回来**（{exc}）—— "
                            "要覆盖的参数名没法核对，**不猜、不建**。")
            continue
        _unknown = sorted(set(_wanted) - _known)
        if _unknown:
            problems.append(
                f"`{_spec['path']}`：要覆盖的参数 {_unknown} **不在**父级 `{_par}` 的参数表里"
                f"（父级有：{'、'.join(sorted(_known)) or '一个都没有'}）—— "
                "MI 只能覆盖父级已有的参数。")

    # ⚠ **点名全不命中 ⇒ 拒收**（2026-10-05 加，与硬规则 6「参数不许被静默吞掉」同源）：
    #   `only` 是阶段五"先小范围试"的唯一手段 —— 点了一串名字却一条都没对上时，
    #   **不许静默地把清单里所有待建 / 待派生的一起建出来**（那是这条参数最危险的失效方式）。
    if only_set:
        _miss = sorted(p for p in only_set if p not in cand_paths)
        if _miss:
            _pending = sorted(build_paths)
            _have = len(cand_paths) - len(build_paths)      # 已经有实例、没事可做的那几行
            problems.append(
                "`only` 点名的这些路径**一条都没命中**任何「要建 / 要派生」的目标（"
                + "、".join(f"`{p}`" for p in _miss) + "）。"
                "`only` 认的是**包路径**：材质（`missing_self_build` 那一行的落点）、"
                "或**父级** / 它**要派生的实例路径** —— "
                "⚠ 它**不认** `element_key`（那是 `apply_surfaces(only=…)` 的口径）。\n"
                "· **这一趟真要建 / 要派生的目标**："
                + ("、".join(f"`{p}`" for p in _pending) if _pending
                   else "**一条都没有** —— 清单里每一行都已经有实例了，没有要建的东西")
                + (f"\n· （另有 {_have} 行的实例**已经存在**、不用建 —— 点它们的实例路径算命中，"
                   "但没事可做，所以不列在上面。）" if _have > 0 else ""))

    if problems:
        raise ToolError("建材质 / 派生实例**拒收** —— **一个字节都没写**：\n  - "
                        + "\n  - ".join(problems))

    created: list[CreatedRow] = []
    materials_created = instances_created = already = failed = 0

    # ---- ⓪ 台账里"我们建过的"路径（判"已存在的那个是不是我们的"）------------------------
    #      ⚠ 原来这里算了一个 `existing` 名集合却**从没用过**（死代码，还让人以为重名有处理）——
    #        现在换成长度真用得上的那一份：台账 `created` 段。
    #      ⚠ **本趟刚认下来的路径也要加进来**：同一块母材质被两行引用时（实测 `grass` 两行都指
    #        `MI_oeeb70`），第二行会撞见第一行刚建出来的那一个 —— 不加就会误报"不是我们建的"。
    mine = known_created_paths(repo)

    # ---- ① 建母材质（只有待自建才走到这儿）----------------------------------------
    for it in to_create:
        spec = it["spec"]
        path = spec["path"]
        calls += 1
        try:
            if await asset_exists(repo, path):
                already += 1
                created.append(CreatedRow(path=path, kind="material", status="already",
                                          row_uid=row_uid(it["row"]),
                                          note="已经存在 ⇒ 跳过（绝不覆盖你已有的东西）"))
                continue
        except ToolError as exc:
            failed += 1
            created.append(CreatedRow(path=path, kind="material", status="failed",
                                      error=f"`exists()` 调用失败：{exc}"))
            continue
        folder, name = path.rsplit("/", 1)
        try:
            calls += 1
            await repo.official("create_material", {"folder_path": folder, "asset_name": name},
                                toolset=TS_MATERIAL)
            # 参数节点 + 连线（每个输出一个参数节点，再连到 MP_<输出>）
            for i, (out_name, raw) in enumerate((spec["params"] or {}).items()):
                is_vec = isinstance(raw, (list, tuple))
                calls += 1
                node = await repo.official(
                    "add_expression",
                    # ⚠ 官方这个函数的入参键是 **`material_or_function`**（不是 `material`）——
                    #   2026-10-04 对照 `describe_toolset(MaterialTools)` 的 schema 核过。
                    #   写错键名官方会直接拒（缺必填参数），整条建材质全废。
                    {"material_or_function": {"refPath": repo.to_object_path(path)},
                     "expression_class": {"refPath": (MAT_EXPR_VECTOR if is_vec else MAT_EXPR_SCALAR)},
                     "x": -400, "y": 180 * i},
                    toolset=TS_MATERIAL)
                nref = _ref(repo, node)
                if not nref:
                    raise ToolError("官方没返回新建节点的引用")
                calls += 1
                await repo.official(
                    "set_properties",
                    # ⚠ **两个坑都在这一处**（2026-10-07 实测，见 `linear_color_value()` 的说明）：
                    #   ① **值的形状**：向量必须给 `{"r","g","b","a"}` 对象 —— 4 元数组官方
                    #      **静默失败**（这就是那 6 块材质默认值是垃圾的根因）⇒ 走 `linear_color_value()`；
                    #   ② **写入顺序**：字典里 `parameterName` **排在 `defaultValue` 前面** ——
                    #      实测"先写值、后命名"会把值写坏（节点值变成 NaN / 次正规数）。
                    #      ⚠ 别信 `op_rows` 那个 detail 打印的顺序：它**不反映真实写入顺序**。
                    #      这里是单个 dict 一次写（Python 保序、`json.dumps` 也保序）。
                    {"instance": {"refPath": nref},
                     "values": json.dumps({MAT_PARAM_NAME_PROP: out_name,
                                           MAT_PARAM_VALUE_PROP: linear_color_value(raw)},
                                          ensure_ascii=False)},
                    toolset=TS_OBJECT)
                # ---- **写后读回**（2026-10-07 加 · 缺的正是这道纪律）--------------------
                # ⚠ 由来（`docs/阶段五-表面材质.md` 的"后果"）：`create` 建了 6 块材质 + 6 个实例，
                #   **12 个全报 `status: ok`**，而其中 6 块的向量默认值是垃圾（NaN）。
                #   为什么没人当场发现：**这一档写完从来不读回**（`tune` / `apply` 都有读回，
                #   只有建材质这条腿没有）—— 于是"写进去但读回不是它"只能等用户到 UE 里看颜色。
                # 现在读 `get_properties(节点)`，比 `parameterName` 与 `defaultValue`：
                #   · **读回来对不上** ⇒ 这一块**判失败**（并说清"它已经建出来、重跑会被幂等跳过"）
                #     —— 宁可当场红，也不许把垃圾值报成 ok（那正是这个 BUG 的成因之一）；
                #   · **没核成**（官方调用失败 / 返回里找不到那两个键）⇒ **只记 warning**，
                #     ⚠ 一个是"读不回来 ≠ 没问题"，另一个是**别让一道只做核对的调用把整条建材质打死**
                #       （万一是我们参数写错，那是"没核成"，不该变成"建不出来"）。
                _back: Any = None
                try:
                    calls += 1
                    _back = await repo.official("get_properties",
                                                {"instance": {"refPath": nref}}, toolset=TS_OBJECT)
                except ToolError as exc:
                    warns.append(f"⚠ 「{path}」的参数节点 `{out_name}` **没核成**"
                                 f"（`get_properties` 调用失败：{exc}）—— "
                                 "**读不回来 ≠ 没问题**，这一块自己到 UE 里看一眼。")
                if _back is not None:
                    _b_name = prop_of(_back, MAT_PARAM_NAME_PROP)
                    _b_val = prop_of(_back, MAT_PARAM_VALUE_PROP)
                    if _b_name is None and _b_val is None:
                        warns.append(f"⚠ 「{path}」的参数节点 `{out_name}` **没核成**："
                                     "`get_properties` 的返回里找不到 `parameterName` / "
                                     "`defaultValue` —— **读不回来 ≠ 没问题**，自己去 UE 里看一眼。")
                    elif (str(_b_name if _b_name is not None else "") != str(out_name)
                          or not value_same(_b_val, linear_color_value(raw))):
                        raise ToolError(
                            f"参数节点 `{out_name}` **写进去了但读回不是它**"
                            f"（读回 `{_b_name}` / `{_b_val!r}`）—— 这一块材质**已经建出来**"
                            f"（`{path}`），但它是半成品；⚠ 重跑 `create` 会因幂等**跳过它**，"
                            "得先在 UE 里删掉 / 改名再建（或用 `mat_mode=\"ops\"` 按 `refPath` 修）。")
                calls += 1
                await repo.official(
                    "connect_to_output",
                    # ⚠ 官方 schema：`expression`（要连的那个节点）+ `output_name` +
                    #   `material_property`（如 `MP_BaseColor`）—— **没有** `material` / `from_expression`
                    #   这两个键（2026-10-04 对 schema 核实）。
                    {"expression": {"refPath": nref},
                     "output_name": "",
                     "material_property": f"MP_{out_name}"},
                    toolset=TS_MATERIAL)
            for flag, val in (spec["flags"] or {}).items():
                calls += 1
                await repo.official(
                    "set_properties",
                    {"instance": {"refPath": repo.to_object_path(path)},
                     "values": json.dumps({flag: val}, ensure_ascii=False)},
                    toolset=TS_OBJECT)
            calls += 1
            await repo.official(
                "recompile",
                # ⚠ 官方入参键同样是 `material_or_function`（对 schema 核实）。
                {"material_or_function": {"refPath": repo.to_object_path(path)}},
                toolset=TS_MATERIAL)
            materials_created += 1
            created.append(CreatedRow(path=path, kind="material", status="ok",
                                      row_uid=row_uid(it["row"]),
                                      note="空材质 + 参数节点 + 连线（复杂材质图走 `mode=\"ops\"`）"))
        except ToolError as exc:
            failed += 1
            created.append(CreatedRow(path=path, kind="material", status="failed",
                                      row_uid=row_uid(it["row"]), error=str(exc)))

    # ---- ② 派生实例（有材质没实例的那些 + 配方里带 parent 的）------------------------
    todo_derive = [{"row": it["row"], "parent": it["parent"], "specified": None, "params": {}}
                   for it in to_derive] \
        + [{"row": it["row"], "parent": it["spec"]["parent"], "specified": None,
            "params": dict(it["spec"].get("params") or {})}
           for it in deriving_from_recipe]
    # 待自建建完材质之后，也要给它派生实例（用户口径：材质和实例都建出来）
    for it in to_create:
        if any(c.path == it["spec"]["path"] and c.status in ("ok", "already") for c in created):
            todo_derive.append({"row": it["row"], "parent": it["spec"]["path"],
                                "specified": None, "params": {}})

    for it in todo_derive:
        row = it["row"]
        parent = pkg_path(it["parent"])
        key = str(row.get("element_key") or "")
        label = str(row.get("label") or key)
        uid = row_uid(row)
        # ⚠ 派生到哪儿**只有一处算法**（`derived_instance_of()`）—— 第 ⑥ 步贴的时候也调它，
        #   两处各算一次的话，"建出来的名字"和"贴上去的名字"可能不一样（用户看到的就是贴错）。
        #   ⚠ **2026-10-07 改：喂给它的是 `row`（整行），不再是手搓的 `{"parent": parent}`** ——
        #     第 ⑥ 步用的是 `surface_instance(row)`（= `instance_of(row) or derived_instance_of(row)`），
        #     它**先看这一行自己写的 `instance`**。原来这里把 `instance` 丢掉 ⇒ **清单点名的那条路径
        #     被无视**（实测：清单写 `/Game/UEMCP/Materials/MI_SakuraLeaves`，实例却建到父级目录下
        #     叫 `MI_normal_leaves`）⇒ ⑤ 建一处、⑥ 找另一处，`exists()` 当场验不过。
        #     现在两边**同一个函数 + 同一个输入**，这条分叉没有了。
        inst_path = derived_instance_of(row, table)
        if not inst_path:
            failed += 1
            created.append(CreatedRow(path="", kind="instance", row_uid=uid, parent=parent,
                                      status="failed",
                                      error=f"「{label}」算不出实例路径（父级是空的）"))
            continue
        calls += 1
        try:
            if await asset_exists(repo, inst_path):
                already += 1
                # ⚠ "已存在"分两种：**我们建的**（幂等跳过，正常）与**别人的**（也跳过，但要点名
                #   —— 绝不覆盖，也不假装它是我们建的）。判据 = 台账 `created` 段，不靠猜。
                if inst_path in mine:
                    note = "实例已存在（台账里记着是我们建的）⇒ 跳过"
                else:
                    note = ("实例已存在，但**台账里没有它**（不是我们建的）⇒ 跳过、绝不覆盖；"
                            "这一次按官方 `exists()` 认它是这一行的靶子。")
                    warns.append(f"⚠ 「{label}」要派生的实例 `{inst_path}` **已经存在**，"
                                 "而且不是台账里我们建的 —— 这次没覆盖它，"
                                 "直接按 `exists()` 认了它是这一行的靶子。要换请在 UE 里把它挪走 / "
                                 "改名后再重跑 `create_surfaces()`。")
                created.append(CreatedRow(path=inst_path, kind="instance", row_uid=uid, parent=parent,
                                          status="already", note=note))
                mine.add(inst_path)      # 本趟认下来的路径 → 后面的行别再把同伴当"别人的"
                _remember_instance(repo, row, inst_path, parent)
                continue
        except ToolError as exc:
            failed += 1
            created.append(CreatedRow(path=inst_path, kind="instance", row_uid=uid, parent=parent,
                                      status="failed", error=f"`exists()` 调用失败：{exc}"))
            continue
        folder, name = inst_path.rsplit("/", 1)
        try:
            calls += 1
            got = await repo.official(
                "create",
                {"folder_path": folder, "asset_name": name,
                 "parent": {"refPath": repo.to_object_path(parent)}},
                toolset=TS_MATINST)
            made = pkg_path(_ref(repo, got)) or inst_path
            if made != inst_path:
                warns.append(f"⚠ 官方把实例建成了 `{made}`，而我们算出来的名字是 `{inst_path}` —— "
                             "第 ⑥ 步贴的时候按**算出来的那个名字**找靶子，对不上会当场拒收。"
                             "真出现这种情况请把这条报回来（判据要跟着改）。")
            mine.add(made)               # 同上：本趟刚建出来的，别让后面的行当成"别人的"
            mine.add(inst_path)
            instances_created += 1
            # ---- ③ 数值提升：父级的参数现值 → 新实例的覆盖 --------------------------
            promoted: dict[str, Any] = {}
            promoted, used, missed = await _promote_values(repo, parent, made)
            calls += used
            if promoted:
                note = "从父级派生；父级参数现值已提升为实例覆盖"
            elif missed:
                # ⚠ 有参数、但一个都没提升成 —— **不许**写成"父级没有可提升的参数值"
                note = ("从父级派生；**参数一个都没提升**（原因见 warnings）—— "
                        "这一次实例是照父级的默认值走的，别以为数值已经提升过了")
                warns.append(f"⚠ 「{label}」派生出来的实例 `{made}` **一个参数都没提升成功**："
                             + "；".join(missed[:6])
                             + ("…" if len(missed) > 6 else "")
                             + "。第 ⑨ 步要调它之前，先确认这些参数到底叫什么、是什么类型。")
            else:
                note = "从父级派生（官方报的父级参数表是空的 —— 确实没有可提升的值）"
            # ---- ④ 配方里写的覆盖参数（2026-10-07 加）--------------------------------
            # ⚠ **这是「配方那条路一直没生效」的根因**：配方表里"带 `parent` 的那条"，
            #   其余非 `_` 键按口径就是**要覆盖的参数**（`_validate_recipe` 是这么收的、
            #   工具描述也是这么承诺的）—— 可以前**一个都没往下传**（`todo_derive` 只带了
            #   parent / specified）⇒ 派生出来的实例永远是父级原样。
            #   实测（`docs/阶段五-表面材质.md`「追加实测」第 5 条）：给 `normal_leaves`
            #   派生 + `BaseColorFactor: [3.0,0.65,1.9,1.0]`，报 `ok`、读回却是 `{1,1,1,1}`
            #   —— 与父级一模一样（**根本不是"静默写坏"，是压根没写**）。
            #   ⚠ 值按**形状**定类型（`_kind_of`，与 `tune` 同一份判据）；**向量走
            #     `linear_color_value()`**（那是另一个 BUG 的根因：4 元数组官方静默失败）。
            _recipe_params = dict(it.get("params") or {})
            if _recipe_params:
                _setters4 = {"scalar": "set_scalar_parameter", "vector": "set_vector_parameter",
                             "texture": "set_texture_parameter",
                             "static_switch": "set_static_switch_parameter"}
                _done4 = 0
                for _pn, _pv in _recipe_params.items():
                    _kind4, _why4 = _kind_of(_pv)
                    if not _kind4:
                        warns.append(f"⚠ 「{label}」配方里的 `{_pn}` **没写进去**：{_why4}")
                        continue
                    if _kind4 == "texture":
                        _val4: Any = {"refPath": repo.to_object_path(str(_pv))}
                    elif _kind4 == "static_switch":
                        _val4 = (_pv if isinstance(_pv, bool)
                                 else str(_pv).strip().lower() == "true")
                    else:
                        _val4 = linear_color_value(_pv)
                    try:
                        calls += 1
                        await repo.official(
                            _setters4[_kind4],
                            {"instance": {"refPath": repo.to_object_path(made)},
                             "name": str(_pn), "value": _val4}, toolset=TS_MATINST)
                    except ToolError as exc:
                        warns.append(f"⚠ 「{label}」配方里的 `{_pn}` **没写进去**：{exc}")
                        continue
                    # ⚠ **写后读回**（2026-10-07 补：这一段原来**只写不读**）—— 判据与
                    #   `tune` / `apply` / `ops` **同一条**（`value_same()`）：
                    #   读回来不是它 ⇒ **不算写成功**（`_done4` 不加），如实进 warnings；
                    #   读不回来 ⇒ 记「没核成」，**不假装核过**（本项目的红线）。
                    _getters4 = {"scalar": "get_scalar_parameter", "vector": "get_vector_parameter",
                                 "texture": "get_texture_parameter",
                                 "static_switch": "get_static_switch_parameter"}
                    try:
                        calls += 1
                        _back4 = await repo.official(
                            _getters4[_kind4],
                            {"instance": {"refPath": repo.to_object_path(made)},
                             "name": str(_pn)}, toolset=TS_MATINST)
                    except ToolError as exc:
                        warns.append(f"⚠ 「{label}」配方里的 `{_pn}` **写进去了但没核成**（{exc}）"
                                     " —— **读不回来 ≠ 没问题**（去 UE 里看一眼那个值）")
                        continue
                    if not value_same(_back4, _val4):
                        warns.append(f"⚠ 「{label}」配方里的 `{_pn}` **写了但读回不是它**"
                                     f"（写的是 {value_text(_val4)}，读回 {value_text(_back4)}）"
                                     "—— 如实报，**不算写成功**")
                        continue
                    promoted[str(_pn)] = _val4
                    _done4 += 1
                # ⚠ 报数如实：写成几个说几个 —— 不许用"配方参数已写入"糊过失败的那几条。
                note += (f"；配方里的覆盖参数 {_done4}/{len(_recipe_params)} 个写入成功"
                         if _done4 else
                         f"；配方里的 {len(_recipe_params)} 个覆盖参数**一个都没写进去**"
                         "（原因见 warnings）")
            created.append(CreatedRow(path=made, kind="instance", row_uid=uid, parent=parent,
                                      status="ok", promoted=promoted, note=note))
            _remember_instance(repo, row, made, parent)
        except ToolError as exc:
            failed += 1
            created.append(CreatedRow(path=inst_path, kind="instance", row_uid=uid, parent=parent,
                                      status="failed", error=str(exc)))
            continue

    ledger = ""
    if created:
        try:
            ledger = ledger_merge(repo, "created", [c.model_dump() for c in created])
        except OSError as exc:
            warns.append(f"⚠ 台账没写成（{exc}）—— 这次**没留下「建了什么」的记录，回滚时得手工查**。")
    if instances_created or materials_created:
        warns.append("⚠ 本工具**不自动存盘**：新建的材质 / 实例只在内存里 —— "
                     "第 ⑦ 步「用户满意就保存」那一次才存（`apply_surfaces(save=True)` 或你在 UE 里 Ctrl+S）。")
    return CreateReport(
        stage="阶段五 · 第 ⑤ 步（建材质 / 派生实例）", mode="create",
        target="、".join(sorted({c.path for c in created if c.path})), planned=len(created),
        created=materials_created + instances_created, materials_created=materials_created,
        instances_created=instances_created, already=already, failed=failed, rows=created,
        ledger_path=ledger, official_calls=calls,
        next_step=("① `apply_surfaces(focus=True)` 真贴 —— 第 ⑥ 步按**同一支算法**"
                   "（`derived_instance_of()`）算出这些派生实例的路径，**清单不必先回写**；"
                   "② 在 UE 里看，不满意就 `create_surfaces(mat_mode=\"tune\", tune={…})` 调参数"
                   "（这些实例已经是清单里那些行的靶子，能直接调）；"
                   "③ 满意 ⇒ `apply_surfaces(save=True)`（**先回写清单、再存盘**）。"),
        warnings=warns)


def _self_build_path(row: dict) -> str:
    """待自建那一行，"我们要建的那块材质"落在哪个包路径。

    取法（**不猜**）：`parent` → `material` → `instance`，哪个非空用哪个；
    三个都空 = 我们不知道该建到哪儿 ⇒ 调用方拒收并点名（让他补）。
    """
    for field in ("parent", "material", "instance"):
        p = pkg_path(row.get(field))
        if p:
            return p
    return ""


def _remember_instance(repo: SurfacesRepo, row: dict, inst: str, parent: str) -> None:
    """把派生出来的实例路径**填回内存里的那一行**（清单落盘由第 ⑦ 步做）。

    ⚠ 这里**故意不写盘**：用户口径是「直到用户满意为止才写回清单」——
      第 ⑤ 步刚落完就写，等于把"还没看过效果的东西"当成定稿。
    """
    row["instance"] = pkg_path(inst)
    row["instance_status"] = INST_IS_INSTANCE
    row["parent"] = pkg_path(parent)
    if not pkg_path(row.get("material")):
        row["material"] = pkg_path(parent)


def param_kind(raw: Any) -> str:
    """官方 `list_parameters` 报的类型 → 我们那四类的哪一类（认不出 ⇒ 空串）。**纯函数**。

    ⚠ 用**子串**匹配而不是等值比较（官方那串没人保证就是 `"scalar"` 这四个字）：认不出时
      由调用方**如实报出来**，不许当成"父级没有参数"（那会把"没读到"说成"没问题"）。
    """
    t = str(raw or "").strip().lower()
    for kind, needle in (("static_switch", "switch"), ("scalar", "scalar"),
                         ("vector", "vector"), ("texture", "texture")):
        if needle in t:
            return kind
    return ""


async def _promote_values(repo: SurfacesRepo, parent: str, inst: str) -> tuple[dict, int, list[str]]:
    """**数值提升**：把（新实例身上继承来的）参数现值读出来、写进它自己当覆盖 →
    `(提升了的参数, 调用数, 没提升的)`。

    ⚠ 参数名**只认官方 `list_parameters` 的返回**；读不到值的参数**跳过**（不猜、不编）。
    ⚠ 只提升**标量 / 向量 / 纹理 / 静态开关**这四类（官方就是这四类）。
    ⚠ 第三个返回值是**没提升成的那些（带原因）** —— 有它，调用方才能把
      「父级本来就没有可提升的参数」与「有参数、但我们没认出来 / 没读回来」**分开报**：
      两者混成一句"没有可提升的参数值"就是**把"没读到"说成"没问题"**。

    ⚠ **2026-10-07 改：读 / 列参数都打在「新实例」上，不再打在父级上。**
      原来读的是父级，而官方的 `get_*_parameter`（`TS_MATINST` 那一族）**只认
      `MaterialInstanceConstant`** —— 父级是**材质**（M_*）时它直接报
      `Parameter error: /Game/…/M_Car.M_Car is not valid MaterialInstanceConstant for property
      'instance'.` ⇒ **一个参数都提升不了**（实测：6 个派生实例每个都带这条 warning）。
      打在实例上是一样的值（实例刚建出来、还没写任何覆盖 ⇒ 读到的就是父级的现值），
      而**这条路对 M 与 MI 两种父级都通**。
      ⚠ 边界照实说：这样读到的"现值"是**实例视角的有效值**；父级是 MI 时它可能与父级文件里
      写死的那个值来源不同（多层继承），但**数值语义一致**（都是"这次派生继承到的那个值"）。
      ⚠ `parent` 这个形参**保留**（调用方按它写报文 / 台账），但函数体里不再拿它当靶子。
    """
    out: dict[str, Any] = {}
    missed: list[str] = []
    calls = 0
    try:
        params = await list_parameters(repo, inst)
        calls += 1
    except ToolError as exc:
        return out, calls, [f"实例参数表读不回来（{exc}）"]
    if not params:
        return out, calls, []
    getters = {"scalar": "get_scalar_parameter", "vector": "get_vector_parameter",
               "texture": "get_texture_parameter", "static_switch": "get_static_switch_parameter"}
    setters = {"scalar": "set_scalar_parameter", "vector": "set_vector_parameter",
               "texture": "set_texture_parameter", "static_switch": "set_static_switch_parameter"}
    for p in params:
        name, kind = p["name"], param_kind(p["type"])
        if not kind:
            missed.append(f"{name}（官方报的类型 {p['type']!r} 我们没认出来）")
            continue
        try:
            calls += 1
            old = await repo.official(
                getters[kind],
                {"instance": {"refPath": repo.to_object_path(inst)}, "name": name},
                toolset=TS_MATINST)
        except ToolError as exc:
            missed.append(f"{name}（现值读不回来：{exc}）")
            continue                     # 读不到就跳过（不编一个值出来）
        try:
            calls += 1
            await repo.official(
                setters[kind],
                {"instance": {"refPath": repo.to_object_path(inst)}, "name": name,
                 "value": old}, toolset=TS_MATINST)
        except ToolError as exc:
            missed.append(f"{name}（写进新实例没成：{exc}）")
            continue
        out[name] = old
    return out, calls, missed


# ══════════════════════════════════════════════════════════════════════════════
# 十、第 ⑥⑧ 步：apply（贴 + 聚焦 + 用户满意才回写清单 / 存盘）
# ══════════════════════════════════════════════════════════════════════════════

async def apply_surfaces(repo: SurfacesRepo, ledger: dict, only: list[str] | None = None,
                         focus: bool = False, focus_max: int = DEFAULT_FOCUS_MAX,
                         save: bool = False, user_quote: str = "") -> SurfaceReport:
    """**第 ⑥⑧ 步**：按材质清单把**实例**贴到白膜行上（组件级覆盖）。

    闸门（任一条不过 ⇒ **一个组件都不写**）：
      ① 清单里要有带实例的行（没有 ⇒ 拒收，让他先 `probe`/`confirm`/`create`）；
      ② 台账里的关卡必须与当前关卡一致（换图了，引用在这儿没意义）；
      ③ 每个实例路径逐个 `exists()`；
      ④ 逐行：找 `cube` 组件 → 读现值（幂等）→ 写 → **读回核对**。
    ⚠ `save=true` 才落盘，而且**先回写清单再存盘**（用户口径：满意才写回）。
    """
    warns: list[str] = []
    calls = 0
    rows = [r for r in (ledger.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        raise ToolError(
            "还没有搭建台账 —— 贴材质靠台账里的 **Actor 引用**，没台账就不知道贴谁。"
            "先把关卡搭起来（`execute_build()`）；场景已经搭好了就先 "
            "`execute_build(mode=\"incremental\", adopt=true)` 认领现状。")

    by_key = _rows_by_key(repo)
    # ⚠ **2026-10-07 用户定（"按你的来"）：不拦，但如实报** —— 原来这里挂着一句
    #   "要不要加'没有签字指纹 ⇒ 拒收'的闸，**等用户定**"。我定的口径：
    #   **报警不拦**（与越界那条同一种处理：只报红、不拦）。理由：`exists()` 逐条验活
    #   + 台账一致 + 写后读回这三道闸保证的是「**贴上去的就是清单点名的那块**」；
    #   而"这份清单**是不是 `confirm` 落盘的**"是另一件事 —— 拿它当闸会挡住正当事
    #   （手改一行就想马上看效果 / 沿用一份拷来的清单）。
    #   ⚠ **这里不做指纹比对**：那要枚举整个材质库（`snapshot_stale()` 的成本）——
    #     要核"过没过期"用 `get_asset_list()`（阶段一那支）或 `apply_surfaces(mode="probe")`。
    if not str(load_snapshot(repo).get("hash") or ""):
        warns.append(
            f"⚠ 这份材质清单**没有签字指纹**（`{MATERIAL_SNAPSHOT_PATH.name}` 不在 / 没有 `hash`）"
            "—— 它可能**不是 `confirm` 落盘的**（手写 / 从别处拷来的）。**这次照贴**"
            "（闸门一条没放松：路径逐个 `exists()`、台账对得上、写完读回），"
            "但你自己核一眼那份清单；要重新签字走 `probe` → `confirm`。")
    # ⚠ **配方表读一次、全程共用**（2026-10-07 加）：派生实例的落点判据要它
    #   （`derived_instance_of(row, recipes)`）—— 第 ⑤ 步与第 ⑥ 步**必须传同一张表**，
    #   否则"建在哪"和"贴哪"又会分叉（那正是修掉的那条）。
    _recipes = create_recipes(repo)[0]
    # ⚠ 靶子取 `surface_instance()`（有实例用实例；"有材质缺实例"那一类用**派生路径**）——
    #   直接看 `instance` 会把那一类整批跳过，而报文只说一句"没有可用的实例行"，
    #   人看不出是"清单里真没有"还是"第 ⑤ 步没跑"。
    if not any(surface_instance(m, _recipes) for m in manifest_rows(repo)):
        raise ToolError(
            f"**没有可贴的材质实例** —— 材质清单 `{MATERIAL_LIST_PATH}` 里一份能当靶子的行都没有。"
            "**一个组件都没写。** 先走：`apply_surfaces(mode=\"probe\")` → `mode=\"confirm\"` → "
            "`create_surfaces()`（有材质没实例的，它会派生一份）。")

    ledger_level = str(ledger.get("level") or "")
    if ledger_level and repo.level and ledger_level != repo.level:
        raise ToolError(f"台账记的关卡是 `{ledger_level}`，现在是 `{repo.level}` —— **不是同一张图**，"
                        "台账里的 Actor 引用在这儿没有意义。**一个组件都没写**。")

    # ---- 挑出要贴的行（白膜；按 element_key 对上清单）-------------------------------
    only_set = {str(x).strip().lower() for x in (only or []) if str(x).strip()}
    targets: list[tuple[dict, dict]] = []
    skipped: list[str] = []
    # ⚠ **`无` 行**（非白膜、问过用户确实没有材质）**点名跳过** —— 不许静默：用户读报文时
    #   要知道"这一行是有意不贴的"，而不是以为它贴上了（用户 2026-10-04 晚口径）。
    none_keys = {str(m.get("element_key") or "").strip().lower()
                 for m in manifest_rows(repo)
                 if str(m.get("status") or "").strip().lower() == MAT_NONE}
    none_skipped: list[str] = []
    for r in rows:
        if str(r.get("kind") or "") != "whitebox":
            continue
        key = str(r.get("element_key") or "").strip().lower()
        if only_set and key not in only_set:
            continue
        hits = [m for m in (by_key.get(key) or []) if surface_instance(m, _recipes)]
        if not hits:
            if key in none_keys:
                none_skipped.append(str(r.get("label") or r.get("uid")))
                continue
            skipped.append(f"{r.get('label') or r.get('uid')}：清单里没有可用的实例行（`{key}`）")
            continue
        insts = {surface_instance(m, _recipes) for m in hits}
        if len(insts) > 1:
            skipped.append(f"{r.get('label')}：清单里 `{key}` 有 {len(hits)} 条、"
                           f"**实例各不相同**（{'、'.join(sorted(insts))}）—— 判不出该用哪一块，**不猜**")
            continue
        targets.append((r, hits[0]))
    if skipped:
        warns.append(f"⚠ 有 {len(skipped)} 行贴不了（不猜）：" + "；".join(skipped[:8]))
    if none_skipped:
        warns.append(f"✅ 有 {len(none_skipped)} 行按清单记的是**无**（{'、'.join(none_skipped[:8])}）"
                     "—— 那是**别人建好的资产**、没有材质 / 实例，**有意跳过不贴**（不是失败）。")
    asset_rows = sum(1 for r in rows if str(r.get("kind") or "") == "asset")
    if not targets:
        raise ToolError("按这份清单**一行都贴不了**（见 warnings）—— **一个组件都没写**。")

    # ---- 实例路径逐个 exists()：不过就拒收（整批，一个组件都不写）--------------------
    dead: list[str] = []
    for _r, m in targets:
        inst = surface_instance(m, _recipes)
        calls += 1
        try:
            ok = await asset_exists(repo, inst)
        except ToolError as exc:
            dead.append(f"{inst}（调用失败：{exc}）")
            continue
        if not ok:
            hint = ("" if instance_of(m) else
                    "（这一行清单里只有母材质 —— 这是它**要派生**的实例，"
                    "先跑 `create_surfaces()`）")
            dead.append(f"{inst}{hint}")
    if dead:
        raise ToolError("拒收：材质实例路径验不过（官方 `exists()` 返回否）：" + "、".join(dead)
                        + " —— **一个组件都没写**。")

    # ---- 逐行贴 ------------------------------------------------------------------
    out: list[SurfaceApplyRow] = []
    applied = already = failed = 0
    touched: list[str] = []
    for r, m in targets:
        inst = surface_instance(m, _recipes)
        actor = str(r.get("actor") or "")
        res = SurfaceApplyRow(label=str(r.get("label") or ""), element_key=str(r.get("element_key") or ""),
                              instance=inst, actor=actor)
        if not actor:
            res.status, res.error = "failed", "台账这一行没有 Actor 引用（上次没落成 / 引用已失效）"
            out.append(res)
            failed += 1
            continue
        try:
            calls += 1
            comps = await components_of(repo, actor)
        except ToolError as exc:
            res.status, res.error = "failed", f"读组件失败：{exc}"
            out.append(res)
            failed += 1
            continue
        cube = next((c for c in comps if c.rsplit(".", 1)[-1] == CUBE_COMPONENT_NAME), "")
        if not cube:
            res.status, res.error = "failed", (f"这个 Actor 上找不到名为 `{CUBE_COMPONENT_NAME}` 的组件"
                                               f"（现有组件：{comps}）")
            out.append(res)
            failed += 1
            continue
        calls += 1
        cur = await get_component_props(repo, cube, ["overrideMaterials"])
        if override_first(cur) == inst:
            res.status = "already"
            out.append(res)
            already += 1
            touched.append(actor)
            continue
        calls += 1
        await repo.official(
            "set_properties",
            {"instance": {"refPath": cube},
             "values": json.dumps({"overrideMaterials": [{"refPath": repo.to_object_path(inst)}]},
                                  ensure_ascii=False)},
            toolset=TS_OBJECT)
        calls += 1
        back = await get_component_props(repo, cube, ["overrideMaterials"])
        if override_first(back) == inst:
            res.status = "applied"
            applied += 1
        else:
            res.status = "failed"
            res.error = f"写了但**读回不是它**（读回 {back!r}）—— 如实报，不当作成功"
            failed += 1
        touched.append(actor)
        out.append(res)

    # ---- 聚焦（第 ⑥ 步「切到那个目标物体视角」）--------------------------------------
    # ⚠ 2026-10-07：那一段原本内联在这里，现在抽成 `focus_actors()` ——
    #   第 ⑨ 步 `tune` 也要它（用户实测反馈：「调成蓝色时没有视角锁定到跑车，我根本察觉不到」），
    #   而"怎么切视角"只许有**一处**判据（官方两件调用 + PIE 那条边界 + focus_max 的取舍）。
    focused: list[str] = []
    if focus and touched and applied + already:
        focused, used_f, warn_f = await focus_actors(repo, touched, focus_max)
        calls += used_f
        if warn_f:
            warns.append(warn_f)
    elif focus:
        warns.append("⚠ 传了 `focus=True`，但这次没有一行真贴上 / 本来就对 —— 没什么可聚焦的。")

    # ---- 第 ⑦ 步：用户满意才回写清单 + 存盘 ------------------------------------------
    wrote_back = ""
    saved = "off"
    if save:
        # ① 先回写清单（把这次真贴上去的实例路径写成定稿）
        try:
            payload = []
            for r in manifest_rows(repo):
                rr = dict(r)
                key = str(rr.get("element_key") or "").strip().lower()
                hit = next((m for _row, m in targets if str(m.get("element_key") or "").lower() == key), None)
                if hit is not None:
                    # ⚠ 这里必须用 `surface_instance()`（**不是** `hit["instance"]`）：
                    #   `from_material` 行的 `instance` 本来就是空的，直接抄它会把清单写成
                    #   「`instance_status=is_instance` 而 `instance` 是空串」—— 自相矛盾的定稿。
                    #   第 ⑤ 步派生的那个路径是可复算的，写回的就是它。
                    inst = surface_instance(hit, _recipes)
                    if not inst:
                        warns.append(f"⚠ 「{rr.get('label') or key}」这一行算不出实例路径 —— "
                                     "回写时**没动它**（清单里那一行还是母材质）。")
                    else:
                        rr["instance"] = inst
                        rr["instance_status"] = INST_IS_INSTANCE
                        rr["parent"] = (pkg_path(hit.get("parent")) or pkg_path(hit.get("material"))
                                        or pkg_path(rr.get("parent")))
                        if not pkg_path(rr.get("material")):
                            rr["material"] = pkg_path(hit.get("material")) or rr["parent"]
                        rr["status"] = MAT_FOUND
                payload.append(rr)
            # ⚠ **不传 `deliverable`** ⇒ `save_manifest()` 会**继承**旧清单那份（用户点头看过的
            #   正文 = 历史记录）+ 同样继承 `confirmed_by_quote` / `signed_at`（2026-10-07 修）。
            #   两者与 `items` 的"新鲜度"不同，所以留一句说明，免得读的人以为正文也是活的。
            save_manifest(repo, payload, user_quote, extra={
                "written_back_at": repo.now(),
                "deliverable_note": ("`deliverable` 是**签字当时**渲染的正文"
                                     "（历史记录，**不会**随回写更新）；"
                                     "`items` 才是回写后的**活状态**。"),
            })
            wrote_back = str(MATERIAL_LIST_PATH)
        except OSError as exc:
            warns.append(f"⚠ 清单回写失败（{exc}）—— 关卡里的材质已经贴上了，但**清单没更新**。")
        # ② 再存盘（空列表 = 存所有脏资产：关卡 + 新建的材质 / 实例）
        try:
            calls += 1
            ok = await repo.official("save_assets", {"asset_paths": []}, toolset=TS_ASSET)
            saved = "saved" if ok else "failed"
        except ToolError as exc:
            saved = "failed"
            warns.append(f"⚠ **存盘失败**（{exc}）—— 但这**不算材质没贴好**（材质已经写进关卡了）；"
                         "你自己在 UE 里按 Ctrl+S。")
    else:
        warns.append("⚠ **没存盘**（`save=False`）—— 第 ⑦ 步「用户满意就保存」：满意之后带 "
                     "`save=True` 重跑一次，或你自己在 UE 里按 Ctrl+S。"
                     "⚠ 新建的材质 / 实例现在只在内存里，切关卡就没了。")

    if focused:
        warns.append("✅ 视口已聚焦并选中目标 —— 请在 UE 里看贴得对不对（**本阶段不出图**）。")
    return SurfaceReport(
        stage="阶段五 · 第 ⑥⑧ 步（按材质清单贴实例）", mode="apply", level=repo.level,
        planned=len(targets), applied=applied, already=already, failed=failed,
        asset_rows_untouched=asset_rows, rows=out, focused=focused, saved=saved,
        wrote_back=wrote_back, official_calls=calls,
        material_list_path=str(MATERIAL_LIST_PATH),
        next_step=("贴完：" + str(applied) + " 行新贴、" + str(already) + " 行本来就对、"
                   + str(failed) + " 行没成。看满意了 ⇒ `apply_surfaces(save=True)` 落盘；"
                   "还要调参数 ⇒ 第 ⑨ 步 `create_surfaces(mat_mode=\"tune\", tune={…})`。"),
        warnings=warns)


# ══════════════════════════════════════════════════════════════════════════════
# 十一、第 ⑨ 步：tune（只调清单里的实例 —— 硬拦）
# ══════════════════════════════════════════════════════════════════════════════

async def focus_actors(
    repo: SurfacesRepo, refs: list[str], focus_max: int = 3,
) -> tuple[list[str], int, str]:
    """**切到目标物体的视角 + 选中高亮**（「用户得看得见改了哪儿」）—— 全工程**唯一一份**。

    用官方两件（2026-10-04 实测 `describe_toolset`）：`EditorAppToolset.FocusOnActors`
    （官方原文就是"重新定位关卡编辑器摄像机以聚焦于指定 Actor"——**就是双击那个动作本身**）
    + `SelectActors`（选中高亮）。返回 `(聚焦过谁, 用掉几次官方调用, 警告)`。

    ⚠ **为什么它必须存在**（2026-10-07 用户实测反馈，原话）：
      「你在调节为蓝色时**没有视角锁定到跑车**，我根本察觉不到你改了它，你说变成了蓝色我才去找到它」
      —— 改完 / 调完**不切视角** = 用户得自己满关卡找那个东西，等于**没验收**。
    ⚠ 官方 `FocusOnActors` **PIE 激活时不能调**（官方原文）⇒ 失败**如实报**，不假装切过。
    ⚠ `focus_max`（默认 3）：一次聚焦几十个的话用户**只看得见最后一个** —— 那等于没切。
    """
    if not refs or focus_max <= 0:
        return [], 0, ""
    batch = [{"refPath": a} for a in refs][:focus_max]
    calls = 0
    try:
        calls += 1
        await repo.official("FocusOnActors", {"actors": batch}, toolset=TS_APP)
        calls += 1
        await repo.official("SelectActors", {"actors": batch}, toolset=TS_APP)
    except ToolError as exc:
        return [], calls, (f"⚠ 视口聚焦没成（{exc}）—— 官方 `FocusOnActors` 在 **PIE 激活时不能调**。"
                           "你自己在 UE 里找那几处看一眼。")
    return refs[:focus_max], calls, ""


async def tune(repo: SurfacesRepo, tune_map: dict | None, focus: bool = False,
               focus_max: int = 3, ledger: dict | None = None) -> CreateReport:
    """**第 ⑨ 步**：对**清单里的实例**读-改-写参数。

    闸门（任一条不过 ⇒ **一个字节都不写**）：
      ① **目标必须是"清单里某一行的靶子"**（用户 2026-10-04 定的**硬拦**）—— 不在就拒收，
         并列出清单里可调的有哪几块；
         ⚠ 靶子 = **`surface_instance()`**（"有材质缺实例"的行算的是它**要派生的那个实例**）——
           不然会死锁：第 ⑤ 步刚派生的实例，清单要等第 ⑦ 步"用户满意"才写回，
           而第 ⑥ 步"反复调"就要调它 ⇒ 用 `instance` 当判据的话，**派生出来的东西永远调不了**。
           ⚠ 这**没有放松**硬拦：共享母料（如 `MI_sjfnch0a`）**不是**任何一行的靶子
           （它那一行的靶子是 `MI_sjfnch0a_1`）⇒ 手一滑改母料照样被拒。
      ② 参数名**只认**官方 `list_parameters` 的返回；
      ③ 值类型必须与官方报的类型对得上；
      ④ 先读现值 → 同值跳过（幂等）→ 写 → **读回核对**；台账记**旧值**（回滚依据）。

    ⚠ **`focus=True`（2026-10-07 加）**：调完**切到那些物体的视角并选中高亮** ——
      判据是「**用户得看得见改了哪儿**」（见 `focus_actors()` 的 docstring，那是用户实测反馈）。
      Actor 从**搭建台账**（`ledger`，走 `active_ledger_path()`）按 `element_key` 取，
      **不按名字猜**；台账没有 / 那一行没有 Actor 引用 ⇒ **如实报"聚焦不了"**，不静默跳过。
    """
    warns: list[str] = []
    calls = 0
    bad: list[str] = []
    if not tune_map:
        raise ToolError("`tune` 是空的 —— 没有要调的参数（**一个字节都没写**）。")

    allowed: dict[str, dict] = {}
    # ⚠ 配方表读一次、共用（2026-10-07）：靶子判据与第 ⑤⑥ 步**同源**（`derived_instance_of(row, recipes)`）——
    #   不然"第 ⑤ 步建在哪"与"这里认为该调哪块"会分叉（配方指定路径那一类就调不着了）。
    _recipes = create_recipes(repo)[0]
    for r in manifest_rows(repo):
        inst = surface_instance(r, _recipes)   # 见闸门 ① 的 ⚠：靶子判据只有这一处
        if inst:
            allowed[inst] = r
    if not allowed:
        raise ToolError(
            f"材质清单 `{MATERIAL_LIST_PATH.name}` 里一个可调的实例都没有 —— "
            "**一个字节都没写**。先 `probe` → `confirm`（有材质没实例的再 `create_surfaces()`）。")

    # ---- ① 硬拦：目标必须是我们清单里的实例 ------------------------------------------
    for raw_path in (tune_map or {}).keys():
        target = pkg_path(raw_path)
        if target not in allowed:
            raise ToolError(
                f"拒收：`{target}` **不在材质清单里** —— 这一档只调**清单里的材质实例**"
                "（用户 2026-10-04 定的硬闸：手一滑改到共享母料/别人的资产，是上一个会话真发生过的"
                "事故）。\n"
                f"· 清单里可调的实例（{len(allowed)} 块）："
                + "、".join(sorted(allowed)[:8]) + ("…" if len(allowed) > 8 else "") + "\n"
                "· 如果这块**确实该调**：先让 `probe`/`confirm` 把它收进清单（或先 `create_surfaces()` "
                "派生一块我们自己的实例），再来调它。")

    # ---- ② 参数名 / 类型 / 值 逐个校验（整批不过 ⇒ 一个都不写）------------------------
    planned: list[dict] = []
    for raw_path, params in (tune_map or {}).items():
        target = pkg_path(raw_path)
        if not isinstance(params, dict) or not params:
            bad.append(f"`{target}`：值要是 `{{参数名: 值}}`（且不许为空）")
            continue
        calls += 1
        try:
            known = {p["name"]: p["type"] for p in await list_parameters(repo, target)}
        except ToolError as exc:
            bad.append(f"`{target}`：`list_parameters` 失败（{exc}）—— **参数名没法核对，拒收**")
            continue
        if not known:
            bad.append(f"`{target}`：`list_parameters` **一个参数都没返回** —— 不猜，拒收")
            continue
        for pname, value in params.items():
            name = str(pname).strip()
            if name not in known:
                bad.append(f"`{target}`：参数 `{name}` **不在**官方返回里"
                           f"（返回的是：{'、'.join(sorted(known))}）—— 参数名只认官方返回")
                continue
            kind, why = _kind_of(value)
            if why:
                bad.append(f"`{target}` 的 `{name}`：{why}")
                continue
            t = known.get(name, "")
            # ⚠ "官方报的类型是哪一类"**只有一处判据**（`param_kind()`）—— 这里原来另有一份
            #   `expect` 表各写一遍，两处一旦不一致就会出现"提升认得出、调参认不出"这种怪事。
            if t and param_kind(t) != kind:
                bad.append(f"`{target}` 的 `{name}`：值的形状像 **{kind}**，"
                           f"而官方报的类型是 `{t}` —— 对不上就拒收（不猜）")
                continue
            if kind == "texture":
                calls += 1
                try:
                    ok = await asset_exists(repo, value)
                except ToolError as exc:
                    bad.append(f"`{target}` 的 `{name}`：纹理路径验活失败（{exc}）")
                    continue
                if not ok:
                    bad.append(f"`{target}` 的 `{name}`：纹理 `{value}` **不存在**")
                    continue
            planned.append({"material": target, "parameter": name, "kind": kind, "wanted": value})
    if bad:
        raise ToolError("`tune` **拒收** —— **一个参数都没写**：\n  - " + "\n  - ".join(bad))

    # ---- ③ 逐条串行：读现值 → 幂等 → 写 → 读回 --------------------------------------
    getters = {"scalar": "get_scalar_parameter", "vector": "get_vector_parameter",
               "texture": "get_texture_parameter", "static_switch": "get_static_switch_parameter"}
    setters = {"scalar": "set_scalar_parameter", "vector": "set_vector_parameter",
               "texture": "set_texture_parameter", "static_switch": "set_static_switch_parameter"}
    results: list[dict] = []
    tuned = already = failed = 0
    for p in planned:
        target, name, kind, wanted = p["material"], p["parameter"], p["kind"], p["wanted"]
        rec = {"material": target, "parameter": name, "kind": kind,
               "wanted": value_text(wanted), "old": "", "read_back": "", "status": "", "error": ""}
        try:
            calls += 1
            old = await repo.official(getters[kind],
                                      {"instance": {"refPath": repo.to_object_path(target)},
                                       "name": name}, toolset=TS_MATINST)
            rec["old"] = value_text(old)
        except ToolError as exc:
            rec.update(status="failed", error=f"读现值失败：{exc}")
            results.append(rec)
            failed += 1
            continue
        # ⚠ **2026-10-04 修（阻断级）**：这里原来写的是 `(wanted == "true")` —— 而 `_kind_of()`
        #   只把 **bool** 判成 static_switch ⇒ 值本来是 `True` 时 `True == "true"` **恒为 False**
        #   ⇒ **恒写 False**（正好写反），而读回布尔 `False` 时 `value_same()` 成立 ⇒ 报文
        #   还报 `tuned`（**把写反的值报成"改成了"**）。现在：bool 直接用；字符串按
        #   `"true" / "false"` 认（`_kind_of()` 上面那条也一并认它）。
        if kind == "texture":
            new_val: Any = {"refPath": repo.to_object_path(wanted)}
        elif kind == "static_switch":
            new_val = (wanted if isinstance(wanted, bool)
                       else str(wanted).strip().lower() == "true")
        else:
            # ⚠ **向量走形状换算**（2026-10-07 修「向量参数写不进去」的根因 A）：
            #   4 元数组直接给官方会**静默失败**（不报错、读回还是旧值）—— 标量不受影响。
            #   幂等比较（`value_same`）与读回核对都走 `value_text()`，它把两种形状
            #   **规整成同一段文本** ⇒ 换形状不会把"已经是对的"判成"要改"。
            new_val = linear_color_value(wanted)
        if value_same(old, new_val):
            rec["status"] = "already"
            results.append(rec)
            already += 1
            continue
        try:
            calls += 1
            await repo.official(setters[kind],
                                {"instance": {"refPath": repo.to_object_path(target)},
                                 "name": name, "value": new_val}, toolset=TS_MATINST)
        except ToolError as exc:
            rec.update(status="failed", error=f"写入失败：{exc}")
            results.append(rec)
            failed += 1
            continue
        calls += 1
        try:
            back = await repo.official(getters[kind],
                                       {"instance": {"refPath": repo.to_object_path(target)},
                                        "name": name}, toolset=TS_MATINST)
            rec["read_back"] = value_text(back)
            if value_same(back, new_val):
                rec["status"] = "tuned"
                tuned += 1
            else:
                rec.update(status="failed",
                           error=f"写了但**读回不是它**（读回 {rec['read_back'] or '空'}）—— 如实报，不当作成功")
                failed += 1
        except ToolError as exc:
            rec.update(status="failed",
                       error=f"写进去了，但**读回核对失败**（{exc}）—— 不许把「没读到」说成「没问题」")
            failed += 1
        results.append(rec)

    if any(r["kind"] == "static_switch" for r in results):
        warns.append("⚠ 这批里有 **StaticSwitch** —— 官方原文说它会**触发 shader 重编译**；"
                     "本工具是**逐条串行**写的，没有并发。")
    if tuned:
        try:
            ledger_merge(repo, "tuned", [r for r in results if r["status"] == "tuned"])
        except OSError as exc:
            warns.append(f"⚠ 台账没写成（{exc}）—— 这次**没留下旧值**，回滚时得手工查。")
        warns.append("⚠ 本工具**不自动存盘**：要留住参数，请在第 ⑦ 步存盘"
                     "（`apply_surfaces(save=True)` 或你在 UE 里 Ctrl+S）。")

    # ---- 聚焦（第 ⑨ 步「切到那个物体视角」—— 用户 2026-10-07 实测要求）------------------
    # 判据：**用户得看得见改了哪儿**（`focus_actors()` 的 docstring 里有用户原话）。
    # Actor 从**搭建台账**（`active_ledger_path()`，本函数经 `ledger` 形参拿到）按
    # `element_key` 取 —— **不按名字猜**（与第 ⑥ 步同一条纪律）。
    focused: list[str] = []
    if focus:
        changed = sorted({str(r.get("material") or "") for r in results
                          if r.get("status") in ("tuned", "already")})
        keys = {str((allowed.get(t) or {}).get("element_key") or "").strip().lower()
                for t in changed} - {""}
        rows = [r for r in ((ledger or {}).get("rows") or []) if isinstance(r, dict)]
        led_level = str((ledger or {}).get("level") or "")
        if not rows:
            warns.append("⚠ 传了 `focus=True`，但**没有搭建台账** —— 不知道该去关卡里哪个 Actor 上"
                         "（`active_ledger_path()`；场景已经搭好就先 "
                         "`execute_build(mode=\"incremental\", adopt=true)` 认领现状）"
                         "⇒ **这次没切视角**，你自己在 UE 里找那几处。")
        elif led_level and repo.level and led_level != repo.level:
            warns.append(f"⚠ 台账记的关卡是 `{led_level}`、现在是 `{repo.level}` —— "
                         "Actor 引用在这儿没有意义 ⇒ **这次没切视角**（不跳到别的图上去）。")
        else:
            refs = [str(r.get("actor") or "") for r in rows
                    if str(r.get("element_key") or "").strip().lower() in keys]
            refs = [a for a in refs if a]
            if not refs:
                warns.append("⚠ 传了 `focus=True`，但这次调的实例**在台账里找不到对应的行**"
                             f"（元素：{'、'.join(sorted(keys)) or '—'}）⇒ **这次没切视角**。")
            else:
                focused, used_f, warn_f = await focus_actors(repo, refs, focus_max)
                calls += used_f
                if warn_f:
                    warns.append(warn_f)
                elif focused:
                    warns.append(f"✅ 视口已聚焦并选中 {len(focused)} 个目标 —— "
                                 "去 UE 里看这一处改得对不对（**本阶段不出图**）。")
    return CreateReport(
        stage="阶段五 · 第 ⑨ 步（调清单里那些实例的参数）", mode="tune",
        target="、".join(sorted({p["material"] for p in planned})),
        planned=len(results), created=0, tuned=tuned, already=already, failed=failed,
        tune_rows=results, official_calls=calls,
        # ⚠ **`focused` 必须传出去**（2026-10-07 预检 ㉑ 抓到的真 bug）：模型里给了默认空表，
        #   这里漏传的话 —— **相机真切了、报文却说没切**（"切了但报没切"比不切更坏：
        #   调用方会以为没切、再想办法，而用户那边视角已经跳过去了）。
        focused=focused,
        ledger_path=str(SURFACE_LEDGER_PATH),
        next_step=("调完：" + str(tuned) + " 个改成、" + str(already) + " 个本来就一样、"
                   + str(failed) + " 个没成。**请在 UE 里看效果**；不满意就把值改掉重调 —— "
                   "**直到你说没有要调整的了**，阶段五就结束了。"),
        warnings=warns)


def _kind_of(value: Any) -> tuple[str, str]:
    """按**值的形状**定它是哪一类参数 → `(kind, 不合法原因)`（形状判据只有这一份）。"""
    if isinstance(value, bool):
        return "static_switch", ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("scalar", "") if _finite_number(value) is not None else ("", "数字不是有限值")
    if isinstance(value, (list, tuple)):
        if len(value) == 4:
            return "vector", ""
        return "", f"数组长度是 {len(value)}，向量参数要 4 个数（RGBA）"
    if isinstance(value, str):
        # ⚠ **2026-10-04 修**：`"true"` / `"false"` 是**静态开关**的字符串写法 —— 原来一律当
        #   texture，于是 `tune` 里 `{"Enable Material AO": "true"}` 会被"形状像 texture、
        #   官方报的类型是 static"那条类型闸**拒收**（`param_kind()` 判的正是 static）
        #   ⇒ **开关永远开不了**。只认这两个词，其余字符串照旧走 texture（纹理路径）。
        if value.strip().lower() in ("true", "false"):
            return "static_switch", ""
        return "texture", ""
    return "", f"值的类型不认识（{type(value).__name__}）"
