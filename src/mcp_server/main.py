# =============================================================================
# 本 server 是编排层，自己不碰 UE：
#   Agent(agent) → 本 server → 官方 Unreal MCP（HTTP 127.0.0.1:8000）→ UE 编辑器
#
# 阶段一流程由用户定义、不许改（原文见 docs/阶段一-资产确认.md）：
#   ① 根据上传图片提取所需资产 → 让用户确认
#   ② 寻找资产：名字对得上就是找到了
#   ③ 没找到 → 禁止寻找疑似的，直接告知用户缺少什么资产
#   ④ 要求用户把"存在但是 agent 没找到"的资产的【路径 + 名字】告诉 agent
#   ⑤ 让 agent 重命名
#   ⑥ 确认资产列表，并向用户确认 → 使用资产清单
#
# 为什么"禁止猜"是硬规则：我给"名字是否规范"做过两版判据，两版都在实测里误报
#   （v1 长度+字符集、v2 连续辅音 ≥4）。根因是没有词典时，随机哈希和英文单词拼接
#   在字符层面无法区分。既然猜不准，就不猜 —— 用户指路，模型改名。
#
# 为什么必须支持"按文件夹名找"：官方 find_assets 的名字过滤只匹配资产名、
#   不匹配文件夹名（实测），哈希名资产（MI_sjfnch0a）靠资产名永远搜不到。
# =============================================================================

import base64
import hashlib
import json
import logging
import math
import os
import re
import shutil
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

# --- 数据模型（工具返回结构）在 schemas.py -------------------------------------  【模块：schemas】
# 为什么是"先相对、失败再顶层"两段式（而不像规划层那样惰性导入）：
#   ① 这些类是 `@mcp.tool()` 的**返回标注** —— 装饰器在**定义时**就求值，所以必须在
#      模块顶层就拿到它们，不能等工具被调用时再导；
#   ② Agent按**脚本方式**起本文件（python ...\src\mcp_server\main.py），此时
#      `__package__` 为空，`from .schemas import ...` 会抛 "attempted relative import
#      with no known parent package"（2026-09-23 血账，规划层就是这么炸过一次）
#      → 兜底：自己把本文件所在目录放进 sys.path，再按顶层模块导入。
try:
    from . import schemas as _schemas
except ImportError as _exc_rel:                      # 脚本方式：__package__ 为空
    _here = str(Path(__file__).resolve().parent)
    if _here not in sys.path:
        sys.path.insert(0, _here)
    try:
        import schemas as _schemas
    except ImportError as _exc_top:
        # 两个错误都带上：只报第一个会让人查错方向（与 _planning_modules() 同一套口径）
        raise ImportError(
            "数据模型层（schemas.py）导入失败："
            f"相对导入 → {_exc_rel}；顶层导入 → {_exc_top}"
        ) from _exc_top

# ⚠ 逐个**显式再导出**（不用 `import *`）：名字在这里看得见、能 grep、IDE 能跳转。
#   schemas.py 里加了模型却漏了这里一行 → 启动时立刻 NameError（fail-fast），不会静默。
OUR_FOLDER_ROOT = _schemas.OUR_FOLDER_ROOT

OfficialStatus = _schemas.OfficialStatus
BuildTargetCheck = _schemas.BuildTargetCheck
BuildOrderRow = _schemas.BuildOrderRow
BuildOrdersResult = _schemas.BuildOrdersResult
BuildRowResult = _schemas.BuildRowResult
BuildReport = _schemas.BuildReport
ElementItem = _schemas.ElementItem
ElementList = _schemas.ElementList
FoundAsset = _schemas.FoundAsset
ElementPlan = _schemas.ElementPlan
PlanReport = _schemas.PlanReport
RenameItem = _schemas.RenameItem
RenameResult = _schemas.RenameResult
RenameReport = _schemas.RenameReport
AssetListItem = _schemas.AssetListItem
UnresolvedElement = _schemas.UnresolvedElement
AssetListDelivery = _schemas.AssetListDelivery
AssetListRow = _schemas.AssetListRow
PathCheck = _schemas.PathCheck
AssetListStatus = _schemas.AssetListStatus
PlanGate = _schemas.PlanGate
PlanSummary = _schemas.PlanSummary
PlanAcceptance = _schemas.PlanAcceptance
PlanResult = _schemas.PlanResult
PlanStatus = _schemas.PlanStatus
SurfaceRowResult = _schemas.SurfaceRowResult
SurfaceReport = _schemas.SurfaceReport
ExchangeReport = _schemas.ExchangeReport
ExchangeDiffReport = _schemas.ExchangeDiffReport
EnvRowResult = _schemas.EnvRowResult
EnvReport = _schemas.EnvReport
PreviewShot = _schemas.PreviewShot
PreviewShotResult = _schemas.PreviewShotResult
PreviewReport = _schemas.PreviewReport
EvaluateReport = _schemas.EvaluateReport
LayoutRowVerdict = _schemas.LayoutRowVerdict

# 阶段二·规划层不能在文件顶部 import —— 见 _planning_modules() 里的血账说明：
#   Agent是按脚本方式起本文件（python ...\src\mcp_server\main.py），此时相对导入
#   `from .planning import ...` 会抛 "attempted relative import with no known parent package"。

# --- 常量 ---------------------------------------------------------------------  【模块：state（跨模块口径）】

OFFICIAL_URL = "http://127.0.0.1:8000/mcp"


# --- 让 httpx 别拿系统代理去连本地 UE（2026-09-30 加 · 用户授权）--------------------  【模块：ue_adapter】
# 症状：httpx 报 `HTTP/1.1 502 Bad Gateway`，而 curl 直连同一个 URL 拿到 200。
# 根因（2026-09-30 一天内复现 3 次，每次白花一轮）：`httpx2/_utils.py:33-37` 调
#   `urllib.request.getproxies()`，它在 Windows 上**回落到注册表**读 WinINET 代理
#   （本机 `ProxyEnable=1 / ProxyServer=127.0.0.1:7897`）；而 CPython 的
#   `getproxies_registry()` **只返回 http/https/ftp、压根不读 `ProxyOverride`** ——
#   Windows 那份「127.* 不走代理」的绕过名单被**整个丢掉** → 连 `127.0.0.1:8000`
#   都被塞给 clash → clash 代理不了回环 → 502。
# 修法：给**本进程**塞 `no_proxy`。`getproxies()` 是**短路或**
#   （`getproxies_environment() or getproxies_registry()`）—— 环境里只要有**任意**
#   一个非空的 `*_proxy` 变量，注册表那一支就**整个不读了** → 全走直连。
# ⚠ 只影响**本 server 进程**：不动系统代理、不动别的程序；已有的 `no_proxy` 是**合并**、不覆盖。
def _install_no_proxy_for_local_ue() -> None:
    """把 `127.0.0.1` / `localhost` 并进本进程的 `no_proxy`（给 httpx 看）。"""
    have = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    parts = [p.strip() for p in have.split(",") if p.strip()]
    for host in ("127.0.0.1", "localhost"):
        if host not in parts:
            parts.append(host)
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = ",".join(parts)


_install_no_proxy_for_local_ue()

# 状态文件目录：项目根目录下的 catalog/（parents[0]=mcp_server, [1]=src, [2]=项目根）
STATE_DIR = Path(__file__).resolve().parents[2] / "catalog"
ELEMENTS_PATH = STATE_DIR / "elements.json"       # 用户确认过的元素清单
ASSET_LIST_PATH = STATE_DIR / "asset_list.json"   # 使用给用户的资产清单
LIBRARY_PATH = STATE_DIR / "library_snapshot.json"  # 资产库指纹（给清单"签字"用）

# 官方工具的"地址" = 工具集名 + 工具名
TS_ASSET = "editor_toolset.toolsets.asset.AssetTools"    # 找资产 / 改名 / 验证存在
TS_SCENE = "editor_toolset.toolsets.scene.SceneTools"    # 关卡信息 / 摆 Actor / outliner
TS_ACTOR = "editor_toolset.toolsets.actor.ActorTools"    # 读回变换 / 改标签 / 读组件
TS_PRIM = "editor_toolset.toolsets.primitive.PrimitiveTools"  # 白膜加图元（add_cube）
TS_OBJECT = "editor_toolset.toolsets.object.ObjectTools"  # 读对象属性（白膜尺寸在组件上）

# 资产类别 → 官方的类路径（find_assets 的 asset_type 要的是类路径）
CLASS_PRESETS: dict[str, str] = {
    "mesh": "/Script/Engine.StaticMesh",
    "material": "/Script/Engine.Material",
    "material_instance": "/Script/Engine.MaterialInstanceConstant",
    "texture": "/Script/Engine.Texture2D",
}
DEFAULT_ASSET_TYPES = ["mesh", "material_instance", "material"]
SIZE_QUERY_TYPES = {"mesh"}

# 关卡资产的类路径（find_assets 的 asset_type 要的是类路径）—— 用来列"项目里有哪些图"
LEVEL_CLASS_PATH = "/Script/Engine.World"

# ⚠ `OUR_FOLDER_ROOT` 现在**不在本文件定义** —— 它在 `schemas.py`（下面几个模型的描述里
#   要用它，f-string 在**定义时**求值）。本文件顶部已经把它再导出，所以下面照旧可用。
#   定义处：schemas.py 顶部的 `OUR_FOLDER_ROOT = "UEMCP"`。

# 阶段三 · 搭建指令表的落盘位置（给人看 / 给下一次执行读）
VIEWS_DIR = STATE_DIR.parent / "views"
BUILD_ORDERS_PATH = VIEWS_DIR / "build_orders_v1.json"

# 阶段三 · **搭建台账**（2026-09-26 增量改造新增）—— 记"上次到底搭了什么"。
# ⚠ 这是我们自己的 JSON（与 catalog/ 同类），**不是 UE 资产、不是关卡存盘** —— 不违反硬规矩。
# 为什么必须有它：没有台账，"增量"就无从判断关卡里哪个 Actor 对应指令表哪一行，
# 只能全量重摆（把用户根本没动过的东西也删一遍再摆一遍）。
BUILD_STATE_PATH = VIEWS_DIR / "build_state_v1.json"

# 阶段三 · **「搭哪张图」的答复台账**（2026-09-26 加）—— `check_build_target()` 把用户的答复记在这儿，
# `execute_build()` 落关卡前拿它**核对当前关卡**。
# 为什么必须有它（把软约束变硬）：`check_build_target` 是阶段三第 1 步，可代码里**从来没核对过
#   它跑没跑** —— 「拿到用户答复前一个 Actor 都不许放」只写在工具描述与 docs 里。
#   没读参数说明的 agent 会直接 `execute_build`，于是可能搭在错的图上（客户问过"你搭哪张图了"）。
BUILD_TARGET_PATH = VIEWS_DIR / "build_target_v1.json"

# 阶段三 · **「人工痕迹」的问答台账**（2026-09-30 加，用户要求做成**两步闸**）——
# 记「问过哪几行人工痕迹」+ 用户怎么答的，`execute_build()` 覆盖前拿它核对。
# 为什么必须有它（**同类事故第二次**）：外部 agent 收到"有 N 处人工痕迹会被删"的拒收后，
#   **自己**传了 `accept_user_edits=true` 就把用户手拖过的水面覆盖了（差 30 cm，全程没问人）；
#   加了 `user_quote` 之后它至少得拿出一句话，但那仍然是"一次调用里说完的事"。
#   现在拆成两步（与 `check_build_target` 同构）：① 第一次调（不带覆盖开关）→ 代码**记下
#   "问的是哪几行"**并拒收；② 拿到用户答复后再调 → 核对「问过的那批 == 现在这批」+ 有他本人的原话
#   + 距提问已过 `MIN_USER_EDITS_ANSWER_DELAY_S` 秒（防"问完立刻自己答复"）。
# ⚠ 这是我们自己的 JSON（与 `catalog/` 同类），**不是 UE 资产、不是关卡存盘**；也**不进几何指纹**。
USER_EDITS_PATH = VIEWS_DIR / "user_edits_v1.json"

# 阶段七 · **落位对账报告**的落盘位置（2026-09-30）。
# ⚠ 这是我们自己的 JSON（与 catalog/ 同类）——**不是 UE 资产、不是关卡存盘**，不违反硬规矩；
#   它**不进几何指纹**（写报告不该让"上一次确认的 plan"作废），也**不改 plan 一个字**。
# 为什么值得留档：对账结果是"这一版到底搭成什么样"的证据，与搭建台账同源、互为佐证。
EVALUATE_PATH = VIEWS_DIR / "evaluate_v1.json"

MIN_CONFIRM_DELAY_S = 15.0
"""确认前，那张图至少要"出炉"多少秒 —— **防机器人式秒确认**（2026-09-26 加）。

依据：**图是 agent 自己画的、确认也是它自己调的** —— 画完立刻 `confirm_plan`，只可能有一个意思：
它压根没把图给用户看（实测被这么绕过一次：客户原话「你摆你牛魔呢，图都不先让我确认就摆」）。
人看一眼平面图不可能 15 秒内完成，我们自己在图上核对一遍要几分钟。
⚠ 这是**可调的口径**（觉得太严/太松就改这个数），不是实测出来的物理常量。"""

MIN_USER_EDITS_ANSWER_DELAY_S = 15.0
"""「人工痕迹」两步闸里，**从提问到答复至少要隔多少秒**（2026-09-30 加）。

为什么（与上面那条同一条纪律）：把"有 N 处人工痕迹会被删"这份清单写出来、交给用户、
等他打字回话，**不可能 15 秒内完成** —— 问完立刻带着"原话"回来，只可能是自己编的。
⚠ 同样是**可调的口径**，不是实测常量。⚠ 它拦不住"存心等够时间再编"（代码验不了真话），
但配合台账里留下的**问过哪几行 + 那句话原文**，事后可以当场对质。"""

# --- 阶段三 · 落位口径（**plan 里没有 Z** —— 竖直方向只能在这里推，依据照实写出来）----  【模块：build】
# 为什么口径写在代码里而不是文档里：文档会腐烂，代码里的常量改不动就摆不出来。
# 下面每一条都标了依据；**没有依据的那就是不变量级的口径决定，不是实测**。

ROAD_SURFACE_Z_M = 0.0
"""车行道**路面**标高（米）= 0。

依据：`views/plan_v1.json` 的白膜行里，车行道那条的 note 写「路面标高」——
也就是说**路面就是 z=0 这个基准面**，其余东西相对它定高低。"""

GROUND_Z_M = 0.15
"""两侧地面（人行道 / 草坪的**顶面**）比路面高 **15 cm** —— 也就是路缘石的高度。

依据：plan 的人行道 / 连接路白膜行自带 `height_m = 0.15`（cube 厚 15 cm），
且同一条 note 写明「y-10～-8.5。cube 高 15 cm」—— 15 cm 就是这个高差。"""

WHITEBOX_VERTICAL: dict[str, tuple[str, float]] = {
    "ground":   ("top", GROUND_Z_M),           # 世界地基：**顶面** = 两侧地面（整块世界垫在最下面）
    "road":     ("top", ROAD_SURFACE_Z_M),     # 路面本身：**顶面**压在路面标高上
    "sidewalk": ("bottom", ROAD_SURFACE_Z_M),  # 人行道：底面落路面，厚 15 cm → 顶面 = 地面
    "path":     ("bottom", ROAD_SURFACE_Z_M),  # 连接路：同上（也是 15 cm 厚）
    "grass":    ("top", GROUND_Z_M),           # 草坪：**顶面** = 两侧地面（薄板 5 cm）
    "shrub":    ("bottom", GROUND_Z_M),        # 灌木：站在 15 cm 高的地面上
}
"""白膜的竖直摆法：`(贴哪一头, 那一头的标高[米])`。

为什么要分「贴顶 / 贴底」：`PrimitiveTools.add_cube` 的方块**以 Actor 原点为中心**
（2026-09-26 实测，见 docs/阶段三 §11 第 4 条），所以拿到的是**中心点标高** ——
薄板（路面 / 草坪 / 地基）该贴顶面，有厚度的台（人行道 / 连接路）与立着的东西（灌木）该贴底面。
**表里没有的 element_key 按 `("bottom", GROUND_Z_M)`**（立在地面上），并在报文里点名。

⚠ **`ground`（世界地基 / 世界底板）的口径**（2026-09-27 用户指令加的那一层，依据如下、不是拍的）：
  它**顶面 = `GROUND_Z_M`（15 cm）= 两侧地面的标高**，所以——
  · 房子底面 15 cm（`GROUND_Z_M`）→ 正好坐在地基顶面上；
  · 草坪薄板顶面 15 cm → 与地基顶面齐平（草坪就是铺在它上面的一层皮）；
  · 车行道顶面 0、厚 15 cm → 占 −15～0，**正好嵌在地基里**（"车行道比两侧地面低 15 cm" 本就是
    `GROUND_Z_M` 的定义：路缘石高度）；
  · 地块尺寸由 plan 的 `footprint_m` 给（应 ≈ 整个世界大小），**厚度由 plan 的 `height_m` 给**；
    建议 20 cm（`z = 15 − 20/2 = 5 cm`，方块占 −5～15 cm）。⚠ 20 cm 是**口径值、不是实测**。
  ⚠ plan 里**没有** `ground` 这一行时，这一层就是空的 —— 不会凭空生出一块底板。"""

DEFAULT_SLAB_THICKNESS_CM = 15.0
"""`plane` 类白膜压成薄板时的**兜底**厚度（厘米）—— 只在阶段一清单里找不到该元素的
`size_cm[2]` 时才用。正常路径是取清单实测/登记的那个数（车行道 15、草坪 5）。"""

SURFACE_MATERIALS_PATH = Path(__file__).resolve().parents[2] / "config" / "surface_materials.json"
"""材质表的**配置文件**位置（与 `config/asset_categories.json` 同一个目录）。

⚠ 这张表**不再写死在代码里**（2026-09-29 用户要求）：它是**用户会反复改的内容**
   （2026-09-27 改了 `ground`、2026-09-29 加了 `shrub`），写死意味着每换一次材质
   都要"改代码 + 预检 + 重启"。现在**每次调用都重新读它** → 改完立刻生效，不用重启。"""

SURFACE_MATERIAL_DEFAULT: dict[str, str] = {}
"""白膜要贴的**表面材质**（`element_key → 材质包路径`）—— **内置默认故意是空的**。

⚠ **为什么是空的**（2026-09-29 使用前查出来的雷）：原先这里硬编码着 6 条**某台机器上那个工程**的路径
  （形如 `/Game/<某工程目录>/mcp_mats/MI_*`）。那种路径**只在那台机器上存在** ——
  客户那边 `apply_surfaces()` 会逐个 `exists()`、**全部不过 → 按设计整批拒收**，
  看起来像"工具坏了"，而他什么都没做错。**材质路径是你工程的资产，不该写死在代码里。**
  → 现在这张表**只从 `config/surface_materials.json` 来**（见 `_surface_materials()`）；
  没配 / 配空 = **什么都不贴**，并明确报"没有材质映射" —— **不是**报一堆假失败。

⚠ 表**只做键级覆盖**、每次调用重读（改完立刻生效、不用重启）；`apply_surfaces()` 贴之前
  仍会逐个 `exists()` 再验一遍（资产库会在工作期间少东西，实测踩过）。

【历史留档 · 原先那 6 条是什么】来自某个工程的阶段一清单备注与用户点名：
  `ground`/`road`/`sidewalk`/`path`/`grass` → 那个工程里的 `/Game/.../mcp_mats/MI_*`，
  `shrub` → 一个 Fab 材质 `/Game/Fab/Materials/Standard/M_MS_Foliage`。
  2026-09-29 实测那三个材质实例（草地 / 柏油 / 混凝土）的 `exists()` **都是 true**、
  且**已真贴进关卡**（33 行 applied / 1 already / 0 failed）。
  它们现在住在 `config/surface_materials.json`（= 那台机器的配置），**不再进代码**。
  ⚠ `shrub` 那条命中的是**材质**（不是材质实例），而且"贴上去像不像灌木"**未经人确认** ——
  那是"按名字选一个"的决定，不是实测结论。"""


def _surface_materials() -> tuple[dict[str, str], str]:
    """读**表面材质表**：内置默认（**故意是空的**）+ `config/surface_materials.json` 的**键级覆盖**。

    返回 `(表, 提示)`：提示为空 = 一切正常；非空 = **该把它带进 warnings / 日志的原因**
    （文件没了 / 读不动 / 形状不对 —— 用户改了配置却还在用旧值，必须当面告诉他，不静默）。

    为什么**每次调用都读文件**（2026-09-29 用户要求）：这张表是用户会反复改的内容 ——
      写死在 `.py` 里意味着"换一个材质就要改代码 + 预检 + 重启"。**数据归数据。**
    ⚠ **空表不是错误**：`SURFACE_MATERIAL_DEFAULT` 故意是空的（材质路径属于用户工程 ——
      见那个常量的说明）→ 没配 = **一个元素都不贴**。这句话必须**明说**，
      不然用户会以为"贴了但没生效"（那种误会最费时间）。
    ⚠ 只做**键级覆盖**：文件里写了哪个元素就覆盖哪个。
    """
    table = dict(SURFACE_MATERIAL_DEFAULT)
    if not SURFACE_MATERIALS_PATH.exists():
        if not table:
            return table, (f"没找到材质表配置 `{SURFACE_MATERIALS_PATH}`，而**内置默认是空的**"
                           "（材质路径属于你的工程，不该写死在代码里）—— "
                           "**这次没有任何元素会贴材质**。要贴就建这个文件、填你自己的材质包路径，"
                           "例如 `{\"materials\": {\"road\": \"/Game/你的目录/MI_X\"}}`。")
        return table, (f"没找到材质表配置 `{SURFACE_MATERIALS_PATH}` —— 用的是**代码里的内置默认**"
                       f"（{len(table)} 条）。要改材质就建这个文件。")
    try:
        doc = json.loads(SURFACE_MATERIALS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return table, (f"⚠ 材质表配置读不动（{type(exc).__name__}: {exc}）—— "
                       + ("**内置默认是空的 → 这次一个元素都不会贴**" if not table
                          else "用**内置默认**顶上了")
                       + f"，你对 `{SURFACE_MATERIALS_PATH}` 的改动**没生效**。")
    conf = (doc or {}).get("materials") if isinstance(doc, dict) else None
    if not isinstance(conf, dict):
        return table, (f"⚠ `{SURFACE_MATERIALS_PATH}` 里没有 `materials` 段（或它不是对象）—— "
                       + ("**内置默认是空的 → 这次一个元素都不会贴**" if not table
                          else "用**内置默认**顶上了")
                       + "。正确形状：`{\"materials\": {\"shrub\": \"/Game/...\"}}`")
    for k, v in conf.items():
        key, val = str(k or "").strip(), str(v or "").strip()
        if key and val:
            table[key] = val
    return table, ""


# --- 阶段六 · 环境搭建的常量与配置（2026-09-29：扩范围；⚠ 出图当天已收回、归阶段七）------  【模块：environment】
# 用户原话：「阶段6改一下，叫环境搭建和相机预，不只有灯光，其他环境因素全部要做」。
# ⚠ 同一天用户又**收回**了"相机预览"那半句 —— **本阶段定名「环境搭建」**，出图归**阶段七
#   （评估与闭环迭代）**（用户原话「6阶段也不用出图，最后阶段才出图」）。
#   `capture_preview()` 的代码**保留**（已实现并实测），归阶段七用。
# ⚠ 本区块的每条口径都有**实测**出处（2026-09-29 直连官方链路探的原始返回值），
#   逐条写在下面 —— 免得以后有人把这些当"大概是吧"随手改掉。
#   ⚠ 别把这些常量删掉或改名：`setup_environment` / `capture_preview` 的函数体里全靠它们，
#     而**函数体是运行时才求值** —— 少一个常量，预检照样绿、一调用就 NameError。

TS_EDITOR_APP = "EditorToolset.EditorAppToolset"
"""编辑器应用级工具集（视口相机 / 截视口 / 选中）—— 实测 2026-09-29 可用。"""

ENV_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "environments.json"
"""环境预设表（时段 → 每个环境因素写什么）—— **不写死在代码里**。

与 `SURFACE_MATERIALS_PATH` 同一条纪律（2026-09-29 用户就材质表拍过同样的板）：
**每次调用都重新读** —— 改完立刻生效，不用改代码、不用重启。"""

ENV_FACTORS_DEFAULT: dict[str, dict] = {
    "sun": {
        "cn": "太阳 / 时段", "class": "/Script/Engine.DirectionalLight",
        "component": "LightComponent",
    },
    "sky_atmosphere": {
        "cn": "大气", "class": "/Script/Engine.SkyAtmosphere",
        "component": "SkyAtmosphereComponent",
    },
    "sky_light": {
        "cn": "天光", "class": "/Script/Engine.SkyLight",
        "component": "SkyLightComponent",
    },
    "fog": {
        "cn": "高度雾", "class": "/Script/Engine.ExponentialHeightFog",
        "component": "HeightFogComponent",
    },
    "cloud": {
        "cn": "体积云", "class": "/Script/Engine.VolumetricCloud",
        "component": "VolumetricCloudComponent",
        "material_prop": "material",     # ← 实测：云组件上指向材质的那个属性就叫 `material`
    },
    "post_process": {
        "cn": "后处理", "class": "/Script/Engine.PostProcessVolume",
        "component": "",                 # 空串 = 参数写在 **Actor 自己**身上（后处理就是这样）
        "spawn": True,                   # 关卡里本来**没有**（实测 `find_actors` 回 0 个）→ 没有就建
        "folder": f"{OUR_FOLDER_ROOT}_env",
    },
}
"""**因素表（内置兜底）**：`因素 key → {中文名 / 类路径 / 组件名提示 / 是否新建 / 材质属性名}`。

⚠ 2026-09-29 **用户授权把它搬进配置**（`config/environments.json` 的 `factors` 段**按键覆盖**它）——
   理由与材质表那次是同一个：**"有哪些环境因素"是内容，不是代码**。以后要加自己的体积雾
   （`/Script/Engine.LocalFogVolume`）、自己的云、自己的蓝图类，**在配置里加一行就行，
   不用改代码、不用重启**（`_env_config()` 每次调用都重读）。

字段含义：
   `cn`            中文名（给人看）。
   `class`         官方**类路径** —— 按类找 Actor，不写死 Actor 名字（名字里的序号是编辑器给的、会变）。
   `component`     **承载参数的组件对象名前缀**；**空串 = 参数就在 Actor 上**（后处理如此）。
                   ⚠ 组件的**属性名**与**对象名**不是一回事（实测：属性叫 `directionalLightComponent`，
                   而对象叫 `LightComponent0`；雾是属性 `component` / 对象 `HeightFogComponent0`）
                   —— 所以只能"枚举组件 + 按对象名前缀挑"，拿属性名拼路径是错的。
   `spawn`         true = 关卡里没有就**新建一个**（后处理卷实测 0 个 → 必须建）。
   `folder`        新建时放进哪个 outliner 分组（默认 `UEMCP_env`，**刻意与阶段三的 `UEMCP/` 分开**）。
   `material_prop` 该因素**能指向材质实例**的属性名（实测：云的 `material`）—— 填了它，
                   预设里就能用 `material` 段"换成我们自己的 MI + 调它的参数"（云量/云密度走这条）。

实测 2026-09-29：`find_actors(actor_type=<类>)` 对上面前 5 个类**各返回 1 个**
（`DirectionalLight_0` / `SkyAtmosphere_0` / `SkyLight_0` / `ExponentialHeightFog_0` /
`VolumetricCloud_0`），`PostProcessVolume` 回 **0 个**。"""

ENV_FACTOR_ORDER: tuple[str, ...] = (
    "sun", "sky_atmosphere", "sky_light", "fog", "local_fog", "cloud", "post_process",
)
"""**因素的处理顺序** —— 2026-09-29 用户拍板（A 案）：把整改版的执行顺序**写进代码**。

出处 = `docs/阶段六-环境搭建.md`「乙 · 二、整改后的执行顺序」：
  第 1 步 太阳 → 第 2 步 大气 → 第 3 步 天光 → 第 4 步 高度雾 → 第 5 步 体积云 → 第 7 步 后处理。
  第 0 步（找 / 建载体）不在这里 —— 它由 `_env_locate()`（按类找）+ `_env_spawn_actor()`
  （标了 `spawn` 的、没有就建）在**逐因素写循环之前**整段做掉，那本身就是"排最前"。
  第 6 步（路灯 / 室内灯）**本工程没有这个元素**（实测：`catalog/elements.json` 10 个元素里
  没有路灯、`views/plan_v1.json` 里 `light`/`灯` 0 命中）→ 是**空步**，不占位。

⚠ **为什么顺序要写进代码、而不是让配置的键序说话**（与阶段三 `BUILD_LAYERS` 同一条纪律）：
  "次序"是**执行口径**，"某个因素写什么值"才是**内容**。改动前顺序完全由
  `config/environments.json` 里 `presets.<档>` 的**键序**决定 —— 而那张表是用户会反复改的，
  把 `post_process` 挪到第一行，执行顺序就跟着变，**工具不拦也不报**（2026-09-29 核对确认）。

⚠ 用**稳定排序**（`sorted` 稳定）实现：只给一个 rank 键，同 rank 与表外因素的相对次序都不动。

⚠ `local_fog`（局部体积雾）在那套外部流程 / 整改版里**没有单列成步** —— 它是后来从配置
  （`config/environments.json` 的 `factors` 段）加进来的因素。这里按**归类**放进雾族
  （紧跟 `fog`、在 `cloud` 之前）：外部流程里「大气与体积雾」那一步就是这么归的。
  ⚠ 这是**判断、不是实测**：位置对最终画面没有影响（两者都是逐帧生效的属性写入）。

⚠ 不在这个元组里的因素（用户在配置里新加的自定义因素）**排到最后**并在 `warnings` 里**点名** ——
  与阶段三"不在五层表里的落到最后「（未分层）」并报红"是同一条纪律：新因素别静默插到最前面。

⚠ 整改版**第 8 步**（"会触发 shader 重编译的写入排最后"，典型是材质实例的 static switch）
  **当前未实现**：材质参数是 `_env_apply_material()` **就地**跟在那个因素后面写的。
  当前没有任何预设用到 static switch（配置里没有布尔参数 → 零副作用）；真要用时再改，
  那时才有靶子可实测。那一步的提示见 `_env_apply_material()` 的 static switch 分支。"""


ENV_MATERIAL_FOLDER = "/Game/UEMCP/env"
"""**我们自己的材质实例**（`material.create` 建的那种，比如"云量"用的 MI）默认放这个 **Content 目录**。

⚠ 这是**内容浏览器里的资产路径**（`/Game/...`），与上面 Actor 的 outliner 分组（`UEMCP_env`）不是一回事。
⚠ 建出来的 MI **要用户自己存盘**才持久（本工具**绝不存盘**）；不存盘 = 关掉重开就没了。
⚠ 目录不存在时 `create` 会失败 —— 那条路会把官方报错**原文**带回来（不掩饰）。"""

TS_MATINST = "editor_toolset.toolsets.material_instance.MaterialInstanceTools"
"""材质实例工具集 —— 实测 2026-09-29 有 `list_parameters` / `get_/set_scalar_parameter` /
`get_/set_vector_parameter` / `set_texture_parameter` / `set_static_switch_parameter` / `create`。
云量 / 云密度就是靠它调的（参数实测叫 `Cloud_GlobalCoverage` / `Cloud_GlobalDensity`）。"""

ENV_ACTOR_FOLDER = f"{OUR_FOLDER_ROOT}_env"
"""阶段六自己建的 Actor（后处理卷）放这个 outliner 分组。

⚠ **刻意与阶段三的 `UEMCP/` 分开**（不是随手起的名）：阶段三 `execute_build(mode="full")`
  的清场会**删掉 `UEMCP/` 下的一切**，增量对账也会把"台账解释不了的活 Actor"当**人工痕迹拒收** ——
  后处理卷要是放进去，第一次全量重摆就被删、第一次增量对账就被拒。分开之后两边互不干扰。"""

ENV_LEDGER_PATH = VIEWS_DIR / "environment_state_v1.json"
"""**环境台账**：改之前逐个记下的**现值**（含太阳的整份变换）—— 它是**可回滚**的依据。

用户 2026-09-29 在两条路里选了「复用并改这 5 个原生 Actor」，代价正是这本台账。
⚠ 这是我们自己的 JSON，**不是 UE 存盘**（与 `build_state_v1.json` 同类）。"""

PREVIEW_DIR = VIEWS_DIR / "preview"
"""预览图落盘目录（PNG）。

⚠ 图**不回传给模型**：实测 2026-09-29 落盘的三张干净图分别是 **0.87 / 0.94 / 1.07 MB**
  （PNG，1133×693；更早单张探针的 base64 是 **695,632 字符**）—— 塞进上下文毫无意义，
  所以工具只回**路径 + 机位元数据**，要看图由Agent读文件。"""

# 视口截图的注释层参数：**必须整个给全**（实测 2026-09-29：少给任一子字段，官方直接
# RuntimeError —— `input param "annotations" needs a default value`）。
# 传 0 = 关掉该层（`gridSpacing=0` 关网格、`maxLabelDistance=0` 关标签）。
PREVIEW_ANN_CLEAN: dict[str, Any] = {
    "gridSpacing": 0, "gridExtent": 0, "gridHeight": 0,
    "maxLabelDistance": 0, "classFilter": None, "maxLabels": 0,
}
PREVIEW_ANN_LABELS: dict[str, Any] = {
    "gridSpacing": 0, "gridExtent": 20000, "gridHeight": 0,
    "maxLabelDistance": 50000, "classFilter": None, "maxLabels": 12,
}
"""注释层两档：`CLEAN`（干净图，出图给人看）/ `LABELS`（叠世界网格 + Actor 标签，给我核对构图）。
实测 2026-09-29：`LABELS` 那档回报了 12 个中文标签（`路右密林_23 @(32,14,0)` …）——
**它就是"这一帧到底框住了谁"的证据**。"""

ENV_PRESET_DEFAULT: dict[str, dict] = {
    "noon": {
        "sun": {"transform": {"pitch": -49.5, "yaw": -10.3, "roll": 0.0},
                "props": {"intensity": 6.0, "temperature": 6500.0}},
        "sky_light": {"props": {"intensity": 1.0}},
    }
}
"""配置读不到时的**兜底**：只有一档中性预设，值 = 2026-09-29 实测的关卡现值。

⚠ 这里**故意只放一档**：完整的时段表是**内容**（用户会反复调），放进 `.py` 就等于
  "改一个雾的浓度要改代码 + 预检 + 重启" —— 与材质表同一条纪律。缺配置时工具会**明说**
  自己退回了兜底（见 `_env_config()`），不静默。"""


def _env_config() -> tuple[dict[str, dict], dict[str, dict], str]:
    """读**环境配置**：`config/environments.json` 的 `factors`（按键覆盖兜底）与 `presets`。

    返回 `(因素表, 预设表, 提示)`：提示为空 = 一切正常；非空 = **必须带进 warnings 的原因**
    （文件没了 / 读不动 / 形状不对）—— "用户改了配置却还在用旧值"必须当面说，**不静默**。

    为什么**每次调用都读文件**：这两张表都是用户会反复改的内容（时段 / 雾色 / 曝光 / 加新因素），
    写死在 `.py` 里意味着每次微调都要"改代码 + 预检 + 重启"。**数据归数据。**

    ⚠ 因素表是**按键覆盖**：配置里写了哪个因素就覆盖哪个字段，没写的仍用内置兜底
      （所以"只改一下雾的组件名"不用把整行抄一遍）。
    """
    factors: dict[str, dict] = {k: dict(v) for k, v in ENV_FACTORS_DEFAULT.items()}
    if not ENV_CONFIG_PATH.exists():
        return factors, dict(ENV_PRESET_DEFAULT), (
            f"没找到环境配置 `{ENV_CONFIG_PATH}` —— 用的是**代码里的内置兜底**"
            f"（{len(factors)} 个因素 / {len(ENV_PRESET_DEFAULT)} 档预设）。"
            "要按时段配整套环境、或加自己的环境因素，就建这个文件。")
    try:
        doc = json.loads(ENV_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return factors, dict(ENV_PRESET_DEFAULT), (
            f"⚠ 环境配置读不动（{type(exc).__name__}: {exc}）—— 用**内置兜底**顶上了，"
            f"你对 `{ENV_CONFIG_PATH}` 的改动**没生效**。")
    doc = doc if isinstance(doc, dict) else {}
    notes: list[str] = []

    # ① 因素表：按键覆盖内置兜底
    conf_factors = doc.get("factors")
    if isinstance(conf_factors, dict):
        for k, v in conf_factors.items():
            key = str(k or "").strip()
            if not key or not isinstance(v, dict):
                continue
            merged = dict(factors.get(key) or {})
            merged.update(v)
            merged["cn"] = str(merged.get("cn") or key)
            merged["class"] = str(merged.get("class") or "").strip()
            merged["component"] = str(merged.get("component") or "")
            if not merged["class"]:
                notes.append(f"⚠ 配置里的因素 `{key}` **没写 `class`**（官方类路径）—— 这个因素跳过了。")
                continue
            factors[key] = merged
    elif conf_factors is not None:
        notes.append("⚠ 配置里的 `factors` 段不是对象 —— **整段用内置兜底**（你那处改动没生效）。")

    # ② 预设表
    conf = doc.get("presets")
    presets: dict[str, dict] = {}
    if isinstance(conf, dict):
        for k, v in conf.items():
            name = str(k or "").strip()
            if name and isinstance(v, dict):
                presets[name] = v
    if not presets:
        presets = dict(ENV_PRESET_DEFAULT)
        notes.append(
            f"⚠ `{ENV_CONFIG_PATH}` 里没有可用的 `presets` 段 —— 预设用**内置兜底**顶上了"
            "（正确形状：`{\"presets\": {\"sunset\": {\"sun\": {\"props\": {...}}}}}`）。")
    return factors, presets, ("；".join(notes) if notes else "")


ASSET_PIVOT_LIFT_CM: dict[str, float] = {
    "/Game/Fab/Tree/realistic_tree/StaticMeshes/realistic_tree": 31.44,
}
"""**原点不在底面**的资产：scale=1 时原点比底面高多少厘米。

依据：2026-09-26 官方 `get_bounds` 实测（docs/阶段三-资产布局生成.md §11 第 2 条）——
`realistic_tree` 在 scale 1、loc.z=0 时**底面在 -31.44 cm** → 让它站在标高 G 上，
原点得放到 `G + 31.44 × scale`；照 `loc.z = G` 硬摆会**沉进地里**（scale 0.31 时沉 9.75 cm）。
`house1` / `house2` 实测原点**就在底面**（bottom ≈ 0）→ 不在本表里 = 不用抬。
⚠ 这是**唯一一处**"按资产路径特判"的地方；多一个资产要特判就往这张表里加一行。"""

FOLDER_BY_ELEMENT: dict[str, str] = {
    "ground":   f"{OUR_FOLDER_ROOT}/ground",
    "house":    f"{OUR_FOLDER_ROOT}/houses",
    "tree":     f"{OUR_FOLDER_ROOT}/trees",
    "road":     f"{OUR_FOLDER_ROOT}/road",
    "sidewalk": f"{OUR_FOLDER_ROOT}/sidewalk",
    "grass":    f"{OUR_FOLDER_ROOT}/grass",
    "path":     f"{OUR_FOLDER_ROOT}/paths",
    "shrub":    f"{OUR_FOLDER_ROOT}/shrubs",
}
"""元素 → outliner 分组。这是**幂等对账的判据**（清场只清 `UEMCP/` 下的），
也是用户能"一键清理"的抓手。表里没有的 element_key 落到 `UEMCP/<element_key>`。"""

# --- 阶段三 · **搭建分层顺序**（2026-09-27 用户指令）--------------------------------  【模块：build】
# 用户原话：「第三阶段得改一下，关于搭建顺序改为先搭建世界地基再到地皮，再到建筑层再到
#   设施层，最后才是植被层」。
# 层的归属由用户 2026-09-27 在选项里定下：**道路 / 人行道归「设施层」**（不归地皮）。
# ⚠ 这里只决定**落进 UE 的顺序**（`build_orders_v1.json` 的行序 + `execute_build` 逐个 spawn 的
#   次序）—— **不动 plan 的两张表顺序**：plan 是几何权威，且台账/增量按 `uid` 对齐、不靠行号。
# ⚠ 表里没有的 element_key → 落到**最后一层**（`(未分层)`）并在报文里点名，
#   免得新元素静默跑到最前面。
BUILD_LAYERS: list[tuple[str, tuple[str, ...]]] = [
    ("① 世界地基", ("ground",)),
    ("② 地皮", ("grass",)),
    ("③ 建筑层", ("house",)),
    ("④ 设施层", ("road", "sidewalk", "path")),
    ("⑤ 植被层", ("tree", "shrub")),
]
"""五层搭建顺序：**地基 → 地皮 → 建筑 → 设施 → 植被**（由下往上、由主体到配景）。
同层内保持 **plan 原序**（资产行在前、白膜行在后 —— 因为 plan 就是这么排的）。"""

ACTOR_CLASS_PATH = "/Script/Engine.Actor"
"""白膜用的空 Actor 类 —— 2026-09-26 实测 `add_to_scene_from_class` 可以 spawn 它
（docs/阶段三 §11 第 3 条）。"""


def _ref_path(value: Any) -> str:
    """从官方返回里取 `refPath`（对象引用）。

    官方把 UObject 引用序列化成 `{"refPath": "..."}`；但**不能假设它一定是这个形状** ——
    实测个别接口会直接把字符串丢回来。取不到就返回空串（调用方据此判"没拿到 Actor"），
    **绝不编一个路径**。
    """
    if isinstance(value, dict):
        ref = value.get("refPath")
        return str(ref) if ref else ""
    if isinstance(value, str):
        return value
    return ""


def _angle_close(a: float, b: float, tol: float = 0.05) -> bool:
    """两个 yaw（度）算不算同一个朝向 —— **按角度差比，不按数值比**。

    为什么不能直接 `abs(a-b) <= tol`（2026-09-26 实测踩到）：UE 把 yaw 折进 **±180** 存，
    指令里写 `270`、`get_actor_transform` 读回来是 `-90` —— **同一个朝向**，
    数值相减却是 360。第一版就是这么比的，于是刚摆好的 5 栋房子里有 2 栋被误报"对不上"。
    """
    return abs((a - b + 180.0) % 360.0 - 180.0) <= tol


def _safe_actor_name(label: str, index: int, element_key: str) -> str:
    """人话标签 → **UE 能当对象名用的字符串**。

    plan 的 label 是中文带括号的（「住宅 #1（house1）」），直接当 Actor 名有风险
    （括号 / `#` / 空格都可能被拒）。这里只保留中日韩汉字、字母、数字、下划线、连字符，
    其余一律换成下划线 —— 结果是**可读且唯一**（label 本来就一物一个），
    省掉了"先摆再改标签"的 60 次调用。
    真要是标签被洗成空串（纯符号），退回 `元素名_序号` 兜底。
    """
    safe = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(label or ""))
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or f"{element_key or 'actor'}_{index:03d}"


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
mcp = MCPServer("UEMCP-SceneLayout", lifespan=app_lifespan)


# --- [完工-02] 厚函数（纯逻辑，不碰网络，可直接单测）---  【模块：ue_adapter/assets/state（按函数分）】
# 实测 2026-09-23 12:07–12:24：token_match / num / size_from_box / parse_return /
# to_object_path / load_json / save_json 经 plan_assets、confirm_assets 反复调用。
# ✅ 2026-09-30：其中 **`token_match` 已改、并已实测**（改成"关键词的词序列连续出现"才算命中，
#   修"多词关键词被报 missing"的 bug，见该函数 docstring）。
#   实测证据（用户重启后真跑）：`plan_assets(keywords=["MI_Asphalt","MI_Cloud_Sunset",
#   "realistic_tree"])` → `found 3 / missing 0`、`official_calls 4`，其中前两个
#   `matched_by="资产名"` —— **改之前这两个必报 missing**（关键词带 `_`，旧版拿整串去比单个词）。
#   `tests/preflight.py`（脚本方式）已绿。
#   除它之外，`num` / `size_from_box` / `parse_return` / `to_object_path` 未改动。

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


def _tokenize(text: str) -> list[str]:
    """把一串名字切成"词"：先按 `_` `-` 空格切，再按"小写→大写"切驼峰。

    ⚠ **切不开的一种写法**（已知限制，这次没改）：全大写紧跟小写，如 `MIAsphalt` ——
      它会被当成**一个词**（`MIAsphalt`），因为这里只认"小写→大写"这个边界。
      改它会动到**所有**单词匹配的判定，不在本次修复范围内。
    """
    words: list[str] = []
    for part in text.replace("-", "_").replace(" ", "_").split("_"):
        chunk = ""
        prev_lower = False
        for ch in part:
            if ch.isupper() and prev_lower:
                if chunk:
                    words.append(chunk)
                chunk = ch
            else:
                chunk += ch
            prev_lower = ch.islower()
        if chunk:
            words.append(chunk)
    return words


def token_match(keyword: str, text: str) -> bool:
    """词边界匹配 —— 专门对冲官方"纯子串"的缺陷。

    官方 find_assets 的名字过滤是纯子串：搜 "Tree" 会命中 "s**tree**t_lamp_01_4k"（路灯）。
    这里两边都先切成"词"，要求**关键词的词序列作为一整段连续出现**才算命中；
    末词允许直接跟数字（house → house1）。

    ⚠ **为什么是"连续一段"而不是"某一个词相等"**（2026-09-30 修的 bug）：
      旧版拿**整个关键词**去比**单个词** —— 关键词自己带分隔符时永远比不中：
      `MI_Asphalt` 被切成 `MI` / `Asphalt` 两个词，两个都不等于 `mi_asphalt`，
      于是**资产明明在、却报"没找到"**（实测：`plan_assets` 对 `MI_Asphalt` /
      `MI_Cloud_Sunset` 这类名字一律 missing）。词数 >1 的关键词（如 `realistic_tree`）同理。
      现在 `kw == 某个词` 只是段长 1 的特例，旧行为原样保留。
    """
    kw = keyword.lower().strip()
    if not kw:
        return False
    want = [w.lower() for w in _tokenize(kw)]
    if not want:
        return False
    words = [w.lower() for w in _tokenize(text)]
    n = len(want)
    for i in range(len(words) - n + 1):
        run = words[i:i + n]
        if run[:-1] != want[:-1]:
            continue
        last = run[-1]
        if last == want[-1]:
            return True
        # house → house1：末词允许后面直接跟数字
        if last.startswith(want[-1]) and last[len(want[-1]):].isdigit():
            return True
    return False


def num(value: Any, digits: int = 2) -> float:
    """把官方给的数字变成"能安全穿过 JSON 的数字"。三件事缺一不可：

      ① float()  —— 官方有时把数字包成字符串
      ② round()  —— 去掉浮点噪声
      ③ 负零归一 + 非有限值兜底

    ⚠ 为什么必须管负零（-0.0）：Python 把 -0.0 序列化成 "-0.0"，而 JS 的
      JSON.stringify(-0) 得到 "0"，往返不无损。Agent会据此判定整个工具返回值
      "value is not lossless JSON" 并**整条拒绝** —— 不是丢一个字段，是工具直接不可用。
      实测（2026-09-23）：get_scene_context(limit=6) 结果里 0 个负零 → 成功；
      limit=15/40 各含 1 个负零（actors[7].center_cm[0] = -0.0）→ Agent报错整条拒收。
      触发条件：某个物体的中心坐标恰好压在某根轴上。
    """
    v = round(float(value), digits)
    if not math.isfinite(v):
        # inf/nan 会让 JSON 写成 Infinity/NaN，同样被Agent整条拒收。
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


# --- 白膜占位（阶段一「没有资产可用」的元素）------------------------------------  【模块：assets】
# 2026-09-24 用户要求（原文见 docs/阶段一-资产确认.md 第四节）：
#   「用户确认没有的资产，以及用材质实例顶替的资产都要先预估尺寸并用白膜替代，
#     然后再让用户确认资产清单」。
# 这里只是清单登记 —— 阶段一不许往关卡里摆任何东西（硬规则 4）。

PLACEHOLDER_SHAPES = ("cube", "plane")
"""白膜形状：`cube`（体，如窗户 / 车道）/ `plane`（面，如马路 / 草坪）。

`cube` 要有**体积**（三个数），`plane` 是**覆盖面**（至少 X、Y）。
阶段三按这个形状用官方 PrimitiveTools 往 Actor 上加图元。
"""

SIZE_SOURCE_ESTIMATED = "预估（参考图目测，待核实）"
"""白膜行的标准尺寸来源文案 —— **预估值不许伪装成实测值**（硬规则 6）。"""

SIZE_SOURCE_ESTIMATED_LEGACY = "预估（待核实）"
"""旧写法，同样只表示「这是估的」（`placeholder_errors()` 按 `"预估" in src` 判合格）。"""

MATERIAL_ONLY_MARK = "命中的全是非网格资产"
"""`plan_assets` 的 `placeholder_hint` 里，用来标"这个元素只命中材质"的那句话。

单独拎成常量：生成它的地方和回头筛它的地方各写一份字面量的话，
改一处忘一处就会静默漏报（不报错，只是少一条提醒）。"""


def placeholder_errors(item: Any) -> list[str]:
    """校验**一行白膜占位**填得合不合规矩；返回错误清单（空 = 合格）。

    为什么要有这个函数：白膜行是"没有可用资产"的元素在清单里的**唯一交代**。
    它一旦填得不完整（没写形状 / 没给尺寸 / 尺寸来源不是预估），
    读表的人就会把**预估值当实测值**用 —— 那正是硬规则 6 要挡的事。
    所以宁可当场拒收，也不放一行"看着像资产"的白膜进去。

    四道校验：
      ① `asset_path` 必须**空** —— 白膜就是没有资产，带路径是自相矛盾；
      ② `placeholder_shape` 必须是 `cube` / `plane` 之一（不给就是没形状，阶段三没法实例化）；
      ③ `size_cm` 至少给 X、Y（`cube` 还要 Z，要有体积）；
      ④ `size_source` 必须写明是**预估**（默认值也算）—— 不许让预估值看起来像实测值。
    """
    errs: list[str] = []
    name = str(getattr(item, "element", "") or "?")

    if str(getattr(item, "asset_path", "") or "").strip():
        errs.append(
            f"{name!r}：白膜占位行的 asset_path **必须是空的**（白膜就是「没有可用资产」），"
            f"现在填了 {getattr(item, 'asset_path', '')!r} —— "
            "要么把它改成真资产行（is_placeholder=false），要么把路径清空。"
        )

    shape = str(getattr(item, "placeholder_shape", "") or "").strip().lower()
    if shape not in PLACEHOLDER_SHAPES:
        errs.append(
            f"{name!r}：白膜占位行必须填 placeholder_shape，"
            f"只能是 {' / '.join(PLACEHOLDER_SHAPES)}（现在填的是 {shape!r}）。"
            "cube = 有体积的（窗 / 车道），plane = 覆盖面（马路 / 草坪）——"
            "阶段三要按这个形状加图元。"
        )

    size = getattr(item, "size_cm", None)
    n = len(size) if isinstance(size, (list, tuple)) else 0
    if n < 2:
        errs.append(
            f"{name!r}：白膜占位行必须给**预估尺寸** size_cm（厘米，至少 X、Y）；"
            + ("cube 还要给 Z（要有体积）。" if shape == "cube" else "")
        )
    elif shape == "cube" and n < 3:
        errs.append(f"{name!r}：cube（体）类的白膜要给三个数 [X, Y, Z] —— 现在只有 {n} 个。")

    src = str(getattr(item, "size_source", "") or "").strip()
    if src and "预估" not in src:
        errs.append(
            f"{name!r}：白膜占位行的尺寸是**估的**，size_source 必须写明"
            f"（建议原样写 {SIZE_SOURCE_ESTIMATED!r}），现在写的是 {src!r} —— "
            "预估值不许伪装成实测值。"
        )
    return errs


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


# --- 大类表（阶段一「元素 → 大类」的词表）---------------------------------------  【模块：assets】
# 2026-09-24 用户要求：先有这张大类表（照 Fab 的 3D 分类树），根据参考图把它填好，再去按类找资产。

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CATEGORY_PATH = CONFIG_DIR / "asset_categories.json"
UNCATEGORIZED = "uncategorized"
UNCATEGORIZED_CN = "未分类"


def load_categories() -> dict:
    """读大类表 → `{by_key, order, kinds, scene_keys, value_field, how_to_fill, fallback_*, defined}`。

    - `by_key`      : key → 中文名
    - `kinds`       : key → `asset`（要去资产库找）/ `scene`（场景级信息，**不是资产**）
    - `value_field` : scene 项的值填在 `ElementItem` 的哪个字段（如地图大小 → size_m）
    ⚠ 读不到表**不报错**，退化成"只有未分类一项" —— 缺一张词表不该把阶段一整条链卡死；
      但 `defined` 会如实报出表里原有几项，调用方一眼能看出表丢了。
    """
    doc = load_json(CATEGORY_PATH) or {}
    by_key: dict[str, str] = {}
    order: list[str] = []
    kinds: dict[str, str] = {}
    value_field: dict[str, str] = {}
    how_to_fill: dict[str, str] = {}
    for c in (doc.get("categories") or []):
        if not isinstance(c, dict):
            continue
        k = str(c.get("key") or "").strip()
        if not k or k in by_key:
            continue
        by_key[k] = str(c.get("cn") or k)
        order.append(k)
        kinds[k] = "scene" if str(c.get("kind") or "asset").strip() == "scene" else "asset"
        value_field[k] = str(c.get("value_field") or "")
        how_to_fill[k] = str(c.get("how_to_fill") or "")
    fb_key = str(doc.get("fallback_key") or UNCATEGORIZED)
    fb_cn = str(doc.get("fallback_cn") or UNCATEGORIZED_CN)
    if fb_key not in by_key:
        by_key[fb_key] = fb_cn
        order.append(fb_key)
        kinds[fb_key] = "asset"
    return {
        "by_key": by_key,
        "order": order,
        "kinds": kinds,
        "scene_keys": [k for k in order if kinds.get(k) == "scene"],
        "value_field": value_field,
        "how_to_fill": how_to_fill,
        "fallback_key": fb_key,
        "fallback_cn": fb_cn,
        "defined": len(order) - 1,
    }


def category_kind(key: str, table: dict | None = None) -> str:
    """这个大类是 `asset`（找资产）还是 `scene`（场景级信息，不是资产）；未分类算 asset。"""
    t = table if table is not None else load_categories()
    return t["kinds"].get((key or "").strip(), "asset")


def is_scene_category(key: str, table: dict | None = None) -> bool:
    """是不是"场景项"（地图大小 / 地图对应时间）—— 这类**不许去搜资产、也不许当白膜**。"""
    return category_kind(key, table) == "scene"


def category_cn(key: str, table: dict | None = None) -> str:
    """大类 key → 中文名（查表；空 key = 未分类）。

    ⚠ 查不到就写 `<key>（不在大类表里）`，**不编**一个中文名、也不静默吞成"未分类" ——
      表改过 / 手改过数据时要看得见。
    """
    t = table if table is not None else load_categories()
    k = (key or "").strip()
    if not k:
        return t["fallback_cn"]
    return t["by_key"].get(k, f"{k}（不在大类表里）")


def group_elements_by_category(items: list[dict], table: dict | None = None) -> dict:
    """把带 `category` 的行分组 → `{中文大类名: [元素名, ...]}`，**顺序照表**，未分类排最后。

    键用中文名：这份分组是要**直接给人看**的（"填好的表"），读表的人不该去猜 furniture 是什么。
    表里没有的大类（旧数据 / 手改）放最后 —— **不许丢**。
    """
    t = table if table is not None else load_categories()
    buckets: dict[str, list[str]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        cn = category_cn(str(it.get("category") or ""), t)
        buckets.setdefault(cn, []).append(str(it.get("element") or ""))
    ordered: dict[str, list[str]] = {}
    for k in t["order"]:
        cn = t["by_key"][k]
        if cn in buckets:
            ordered[cn] = buckets.pop(cn)
    for cn in sorted(buckets):
        ordered[cn] = buckets[cn]
    return ordered


def order_category_names(names: set[str], table: dict | None = None) -> list[str]:
    """把一组中文大类名按**表里的顺序**排好；表外的（未分类 / 手改过的）放最后 —— **不丢**。"""
    t = table if table is not None else load_categories()
    order = [t["by_key"][k] for k in t["order"]]
    return [cn for cn in order if cn in names] + sorted(cn for cn in names if cn not in order)


def scene_from_rows(rows: list[dict], table: dict | None = None) -> tuple[dict, list, str]:
    """从表里 `kind=scene` 的行抽出场景项。

    返回 `({中文名: 人话值}, 地图大小 [X,Y] 或 None, 时间字符串)`。
    值填在哪个字段由**表**决定（`value_field`）—— 代码不写死 key，
    以后往表里加第三个场景项，只要表里写清 value_field 就能自动带出来。
    """
    t = table if table is not None else load_categories()
    scene: dict[str, str] = {}
    size_m: list[float] | None = None
    tod = ""
    for e in rows:
        if not isinstance(e, dict):
            continue
        cat = str(e.get("category") or "")
        if not is_scene_category(cat, t):
            continue
        cn = t["by_key"].get(cat, cat)
        vf = t["value_field"].get(cat, "")
        raw = e.get("size_m")
        if vf == "size_m" and isinstance(raw, (list, tuple)) and len(raw) >= 2:
            size_m = [float(raw[0]), float(raw[1])]
            scene[cn] = f"{size_m[0]:g}×{size_m[1]:g} m（预估）"
        elif vf == "time_of_day" and str(e.get("time_of_day") or "").strip():
            tod = str(e["time_of_day"]).strip()
            scene[cn] = tod
        else:
            scene[cn] = str(e.get("note") or "(未记录)")
    return scene, size_m, tod


def library_fingerprint(inventory: dict[str, list[str]]) -> tuple[str, dict[str, int]]:
    """把「资产库快照」压成一个 sha256 指纹 + 每类数量。

    为什么需要它：`delivered_at` 只能说明"什么时候确认的"，回答不了"项目变没变"。
    这里**只对路径负责**：同一批路径（与枚举顺序无关）→ 同一个指纹。

    ⚠ 路径没变、资产**内容**变了（换贴图、改材质参数）**测不出来** —— 这是刻意的取舍：
      要覆盖内容得上资产 GUID / 磁盘时间戳，成本高一个量级。
      所以使用文案里要写明：清单承诺的是**路径有效**，不是**内容没变**。
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
    """从使用表的行里取出「已经落位的元素关键词」，以及**用的是哪种判定方式**。

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


# --- [完工-03] 官方调用与枚举/实测（call_official / library_inventory / measure_asset）---  【模块：ue_adapter】
# 实测 2026-09-23 12:24：confirm_assets 用它量到 4 个网格的包围盒，
# 并对 2 个材质如实报"material_instance 没有包围盒"（没编数字）。


async def call_official(
    ctx: Context[AppContext], tool_name: str, arguments: dict, toolset: str | None = None,
    check_link: bool = True,
) -> Any:
    """调用官方的一个能力，并把结果解析成 Python 对象。

    两种调用方式（实测）：
      - toolset=None   → 直接调官方**元工具**（list_toolsets / describe_toolset / call_tool）
      - toolset=某工具集 → 经元工具 call_tool 转发到具体工具
        （官方 Tool Search 默认开着，具体工具不出现在 tools/list 里，只能这样转发）

    ⚠ 连接由 `_connection_loop` 持有：这里**只借用，不建也不关**
      （原因见本文件顶部"官方连接"那段）。
    ⚠ `check_link` 原样透传给 `official_client()`（默认 `True` = 先确认链路）——
      只有"检查链路"这件事本身（`official_status()`）才传 `False`。
    """
    if toolset is None:
        mcp_tool, payload = tool_name, arguments
    else:
        mcp_tool = "call_tool"
        payload = {"toolset_name": toolset, "tool_name": tool_name, "arguments": arguments}

    app = ctx.request_context.lifespan_context
    client = await official_client(ctx, check_link=check_link)

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
        # 连接可能已经死了（编辑器关了、会话被踢）。不能在这里关连接 ——
        # 只发一个"请重连"的信号，让连接 task 在它自己的 task 里收尾并重建。
        app.stop.set()
        new_client = None
        for _ in range(60):
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

    ⚠ 这里是**唯一一份**枚举逻辑：`plan_assets` 和 `get_asset_list` 都调它
      （2026-09-23 用户拍板合并）。
    返回的路径已排序：让指纹与枚举顺序无关，也让"截前 N 个"更确定。
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
    volume = num(size[0] * size[1] * size[2])
    return size, volume, "官方 get_bounds 实测（包围盒）"


# --- [完工-04] 模型（工具返回结构）已移到 schemas.py（2026-09-27）----------------  【模块：schemas】
# 约 709 行的 25 个 Pydantic 类整体搬进 `src/mcp_server/schemas.py`，本文件顶部把它们
# **逐个显式再导出** —— 所以下面所有 `-> SomeModel` 的标注与 `SomeModel(...)` 的构造
# 一个字都不用改。搬运是逐行切片做的（没手抄），模型定义未改。


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
      Agent侧表现为 mcp-client(mine) 重连 10 次全败。
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
    out.sort(key=lambda a: (a.matched_by != "资产名", len(a.path)))
    return out[:limit]


# --- [完工-05] 工具 1：链路自检 ---  【模块：workflow（壳）+ state（闸）】
# 实测 2026-09-23 12:07：返回 reachable=true / protocol 2025-11-25 / 52 个工具集。

@mcp.tool()
async def official_status(ctx: Context[AppContext]) -> OfficialStatus:
    """自检：能不能连上官方 Unreal MCP，并报告协议版本和工具集数量。
    ⚠ **闸的钥匙**（不是可选步骤）：`_link_guard()` 认它 —— **任何碰 UE 的工具**（它们都经
      `official_client()`）在本 server 进程里没见它**报过连上**，就会被拒收并点名要你先调它。
      （2026-09-26 它只被 `execute_build()` 要求；**2026-09-30 起扩到所有碰 UE 的路径** ——
      用户要求「每次要动 UE 必须检查连接状态」；它同时是唯一能当"链路诊断"的口子。）

    任何依赖官方的操作之前，先用它确认链路。连不上时返回**错误原文**，不猜原因。

    ⚠ 本 server 是**编排层**：自己完全不碰 UE —— 改关卡 / 摆 Actor / 改材质都要经官方
      `unreal` 那侧的工具做。
    ⚠ **没有对应工具的能力 = 还没实现** —— 如实说「还没做」，不许硬凑、不许假装能做。
    ⚠ 讲流程时**以 `docs/` 下的规划文档为准，不许凭印象**。
    ⚠ 硬性禁止：不许寻找/猜测「疑似资产」；不许删资产；**不许存盘**（保存 UE 关卡 / 资产）。
    """
    # ⚠ 这次调用留**两种**痕（2026-09-26 加第一种；2026-09-30 补第二种）：
    #   ① `_mark_session_prereq` —— "开场动作做过没有"（`_session_prereq_guard()` 认它，**不看结果**）；
    #   ② `_mark_link_state`    —— "**这次链路通没通**"（`_link_guard()` 认它，**看结果**）。
    #   只记①不记②曾是漏洞：连不上也算"调过"，于是链路一断就一路以最难懂的方式失败
    #   （2026-09-30 实测：开机后客户端与 MCP 断连，Agent 一上来就调工具）。所以①照旧记、
    #   ②按**返回值**记 —— 且本工具自己调官方时传 `check_link=False`（不能自己拦自己）。
    _mark_session_prereq("official_status")
    try:
        catalog = await call_official(ctx, "list_toolsets", {}, check_link=False)
        text = catalog if isinstance(catalog, str) else json.dumps(catalog, ensure_ascii=False)
        # 官方把工具集列成 "- 工具集名: 说明"。只数形如 "- xxx.yyy:" 的行，
        # 别把说明里的项目符号也算进去（实测：那样会数出 67，真实值是 52）。
        count = len(re.findall(r"^-\s+\S*\.\S*:", text, flags=re.MULTILINE))
        _mark_link_state(True)
    except ToolError as exc:
        _mark_link_state(False, str(exc))
        return OfficialStatus(reachable=False, error=str(exc))

    # 协商出的协议版本：上面那次 list_toolsets 调用已经把连接建起来了，
    # 所以此刻读 client.protocol_version 是安全的（未连接时抛 RuntimeError）。
    # 读不到不算自检失败：版本只是报告项，不该把整条结果判死。
    version: str | None = None
    try:
        app = ctx.request_context.lifespan_context
        version = app.ue.protocol_version if app.ue is not None else None
    except (RuntimeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("读协议版本失败（已忽略）：%r", exc)

    return OfficialStatus(reachable=True, protocol_version=version, toolset_count=count)


# --- 阶段三 · 步骤 1：搭建目标检查（2026-09-26 用户要求）--------------------------  【模块：state（搭哪张图闸）】
# 用户原话：「第三阶段第一步应该是检查，别动用户的 UE，先问用户在哪开始搭建，
#   是开新图还是用户指定地图或区域，确认开新图就新建关卡开始搭建，
#   用户有指定就去指定地方搭建」。
# 所以这一步**只读**：只问、只看、只报告 —— 一个 Actor 都不放、一张图都不切。

# --- check_build_target 的分段私有函数（2026-09-27 结构优化第 2 步）-------------  【模块：state】
# 为什么拆：这个工具原本是**一个 306 行的函数**（210 行代码），读一遍要来回滑好几屏。
#   **行为一个字没改** —— 只是把"读关卡 / 数我们摆过的东西 / 组织问句 / 记答复 / 出下一步"
#   各挪成有名字的函数，工具主体只剩编排。
# ⚠ `calls`（官方调用留痕）与 `warnings`（警告）两个列表**按原顺序逐个透传**：
#   谁都不新建、不重排 —— 这两样是调用方直接看到的输出，顺序变了等于报文变了。
# ⚠ 这些函数**只被 check_build_target 用**；它们不碰关卡（本工具只读，唯一写的是答复台账）。


async def _bt_probe_level(
    ctx: Context[AppContext], calls: list[str], warnings: list[str]
) -> tuple[str, str, list[str], int]:
    """① 当前关卡 ② 项目里的关卡候选 ③ 关卡 Actor 总数。

    返回 `(current, read_error, candidates, total)`。
    ⚠ `read_error` 非空 = **读当前关卡这一步就失败了**（链路 / 编辑器的问题）。
      它必须单独占一个返回值：读到了但为空串（无关卡的 World）与"读不到"是两回事，
      拿 `current == ""` 当失败信号会把后者误判成前者。
    """
    # ① 当前关卡（读不到就是链路/编辑器的问题，交给调用方如实返回）
    current = ""
    try:
        current = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
        calls.append("SceneTools.get_current_level")
    except ToolError as exc:
        return "", str(exc), [], 0

    # ② 项目里有哪些关卡资产（供用户「指定地图」用）
    candidates: list[str] = []
    try:
        found = await call_official(
            ctx, "find_assets",
            {"folder_path": "/Game", "name": "",
             "asset_type": {"refPath": LEVEL_CLASS_PATH}, "recursive": True},
            toolset=TS_ASSET,
        )
        candidates = sorted(p for p in (found or []) if isinstance(p, str))
        calls.append("AssetTools.find_assets(/Script/Engine.World)")
    except ToolError as exc:
        warnings.append(f"列关卡资产失败（{exc}）—— 候选列表不完整，不影响你直接指定路径。")

    # ③ 当前关卡里有多少 Actor
    total = 0
    try:
        actors = await call_official(
            ctx, "find_actors", {"name": "", "tag": "", "collision_channels": []},
            toolset=TS_SCENE)
        total = len(actors or [])
        calls.append("SceneTools.find_actors")
    except ToolError as exc:
        warnings.append(f"数关卡 Actor 失败（{exc}）。")

    return current, "", candidates, total


async def _bt_count_our_actors(
    ctx: Context[AppContext], calls: list[str], warnings: list[str]
) -> tuple[list[str], int]:
    """④ 当前关卡里属于 `UEMCP` 的 outliner 文件夹 ⑤ 我们摆过的 Actor 数（按引用去重）。

    这两件事绑在一起拆：它们共用 `our_folders`（后者靠前者决定去数哪些文件夹）。
    """
    our_folders: list[str] = []
    try:
        folders = await call_official(ctx, "get_folders", {}, toolset=TS_SCENE)
        our_folders = sorted(
            f for f in (folders or [])
            if isinstance(f, str)
            and (f == OUR_FOLDER_ROOT or f.startswith(OUR_FOLDER_ROOT + "/"))
        )
        calls.append("SceneTools.get_folders")
    except ToolError as exc:
        warnings.append(f"读 outliner 文件夹失败（{exc}）。")

    # ⚠ **只数根文件夹一次**（`recursive=True` 已经把子文件夹里的都算进来了）。
    # 2026-09-26 实测踩到：根 + 7 个子文件夹各数一遍 = **2 倍**（根里真 89 个，报成了 178）。
    our_refs: set[str] = set()
    for folder in ([OUR_FOLDER_ROOT] if OUR_FOLDER_ROOT in our_folders else our_folders):
        try:
            members = await call_official(
                ctx, "get_actors_in_folder",
                {"folder_path": folder, "recursive": True}, toolset=TS_SCENE)
            calls.append(f"SceneTools.get_actors_in_folder({folder})")
            for item in (members or []):
                ref = _ref_path(item)
                if ref:
                    our_refs.add(ref)
        except ToolError:
            continue
    return our_folders, len(our_refs)


def _bt_warn_existing_actors(
    warnings: list[str], our_count: int, our_folders: list[str], current: str
) -> None:
    """⑥ 关卡里已经躺着我们摆过的 Actor → 如实警告，并给出"增量 / 全量"两条路。

    顺带比一下"指令表几行"—— 数量一致就说明关卡与这一版表对得上，局部改可以直接走增量。
    """
    orders_doc = load_json(BUILD_ORDERS_PATH) or {}
    want = len(orders_doc.get("rows") or []) if isinstance(orders_doc, dict) else 0

    if our_count:
        if not want:
            hint = " 还没有指令表可比 —— 先用 `generate_build_orders()` 生成。"
        elif want == our_count:
            hint = (f" 与 `views/build_orders_v1.json` 的 {want} 行**数量一致**，看起来就是这一版"
                    "搭出来的 → 局部改动走**增量**：`execute_build(mode=\"incremental\")`"
                    "（还没台账就先 `adopt=true` 认领现状）。")
        else:
            hint = (f" 但指令表是 {want} 行 —— **数量对不上**，先查差额（多半是上一版的残留）："
                    "要么 `mode=\"full\"` 重摆，要么先 `adopt=true` 看清现状。")
        warnings.append(
            f"⚠ 当前关卡里已经躺着**我们摆过的 {our_count} 个 Actor**"
            f"（outliner 文件夹：{'、'.join(our_folders)}）——" + hint + " 两条路都要用户拍板。")
    if not current:
        warnings.append("当前关卡路径读不到（返回空字符串）。")


def _bt_question_and_options(current: str, candidates: list[str]) -> tuple[str, list[str]]:
    """⑦ 要问用户的那句话 + 三个选项（纯字符串拼装，不读任何东西）。"""
    question = (
        f"搭在哪里？（当前关卡：{current or '读不到'}）"
        "① 开新图　② 用项目里已有的某张图（你把路径给我）　③ 就用当前这张图 —— 你定。"
        "⚠ 官方工具里**没有「新建关卡」**，所以选 ① 的话要**你在 UE 里新建并打开**，我再去那张图上搭。"
    )
    options = [
        "① 开新图 —— 你在 UE 里新建并打开（官方没有 new level 工具，我只能在你建好的图上搭）",
        f"② 用指定的既有图 —— 项目里能列到 {len(candidates)} 张（见 candidate_levels）；"
        "你给路径，我用 SceneTools.load_level 切过去",
        f"③ 就用当前这张：{current or '（读不到）'}",
    ]
    return question, options


def _bt_record_answer(
    current: str,
    candidates: list[str],
    decision: str,
    target_level: str,
    user_quote: str,
    warnings: list[str],
) -> tuple[str, dict | None, bool, str]:
    """⑧ 把用户的答复记进台账 `views/build_target_v1.json` —— 本工具**唯一的写操作**。

    返回 `(decision_key, answer, kept_answer, record_path)`：
      · `decision_key` 空串 = 这一版只问、没带答复；
      · `kept_answer` = 台账里**原来那份答复还在**（要如实报，别报成"没答复"）。
    ⚠ 给了 `decision` 却不给 `user_quote` → **当场拒收**（拿不出用户原话，就说明没问过他）。
    """
    decision_key = ""
    answer: dict | None = None
    kept_answer = False          # 这一版没带答复、但台账里**已有**一份（要如实报，别报成"没答复"）
    record_path = ""
    if str(decision or "").strip():
        decision_key = _norm_decision(decision)
        quote = str(user_quote or "").strip()
        if not quote:
            raise ToolError(
                "拒收：给了 `decision` 但**没给 `user_quote`（用户答复的原话）**。\n"
                "· 目标图是**用户**定的，不是我们挑的 —— 拿不出他的原话，就说明还没问过他。\n"
                "· 正确顺序：先不带 `decision` 调一次（只读地问）→ 把问题交给用户、**停下等他打字**\n"
                "  → 再带 `decision` + 他的原话调一次（把答复记下来）。"
            )
        if decision_key == "existing" and not str(target_level or "").strip():
            raise ToolError(
                "拒收：`decision=\"existing\"`（用指定的既有图）必须同时给 `target_level` —— "
                "**路径要用户给**，我不猜他用哪张图（项目里可能有好几张）。"
            )
        answer_level = (str(target_level or "").strip() if decision_key == "existing" else current)
        answer = {
            "decision": decision_key,
            "level": answer_level,
            "user_quote": quote,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        record_path = _save_build_target(current, answer)
        warnings.append(
            f"答复已记进 `{record_path}`：**{decision_key}** → `{answer_level or '（关卡读不到）'}`"
            f"（用户原话「{quote}」）。`execute_build()` 会拿它与当前关卡核对，对不上就拒收。")
        if decision_key == "existing" and answer_level and candidates and answer_level not in candidates:
            warnings.append(
                f"⚠ 你指定的 `{answer_level}` **不在我列到的 {len(candidates)} 张关卡里** —— "
                "可能它在别的目录、或路径写法不同（我不猜）。本工具**不切图**：请确认这张图存在，"
                "并在 UE 里切过去；切过去之后 `execute_build` 才认。")
        if decision_key == "new":
            warnings.append(
                f"⚠ 用户选的是**开新图** —— 官方没有「新建关卡」的工具（实测只有 `load_level`），"
                "**要他自己在 UE 里新建并打开**。在他打开新图之前调 `execute_build` 会被拒收"
                f"（现在开着的还是 `{current or '（读不到）'}`）。")
    else:
        record_path = _save_build_target(current, None)
        _prev = _load_build_target().get("answer")
        if isinstance(_prev, dict) and _prev:
            kept_answer = True
            warnings.append(
                f"这次只**问**、没带答复 —— 已经留痕的那份答复"
                f"（`{_prev.get('decision')}` → `{_prev.get('level')}`）**原样保留**"
                f"（`{record_path}`）。要**改**答复就带 `decision`（`existing` 还要 "
                "`target_level`）+ 用户原话再调一次；否则 `execute_build` 认的还是原来那份。")
        else:
            warnings.append(
                f"这次只**问**、还没有答复（已把『问的时候 UE 开着哪张图』记进 `{record_path}`）。"
                "把上面那句 `question` 交给用户、**停下等他打字**，再带他的原话调一次本工具。"
                "⚠ **在他答复之前，`execute_build` 会被代码拒收**（一个 Actor 都不会落）。")
    return decision_key, answer, kept_answer, record_path


def _bt_next_step(answer: dict | None, decision_key: str, kept_answer: bool) -> str:
    """⑨ 下一步指引 —— 分"答复已留痕"与"只问没答"两种。"""
    if answer:
        return (
            f"答复已留痕（`{decision_key}`）。**下一步**："
            "⓪ 开场动作（`official_status()` + `get_asset_list()`，各一次；没做过 `execute_build` 会拒收）→ "
            "① `generate_build_orders()` 生成搭建指令表 → "
            "② `execute_build()` 落关卡 —— 它会**先核对『这份答复 vs 当前关卡』**，不一致就拒收列出来；"
            "③ 想先看不动手就传 `dry_run=true`。⚠ `execute_build` 还会过**回读闸**"
            "（这一版没 `get_plan()` 过就拒收）。⚠ 全程**不存盘**。"
        )
    return (
        ("（⚠ 台账里**原来那份答复还在** —— `execute_build` 认的还是它；要改就带 "
         "`decision` + 用户原话重调一次。）" if kept_answer else "")
        + "把用户的选择回给我。**在他答复之前，我不会往关卡里放任何东西**"
          "（现在这条是**代码闸**：没答复就调 `execute_build`，会被拒收）。"
          "他答复之后：带 `decision`（`existing` 还要 `target_level`）+ 他的原话**再调一次本工具**，"
          "才轮到「生成搭建指令表 → 落关卡」。"
    )


@mcp.tool()
async def check_build_target(
    ctx: Context[AppContext],
    decision: Annotated[str, Field(
        description=(
            "**用户的答复**（拿到答复后**再调一次本工具**把它记下来）："
            "`current` = 就用当前这张图 / `existing` = 用他指定的既有图（同时给 `target_level`）/ "
            "`new` = 开新图（**他自己**在 UE 里建好并打开）。"
            "⚠ 留空 = 还没问过用户 —— 那就只是问一遍（只记下「问的是哪张图」，不记答复），"
            "`execute_build` 也不会放行"))] = "",
    target_level: Annotated[str, Field(
        description="`decision=\"existing\"` 时，用户指定的关卡路径（如 `/Game/Maps/Street`）")] = "",
    user_quote: Annotated[str, Field(
        description=(
            "**用户答复的原话（给了 `decision` 就必填）** —— 与 `confirm_plan` 同一条纪律："
            "拿不出原话，就说明你还没问过他。**不许自己编**"))] = "",
) -> BuildTargetCheck:
    """阶段三第 1 步：问清楚「搭在哪张图」—— **不碰关卡**，只写我们自己的答复台账。

    为什么第一步是它（用户 2026-09-26 明确要求）：**别动用户的 UE**。开搭之前必须先问清楚
    目标是**新图**、**指定的既有图**、还是**当前这张图**；确认开新图才新建关卡，有指定就去
    指定地方搭。

    ⚠ **本工具不碰关卡**：不新建关卡、不切换关卡、不摆也不删任何 Actor，**不存 UE**。
      唯一写的是我们自己的 `views/build_target_v1.json`（谁问的、用户怎么答的）。
    ⚠ **官方没有「新建关卡」的工具**（实测：`SceneTools` 只有 `load_level`，没有 new level）——
      所以「开新图」这一步得**用户自己**在 UE 里建好并打开，我们只在他建好的图上搭。
      这条如实报出来，不假装能建。
    ⚠ **流程（两步，别合成一步）**：
      ① **先问**（`decision` 留空）→ 把 `question` / `options` 交给用户 → **停下等他打字**；
      ② 拿到他的话之后**再调一次**，带 `decision`（+ `existing` 还要 `target_level`）+ `user_quote`
         —— 这一步只是把答复记下来。
      ③ 之后 `execute_build()` 会拿这份答复**核对当前关卡**：对不上就拒收（一个 Actor 都不动）。
         "拿到用户答复前一个 Actor 都不许放" 现在是**代码闸**，不再是文档里的软约束。
    """
    calls: list[str] = []
    warnings: list[str] = []

    current, read_error, candidates, total = await _bt_probe_level(ctx, calls, warnings)
    if read_error:
        # ① 就读不到当前关卡 = 链路 / 编辑器的问题，如实返回（与拆分前同一个分支）
        return BuildTargetCheck(
            stage="阶段三 · 资产布局生成（步骤 1/检查）",
            readonly=True, official_calls=calls, current_level="",
            question=f"读不到当前关卡：{read_error}",
            next_step="先确认 UE 编辑器开着、ModelContextProtocol 插件启用、端口 8000 在监听，再调一次。",
        )

    our_folders, our_count = await _bt_count_our_actors(ctx, calls, warnings)
    _bt_warn_existing_actors(warnings, our_count, our_folders, current)
    question, options = _bt_question_and_options(current, candidates)

    # ---------- 用户的答复：记下来（**阶段三第 1 步的终点，也是 execute_build 的闸**）----------
    decision_key, answer, kept_answer, record_path = _bt_record_answer(
        current, candidates, decision, target_level, user_quote, warnings)
    next_step = _bt_next_step(answer, decision_key, kept_answer)

    return BuildTargetCheck(
        stage="阶段三 · 资产布局生成（步骤 1/检查）",
        readonly=True,
        official_calls=calls,
        current_level=current,
        candidate_levels=candidates,
        our_folders=our_folders,
        our_actor_count=our_count,
        total_actors=total,
        can_create_level=False,     # 实测：官方没有 new level 工具
        decision=decision_key,
        answer_recorded=bool(answer) or kept_answer,
        record_path=record_path,
        warnings=warnings,
        question=question,
        options=options,
        next_step=next_step,
    )


# --- 阶段三 · 步骤 2：搭建指令表 + 批量落关卡（2026-09-26）-----------------------  【模块：build】
# 用户原话（2026-09-26）：「继续完善第三阶段，第二步代码，按照位置坐标表开始一次性全部批量搭建」。
# 分工照 docs/阶段三-资产布局生成.md §2：
#   ① `generate_build_orders()` —— **纯计算**，把 plan（米）翻成 UE 能照做的指令表（厘米）；
#   ② `execute_build()` —— **先整批校验**（不过就一个 Actor 都不落）→ 清旧的 → 按表落 → 读回对账。
# ⚠ 两个工具**都不存盘**（不调任何 save 类工具）—— 这是硬规矩。
#
# ✅ [完工-14] 实测 2026-09-26（经 MCP 真跑，非演练）：plan 指纹 a01717bd14…（已确认+验收通过）
#   · `generate_build_orders()` → 落盘 views/build_orders_v1.json，**60 行**（资产 39 + 白膜 21），
#     分元素 house5/tree34/road1/sidewalk2/grass9/path6/shrub3，warnings 为空；
#   · `execute_build()` → **placed 60 / 60**、`removed 0`（首跑，图上本来没有 UEMCP/ 的东西）、
#     `official_calls 206`、7 个分组各就各位、落完后 UEMCP/ 计数 60（= 指令表行数，没报警）；
#   · 读回对账报 2 条 "yaw -90 != 270"（house2 ×2）—— **是同一角度**，UE 把 yaw 折进 ±180 存；
#     → 对账已改成按角度差比（`_angle_close`）。
#   · 全程未调任何 save。
# ✅ [完工-14] 追加实测 2026-09-26（同一批工具，第二次真跑 —— 把上面那条修正验掉了）：
#   plan 指纹 36d3697d37…（第 22 轮，用户看图后确认）· **89 行**（资产 39 + 白膜 50）：
#   · `generate_build_orders()` → 89 行，分元素 house5/tree34/road1/sidewalk2/grass5/path25/shrub17；
#     报出**越界警告**（#60/#68 屋后走道中心已出界）—— 如实报、不拦（越界处置留给阶段四）。
#   · `execute_build()` → **placed 89 / 89**、`removed 60`（清掉上一版全部旧 Actor）、
#     `verified 89 / 89`、**`mismatches` 空**、`official_calls 383`、7 个分组计数与指令表逐项相等。
#     ⚠ 上一版同一组 house2 行（yaw 指令 270 / 读回 -90）在这里报过 2 条假警报，
#       本次判据换成 `_angle_close` 后**为 0 条** —— 这一处修正**已实测**。
#   · **仍未实测**的一处：`get_actors_in_folder` 抛 "Folder does not exist" 时**不再刷警告**
#     那段分支（本次开局 `UEMCP/` 里有 60 个旧 Actor，走的不是那条路）。
# ✅ [完工-14] 追加实测 2026-09-27（同一批工具**第三次**真跑 —— 全量 + 按 plan 覆盖人工痕迹）：
#   plan 指纹 956637e620…（第 23 轮，用户原话「确认」已留痕）· 89 行。
#   ⚠ 这次的关卡**不是**台账描述的那一版：台账 89 行记的 `StaticMeshActor_48…` 一个都不在关卡里，
#     关卡里是**另一版 34 个 Actor**（中文分组，含栅栏 / 路灯 / 车 —— 都不是本套代码摆的）。
#   · `dry_run=true` → 「要删 34 / 要摆 89 / 人工改过 0 / 台账解释不了的活 Actor 34」（干净）；
#   · 真跑 `mode="full", force_full=true, accept_user_edits=true` → **removed 34 / placed 89 /
#     verified 89 / `mismatches` 空**、7 个分组计数与指令表逐项相等；**全程未存盘**。
#   · **`official_calls 457`** —— 逐项对平（这是"调用次数"第一次拿到实测口径）：
#     `exists` 3（house1 / house2 / tree 三个不同路径）+ `get_current_level` 1
#     + 清点 `UEMCP/` 1 + **人工痕迹扫描 0**（台账 89 行引用全失效 → `_live_vs_ledger` 直接
#       `continue`，所以一分钱没花；这正是"扫描零成本 = 台账已整体失效"那个信号
#       → 2026-09-27 补了 A 项报警，见下面那条）
#     + 清场 34 + 落资产 39 + 落白膜 50×2 + `set_actor_folder` 89 + 读回 89
#     + 白膜尺寸复核 50×2 + 落完清点 1 = **457**。
#   · 由此改正两条口径：① **"指令表过期"不影响落盘**（`execute_build` 用 `_compose_build_rows`
#     按 plan 自己重算，从不读 `build_orders_v1.json`）—— 该表只是留痕件；
#     ② 每行固定开销 ≈ **4.5 次**调用（分组 1 + 读回 1 + 白膜尺寸 2），89 行光这一块就 400 上下
#     → 因此 2026-09-27 加了 `verify` 档位（`"transform"` 省下白膜尺寸那 100 次）。
# ⚠ 2026-09-27 增补（A / B / C 三项，**都尚未实测** —— 等用户跑 `tests/preflight.py` 与下一次真跑）：
#   · A：台账**整体失效**报警（`execute_build` 里按"台账里有几个 Actor 引用还活着"判，
#     全失效 / 大半失效各报一条）—— 补的正是上面那个"静默零成本"的洞；
#   · B：指令表指纹一致性提示（`execute_build` 与 `get_plan` 各一条，**只报不拦**）；
#   · C：`verify` 三档（`full` / `transform` / `off`）+ 台账**如实分档**记白膜尺寸
#     （读过记实测值 + `size_readback=true`；没读记指令值 + `size_readback=false`）
#     + 落完**按引用**核对分组并**就地补一次** `set_actor_folder`。
#   · C 里**没做**的一件：用官方 `ProgrammaticToolset` 把 89 次 `set_actor_folder` 合并成一次调用 ——
#     动手时 `describe_toolset` 对**所有**工具集都返回 `fetch failed`（实测），拿不到 `execute_tool`
#     的入参 schema，照猜写违反证据门槛。**链路恢复、schema 读得到之后再补。**
# ⚠ 2026-09-26 增量改造（**尚未实测** —— 等用户跑 `tests/preflight.py` / MCP 真跑补证据）：
#   · 每行新增稳定身份 `uid`（`element_key`|`label`）、新增台账 `views/build_state_v1.json`；
#   · `execute_build` 新增 `mode`（full / incremental）与 `adopt`；
#   · `check_build_target` 的 `our_actor_count` 由"根 + 各子文件夹递归相加"（实测正好 2 倍：
#     根里 89 个报成 178）改成**只数根一次**；
#   · 上面两条 [完工-14] 的实测记录针对的是**全量**路径 —— 全量逻辑本身没改
#     （只是落完之后多写一次台账、并且现在按标签认领而不是靠行号）。


# --- 阶段三 · 增量搭建的对账件（2026-09-26；用户要求：局部改动只重建改动处）-------  【模块：state】
# 用户原话：「关于已经大搭建好的，如果用户需要局部更改就继续回去改图，然后再让用户确认，
#   再生成需要改的地方而不是全部重新搭建」。
# 两个前提事实（2026-09-26 实测，不是推断）：
#   ① `add_to_scene_from_asset(name=...)` 传的名字落在 **Actor 的 label（显示名）** 上，
#      对象名是 UE 自动编号（`StaticMeshActor_48` / `Actor_25`）——
#      所以"认领"要靠 `get_label`，不是靠 refPath 猜；
#   ② 曾经把"根文件夹递归"与"每个子文件夹递归"相加当总数 → **2 倍**（根 89 报成 178）。
# 增量靠 `uid`（= `element_key`|`label`）把"指令表一行"与"台账一行"对齐；台账里存 Actor 引用。


def _row_uid(element_key: str, label: str) -> str:
    """一行指令的**稳定身份**（`element_key` + `label`）—— 增量对账全靠它。

    为什么不用 `index`：`index` 是行号，**中间插一行会让后面所有行的号码平移**，
    按号码对账会把"新加了一行"错认成"后面每一行都变了"。
    label 是阶段二要求"同类多个要能区分"的，天然唯一；element_key 再兜一层。
    ⚠ label 是用户可见、可改的：他要是把某行改名，这一版就会被当成"删一行 + 加一行" ——
      这是**如实行为**（不猜），报告里会点名。
    """
    return f"{element_key or '-'}|{label or '-'}"


def _load_build_state() -> dict:
    """读搭建台账；没有 / 格式不对都返回 `{}`（当"没有基线"处理，拒不拒收由调用方定）。"""
    doc = load_json(BUILD_STATE_PATH)
    return doc if isinstance(doc, dict) else {}


def _save_build_state(level: str, plan_hash: str, how: str, ledger_rows: list[dict]) -> str:
    """写搭建台账（**我们自己的文件，不碰 UE**）。`how` = full / incremental / adopt。

    ⚠ **写之前先把上一版移进 `views/archive/`**（2026-09-30 加）：以前这里是**覆盖写**，
      于是「这一整套操作到底用过什么模式」**事后无法从磁盘审计** —— 用户问
      「有没有全删全摆过」时磁盘上查不到，只能翻会话记录（实测就这样被问住一次）。
      留档名 `build_state_<UTC 时间戳>.json`，与 plan 的留档同一套路。
      ⚠ 留档失败**不拦**本次落盘（把台账写下去比留档重要），只记一条日志。
    """
    try:
        if BUILD_STATE_PATH.exists():
            _ar = BUILD_STATE_PATH.parent / "archive"
            _ar.mkdir(parents=True, exist_ok=True)
            _stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(BUILD_STATE_PATH, _ar / f"build_state_{_stamp}.json")
    except OSError as exc:                  # noqa: BLE001 —— 留档失败不该拦住落盘
        logging.getLogger(__name__).warning("搭建台账留档失败（不影响本次落盘）：%s", exc)
    save_json(BUILD_STATE_PATH, {
        "stage": "阶段三 · 搭建台账（我们自己的文件，不是 UE 资产；里面没有任何关卡存盘）",
        "level": level,
        "plan_hash": plan_hash,
        "how": how,
        "at": datetime.now(timezone.utc).isoformat(),
        "unit": "cm",
        "rows": ledger_rows,
        "note": ("每一行 = 指令表的一行，靠 `uid`（element_key|label）对齐。"
                 "`readback=true` 表示 loc/yaw/scale 是**从关卡读回来的**；"
                 "`false` 表示记的是**指令值**、没核实过。"),
    })
    return str(BUILD_STATE_PATH)


# --- 阶段三 · 「搭哪张图」的答复台账与闸（2026-09-26 加；**尚未实测** —— 等 preflight / 真跑补证据）--  【模块：state】
# 客户的 agent 使用后自评里点名的软约束之一：「连上 MCP 应该先看完所有工具参数 —— 我走一步查一步」，
# 以及更贵的那条：「用户手动改的东西不该删 —— 我 full 重摆给删了」。
# 前者在代码里的具体表现就是：`check_build_target` 是第 1 步，但**没有任何代码核对它跑没跑**；
# 后者是：落关卡前**从没读过关卡现状**，于是"手改过的东西"既没人保护、也没人报。
# 下面这几个函数就是把这两条从文档搬进代码。


def _load_build_target() -> dict:
    """读「搭哪张图」的答复台账；没有 / 格式不对都返回 `{}`（当"还没问过"处理）。"""
    doc = load_json(BUILD_TARGET_PATH)
    return doc if isinstance(doc, dict) else {}


def _save_build_target(asked_level: str, answer: dict | None = None) -> str:
    """写「搭哪张图」的答复台账（**我们自己的 JSON，不是 UE 存盘、不碰关卡**）。

    ⚠ 只记「问的时候 UE 开着哪张图 / 用户怎么答的」—— 不改关卡、不摆 Actor。
    ⚠ **"已经答复过"的答复不许被下一次"只问一句"擦掉**（这次 `answer=None`）：
      否则 agent 顺手再问一遍现场情况（`our_actor_count` 之类），
      刚留痕的答复就没了、`execute_build` 会莫名被拒 —— 这就是"记的东西被自己抹掉"。
      「问题问的是哪张图」（`asked_at` / `asked_level`）同样只在**还没有答复**时才重记：
      它是"用户选的是开新图，而他到底换图没有"的凭据，换掉了就判断不出来。
    """
    old = _load_build_target()
    old_answer = old.get("answer") if isinstance(old.get("answer"), dict) else None
    keep_ask = bool(old.get("asked_at")) and (answer is not None or old_answer is not None)
    doc = {
        "stage": "阶段三 · 「搭哪张图」的答复台账（我们自己的文件，非 UE 存盘）",
        "asked_at": (str(old.get("asked_at") or "") if keep_ask
                     else datetime.now(timezone.utc).isoformat()),
        "asked_level": (str(old.get("asked_level") or "") if keep_ask else str(asked_level or "")),
        "answer": answer if answer is not None else old_answer,
        "at": datetime.now(timezone.utc).isoformat(),
        "note": ("`asked_*` = 问的时候 UE 开着哪张图；`answer.decision` 三个取值："
                 "current（就用当前这张）/ existing（用户指定的既有图，路径在 answer.level）/ "
                 "new（开新图，用户自己建）。`execute_build` 拿它与当前关卡核对 —— 对不上不搭。"),
    }
    save_json(BUILD_TARGET_PATH, doc)
    return str(BUILD_TARGET_PATH)


_DECISION_ALIASES = {
    "current": "current", "当前": "current", "当前图": "current",
    "就用当前这张": "current", "就用当前这张图": "current",
    "existing": "existing", "既有": "existing", "既有图": "existing", "指定": "existing",
    "new": "new", "新图": "new", "开新图": "new", "新建": "new",
}


def _norm_decision(raw: str) -> str:
    """把用户的答复（中英文都接）归一成 `current` / `existing` / `new`；认不出就抛。

    ⚠ 只认这三个词，**不猜** —— 「随便」「你看着办」换不出目标图，那种答复要回去问清楚：
      挑哪张图是**用户的决定**（AGENTS：不许替用户做决定）。
    """
    key = str(raw or "").strip().lower()
    if key in _DECISION_ALIASES:
        return _DECISION_ALIASES[key]
    for alias, val in _DECISION_ALIASES.items():        # 容 "① 开新图" / "③ 就用当前这张" 这种写法
        if alias and key.endswith(alias):
            return val
    raise ToolError(
        f"`decision` 认不出：`{raw}` —— 只认三样：`current`（就用当前这张）/ "
        "`existing`（用他指定的既有图，同时给 `target_level`）/ `new`（开新图，他自己建）。"
        "用户要是没说清搭哪张图，就再问他一次；**不许替他挑**。")


def _same_level(a: str, b: str) -> bool:
    """两个关卡路径算不算**同一张图**。

    为什么要它：`get_current_level` 返回的是**包路径**（形如 `/Game/<目录>/<关卡>`），
    而用户/文档里常常写成 UE 的"包.对象"写法（`/Game/<目录>/<关卡>.<关卡>`）——
    只做**这一种**写法归一（不做大小写/前缀乱配），免得把"写法不同"误判成"搭错图"。
    """
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return a == b.split(".", 1)[0] or b == a.split(".", 1)[0]
    except (AttributeError, IndexError):
        return False


def _build_target_guard(level: str) -> None:
    """**「先问清搭哪张图」这道闸**（2026-09-26 加，把软约束变硬）。

    谁调它：`execute_build()` —— 拿到当前关卡之后、**动任何 Actor 之前**。
    判据：`views/build_target_v1.json` 里要有一份**用户答复过的**目标，且与当前这张图对得上：
      · `current`  —— 答复时那张图必须就是现在这张（用户中途换了图 = 那次答复不再指这张）；
      · `existing` —— 用户指定的路径必须就是现在这张（还没切过去 → 不许搭）；
      · `new`      —— 现在这张**不能**还是答复当时那张（说明他还没把新图建好/打开）。
    为什么要有它：`check_build_target` 是阶段三第 1 步，可代码里从来没核对过它跑没跑 ——
      「拿到用户答复前一个 Actor 都不许放」原先只写在工具描述与 docs 里（软约束）。
    ⚠ 台账没答复 / 内容不认 → **照拦**（与 `_readback_guard` 的"基础设施故障就放行"相反）：
      这道闸挡的是"往用户的图里放东西"，拿不准就该停下问人 —— **放行才是会造成损失的那一边**。
    """
    doc = _load_build_target()
    ans = doc.get("answer") if isinstance(doc.get("answer"), dict) else {}
    if not ans:
        raise ToolError(
            "拒收：**还没拿到用户对『搭哪张图』的答复** —— 一个 Actor 都没动。\n"
            "正确顺序：① `check_build_target()`（先只读地问一遍：当前关卡 / 可选的图 / "
            f"`{OUR_FOLDER_ROOT}/` 下有没有旧东西）→ ② **把问题交给用户、停下等他打字** → "
            "③ 带他的原话再调一次 `check_build_target(decision=…, user_quote=…)`（只记答复，仍不碰关卡）"
            "→ ④ 才轮到 `execute_build()`。\n"
            "（为什么拦：官方**没有「新建关卡」的工具**，选「开新图」得用户自己在 UE 里建 —— "
            "不问就搭，十有八九搭在他不想要的那张图上。这条以前只是文档里的软约束。）"
        )
    dec = str(ans.get("decision") or "")
    if dec not in ("current", "existing", "new"):
        raise ToolError(
            f"拒收：答复台账里的 `answer.decision` 不认：`{dec}`（`{BUILD_TARGET_PATH}`）—— "
            "一个 Actor 都没动。请重新走一遍 `check_build_target(decision=…, user_quote=…)`。"
        )
    asked = str(doc.get("asked_level") or "")
    answered = str(ans.get("level") or "")
    if dec == "new" and asked and _same_level(level, asked):
        raise ToolError(
            f"拒收：用户选的是**开新图**，但当前关卡还是当初问他的那张 `{asked}` —— "
            "他还没把新图建好/打开（官方没有新建关卡的工具，这一步只能他做）—— **一个 Actor 都没动**。\n"
            "请他新建并打开目标图，再调 `execute_build()`。"
        )
    if dec == "existing" and answered and not _same_level(level, answered):
        raise ToolError(
            f"拒收：用户指定的是 `{answered}`，当前关卡却是 `{level}` —— **还没切过去**，"
            "**一个 Actor 都没动**。请先在 UE 里切到那张图（或明确让我用官方 `load_level` 切），再搭。"
        )
    if dec == "current" and answered and not _same_level(level, answered):
        raise ToolError(
            f"拒收：用户当时选的是『就用当前这张』= `{answered}`，现在开着的是 `{level}` —— "
            "**对不上了**（有人换过图），那次答复不再指这张图 —— **一个 Actor 都没动**。\n"
            "请重新问一次：`check_build_target()` → 用户答复 → `check_build_target(decision=…)`。"
        )


# --- 阶段三 · 「人工痕迹」的**两步闸**（2026-09-30 加，用户要求；**尚未实测**）------------  【模块：state】
# 起因（**同类事故第二次**）：外部 agent 收到"有 N 处人工痕迹会被这次操作删掉"的拒收后，
#   **自己**传了 `accept_user_edits=true`，把用户手拖过的水面（差 30 cm）覆盖掉了 —— 全程没问过人。
#   当天早些时候的第一次加固只加了 `user_quote`：它至少得拿出一句话。但那仍是**一次调用里说完的事**。
# 现在拆成两步（与 `check_build_target` 同构）：
#   ① 第一次调（不带覆盖开关）→ 代码**记下"我问了哪几行"**再拒收（`views/user_edits_v1.json`）；
#   ② 拿到用户答复后再调 → 核对「问过的那批 == 现在这批」+ 有他本人的原话 + 距提问够久 → 才放行。
# ⚠ **边界说清**：这**拦不住"存心等够时间再编一句话"**（代码验不了真话）。它做到的是
#   **不能悄悄干**，且台账里留下「问过哪几行 + 那句话原文」，事后可以当场对质。

def _load_user_edits() -> dict:
    """读「人工痕迹」问答台账；没有 / 格式不对都返回 `{}`（当"还没问过"处理）。"""
    doc = load_json(USER_EDITS_PATH)
    return doc if isinstance(doc, dict) else {}


def _traces_key(traces: list) -> list[str]:
    """这批人工痕迹的**身份** = 排好序的 label 列表（判"问过的那批"与"现在这批"是不是同一批）。"""
    rows = [str(t.get("label") or "") for t in (traces or []) if isinstance(t, dict)]
    return sorted(x for x in rows if x)


def _save_user_edits_question(traces: list, level: str, mode: str) -> str:
    """**第 1 步**：记下「问用户的是哪几行人工痕迹」（**不碰关卡、不改 plan**）。返回台账路径。"""
    rows = _traces_key(traces)
    doc = {
        "stage": "阶段三 · 「人工痕迹」问答台账（我们自己的文件，非 UE 存盘）",
        "asked_at": datetime.now(timezone.utc).isoformat(),
        "asked_level": str(level or ""),
        "asked_mode": str(mode or ""),
        "asked_rows": rows,
        "asked_count": len(rows),
        "answer": None,
        "history": list(_load_user_edits().get("history") or []),
        "note": ("`asked_rows` = 问用户时逐行列出的那几行（关卡现状与台账不符 / 台账解释不了的 Actor）；"
                 "`answer` = 他的答复（`decision` = overwrite + `quote` 原话 + `at`）。"
                 "`execute_build` 要按 plan 覆盖之前会核对：**没问过 / 问过的那批与现在不一致 / "
                 f"距提问不到 {MIN_USER_EDITS_ANSWER_DELAY_S:g} 秒 → 拒收**（一个 Actor 都不动）。"
                 "覆盖真跑成功之后这道问答被移进 `history`（下一批要重新问）。"),
    }
    save_json(USER_EDITS_PATH, doc)
    return str(USER_EDITS_PATH)


def _user_edits_gate(traces: list, level: str, mode: str, user_quote: str) -> str:
    """**第 2 步**：要按 plan 覆盖这些人工痕迹之前先过这道闸。过不了抛 `ToolError`（= 拒收，一个 Actor 都不动）。

    三条判据都要过：
      ① **问过**：台账里有 `asked_at`（第 1 步真的跑过）—— 没问过直接要覆盖 = 拒收；
      ② **问的就是现在这批**：`asked_rows == 现在这批的 label 集合`（多一行 / 少一行都要重问）；
      ③ **有他本人的原话**，且**距提问 ≥ `MIN_USER_EDITS_ANSWER_DELAY_S` 秒**（问完立刻自己答复 = 没人看过）。
    """
    quote = str(user_quote or "").strip()
    doc = _load_user_edits()
    if not str(doc.get("asked_at") or ""):
        raise ToolError(
            "拒绝搭建：**你还没把这份人工痕迹清单问过用户**（或上一批问答已经用掉了）—— "
            "一个 Actor 都没动。\n"
            "正确顺序（**两步，别合成一步**）：① 先按**默认参数**（不带 `accept_user_edits`）调一次 —— "
            "它会把清单逐行列出来、**拒绝执行**，同时留痕「你问的是哪几行」；"
            "② **把那份清单原样交给用户、停下等他打字**；"
            "③ 拿到他的原话再调 `execute_build(accept_user_edits=true, user_quote=…)`。\n"
            "（为什么拦：这是同类事故第二次 —— 上一次外部 agent 收到拒收后**自己**传了 `true`，"
            "把用户手拖过的东西覆盖掉了，全程没问过人。）"
        )
    asked = [str(x) for x in (doc.get("asked_rows") or [])]
    now = _traces_key(traces)
    if asked != now:
        extra = [x for x in now if x not in asked]
        gone = [x for x in asked if x not in now]
        raise ToolError(
            "拒绝搭建：**你问过的那批人工痕迹，与现在这批对不上** —— 一个 Actor 都没动。\n"
            f"· 当初问的 {len(asked)} 行 / 现在 {len(now)} 行"
            + (f"\n· **现在多出来的**（他没看过）：{'、'.join(extra[:8])}" if extra else "")
            + (f"\n· **当初有、现在没了**：{'、'.join(gone[:8])}" if gone else "")
            + "\n把**现在这份清单**重新交给用户、拿他的答复再来"
              "（口径：问过的那批必须就是现在这批 —— 多一行少一行都说明他没看全）。"
        )
    if not quote:
        raise ToolError(
            "拒绝搭建：要给 `user_quote`（**用户本人的原话**，例：「那是我改的，按 plan 覆盖吧」）—— "
            "一个 Actor 都没动。⚠ **不许自己编**（编了就是伪造人的确认）；拿不出原话就说明你还没问他。"
        )
    waited: float | None = None
    try:
        asked_ts = datetime.fromisoformat(str(doc.get("asked_at") or ""))
        waited = (datetime.now(timezone.utc) - asked_ts).total_seconds()
    except (TypeError, ValueError):
        waited = None
    if waited is not None and waited < MIN_USER_EDITS_ANSWER_DELAY_S:
        raise ToolError(
            f"拒绝搭建：你把清单交出去才 **{waited:.0f} 秒**，就带着『原话』回来了 —— "
            f"用户不可能已经看完（这道下限是 {MIN_USER_EDITS_ANSWER_DELAY_S:g} 秒）—— 一个 Actor 都没动。\n"
            "正确顺序：**先调一次（不带覆盖开关）拿到清单 → 把清单原样交给用户 → 停下等他打字 → "
            "再带他的原话回来**。"
        )
    return quote


def _mark_user_edits_answered(quote: str) -> str:
    """覆盖真跑成功后：把这次问答**移进 `history` 并清掉待答状态**（下一批人工痕迹要重新问一次）。"""
    doc = _load_user_edits()
    hist = list(doc.get("history") or [])
    hist.append({
        "asked_at": str(doc.get("asked_at") or ""),
        "asked_level": str(doc.get("asked_level") or ""),
        "asked_mode": str(doc.get("asked_mode") or ""),
        "asked_rows": list(doc.get("asked_rows") or []),
        "decision": "overwrite",
        "quote": str(quote or ""),
        "at": datetime.now(timezone.utc).isoformat(),
    })
    doc.update({"asked_at": "", "asked_level": "", "asked_mode": "", "asked_rows": [],
                "asked_count": 0, "answer": None, "history": hist,
                "note": "上一批问答已用掉（在 `history` 里）—— 下一批人工痕迹要重新走两步。"})
    save_json(USER_EDITS_PATH, doc)
    return str(USER_EDITS_PATH)


def _xform_of(got: Any) -> tuple[list[float] | None, float | None, list[float] | None]:
    """官方 `get_actor_transform` 的返回 → `(loc_cm, yaw, scale)`；解析不了给 `(None, None, None)`。"""
    doc = got if isinstance(got, dict) else {}
    loc = doc.get("location") or {}
    rot = doc.get("rotation") or {}
    scl = doc.get("scale") or {}
    try:
        return (
            [num(float(loc.get("x", 0.0))), num(float(loc.get("y", 0.0))),
             num(float(loc.get("z", 0.0)))],
            num(float(rot.get("yaw", 0.0))),
            [num(float(scl.get("x", 0.0))), num(float(scl.get("y", 0.0))),
             num(float(scl.get("z", 0.0)))],
        )
    except (TypeError, ValueError):
        return (None, None, None)


def _loc_close(a: Any, b: Any, tol: float = 0.05) -> bool:
    """两个坐标是不是同一个（厘米，容差 0.05 = 与读回对账同一把尺子）。"""
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
            and len(a) == 3 and len(b) == 3):
        return False
    try:
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    except (TypeError, ValueError):
        return False


def _scale_close(a: Any, b: Any, tol: float = 1e-4) -> bool:
    """两个缩放是不是同一个（与读回对账同一把尺子）。"""
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
            and len(a) == 3 and len(b) == 3):
        return False
    try:
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    except (TypeError, ValueError):
        return False


async def _read_cube_size_cm(
    ctx: Context[AppContext], actor_ref: str
) -> tuple[list[float] | None, int]:
    """读白膜 Actor 上那个 cube 组件的**实际尺寸**（厘米）→ `(尺寸 或 None, 调了几次官方)`。

    ⚠ **为什么非要读它（2026-09-26 动手前实测出来的一个漏洞，别删这段）**：
      白膜的尺寸**不在 Actor 变换里** —— `add_cube` 把它写在组件的 `RelativeScale3D` 上
      （基础方块 100 cm；实测 `Actor_25.cube` = {x:120, y:8, z:0.15} → 12000×800×15 cm，
      正好是「沥青车行道 120×8 m」那条），而官方 `get_actor_bounds` 对这类 Actor
      恒返回 ±128 的**假包围盒**（实测，见 docs/阶段三 §11.1）。
      于是「认领现状」（adopt）若拿**当前指令表**里的尺寸当基线，就会把「我以为它多大」
      当成「它真的多大」：用户刚把五条连接路加宽到 4 m，adopt 却记成"已经是 4 m"，
      增量比对判它**没变** → **那五条路永远摆不下去**（静默失效，比报错坏得多）。
      实测佐证：`Actor_33.cube`（连接路 #1）= {x:2, y:36} —— 关卡里**确实还是 2 m**。

    读不到就给 `None`：调用方据此记 `null`，让下一次比对**必判"尺寸变了"→ 重摆**
    （安全侧：宁可多重摆一行，也不许把用户刚改的东西静默跳过）。
    """
    used = 0
    try:
        comps = await call_official(
            ctx, "get_components", {"actor": {"refPath": actor_ref}}, toolset=TS_ACTOR)
        used += 1
    except ToolError:
        return (None, used)
    target = ""
    for c in (comps or []):
        ref = _ref_path(c)
        if ref and ref.rsplit(".", 1)[-1] == "cube":     # add_cube 时给组件起的名字
            target = ref
            break
    if not target:                                        # 名字对不上就退一步：挑一个非默认组件
        for c in (comps or []):
            ref = _ref_path(c)
            if ref and "DefaultSceneRoot" not in ref and "Billboard" not in ref:
                target = ref
                break
    if not target:
        return (None, used)
    try:
        props = await call_official(
            ctx, "get_properties",
            {"instance": {"refPath": target}, "properties": ["RelativeScale3D"]},
            toolset=TS_OBJECT)
        used += 1
    except ToolError:
        return (None, used)
    doc = props
    if isinstance(doc, str):                              # 官方把属性包成 JSON 字符串（实测）
        try:
            doc = json.loads(doc)
        except (ValueError, TypeError):
            return (None, used)
    scl = doc.get("RelativeScale3D") if isinstance(doc, dict) else None
    if not isinstance(scl, dict):
        return (None, used)
    try:
        # 基础方块 100 cm：官方 add_cube 传 [100,200,50] 得到 RelativeScale3D=(1,2,0.5)（实测）
        base_cm = 100.0
        return ([num(float(scl.get("x", 0.0)) * base_cm),
                 num(float(scl.get("y", 0.0)) * base_cm),
                 num(float(scl.get("z", 0.0)) * base_cm)], used)
    except (TypeError, ValueError):
        return (None, used)


async def _live_vs_ledger(
    ctx: Context[AppContext],
    ledger_rows: list[dict],
    live_set: set[str],
    loc_tol: float = 0.5,
) -> tuple[list[dict], int]:
    """把**关卡现状**与台账逐行比一遍 → `([{uid,label,actor,kind,why,ledger}, …], 官方调用次数)`。

    为什么要有它（2026-09-26 加，客户 agent 自评里点名的那条「没帮到我」）：
      · `full` 会把 `UEMCP/` 下的 Actor **全部清掉** —— 用户手工挪过的那几个也在里面；
      · 增量虽然不碰"没变的行"，但它**压根没读过关卡现状** —— 于是"地皮厚度被人改过"这种事
        既没人发现、也没人报（原话：「它不会告诉我『用户手动改了地皮你别删』」、
        「incremental 对比不够智能，识别不出『地皮高度变了需要重贴』」）。
    判据：台账那一行的 `loc_cm / yaw / scale` vs 关卡**现在**的样子。
      `readback=false` 的行台账记的是**指令值** —— 拿它比仍然成立：它代表"我们上次让它变成的样子"。
      ⚠ **白膜行还要另外读组件尺寸**（`_read_cube_size_cm`）：厚薄 / 高度**不在 Actor 变换里**，
        这正是"地皮高度变了却没人知道"的根因。
      ⚠ 位置容差给 **0.5 cm**（不是读回对账那 0.05）：这里判的是"人手动挪过没有" ——
        人挪一下至少几厘米，而浮点回写的零头不该刷出假警报。
    只比"台账里有 Actor 引用、且那个 Actor 现在还在关卡里"的行；**读不回来就不判"改过"**（不猜）。
    """
    used = 0
    out: list[dict] = []
    for old in ledger_rows:
        actor = str(old.get("actor") or "")
        if not actor or actor not in live_set:
            continue
        used += 1
        try:
            got = await call_official(
                ctx, "get_actor_transform", {"actor": {"refPath": actor}}, toolset=TS_ACTOR)
        except ToolError:
            continue                       # 读不到就不判"改过"（宁可漏报，不编）
        loc, yaw, scale = _xform_of(got)
        why: list[str] = []
        if loc is not None and not _loc_close(loc, old.get("loc_cm"), tol=loc_tol):
            why.append(f"位置 {old.get('loc_cm')} → 现在 {loc}")
        if yaw is not None:
            try:
                if not _angle_close(yaw, float(old.get("yaw") or 0.0)):
                    why.append(f"yaw {old.get('yaw')} → 现在 {yaw}")
            except (TypeError, ValueError):
                pass
        if scale is not None and not _scale_close(scale, old.get("scale")):
            why.append(f"缩放 {old.get('scale')} → 现在 {scale}")
        if str(old.get("kind") or "") == "whitebox":
            dim, cost = await _read_cube_size_cm(ctx, actor)
            used += cost
            want = old.get("size_cm")
            if (isinstance(dim, list) and len(dim) == 3
                    and isinstance(want, (list, tuple)) and len(want) == 3):
                if [round(float(x), 2) for x in dim] != [round(float(x), 2) for x in want]:
                    why.append(f"白膜尺寸（厚薄 / 高宽）{list(want)} → 现在 {dim}")
        if why:
            out.append({
                "uid": str(old.get("uid") or ""),
                "label": str(old.get("label") or ""),
                "actor": actor,
                "kind": str(old.get("kind") or ""),
                "why": why,
                "ledger": old,
            })
    return out, used


def _row_changes(order: dict, old: dict) -> list[str]:
    """指令表的这一行 vs 台账里的那一行 —— 变了没有？变了就**逐条说清变在哪**。"""
    why: list[str] = []
    if str(order.get("asset_path") or "") != str(old.get("asset_path") or ""):
        why.append(f"资产路径 {old.get('asset_path') or '（空）'} → {order.get('asset_path') or '（空）'}")
    if (order.get("size_cm") or None) != (old.get("size_cm") or None):
        # ⚠ 第 3 维就是**厚薄 / 高度**（plan 的 `height_m` → 白膜 cube 的 Z）。
        #   客户 agent 自评里点名「incremental 对比不够智能，识别不出『地皮高度变了需要重贴』」——
        #   尺寸变了本来就判得出；这里额外**把话说清楚**，免得差异明细里一行数字看不出是高度。
        note = ""
        try:
            oz = float((old.get("size_cm") or [None, None, None])[2])
            nz = float((order.get("size_cm") or [None, None, None])[2])
            if abs(oz - nz) > 1e-6:
                note = f"（第 3 维 = 厚薄 / 高度变了：{oz:g} → {nz:g} cm —— 这一行必须重贴）"
        except (TypeError, ValueError, IndexError):
            note = "（尺寸变了 —— 这一行必须重贴）"
        why.append(f"白膜尺寸 {old.get('size_cm')} → {order.get('size_cm')}{note}")
    if str(order.get("folder") or "") != str(old.get("folder") or ""):
        why.append(f"分组 {old.get('folder')} → {order.get('folder')}")
    if not _loc_close(order.get("loc_cm"), old.get("loc_cm")):
        why.append(f"位置 {old.get('loc_cm')} → {order.get('loc_cm')}")
    try:
        if not _angle_close(float((order.get("rot") or {}).get("yaw") or 0.0),
                            float(old.get("yaw") or 0.0)):
            why.append(f"yaw {old.get('yaw')} → {(order.get('rot') or {}).get('yaw')}")
    except (TypeError, ValueError):
        why.append("yaw 读不出来")
    if not _scale_close(order.get("scale"), old.get("scale")):
        # ⚠ 第 3 维就是**高度**（`RelativeScale3D` 的 Z；资产行现在还会乘 `scale_z`）——
        #   客户 agent 自评里点名「incremental 对比不够智能，识别不出『高度变了』」，
        #   所以这里额外把话说清楚：**高度变了的行只有立面图看得出来**（2026-09-30 加）。
        note = ""
        try:
            oz = float((old.get("scale") or [None, None, None])[2])
            nz = float((order.get("scale") or [None, None, None])[2])
            if abs(oz - nz) > 1e-6:
                note = f"（**Z 分量变了 = 高度变了**：{oz:g} → {nz:g}）"
        except (TypeError, ValueError, IndexError):
            note = ""
        why.append(f"缩放 {old.get('scale')} → {order.get('scale')}{note}")
    return why


def _ledger_row(order: dict, actor: str, loc: Any = None, yaw: Any = None,
                scale: Any = None, readback: bool = False) -> dict:
    """指令表一行 → 台账一行。

    `readback=False` 时 loc/yaw/scale 记的是**指令值**（没读回来核实过）—— 台账里带这个标记，
    免得以后把"我以为它在那儿"当成"它真在那儿"（硬规则 6 的同一条纪律）。
    """
    return {
        "uid": order["uid"], "index": order["index"], "label": order["label"],
        "name": order["name"], "kind": order["kind"], "element_key": order["element_key"],
        "asset_path": order["asset_path"], "size_cm": order.get("size_cm"),
        "folder": order["folder"], "actor": actor,
        "loc_cm": (list(loc) if (isinstance(loc, (list, tuple)) and len(loc) == 3)
                   else list(order["loc_cm"])),
        "yaw": (float(yaw) if isinstance(yaw, (int, float))
                else float((order.get("rot") or {}).get("yaw") or 0.0)),
        "scale": (list(scale) if (isinstance(scale, (list, tuple)) and len(scale) == 3)
                  else list(order["scale"])),
        "readback": bool(readback),
    }


def _compose_build_rows(plan: dict, asset_list: dict) -> tuple[list[dict], list[str], list[str]]:
    """把 `plan_v1.json` 翻成**搭建指令表**（纯计算，不碰 UE）。返回 `(rows, z_rules, warnings)`。

    翻译只做三件事，其余一律照抄 —— **坐标本身一个字都不许在这里改**：
      ① **米 → 厘米**：`cm = m × 100`，**只缩放不翻轴**（plan 写明与 UE 同为左手系 Z-up，
         X 前进 / Y 右 / Z 上 —— `world.coordinate_system`）；
      ② `rot_deg` → UE `yaw`：**不反号**（2026-09-26 实测两者同向，docs/阶段三 §3 的表）；
      ③ **补 Z**：plan 只有平面坐标 `pos: [X, Y]`，竖直方向的两条依据写在本文件顶部的
         `WHITEBOX_VERTICAL`（白膜）与 `ASSET_PIVOT_LIFT_CM`（原点不在底面的资产）里。

    顺序：**按五层搭建顺序重排**（`BUILD_LAYERS`：① 世界地基 → ② 地皮 → ③ 建筑层 →
    ④ 设施层 → ⑤ 植被层；2026-09-27 用户指令）。同层内保持 plan 的原序（资产行在前、白膜行在后）。
    plan 的 pos 坐标一个字都不动 —— 改的只是**落进 UE 的先后**。
    """
    warnings: list[str] = []
    # 材质表**每次调用都重新读**（配置文件改完立刻生效，不用重启）—— 见 `_surface_materials()`。
    # 读不动 / 没配置时退回内置默认（⚠ **默认是空的** → 那就是"什么都不贴"），
    # 并把原因写进 warnings（**不静默**）。
    _mats, _mats_note = _surface_materials()
    if _mats_note:
        warnings.append(_mats_note)

    # 白膜厚度兜底：阶段一清单里每个元素都登记过一个 `size_cm[2]`（车行道 15 cm、草坪 5 cm…）。
    # 为什么从清单取而不是在代码里写死：那是**登记过的**尺寸，代码写死就成了"我替用户估"。
    thickness_cm: dict[str, float] = {}
    for it in (asset_list.get("items") or []):
        if not isinstance(it, dict):
            continue
        key = str(it.get("element_key") or "").strip()
        size = it.get("size_cm")
        if key and key not in thickness_cm and isinstance(size, (list, tuple)) and len(size) >= 3:
            try:
                thickness_cm[key] = float(size[2])
            except (TypeError, ValueError):
                pass

    # 资产**实测包围盒**（按资产路径）—— 2026-09-30 加：画立面图要知道楼有多高、
    # 画平面图要知道它占多大（`get_plan().drawing`）。取的是阶段一那次 `get_bounds` 的实测值。
    bbox_by_path: dict[str, list[float]] = {}
    for it in (asset_list.get("items") or []):
        if not isinstance(it, dict):
            continue
        _p = str(it.get("asset_path") or "")
        _size = it.get("size_cm")
        if _p and _p not in bbox_by_path and isinstance(_size, (list, tuple)) and len(_size) >= 3:
            try:
                bbox_by_path[_p] = [float(_size[0]), float(_size[1]), float(_size[2])]
            except (TypeError, ValueError):
                pass

    rows: list[dict] = []
    index = 0

    # ---------- ① 已有资产：占地与坐标都是阶段二确认过的 ----------
    for a in (plan.get("assets") or []):
        index += 1
        key = str(a.get("element_key") or "")
        label = str(a.get("label") or key)
        path = str(a.get("asset_path") or "")
        pos = list(a.get("pos") or [0.0, 0.0])
        scale = float(a.get("scale") or 1.0)
        # `scale_z`（**Z 方向倍率**，2026-09-30 加）：**只拉高 / 压低，占地一个数都不动** ——
        # 用户要的"楼别一样高、但街道布局不变"就靠它（改 `scale` 是整体缩放，占地会跟着变）。
        # 归一化在阶段二已经校验过（正的有限数）；这里再挡一次"读不出来"的情况，**不静默**。
        scale_z = 1.0
        if a.get("scale_z") is not None:
            try:
                _sz = float(a["scale_z"])
            except (TypeError, ValueError):
                _sz = 0.0
            if math.isfinite(_sz) and _sz > 0:
                scale_z = _sz
            else:
                warnings.append(
                    f"⚠ 资产「{label}」的 `scale_z` 读不出来（{a.get('scale_z')!r}）—— 按 1.0 处理。")
        yaw = float(a.get("rot_deg") or 0.0)
        note = str(a.get("note") or "")
        if abs(scale_z - 1.0) > 1e-9:
            note += (f" ｜ 阶段三：`scale_z` = {scale_z:g}（Z 方向倍率）→ RelativeScale3D = "
                     f"[{scale:g}, {scale:g}, {scale * scale_z:g}]（**占地仍是 {scale:g}**）")

        # Z：地面标高 + 「原点比底面高」的那一段（乘上这个资产的缩放）—— 见 ASSET_PIVOT_LIFT_CM
        lift_cm = ASSET_PIVOT_LIFT_CM.get(path, 0.0) * scale
        z_cm = GROUND_Z_M * 100.0 + lift_cm
        if a.get("z_m") is not None:
            # plan 显式给了**绝对标高**（米）→ 用它，覆盖上面按口径推的值（2026-09-30 加）
            # ⚠ 对**资产行**这个值是 **Actor 原点**的标高（资产的 `loc.z` 就是原点，不是中心）——
            #   原先这里写的是"中心绝对标高"，那是错的（2026-09-30 只改文案，坐标一个字没动）。
            z_cm = float(a["z_m"]) * 100.0
            note += (f" ｜ 阶段三：plan 显式给了 `z_m` = {float(a['z_m']):g} m"
                     f" → **原点**标高 {z_cm:.2f} cm（**覆盖**按口径推的值）")
        if lift_cm:
            note += (f" ｜ 阶段三补 Z：该资产原点比底面高 {ASSET_PIVOT_LIFT_CM[path]:g} cm"
                     f"（scale 1 实测）×{scale:g} = {lift_cm:.2f} cm，"
                     f"挪到地面 {GROUND_Z_M * 100:g} cm 之上 → 原点 z = {z_cm:.2f} cm")

        # **画图用的底 / 顶绝对标高 + 占位包围盒**（2026-09-30 加，供 `get_plan().drawing`）：
        # 立面图必须知道这两头；口径就在这一段里算（**同一处 Z 口径**，不让画图的人再推一遍）。
        # ⚠ 顶面 = 底面 + 阶段一实测包围盒的高 × 缩放 × `scale_z`；**取不到就给 None 并如实报**。
        _bb = bbox_by_path.get(path)
        bbox_cm = ([num(_bb[0] * scale), num(_bb[1] * scale), num(_bb[2] * scale * scale_z)]
                   if _bb else None)
        z_base_cm = z_cm - lift_cm
        if bbox_cm:
            z_top_cm: float | None = z_base_cm + float(bbox_cm[2])
        else:
            z_top_cm = None
            warnings.append(
                f"⚠ 画图几何：资产「{label}」在资产清单里找不到实测包围盒（{path}）—— "
                "这一行给不出 `z_top`（立面图画不出它的顶）。**不拿别的数顶替**，去阶段一清单核对。")

        rows.append({
            "index": index, "label": label,
            "uid": _row_uid(key, label),
            "name": _safe_actor_name(label, index, key),
            "kind": "asset", "element_key": key, "asset_path": path, "primitive": "",
            "loc_cm": [num(pos[0] * 100.0), num(pos[1] * 100.0), num(z_cm)],
            "rot": {"pitch": 0.0, "yaw": num(yaw), "roll": 0.0},
            "scale": [num(scale), num(scale), num(scale * scale_z)],
            "size_cm": None,
            "bbox_cm": bbox_cm,
            "z_base_cm": num(z_base_cm),
            "z_top_cm": (num(z_top_cm) if z_top_cm is not None else None),
            "folder": FOLDER_BY_ELEMENT.get(key, f"{OUR_FOLDER_ROOT}/{key or 'misc'}"),
            "surface_material_hint": "",
            "note": note,
        })

    # ---------- ② 白膜：形状 / 占地 / 高度都是阶段二确认过的 ----------
    for w in (plan.get("whiteboxes") or []):
        index += 1
        key = str(w.get("element_key") or "")
        label = str(w.get("label") or key)
        pos = list(w.get("pos") or [0.0, 0.0])
        fp = list(w.get("footprint_m") or [0.0, 0.0])
        yaw = float(w.get("rot_deg") or 0.0)
        note = str(w.get("note") or "")
        shape = str(w.get("shape") or "").strip().lower()

        # 高度：plan 的 `height_m`（cube 行）优先；plane 行没有，取阶段一登记的薄板厚度。
        if w.get("height_m") is not None:
            height_m = float(w["height_m"])
            how = "plan 的 height_m"
        else:
            thick = thickness_cm.get(key, DEFAULT_SLAB_THICKNESS_CM)
            height_m = thick / 100.0
            how = (f"阶段一清单 {key} 的 size_cm[2] = {thick:g} cm"
                   if key in thickness_cm else f"兜底 {DEFAULT_SLAB_THICKNESS_CM:g} cm（清单里没登记）")
            if key not in thickness_cm:
                warnings.append(f"白膜「{label}」：清单里没有 {key} 的 size_cm[2]，厚度用了兜底值。")

        # Z：贴顶还是贴底 —— 官方 add_cube 的方块**以 Actor 原点为中心**，所以这里算的是中心点。
        anchor, anchor_z = WHITEBOX_VERTICAL.get(key, ("bottom", GROUND_Z_M))
        if key not in WHITEBOX_VERTICAL:
            warnings.append(f"白膜「{label}」：element_key `{key}` 没有竖直口径，按"
                            f"『底面贴地面 {GROUND_Z_M:g} m』处理 —— 不对就说，改代码里的表。")
        z_m = (anchor_z - height_m / 2.0) if anchor == "top" else (anchor_z + height_m / 2.0)
        if w.get("z_m") is not None:
            # plan 显式给了**中心绝对标高**（米）→ 用它，覆盖上面按「贴顶 / 贴底」推的值（2026-09-30 加）
            z_m = float(w["z_m"])
            note += (f" ｜ 阶段三：plan 显式给了 `z_m` = {z_m:g} m → 中心标高 {z_m * 100:.2f} cm"
                     f"（**覆盖**『{anchor}』口径）")

        if shape == "plane":
            note += (f" ｜ 阶段三：官方 PrimitiveTools **没有 add_plane**，按口径压成极薄 cube"
                     f"（厚 {height_m * 100:g} cm，取自{how}）")
        elif shape != "cube":
            warnings.append(f"白膜「{label}」：plan 里的 shape 是 `{shape or '空'}`，"
                            "只认 cube / plane —— 按 cube 处理。")

        # 画图用的**底 / 顶绝对标高**（2026-09-30 加，供 `get_plan().drawing`）：
        # 官方 `add_cube` 的方块**以 Actor 原点为中心**（2026-09-26 实测）⇒
        # 底面 = 中心 − 高/2、顶面 = 中心 + 高/2 —— 立面图直接用这两头，不必自己推。
        size_cm_row = [num(fp[0] * 100.0), num(fp[1] * 100.0), num(height_m * 100.0)]
        rows.append({
            "index": index, "label": label,
            "uid": _row_uid(key, label),
            "name": _safe_actor_name(label, index, key),
            "kind": "whitebox", "element_key": key, "asset_path": "", "primitive": "cube",
            "loc_cm": [num(pos[0] * 100.0), num(pos[1] * 100.0), num(z_m * 100.0)],
            "rot": {"pitch": 0.0, "yaw": num(yaw), "roll": 0.0},
            "scale": [1.0, 1.0, 1.0],
            "size_cm": size_cm_row,
            "bbox_cm": list(size_cm_row),
            "z_base_cm": num(z_m * 100.0 - height_m * 100.0 / 2.0),
            "z_top_cm": num(z_m * 100.0 + height_m * 100.0 / 2.0),
            "folder": FOLDER_BY_ELEMENT.get(key, f"{OUR_FOLDER_ROOT}/{key or 'misc'}"),
            "surface_material_hint": _mats.get(key, ""),
            "note": note,
        })

    # ---------- ②b 排序：按**五层**重排行序（2026-09-27 用户指令）--------------------
    # 用户原话：「搭建顺序改为先搭建世界地基再到地皮，再到建筑层再到设施层，最后才是植被层」。
    # 实现：**稳定排序**，只按"层号"排；同层内保持上面那个流里的原序（资产行在前、白膜行在后）
    #   —— 所以它不会悄悄改动同层内的相对顺序，也不会给谁改名。
    # ⚠ `index` **排完再重新编号**：它是对账 / 报告里的行号，不能和实际落库顺序脱节。
    # ⚠ 只影响阶段三（指令表行序 + 落 Actor 的次序）；plan 的两张表一个字不动。
    # ⚠ Actor 名**不跟着 index 走**（`_safe_actor_name` 只用 label 生成，index 只在标签被洗空时兜底）——
    #   所以这次重排**不会让已有台账里的 Actor 名失配**（增量仍能按 label 认领）。
    layer_rank: dict[str, tuple[int, str]] = {}
    for _i, (_lname, _keys) in enumerate(BUILD_LAYERS):
        for _k in _keys:
            layer_rank[_k] = (_i, _lname)
    unlayered: list[str] = []
    for r in rows:
        rank = layer_rank.get(r["element_key"])
        if rank is None:
            rank = (len(BUILD_LAYERS), "（未分层）")
            unlayered.append(r["label"])
        r["layer"] = rank[1]
        r["_layer_rank"] = rank[0]
    rows.sort(key=lambda r: r["_layer_rank"])       # Python 的 sort 是**稳定**的
    for _i, r in enumerate(rows, start=1):
        r["index"] = _i
        r.pop("_layer_rank", None)
    if unlayered:
        warnings.append(
            f"⚠ 有 {len(unlayered)} 行的 element_key **不在五层表里**（`BUILD_LAYERS`）—— "
            "它们被排到最后一层「（未分层）」：" + "、".join(unlayered[:8])
            + ("…" if len(unlayered) > 8 else "") + "。要归层就在代码里那张表加一行。")
    _layer_counts: dict[str, int] = {}
    for r in rows:
        _layer_counts[r["layer"]] = _layer_counts.get(r["layer"], 0) + 1
    warnings.append(
        "搭建顺序（五层，由下往上）："
        + "；".join(f"{_n} {_layer_counts.get(_n, 0)} 个" for _n, _ks in BUILD_LAYERS)
        + (f"；（未分层）{_layer_counts.get('（未分层）', 0)} 个" if unlayered else ""))

    # ---------- ③ 越界体检：**共用阶段二那一条检查**（只报、不拦）------------------------
    # ⚠ 2026-09-27 改（用户要求"完善自检"）：这里原先**自己算了一遍**（分"中心出界 / 白膜四角
    #   出界"两类），与阶段二的 `bounds_check()` 是**两套口径** —— 于是"哪些大类算越界、
    #   哪一级更要紧"两处会各说各话。现在**只留一个口径**：直接借阶段二那条检查
    #   （含硬 · 实体 / 软 · 铺装 / 豁免 · 边界元素三级），阶段三只负责把它带进报告。
    #   阶段三**不改坐标**（改坐标是阶段二的事），所以照旧**只报不拦**。
    try:
        _bounds_note = _planning_modules().bounds_check_text(plan)
    except Exception as exc:                    # noqa: BLE001 —— 检查跑不动不该把装配带崩
        _bounds_note = ""
        warnings.append(f"⚠ 越界体检没跑成（{type(exc).__name__}: {exc}）—— 这一项**没查**。")
    if _bounds_note:
        warnings.append(_bounds_note)

    z_rules = [
        f"路面标高 z = {ROAD_SURFACE_Z_M:g} m；两侧地面（人行道 / 草坪**顶面**）"
        f"比路面高 {GROUND_Z_M * 100:g} cm —— 依据：plan 的人行道/连接路白膜自带 height_m=0.15。",
        "白膜 cube：`add_cube` 的方块**以 Actor 原点为中心**（2026-09-26 实测）→ "
        "loc.z = 贴的那一头 ± 高度/2；贴哪一头见本文件 `WHITEBOX_VERTICAL`。",
        "⚠ 上面两条是**默认**：plan 的放置行若显式给了 `z_m`（**米，中心绝对标高**）→ "
        "**以它为准**、覆盖按口径推的值（2026-09-30 加；用来表达「用户把某一行手动挪高 / 挪低了」）。",
        f"已有资产：loc.z = {GROUND_Z_M * 100:g} cm（站在抬高的地面上）；"
        "原点不在底面的（见 `ASSET_PIVOT_LIFT_CM`）再加『高出的那段 × scale』。",
        "⚠ 上面几条是**口径**（依据写在常量旁），不是实测值 —— 觉得不对就说，改表里的数即可。",
    ]
    return rows, z_rules, warnings


def _drawing_geometry(plan: dict) -> dict:
    """给"画图的人"一份**可直接画**的逐行几何（含 Z）—— 出处是 `get_plan().drawing`。

    为什么要有它（用户 2026-09-30：「当调整的是高度时生成的图得是正视图或者左右视图」）：
      画立面图必须知道每一行的**底 / 顶绝对标高**，而 `plan_v1.json` 里只有平面坐标 `pos`
      加高度字段。原先"Z 从哪来"要靠画图的人自己按 `WHITEBOX_VERTICAL` / `ASSET_PIVOT_LIFT_CM`
      推 —— **推错就是两套口径**（图与数据对不上，而图是用户唯一的判断依据）。
      这里直接复用 `_compose_build_rows()`（阶段三落关卡用的**同一处** Z 口径）⇒ 一张图一个数据源。

    ⚠ **只读、不落盘、不进几何指纹**：它是 `get_plan()` 的返回值，不是 plan 的一部分 ——
      往 `plan_v1.json` 里写派生数据，会让"读一次状态"就把上一次确认无谓作废。
    ⚠ 取不到实测包围盒的行：`z_top_m` 给 `None` 并**如实写在报告里**（不拿平面数据冒充高度 ——
      与硬规则 6「预估值不许伪装成实测值」同源）。
    """
    asset_list = load_json(ASSET_LIST_PATH) or {}
    rows, z_rules, compose_warnings = _compose_build_rows(plan, asset_list)
    compose_warnings = list(compose_warnings)

    try:
        cs = _planning_modules().change_set(plan)
    except Exception as exc:                    # noqa: BLE001 —— 画图数据取不到不该把读工具带崩
        cs = {}
        compose_warnings.append(
            f"⚠ 画图几何：变更集没算出来（{type(exc).__name__}: {exc}）—— "
            "`changed` 这一列这次**没查**。")

    changed = set(str(x) for x in (cs.get("changed") or []))
    changed |= set(str(x) for x in (cs.get("added") or []))

    def _m(val, digits: int = 4):
        """厘米 → 米（取不到给 None）—— 画图数据一律用米，与 plan_v1.json 同口径。"""
        try:
            return round(float(val) / 100.0, digits)
        except (TypeError, ValueError):
            return None

    out_rows: list[dict] = []
    for r in rows:
        loc = list(r.get("loc_cm") or [0.0, 0.0, 0.0])
        bb = list(r.get("bbox_cm") or [None, None, None])
        out_rows.append({
            "label": str(r.get("label") or ""),
            "kind": str(r.get("kind") or ""),
            "element_key": str(r.get("element_key") or ""),
            "layer": str(r.get("layer") or ""),
            "x_m": _m(loc[0] if len(loc) > 0 else None),
            "y_m": _m(loc[1] if len(loc) > 1 else None),
            "z_base_m": _m(r.get("z_base_cm")),
            "z_top_m": _m(r.get("z_top_cm")),
            "w_m": _m(bb[0] if len(bb) > 0 else None),
            "d_m": _m(bb[1] if len(bb) > 1 else None),
            "h_m": _m(bb[2] if len(bb) > 2 else None),
            "rot_deg": float((r.get("rot") or {}).get("yaw") or 0.0),
            "changed": str(r.get("label") or "") in changed,
        })

    world = plan.get("world") or {}
    return {
        "unit": "m",
        "world": {"center": world.get("center"), "size": world.get("size"),
                  "bounds": world.get("bounds")},
        "coordinate_system": world.get("coordinate_system"),
        "required_views": list(cs.get("required_views") or ["top"]),
        "views_why": str(cs.get("views_why") or ""),
        "required_labels": list(cs.get("required_labels") or []),
        "changed_dims": list(cs.get("changed_dims") or []),
        "rows": out_rows,
        "removed_labels": list(cs.get("removed") or []),
        "z_rules": z_rules,
        "compose_warnings": compose_warnings,
        "note": (
            "画图**直接用这份数据**，别再自己推 Z：坐标口径与 `plan_v1.json` 一致"
            "（左手系 Z-up，X 前进 / Y 右 / Z 上，单位米）；`z_base_m` / `z_top_m` 是"
            "**绝对标高**（按阶段三**同一套** Z 口径算出来的），`w_m` / `d_m` / `h_m` 是"
            "**占位包围盒**（资产 = 阶段一实测包围盒 × 缩放）。"
            "⚠ **每一版都出两张图**（2026-09-30 用户定案）：**顶视图 = X-Y 平面**（俯视）、"
            "**原图视角正视图 = Y-Z 平面**（横轴 Y、纵轴 Z —— 因为本工程坐标系是 **X 前进**，"
            "`world.coordinate_system` 里写着，所以「原图视角」= 沿 X 看）。"
            "顶视图画 `x_m` / `y_m` + `w_m`×`d_m`（可画矩形 + 朝向）；"
            "正视图画 `y_m`（横）× `z_base_m` / `z_top_m`（纵）。"
            "⚠ 正视图是**立面展开图**（X 被压掉，不同 X 上的东西会叠在一起）："
            "给用户核对体量与高度的**示意图，不是严格投影**；`rot_deg` 的影响按轴对齐近似。"
            "本版图里要点名的行 = `required_labels`（其中 `changed=true` 的是本次动过的）。"
            "⚠ **两张图**里都要写**当前几何指纹前 10 位**，否则 `confirm_plan` 拒收。"),
    }


def _write_build_orders(payload: dict) -> str:
    """把指令表落盘到 `views/build_orders_v1.json`（**这不是 UE 资产，不违反"不许存盘"**）。"""
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_ORDERS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(BUILD_ORDERS_PATH)


def _counts_of(rows: list[dict]) -> dict:
    """行数对账：资产 / 白膜 / 合计 / 分元素 / 分分组。"""
    by_element: dict[str, int] = {}
    by_folder: dict[str, int] = {}
    for r in rows:
        by_element[r["element_key"]] = by_element.get(r["element_key"], 0) + 1
        by_folder[r["folder"]] = by_folder.get(r["folder"], 0) + 1
    return {
        "资产行": sum(1 for r in rows if r["kind"] == "asset"),
        "白膜行": sum(1 for r in rows if r["kind"] == "whitebox"),
        "合计": len(rows),
        "分元素": by_element,
        "分分组": by_folder,
    }


@mcp.tool()
async def generate_build_orders() -> BuildOrdersResult:
    """阶段三第 2 步（前半）：把确认过的**平面放置表翻译成 UE 能照做的搭建指令表**。
    ⚠ **编排内部件**：不是必经步骤 —— `execute_build()` 会自己按 plan 重算指令表；它的独立价值是**离线预览 + 留痕**（不碰 UE 就能看这一批会落成什么样）。

    读 `views/plan_v1.json`（米）+ `catalog/asset_list.json`（白膜厚度 / 材质线索），
    落盘 `views/build_orders_v1.json`（厘米），**一个 Actor 都不落、不碰关卡**。

    翻译与补全规则（每一条在代码里都有出处）：
      ① 单位：**米 ×100 → 厘米**，只缩放不翻轴（plan 写明与 UE 同为左手系 Z-up）；
      ② 旋转：`rot_deg` 直接当 UE `yaw`，**不反号**（2026-09-26 实测同向）；
      ③ **补 Z**：plan 没有竖直信息 —— 路面 z=0、两侧地面顶面 z=15 cm；白膜 cube 贴顶或贴底
         （`add_cube` 的方块以 Actor 原点为中心，实测）；原点不在底面的资产（树）要抬一段；
      ④ 白膜图元：官方 `PrimitiveTools` **没有 `add_plane`** → `plane` 一律压成极薄 cube；
      ⑤ 材质**一律不贴**，只把阶段一记的材质实例当 `surface_material_hint` 带出去（留给阶段五）。

    ⚠ 本工具**不拦没确认的 plan**（那是 `execute_build` 的闸）：它只翻译。
      但落关卡前 `execute_build` 会**重新**校验闸门与指纹，所以这张表过期了也落不进去。
    ⚠ `z_rules` 那几条 Z 口径是**推出来的口径、不是实测值** —— 觉得不对就说，改常量即可。
    """
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划数据（views/plan_v1.json）：先走完阶段二，再生成指令表。")
    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    if not (plan.get("assets") or plan.get("whiteboxes")):
        raise ToolError("plan_v1.json 是**初始化状态**（两张表都空）—— 没有坐标可翻译。")

    rows, z_rules, warns = _compose_build_rows(plan, load_json(ASSET_LIST_PATH) or {})
    plan_hash = planning.plan_geometry_hash(plan)
    counts = _counts_of(rows)

    path = _write_build_orders({
        "stage": "阶段三 · 搭建指令表（离线翻译件）",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "unit": "cm",
        "plan_hash": plan_hash,
        "world": plan.get("world") or {},
        "z_rules": z_rules,
        "counts": counts,
        "rows": rows,
        "warnings": warns,
        "note": "一物一行，**按五层搭建顺序**排（① 世界地基 → ② 地皮 → ③ 建筑层 → ④ 设施层 → "
                "⑤ 植被层；同层内保持 plan 原序，见每行的 `layer`）。"
                "loc_cm 由 plan 的 pos（米）×100 得来，坐标本身未做任何改动；"
                "补的只有 Z 与白膜图元 —— 依据见 z_rules。",
    })

    gate = planning.gate_check(plan)
    state = planning.acceptance_state(plan)
    extra: list[str] = []
    # 阶段三第 1 步（搭哪张图）有没有收到用户答复 —— 提前说，免得 agent 到 execute_build 才撞墙。
    _tgt = _load_build_target()
    if not (_tgt.get("answer") if isinstance(_tgt.get("answer"), dict) else {}):
        extra.append(
            "⚠ **还没拿到用户对「搭哪张图」的答复** —— `execute_build()` 会**拒收**。"
            "先 `check_build_target()`（只读地问：当前关卡 / 可选的图 / `UEMCP/` 下有没有旧东西）→ "
            "**把问题交给用户、停下等他打字** → 再带他的原话调 "
            "`check_build_target(decision=…, user_quote=…)` 把答复记下来。")
    if not gate["confirmed"]:
        extra.append("⚠ 这份 plan **还没被确认**（闸门关着）—— execute_build 会拒收。")
    elif not gate["plan_hash_ok"]:
        extra.append("⚠ 几何指纹对不上（plan_v1.json 被改过）—— 上次确认已作废，execute_build 会拒收。")
    if state != "accepted":
        extra.append(f"⚠ 阶段二验收状态是 `{state}`（不是 accepted）—— execute_build 会拒收。")

    return BuildOrdersResult(
        stage="阶段三 · 搭建指令表（离线翻译件）",
        data_path=path,
        plan_hash=plan_hash,
        world=plan.get("world") or {},
        counts=counts,
        z_rules=z_rules,
        rows=[BuildOrderRow(**r) for r in rows],
        warnings=warns + extra,
        next_step=(
            f"指令表已落盘（{len(rows)} 行）。**下一步调 `execute_build()`** —— 它会"
            "① 重新按当前 plan 算一遍（保证与几何一致）② 整批校验（闸门 / 指纹 / 路径 exists / "
            "行数 / 白膜自洽）③ 清掉 UEMCP/ 下的旧 Actor ④ 一次性把这一批全落进关卡 ⑤ 读回来对账。"
            "想先看不动手就传 `dry_run=true`。⚠ 全程**不存盘**。\n"
            "**局部改动别全量重摆**：关卡里已经搭好、这次只改了几行 → 用 "
            "`execute_build(mode=\"incremental\")`（只动与台账比变了的行）；"
            "还没有台账就先 `execute_build(mode=\"incremental\", adopt=true)` 认领现状。"
        ),
    )


def _verify_level(verify: Any) -> str:
    """把 `execute_build` 的 `verify` 参数归一成三档：`off` / `transform` / `full`。

    为什么给档位（2026-09-27 加；用户问「怎么调用了 400 多次」）：
      一次 89 行的**全量**真跑实测 **457 次**官方调用，逐项对平是 ——
        exists 3 + get_current_level 1 + 清点 1 + 人工痕迹扫描 **0** + 清场 34
        + 落资产 39 + 落白膜 50×2 + `set_actor_folder` 89 + 读回 89
        + **白膜尺寸复核 50×2 = 100** + 落完清点 1 = 457。
      其中那 **100 次**就是白膜尺寸复核（`get_components` + `get_properties`，每行 2 次）。
      只验位置/朝向（`verify="transform"`）就省下这 100 次。

    ⚠ **默认仍是 `full`**：别为了省调用把复核关掉 —— 那 100 次买的正是
      「位置对了、厚薄不对」这种错的发现能力（客户 agent 自评点名过的那条：
      「它只管按 plan 摆，不管摆的对不对」）。
    ⚠ 兼容旧口径：`True` = `full`、`False` = `off`（老调用一个字都不用改）。
    """
    if verify is True:
        return "full"
    if verify is False or verify is None:
        return "off"
    text = str(verify).strip().lower()
    if text in ("full", "true", "all", "size"):
        return "full"
    if text in ("transform", "xform", "pose"):
        return "transform"
    if text in ("off", "none", "false", ""):
        return "off"
    raise ToolError(
        f"`verify` 只认 true / false 或 'full' / 'transform' / 'off'，收到 {verify!r}。")


# --- execute_build 的分段私有函数（2026-09-27 结构优化第 3 步）------------------  【模块：build / verification】
# 为什么拆：这个工具原本是**一个 936 行的函数**（846 行主体），从"整批校验"一路写到"写台账 + 报表"，
#   读一遍要滑十几屏。**行为一个字没改**：只是把几段机械上自成一体的活儿挪成有名字的函数。
#
# ⚠ 本组函数**只被 execute_build 用**；它们真的会动关卡（spawn / 删 / 改分组），
#   所以每一处官方调用都由调用方累加进 `official_calls` —— 口径见各函数返回的 `used`。
#   （这个"(值, used)"的写法是沿用本文件既有的约定，见 `_live_vs_ledger` / `_read_cube_size_cm`。）
#
# ⚠ **没搬出去**的两段（刻意留着，它们太依赖主体里的局部状态，搬出去得传十几个参数）：
#   ① 演练（`dry_run`）的差异报告；② 结尾那份 `BuildReport`。它们读起来本来就是"报表"。


@dataclass
class _LiveScan:
    """「当前在哪张图 + 关卡现状」清点结果（`_scan_live_level` 的产物）。"""

    level: str = ""                              # 当前关卡路径
    live_refs: list[str] = field(default_factory=list)   # `UEMCP/` 下现有 Actor 的引用
    ledger: dict = field(default_factory=dict)   # 搭建台账（我们自己的 JSON）
    used: int = 0                                # 这趟用掉几次官方调用


@dataclass
class _Precheck:
    """① 整批校验的产物（`_precheck_build` 的产物）。"""

    plan: dict = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)       # 按当前 plan **重算**出来的指令行
    warns: list[str] = field(default_factory=list)
    gate: dict = field(default_factory=dict)
    used: int = 0


# --- [完工-20] 「读过」与「对上」分开记 · ✅ 实测 2026-09-30T13:08Z（第 38 轮后的全量重摆）-------  【模块：verification】
# 实测证据（**正常路径**）：`execute_build(mode="full")` 重摆 26 行 →
#   台账里 **26/26 行带 `verified`、且全为 `true`**；`mismatch` 字段 **0 条**（全对上了、
#   按设计就不加它）；行键形如 `… readback, verified`（两个字段并列，语义分开）。
#   ⚠ 顺带踩到一个坑、记在这儿：**「用户说重启了」≠「进程吃到新代码」** —— 上一次跑（增量 1 行）
#     台账里 `verified` 是 **0/26**，靠这个"只有新代码才会产生的可观察差异"才查出进程还跑着旧代码；
#     重启之后同一件事变成 26/26。（不写进文档，用户用的是最终版、不会动源码。）
#   ⚠ **失败分支（`verified=false` + `mismatch`）仍未被执行** —— 工具自己摆、自己读，输入里
#     没有东西能让"指令 ≠ 实际"（见 `docs/阶段三-资产布局生成.md` 的"已知缺口"）。
#     这一半**不许当成验过**，它只是没被触发过。
# 修的是什么（用户 2026-09-30 点名要的那条"不含混报 PASS"）：`out.verified_uids.add()` 原先在
#   **比对之前**执行，于是**对不上的行也在里面**；而台账的 `readback` 正是拿它算的
#   （`_write_ledger_rows` 的 `ok_read`）→ 一行**验证失败**的记录在台账里照样写 `readback: true`
#   —— 读表的人会把它当成 PASS（`readback` 的中文语感和"验过了"没区别）。
# 现在（四处一起改）：① `verified_uids`（读过）/ `matched_uids`（对上了）分开；
#   ② 本轮落的台账行多带 `verified: bool`，对不上时再带 `mismatch: "差在哪"`；
#   ③ `mismatches` 非空时 `warnings` 里显式写「🔴 这不是 PASS（落成 N / 对上 M）」；
#   ④ `next_step` 把同一句排到**最前面**（增量档）。
# ⚠ **认领回来的 / 没动过的行不加这两个字段**（"原样保留"才是"没碰它"的凭据）——
#   读表的人按「**没有 `verified` 字段 = 这一行不是本轮落的、本轮没验过**」理解，别当成验过了。


@dataclass
class _VerifyOutcome:
    """③ 读回对账的结果（`_verify_rows` 的产物）。

    ⚠ **"读了回" ≠ "对上了"**（2026-09-30 分开记，见 `[完工-20]`）—— 两个集合都要留着：
      · `verified_uids` = **真的读回来过**（读失败 / 解析不了的行**不在**里面）；
      · `matched_uids`  = **读回来而且与指令一致**（对不上的行**不在**里面，原因在 `bad_by_uid`）。
    以前只有一个集合，于是台账里 `readback=true` 既能表示"读过"、又会被读成"验过了"。
    """

    mismatches: list[str] = field(default_factory=list)
    verified: int = 0
    verified_uids: set[str] = field(default_factory=set)  # **真的读回来过**的行
    matched_uids: set[str] = field(default_factory=set)   # **读回来且对上了**的行
    bad_by_uid: dict = field(default_factory=dict)        # uid → 差在哪（只装对不上的行）
    size_back: dict = field(default_factory=dict)         # 白膜行**实测**尺寸（只有 full 档才有）
    used: int = 0


async def _scan_live_level(
    ctx: Context[AppContext], mode: str, dry_run: bool, adopt: bool, warns: list[str]
) -> _LiveScan:
    """读当前关卡 + 数 `UEMCP/` 下的 Actor + 读台账；中间**过两道闸**。

    为什么两道闸放在这儿：它们都要先知道"当前是哪张图"才谈得上拦，
      而且必须在**动任何 Actor 之前**拦住。
      · **「搭哪张图」闸**（2026-09-26）：挡"搭错图"。演练与 `adopt` 不拦（它们不碰关卡）；
      · **开场动作闸**（2026-09-26）：`official_status()` + `get_asset_list()` 调过没有
        —— 客户 agent 自评里承认这两条他都没做。`dry_run` / `adopt` 同样不拦。
    """
    if mode not in ("auto", "full", "incremental"):
        raise ToolError(f"`mode` 只认 `auto`（默认，推荐）/ `full` / `incremental`，收到 `{mode}`。")

    used = 0
    level = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
    used += 1

    # ⚠ 拿到"当前是哪张图"之后、**动任何 Actor 之前**：先过**「搭哪张图」这道闸**（2026-09-26 加）。
    #   它挡的是"搭错图" —— 这一步以前只写在工具描述与 docs 里，代码里没人核对（软约束）。
    #   演练不拦（不碰关卡）；`adopt` 也不拦（它只把现状读成台账，**一个 Actor 都不动**）。
    if not dry_run and not adopt:
        _build_target_guard(level)

    # ⚠ 真跑之前还要过**开场动作闸**（2026-09-26 加）：`official_status()` + `get_asset_list()`
    #   都调过没有 —— 客户 agent 自评里承认这两条他都没做。`dry_run` / `adopt` 不拦。
    if not dry_run and not adopt:
        _session_prereq_guard()

    # ⚠ 只数根文件夹一次（recursive=True 已含子文件夹）。2026-09-26 实测踩到：
    #   连根带 7 个子文件夹各数一遍 = **2 倍**（根 89 报成 178）。
    live_refs: list[str] = []
    try:
        found = await call_official(
            ctx, "get_actors_in_folder",
            {"folder_path": OUR_FOLDER_ROOT, "recursive": True}, toolset=TS_SCENE)
        used += 1
        for item in (found or []):
            ref = _ref_path(item)
            if ref:
                live_refs.append(ref)
    except ToolError as exc:
        # 「文件夹不存在」= 这张图上还没摆过我们的东西（**首次搭建的正常情况**），
        # 不是故障 —— 别把它报成警告（2026-09-26 首次真跑就被它刷了一条红字）。
        if "does not exist" not in str(exc).lower():
            warns.append(
                f"读 `{OUR_FOLDER_ROOT}/` 现有 Actor 失败（{exc}）—— 按『当前没有旧东西』继续。")

    return _LiveScan(level=level, live_refs=live_refs, ledger=_load_build_state(), used=used)


async def _precheck_build(ctx: Context[AppContext], planning: Any) -> _Precheck:
    """① 整批校验 —— **任何一条不过就一个 Actor 都不落**（与 confirm_assets「一个字节都不写」同一条纪律）。

    校验顺序照原样：阶段二闸门 `confirmed` → 几何指纹 → 验收状态 `accepted` →
    确认里有没有**用户原话** → 行数对不对得上 plan → 逐行自洽（白膜不许带路径 / 资产行必须有路径 / 三维）
    → 资产路径**逐个**官方 `exists()`（实测教训：资产库会在工作期间少东西）。

    ⚠ 指令行是**按当前 plan 现算**的（`_compose_build_rows`）—— **不读** `build_orders_v1.json`：
      那张表只是留痕件（这条是 2026-09-27 读源码核对出来的，别再以为它会拦落盘）。
    """
    used = 0
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划数据：先走完阶段二（generate_plan → 出图 → confirm_plan）。")
    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    problems: list[str] = []

    gate = planning.gate_check(plan)
    if not gate["confirmed"]:
        problems.append("阶段二闸门没过：plan_v1.json 的 `confirmation.confirmed` 是 false"
                        "（用户还没确认这一版规划）")
    if not gate["plan_hash_ok"]:
        problems.append("几何指纹对不上：plan_v1.json 被改过，上一次确认已作废"
                        "（要重新出图、重新确认）")
    state = planning.acceptance_state(plan)
    if state != "accepted":
        problems.append(f"阶段二验收状态是 `{state}`，不是 `accepted`")

    # ⚠ 证据链（2026-09-26）：确认里必须带**用户原话** —— 图是 agent 画的、确认是它调的，
    #   没有这句话就无法证明有人点过头（实测被绕过一次：客户当场质问"图都不先让我确认就摆"）。
    _conf = plan.get("confirmation") or {}
    if not str(_conf.get("user_quote") or "").strip():
        problems.append(
            "这一版的确认里**没有用户原话**（`confirmation.user_quote` 为空）—— "
            "证明不了是用户点的头。请按「使用图 → 停下等用户回话 → "
            "`confirm_plan(confirmed_by=…, user_quote=原话)`」重走一遍确认，再来搭建")

    rows, _z_rules, warns = _compose_build_rows(plan, load_json(ASSET_LIST_PATH) or {})

    want = len(plan.get("assets") or []) + len(plan.get("whiteboxes") or [])
    if len(rows) != want:
        problems.append(f"指令表行数 {len(rows)} != plan 的 assets+whiteboxes {want}（漏摆或重复）")

    for r in rows:
        if r["kind"] == "whitebox":
            if r["asset_path"]:
                problems.append(f"第 {r['index']} 行（{r['label']}）：白膜行带了资产路径 —— 自相矛盾")
            if not r["primitive"]:
                problems.append(f"第 {r['index']} 行（{r['label']}）：白膜行没写图元")
            if not (isinstance(r["size_cm"], list) and len(r["size_cm"]) == 3):
                problems.append(f"第 {r['index']} 行（{r['label']}）：白膜行尺寸不是三维的")
        else:
            if not r["asset_path"]:
                problems.append(f"第 {r['index']} 行（{r['label']}）：资产行没有路径")
            if len(r["loc_cm"]) != 3:
                problems.append(f"第 {r['index']} 行（{r['label']}）：坐标不是三维的")

    # 资产路径存在性：**逐个**调官方 exists()（实测教训：资产库会在工作期间少东西）
    unique_paths = sorted({r["asset_path"] for r in rows if r["kind"] == "asset" and r["asset_path"]})
    dead: list[str] = []
    for p in unique_paths:
        used += 1
        try:
            ok = await call_official(ctx, "exists", {"path": p}, toolset=TS_ASSET)
        except ToolError as exc:
            dead.append(f"{p}（调用失败：{exc}）")
            continue
        if not ok:
            dead.append(p)
    if dead:
        problems.append("资产路径验不过（官方 exists() 返回否）：" + "、".join(dead))

    if problems:
        raise ToolError(
            "拒绝搭建：**一个 Actor 都没落**。问题如下 ——\n· " + "\n· ".join(problems)
            + "\n（口径见 docs/阶段三-资产布局生成.md §8：先整批校验、再执行。）"
        )
    return _Precheck(plan=plan, rows=rows, warns=warns, gate=gate, used=used)


def _resolve_build_mode(
    mode: str, dry_run: bool, adopt: bool, ledger: dict, ledger_by_uid: dict,
    rows: list[dict], live_refs: list[str], force_full: bool, level: str,
) -> tuple[str, str]:
    """定模式：**默认 auto = 能不推倒就不推倒**（2026-09-26 实测教训）。

    背景（用户问"为啥总是全删再摆放，我 MCP 工具里有局部改的啊"）：`mode` 原先默认 `full`，
      于是**没读参数说明的 agent** 每次都用默认值 → **每轮全清重摆 89 个**，好几轮白做。
      → 教训：**默认值必须落在最省、最不破坏的那条路上**，不能指望 agent 去读 schema。
      → 现在：有对得上的台账 = 增量；没有（第一次搭 / 换关卡）= 全量；显式 `full` 真跑要二次确认。

    返回 `(mode, mode_why)`；显式全量 + 有台账 + 没给 `force_full` → **拒收**一次，并把话说清。
    """
    mode_why = ""
    if mode == "auto":
        if adopt:
            # `adopt`（认领现状）本来就是增量语义；给定 auto 时必须先落到 incremental，
            # 否则会被下面那句"adopt 只跟 incremental 合用"拒掉（2026-09-26 自查发现的回归）。
            mode, mode_why = "incremental", "auto → 增量（adopt 认领现状）"
        else:
            usable = bool(ledger_by_uid) and str(ledger.get("level") or "") in ("", level)
            mode = "incremental" if usable else "full"
            mode_why = ("auto → 增量（台账对得上）" if usable
                        else "auto → 全量（没有可用台账：第一次搭或换了关卡）")
    elif mode == "full" and not dry_run and ledger_by_uid and not force_full:
        # 显式全量 + 有台账 = 十有八九是"没读参数就写了 full"或"手滑" → 拒收一次，把话说清
        _lb = ledger_by_uid
        n_changed = 0
        for _r in rows:
            _old = _lb.get(_r["uid"])
            if _old is None or _row_changes(_r, _old):
                n_changed += 1
        raise ToolError(
            f"拒收：你选的是**全量重摆**（`mode=\"full\"`）—— 那会把 `{OUR_FOLDER_ROOT}/` 下的 "
            f"{len(live_refs)} 个 Actor **全部删掉再摆一遍**。而这一版相对台账大约只动了 "
            f"**{n_changed} 行**，增量就够（默认 `mode=\"auto\"` 会自动走增量）。\n"
            "· 只想改几行 → 去掉 `mode`（或写 `mode=\"incremental\"`）。\n"
            "· 确实要推倒重来（关卡被搞乱了 / 想整批重摆）→ 再显式传 `force_full=true`。"
        )
    return mode, mode_why


async def _adopt_ledger(
    ctx: Context[AppContext], rows: list[dict], live_refs: list[str],
    level: str, plan_hash: str, warns: list[str],
) -> tuple[BuildReport, int]:
    """`adopt`：把关卡现状读成台账基线 —— **一个 Actor 都不动、也不存盘**。

    返回 `(要交回给调用方的报告, 用掉几次官方调用)`。
    ⚠ 白膜尺寸必须**单独从关卡读**（理由见 `_read_cube_size_cm`），照抄指令表会让
      "刚加宽过的那几行"被判成"没变" → 静默不摆。
    """
    used = 0
    by_name: dict[str, dict] = {}
    for r in rows:
        by_name.setdefault(r["name"], r)          # Actor label = 我们 spawn 时传的 name（实测）
    adopted_rows: list[dict] = []
    unmatched: list[str] = []
    for ref in live_refs:
        used += 1
        try:
            label = str(await call_official(
                ctx, "get_label", {"actor": {"refPath": ref}}, toolset=TS_ACTOR) or "")
        except ToolError as exc:
            unmatched.append(f"{ref}（读 label 失败：{exc}）")
            continue
        r = by_name.get(label)
        if r is None:
            unmatched.append(f"{ref}（label = `{label}`，指令表里没有这一行）")
            continue
        used += 1
        loc = yaw = scale = None
        try:
            got = await call_official(
                ctx, "get_actor_transform", {"actor": {"refPath": ref}}, toolset=TS_ACTOR)
            loc, yaw, scale = _xform_of(got)
        except ToolError as exc:
            warns.append(f"「{r['label']}」读回变换失败（{exc}）"
                         " —— 台账里这一行记的是**指令值**，不是实测值。")
        entry = _ledger_row(r, ref, loc, yaw, scale, readback=loc is not None)
        if r["kind"] == "whitebox":
            # ⚠ 白膜尺寸必须**单独从关卡读**（详见 _read_cube_size_cm 的说明）：
            #   照抄"当前指令表"的尺寸会让"刚加宽过的那几行"被判成"没变" → 静默不摆。
            dim, cost = await _read_cube_size_cm(ctx, ref)
            used += cost
            entry["size_cm"] = dim
            entry["size_readback"] = dim is not None
            if dim is None:
                warns.append(
                    f"⚠ 「{r['label']}」的白膜尺寸**读不回来** —— 台账里记 null，"
                    "下一次增量会把它当『尺寸变了』**重摆一遍**（安全侧：宁可多摆一行）。")
            elif r.get("size_cm") and [round(float(x), 2) for x in dim] != [
                    round(float(x), 2) for x in r["size_cm"]]:
                warns.append(
                    f"⚠ 「{r['label']}」的**实际尺寸与当前指令表不一致**：关卡里 {dim} cm、"
                    f"指令表 {r['size_cm']} cm —— 台账记的是**关卡实测值**"
                    "（这就是『已搭好的场景遇上改了指令表』该有的样子：下次增量会重摆它）。")
        adopted_rows.append(entry)
    _save_build_state(level, plan_hash, "adopt", adopted_rows)
    adopted_uids = {x["uid"] for x in adopted_rows}
    not_yet = [r["label"] for r in rows if r["uid"] not in adopted_uids]
    size_lie = [x["label"] for x in adopted_rows
                if x.get("kind") == "whitebox" and not x.get("size_readback")]
    if size_lie:
        warns.append(
            f"⚠ 有 {len(size_lie)} 行白膜的尺寸**没能从关卡读回来**（台账里记 null）——"
            "下一次增量会把它们当『尺寸变了』各重摆一遍。清单："
            + "、".join(size_lie[:8]) + ("…" if len(size_lie) > 8 else ""))
    if unmatched:
        warns.append(
            f"⚠ 有 {len(unmatched)} 个 `{OUR_FOLDER_ROOT}/` 下的 Actor 认不出对应哪一行 —— "
            "它们会在下次增量时**挡住**（拒收），到时要么删掉它们、要么跑一次 `full`：\n· "
            + "\n· ".join(unmatched))
    return BuildReport(
        stage="阶段三 · 认领现状（adopt：只读现状 + 写台账，没动任何 Actor）",
        level=level, dry_run=False, refused=False, reason="adopt：不动 Actor，只登记基线",
        plan_hash=plan_hash, planned=len(rows),
        mode="incremental", ledger_path=str(BUILD_STATE_PATH), ledger_written=True,
        diff={"认领回来的": len(adopted_rows), "认不出的": len(unmatched),
              "指令表里还没有的": not_yet},
        removed=0, placed=0, verified=0, groups={}, rows=[],
        official_calls=used,
        next_step=("台账已按**关卡现状**登记（一个 Actor 都没动、也没存盘）。之后的循环是："
                   "改 plan（`generate_plan`）→ **重画图（写新指纹 + 本轮变更集那几行）** → "
                   "让用户看图确认（`confirm_plan`）→ `generate_build_orders()` → "
                   "`execute_build(mode=\"incremental\")` —— 那一次只动改动过的行。"
                   "⚠ 改完先看 `get_plan().acceptance.change_set.required_labels`："
                   "**那就是这一轮图里只需出现的那几行**，别整张重画。"),
        warnings=warns,
    ), used


def _incremental_diff(
    targets: list[dict], same: list[dict], gone: list[dict],
    changed: list[tuple[dict, dict, list[str]]], orders: dict, reclaim_actor: dict,
    drifted: list[tuple[dict, dict]], manual: list[dict], ledger_note: str | None = None,
) -> dict:
    """增量差异摘要 —— 演练与真跑**共用同一份**，免得两处说法不一致。

    `ledger_note` 给值时在最前面加一条 `"台账"`（演练要用它点名台账在不在）；
    **键的顺序照原样** —— 这份摘要是给人看的，顺序也算输出。
    """
    out: dict = {}
    if ledger_note is not None:
        out["台账"] = ledger_note
    out["要删"] = [old.get("label") for old in gone] + [r["label"] for r, _o, _w in changed]
    out["要摆"] = [r["label"] for r in targets]
    out["没动"] = len(same)
    out["认领回来的"] = [orders[u]["label"] for u in reclaim_actor]
    out["台账说在、关卡里却没了"] = [r["label"] for r, _ in drifted]
    out["改动明细"] = {r["label"]: w for r, _o, w in changed}
    out["人工改过（现状与台账不符）"] = {m["label"]: m["why"] for m in manual}
    return out


async def _place_rows(
    ctx: Context[AppContext], targets: list[dict]
) -> tuple[list[BuildRowResult], int]:
    """② 按表落：资产行 `add_to_scene_from_asset`；白膜行 `Actor` + `add_cube`；再逐个进分组。

    返回 `(逐行结果, 用掉几次官方调用)`。
    ⚠ **一行失败不带崩整批** —— `except Exception` 那一档是刻意的（见行内注释）。
    """
    used = 0
    results: list[BuildRowResult] = []
    for r in targets:
        xform = {
            "location": {"x": r["loc_cm"][0], "y": r["loc_cm"][1], "z": r["loc_cm"][2]},
            "rotation": dict(r["rot"]),
            "scale": {"x": r["scale"][0], "y": r["scale"][1], "z": r["scale"][2]},
        }
        actor, err = "", ""
        try:
            if r["kind"] == "asset":
                used += 1
                got = await call_official(
                    ctx, "add_to_scene_from_asset",
                    {"asset_path": r["asset_path"], "name": r["name"], "xform": xform},
                    toolset=TS_SCENE)
            else:
                used += 1
                got = await call_official(
                    ctx, "add_to_scene_from_class",
                    {"actor_type": {"refPath": ACTOR_CLASS_PATH}, "name": r["name"], "xform": xform},
                    toolset=TS_SCENE)
                spawned = _ref_path(got)
                if not spawned:
                    raise ToolError("官方没返回 Actor 引用（spawn 失败）")
                dim = r["size_cm"]
                used += 1
                await call_official(
                    ctx, "add_cube",
                    {"actor": {"refPath": spawned}, "name": "cube",
                     "dimensions": {"x": dim[0], "y": dim[1], "z": dim[2]}},
                    toolset=TS_PRIM)
                got = {"refPath": spawned}
            actor = _ref_path(got)
            if not actor:
                raise ToolError("官方没返回 Actor 引用")
            used += 1
            await call_official(
                ctx, "set_actor_folder",
                {"actor": {"refPath": actor}, "folder_path": r["folder"]},
                toolset=TS_SCENE)
        except ToolError as exc:
            err = str(exc)
        except Exception as exc:                       # noqa: BLE001 —— 一行失败别把整批带崩
            err = f"{type(exc).__name__}: {exc}"
        results.append(BuildRowResult(
            index=r["index"], label=r["label"], kind=r["kind"],
            actor=actor, loc_cm=list(r["loc_cm"]), scale=list(r["scale"]),
            yaw=r["rot"]["yaw"], ok=(err == ""), error=err,
        ))
    return results, used


async def _verify_rows(
    ctx: Context[AppContext], results: list[BuildRowResult], targets: list[dict],
    verify_mode: str, warns: list[str],
) -> _VerifyOutcome:
    """③ 读回对账：逐个 `get_actor_transform` 与指令比；白膜在 `full` 档再读组件尺寸。

    ⚠ 省的是**确认能力**，不是省"报错"：`verify="transform"` 不读白膜尺寸，那时台账里
      那些行记 `size_readback=false`（尺寸是**指令值**，不是实测值）—— 绝不假装核过。
    """
    out = _VerifyOutcome()
    if verify_mode == "off":
        return out
    for res, r in zip(results, targets):
        if not res.actor:
            continue
        out.used += 1
        try:
            got = await call_official(
                ctx, "get_actor_transform", {"actor": {"refPath": res.actor}}, toolset=TS_ACTOR)
        except ToolError as exc:
            out.mismatches.append(f"#{r['index']} {r['label']}：读回失败（{exc}）")
            out.bad_by_uid[r["uid"]] = f"读回失败（{exc}）"
            continue
        got = got if isinstance(got, dict) else {}
        loc = got.get("location") or {}
        rot = got.get("rotation") or {}
        scl = got.get("scale") or {}
        try:
            back = [num(float(loc.get("x", 0.0))), num(float(loc.get("y", 0.0))),
                    num(float(loc.get("z", 0.0)))]
            back_yaw = num(float(rot.get("yaw", 0.0)))
            back_scale = [num(float(scl.get("x", 0.0))), num(float(scl.get("y", 0.0))),
                          num(float(scl.get("z", 0.0)))]
        except (TypeError, ValueError):
            out.mismatches.append(f"#{r['index']} {r['label']}：读回的变换解析不了（{got!r}）")
            out.bad_by_uid[r["uid"]] = "读回的变换解析不了"
            continue
        res.loc_cm, res.yaw, res.scale = back, back_yaw, back_scale
        out.verified_uids.add(r["uid"])
        bad: list[str] = []
        if any(abs(a - b) > 0.05 for a, b in zip(back, r["loc_cm"])):
            bad.append(f"位置 {back} != 指令 {r['loc_cm']}")
        if not _angle_close(back_yaw, r["rot"]["yaw"]):
            bad.append(f"yaw {back_yaw} != {r['rot']['yaw']}（按角度差比，差 "
                       f"{(back_yaw - r['rot']['yaw'] + 180.0) % 360.0 - 180.0:.3f}°）")
        if any(abs(a - b) > 1e-4 for a, b in zip(back_scale, r["scale"])):
            bad.append(f"缩放 {back_scale} != {r['scale']}")
        if r["kind"] == "whitebox" and verify_mode == "full":
            # ⚠ 白膜**尺寸**不在 Actor 变换里（它在组件的 `RelativeScale3D` 上）——
            #   必须单独读，否则"位置对了、尺寸不对"这种错永远报不出来
            #   （客户 agent 自评：「它只管按 plan 摆，不管摆的对不对」）。
            #   官方 `get_actor_bounds` 对这类 Actor 恒返回 ±128 的**假包围盒**（实测），不能用它。
            #   ⚠ `verify="transform"` 档**不读它**（每行省 2 次调用，见 `_verify_level`）——
            #     那时这里什么都不记，下面写台账时 `size_readback=false`，绝不假装核过。
            dim, cost = await _read_cube_size_cm(ctx, res.actor)
            out.used += cost
            out.size_back[r["uid"]] = dim
            if dim is None:
                warns.append(f"⚠ 「{r['label']}」的白膜尺寸**没读回来** —— 这一行的尺寸没复核。")
            elif r.get("size_cm") and [round(float(x), 2) for x in dim] != [
                    round(float(x), 2) for x in r["size_cm"]]:
                bad.append(f"白膜尺寸 {dim} != 指令 {r['size_cm']}")
        if bad:
            out.mismatches.append(f"#{r['index']} {r['label']}：" + "；".join(bad))
            # 台账那一行要能说清"差在哪"（[完工-20]：`readback` 只说明读过，`verified` 才说明对上）
            out.bad_by_uid[r["uid"]] = "；".join(bad)
        else:
            out.matched_uids.add(r["uid"])
            out.verified += 1
    return out


async def _recount_and_heal(
    ctx: Context[AppContext], results: list[BuildRowResult], targets: list[dict],
    warns: list[str],
) -> tuple[list[str], int, int]:
    """④ 落完清点 `UEMCP/` + **分组自愈**（缺的按引用就地补一次，最多一轮）。

    为什么加自愈（C 项 · 2026-09-27 · **尚未实测**）：`set_actor_folder` 是**每行一次**的
      （89 行 = 89 次，占全量调用近 1/5）；它失败时上游只把错误记进那一行，而"落成了、
      却没进 `UEMCP/` 分组"这件事在报告里只体现为**一个总数不符** —— 具体是谁、要不要补，
      没人说得清。这里拿落完那一次的**引用列表**（不只是个数）与"刚摆成的行"对一遍：
      缺的**原地补一次**、再复核一遍（最多一轮，不无限重试）。
      ⚠ 判据用引用不用个数：个数对得上、错行也可能存在。

    返回 `(落完的引用列表, 个数, 用掉几次官方调用)`。
    ⚠ **只在"清点调用真的成功了"时才自愈**（`after_read_ok`）：否则一次失败会把 89 行
      全判成"缺分组"，白跑 89 次 `set_actor_folder`（退化路径，别删这个开关）。
    """
    used = 0
    after_refs: list[str] = []
    after_read_ok = False
    try:
        found = await call_official(
            ctx, "get_actors_in_folder",
            {"folder_path": OUR_FOLDER_ROOT, "recursive": True}, toolset=TS_SCENE)
        used += 1
        after_read_ok = True
        for item in (found or []):
            _ref = _ref_path(item)
            if _ref:
                after_refs.append(_ref)
    except ToolError as exc:
        warns.append(f"落完复核 `{OUR_FOLDER_ROOT}/` 数量失败（{exc}）")

    pairs = list(zip(results, targets))
    missing_in_folder = [(res, r) for res, r in pairs
                         if res.ok and res.actor and res.actor not in set(after_refs)]
    if missing_in_folder and after_read_ok:
        fixed = 0
        for res, r in missing_in_folder:
            used += 1
            try:
                await call_official(
                    ctx, "set_actor_folder",
                    {"actor": {"refPath": res.actor}, "folder_path": r["folder"]},
                    toolset=TS_SCENE)
                fixed += 1
            except ToolError as exc:
                warns.append(f"「{r['label']}」补分组失败（{exc}）。")
        if fixed:
            used += 1
            try:
                found2 = await call_official(
                    ctx, "get_actors_in_folder",
                    {"folder_path": OUR_FOLDER_ROOT, "recursive": True}, toolset=TS_SCENE)
                after_refs = [x for x in (_ref_path(i) for i in (found2 or [])) if x]
            except ToolError as exc:
                warns.append(f"补分组后复核失败（{exc}）。")
        still = [r["label"] for res, r in missing_in_folder if res.actor not in set(after_refs)]
        warns.append(
            f"⚠ 有 {len(missing_in_folder)} 行**落成了却没进 `{OUR_FOLDER_ROOT}/` 分组**"
            f"（已就地补 {fixed} 次）"
            + (f"，补完仍缺：{'、'.join(still[:6])}" + ("…" if len(still) > 6 else "")
               if still else "，补完已到位。"))
    return after_refs, len(after_refs), used


def _write_ledger_rows(
    rows: list[dict], results: list[BuildRowResult], targets: list[dict],
    ledger_by_uid: dict, verified_uids: set[str], size_back: dict, reclaim_actor: dict,
    matched_uids: set[str] | None = None, bad_by_uid: dict | None = None,
) -> list[dict]:
    """④ 组装新台账的每一行（下次增量全靠它；**我们自己的 JSON，不是 UE 存盘**）。

    三类行分得很清，别混：
      · **这次动过的** —— 记它落成的 Actor；`readback` / `size_readback` **如实分档**
        （`verify=false` 时 `res.loc_cm` 里装的是**指令值**，只能记 `readback=false`；
         硬规则 6：预估值不许伪装成实测值）；
      · **认领回来的** —— 保留台账里原来那份变换；
      · **没动过的** —— 台账里那条**原样保留**（连 Actor 引用一起）：这就是"没碰它"的凭据。

    ⚠ **`readback` 只说明"读过"，不等于"对上了"**（2026-09-30 分开记，见 `[完工-20]`）：
      所以**本轮落的**行还会带上 `verified`（bool，读回来**且与指令一致**）——
      对不上的行 `verified=false` + `mismatch`（差在哪）。这样台账里不会再出现
      「`readback: true` 被读成 PASS」那种含混。
      ⚠ **认领回来的 / 没动过的行不加这两个字段**（它们**原样保留**才是"没碰它"的凭据）——
        读表的人要按「**没有 `verified` 字段 = 这一行不是本轮落的**」理解，别当成验过了。
    """
    res_by_uid = {r["uid"]: res for r, res in zip(targets, results)}
    matched_uids = matched_uids or set()
    bad_by_uid = bad_by_uid or {}
    new_ledger_rows: list[dict] = []
    for r in rows:
        res = res_by_uid.get(r["uid"])
        if res is not None:
            # 这一行这次动过：记它落成的 Actor。
            # ⚠ `readback` 只认**真的读回来过**的行（`verified_uids`）——
            #   传 `verify=false` 时 `res.loc_cm` 里装的是**指令值**，不是实测值，
            #   那时只能记 `readback=false`（硬规则 6：预估值不许伪装成实测值）。
            ok_read = bool(res.actor) and r["uid"] in verified_uids
            entry = _ledger_row(
                r, res.actor,
                res.loc_cm if ok_read else None, res.yaw if ok_read else None,
                res.scale if ok_read else None, readback=ok_read)
            # ⚠ 「读过」与「对上」分开记（[完工-20]）—— 没有这两行的话，
            #   一行**验证失败**的记录照样写着 `readback: true`，读表的人会当成 PASS。
            entry["verified"] = bool(r["uid"] in matched_uids)
            if not entry["verified"]:
                entry["mismatch"] = (str(bad_by_uid.get(r["uid"]) or "")
                                     or ("落成失败：" + (res.error or "没有落成 / 没有 Actor 引用")))
            if r["kind"] == "whitebox":
                # ⚠ 尺寸也要**如实分档**（C 项 · 2026-09-27 补）：
                #   · 真读过（`verify="full"`）→ 记**关卡实测值** + `size_readback=true`。
                #     为什么记实测值而不是指令值：`adopt` 早就是这么做的（见它那段注释），
                #     这样"人把某行加宽过"下次增量一定判得出「尺寸变了 → 重贴」；
                #     指令值与实测值只有在**复核通过**时才相等，那时两者等价、不影响判定。
                #   · 没读（`verify="transform"` / `"off"`）→ 保留指令值，但**必须**
                #     标 `size_readback=false` —— 不许让指令值躺着冒充实测值（硬规则 6）。
                measured = size_back.get(r["uid"])
                if isinstance(measured, list) and len(measured) == 3:
                    entry["size_cm"] = [float(x) for x in measured]
                    entry["size_readback"] = True
                else:
                    entry["size_readback"] = False
            new_ledger_rows.append(entry)
            continue
        if r["uid"] in reclaim_actor:
            old = ledger_by_uid.get(r["uid"]) or {}
            new_ledger_rows.append(_ledger_row(
                r, reclaim_actor[r["uid"]], old.get("loc_cm"), old.get("yaw"),
                old.get("scale"), readback=bool(old.get("readback"))))
            continue
        old = ledger_by_uid.get(r["uid"])
        # 没动过的行：台账里原来那条**原样保留**（连 Actor 引用一起）—— 这就是"没碰它"的凭据
        new_ledger_rows.append(old if old else _ledger_row(r, ""))
    return new_ledger_rows


@mcp.tool()
async def execute_build(
    ctx: Context[AppContext],
    mode: Annotated[str, Field(
        description=(
            "**默认 `auto`，一般别手动指定**：有可用台账就**只动「与台账比变了的那些行」**"
            "（增量，其余 Actor 一个都不碰）；没有台账（第一次搭 / 换了关卡）才全量。"
            "`incremental` = 强制增量；`full` = **全量重摆（会把 `UEMCP/` 下全部 Actor 删掉重摆）**"
            "—— 真跑时还要 `force_full=true` 才放行，**别用它来改几行**"
        ))] = "auto",
    force_full: Annotated[bool, Field(
        description=(
            "**只有 `mode=\"full\"` 真跑时才需要**：确认你**确实要**把 `UEMCP/` 下的 Actor 全删了重摆。"
            "台账存在时不给它 → `full` 会被拒收，并告诉你这次其实只变了几行（该走增量）"
        ))] = False,
    adopt: Annotated[bool, Field(
        description=(
            "**只与 `mode=\"incremental\"` 合用**：不改任何 Actor，把关卡现状（`UEMCP/` 下现有 "
            "Actor 的 label + 变换）读成台账（基线）。用在『场景已经搭好了但还没有台账』的时候，"
            "省掉一次白重摆。跑完它再调 `mode=\"incremental\"` 才是真正落差异"
        ))] = False,
    dry_run: Annotated[bool, Field(
        description="true = 只校验 + 对账，**一个 Actor 都不落**（先看一遍再动手用这个）")] = False,
    verify: Annotated[bool | str, Field(
        description=("读回对账的档位（默认 `true` = `full`）："
                     "`true`/`'full'` = 逐个 `get_actor_transform` 对账 **+ 白膜再读一遍组件尺寸"
                     "（`RelativeScale3D`）**；"
                     "`'transform'` = 只对账位置/朝向/缩放，**省掉白膜尺寸那部分**"
                     "（89 行实测省约 100 次官方调用）；"
                     "`false`/`'off'` = 完全不读回（台账里 `readback=false`，记的是指令值）。"
                     "⚠ 省的是**确认能力**：`'transform'` 下『位置对了、厚薄不对』查不出来。"))] = True,
    accept_user_edits: Annotated[bool, Field(
        description=(
            "**默认 false = 保护用户在 UE 里手改的东西**：动手前先逐行读一遍**关卡现状**与台账比 —— "
            "对不上（人工挪过 / 转过 / 缩放过 / 改过白膜厚薄）的行、以及台账里没有的活 Actor，"
            "**一律拒收并列出来**（一个 Actor 都不动）。"
            "确认『这些人工改动可以按 plan 覆盖（会被删掉重摆）』才传 true。"
            "⚠ 不要为了「让它过」随手传 true —— 那正是「用户手动改的东西被删了」的来源。"
            "⚠ **这是两步闸（2026-09-30 加固）**：真跑要覆盖必须**先按默认参数调一次**（拿到那份"
            "人工痕迹清单、代码会留痕「你问的是哪几行」）→ **把清单原样交给用户、停下等他打字** → "
            "再带 `accept_user_edits=true` + 他的原话 `user_quote` 调第二次。"
            "**没先问过 / 问的那批与现在这批不一致 / 答复来得太快，一律拒收**"
            "（光有布尔开关，这道闸就只剩『靠自觉』）"
        ))] = False,
    user_quote: Annotated[str, Field(
        description=(
            "**用户本人同意「按 plan 覆盖他手改的东西」的原话**（例：「那是我改的，按 plan 覆盖吧」）。"
            "⚠ 只在 `accept_user_edits=true` **且真跑**（`dry_run=false`）时必填："
            "没有它 = **证明不了有人确认过**，一律拒收；演练（`dry_run=true`）不需要。"
            "⚠ 还要求：**距你用默认参数拿到那份清单至少 15 秒**（问完立刻自己答复 = 没人看过）。"
            "⚠ **不许自己编** —— 拿不出原话，就说明你还没问他"
        ))] = "",
) -> BuildReport:
    """阶段三第 2 步（后半）：**把放置表落进关卡 —— 默认只动改过的行（增量），不推倒重来**。

    流程（顺序不能反）：
      ⓪ **「搭哪张图」闸**（2026-09-26 加）：`check_build_target()` 里那份**用户答复**必须存在，
         且与当前关卡对得上（选开新图而他还没换图 / 指定了别的图 / 中途换了图 → 拒收）。
         ⚠ 演练（`dry_run=true`）与 `adopt=true` 不拦（它们不碰关卡），但演练会在报告里点名
         "答复还没记"。
      ① **整批校验**：阶段二闸门 `confirmed` + 几何指纹 + 验收状态 `accepted` + 用户原话；
         资产路径逐个官方 `exists()`；白膜行不许带路径、必须有图元与三维尺寸；
         行数 == plan 的 `assets + whiteboxes` —— **任何一条不过 → 一个 Actor 都不落**
         （与 `confirm_assets` 拒收时"一个字节都不写"同一个道理）。
      ② **人工痕迹闸**（2026-09-26 加；**2026-09-30 两次加固**）：动手前读**关卡现状**
         与台账比（位置 / 朝向 / 缩放 / 白膜厚薄）；对不上的行、以及台账解释不了的活 Actor，
         **默认拒收**（一个 Actor 都不动）—— 拒收时**顺手留痕「这一批问的是哪几行」**
         （`views/user_edits_v1.json`），第 2 步会拿它核对。
         ⚠ **"按 plan 覆盖它们"＝ 用户本人的决定，而且现在是两步闸**：
           ① 先按**默认参数**调一次 → 拿到那份清单（并已留痕）；
           ② **把清单原样交给用户、停下等他打字**；
           ③ 再带 `accept_user_edits=true` + `user_quote`（他的原话）调第二次。
         判据（三条都要过）：**问过** + **问的那批就是现在这批**（多一行少一行都重问）+
         **有他本人的原话且距提问 ≥ 15 秒**（`MIN_USER_EDITS_ANSWER_DELAY_S`）。
         演练（`dry_run=true`）不要求原话 —— 它不碰关卡。
         ⚠ **边界（不吹）**：这拦不住"存心等够时间再编一句话"（代码验不了真话）；它做到的是
         **不能悄悄干** + 台账里留着「问过哪几行 + 那句话原文」可以事后对质。
         ⚠ 为什么有它：2026-09-30 实测（**同类事故第二次**）—— 外部 Agent 收到拒收后**自己**传了
         `accept_user_edits=true`，把用户手拖过的水面（差 30 cm）覆盖掉了，全程没问过人。
         ⚠ **但"拒收"只在"扫描范围"之内成立**（这一点 2026-09-30 实测两次、并已更正文档）：
           · `dry_run` / `accept_user_edits=true` / `mode="full"` → 扫**全表**；
           · **纯增量真跑（默认那条）→ 只扫「本次会被删 / 重摆的那些行」**。
         所以范围之外的人工痕迹**既不拦、也不报**（**静默保留**：东西不丢，但没人告诉你）。
         要"搭建前统一看清有人改过什么"，用 `evaluate_layout()`（它扫全表）。
         ⚠ 为什么要它：客户原话「用户手动改的东西不该删 —— 我 full 重摆给删了」；
           以前 `full` 无条件清空 `UEMCP/`，增量则**从不读现状**，手改的东西既没人保护也没人报。
      ③ **幂等清场**：只清 outliner 里 `UEMCP/` 下的 Actor（那才是**我们的**）；
         关卡自带的方向光 / 天空 / 玩家起点等**一个都不碰**。
      ④ **按表落**：资产行 → `add_to_scene_from_asset`；白膜行 →
         `add_to_scene_from_class(/Script/Engine.Actor)` + `PrimitiveTools.add_cube`；
         然后逐个 `set_actor_folder` 进 `UEMCP/<类>`（这也是下一次清场的判据）。
      ⑤ **读回对账**：逐个 `get_actor_transform` 与指令表的 `loc_cm / yaw / scale` 比；
         **白膜另外读组件 `RelativeScale3D`（×100 = 厘米）核对尺寸** —— 对不上的原地报出来（不掩饰）。
         ⚠ **档位**（2026-09-27 加，`_verify_level`）：`verify=true`（默认，`full`）连白膜尺寸一起验；
         `verify="transform"` 只验位置/朝向/缩放（89 行实测省约 100 次官方调用，但**厚薄不对查不出来**）；
         `verify=false` 完全不读回。**省的是确认能力，不是省"报错"** —— 台账里如实分档记。
      ⑥ **落完按引用核对分组**（2026-09-27 加）：落完那次 `get_actors_in_folder` 拿回的是**引用列表**
         （不只是个数），与"刚摆成的行"对一遍 —— 缺的**就地补一次** `set_actor_folder` 再复核。
         以前它只比个数，于是"落成了却没进 `UEMCP/` 分组"只表现为一个总数不符，是谁、补不补都说不清。
    ⚠ **绝不存盘**（不调任何 save 类工具）—— 关卡脏没脏由用户在 UE 里看。
    ⚠ 白膜尺寸**不能**用官方 `get_actor_bounds` 复核：它对这类 Actor 恒返回 ±128 的**假包围盒**
      （2026-09-26 实测）—— 所以走组件 `RelativeScale3D`（见 `_read_cube_size_cm`）。

    **增量（`mode="incremental"`，2026-09-26 用户要求）**：已经大搭建好的场景要局部改，就
    "回去改图 → 用户重新确认 → 只重摆改动的那几行"。差异靠**台账** `views/build_state_v1.json`
    与指令表的 `uid` 对账，分四类：`added`（表里有、台账没有）/ `changed`（同一 uid 但位置 /
    朝向 / 缩放 / 路径 / 白膜尺寸 / 分组变了）/ `removed`（台账有、表里没了）/ `drift`（台账说它在，
    关卡里却找不到 → 当"要重摆"）。**没变的行一个 Actor 都不碰。**
    ⚠ 台账是我们自己的 JSON，**不是 UE 存盘**。做增量的前提是台账存在且对得上当前关卡 ——
      不满足时**拒收**（一个 Actor 都不动），出路是 `adopt=true` 认领现状、或先跑一次 `full`。
    ⚠ 关卡 `UEMCP/` 下若有台账与指令表**都解释不了**的 Actor（有人手动改过名 / 手动摆过）→
      **拒收并列出来**。增量只能靠对账，对不上就不猜、也不删。
    ⚠ 使用后的自评点过一条「它不会告诉我『用户手动改了地皮你别删』」—— 现在会了：
      每次动手前都会读一遍现状，落在 `diff["人工改过（现状与台账不符）"]` 与 `warnings` 里。
    """
    planning = _planning_modules()
    calls = 0
    # `verify` 三档归一（`True`/`"full"` / `"transform"` / `False`/`"off"`，见 `_verify_level`）。
    # 参数不对当场抛错 —— **一个 Actor 都还没落**（放在最前面就是为了这个）。
    verify_mode = _verify_level(verify)

    # ⚠ 碰 UE 之前先过**回读闸**（2026-09-26 用户要求把软约束变硬）：这一版没回读过，就不许落关卡。
    _readback_guard()

    # ---------- ① 整批校验（不过就一个 Actor 都不落）----------
    pre = await _precheck_build(ctx, planning)
    calls += pre.used
    plan, rows, warns, gate = pre.plan, pre.rows, pre.warns, pre.gate

    # ---------- 指令表（留痕件）是不是这一版的（2026-09-27 补 · B 项 · **尚未实测**）----------
    # ⚠ 先说清一件事（2026-09-27 读源码核对出来的）：`execute_build` 在**上面**已经用
    #   `_compose_build_rows(plan, …)` 按当前 plan **自己重算了一遍**，**从不读**
    #   `views/build_orders_v1.json` —— 所以"指令表是旧版"**不影响落盘**，它只是给人看的留痕件。
    #   但没有它 / 它是旧版时会误导人：2026-09-26 真跑就踩到过（表是第 22 轮指纹、plan 已是第 23 轮，
    #   人拿着旧表以为搭的是旧版）。所以：**只报，不拦、不落盘**。
    _orders_doc = load_json(BUILD_ORDERS_PATH) or {}
    if not _orders_doc:
        warns.append(
            f"提示：还没生成指令表（`{BUILD_ORDERS_PATH.name}`）—— **不影响这次落盘**"
            "（`execute_build` 按 plan 自己重算），但拿它当参考就什么都没有；"
            "想要留痕件就调 `generate_build_orders()`。")
    elif str(_orders_doc.get("plan_hash") or "") != str(gate["plan_hash"] or ""):
        warns.append(
            f"⚠ 指令表 `{BUILD_ORDERS_PATH.name}` 是**旧版**（表里 plan_hash "
            f"{str(_orders_doc.get('plan_hash') or '(空)')[:10]}… ≠ 当前确认版 "
            f"{str(gate['plan_hash'] or '')[:10]}…）—— **不影响这次落盘**"
            "（`execute_build` 按 plan 自己重算），但别拿它当这一版看；"
            "要更新就重跑 `generate_build_orders()`。")

    # ---------- 当前关卡 + 现状清点（**只数根文件夹一次**）----------
    live = await _scan_live_level(ctx, mode, dry_run, adopt, warns)
    calls += live.used
    level, live_refs, ledger = live.level, live.live_refs, live.ledger

    # ---------- 定模式：**默认 auto = 能不推倒就不推倒**（2026-09-26 实测教训）------------
    ledger_by_uid = {str(x.get("uid")): x for x in (ledger.get("rows") or [])
                     if isinstance(x, dict) and x.get("uid")}
    mode, mode_why = _resolve_build_mode(
        mode, dry_run, adopt, ledger, ledger_by_uid, rows, live_refs, force_full, level)

    # ---------- ⚠「没有台账 + 关卡里已经有我们的 Actor」= **别悄悄全量清场** --------------
    # 2026-09-26 加（客户 agent 自评：「局部改不要全部重做 —— 反复违反，几乎每次都 mode=full
    #   全量重摆」；「用户手动改的东西不该删 —— 我 full 重摆给删了」）。
    # 为什么单独设这一道：`auto` 在没有可用台账时会落到**全量**，而全量 = 把 `UEMCP/` 下
    #   **全部** Actor 删掉重摆。这一步在**读现状之前**就拦住，既不再花那几十次调用，
    #   也把"安全的出路"直接摆在眼前（`adopt` 认领现状 = 一个 Actor 都不动）。
    # 判据：全量 + 关卡里已有我们的 Actor + **没有可用台账**（第一次搭不在内：那时 live_refs 为空）
    #   + 不是演练 + **没同时给 force_full 与 accept_user_edits**。
    if (mode == "full" and live_refs and not ledger_by_uid and not dry_run
            and not (force_full and accept_user_edits)):
        raise ToolError(
            f"拒收：`{OUR_FOLDER_ROOT}/` 下已经有 **{len(live_refs)} 个 Actor**，"
            "但**没有可用台账**（第一次搭那次没写 / 换了关卡 / 台账被删）—— "
            "现在这一版会走**全量重摆**，把它们**全部删掉重来** —— **一个 Actor 都没动**。\n"
            "· **先认领现状**（推荐，不动任何 Actor）：`execute_build(mode=\"incremental\", "
            "adopt=true)` 把关卡现状登记成基线 → 再 `execute_build(mode=\"incremental\")` 只落差异。\n"
            "· 确实要推倒重来（关卡被搞乱了 / 想整批重摆）："
            "`mode=\"full\", force_full=true, accept_user_edits=true` —— **两个都要给**。\n"
            "（为什么拦：客户 agent 自评原话「局部改不要全部重做 —— 反复违反，几乎每次都 mode=full "
            "全量重摆」。没有台账时全量是唯一选择，但「悄悄清掉 89 个再摆一遍」不该是默认路径。）"
        )

    # ---------- adopt：把关卡现状读成基线（**一个 Actor 都不动**）----------
    if adopt:
        if mode != "incremental":
            raise ToolError("`adopt` 只跟 `mode=\"incremental\"` 合用（`full` 本来就会重建台账）。")
        if dry_run:
            raise ToolError("`adopt` 要写台账 —— 与 `dry_run` 冲突，请去掉一个。")
        if not live_refs:
            raise ToolError(f"`{OUR_FOLDER_ROOT}/` 下一个 Actor 都没有 —— 没有现状可认领。"
                            "关卡还没搭就用 `mode=\"full\"`。")
        report, cost = await _adopt_ledger(
            ctx, rows, live_refs, level, gate["plan_hash"], warns)
        calls += cost
        return report

    # ---------- 差异：这次到底要动哪几行（增量全靠它）----------
    orders: dict[str, dict] = {}
    dup: list[str] = []
    for r in rows:
        if r["uid"] in orders:
            dup.append(r["label"])
        orders[r["uid"]] = r
    if dup:
        raise ToolError("指令表里有**两行 label 相同**（uid 撞了）：" + "、".join(dup)
                        + " —— 增量对账认不出谁是谁。请把 label 改成互不相同再走增量。")

    # 增量是**按 Actor 的 label 认领**的（`_safe_actor_name` 会把括号 / # / 空格洗成下划线）：
    # 两个不同的 label 洗出同一个名字就认错了 —— 与其猜，不如当场拒收。
    name_owner: dict[str, list[str]] = {}
    for r in rows:
        name_owner.setdefault(r["name"], []).append(r["label"])
    clash = {n: ls for n, ls in name_owner.items() if len(ls) > 1}
    if clash and mode == "incremental":
        raise ToolError(
            "这些 label 洗干净之后**撞成同一个 Actor 名**了 —— 增量靠 label 认领，撞名就认不出来：\n· "
            + "\n· ".join(f"`{n}` ← " + "、".join(ls) for n, ls in clash.items())
            + "\n请把这几个 label 改得互不相同（阶段二允许同类多行，但每行要有区别）。")

    live_set = set(live_refs)          # `ledger_by_uid` 已在上面"定模式"时算好（同一份台账）

    if mode == "incremental" and not ledger_by_uid:
        raise ToolError(
            f"增量搭建需要**台账**（`{BUILD_STATE_PATH}`），现在没有 —— **一个 Actor 都没动**。"
            "两条出路：\n"
            "· 关卡里已经搭好了 → 先用 `adopt=true` 认领现状（只读 + 写台账，**不动任何 Actor**）；\n"
            "· 关卡还没搭 / 想推倒重来 → 用 `mode=\"full\"` 跑一次（会先清 UEMCP/ 再整批重摆）。")
    if mode == "incremental" and str(ledger.get("level") or "") not in ("", level):
        raise ToolError(
            f"台账记的关卡是 `{ledger.get('level')}`，当前关卡是 `{level}` —— **换图了**，"
            "台账里的 Actor 引用在这儿没有意义 —— **一个 Actor 都没动**。要么切回那张图，"
            "要么在**这张图上**用 `adopt=true` 重新登记，要么 `mode=\"full\"` 重摆。")

    # ---------- 关卡现状 vs 台账：**用户手改过的东西不许被静默删掉**（2026-09-26 加）----------
    # 这一步要**读关卡**（每个待查行 1 次 `get_actor_transform`，白膜行再 +2 次读组件尺寸），
    # 所以"查哪些行"有取舍 —— 扫描范围定在下面「算完差异之后」（见那段注释）。
    ledger_rows_all = [x for x in (ledger.get("rows") or []) if isinstance(x, dict)]
    known_refs = {str(x.get("actor") or "") for x in ledger_rows_all}
    unknown_refs = [ref for ref in live_refs if ref and ref not in known_refs]

    # ---------- 台账"还活着吗"：整体失效必须**当面报出来**（A 项 · 2026-09-27 · **尚未实测**）----
    # 为什么加（2026-09-27 真跑实测出来的洞）：`_live_vs_ledger` 只比"Actor 还能在关卡里找到"的行
    #   （见它开头的 `actor not in live_set: continue`），于是台账 89 行的引用**全部失效**时，
    #   它静默返回 0 条 —— 报告里**一个字都没有**，而"全表扫描只花 0 次调用"这件事本身
    #   恰恰就是"台账已经整体失效"的信号。那次真跑：台账记的是 `StaticMeshActor_48…` 那一批，
    #   关卡里已经是另一版 34 个 Actor，工具照常往下走，是我另外查关卡才发现的。
    #   **"扫描零成本"不等于"台账没问题"。**
    ledger_alive = [x for x in ledger_rows_all if str(x.get("actor") or "") in live_set]
    if ledger_rows_all and not ledger_alive:
        warns.append(
            f"⚠ **台账已整体失效**：`{BUILD_STATE_PATH.name}` 里 {len(ledger_rows_all)} 行记的 "
            f"Actor 引用**一个都不在当前关卡里**（多半是关卡被重新加载 / 被别的会话或手工内容顶掉 / "
            "换过图）。它**不能**再当基线。现状："
            f"`{OUR_FOLDER_ROOT}/` 下有 {len(live_refs)} 个 Actor，其中 {len(unknown_refs)} 个"
            "**台账里没有**。出路：认领现状"
            "（`execute_build(mode=\"incremental\", adopt=true)`，不动任何 Actor）"
            "或全量重摆（`mode=\"full\", force_full=true, accept_user_edits=true`）。")
    elif ledger_alive and len(ledger_alive) * 2 < len(ledger_rows_all):
        warns.append(
            f"⚠ 台账**大半失效**：{len(ledger_rows_all)} 行里只有 {len(ledger_alive)} 行的 Actor "
            "还能在当前关卡里找到 —— 增量会把这些行当成『台账说在、关卡里却没了』**重摆**。")

    added: list[dict] = []
    # ⚠ 这里是**三元组**：(指令行, 台账里的旧行, 变在哪的说明)。
    #   2026-09-26 真跑踩到过：只存两项 `(行, 说明)`，下面 `remove_refs` 却按"(行, 旧行)"解包 →
    #   拿到的 `old` 是那个**说明 list** → `AttributeError: 'list' object has no attribute 'get'`
    #   （Agent只显示「Error executing tool execute_build」，得去 logs/harness.log 才看到栈）。**别改回去。**
    changed: list[tuple[dict, dict, list[str]]] = []
    same: list[dict] = []
    gone: list[dict] = []
    drifted: list[tuple[dict, dict]] = []
    reclaim_actor: dict[str, str] = {}
    strays: list[str] = []

    if mode == "incremental":
        for uid, r in orders.items():
            old = ledger_by_uid.get(uid)
            if old is None:
                added.append(r)
                continue
            actor = str(old.get("actor") or "")
            if not actor or actor not in live_set:
                # 台账说它在这儿，关卡里却没有（或上次就没落成）→ 得重摆
                drifted.append((r, old))
                continue
            why = _row_changes(r, old)
            if why:
                changed.append((r, old, why))
            else:
                same.append(r)
        for uid, old in ledger_by_uid.items():
            if uid not in orders:
                gone.append(old)

        # 台账解释不了的活 Actor：读 label 试着认领；认不出就**拒收**（不猜、不删）
        known_refs = {str(x.get("actor") or "") for x in ledger_by_uid.values()}
        for ref in live_refs:
            if ref in known_refs:
                continue
            calls += 1
            try:
                label = str(await call_official(
                    ctx, "get_label", {"actor": {"refPath": ref}}, toolset=TS_ACTOR) or "")
            except ToolError as exc:
                strays.append(f"{ref}（读 label 失败：{exc}）")
                continue
            hit = next((r for r in added if r["name"] == label), None)
            if hit is None:
                strays.append(f"{ref}（label = `{label}`：台账里没有它，指令表里也没有这个名字）")
            else:
                added.remove(hit)
                reclaim_actor[hit["uid"]] = ref
        if strays:
            raise ToolError(
                f"`{OUR_FOLDER_ROOT}/` 下有**台账和指令表都解释不了**的 Actor —— "
                "**一个 Actor 都没动**：\n· " + "\n· ".join(strays)
                + "\n（多半是有人在 UE 里手动改名 / 手动摆过。增量只能靠对账，对不上就不猜、"
                  "也不删 —— 请先把它们清理掉，或用 `adopt=true` 重新登记，或跑一次 `full`。）")

    # ---------- 扫描范围：**读关卡现状**是要花官方调用的，所以按"要动多少"来定 --------
    # 2026-09-26 的取舍（别随手改）：每查一行 1 次 `get_actor_transform`，白膜行再 +2 次
    #   （读组件尺寸）。真跑增量时**只扫这次会被删掉的那些行** —— 保护够用，代价与"动多少"成正比
    #   （实测口径：89 场景里改 5 行 ≈ 多花 15 次，而不是 +189 次）。
    # 下面三种情况扫**全表**：
    #   · `dry_run` —— 它是"看清楚"的那一步（用户问「我手改的东西还在不在」就靠它）；
    #   · `accept_user_edits=true` —— 要按 plan 覆盖人工改动，得先知道**哪些**被改过；
    #   · 全量 —— 本来就要把 `UEMCP/` 下全部清掉，每一行都在风险里。
    if dry_run or accept_user_edits or mode == "full":
        scan_rows = ledger_rows_all
        scan_why = "全表（演练 / 要按 plan 覆盖人工改动 / 全量重摆）"
    else:
        scan_rows = gone + [o for _r, o, _w in changed]
        scan_why = "只扫这次会被删掉的行（真跑增量）"
    manual, used = await _live_vs_ledger(ctx, scan_rows, live_set)
    calls += used
    if manual:
        warns.append(
            f"⚠ 有 {len(manual)} 行**现状与台账不符**（人工改过 / 官方没照做；扫描范围：{scan_why}）："
            + "；".join(f"「{m['label']}」{'；'.join(m['why'])}" for m in manual[:6])
            + ("…" if len(manual) > 6 else ""))
    # ⚠ `unknown_refs` 只在**全量**时报/拦：增量那边有更细的处置（读 label 认领，认不出就拒收），
    #   在这里重复一遍会把"能认领回来的新行"误报成人工痕迹。
    if unknown_refs and mode == "full":
        warns.append(
            f"⚠ `{OUR_FOLDER_ROOT}/` 下有 {len(unknown_refs)} 个 Actor **台账里没有**"
            "（可能是你手动摆的，也可能是上一版没登记）：" + "、".join(unknown_refs[:8])
            + ("…" if len(unknown_refs) > 8 else ""))

    # 用户手改过的行：`accept_user_edits=true` 时**当"改动"重摆**（plan 覆盖现状）；
    #   不传的话它们**一个都不动**，只有"会被删掉的那些"才走下面那道拒收闸。
    if accept_user_edits and mode == "incremental":
        promoted: list[str] = []
        for m in manual:
            r = orders.get(m["uid"])
            if r is None or any(r["uid"] == x["uid"] for x, _o, _w in changed):
                continue
            changed.append((r, m["ledger"], [f"人工改过 → 按 plan 覆盖：{'；'.join(m['why'])}"]))
            promoted.append(r["uid"])
        if promoted:
            same = [r for r in same if r["uid"] not in set(promoted)]
            warns.append(f"按 `accept_user_edits=true`：{len(promoted)} 行**人工改过的**被当成改动重摆。")

    targets: list[dict] = rows if mode == "full" else (
        [r for r in added if r["uid"] not in reclaim_actor]
        + [r for r, _old, _why in changed]
        + [r for r, _old in drifted])
    # --- [完工-21] 落关卡按**全局五层序**排一遍 · ✅ 实测 2026-09-30T13:08Z（压测跑出来的）-------
    # 实测证据：26 行那次的 `rows` 回执顺序是「新增 20 行（层序）→ 改动 4 行（层序）」——
    #   `要摆` 里 **住宅 #1（③建筑层）** 排在 **灌木带（⑤植被层）之后**落。
    # 为什么：上面三段是「新增 / 改动 / 漂移」拼起来的，**各自内部**才是层序，跨段就断了。
    #   `_compose_build_rows()` 给每行编的 `index` **正是排完 `BUILD_LAYERS` 之后的序号**，
    #   所以按 `index` 稳定排一次就恢复全局层序（同层内仍是 plan 原序）。
    # ⚠ 必须在 `_place_rows()` **之前**排 —— 那之后 `results` 与 `targets` 是按位 zip 的，
    #   在中间动 targets 会把回执与指令错位。
    # ⚠ `mode="full"` 时 `targets` **就是 `rows` 本身**（别名）—— 那份本来就排好序了，
    #   而且 `list.sort()` 是**原地**的，排它会连带改 `rows`，所以这里显式跳过。
    if mode != "full":
        targets.sort(key=lambda r: int(r.get("index") or 0))
    remove_refs: list[str] = live_refs if mode == "full" else [
        str(old.get("actor") or "") for old in (gone + [o for _r, o, _w in changed])
        if str(old.get("actor") or "") in live_set]

    # ---------- 人工痕迹的处置：**默认拒收，不静默删**（2026-09-26 加）------------------
    # 这一步在**清场之前**：清场一旦开始，用户手改的东西就没了（客户原话：
    # 「用户手动改的东西不该删 —— 我 full 重摆给删了」）。
    if mode == "incremental":
        will_delete_uids = ({str(o.get("uid") or "") for o in gone}
                            | {str(o.get("uid") or "") for _r, o, _w in changed})
        traces = [m for m in manual if m["uid"] in will_delete_uids]
    else:
        # 全量会把 UEMCP/ 下**全部**清掉：台账能认但现状被改过的是人工痕迹，
        # 台账**根本解释不了**的活 Actor 也是（有人手动摆在了我们的文件夹里）。
        traces = list(manual) + [
            {"label": ref, "why": ["台账里没有它 —— 可能是你手动摆的，也可能是上一版没登记"]}
            for ref in unknown_refs]
    if traces and not dry_run:
        head = "\n".join(f"· 「{t['label']}」{'；'.join(t['why'])}" for t in traces[:12])
        more = f"\n（还有 {len(traces) - 12} 处没列全）" if len(traces) > 12 else ""
        if not accept_user_edits:
            # **第 1 步**：先留痕「我问了哪几行」（不碰关卡），再拒收 —— 与 `check_build_target` 同构。
            try:
                _save_user_edits_question(traces, level, mode)
                led = ("（✅ 已留痕：**这一批问的是哪几行** → "
                       f"`{USER_EDITS_PATH.name}`；第 2 步会拿它核对，别跳过这一步）")
            except OSError as exc:
                led = f"（⚠ 台账没写成：{exc} —— 这次**没留下**『问过哪几行』的记录）"
            raise ToolError(
                f"拒绝搭建：`{OUR_FOLDER_ROOT}/` 下有 **{len(traces)} 处人工痕迹**会被这次操作删掉 —— "
                "**一个 Actor 都没动**：\n" + head + more + "\n" + led + "\n"
                "两条出路：\n"
                "· **保住它们**（推荐）：把用户想要的改动**写回阶段二** —— "
                "`request_plan_change(items=[用户原话], by=\"用户\")` → `generate_plan(patch=[...])` → "
                "**重画两张图** → 用户看图确认 → `generate_build_orders()` → 增量落。"
                "这样 plan 与关卡就一致了（**用户手挪的那个位置会被保留**）。\n"
                "· **按 plan 覆盖它们**（会把上面这些行删掉重摆）：**把上面这份清单原样交给他"
                "（一行都别省）→ 停下等他打字**，拿到他本人的原话后再调 "
                "`execute_build(accept_user_edits=true, user_quote=\"他的原话\")`。\n"
                "⚠ 现在这是**两步闸**：没先问过 / 问过的那批与现在这批不一致 / 答复来得太快"
                f"（< {MIN_USER_EDITS_ANSWER_DELAY_S:g} 秒）—— **照样拒收**。"
                "⚠ 想先看清「会覆盖什么」，用 `dry_run=true`（演练不碰关卡、也不要原话）。\n"
                "（为什么拦：客户原话「用户手动改的东西不该删」。以前 `full` 无条件把 "
                f"`{OUR_FOLDER_ROOT}/` 下全清、增量**从不读现状** —— 手改的东西就这么没了，还没人报。）")
        # **第 2 步**：核对「问过没有 / 问的那批就是这批 / 有他本人的原话 / 距提问够久」。
        #   加固由来：外部 Agent 收到上面那条拒收后**自己**传了 `accept_user_edits=true`，
        #   用户手拖过的水面（差 30 cm）就这么被覆盖了 —— 覆盖必须是"两步 + 人的凭据"。
        _quote_ok = _user_edits_gate(traces, level, mode, user_quote)
        try:
            _mark_user_edits_answered(_quote_ok)
        except OSError:
            pass

    if dry_run:
        if mode == "incremental":
            diff_dry = _incremental_diff(
                targets, same, gone, changed, orders, reclaim_actor, drifted, manual,
                ledger_note=str(BUILD_STATE_PATH) + ("" if ledger_by_uid else "（**还没有**）"))
        else:
            diff_dry = {
                "全量重摆": True,
                "要删": f"清掉 {OUR_FOLDER_ROOT}/ 下**全部** {len(remove_refs)} 个旧 Actor",
                "要摆": f"指令表**全部** {len(targets)} 行",
                "没动": 0,
                "人工改过（现状与台账不符）": {m["label"]: m["why"] for m in manual},
                "台账解释不了的活 Actor": unknown_refs,
            }
        dry_warns = [
            f"演练：当前 `{OUR_FOLDER_ROOT}/` 下有 {len(live_refs)} 个 Actor"
            "（全量会把它们全清掉重摆；增量只动差异那几行）。",
            (f"⚠ 演练的是**全量重摆**（{mode_why or 'mode=full'}）：真跑会被**拒收**，"
             "除非显式传 `force_full=true`。只想改几行就别传 `mode`（默认 auto 走增量）。"
             if mode == "full" and ledger_by_uid else
             f"演练模式：**{('增量' if mode == 'incremental' else '全量')}**"
             f"（{mode_why or mode}）。"),
        ]
        _tgt_ans = _load_build_target().get("answer")
        if not (isinstance(_tgt_ans, dict) and _tgt_ans):
            dry_warns.append(
                "⚠ **真跑会被拒收**：还没拿到用户对**「搭哪张图」**的答复 —— 先 "
                "`check_build_target()`（只读地问）→ **停下等用户打字** → 带他的原话调 "
                "`check_build_target(decision=…, user_quote=…)` 把答复记下来。")
        if traces:
            dry_warns.append(
                (f"⚠ **真跑会被拒收**：有 {len(traces)} 处人工痕迹会被这次操作删掉，而你没传 "
                 "`accept_user_edits=true`（默认就是保护它们）—— 演练里一个 Actor 都没动。"
                 if not accept_user_edits else
                 f"⚠ 你传了 `accept_user_edits=true`：真跑会把这 {len(traces)} 处人工痕迹"
                 "**删掉重摆**（演练里没动）。"))
        return BuildReport(
            stage=f"阶段三 · 批量落关卡（演练 · {mode}）",
            level=level, dry_run=True, refused=False,
            reason="dry_run：只校验 + 算差异，**没动手**",
            plan_hash=gate["plan_hash"], planned=len(rows),
            mode=mode, ledger_path=str(BUILD_STATE_PATH), ledger_written=False,
            diff=diff_dry,
            removed=0, placed=0, verified=0, groups={}, rows=[],
            official_calls=calls,
            next_step=(f"演练：这次会删 {len(remove_refs)} 个、摆 {len(targets)} 个"
                       f"（指令表一共 {len(rows)} 行）。要真做就去掉 `dry_run` 重调 —— "
                       "那一次**只动上面『要删 / 要摆』里列出的行**，其余行一个 Actor 都不碰。"),
            warnings=warns + dry_warns,
        )

    removed = 0
    for ref in remove_refs:
        calls += 1
        try:
            await call_official(ctx, "remove_from_scene", {"actor": {"refPath": ref}}, toolset=TS_SCENE)
            removed += 1
        except ToolError as exc:
            warns.append(f"清旧 Actor 失败：{ref}（{exc}）")

    # ---------- ② 按表落 ----------
    results, cost = await _place_rows(ctx, targets)
    calls += cost

    # ---------- ③ 读回对账 ----------
    checked = await _verify_rows(ctx, results, targets, verify_mode, warns)
    calls += checked.used
    mismatches = checked.mismatches
    verified = checked.verified
    verified_uids = checked.verified_uids
    matched_uids = checked.matched_uids
    bad_by_uid = checked.bad_by_uid
    size_back = checked.size_back

    placed = sum(1 for res in results if res.ok)

    groups: dict[str, int] = {}
    for res, r in zip(results, targets):
        if res.ok:
            groups[r["folder"]] = groups.get(r["folder"], 0) + 1

    after_refs, after, cost = await _recount_and_heal(ctx, results, targets, warns)
    calls += cost

    if after != len(rows):
        failed = [r["label"] for r, res in zip(targets, results) if not res.ok]
        warns.append(f"⚠ 落完 `{OUR_FOLDER_ROOT}/` 下是 {after} 个，指令表是 {len(rows)} 个 —— "
                     "差额要查（这次没落成的行：" + ("、".join(failed) if failed else "无") + "）。")
    if verify_mode == "full":
        warns.append("白膜**尺寸**已逐行按组件 `RelativeScale3D`（×100 = 厘米）复核 —— 官方 "
                     "`get_actor_bounds` 对这类 Actor 恒返回 ±128 的**假包围盒**（实测），不能用它；"
                     "对不上的已列进 `mismatches`。")
    elif verify_mode == "transform":
        warns.append("⚠ `verify=\"transform\"`：只对账了**位置 / 朝向 / 缩放**，"
                     "**白膜尺寸（厚薄 / 高宽）这一档没验**（每行省下 2 次官方调用）—— "
                     "台账里那些行记 `size_readback=false`（尺寸是**指令值**，不是实测值）。"
                     "要连尺寸一起验就传 `verify=true`（默认）。")
    else:
        warns.append("⚠ `verify=false`：**没读回对账** —— 位置 / 朝向 / 缩放 / 白膜尺寸都没核实，"
                     "台账里 `readback=false`（记的是**指令值**，不是实测值）。")

    # ⚠ **对不上的行必须显式喊出来**（[完工-20]，2026-09-30）—— 不许让「落成 N」被读成 PASS：
    #   **落成 ≠ 对账通过**。落成数（`placed`）与对上数（`verified`）是两个不同的数，
    #   这里给一句不带修饰的结论 + 去哪看 + 本次没有退路（回滚是已知缺口）。
    if mismatches:
        warns.append(
            f"🔴 **读回对账有 {len(mismatches)} 行没对上 —— 这不是 PASS**："
            f"落成的 {placed} 行里只有 **{verified}** 行与指令一致。"
            "逐条原因见 `mismatches`；台账里对不上的行记 `verified=false` + `mismatch` 写明差在哪"
            "（⚠ `readback` 只说明**读过**、不代表对上）。"
            "⚠ 本次**没有回滚**（阶段三已知缺口）：那些行就留在关卡里**实际**的位置上 —— "
            "要么在 UE 里看一眼，要么回阶段二重规划。")
    warns.append("**全程未存盘** —— 关卡里有没有变脏，请你在 UE 里看标题栏的未保存标记。")

    # ---------- ④ 写台账（下次增量全靠它；**这是我们自己的 JSON，不是 UE 存盘**）----------
    # ⚠ 把「读过」（`verified_uids`）与「对上了」（`matched_uids`）**两个都**传进去 ——
    #   只传前者的话，一行验证失败的记录在台账里照样写 `readback: true`（[完工-20] 修的就是这个）。
    new_ledger_rows = _write_ledger_rows(
        rows, results, targets, ledger_by_uid, verified_uids, size_back, reclaim_actor,
        matched_uids, bad_by_uid)
    ledger_path = _save_build_state(level, gate["plan_hash"], mode, new_ledger_rows)

    # --- [完工-19] 重摆后**自动补材质** · ✅ 实测 2026-09-30T12:21Z（第 35 轮真跑）---------------
    # 实测证据：`execute_build()` 增量重摆「沥青车行道」1 行（`Actor_7 → Actor_8`，官方调用 19 次）
    #   → warnings 里出现「阶段五**自动补材质**：这次重摆的白膜 1 行 → 新贴 1 / 本来就对 0 / 没成 0」；
    #   随后 `evaluate_layout()` → **ok 7/7、`material_missing` 0**。
    #   ⚠ **关键**：那一次**没有手动跑 `apply_surfaces()`** —— 材质是这段代码自己补回去的
    #   （改之前的三次都得手动补：`Actor_3→4` / `Actor_4→5` / `Actor_6/7`）。
    # 为什么要有它（实测坐实 3 次的 bug）：白膜 = cube 图元 + **组件级** `overrideMaterials`，
    #   而"重摆" = `remove_from_scene` + 重新 `add_cube` → **新组件上没有覆盖** → 上次贴的材质没了。
    #   实测：`Actor_3→4`（r31 落路）、`Actor_4→5`（adopt 后重摆路）、`Actor_6/7`（r33 落地基+路）
    #   —— 每次都得**手动**再跑一次 `apply_surfaces()`，忘一次那一行就变成灰白模。
    # 口径（三条，别改）：
    #   ① **只补这次真落了**的白膜行 —— 没落的行组件没被换掉，材质还在，多写一遍是白费；
    #   ② **材质从 `config/surface_materials.json` 现读**（与 `apply_surfaces` 同一张表、同一个函数），
    #      不另存一份"落的时候用的材质"，免得两处口径将来打架；
    #   ③ **补失败不算搭建失败** —— 这一步是收尾，不是闸；失败只写进 `warnings`（异常也吞在这一层）。
    #   幂等：`_surface_apply_row` 自己先读现值，是那块材质就记 `already`、不重复写。
    if placed:
        mats, mats_note = _surface_materials()
        if mats_note:
            warns.append(mats_note)
        led_by_uid = {str(x.get("uid")): x for x in new_ledger_rows}
        surf: list[dict] = []
        for res, r in zip(results, targets):
            if not res.ok or r.get("kind") != "whitebox":
                continue
            want = str(mats.get(str(r.get("element_key"))) or "")
            if not want:
                continue                      # 这颗元素本来就没配材质 → 不是"丢了"，别贴
            led = led_by_uid.get(str(r.get("uid"))) or {}
            try:
                status, err, used = await _surface_apply_row(ctx, led, want)
                calls += used
            except ToolError as exc:
                status, err = "failed", str(exc)
            surf.append({"label": str(r.get("label") or ""), "material": want,
                         "actor": str(led.get("actor") or ""),
                         "status": status, "error": err})
        if surf:
            n_ap = sum(1 for s in surf if s["status"] == "applied")
            n_al = sum(1 for s in surf if s["status"] == "already")
            n_fa = sum(1 for s in surf if s["status"] == "failed")
            warns.append(
                f"阶段五**自动补材质**：这次重摆的白膜 {len(surf)} 行 → 新贴 {n_ap} / "
                f"本来就对 {n_al} / 没成 {n_fa}"
                + ("；没成的：" + "、".join(f"{s['label']}（{s['error']}）"
                                          for s in surf if s["status"] == "failed")
                   if n_fa else "")
                + "。⚠ 重摆 = 删了重建，**新组件上没有材质覆盖**（2026-09-30 实测坐实 3 次："
                  "`Actor_3→4` / `Actor_4→5` / `Actor_6/7`）—— 所以这里就地补，"
                  "不用你再手动跑一次 `apply_surfaces()`。")
            for s in surf:
                if s["status"] == "failed":
                    warns.append(f"⚠ 自动补材质没成：「{s['label']}」{s['error']}")

    if mode == "incremental":
        diff_summary = _incremental_diff(
            targets, same, gone, changed, orders, reclaim_actor, drifted, manual)
        warns.append(f"增量：没动的 {len(same)} 行**一个 Actor 都没碰**（台账里那些行原样保留）。")
        warns.append(f"本次模式：**增量**（{mode_why or 'mode=incremental'}）—— 只动了 "
                     f"{len(targets)} 行、官方调用 {calls} 次。**下次改几行也不用传参数**（默认就是这条）。")
    else:
        diff_summary = {
            "全量重摆": True, "要删": len(remove_refs), "要摆": len(targets), "没动": 0,
            "人工改过（现状与台账不符）": {m["label"]: m["why"] for m in manual},
            "台账解释不了的活 Actor": unknown_refs,
        }
        warns.append(
            f"⚠ 本次模式：**全量重摆**（{mode_why or 'mode=full'}）—— 删了 {removed} 个、摆了 {placed} 个、"
            f"官方调用 {calls} 次。**下次只改几行时别传 `mode`**：默认 `auto` 只会动变动的那几个。")
    if manual:
        # 客户原话：「它不会告诉我『用户手动改了地皮你别删』」—— 这一条就是那句"告诉"。
        # ⚠ 覆盖那一路必须**带上人的凭据**（`user_quote`）—— 不然这条报文只有"覆盖了"三个字，
        #   事后谁也答不了"是谁同意的"（2026-09-30 加固）。
        warns.append(
            (f"⚠ 有 {len(manual)} 行**人工改过**（现状与台账不符），本次按 `accept_user_edits=true` "
             f"**按 plan 覆盖**（已删掉重摆）；依据 = **用户原话**：「{user_quote.strip()}」："
             if accept_user_edits else
             f"⚠ 有 {len(manual)} 行**人工改过**（现状与台账不符）—— 本次**没动它们**（默认保护）：")
            + "；".join(f"「{m['label']}」{'；'.join(m['why'])}" for m in manual[:6])
            + ("…" if len(manual) > 6 else "")
            + ("" if accept_user_edits else
               "。要让 plan 覆盖它们：回阶段二把改动写进 plan（`generate_plan(patch=[...])`）"
               "→ 重画图 → 用户确认 → 增量落；或**先问用户、拿到他的原话**之后传 "
               "`accept_user_edits=true, user_quote=\"他的原话\"`（只传 true 会被拒收）。"))
    if unknown_refs and mode == "full":
        warns.append(f"⚠ 全量里**顺手清掉了 {len(unknown_refs)} 个台账解释不了的 Actor**（你传了 "
                     "`accept_user_edits=true`）：" + "、".join(unknown_refs[:8])
                     + ("…" if len(unknown_refs) > 8 else ""))
    warns.append(f"台账已更新（`{ledger_path}`）—— 我们自己的 JSON，**不是 UE 存盘**。")

    return BuildReport(
        stage=f"阶段三 · 批量落关卡（{mode}）",
        level=level, dry_run=False, refused=False, reason="",
        plan_hash=gate["plan_hash"], planned=len(rows), removed=removed, placed=placed,
        verified=verified, mismatches=mismatches, groups=groups, rows=results,
        mode=mode, ledger_path=ledger_path, ledger_written=True, diff=diff_summary,
        official_calls=calls,
        next_step=(
            (f"增量完成：这次动了 {len(targets)} 行（落成 {placed}、删掉 {removed}），"
             f"**没动的 {len(same)} 行原样留着**。"
             # ⚠ 对不上时**不许把"完成"读成 PASS**（[完工-20]）—— 所以这句排在最前面
             + (f"🔴 **先看这个：{len(mismatches)} 行读回没对上**（落成 {placed} / 对上 {verified}）"
                "—— **别当成做完了**，逐条查 `mismatches`，台账里那些行 `verified=false`；"
                if mismatches else "")
             + "① 在 UE 里看这几处对不对"
             "（`rows` 里 `ok=false` 的就是没落成的）；② `mismatches` 非空就逐条查；"
             "③ 还要改就回阶段二：改 plan → **重画图（写新指纹 + 本轮变更集那几行）** → "
             "让用户看图确认 → "
             "`generate_build_orders()` → 再 `execute_build(mode=\"incremental\")`；"
             "④ 存不存盘由你在 UE 里决定（阶段三自己绝不存）。")
            if mode == "incremental" else
            (f"① 在 UE 里看：outliner 的 `{OUR_FOLDER_ROOT}/` 下应当有 {len(rows)} 个 Actor"
             f"（分 {len(groups)} 组）—— 位置/朝向对不对以**你的眼睛**为准；"
             "② `mismatches` 非空就逐条查；③ ⚠ 房子『正门相对还是背对背』取决于"
             "**资产自身在 rot 0 时朝哪边**，这条**仍未实测**（docs/阶段三 §11.2）—— "
             "用视口截图看一眼再定要不要给房子 ±180°；"
             "④ 确认无误后**由你在 UE 里决定要不要存盘**（阶段三自己绝不存）。")
        ),
        warnings=warns,
    )


# --- 阶段四 · 导出 / 交换（2026-09-27 实现；用户拍板：「以后方便在 Blender 和 UE 等之间切换」）--  【模块：exchange】
# 由来：文档里第四阶段叫「布局序列化（JSON 主文件 + 统计，支持按 ID 增量更新）」。2026-09-27
#   当天试做又**整块回退**，回退理由写的是「**先定清谁消费**（外部工具 or 我们自己）」。
#   现在消费者明确了 —— 要在 **Blender / UE** 之间搬同一套布局 —— 所以这一版把它定义成
#   **导出 / 交换**，而不是"再存一份主文件"（那才是当初回退的原因）。
#
# ⚠ **只有场景描述，没有几何**（2026-09-27 实测：官方 20 个 AssetTools 里没有 glTF / FBX /
#   场景导出；Sequencer 那套 FBX 是**动画**，不是场景）。所以这份文件的作用是
#   "让 Blender 那边照着**重摆一遍**"，不是"把模型搬过去"。
# ⚠ 用户 2026-09-27 定的两条口径：
#   ① **单向导出**：外部改完只出「对账报告」（`check_exchange()`），要落到 plan 必须回阶段二
#      （`request_plan_change` → `generate_plan(patch=…)` → 重画图 → **用户看图确认**）——
#      **不许拿外部文件直接改 plan**：那道"几何改动必须经用户点头"的闸不能绕；
#   ② **两边都写**：我们自己的 `views/exchange/`（零 UE 依赖）+ 经官方 `write_file` 复制进
#      UE 工程的 `Saved/`。
#
# ⚠ **写 UE 那边必须用绝对路径**（2026-09-27 实测踩到）：传相对路径 `Saved/…` 会被解析到
#   **引擎进程的工作目录**（实测落到了 `d:\epicgame\ue_5.8\engine\binaries\win64\saved\…`）并
#   被拒；官方在错误里列了允许的根 = `<工程>\Content` 与 `<工程>\Saved`。
#   所以本工具**不猜工程目录**（官方没有"取工程目录"的工具）—— 要写 UE 就由调用方把工程
#   的 `Saved` 绝对路径交进来。

EXCHANGE_DIR = VIEWS_DIR / "exchange"
EXCHANGE_JSON = EXCHANGE_DIR / "scene_v1.json"
EXCHANGE_CSV = EXCHANGE_DIR / "scene_v1.csv"

_EXCHANGE_CSV_COLS = ("id", "layer", "element_key", "kind", "label",
                      "pos_m", "footprint_m", "height_m", "rot_deg", "scale",
                      "material", "asset_path", "folder")


def _exchange_height_m(order: dict, bbox_cm: list | None) -> float | None:
    """这一条在世界里的**高度**（米）。

    两种来源（都在我方数据里，不用猜）：
      · **白膜行** —— 图元的三维尺寸是阶段一登记的（`size_cm[2]`，如车行道 15 cm）；
      · **资产行** —— 资产自身的包围盒 Z × `scale`（实测 `house1` 50.12 cm × 16 ≈ 8.02 m）。
    ⚠ 量不到就给 `None`（**不编一个数** —— 硬规则 6 的同一条纪律）。
    """
    if str(order.get("kind") or "") == "whitebox":
        sc = order.get("size_cm")
        if isinstance(sc, list) and len(sc) == 3:
            try:
                return round(float(sc[2]) / 100.0, 4)
            except (TypeError, ValueError):
                return None
        return None
    if isinstance(bbox_cm, list) and len(bbox_cm) == 3:
        scale = order.get("scale") or []
        k = float(scale[0]) if (isinstance(scale, list) and scale) else 1.0
        try:
            return round(float(bbox_cm[2]) * k / 100.0, 4)
        except (TypeError, ValueError):
            return None
    return None


def _exchange_coordinate_note() -> dict:
    """交换文件的**坐标口径 + 到 Blender 的换算**（写给消费端看，别让它猜）。

    ⚠ 文件里**只存一套坐标**（plan / UE 的口径：米、左手系、Z 上、X 前、Y 右）——
      两套坐标混在一个文件里最容易出错，所以把换算**写成公式**放在这里。
    ⚠ Blender 是**右手系 Z-up**：把 Y 取反即完成手性翻转；但手性一翻，**绕 Z 的旋转方向
      也跟着反**，所以 `yaw` 要取负。
    ⚠ 上面这条换算是**推导**（本工程没有 Blender，没法实测）—— 消费端第一次用请自己核一眼。
    """
    return {
        "handedness": "left",
        "up": "Z",
        "forward": "X",
        "right": "Y",
        "unit": "m",
        "right_normal_rule": "right_normal(d) = (-dy, dx)",
        "note": "与 UE 一致（左手系 Z-up、X 前、Y 右）。**文件里只有这一套坐标**。",
        "to_blender": {
            "system": "Blender 是右手系 Z-up",
            "location": "[x, -y, z]",
            "rotation_z_deg": "-yaw",
            "scale": "同（1:1）",
            "note": ("Y 取反 = 手性翻转；手性一翻，绕 Z 的旋转方向也反，故 yaw 取负。"
                     "⚠ 推导，未实测。"),
        },
    }


def _exchange_objects(plan: dict, rows: list[dict], asset_list: dict) -> list[dict]:
    """把 plan + 指令行 + 资产清单拼成**每个物体一条**的交换记录（纯计算，不碰 UE）。

    为什么以**指令行**为主干：它带着阶段三补出来的 **Z**、**层**（①地基…⑤植被）、
    白膜图元尺寸、以及阶段五要贴的**材质线索** —— plan 里没有这些。
    plan 那边补进来的是 `footprint_m` / `shape` / `note`（人的原话）。
    两边靠 **`id`（＝台账与指令表共用的 `uid`：`element_key|label`）** 对齐。
    """
    plan_rows: dict[str, dict] = {}
    for kind in ("assets", "whiteboxes"):
        for r in (plan.get(kind) or []):
            if isinstance(r, dict):
                plan_rows[f"{r.get('element_key')}|{r.get('label')}"] = r
    bbox_by_path: dict[str, list] = {}
    for it in (asset_list.get("items") or []):
        if isinstance(it, dict):
            p = str(it.get("asset_path") or "")
            if p and isinstance(it.get("size_cm"), list):
                bbox_by_path[p] = it["size_cm"]

    out: list[dict] = []
    for r in rows:
        uid = str(r.get("uid") or "")
        p = plan_rows.get(uid) or {}
        loc = list(r.get("loc_cm") or [0.0, 0.0, 0.0])
        path = str(r.get("asset_path") or "")
        out.append({
            # ---- 跨 DCC 通用（Blender / UE 都看得懂；单位**一律是米**）----
            "id": uid,
            "label": r.get("label"),
            "element_key": r.get("element_key"),
            "layer": r.get("layer"),
            "kind": r.get("kind"),
            "pos_m": [round(float(loc[0]) / 100.0, 4), round(float(loc[1]) / 100.0, 4),
                      round(float(loc[2]) / 100.0, 4)],
            "footprint_m": [float(v) for v in (p.get("footprint_m") or [0.0, 0.0])],
            "height_m": _exchange_height_m(r, bbox_by_path.get(path)),
            "rot_deg": float((r.get("rot") or {}).get("yaw") or 0.0),
            "scale": list(r.get("scale") or []),
            "material": r.get("surface_material_hint") or "",
            "note": str(p.get("note") or ""),
            # ---- UE 专属（Blender 那边按 element_key 挂自己的资产，用不上这两样）----
            "ue": {"asset_path": path, "folder": r.get("folder") or ""},
        })
    return out


def _exchange_doc(plan: dict, rows: list[dict], asset_list: dict, level: str,
                  plan_hash: str) -> dict:
    """组装**交换主文件**的内容（纯计算）。统计那一段就是文档要求的「+ 统计」。"""
    objs = _exchange_objects(plan, rows, asset_list)

    def _tally(field: str) -> dict:
        d: dict[str, int] = {}
        for o in objs:
            k = str(o.get(field) or "")
            d[k] = d.get(k, 0) + 1
        return dict(sorted(d.items()))

    world = plan.get("world") or {}
    size = [float(v) for v in (world.get("size") or [0.0, 0.0])]
    area = 0.0
    for o in objs:
        fp = o.get("footprint_m")
        if isinstance(fp, list) and len(fp) >= 2:
            area += float(fp[0]) * float(fp[1])
    return {
        "schema": "uemcp.exchange.scene/v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "plan": "views/plan_v1.json",
            "plan_hash": plan_hash,
            "orders": BUILD_ORDERS_PATH.name,
            "level": level,
            "note": ("**派生物**：权威几何永远是 plan_v1.json。这份文件被人改过**不会**自动"
                     "回到 plan —— 用 `check_exchange()` 看对账报告，再回阶段二改。"),
        },
        "unit": "m",
        "coordinate_system": _exchange_coordinate_note(),
        "world": {
            "center": [float(v) for v in (world.get("center") or [0.0, 0.0])],
            "size": size,
            "bounds": world.get("bounds") or {},
        },
        "objects": objs,
        "stats": {
            "objects": len(objs),
            "assets": sum(1 for o in objs if o.get("kind") == "asset"),
            "whiteboxes": sum(1 for o in objs if o.get("kind") == "whitebox"),
            "by_element": _tally("element_key"),
            "by_layer": _tally("layer"),
            "world_area_m2": round(size[0] * size[1], 2),
            "object_footprint_area_m2": round(area, 2),
            "note": ("footprint 面积是各物体占地**相加**，会互相重叠、**不等于「覆盖率」**"
                     "（重叠是允许的，阶段二只查越界）。"),
        },
    }


def _csv_cell(value: Any) -> str:
    """CSV 一格：含逗号 / 引号 / 换行就按 RFC4180 加引号。"""
    s = "" if value is None else str(value)
    if any(ch in s for ch in ',"\n'):
        s = '"' + s.replace('"', '""') + '"'
    return s


def _exchange_csv(objs: list[dict]) -> str:
    """交换文件的**表格视图**（同一份数据，给人看 / 给表格工具）。

    ⚠ 写成 **utf-8-sig（带 BOM）** —— Excel 直接双击中文不乱码；
      脚本读请用 `encoding="utf-8-sig"`（用普通 utf-8 读，第一格会带一个 BOM 字符）。
    """
    lines = [",".join(_EXCHANGE_CSV_COLS)]
    for o in objs:
        ue = o.get("ue") or {}
        cells = [
            o.get("id"), o.get("layer"), o.get("element_key"), o.get("kind"), o.get("label"),
            ",".join(f"{float(v):g}" for v in (o.get("pos_m") or [])),
            ",".join(f"{float(v):g}" for v in (o.get("footprint_m") or [])),
            "" if o.get("height_m") is None else f"{float(o['height_m']):g}",
            f"{float(o.get('rot_deg') or 0.0):g}",
            ",".join(f"{float(v):g}" for v in (o.get("scale") or [])),
            o.get("material"), ue.get("asset_path"), ue.get("folder"),
        ]
        lines.append(",".join(_csv_cell(c) for c in cells))
    return "\n".join(lines) + "\n"


@mcp.tool()
async def export_layout(
    ctx: Context[AppContext],
    ue_dir: Annotated[str, Field(
        description=("要同时写进 UE 工程时，给它 **`Saved` 目录的绝对路径**"
                     "（实测例：`<你的UE工程>\\Saved`）；留空 = 只写我们自己的 `views/exchange/`。"
                     "⚠ 必须绝对路径 —— 相对路径会被解析到**引擎目录**并被拒（实测踩到），"
                     "而官方没有『取工程目录』的工具，所以这条路得你给，不是我们猜"))] = "",
) -> ExchangeReport:
    """**阶段四 · 导出/交换**：把当前布局导成一份**与 DCC 无关**的场景描述。
    ⚠ **编排内部件**：只在"要把这套布局搬到别的 DCC"时才需要；平时不用调。

    给谁用：**Blender / UE 之间搬同一套布局**（用户 2026-09-27：「以后方便在 Blender 和 UE
    等之间切换」）。所以文件里带的是**语义 + 尺寸 + 变换 + 材质 + 层**，不是模型。

    做三件事：
      ① 读 `plan_v1.json`（权威几何，米）+ `catalog/asset_list.json`（资产实测包围盒）
         + 重算一遍指令行（拿阶段三补的 **Z**、**层**、白膜尺寸、材质线索）；
      ② 落盘 `views/exchange/scene_v1.json`（机器读）+ `scene_v1.csv`（表格视图）；
      ③ 给了 `ue_dir` 就再经官方 `write_file` **复制一份**进 UE 工程 `Saved/UEMCP/`
         —— 那一步失败**不算导出失败**（我们自己的两份已经写好了），失败原文如实带出来。

    ⚠ **导不了几何**（实测：官方没有 glTF / FBX / 场景导出工具）—— 消费端是照着这份描述
      **重摆一遍**。
    ⚠ **只读导出**：权威几何永远是 `plan_v1.json`。外部改过这份文件**不会**自动回到 plan ——
      用 `check_exchange()` 看对账报告，再按阶段二那套改（`request_plan_change` →
      `generate_plan(patch=…)` → **重画图** → 用户看图确认）。
    ⚠ 写 UE 那一步只写**纯文本**、落在工程 `Saved/` 下：**不存盘关卡、不碰任何资产**。
    """
    calls = 0
    warns: list[str] = []
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划数据：先走完阶段二（generate_plan → 出图 → confirm_plan）。")
    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    gate = planning.gate_check(plan)
    asset_list = load_json(ASSET_LIST_PATH) or {}
    rows, _z_rules, row_warns = _compose_build_rows(plan, asset_list)
    warns += row_warns
    if not gate["confirmed"]:
        warns.append("⚠ 这一版规划**还没被用户确认**（闸门关着）—— 导出去的这份没经过验收，"
                     "别拿它当准的。")

    # 当前关卡（**只读**；拿不到就记空，不影响导出）
    level = ""
    try:
        level = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
        calls += 1
    except ToolError as exc:
        warns.append(f"读当前关卡失败（{exc}）—— 交换文件里的 `source.level` 记空。")

    doc = _exchange_doc(plan, rows, asset_list, level, str(gate.get("plan_hash") or ""))
    EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
    save_json(EXCHANGE_JSON, doc)
    csv_text = _exchange_csv(doc["objects"])
    EXCHANGE_CSV.write_text(csv_text, encoding="utf-8-sig")

    ue_written: list[str] = []
    ue_error = ""
    if str(ue_dir or "").strip():
        base = str(ue_dir).strip().rstrip("\\/")
        payloads = (
            (EXCHANGE_JSON.name, json.dumps(doc, ensure_ascii=False, indent=2)),
            (EXCHANGE_CSV.name, csv_text),
        )
        for name, text in payloads:
            target = f"{base}/UEMCP/{name}"
            calls += 1
            try:
                await call_official(ctx, "write_file",
                                    {"file_path": target, "content": text}, toolset=TS_ASSET)
                ue_written.append(target)
            except ToolError as exc:
                ue_error = f"{target}（{exc}）"
                break
        if ue_error:
            warns.append(
                f"⚠ 写 UE 工程失败：{ue_error} —— **我们自己的两份已经写好了**"
                f"（`{EXCHANGE_JSON}` / `{EXCHANGE_CSV}`），这一步只是复制过去。"
                "⚠ 要**绝对路径**、且必须落在工程的 `Content` / `Saved` 下（实测）。")
        else:
            warns.append(f"已同时写进 UE 工程：{ue_written}")
    else:
        warns.append("这次**没写 UE 工程**（`ue_dir` 留空）。要写就传工程 `Saved` 的**绝对路径** —— "
                     "⚠ 官方没有『取工程目录』的工具，所以这个路径得由你给。")

    if ue_written:
        warns.append("⚠ 写 UE 那一步用的是官方 `write_file`（**纯文本**）：**没有存盘关卡**，"
                     "也没碰任何资产。")
    stats = doc["stats"]
    return ExchangeReport(
        stage="阶段四 · 导出/交换（布局序列化）",
        json_path=str(EXCHANGE_JSON), csv_path=str(EXCHANGE_CSV),
        plan_hash=str(gate.get("plan_hash") or ""), level=level,
        objects=int(stats["objects"]), stats=stats,
        ue_written=ue_written, ue_error=ue_error, official_calls=calls,
        next_step=(
            f"导出 {stats['objects']} 个物体（资产 {stats['assets']} + 白膜 {stats['whiteboxes']}）"
            f"到 `{EXCHANGE_JSON}`。拿给 Blender 时记住：文件是**米、左手系 Z-up**，"
            "换算规则写在文件里的 `coordinate_system.to_blender`（`location=[x,-y,z]`、`yaw` 取负）。"
            "⚠ 外面改完**不会**自动回到 plan —— 调 `check_exchange()` 看对账报告，"
            "要落到 plan 就回阶段二：`request_plan_change` → `generate_plan(patch=…)` → "
            "**重画图** → 用户看图确认。"),
        warnings=warns,
    )


@mcp.tool()
async def check_exchange(
    path: Annotated[str, Field(
        description=(f"要比的交换文件；留空 = 我们自己的 `views/exchange/{EXCHANGE_JSON.name}`"))] = "",
) -> ExchangeDiffReport:
    """**阶段四 · 回读对账**：把（可能被 Blender 那边改过的）交换文件与**当前 plan** 逐条比。
    ⚠ **编排内部件**：只读对账，配合 `export_layout()` 用；平时不用调。

    ⚠ **本工具不改 plan、不写任何文件** —— 它只出报告。为什么（用户 2026-09-27 定的）：
      几何改动**必须经用户看图点头**（阶段二那道闸），拿外部文件直接改 plan 就是绕过它。
      要落到 plan 走这一串：`request_plan_change(items=[...], by=...)` →
      `generate_plan(patch=[...])` → **重画图** → 用户看图确认 → `confirm_plan`。
    ⚠ **报告 ≠ 确认**：它连"用户看过"都算不上，别拿它当放行凭据。

    怎么比（逐条，按 `id` 对齐）：
      `same` 两边一致 / `changed` 位置·占地·朝向·材质有变 / `added` 文件里有、plan 里没有
      / `removed` plan 里有、文件里没有。位置与占地的容差 **0.01 m**（1 cm），朝向按角度差比。
    """
    src = Path(str(path).strip()) if str(path or "").strip() else EXCHANGE_JSON
    doc = load_json(src)
    if not isinstance(doc, dict) or not doc.get("objects"):
        raise ToolError(
            f"读不到交换文件、或里面没有 `objects`：`{src}`。"
            "先用 `export_layout()` 导一份；如果你比的是别处的文件，把绝对路径传进来。")
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划数据（views/plan_v1.json）—— 没有可比的基准。")
    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    now_hash = planning.plan_geometry_hash(plan)
    file_hash = str((doc.get("source") or {}).get("plan_hash") or "")

    warns: list[str] = []
    _mats, _mats_note = _surface_materials()
    if _mats_note:
        warns.append(_mats_note)
    if file_hash != now_hash:
        warns.append(
            f"⚠ 这份交换文件是**按另一版 plan 导出的**（文件记 {file_hash[:10] or '(空)'}… ≠ "
            f"现在这一版 {now_hash[:10]}…）—— 多半中间改过 plan。逐条比仍有参考价值，"
            "但「哪里算改动」以**现在这一版**为准。")

    plan_rows: dict[str, dict] = {}
    for kind in ("assets", "whiteboxes"):
        for r in (plan.get(kind) or []):
            if isinstance(r, dict):
                plan_rows[f"{r.get('element_key')}|{r.get('label')}"] = r

    rows_out: list[dict] = []
    same = changed = added = removed = 0
    seen: set[str] = set()
    for o in (doc.get("objects") or []):
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or "")
        seen.add(oid)
        p = plan_rows.get(oid)
        if p is None:
            added += 1
            rows_out.append({"id": oid, "label": o.get("label"), "kind": "文件里有、plan 里没有",
                             "why": ["这条在 plan 里找不到（外部新增？还是 label 被改过？）"]})
            continue
        why: list[str] = []
        fp = [float(v) for v in (o.get("footprint_m") or [0.0, 0.0])]
        pfp = [float(v) for v in (p.get("footprint_m") or [0.0, 0.0])]
        # 位置比**平面**那两位（`pos_m` 是米、含 Z；plan 只有平面坐标）
        pos = [float(v) for v in (o.get("pos_m") or [0.0, 0.0, 0.0])][:2]
        ppos = [float(v) for v in (p.get("pos") or [0.0, 0.0])]
        if any(abs(a - b) > 0.01 for a, b in zip(pos, ppos)):
            why.append(f"位置 {pos} != plan {ppos}（米）")
        if any(abs(a - b) > 0.01 for a, b in zip(fp, pfp)):
            why.append(f"占地 {fp} != plan {pfp}（米）")
        if not _angle_close(float(o.get("rot_deg") or 0.0), float(p.get("rot_deg") or 0.0)):
            why.append(f"朝向 {o.get('rot_deg')} != plan {p.get('rot_deg')}（度）")
        want_mat = _mats.get(str(p.get("element_key") or ""), "")
        if str(o.get("material") or "") != want_mat:
            why.append(f"材质 {o.get('material') or '(空)'} != 本工具表里的 {want_mat or '(无)'}")
        if why:
            changed += 1
            rows_out.append({"id": oid, "label": o.get("label"), "kind": "改了", "why": why})
        else:
            same += 1
    for oid, p in plan_rows.items():
        if oid not in seen:
            removed += 1
            rows_out.append({"id": oid, "label": p.get("label"), "kind": "plan 里有、文件里没有",
                             "why": ["这条在交换文件里找不到（外部删了？还是 label 被改过？）"]})

    if changed or added or removed:
        warns.append(f"⚠ 有差异：改 {changed} / 文件新增 {added} / 文件删除 {removed} —— "
                     "**本工具什么都不改**，也**不是确认**。要落到 plan 必须回阶段二那一串。")
    else:
        warns.append("两边逐条一致（位置 / 占地 / 朝向 / 材质全对得上）—— 没什么要处理的。")
    return ExchangeDiffReport(
        stage="阶段四 · 回读对账（只出报告，不改 plan）",
        json_path=str(src), plan_hash=now_hash, file_plan_hash=file_hash,
        hash_match=(file_hash == now_hash),
        same=same, changed=changed, added=added, removed=removed, rows=rows_out,
        next_step=(
            (f"逐条看 `rows`：改 {changed}、文件新增 {added}、文件删除 {removed}。"
             "要落到 plan：① `request_plan_change(items=[...], by=…)` 把「改什么 / 谁要的」记进台账；"
             "② `generate_plan(patch=[...])` 只改那几行；③ **重画图**（写新指纹 + 本轮变更集那几行）；"
             "④ 用户看图点头 → `confirm_plan`；⑤ `generate_build_orders()` → "
             "`execute_build(mode=\"incremental\")` 只重摆改动的那几个。"
             if (changed or added or removed) else
             "两边一致，不用做什么。要再导一份就把 Blender 那边的改动先写回文件再比。")
        ),
        warnings=warns,
    )


# --- 阶段五 · 给白膜贴表面材质（2026-09-27 实现；**机制已实测**）------------------  【模块：surfaces】
# 由来：用户 2026-09-27「继续完成项目」，并定下 `ground`（世界地基）贴草地。
#
# ⚠ **路线是实测出来的，不是猜的**（2026-09-27 拿世界地基那一行真跑过）：
#   ① 官方**没有**"给单个实例换材质"的现成工具 —— 三条路都堵着：
#      · `PrimitiveTools` 四个图元工具（cone/cylinder/sphere/cube）**都没有材质参数**；
#      · `StaticMeshTools` 里**只有** `set_material`，那改的是**资产** —— 白膜的网格是
#        `/Engine/BasicShapes/Cube`，改资产会把**引擎自带网格**一起改掉（所有实例跟着变）。
#        它描述里提到的 `set_component_material_override` **并不在工具列表里**（实测点名）；
#      · `MaterialInstanceTools` 只管改材质实例**自身**的参数 / 父级，不管"贴到谁身上"。
#   ② 能走通的只有**组件级通用属性写入**：
#      `ObjectTools.set_properties(组件, '{"overrideMaterials":[{"refPath":"<包路径>.<资产名>"}]}')`
#      —— 属性名 `overrideMaterials` 是从 `ObjectTools.list_properties(组件)` 里**读出来**的
#      （`{"type":"array","items":MaterialInterface,"description":"材质重载。"}`），不是猜的。
#   ③ ⚠ **路径必须带对象名后缀**：`/Game/…/MI_X` 会被拒，官方原话
#      `is not a valid object path for property 'OverrideMaterials'`；
#      要 `/Game/…/MI_X.MI_X` —— 本文件早就有 `to_object_path()` 干这个转换。
#   ④ 读回用 `get_properties(组件, ["overrideMaterials"])`，它返回的是 **JSON 字符串**。
# ⚠ 本工具**绝不存盘**；只改**组件材质覆盖**，不动几何 / 位置 / 分组。


def _surface_matches(raw: Any, material: str) -> bool:
    """读回的 `overrideMaterials` 里第一块是不是就是目标材质（幂等判断用它）。

    ⚠ `get_properties` 返回的是 **JSON 字符串**（它的 outputSchema 就是 string），得自己解；
      解不动、或形状不认识 → 一律当**不匹配** —— 安全侧：宁可多写一遍，也不假装已经贴好了。
    """
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    if not isinstance(doc, dict):
        return False
    arr = doc.get("overrideMaterials")
    if not isinstance(arr, list) or not arr:
        return False
    first = arr[0]
    got = str((first or {}).get("refPath") or "") if isinstance(first, dict) else str(first)
    return got == to_object_path(material)


async def _surface_apply_row(
    ctx: Context[AppContext], row: dict, material: str, dry: bool = False
) -> tuple[str, str, int]:
    """给台账里这一行贴材质。返回 `(status, error, 用掉几次官方调用)`。

    status ∈ `applied`（贴上了）/ `already`（本来就是它，幂等跳过）/ `failed` / `dry`。
    ⚠ `dry=True` 时**仍然去找组件**（这一趟的意义就是"确认这个 Actor 还活着、
      而且有 `cube` 组件"），但**读也不写** —— 一个字节都不改。
    """
    used = 0
    actor = str(row.get("actor") or "")
    if not actor:
        return "failed", "台账这一行没有 Actor 引用（上次没落成 / 引用已失效）", 0

    # 白膜组件名固定是 `cube`（`add_cube` 时传的 name）—— 按引用末段找，不靠顺序猜
    used += 1
    comps = await call_official(
        ctx, "get_components", {"actor": {"refPath": actor}}, toolset=TS_ACTOR)
    cube = ""
    for item in (comps or []):
        ref = _ref_path(item)
        if ref and ref.rsplit(".", 1)[-1] == "cube":
            cube = ref
            break
    if not cube:
        names = [_ref_path(i) for i in (comps or [])]
        return "failed", f"这个 Actor 上找不到名为 `cube` 的组件（现有组件：{names}）", used
    if dry:
        return "dry", "", used

    wanted = {"instance": {"refPath": cube}, "properties": ["overrideMaterials"]}
    used += 1
    cur = await call_official(ctx, "get_properties", dict(wanted), toolset=TS_OBJECT)
    if _surface_matches(cur, material):
        return "already", "", used

    used += 1
    await call_official(ctx, "set_properties", {
        "instance": {"refPath": cube},
        "values": json.dumps(
            {"overrideMaterials": [{"refPath": to_object_path(material)}]},
            ensure_ascii=False),
    }, toolset=TS_OBJECT)

    # 读回核对：写进去了但读回不是它 = **没成**（与阶段三"读回对账"同一条纪律）
    used += 1
    back = await call_official(ctx, "get_properties", dict(wanted), toolset=TS_OBJECT)
    if _surface_matches(back, material):
        return "applied", "", used
    return "failed", f"写了但读回不是这块材质（读回 {back!r}）", used


@mcp.tool()
async def apply_surfaces(
    ctx: Context[AppContext],
    dry_run: Annotated[bool, Field(
        description=("true = 只算「哪几行要贴哪块材质」+ 逐个验材质路径、逐个确认 Actor 还在，"
                     "**一个组件都不写**（先看一遍再动手用这个）"))] = False,
    only: Annotated[list[str] | None, Field(
        description=("只处理这几个 `element_key`（如 `[\"road\"]`）；留空 = 全部有材质映射的白膜行"))] = None,
) -> SurfaceReport:
    """**阶段五 · 第 1 步**：给白膜**贴表面材质**（不贴的话落下去的就是灰白模）。

    做四件事：
      ① 读**搭建台账** `views/build_state_v1.json` —— 靠里面记的 **Actor 引用**贴，
         **不重新去关卡里找**（那得重跑一遍识别，是另一码事）；
      ② 核对**当前关卡与台账一致**（换图了就拒收，一个组件都不写）；
      ③ **逐个验材质路径** `exists()` —— 不过就拒收（与阶段三"一条不过就一个都不落"同一条纪律）；
      ④ 逐行：找 `cube` 组件 → 读现值（**幂等**：已是这块材质就跳过）→ `set_properties`
         写 `overrideMaterials` → **读回核对**（写进去但读回不是它 = 没成，如实报）。

    ⚠ 贴的是**组件级覆盖**，**不改资产**：改资产会把 `/Engine/BasicShapes/Cube` 这种
      引擎自带网格一起改掉（所有实例跟着变）—— 这是实测确认过的。
    ⚠ 只贴**白膜行**；资产行（house / tree）**自带材质**，本工具不碰。
    ⚠ **绝不存盘** —— 贴完你在 UE 里看，存不存由你定。
    ⚠ 材质表在 **`config/surface_materials.json`**（**不写死在代码里**）—— 改它**不用改代码、
      不用重启**，因为本工具**每次调用都重新读**。代码里的 `SURFACE_MATERIAL_DEFAULT`
      **故意是空的**（材质路径属于你工程，不该写死在代码里）：配置缺了 / 坏了就退回它，
      并把原因写进 `warnings` —— **那时就是一个元素都不贴**，会明说，不假装"贴了"。
    """
    calls = 0
    warns: list[str] = []

    # 材质表**每次调用都重新读**（配置文件改完立刻生效，不用重启）—— 见 `_surface_materials()`。
    _mats, _mats_note = _surface_materials()
    if _mats_note:
        warns.append(_mats_note)

    ledger = _load_build_state()
    ledger_rows = [x for x in (ledger.get("rows") or []) if isinstance(x, dict)]
    if not ledger_rows:
        raise ToolError(
            f"还没有搭建台账（`{BUILD_STATE_PATH.name}`）—— 贴材质靠台账里的**Actor 引用**，"
            "没台账就不知道贴谁。先把关卡搭起来（`execute_build()`）；"
            "场景已经搭好了就先 `execute_build(mode=\"incremental\", adopt=true)` 认领现状。")

    # ① 当前关卡必须与台账一致（换图了，台账里的 Actor 引用在这儿没有意义）
    level = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
    calls += 1
    if str(ledger.get("level") or "") not in ("", level):
        raise ToolError(
            f"台账记的关卡是 `{ledger.get('level')}`，当前关卡是 `{level}` —— **换图了**，"
            "台账里的 Actor 引用在这儿没有意义 —— **一个组件都没写**。要么切回那张图，"
            "要么在**这张图上**重新搭（`execute_build`）。")

    # ② 挑出要贴的行（只白膜；资产行自带材质，不进这张表）
    wanted_keys = set(_mats)
    if only:
        picked = {str(k).strip() for k in only if str(k).strip()}
        wanted_keys &= picked
        if not wanted_keys:
            raise ToolError(
                f"`only` 里的元素**都没有材质映射** —— 有映射的是：{sorted(_mats)}。"
                f"（收到：{only}）")
    targets = [r for r in ledger_rows
               if str(r.get("kind") or "") == "whitebox"
               and str(r.get("element_key") or "") in wanted_keys]
    asset_rows = sum(1 for r in ledger_rows if str(r.get("kind") or "") == "asset")
    if not targets:
        warns.append(f"台账里没有**有材质映射的白膜行**（要贴的元素：{sorted(wanted_keys)}）—— "
                     "没什么可贴的。")

    # ③ 材质路径逐个 exists()：不过就拒收（**一个组件都不写**）
    materials = sorted({_mats[str(r["element_key"])] for r in targets})
    dead: list[str] = []
    for p in materials:
        calls += 1
        try:
            ok = await call_official(ctx, "exists", {"path": p}, toolset=TS_ASSET)
        except ToolError as exc:
            dead.append(f"{p}（调用失败：{exc}）")
            continue
        if not ok:
            dead.append(p)
    if dead:
        raise ToolError(
            "拒收：材质路径验不过（官方 exists() 返回否）：" + "、".join(dead)
            + " —— **一个组件都没写**。先确认这几块材质还在不在"
              "（映射见 `config/surface_materials.json`）。")

    # ④ 逐行贴（dry_run 仍会逐个找组件确认 Actor 还活着，但不写）
    rows_out: list[SurfaceRowResult] = []
    applied = already = failed = 0
    for r in targets:
        material = _mats[str(r["element_key"])]
        try:
            status, err, used = await _surface_apply_row(ctx, r, material, dry=dry_run)
            calls += used
        except ToolError as exc:
            status, err = "failed", str(exc)
        if status == "applied":
            applied += 1
        elif status == "already":
            already += 1
        elif status == "failed":
            failed += 1
        rows_out.append(SurfaceRowResult(
            label=str(r.get("label") or r.get("uid") or ""),
            element_key=str(r.get("element_key") or ""),
            material=material, actor=str(r.get("actor") or ""),
            status=status, error=err))

    if failed:
        warns.append(f"⚠ 有 {failed} 行**没贴上** —— 逐条原因在 `rows` 里（不掩饰）。")
    if dry_run:
        warns.append("演练：**一个组件都没写**。要真贴就去掉 `dry_run` 重调。")
    else:
        warns.append("**全程未存盘** —— 贴完的样子在 UE 里看，存不存由你定。")
    warns.append("⚠ 本工具只改**组件材质覆盖**，不动几何 / 位置 / 分组 —— "
                 "阶段三那套台账对账不受影响。")

    return SurfaceReport(
        stage="阶段五 · 表面材质（给白膜贴材质）",
        level=level, dry_run=dry_run, planned=len(targets),
        applied=applied, already=already, failed=failed,
        asset_rows_untouched=asset_rows, rows=rows_out, official_calls=calls,
        next_step=(
            (f"演练：这次会给 {len(targets)} 行贴材质（元素：{'、'.join(sorted(wanted_keys))}）。"
             "要真做就去掉 `dry_run` 重调。"
             if dry_run else
             f"贴完：{applied} 行新贴、{already} 行本来就对、{failed} 行没成。"
             "在 UE 视口里看一眼（还嫌灰就是没贴上 —— 看 `rows` 里的 error）。"
             "⚠ 存不存盘由你在 UE 里决定。下一步是**阶段六 · 环境搭建**（`setup_environment` —— "
             "按时段配太阳 / 天空 / 大气 / 天光 / 雾 / 云 / 后处理）。")
        ),
        warnings=warns,
    )


# --- [完工-06] 工具 2：确认元素清单 ---  【模块：assets】
# 实测 2026-09-23T10:26:36Z：catalog/elements.json（14 个元素）由它落盘。

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

    ⚠ 调用前必须先让用户确认过这张表。**没确认就别调**。
    ⚠ **阶段一的流程（2026-09-24 整改）**：拿用户上传的图 → 提取画面信息 → **填进表里对应的大类**
      → 有数量的填**预估数量** → 表里那两个**场景项**（地图大小 / 地图对应时间）也要填
      → 交用户确认 → **然后才**去找资产。
      ① 表 = `config/asset_categories.json`：11 个资产大类（Fab 的 3D 分类树，用户截图原文）
         + 2 个**场景项**（`kind=scene`）；
      ② 每个元素填一个 `category` key —— 这一步就是"根据参考图把表填好"；
         表里没有的 key 会被**拒收**（拒收信息里列出可用的 key），不许自己造大类；
         没把握就填 `uncategorized` 并在 note 里写明原因；
      ③ **有数量的要填 `count`**，并用 `count_source` 写明是估的还是数的；
         材质 / 面这类确实没有『个数』的可以留空，但要在 note 里写明为什么；
      ④ **两个场景项必须填**：地图大小 → `size_m: [X, Y]`（米，预估）；
         地图对应时间 → `time_of_day`（如 sunset 黄昏 / blue_hour 蓝调）—— 没填会被拒收；
      ⑤ ⚠ 场景项**不是资产**：`plan_assets` 不会去搜它们，`confirm_assets` 也不许写进资产清单。
      ⚠ 大类**不是官方的搜索参数**（find_assets 只按资产名/文件夹名/类路径过滤）——
        它的用处是分组确认、逐类核对有没有漏，**不许**假装它能过滤搜索结果。
    ⚠ 元素粒度由**资产库**定，不由画面部件数定：屋顶 / 石材基座 / 路缘石这类通常
      **并进主体资产**，拆出来只会给 plan_assets 喂搜不到的词、制造假缺口。
      同一 element 可以**多行**（同关键词在多处出现）；覆盖判定按关键词集合算，不按条数算。
    ⚠ 图上**看不清**的：留在清单 + 写「⚠ 待核实」+ 给备用关键词；**不许凭空添一条，也不许删**。
      清单是给用户**逐条确认**的 —— 塞进猜测会污染他的确认，删掉该留的会丢信息。
    """
    table = load_categories()
    warnings: list[str] = []

    # 表里没有的 key 当场拒收 —— 不然"先有表再填表"就是句空话
    known = set(table["by_key"])
    bad = sorted({
        str(e.category).strip() for e in elements
        if str(e.category).strip() and str(e.category).strip() not in known
    })
    if bad:
        raise ToolError(
            f"大类 key 不在表里：{bad}。可用的 key：{sorted(known)}"
            f"（表在 {CATEGORY_PATH}）。实在没把握就填 {table['fallback_key']}，"
            "并在 note 里写明为什么 —— **不许自己造一个大类**。"
        )

    # 空大类 → 未分类：照实登记 + 报警，不替它编一个
    filled = [
        e.model_copy(update={"category": str(e.category).strip() or table["fallback_key"]})
        for e in elements
    ]
    n_uncat = sum(1 for e in filled if e.category == table["fallback_key"])
    if n_uncat:
        warnings.append(
            f"有 {n_uncat} 个元素**没填大类**，已照实记成「{table['fallback_cn']}」—— "
            "这份表没填全，请用户确认时重点看这几个。"
        )
    if table["defined"] == 0:
        warnings.append(
            f"读不到大类表（{CATEGORY_PATH}）—— 全部只能记成「{table['fallback_cn']}」。"
            "先确认那个文件在不在，别就这么往下走。"
        )

    # 场景项校验（表里 kind=scene 的行）：必须在表里、且值必须填。
    # 值填在哪个字段由表决定（value_field），代码不写死 key —— 以后往表里加第三、
    # 第四个场景项，只要在表里写清 value_field 就行。
    _present_scene = {e.category for e in filled if is_scene_category(e.category, table)}
    _absent_scene = [table["by_key"][k] for k in table["scene_keys"]
                     if k not in _present_scene]
    if _absent_scene:
        raise ToolError(
            "表里**少了场景项**：" + "、".join(_absent_scene) + "。"
            "它们和资产大类一样是这张表的一部分（2026-09-24 用户要求新增）—— "
            "地图大小填 `size_m`（米，预估）、地图对应时间填 `time_of_day`（如 sunset / blue_hour）；"
            "少了它们就不是一张填好的表。"
        )
    _missing_scene: list[str] = []
    for e in filled:
        if not is_scene_category(e.category, table):
            continue
        _vf = table["value_field"].get(e.category, "")
        _cn = table["by_key"].get(e.category, e.category)
        if _vf == "size_m":
            if (not e.size_m or len(e.size_m) < 2
                    or min(float(e.size_m[0]), float(e.size_m[1])) <= 0):
                _missing_scene.append(f"{_cn}（要填 size_m: [X, Y]，单位米）")
        elif _vf == "time_of_day":
            if not str(e.time_of_day).strip():
                _missing_scene.append(f"{_cn}（要填 time_of_day，如 sunset / blue_hour）")
    if _missing_scene:
        raise ToolError(
            "场景项没填值：" + "；".join(_missing_scene) + "。"
            "这两项是 2026-09-24 用户要求加进表里的（地图大小 / 地图对应时间）—— "
            "它们**不是资产**，但**必须填**：后面的世界范围和环境搭建（时段/太阳）都靠它们，"
            "不填就没有依据。"
        )

    scene, scene_size, scene_time = scene_from_rows(
        [e.model_dump() for e in filled], table
    )

    # 缺数量的（2026-09-24 用户要求：有数量的就要填预估数量）
    no_count = [
        e.element for e in filled
        if not is_scene_category(e.category, table) and not (e.count and e.count >= 1)
    ]
    if no_count:
        warnings.append(
            f"有 {len(no_count)} 个元素**没填预估数量**：{'、'.join(no_count)}。"
            "用户 2026-09-24 要求『有数量的还要填预估数量』—— "
            "材质 / 面这类确实没有个数的，请在该行 note 里写明；其余的请补 count。"
        )

    result = ElementList(
        confirmed_at=datetime.now(timezone.utc).isoformat(),
        source_image=source_image,
        total=len(filled),
        elements=filled,
        groups=group_elements_by_category([e.model_dump() for e in filled], table),
        categories_defined=table["defined"],
        scene=scene,
        scene_map_size_m=scene_size,
        scene_time_of_day=scene_time,
        no_count=no_count,
        next_step=(
            "元素清单已记录（按大类填好了，含地图大小 / 地图对应时间两个场景项）。"
            "下一步调 plan_assets()（不传参数即可）去找资产 —— "
            "⚠ **场景项不参与找资产**（它们不是资产），会在报告的 scene 里原样带出。"
            "找到就是找到，没找到就直接告诉用户缺什么，"
            "并请用户给出【路径 + 名字】—— 禁止猜疑似的。"
        ),
        warnings=warnings,
    )
    save_json(ELEMENTS_PATH, result.model_dump())
    return result


# --- [完工-07] 工具 3：找资产（阶段一主工具）---  【模块：assets】
# 实测 2026-09-24（**check_tools 60 通过 / 0 失败**，用户喂回的原始输出）：
#   本轮白膜整改新增的两条断言都绿 ——「找不到的元素带出 placeholder_hint（要求登记白膜占位）」
#   与「找不到元素时不再说『跳过这个元素』」。
# 2026-09-24 改过本块逻辑（阶段一白膜整改）：
#   ① `ElementPlan` 新增 `placeholder_hint`（缺失 / 只有材质实例顶着的元素，
#      要带上"必须登记成白膜占位"的提示 —— confirm_assets 会拒收没登记的白膜）；
#   ② 新增 warning：点出哪几个元素只命中非网格资产；
#   ③ 提问文案不再说「跳过这个元素」（用户口径是登记成白膜，不是跳过）。
# 实测 2026-09-23 12:07（不传参读元素清单）、12:12（keywords=["grass"]）两次调用。

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

    没找到时：直接告知缺什么，并请用户把"存在但没被找到"的资产的【路径 + 名字】给你；
    用户确认「确实没有」的，**登记成白膜占位**（不是"跳过" —— 跳过会让这个元素静默消失）。

    ⚠ **每个元素都带 `placeholder_hint`**（2026-09-24 用户要求）：非空 = 这个元素**没有可用资产**
      （没找到，或命中的全是材质实例这种摆不出实体的）。这类**必须先在 confirm_assets 里
      登记成白膜占位**（`is_placeholder=true` + `placeholder_shape` + **预估** `size_cm`）
      再让用户确认清单 —— **不许当资产行交出去**（路径验不过会被当场拒收）。
      命中的只有材质、但用户说"就用它当表面"的，那是用户拍板，占位提示以用户答复为准。

    ⚠ 本工具**不改动任何东西**，`needs_user_confirmation` 恒为 true。
    """
    calls = 0
    warnings: list[str] = []

    stored = load_json(ELEMENTS_PATH)
    _scene_rows: list[dict] = []          # 场景项（地图大小 / 地图对应时间）—— 不是资产
    if keywords:
        elements_kw = [k for k in keywords if k and k.strip()]
        source = "本次传入的关键词"
    elif stored:
        _tbl = load_categories()
        elements_kw = []
        for _e in (stored.get("elements") or []):
            if not (isinstance(_e, dict) and _e.get("element")):
                continue
            # 场景项不参与找资产：去搜「地图大小」这种词没有意义
            if is_scene_category(str(_e.get("category") or ""), _tbl):
                _scene_rows.append(_e)
            else:
                elements_kw.append(_e["element"])
        source = f"用户确认过的元素清单（{stored.get('confirmed_at', '?')}）"
    else:
        raise ToolError(
            "没有元素清单：既没传 keywords，也没有 confirm_elements 记录过。"
            "请先读参考图列出元素、让用户确认，然后调 confirm_elements。"
        )
    if not elements_kw and not _scene_rows:
        raise ToolError("元素清单是空的，没什么可查。")

    # 每类资产枚举一次，之后都在本地匹配（枚举逻辑收敛在 library_inventory() 一处）
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

        found.sort(key=lambda a: (a.matched_by != "资产名", len(a.path)))

        # "只有材质实例顶着" = 这个元素摆不出实体（材质没有包围盒、量不出尺寸）。
        # 不能自作主张替用户判定：材质是真的不够用、还是本来就当表面用，得用户拍板 ——
        # 这里只把事实摆出来 + 给出白膜该怎么办。
        mesh_hits = [f for f in found if f.asset_type in SIZE_QUERY_TYPES]
        only_material = bool(found) and not mesh_hits
        hint = ""
        if not found:
            hint = (
                "找不到资产 → **登记成白膜占位**（先给预估尺寸，再让用户确认清单）："
                "`is_placeholder=true`、`asset_path` 留空、"
                "`placeholder_shape` 按它是什么填 cube（体）/ plane（面）、"
                f"`size_cm` 给 [X, Y, (Z)]、`size_source` 写 {SIZE_SOURCE_ESTIMATED}。"
            )
        elif only_material:
            types = "、".join(sorted({f.asset_type for f in found}))
            hint = (
                f"{MATERIAL_ONLY_MARK}（{types}）—— **材质没有包围盒、摆不出实体**，"
                "等于没资产。2026-09-24 用户要求：这类元素**先给预估尺寸、登记成白膜占位**"
                f"（`is_placeholder=true`、`placeholder_shape` 填 cube/plane、"
                f"`size_cm` 给预估、`size_source` 写 {SIZE_SOURCE_ESTIMATED}），"
                "**再让用户确认清单**。"
                "⚠ 但**要不要把它当资产用，由用户拍板** ——"
                "若用户确认「就用这个材质当表面」，那才是普通资产行；"
                "本工具不替你决定，也不许拿它顶替实体。"
            )

        if found:
            found_count += 1
            names = "、".join(f.name for f in found[:max_per_element])
            question = (
                f"「{kw}」找到 {len(found)} 个候选：{names}。"
                f"请确认用哪一个（或明确让我挑最匹配的）。"
            )
            if only_material:
                question += (
                    " ⚠ 这几个都是**非网格资产**（没有包围盒、摆不出实体）："
                    "请用户明确 —— 是就用它当**表面**，还是**登记成白膜占位**？"
                )
        else:
            # 不猜、不找疑似的 —— 直接说缺，请用户给路径
            question = (
                f"「{kw}」按名字没找到（资产名和文件夹名都试过了）。"
                f"**如果它其实存在、只是名字对不上，请把它的【路径 + 名字】告诉我，我来改名。**"
                f"如果确实没有，请告诉我：**登记成白膜占位**（先给预估尺寸），"
                f"而不是把它当资产行交出去。"
            )

        elements.append(
            ElementPlan(
                element=kw, status="found" if found else "missing",
                assets=found[:max_per_element],
                placeholder_hint=hint,
                question=question,
            )
        )

    missing = len(elements) - found_count

    # 白膜提醒（2026-09-24 用户要求：没有可用资产的元素，先给预估尺寸、登记白膜）
    # _none_found：按名字一个都没找到；_only_mat：只命中材质/材质实例（算 found，
    # 但摆不出实体）。两类原因不同、对策一样：登记成白膜占位，而不是"跳过"。
    _none_found = [p.element for p in elements if not p.assets and p.placeholder_hint]
    _only_mat = [p.element for p in elements if MATERIAL_ONLY_MARK in p.placeholder_hint]

    if _none_found or _only_mat:
        _parts: list[str] = []
        if _none_found:
            _parts.append(
                f"{len(_none_found)} 个**按名字一个都没找到**（{'、'.join(_none_found)}）"
            )
        if _only_mat:
            _parts.append(
                f"{len(_only_mat)} 个**只命中非网格资产**（材质/材质实例 —— "
                f"没有包围盒、摆不出实体：{'、'.join(_only_mat)}）"
            )
        warnings.append(
            "有 " + "；".join(_parts) + "。**禁止猜疑似的** —— 把事实如实告诉用户："
            "存在但名字对不上的，请用户给【路径 + 名字】；"
            "用户确认「确实没有」的，**登记成白膜占位**（`is_placeholder=true` + "
            "`placeholder_shape` + **预估** `size_cm`）再让用户确认清单 —— "
            "**不许当资产行交出去**（路径验不过会被 confirm_assets 当场拒收），"
            "也**不许「跳过」了事**（那会让这个元素在清单里静默消失）。"
            "详见各元素的 `placeholder_hint`。"
        )

    # 按大类分组（2026-09-24 用户要求：报告也按类给）
    # 元素 → 大类 来自 elements.json；本次显式传 keywords 时，清单里没记过的词落「未分类」。
    _cat_by_element = {
        str(e.get("element") or ""): str(e.get("category") or "")
        for e in ((stored or {}).get("elements") or []) if isinstance(e, dict)
    }
    _cat_table = load_categories()
    by_cat: dict[str, dict[str, list[str]]] = {}
    for p in elements:
        _cn = category_cn(_cat_by_element.get(p.element, ""), _cat_table)
        _bucket = by_cat.setdefault(_cn, {"found": [], "missing": []})
        _bucket["found" if p.status == "found" else "missing"].append(p.element)
    by_cat = {cn: by_cat[cn] for cn in order_category_names(set(by_cat), _cat_table)}

    scene_out, _scene_size, _scene_time = scene_from_rows(_scene_rows, _cat_table)
    if _scene_rows:
        warnings.append(
            f"清单里有 {len(_scene_rows)} 个**场景项**（" + "、".join(scene_out.keys())
            + "）—— 它们**不是资产**，本次没有去搜；值见返回里的 `scene`，"
            "后面的世界范围 / 环境搭建（时段/太阳）要用它们。"
        )

    return PlanReport(
        stage="阶段一 · 资产确认",
        needs_user_confirmation=True,
        source=source,
        total_elements=len(elements),
        found=found_count,
        missing=missing,
        elements=elements,
        by_category=by_cat,
        scene=scene_out,
        official_calls=calls,
        next_step=(
            "把每个元素的 question **原样逐条问用户**："
            "找到的问用哪个（**连预估数量一起报上去**，用户要按数量判断够不够）；"
            "**缺的请用户给出【路径 + 名字】**。"
            "⚠ 每个元素还带 `placeholder_hint`：**非空 = 它没有可用资产**"
            "（没找到，或命中的全是材质实例这种摆不出实体的），"
            "这类**必须登记成白膜占位**（`is_placeholder=true` + `placeholder_shape` + "
            "**预估** `size_cm`）再让用户确认清单 —— **不许当资产行交出去**"
            "（confirm_assets 会当场拒收一条验证不过的路径）。"
            "⚠ 用 `by_category` **逐类核对有没有漏**（哪个大类还缺哪些元素）——"
            "但大类**不是搜索参数**，缺了仍然是按名字找 + 请用户指路。"
            "⚠ `scene` 里那两个场景项**不是资产**，别去问用户『用哪个资产』；"
            "它们是地图大小 / 地图对应时间，后面摆场景时才用。"
            "拿到用户答复后：路径 → rename_assets 改名；"
            "用户说「确实没有」 → 按白膜登记；"
            "全部齐了 → confirm_assets 使用清单。"
        ),
        warnings=warnings,
    )


# --- [完工-10] 工具 4：改名（用户指路之后）----------------------------------------------  【模块：assets】
# 实测 2026-09-23 晚：check_tools 4 条全绿 —— 默认 dry_run / 一个都没真改 /
# 拦下带斜杠的非法名（本地就挡住，不发去 UE）/ 给出了改名前后路径。
# 它只测预览路径（confirm=False），绝不真改用户资产。

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


# --- [完工-08] 工具 5：确认并使用资产清单（阶段一收尾）---  【模块：assets】
# 实测 2026-09-24（**check_tools 60 通过 / 0 失败**，用户喂回的原始输出）：
#   ① 纯逻辑那 11 条白膜校验断言全绿（带路径 / 没形状 / 形状写错 / 没尺寸 / cube 少一个数 /
#      来源写成实测 → 都拒收；plane 两个数、旧文案、空 size_source → 都算合格）；
#   ② 经 MCP 端到端两条拒收断言绿：「拒收路径验证不过的行（不再当 invalid_paths 使用）」
#      与「拒收没写 placeholder_shape 的白膜行」；
#      走通的那一批绿：1 个真资产 / 2 个白膜占位、`invalid_paths` 恒空、清单里点明了
#      "摆不出实体"、`note` 里点名了只拿材质顶着的元素、白膜行标成 ◻ 白膜且不带路径。
# 2026-09-24（二）再次整改（阶段一白膜契约收紧，逻辑改动不是文案）：
#   ① `placeholder_errors()` 四道校验（带路径 / 没形状 / 没尺寸 / 来源不是预估 → 拒收）；
#   ② 路径验证不通过不再记进 invalid_paths 使用，改为整表拒收 + 给出改法；
#   ③ 新增"只拿材质/材质实例顶着"的点名（只点名，不替用户改）；
#   ④ 拒收发生在写盘之前 —— catalog/ 不会被写到一半。
# 实测 2026-09-23 12:12（6/6 exists + 指纹签字）、12:24（4 个网格实测尺寸 + 重签）。
# 实测 2026-09-23 晚：复跑 check_tools —— 原先失败的 3 条全绿，
#   且末尾 [还原] 行列出了 asset_list.json / elements.json / library_snapshot.json，
#   证明函数体真跑到了写盘那一步。
# 2026-09-23 晚（二）：新增 unresolved_elements（未落位元素），尚无实测证据。

# --- confirm_assets 的分段私有函数（2026-09-27 结构优化第 2 步）------------------  【模块：assets】
# 为什么拆：这个工具原本是**一个 379 行的函数**（314 行代码），里面塞了四件事 ——
#   逐行体检（验路径 / 实测尺寸）、未落位元素的对账、使用清单正文的排版、给表签字。
#   **行为一个字没改**：只是各挪成有名字的函数，工具主体只剩"流程 + 拒收闸"。
# ⚠ 本组函数**只被 confirm_assets 用**。它们**不改 UE**（只调官方 exists() / get_bounds 读资产），
#   真正的写入只有两处我们自己的文件：catalog/asset_list.json 与 catalog/library_snapshot.json。


@dataclass
class _AssetScan:
    """`confirm_assets` 逐行体检的计数器。

    为什么要一个对象：拆分之后这些计数要跨好几个函数用，散着传会变成五六个参数；
    打包成一个对象，调用方读起来也清楚"这是这次体检的账"。
    """

    measured: int = 0        # 量到包围盒的网格数
    no_size: int = 0         # 本来就没有包围盒的（材质 / 材质实例等）
    size_failed: int = 0     # 想量却没量到的
    placeholders: int = 0    # 白膜占位几行（没有可用资产的元素）
    bad_rows: list[str] = field(default_factory=list)
    """不合格的行（白膜填错 / 路径验不过）—— **收齐再判**，见主体里那段说明。"""


def _asset_category_map(stored: dict) -> dict[str, str]:
    """从 `elements.json` 那张表里取『元素关键词 → 大类』。

    用途（2026-09-24 用户要求：资产清单也要分大类）：调用方**没填**大类时的兜底来源。
    中文名一律 `category_cn()` 查表回填 —— 表是唯一来源，调用方不用填中文名。
    """
    by_key: dict[str, str] = {}
    for _e in (stored.get("elements") or []):
        if isinstance(_e, dict):
            _k = str(_e.get("element") or "").strip().lower()
            if _k and str(_e.get("category") or "").strip():
                by_key.setdefault(_k, str(_e["category"]).strip())
    return by_key


def _item_category(item: AssetListItem, cat_by_key: dict[str, str]) -> str:
    """这一行归哪个大类：调用方填的 → elements.json 里记的 → 空（记成未分类）。

    查两次是因为 `element_key` 可能留空（那就退回标签首词）—— 两者都查不到才算没分类。
    """
    given = str(item.category).strip()
    if given:
        return given
    for probe in (str(item.element_key or "").strip().lower(),
                  str(item.element or "").strip().split(" ")[0].lower()):
        if probe and probe in cat_by_key:
            return cat_by_key[probe]
    return ""


def _placeholder_row(
    item: AssetListItem, cat_of: str, cat_table: dict
) -> tuple[AssetListItem, list[str]]:
    """**白膜占位行**：没有资产可验、没有东西可量 —— 只校验"填得合不合规矩"。

    2026-09-24 用户要求：找不到资产的、用户确认没有的、只有材质实例顶着的元素，
    都要先给预估尺寸、登记成白膜，再让用户确认清单。**只是清单登记，不往关卡里摆东西。**

    返回 `(回填后的行, 不合格原因)` —— 不合格的由调用方收齐后**一次性**拒收。
    """
    bad = placeholder_errors(item)
    vol = None
    if item.size_cm and len(item.size_cm) >= 3:
        vol = num(float(item.size_cm[0]) * float(item.size_cm[1]) * float(item.size_cm[2]))
    row = item.model_copy(update={
        "exists": False,          # 没有路径可验 —— 不许写成"存在"
        "bbox_volume_cm3": vol,
        "asset_type": item.asset_type or "whitebox",
        "size_source": item.size_source or SIZE_SOURCE_ESTIMATED,
        "category": cat_of,
        "category_cn": category_cn(cat_of, cat_table),
    })
    return row, bad


async def _asset_row(
    ctx: Context[AppContext], item: AssetListItem, cat_of: str, cat_table: dict
) -> tuple[AssetListItem, str, str]:
    """**资产行**：验路径（官方 `exists()`）+ 实测尺寸（官方 `get_bounds`）→ 回填。

    返回 `(回填后的行, 尺寸去向, 不合格原因)`；尺寸去向 ∈ `measured` / `no_box` / `failed`
    —— 交给调用方记账，本函数不自己数数。
    ⚠ 路径验不过的行**也**会落到尺寸那一档（`路径不存在，没量` → `failed`）——
      与拆分前一致，别"顺手"改成不计。
    """
    ok = False
    err = ""
    try:
        exists = await call_official(
            ctx, "exists", {"path": item.asset_path}, toolset=TS_ASSET
        )
        ok = exists is True or (isinstance(exists, str) and exists.lower() == "true")
    except ToolError as exc:
        err = str(exc)

    bad = ""
    if not ok:
        # 2026-09-24（二）：不再记进 invalid_paths 一路使用出去。
        # 路径验不过的元素 = 没有可用资产 = 该登记成白膜；
        # 先收着，等整表走完一起拒收（带上"怎么改"的原文）。
        bad = (
            f"{item.element!r}：路径验证不通过"
            + (f"（{err}）" if err else "（官方 exists() 返回否）")
            + f" —— `asset_path` = {item.asset_path!r}。"
            "请二选一：① 用户给出正确路径 → 改成那一行重交；"
            "② 用户确认「确实没有」→ 改成**白膜占位行**"
            f"（`is_placeholder=true`、`asset_path` 留空、`placeholder_shape` 填 "
            f"{' / '.join(PLACEHOLDER_SHAPES)}、`size_cm` 给**预估**尺寸、"
            f"`size_source` 写 {SIZE_SOURCE_ESTIMATED}）。"
            "**不许当资产行交出去** —— 否则下一阶段会挂上一个解析不到的材质/网格。"
        )

    size: list[float] | None = None
    volume: float | None = None
    size_src = "路径不存在，没量"
    if ok:
        size, volume, size_src = await measure_asset(
            ctx, item.asset_path, item.asset_type
        )
    if size:
        kind = "measured"
    elif "没有包围盒" in size_src:
        kind = "no_box"
    else:
        kind = "failed"

    row = item.model_copy(update={
        "exists": ok,
        "size_cm": size,
        "bbox_volume_cm3": volume,
        "size_source": size_src,
        "category": cat_of,
        "category_cn": category_cn(cat_of, cat_table),
    })
    return row, kind, bad


def _unresolved_elements(stored: dict, delivered_keys: set[str]) -> list[UnresolvedElement]:
    """**未落位元素**：元素清单里有、使用表里没有的 —— 不许静默消失。

    note 原样带出元素清单里那一句，**不推断原因**
    （例：sky 写着"按【光照环境】处理…不按资产找（用户已拍板）"）。
    """
    notes_by_key: dict[str, list[str]] = {}
    for _e in (stored.get("elements") or []):
        if not isinstance(_e, dict):
            continue
        _k = str(_e.get("element") or "").strip().lower()
        if _k:
            notes_by_key.setdefault(_k, []).append(str(_e.get("note") or ""))
    return [
        UnresolvedElement(
            element_key=_k,
            occurrences=len(_ns),
            note=" / ".join(n for n in _ns if n),
        )
        for _k, _ns in sorted(notes_by_key.items())
        if _k not in delivered_keys
    ]


def _render_asset_list(
    rows: list[AssetListItem],
    stored: dict,
    cat_table: dict,
    scan: _AssetScan,
    mat_only: list[str],
    unresolved: list[UnresolvedElement],
) -> list[str]:
    """把使用清单**正文**拼出来（带尺寸 / 体积两列、按大类分组、末尾附口径提示）。

    这一段以前混在工具主体里，把"写盘与签字"挤到了看不见的地方 —— 拆出来之后，
    主体上一眼就能看到"先拒收、再排版、最后落盘签字"的次序。
    """
    lines = ["资产清单（阶段一使用）", ""]
    lines.append(f"参考图：{stored.get('source_image') or '(未记录)'}")
    lines.append(
        f"元素数：{len(rows)}    路径验证通过：{sum(1 for r in rows if r.exists)}"
        + (f"    白膜占位：{scan.placeholders}（尺寸为**预估**，见该行 size_source）"
           if scan.placeholders else "")
    )
    lines.append(
        f"尺寸实测：{scan.measured} 个网格量到包围盒，{scan.no_size} 个非网格本来就没有，"
        f"{scan.size_failed} 个没量到（原因见该行下方）"
    )
    lines.append("")
    _mat_set = set(mat_only)          # 只拿材质/材质实例顶着的行（下面打印时点名）

    def _emit_rows(batch: list[AssetListItem]) -> None:
        lines.append(f"{'元素':<16} {'状态':<6} {'尺寸 X×Y×Z (cm)':<28} {'包围盒体积 (cm³)':>18}  资产路径")
        lines.append("-" * 122)
        for r in batch:
            size_txt = "×".join(f"{v:.1f}" for v in r.size_cm) if r.size_cm else "—"
            vol_txt = f"{r.bbox_volume_cm3:,.0f}" if r.bbox_volume_cm3 is not None else "—"
            status = ("◻ 白膜" if r.is_placeholder else ("✓ 存在" if r.exists else "✗ 不存在"))
            where = r.asset_path or f"(白膜占位 {r.placeholder_shape or 'cube'}，无资产)"
            if r.element in _mat_set:
                where += "（⚠ 只有材质实例，摆不出实体 —— 见下方点名）"
            lines.append(
                f"{r.element:<16} {status:<6} {size_txt:<28} {vol_txt:>18}  {where}"
            )
            if r.note:
                lines.append(f"{'':<16} {'':<6} └ {r.note}")
            if r.size_cm is None:
                lines.append(f"{'':<16} {'':<6} └ 无尺寸原因：{r.size_source or '(未记录)'}")

    # 按大类分组打印（2026-09-24 用户要求：资产清单要分大类，能逐类看）
    _grouped: dict[str, list[AssetListItem]] = {}
    for r in rows:
        _grouped.setdefault(r.category_cn or "未分类", []).append(r)
    for _cn in order_category_names(set(_grouped), cat_table):
        lines.append("")
        lines.append(f"## {_cn}（{len(_grouped[_cn])} 项）")
        _emit_rows(_grouped[_cn])
    if _grouped.get("未分类"):
        lines.append("")
        lines.append(
            "⚠ 上面「未分类」那些：`elements.json` 里也没给它们分类 —— "
            "先用 confirm_elements 把大类表填全，再使用更清楚。"
        )
    _scene_recap, _, _ = scene_from_rows(stored.get("elements", []) or [], cat_table)
    if _scene_recap:
        lines.append("")
        lines.append(
            "场景项（**不是资产**，已记在 catalog/elements.json）："
            + "；".join(f"{k}：{v}" for k, v in _scene_recap.items())
        )
    lines.append("")
    lines.append(
        "⚠ 口径：尺寸/体积都是**官方 get_bounds 实测的包围盒**（体积 = X×Y×Z），"
        "**不是网格实际体积**；材质/材质实例没有包围盒，记 —。"
        "⚠ 标 `◻ 白膜` 的行**例外**：那是没有可用资产、用**预估尺寸**登记的白膜占位，"
        "尺寸不是实测 —— 请重点确认；`plane`（面）类的覆盖面阶段二会用参数几何重算。"
    )
    # ⚠ **「尺寸这一列怎么读」**（2026-09-26 加；**本次改动尚未实测**）：客户那头的 agent 看到
    #   「house1 实测包围盒只有 89×70×50 cm」，就去问用户「要不要换个房子模型 / 是不是被缩放过」——
    #   而答案本来就在工具链里：**阶段二的 `scale` 就是干这个的**（实测 house1 scale=16 → 14.25 m）。
    #   ⚠ 有实测证据的是**那个问题**（客户反馈的原话 + 我方 plan_v1.json 里 house1 的 scale=16）；
    #     下面这段提示本身还没经过一次真跑 —— 别当成验过的。
    #   把这条"怎么读这一列"的规则钉在**表下面**（表是它必看的地方），不指望它记得文档。
    if scan.measured:
        lines.append("")
        lines.append(
            "⚠ **尺寸这一列是「资产自身」的大小，不是「场景里该有多大」** —— "
            "两者差几倍、几十倍都是**正常**的（实测例：house1 89 cm、tree 16 m）。"
            "阶段二按参考图给 `scale` 换算：**占地（`footprint_m`）= 实测包围盒 × `scale`**"
            "（89 cm 的房子要摆成 12 m 宽 → `scale` ≈ 13.5）。"
            "**资产偏小 / 偏大是常态，用 `scale` 解决 —— 不要为它换资产，更不要拿这个去问用户。**"
        )
    if mat_only:
        lines.append("")
        lines.append(
            "⚠ 下面这些元素**只拿材质 / 材质实例顶着** —— 材质能做表面覆盖（路面 / 草坪），"
            "但**没有包围盒、量不出尺寸，摆不出实体**："
        )
        for _name in mat_only:
            lines.append(f"  - {_name}")
        lines.append(
            "  用户 2026-09-24 的要求是：这类元素**先给预估尺寸、登记成白膜占位**，"
            "再确认清单。**到底当表面用还是登记白膜，请用户拍板** —— "
            "本工具不替他决定（不猜是硬规则）。"
        )

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
            "  ⚠ 本工具**不推断**原因。可能是：找不到名字对得上的资产 / "
            "本来就按光照环境处理不走资产。**要原因，得问用户。**"
            "（路径验证不通过的行走不到这里 —— 它们在使用前就被拒收了。）"
        )
    return lines


async def _sign_library_snapshot(ctx: Context[AppContext], delivered_at: str) -> str:
    """给这张清单「签字」：记下当时的**资产库指纹**，返回一句写给用户的说明。

    为什么要有它：之后 `get_asset_list()` 才判定得了这张表过没过期（指纹对不上 = 表过期）。
    best-effort —— 指纹只是"下次判定过期"的依据，不该因为它失败就把整次使用判死；
    但失败**必须说出来**，不许默默吞掉。
    """
    try:
        inventory, fp_calls = await library_inventory(ctx)
        digest, counts = library_fingerprint(inventory)
        save_json(LIBRARY_PATH, {
            "captured_at": delivered_at,
            "hash": digest,
            "counts": counts,
            "total": sum(counts.values()),
            "note": "confirm_assets 落盘时记的资产库指纹；只覆盖路径层面（哪些资产存在）",
        })
        return (
            f"资产库指纹已记录（{sum(counts.values())} 个资产 / {fp_calls} 次枚举）—— "
            "之后 get_asset_list() 才判定得了这张表过没过期。"
        )
    except Exception as exc:
        # 这里刻意接 Exception（比 ToolError 宽一档）：使用本身已经落盘了，
        # 指纹只是"下次判定过期"的依据。而 Exception 接不住 CancelledError
        # （BaseException 子类）—— 这正是我们要的：关机/取消要能正常往外抛。
        return (
            f"⚠ 资产库指纹记录失败（{type(exc).__name__}: {exc}）—— "
            "这张表下次取用时会报『没有指纹』。"
        )


@mcp.tool()
async def confirm_assets(
    ctx: Context[AppContext],
    items: Annotated[
        list[AssetListItem],
        Field(description="用户确认过的「元素 → 资产」对照表（exists 字段由本工具回填）"),
    ],
) -> AssetListDelivery:
    """**阶段一收尾 + 使用**：验证资产是否真的存在 → 实测尺寸 → 落盘 → 签字 → 输出清单。

    做七件事：
      ① 逐个调官方 `exists()` **验证路径**（不是形式主义，见下）
      ② **实测尺寸**：网格调官方 `get_bounds`，把包围盒 X×Y×Z 与体积**回填进表**
         （调用方填的尺寸一律被实测值覆盖 —— 理由见 measure_asset 的说明）
      ③ 落盘到 `catalog/asset_list.json`（这是清单文件，不是 UE 资产，不违反"不许存盘"）
      ④ **给表签字**：记下当时的资产库指纹到 `catalog/library_snapshot.json`，
         之后 `get_asset_list()` 才判定得了这张表过没过期
      ⑤ 生成 `deliverable` —— 一段**给人看的清单正文**（带尺寸/体积两列），直接贴给用户确认
      ⑥ 列出**未落位元素**（元素清单里有、本表里没有对应资产）到 `unresolved_elements` ——
         免得它静默消失：读表的人必须一眼看出"这份清单不完整"，而不是以为一共就这么多
      ⑦ **白膜占位**：下面这两类元素**先给预估尺寸、登记成白膜**，再让用户确认清单（见下）

    ⚠ **第 ⑦ 步：什么元素要登记白膜占位**（2026-09-24 用户要求）
      ① 找不到资产的（`plan_assets` 报 missing）、或用户确认"这个没有"的元素；
      ② **现在只有材质实例顶着**的元素（如 road / grass）—— 材质没有包围盒、摆不出实体，
         等于没资产。
      两类都填：`is_placeholder=true`、`asset_path` 留空、
      `placeholder_shape` 填 `cube`（有体积的，如窗/车道）/ `plane`（覆盖面，如路面/草坪）、
      `size_cm` 给**预估尺寸**、`size_source` 写明 `预估（参考图目测，待核实）`。
      ⚠ 四样**缺一不可**，填错**当场拒收**（不是警告）：带路径的白膜（自相矛盾）、
      没写形状（阶段三没法实例化）、没给尺寸（等于没占位）、
      尺寸来源写成"实测"（预估值伪装成实测值 —— 硬规则 6）。

    ⚠ **路径验证不通过的行，本工具现在直接拒收**（2026-09-24 整改，理由如下）：
      以前它把这种行记进 `invalid_paths` 照常使用，于是"元素没有资产"这件事
      只在备注里出现，清单本身照样带着一条**解析不到的路径**往下走。
      按用户的要求（`docs/阶段一-资产确认.md` 第 9 步）：**没有资产 = 登记成白膜占位**。
      所以现在二选一 —— 要么给对路径（`exists()` 通过），要么登记成白膜；
      **拒收时 catalog/ 一个字节都不写**，不会把上一次使用物覆盖成半新半旧。

    ⚠ **白膜只是"清单登记"，不是"往关卡里摆东西"**：
      `docs/阶段一-资产确认.md` 硬规则 4 —— 用户确认资产列表前不许摆放任何东西；
      规划图闸门也一样。真正生成白膜是**阶段三**（官方 PrimitiveTools 往 Actor 上加 Cube/Plane
      图元就行，**不用新建任何 UE 资产、不用存盘**）。
    ⚠ **预估值不许伪装成实测值**：白膜行没有东西可测，它的 size_cm 就是你填的那个数；
      必须在 size_source 里写明是预估、待核实 —— 用户确认清单时要能一眼看出哪个是估的。
      `plane`（面）类要额外说明：它的覆盖面**阶段二会用参数几何重算**，这里的预估值只是占位。

    ⚠ 每行的 `element_key` 请填**元素关键词**（与 elements.json 里的元素名一致）：
      `get_asset_list()` 靠它算"还有哪些元素没落位"。不填就只能按标签首词推断，
      而那只是推断 —— 返回值里会明说，不会假装是事实。

    ⚠ 为什么必须逐个验证存在性（实测价值）：
      2026-09-23 实测：`/Game/Fab/Urban_Street_Pack` 整个文件夹在我工作期间消失了
      （材质实例总数 58 → 56，exists() = False），而我此前一直以为人行道材质还在用它的
      Material_002。有这一步就会立刻暴露。
      **"我以为它在"和"它真的在"是两回事。**

    ⚠ **只拿材质 / 材质实例顶着的行**：本工具**只点名、不替用户改**
      （`note` 里列出来）—— 到底"就用这个材质当表面"还是"登记成白膜"，得用户拍板；
      替用户决定就是猜（硬规则 1）。
    """
    results: list[AssetListItem] = []
    stored = load_json(ELEMENTS_PATH) or {}

    # 大类（2026-09-24 用户要求：资产清单也要分大类）
    # 调用方填了就用它；没填就从 elements.json 那张表里按关键词取；
    # 两边都没有 = 未分类。中文名一律查表回填 —— 表是唯一来源。
    _cat_table = load_categories()
    _cat_by_key = _asset_category_map(stored)

    scan = _AssetScan()

    for item in items:
        _cat = _item_category(item, _cat_by_key)

        # 场景项（地图大小 / 地图对应时间）不是资产 —— 不许写进资产清单
        if is_scene_category(_cat, _cat_table):
            raise ToolError(
                f"{item.element!r} 是**场景项**（{category_cn(_cat, _cat_table)}），"
                "不是资产 —— 资产清单里不该有它。它的值已经记在 catalog/elements.json 里了。"
            )

        # 白膜占位行：没有资产可验、没有东西可量 —— 校验填得合不合规矩。
        # 2026-09-24 用户要求：找不到资产的、用户确认没有的、只有材质实例顶着的元素，
        # 都要先给预估尺寸、登记成白膜，再让用户确认清单。只是清单登记，不往关卡里摆东西。
        # 校验不过的收进 scan.bad_rows、等整表走完再一次性拒收（别探到第一个坏行就抛）。
        if item.is_placeholder:
            row, bad = _placeholder_row(item, _cat, _cat_table)
            scan.bad_rows += bad
            scan.placeholders += 1
            results.append(row)
            continue

        row, size_kind, bad = await _asset_row(ctx, item, _cat, _cat_table)
        if bad:
            scan.bad_rows.append(bad)
        if size_kind == "measured":
            scan.measured += 1
        elif size_kind == "no_box":
            scan.no_size += 1
        else:
            scan.size_failed += 1
        results.append(row)

    # 只拿材质 / 材质实例顶着的行：摆不出实体，要点名（2026-09-24 用户要求）。
    # 只点名、不替用户改 —— 当表面用还是登记白膜，得用户拍板（硬规则 1「不猜」）。
    mat_only = [
        r.element for r in results
        if not r.is_placeholder
        and str(r.asset_type or "").strip().lower() in ("material", "material_instance")
    ]

    # 不合格的行：拒收，一个都不许落盘（白膜填错 / 路径验不过）。
    # 一次列全再抛（一个一个抛，用户得改一轮试一轮）；且在写盘之前抛 ——
    # 写盘在函数末尾，在这里抛 = catalog/ 一个字节都没动。
    if scan.bad_rows:
        raise ToolError(
            f"资产清单**拒收**：有 {len(scan.bad_rows)} 行不合格 —— 先改掉，清单没落盘。\n"
            "  - " + "\n  - ".join(scan.bad_rows)
        )

    # 未落位元素：元素清单里有、使用表里没有的 —— 不许静默消失（见 _unresolved_elements）。
    delivered_keys, _cov_mode = element_keys_from_items([r.model_dump() for r in results])
    unresolved = _unresolved_elements(stored, delivered_keys)

    lines = _render_asset_list(results, stored, _cat_table, scan, mat_only, unresolved)

    base_note = (
        "资产清单已生成并落盘（catalog/asset_list.json）。"
        + (f"其中 **{scan.placeholders} 行是白膜占位、尺寸为预估** —— 请用户重点确认这些尺寸。"
           if scan.placeholders else "")
        + (f"⚠ 另有 {len(mat_only)} 行**只有材质/材质实例顶着**（摆不出实体）："
           + "、".join(mat_only)
           + " —— 请用户拍板：当表面用，还是登记成白膜占位。"
           if mat_only else "")
        + "⚠ 表里的「尺寸」是**资产自身**的包围盒，**不是**场景目标尺寸：偏小 / 偏大用阶段二的 `scale` 换算"
          "（`footprint_m` = 实测 × `scale`）—— **不许拿「这个资产只有 89 cm」去问用户要不要换资产**"
          "（那是**你**该算的，不是让用户拍的）。"
        + "**请把 deliverable 贴给用户做最终确认。**"
    )
    delivery = AssetListDelivery(
        delivered_at=datetime.now(timezone.utc).isoformat(),
        source_image=stored.get("source_image", ""),
        total=len(results),
        verified=sum(1 for r in results if r.exists),
        placeholders=scan.placeholders,
        # invalid_paths 恒为空：路径验不过的行在上面就被拒收了，走不到这里。
        invalid_paths=[],
        items=results,
        unresolved_elements=unresolved,
        deliverable="\n".join(lines),
        note=base_note,
    )
    # 给这张清单「签字」：记下当时的资产库指纹（best-effort —— 理由见 _sign_library_snapshot）。
    fingerprint_note = await _sign_library_snapshot(ctx, delivery.delivered_at)

    delivery = delivery.model_copy(
        update={"note": f"{delivery.note} {fingerprint_note}"}
    )
    save_json(ASSET_LIST_PATH, delivery.model_dump())
    return delivery


# --- [完工-09] 工具 6：取清单（读）---  【模块：assets】
# 实测 2026-09-23 12:12（stale=false；bush 从"还缺的元素"里消失）、12:24（items 带尺寸）。
#
# 为什么需要它（用户 2026-09-23 拍板加）：工具 5 是写表，本工具是读表 + 判定这张表
# 还作不作数。没有它，Agent只能自己去读 catalog/asset_list.json，那就绕过了 MCP，
# 等于把"用哪张表"交回给模型自觉。

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
    ⚠ **闸的钥匙**（不是可选步骤）：`_session_prereq_guard` 要它（每个 server 进程一次）；它也负责报「这张清单过没过期」。

    它回答四个问题：
      ① 有没有使用过清单 —— 没有就别往下走，先用 confirm_assets 落一份；
      ② 这张表还是不是签字时的那张 —— 用**资产库指纹**比（哈希 + 每类数量）；
      ③ 表里的路径**逐条**还活着吗 —— verify_paths=True 时调官方 exists() 验；
      ④ 还没落位的元素，现在出现候选了吗 —— 实况枚举后按【资产名/文件夹名】两条路匹。

    顺带把**整张表原样带出**（`items` 字段）：每行的元素、路径、类别，以及使用时
    **实测的包围盒尺寸与体积** —— "列出表"用这一个调用就够，不必去读 catalog 里的文件。

    ⚠ **只读**：不改 asset_list.json、不碰关卡、**不猜**。第 ④ 步只报"名字对得上"的候选，
      找不到就是空 —— 禁止拿别的资产顶替。
    ⚠ 指纹只覆盖**路径层面**（哪些资产存在），覆盖不了"同一个路径内容变了"
      （你把某个材质实例的贴图换了，路径没变、exists() 仍是 true）。
    ⚠ **开场先查状态**：讲流程 / 给方案 / 报进度**之前**先调本工具 —— 不是「动东西之前」，
      是「**开口之前**」。状态只认本工具的**返回值**，不认文件里叙述性的旧措辞
      （例：`plan_v1.json` 里那句「参数或几何均未变，沿用上一次确认」——
      实际参数后来改过，那行文案早已过期）。
    ⚠ Agent**不许**用上下文里记得的旧表；每次开工都必须重新调本工具。
    """
    # ⚠ 这次调用本身要留痕（2026-09-26 加）：`execute_build` 落关卡前会核对"开场动作做过没有"，
    #   见 `_session_prereq_guard()`。**在入口就记**（不看返回值）—— "清单还没使用"这类
    #   正常结果也算调过，否则预检没走完时就变成死锁（越没表越被拦、越被拦越没法推进）。
    _mark_session_prereq("get_asset_list")
    delivery = load_json(ASSET_LIST_PATH)
    elements_doc = load_json(ELEMENTS_PATH) or {}
    elements = [
        e.get("element", "")
        for e in elements_doc.get("elements", [])
        if isinstance(e, dict) and e.get("element")
    ]
    # 场景项不是资产、永远不会出现在资产清单里 —— 不许算成"未落位元素"（假缺口）
    _tbl_all = load_categories()
    _scene_names = {
        str(e.get("element") or "")
        for e in elements_doc.get("elements", [])
        if isinstance(e, dict) and is_scene_category(str(e.get("category") or ""), _tbl_all)
    }
    elements = [e for e in elements if e not in _scene_names]
    _scene_out, _scene_size, _scene_time = scene_from_rows(
        elements_doc.get("elements", []) or [], _tbl_all
    )
    warnings: list[str] = []
    calls = 0

    # 情况 0：压根没使用过清单 —— 这不是错误，是一种明确状态
    if not delivery:
        return AssetListStatus(
            has_list=False,
            stale=None,
            missing_elements=elements,
            next_step=(
                "还没有使用过资产清单：先 confirm_elements 记元素、plan_assets 找资产，"
                "再用 confirm_assets 落一份表（它会顺手给表记上资产库指纹）。"
                "**在这之前没有任何『权威清单』可用 —— 禁止凭上下文里的旧表干活。**"
            ),
            warnings=["catalog/asset_list.json 不存在。"],
        )

    items = delivery.get("items", []) or []
    # 表里每一行原样带出（含使用时实测的尺寸/体积），不做二次加工
    rows = [
        AssetListRow(
            element=str(it.get("element") or ""),
            element_key=str(it.get("element_key") or ""),
            category=str(it.get("category") or ""),
            category_cn=str(it.get("category_cn") or ""),
            asset_path=str(it.get("asset_path") or ""),
            asset_type=str(it.get("asset_type") or ""),
            is_placeholder=bool(it.get("is_placeholder")),
            placeholder_shape=str(it.get("placeholder_shape") or ""),
            size_cm=it.get("size_cm"),
            bbox_volume_cm3=it.get("bbox_volume_cm3"),
            size_source=str(it.get("size_source") or ""),
            note=str(it.get("note") or ""),
        )
        for it in items
    ]

    inventory, calls = await library_inventory(ctx)
    live_hash, live_counts = library_fingerprint(inventory)

    signed = load_json(LIBRARY_PATH)
    if signed:
        signed_hash = signed.get("hash", "")
        signed_counts = signed.get("counts", {}) or {}
        stale = signed_hash != live_hash
    else:
        signed_hash, signed_counts, stale = "", {}, None
        warnings.append(
            "这张清单**没有指纹**（是旧版本 confirm_assets 落的，或指纹文件被删了）。"
            "重跑一次 confirm_assets 就能给它签字，之后才判定得了『还作不作数』。"
        )

    changed = describe_count_changes(signed_counts, live_counts) if signed else []

    checks: list[PathCheck] = []
    if verify_paths:
        for it in items:
            if it.get("is_placeholder"):
                continue      # 白膜占位没有路径 —— 不许拿空串去 exists()
            path = it.get("asset_path", "")
            entry = PathCheck(element=it.get("element", ""), asset_path=path, ok=False)
            try:
                exists = await call_official(
                    ctx, "exists", {"path": path}, toolset=TS_ASSET
                )
                calls += 1
                entry.ok = exists is True or (
                    isinstance(exists, str) and exists.lower() == "true"
                )
            except ToolError as exc:
                calls += 1
                entry.error = str(exc)
            checks.append(entry)
    dead = [c.asset_path for c in checks if not c.ok]

    covered, mode = element_keys_from_items(items)
    missing = [e for e in elements if e.lower().strip() not in covered]
    found: dict[str, list[str]] = {}
    for kw in missing:
        cands = candidates_for(kw, inventory, max_per_element)
        if cands:
            found[kw] = [c.path for c in cands]

    # 缺失再按大类分一次组（2026-09-24 用户要求分大类）
    _cat_table = load_categories()
    _cat_by_element = {
        str(e.get("element") or ""): str(e.get("category") or "")
        for e in elements_doc.get("elements", []) if isinstance(e, dict)
    }
    _miss_cat: dict[str, list[str]] = {}
    for kw in missing:
        _miss_cat.setdefault(category_cn(_cat_by_element.get(kw, ""), _cat_table), []).append(kw)
    missing_by_cat = {cn: _miss_cat[cn]
                      for cn in order_category_names(set(_miss_cat), _cat_table)}

    # 按优先级给出下一步（死路径 > 过期 > 有新候选 > 可以用）
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
        _ph = sum(1 for it in items if it.get("is_placeholder"))
        next_step = (
            "清单 fresh、路径全部存活，可以用。"
            + (f"⚠ 表里有 {_ph} 行**白膜占位**（尺寸是**预估**、不是实测）—— "
               "用之前要让用户确认过这些尺寸。" if _ph else "")
            + "⚠ 照实说明：指纹只保证**路径有效**，不保证**同名资产的内容没被改过**。"
        )

    # ⚠ 「尺寸这一列怎么读」（2026-09-26 加；客户反馈）：agent 拿「house1 实测 89 cm」去问用户
    #   「要不要换资产」—— 而 `scale` 就是干这个的。挂在**每次都要读的清单返回**上。
    if any(isinstance(r.size_cm, list) and r.size_cm for r in rows):
        warnings.append(
            "⚠ 清单里的「尺寸」是**资产自身**的包围盒，**不是**场景里该有多大的目标尺寸"
            "（实测例：house1 89 cm、tree 16 m）。阶段二用 `scale` 换算："
            "`footprint_m`（米）= 实测包围盒（cm）÷ 100 × `scale`。"
            "**偏小 / 偏大是常态 —— 用 `scale` 解决，不要为它换资产、更不要拿这个去问用户。**"
        )

    return AssetListStatus(
        has_list=True,
        delivered_at=delivery.get("delivered_at", ""),
        source_image=delivery.get("source_image", ""),
        total=len(items),
        placeholders=sum(1 for it in items if it.get("is_placeholder")),
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
        missing_by_category=missing_by_cat,
        scene=_scene_out,
        new_candidates=found,
        coverage_mode=mode,
        official_calls=calls,
        next_step=next_step,
        warnings=warnings,
    )


# --- 阶段二辅助：参数合并 / 摘要 / 闸门取数 -------------------------------------  【模块：planning】

_PLANNING = None


def _planning_modules():
    """取规划层模块（plan）—— 惰性导入，带两种启动方式的兜底。

    为什么不在文件顶部 import（2026-09-23 血账，两次炸 server 的第二次）：
      ① 启动方式不兼容：Agent是按脚本方式起这个文件的
         （`python <仓库根>\\src\\mcp_server\\main.py`），这时 `__package__` 是空的，
         顶部写 `from .planning import ...` 会直接抛
         `ImportError: attempted relative import with no known parent package`
         —— 整个 server 起不来（Agent侧表现为 mcp-client(mine) 重连 7 次全败）。
      ② 不该连坐：规划层出任何问题，都不该把阶段一那 6 个工具一起带崩。
    """
    global _PLANNING
    if _PLANNING is not None:
        return _PLANNING

    errors: list[str] = []
    try:
        from .planning import plan as p
    except ImportError as exc:
        errors.append(f"相对导入失败：{exc}")
        # 脚本方式下 __package__ 为空，相对导入必失败 —— 自己把本文件所在目录放进
        # sys.path 再按顶层包导入。别依赖"Agent会把脚本目录加进 sys.path"（runpy 就不加）。
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            from planning import plan as p
        except ImportError as exc2:
            errors.append(f"顶层导入失败：{exc2}")
            # 两个错误都带上：只报第一个会让人查错方向
            raise ToolError("阶段二规划层导入失败（阶段一工具不受影响）："
                            + "；".join(errors)) from exc2
    _PLANNING = p
    return _PLANNING


def _planning_warnings(previous: dict) -> list[str]:
    """阶段二开工时的**提示**：清单读没读到、有没有元素"阶段一记过、但清单里没有"。

    ⚠ 2026-09-25 改（用户指令「阶段二不要这个自检阶段了，跟它有关的都记得改」）：
      本函数原来叫 `_planning_classify`，返回 `(known, no_asset, 来源说明, 判没判出来)` ——
      那四样只有 11 项自检在消费；自检删了，它们就没有下游了，一并去掉，只留对人的警告。

    留下的这条为什么值得留：它让"阶段一记过、阶段二却没落位"的元素在报文里**显形**
      （实测例子：`sky` / `map_size` / `time_of_day` —— 它们本来就不走资产，
       但读报文的人得知道，否则会以为阶段二漏了）。
    """
    known = {str(e.get("element") or "").strip().lower()
             for e in (previous.get("rows") or []) if e.get("element")}
    warnings: list[str] = []

    delivered = load_json(ASSET_LIST_PATH) or {}
    rows = [it for it in (delivered.get("items") or []) if isinstance(it, dict)]
    delivered_keys = {str(it.get("element_key") or "").strip().lower() for it in rows}
    delivered_keys.discard("")
    # 使用清单里的 `element_key` 才是**权威关键词**（`elements.json` 的 element 字段带中文后缀，
    # 形如 "trash_bin 红色垃圾桶"，拿它跟放置表的 element_key 比会全线对不上）。
    known |= delivered_keys

    if not rows:
        warnings.append("读不到 catalog/asset_list.json —— 阶段一使用清单没读到，请留意。")
        return warnings
    if not any("is_placeholder" in it for it in rows):
        warnings.append(
            "catalog/asset_list.json 还是**旧格式**（行里没有 is_placeholder 字段）—— "
            "重跑一次阶段一的 confirm_assets 会补上这个字段。"
        )
    not_delivered = sorted(known - delivered_keys)
    if not_delivered:
        warnings.append(
            "这些元素**在阶段一记过、但使用清单里没有**：" + "、".join(not_delivered)
            + "。它们可能本来就不走资产（如 sky 按光照环境处理）—— "
            "**要摆它们之前先向用户确认**，别当成漏了。"
        )
    return warnings


def _sync_scene_map_size(new_size, why: str) -> str:
    """把阶段二定下来的世界大小**回填**进阶段一的 `catalog/elements.json`（场景项「地图大小」）。

    为什么必须由**代码**干这件事（2026-09-24 用户要求 #6）：阶段一记的「地图大小」是从参考图
    **目测**来的，阶段二把确认过的元素一排，常常发现装不下（实测：记 120×30，实际进深要
    38.7 m → 120×40）。以前这件事只能**靠人手工回填** —— 换个 agent 就会忘，于是阶段一 /
    阶段二两份使用物**永久对不上**（`plan.world` 说自己被"调用方覆盖"、`elements.json` 还写着旧值）。
    现在由 `generate_plan` 在"调用方覆盖了 `world.size`"时**自动同步**，谁都不需要记得。

    只动 `map_size` 那一行 + 两个派生字段（`scene` / `scene_map_size_m`），其余原样保留；
    回填写进该行 `note`（谁改的、什么时候、原值多少 → 新值多少）。返回一句人话（没改则空串）。
    """
    doc = load_json(ELEMENTS_PATH) or {}
    rows = doc.get("elements")
    if not isinstance(rows, list):
        return ""
    target = None
    for e in rows:
        if isinstance(e, dict) and str(e.get("category") or "") == "map_size":
            target = e
            break
    if target is None:
        return ""
    try:
        old = [float(v) for v in (target.get("size_m") or [])][:2]
    except (TypeError, ValueError):
        old = []
    new = [float(new_size[0]), float(new_size[1])]
    if old == new:
        return ""                       # 已经一致：什么都不做（幂等 —— 重复调用不会刷出多条备注）
    old_txt = "×".join(f"{v:g}" for v in old) if old else "没记"
    target["size_m"] = new
    target["note"] = (str(target.get("note") or "").rstrip()
                      + f"\n【由阶段二回填】{datetime.now(timezone.utc).isoformat()}："
                        f"阶段一原记 {old_txt} m，{why} → 回填为 {new[0]:g}×{new[1]:g} m。")
    scene = dict(doc.get("scene") or {})
    scene["地图大小"] = f"{new[0]:g}×{new[1]:g} m（预估）"
    doc["scene"] = scene
    doc["scene_map_size_m"] = new
    save_json(ELEMENTS_PATH, doc)
    return (f"阶段一记的『地图大小』（{old_txt} m）与这版世界 {new[0]:g}×{new[1]:g} m 不一致 —— "
            "**已由代码回填**进 catalog/elements.json 的 map_size 行（免得两份使用物对不上；"
            "这种事不许靠人记得）。")


def _planning_snapshot_check() -> list[str]:
    """查「使用清单和资产库指纹是不是**同一次签字**」→ 警告列表（只读、离线）。

    ⚠ **照实说清它查不出什么**：本工具是**离线**的（刻意的 —— 阶段二不依赖 UE，编辑器关着
    也能跑），所以"资产库**后来**变了"这件事它查不出来 —— 那要调 `get_asset_list()`
    （只有它会实况枚举 + 判过期）。这里只保证两份使用物**互相对得上**：
    `asset_list.json` 的 `delivered_at` 应当 == `library_snapshot.json` 的 `captured_at`。
    """
    warnings: list[str] = []
    delivered = load_json(ASSET_LIST_PATH) or {}
    if not delivered.get("items"):
        return warnings                      # 还没使用过清单：由阶段一那边去管，这里不喊
    snapshot = load_json(LIBRARY_PATH) or {}
    if not snapshot:
        warnings.append(
            "读不到 catalog/library_snapshot.json —— 这份使用清单**没有资产库指纹**，"
            "『清单没过期』这件事**没验**（要验请调 get_asset_list()）。"
        )
        return warnings
    d_at = str(delivered.get("delivered_at") or "")
    s_at = str(snapshot.get("captured_at") or "")
    if d_at and s_at and d_at != s_at:
        warnings.append(
            f"使用清单与资产库指纹**不是同一次签字**（清单 {d_at} / 指纹 {s_at}）—— "
            "要么清单被手改过、要么指纹被单独换过。用之前先调 get_asset_list() 查实况。"
        )
    if not snapshot.get("hash"):
        warnings.append("catalog/library_snapshot.json 里没有 hash —— 指纹不可用。")
    return warnings


def _summarize(plan: dict) -> PlanSummary:
    """从放置数据里数几个数出来。

    ⚠ 2026-09-25：原来还会数"自检几项、没过几项" —— 阶段二自检已整段删除（用户指令），
      这里不再读 `plan["checks"]`；旧 `plan_v1.json` 里残留的该字段被**忽略**。
    """
    world = plan.get("world", {}) or {}
    return PlanSummary(
        world_center=world.get("center", []) or [],
        world_size=world.get("size", []) or [],
        assets=len(plan.get("assets", []) or []),
        whiteboxes=len(plan.get("whiteboxes", []) or []),
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


def _readback_guard() -> None:
    """**「改完必须回读」这道闸**（2026-09-26 用户要求：文档是软约束，要更强的）。

    谁调它：`generate_plan`（写之前）与 `execute_build`（碰 UE 之前）。
    不通过就抛 `ToolError`，并把**这一版到底改了什么**原样摆出来 —— 即使 agent 想跳步，
    也得先把事实读进上下文。**清掉它的唯一途径是调 `get_plan()`**（只读工具，改不了数据）。
    为什么要有它：以前"改完必须回读核对"只写在 AGENTS.md / docs 里 —— 实测被漏过一次，
    结果一条该删的行残留在 plan 里，直到下一次偶然 `get_plan()` 才发现。
    ⚠ 台账读不动时**放行**（不拿基础设施故障拦人）；拦不拦都不改任何几何。
    """
    planning = _planning_modules()
    try:
        pend = planning.pending_readback()
    except Exception:                       # noqa: BLE001 —— 台账读不动不拦人
        return
    if not pend:
        return
    cs = pend.get("change_set") if isinstance(pend.get("change_set"), dict) else {}
    bits: list[str] = []
    for key, word in (("added", "新增"), ("changed", "改动"), ("removed", "删除")):
        vals = cs.get(key) or []
        if vals:
            head = "、".join(str(x) for x in vals[:6])
            tail = f" 等 {len(vals)} 行" if len(vals) > 6 else ""
            bits.append(f"{word} {len(vals)} 行：{head}{tail}")
    detail = "；".join(bits) if bits else "（与上一版几何相同）"
    raise ToolError(
        "拒收：**这一版你还没回读核对** —— 先调 `get_plan()`（只读，看一眼那几行）再回来继续。\n"
        f"· 待核对的几何指纹：{str(pend.get('plan_hash') or '')[:10]}…（写于 {pend.get('at') or '?'}）\n"
        f"· 它相对上一版的变化：{detail}\n"
        "（这道闸为什么存在：以前「改完要回读」只写在文档里，实测被漏过 —— 一条该删的行残留到"
        "下一次偶然 `get_plan()` 才发现。现在由代码强制：不调 `get_plan()`，`generate_plan` 与 "
        "`execute_build` 都不放行。）"
    )


# --- 阶段三 · 「开场动作」的登记与闸（2026-09-26 加；**尚未实测**）---------------------  【模块：state（开场动作闸）】
# 客户 agent 使用后自评里「写在描述里但代码不拦的」两条：
#   · 「开场先调 get_asset_list」—— **没调**（开场只调了 get_plan）；
#   · 「依赖官方前先 official_status」—— **没调**（直接 list_tools 就开干了）。
# ⚠ 这是**纪律闸**，不是正确性闸：它保证不了"你读懂了返回内容"，只保证"你调过"。
#   敢拦的理由：两个都是**只读、各一次就够**的开场动作，代价极低；
#   而它们的返回正是"要不要继续往下走"的判断依据（链路通不通 / 清单过没过期、还有谁没落位）。
# ⚠ 只记**本 server 进程内**的调用（服务重启就清零）—— 如实写清楚，不假装是永久记录。

_SESSION_PREREQ_DONE: set[str] = set()

# --- 链路状态（2026-09-30 加：用户要求「每次要动 UE 必须检查连接状态」）--------------  【模块：gate】
# 记的是 `official_status()` **这次的返回值**，不是"调过没有" —— 只记"调过"等于没检查：
#   2026-09-30 实测踩到：开机后客户端与 MCP 断连（stdio 子进程没被拉起 / 会话工作区没恢复），
#   Agent 一上来就调工具，一路以**最难懂的方式**失败，排查花很久。
# `None` = 本进程里**还没检查过**；`True` = 上次检查**连上了**；`False` = 上次检查没连上（原因见 `_SESSION_LINK_ERR`）。
_SESSION_LINK_OK: bool | None = None
_SESSION_LINK_ERR: str = ""


def _mark_session_prereq(name: str) -> None:
    """记下"本进程里调过某个开场工具了"。只加不减。"""
    _SESSION_PREREQ_DONE.add(str(name))


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


def _session_prereq_guard() -> None:
    """**碰关卡之前，先做过开场动作** —— 只认本 server 进程内的调用记录。

    谁调它：`execute_build()`（真跑之前；`dry_run` 与 `adopt` 不拦 —— 一个只看、一个只读现状）。
    缺哪个就点名哪个，并把"它给的是什么"写清楚 —— 拒绝要能教会人，不然只是添堵。
    """
    need = ["official_status", "get_asset_list"]
    missing = [n for n in need if n not in _SESSION_PREREQ_DONE]
    if not missing:
        return
    raise ToolError(
        "拒收：**开场动作还没做** —— 一个 Actor 都没动。缺："
        + "、".join(f"`{n}()`" for n in missing) + "\n"
        "为什么要它们（都只读、各一次就够）：\n"
        "· `official_status()` —— 先确认官方链路（协议版本 / 工具集数）；链路不通时，"
        "后面每一步都会以更难懂的方式失败；\n"
        "· `get_asset_list()` —— 你正要摆的东西的账本：这份清单**过没过期**、"
        "还有哪些元素**没落位** —— 搭建之前就该知道，而不是搭完才发现。\n"
        "（客户 agent 自评原话：「开场只调了 get_plan，没调 get_asset_list」、"
        "「依赖官方前没调 official_status，直接 tools/list 就开干了」。"
        "这道闸只认**本 server 进程内**调过没有 —— 服务重启后要再调一次。）"
    )


def _change_request_guard(planning, plan: dict) -> None:
    """**「改一份"已经被确认过"的规划之前，先留下『改什么 / 谁要的』」这道闸**（2026-09-26 加）。

    为什么要有它（客户 agent 使用后自评的原话）：「用户提改动要调 `request_plan_change` ——
      **一次都没调过** —— 你每次说要改，我直接 patch，没走变更台账」。
    后果不是几何错，而是台账回答不了「用户说了什么 ↔ 数据变成什么」——
      而那份台账存在的**全部意义**就是那个对应关系（他自己也写了：「这是我流程纪律的问题」）。
    判据（三条**同时**成立才拦）：
      ① 这一版与盘上那版**指纹不同**（真的一次内容变更；原样重发 / 只重算一遍不算）；
      ② 之前**确认过至少一版**（台账里有 `accepted_rows`）—— 第一次排表不受影响；
      ③ 台账的**改动窗口是关着的**（`change_window_open`）——
         由 `request_plan_change()` 打开、由 `confirm_plan()` 关闭，一轮里可以改多次。
    出路（**不是要你等用户开口，是要你留下对应关系**）：
      · 用户提的 → `request_plan_change(items=[他的原话], by="用户")`；
      · 你自己发现要修 → `request_plan_change(items=["自查：…"], by="agent 自查")`。
    ⚠ 台账读不动 → **放行**（与 `_readback_guard` 同口径：基础设施故障不拦人）。
    ⚠ 位置说明：本闸在 `_sync_scene_map_size()`（阶段一世界大小回填）**之后**跑 ——
      "同一次调用里既覆盖了世界大小、又被本闸拒收"这一种情况下，阶段一那份文件**已经**回填过。
      如实写在这里，不假装整条路径是原子的（要同时满足两个罕见条件才遇上）。
    """
    try:
        acc = planning.load_acceptance()
    except Exception:                       # noqa: BLE001 —— 台账读不动不拦人
        return
    if not isinstance(acc, dict) or not acc.get("accepted_rows"):
        return                              # 从没确认过 → 第一次排表，自由
    if acc.get("change_window_open"):
        return                              # 已经记过"要改什么 / 谁要的" → 窗口开着，放行
    new_hash = planning.plan_geometry_hash(plan)
    old: dict | None = None
    try:
        if planning.OUT_JSON.exists():
            old = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        old = None
    if isinstance(old, dict) and planning.plan_geometry_hash(old) == new_hash:
        return                              # 一模一样地重发 → 不拦（它连上次确认都不会丢）
    raise ToolError(
        "拒收：**这一版要改内容，但台账里没有「改什么 / 谁要的」的记录** —— 一个字节都没写。\n"
        "· 这份规划**已经被用户确认过**（台账里有确认基线）—— 改动会把那次确认**作废**；\n"
        "· 「用户说了什么 ↔ 数据变成什么」的对应关系，就靠 `request_plan_change()` 那一条记录留住"
        "（客户 agent 自评：他直接 patch，一次都没调过它）。\n"
        "先调一次 `request_plan_change(items=[...], by=...)`：\n"
        "· 用户提的改动 → `items` 用**他的原话**，`by=\"用户\"`；\n"
        "· 你自己发现要修的 → `items=[\"自查：…\"]`，`by=\"agent 自查\"`。\n"
        "记完就能改（窗口一直开到下一次 `confirm_plan()`，所以一轮里可以连着改好几次）。"
    )


def _acceptance_model(plan: dict) -> PlanAcceptance:
    """取**阶段二验收**状态 —— 交给 `planning.acceptance_view()`（**现算**，不写盘）。

    为什么现算而不是读 `views/acceptance.json` 里的 `state` 字段：图常常是**落盘之后**
    才画好的，读快照就会拿着"等出图"的旧字样去回答"走到哪了"。
    （与 `status` 不落盘是同一条口径。）
    """
    planning = _planning_modules()
    try:
        view = planning.acceptance_view(plan)
    except Exception as exc:        # noqa: BLE001 —— 台账读不动不该把读工具带崩
        return PlanAcceptance(
            state="unknown",
            state_cn=f"验收台账没读成（{type(exc).__name__}: {exc}）",
        )
    return PlanAcceptance(
        stage=view.get("stage", "阶段二验收"),
        round=int(view.get("round") or 0),
        state=view.get("state", "") or "",
        state_cn=view.get("state_cn", "") or "",
        plan_hash=view.get("plan_hash", "") or "",
        figures=list(view.get("figures") or []),
        pending_changes=list(view.get("pending_changes") or []),
        change_window_open=bool(view.get("change_window_open")),
        change_set=dict(view.get("change_set") or {}),
        awaiting_readback=dict(view.get("awaiting_readback") or {}),
        updated_at=view.get("updated_at", "") or "",
        ledger_path=view.get("ledger_path", "") or "",
        history=list(view.get("history") or []),
    )


def _stage2_next_step(plan: dict, acc: PlanAcceptance, empty: bool = False) -> str:
    """「下一步做什么」的**唯一处**（generate_plan 与 get_plan 共用，免得两处话术漂移）。

    按验收状态给话术 —— 这就是"阶段二验收"这个阶段在人眼前的样子。
    """
    if empty:
        return (
            "**当前是初始化状态**（两张表都空）：结构已按新口径立好，但没有任何坐标 —— "
            "**因为坐标的依据是参考图，图还没来**。下一步：拿到用户上传的图、"
            "过完阶段一（提元素 → 用户确认 → 找资产 → 使用清单）之后，"
            "你按图规划每件东西的 `pos` / `footprint_m`，再调本工具填进去。"
            "**别在没图的时候编坐标**（编了就是替用户决定，且违反『不猜』）。"
        )
    head = f"第 {acc.round} 轮 · 几何指纹 {acc.plan_hash[:10]}… · "
    if acc.state == "accepted":
        return (
            f"**阶段二验收通过**（{head}确认人已留痕）—— 闸门已开。"
            "下一步进第三阶段：**⓪ 先做两个开场动作**（都只读、各一次）：`official_status()` "
            "（确认官方链路）+ `get_asset_list()`（清单过没过期 / 还有谁没落位）—— "
            "没做过，`execute_build()` 会拒收。"
            "**第一步是问清「搭哪张图」** `check_build_target()`"
            "（先只读地问一遍 → **把问题交给用户、停下等他打字** → 带他的原话再调一次把答复记下来；"
            "官方没有『新建关卡』的工具），⚠ 现在这是**代码闸**：没拿到答复就调 `execute_build()` "
            "会被拒收。"
            "**第二步是批量搭建**：`generate_build_orders()` 先把放置表翻成指令表"
            "（米→厘米、补 Z、plane 压成薄 cube），再 `execute_build()` —— 它整批校验"
            "（闸门/指纹/路径 exists/行数）→ 清掉 `UEMCP/` 下的旧 Actor → **按表一次性全落** → "
            "读回 `get_actor_transform` **与白膜组件尺寸**对账。想先看不动手就传 `dry_run=true`。"
            "⚠ **搭之前会先过回读闸**：这一版若还没 `get_plan()` 过，`execute_build` 会拒收 —— "
            "先看一眼再搭。⚠ 清场 / 重摆前还会**逐行读关卡现状与台账比**（位置 / 朝向 / 缩放 / "
            "白膜厚薄）：⚠ **只扫「本次会被删 / 重摆的那些行」**（不是全表 —— 2026-09-30 实测更正；"
            "纯增量真跑时范围之外的人工痕迹既不拦也不报 = 静默保留）；范围内改过的行、"
            "以及台账解释不了的活 Actor **默认拒收**（要按 plan 覆盖才传 "
            "`accept_user_edits=true`）；**没有台账却有旧 Actor 时不许悄悄全量清场**"
            "（先 `adopt=true` 认领现状）。⚠ 这一版**要想再改**：先 "
            "`request_plan_change(items=[...], by=...)` 留一条「改什么 / 谁要的」，"
            "否则 `generate_plan` 会拒收（客户 agent 自评：他一次都没调过它）。"
            "⚠ 全程**不存盘**。口径见 `docs/阶段三-资产布局生成.md`。"
        )
    if acc.state == "changes_requested":
        return (
            "**当前在【阶段二验收】，状态 = 用户提了要改的地方**（"
            + (f"{head}还没落到数据里：" + "；".join(acc.pending_changes)
               if acc.pending_changes else head + "台账里有改动记录")
            + "）。下一步：按这些改动改 `pos` / `footprint_m` / `scale` → **重调本工具**"
            "（几何一变自动进新一轮、上一版自动留档）→ **重画图**（图内写新指纹）"
            "→ 再把图使用成卡片给用户。循环到他说无误为止。"
            "⚠ **只改动的行才要重画**：改完调 `get_plan()` 看 `acceptance.change_set.required_labels` —— "
            "那就是这一轮图里**只需**出现的那几行（用户要求：局部改就全局部，不要全部重做）。"
            "⚠ **要出哪几张图也看那里**（`required_views`）：**位移 / 朝向 → 顶视图；"
            "大小 / 高度 → 正视图或左右视图**（`views/plan_v1_elevation*.svg`）；两样都动 → 两张都要。"
            "⚠ 只动高度时**顶视图看不出高低** —— 那一版就只要立面。"
        )
    if acc.state == "awaiting_user":
        figs = "、".join(acc.figures) if acc.figures else "（没有认这份数据的图）"
        return (
            f"**阶段二已结束，当前在【阶段二验收】—— 等待用户看图确认**（{head}图：{figs}）。"
            "下一步**只做三件事，然后停下**："
            "① 把 `acceptance.figures` 里的图**使用成用户能点开看的卡片**"
            "（MCP 自己弹不出卡片，交给Agent使用）；"
            "② 附一张**验收清单**：图里哪几处是他要判断的（如「车列位置」「树的疏密」）；"
            "③ **停下等他打字** —— 不要再往问题框里塞「确认吗」。"
            "他说改哪儿 → 调 `request_plan_change` 记下来；他说行 → `confirm_plan`。"
        )
    if acc.state == "awaiting_figure":
        cs = acc.change_set if isinstance(acc.change_set, dict) else {}
        req = [str(x) for x in (cs.get("required_labels") or [])]
        shown = "、".join(req[:8]) + ("…" if len(req) > 8 else "")
        if not cs:
            scope = "⚠ 变更集读不到（验收台账没读成）—— 按**全量**画：图里要出现**每一行**的 label。"
        elif cs.get("local"):
            scope = (f"⚠ 这是**局部一轮**（基线：{cs.get('baseline')}）—— 图里**只要**出现本次改动的 "
                     f"{len(req)} 行：{shown}；另外 {cs.get('same')} 行**没动、不必重画**"
                     "（用户要求：局部改就全局部，不要全部重做）。")
        else:
            scope = (f"⚠ 还没有『确认过的基线』→ 这一版按**全量**画：图里要出现**每一行**的 label"
                     f"（共 {len(req)} 行），一行都不能漏。")
        # ⚠ **每一版都出两张图**（2026-09-30 用户定案）⇒ 这里不再按维度挑图，只报"要出哪两张"
        _views = [str(x) for x in (cs.get("required_views") or ["top", "elevation"])]
        _names = " + ".join("**顶视图（X-Y，俯视）**" if v == "top"
                            else "**原图视角正视图（Y-Z）**" for v in _views)
        scope += ("⚠ 这一版**要出的图**：" + _names
                  + (f"；{cs.get('views_why')}" if cs.get("views_why") else "") + "。")
        _have = "、".join(str(x) for x in (acc.figures or [])) or "（一张都没有）"
        scope += (f"⚠ **现在认账的图**：{_have} —— **缺哪张补哪张**"
                  "（`acceptance.figures` 是「认账的」，上面那两张是「该有的」；"
                  "两边齐了状态才会变成 `awaiting_user`）。")
        return (
            f"**当前在【阶段二验收】的「等出图」**（{head}views/ 里没有认这份数据的图）。"
            "下一步**先 `get_plan()`**（只读，过**回读闸** —— 这一版写完之后你还没回读，"
            "不调它，下一次 `generate_plan` / `execute_build` 都会被拒收），"
            "② 再拿 `drawing` 段（`get_plan()` 里那份**每行带 `z_base_m` / `z_top_m`** 的几何）"
            "**自己画两张图**（每一版都出这两张）—— "
            "**顶视图**存 `views/plan_v1_overview*.svg`、**原图视角正视图**存 "
            "`views/plan_v1_elevation*.svg`"
            "（前缀是代码找图的约定；存成别的名字，确认时会报「views/ 里没有**那一路**的图」）—— "
            f"**每一张**图内都必须写明当前几何指纹 `{acc.plan_hash[:10]}…`"
            "（否则确认时会被判成「图不认数据」），"
            + scope + "再使用给用户（**两张就是两张卡片**）。**没有图不许让用户确认。**"
        )
    return (
        "现在**没有任何坐标**：先去拿用户上传的图，走完阶段一（提元素 → 用户确认 → "
        "找资产 → 使用清单），再按图规划每件东西的 `pos` 填进来。**别在没图时编坐标。**"
    )


def _write_plan(plan: dict) -> dict:
    """落盘计划数据 —— 交给 `planning.write_plan()`（**全工程唯一写入点**）。

    为什么改成委托（2026-09-24 收尾清单 #4 / #8）：写盘之外还要**给上一版留档**、
    还要**清除不认这份数据的图**，而 CLI 的 `--confirm` 也走同一份逻辑 —— 写入点只能有一个，
    否则两条路迟早写出不一样的文件。返回它的结果字典（留档名 / 清掉的图 / 留下的图）。
    （净化 `-0.0` / 禁用 NaN 都在 `write_plan` 里，行为与原先逐字一致。）
    """
    planning = _planning_modules()
    return planning.write_plan(plan)


_PATCH_ASSET_FIELDS = {"label", "element_key", "asset_path", "pos", "footprint_m",
                       "rot_deg", "scale", "scale_z", "note", "z_m"}
_PATCH_BOX_FIELDS = {"label", "element_key", "shape", "pos", "footprint_m",
                     "rot_deg", "height_m", "size_source", "note", "z_m"}
"""补丁里**允许改**的字段（按行类型分）。白名单是刻意的：
给资产行写 `height_m`、给白膜行写 `scale`/`asset_path` 都是**类型不对**的改法，
不该被静默接受（它们会被 `normalize_*` 或后续阶段当成有效数据读走）。
⚠ 资产行的 `scale_z`（Z 方向倍率，2026-09-30 加）= **只拉高 / 压低、占地不动**；
   白膜行对应的字段是 `height_m`（直接就是高度），所以 `scale_z` **不在**白膜行的白名单里。"""


def _apply_plan_patch(cur_plan: dict, patch: list) -> tuple[list[dict], list[dict], list[str]]:
    """按 `label` 给**现有**放置表打补丁 → `(资产行, 白膜行, 说明)`。不合规 → 抛 `ToolError`。

    为什么要它（2026-09-26 用户要求：「用户想局部改，**所有都局部改，不要全部重做**」）：
    原来改一行也得把**整张表**（89 行）重新发一遍 —— 数据没被局部对待，"全部重做"只是从
    "重画图"挪到了"重发数据"上。现在只发改动的那几行 + 一条指令。

    四种操作（`op` 字段）：
      · `set`          `{"op":"set","label":"住宅 #1（house1）","set":{"pos":[-53,-32]}}`
      · `add_asset`    `{"op":"add_asset","row":{...整行...}}`
      · `add_whitebox` `{"op":"add_whitebox","row":{...整行...}}`
      · `remove`       `{"op":"remove","label":"..."}`

    ⚠ 定位**只认 `label`**（阶段二的规则：同类多个必须能区分）——必须**唯一命中**；
      命中 0 行（要用 add_*）或命中多行（先把 label 改得互不相同）都**拒收**。
    ⚠ 全程在**内存副本**上做：任何一条不合规 → 整个调用拒收，**一个字节都不写** ——
      不会留下"半新半旧"的放置表（与 `confirm_assets` 拒收时同一条纪律）。
    ⚠ 打完之后**仍走原来的整表校验**（`normalize_asset` / `normalize_whitebox`）——
      补丁没有自己的"宽松通道"。
    """
    asset_rows = [dict(r) for r in (cur_plan.get("assets") or []) if isinstance(r, dict)]
    box_rows = [dict(r) for r in (cur_plan.get("whiteboxes") or []) if isinstance(r, dict)]

    def _hits(label: str) -> list[tuple[str, int]]:
        out = [("asset", i) for i, r in enumerate(asset_rows)
               if str(r.get("label") or "") == label]
        out += [("whitebox", i) for i, r in enumerate(box_rows)
                if str(r.get("label") or "") == label]
        return out

    def _find(label: str, op_name: str) -> tuple[str, int]:
        if not label:
            raise ToolError(f"补丁里有一条（{op_name}）没给 `label` —— 定位只认 label。")
        hits = _hits(label)
        if not hits:
            raise ToolError(
                f"补丁（{op_name}）要动的行 `{label}` 在**当前放置表里找不到** —— "
                "补丁只能动**已有**的行；要加新行用 `add_asset` / `add_whitebox`。")
        if len(hits) > 1:
            raise ToolError(
                f"补丁（{op_name}）要动的行 `{label}` 在当前表里**有多行同名** —— 认不出是哪一个。"
                "请先把这几行的 label 改得互不相同（阶段二允许同类多行，但每行要有区别）。")
        return hits[0]

    notes: list[str] = []
    for k, op in enumerate(patch or [], 1):
        if not isinstance(op, dict):
            raise ToolError(f"补丁第 {k} 条不是对象：{op!r}")
        kind = str(op.get("op") or "").strip()
        if kind == "set":
            where, idx = _find(str(op.get("label") or "").strip(), "set")
            changes = op.get("set")
            if not isinstance(changes, dict) or not changes:
                raise ToolError(f"补丁第 {k} 条（set）没给 `set` —— 空补丁没有意义。")
            allowed = _PATCH_ASSET_FIELDS if where == "asset" else _PATCH_BOX_FIELDS
            bad = sorted(f for f in changes if f not in allowed)
            if bad:
                raise ToolError(
                    f"补丁第 {k} 条（set）里 {bad} 不是"
                    f"{'资产' if where == 'asset' else '白膜'}行能改的字段 —— 可改：{sorted(allowed)}")
            row = asset_rows[idx] if where == "asset" else box_rows[idx]
            before = {f: row.get(f) for f in changes}
            row.update(changes)
            notes.append("改 " + str(row.get("label")) + "："
                         + "、".join(f"{f} {before[f]} → {changes[f]}" for f in changes))
        elif kind in ("add_asset", "add_whitebox"):
            row = op.get("row")
            if not isinstance(row, dict) or not row:
                raise ToolError(f"补丁第 {k} 条（{kind}）没给 `row`（整行内容）。")
            label = str(row.get("label") or "").strip()
            if not label:
                raise ToolError(f"补丁第 {k} 条（{kind}）的 `row` 没写 `label`。")
            if _hits(label):
                raise ToolError(f"补丁第 {k} 条要加的行 `{label}` **已经存在** —— "
                                "加行必须用新 label；改已有的行请用 `set`。")
            (asset_rows if kind == "add_asset" else box_rows).append(dict(row))
            notes.append(f"新增{'资产' if kind == 'add_asset' else '白膜'}行：{label}")
        elif kind == "remove":
            where, idx = _find(str(op.get("label") or "").strip(), "remove")
            gone = (asset_rows if where == "asset" else box_rows).pop(idx)
            notes.append(f"删除：{gone.get('label')}"
                         f"（{'资产' if where == 'asset' else '白膜'}行）")
        else:
            raise ToolError(f"补丁第 {k} 条的 `op` 是 `{kind or '空'}` —— "
                            "只认 `set` / `add_asset` / `add_whitebox` / `remove`。")
    return asset_rows, box_rows, notes


# --- [完工-12] 工具 7：generate_plan（阶段二 · 记放置表；写）---  【模块：planning】
# ⚠ 2026-09-25：**本工具已不自检**（用户指令：「阶段二不要这个自检阶段了，跟它有关的都记得改」）——
#   11 项自检 + `build_checks()` + `_planning_measured()` + 摘要里的 checks_* 字段全部删除。
#   **下面这些 2026-09-23/24 的实测记录如实保留**（它们记的是"当时那版有自检时验过什么"），
#   但**不再描述当前行为** —— 读的时候请对照上面这条。碰撞检测与布局修正**已整段删除**（2026-09-27，不再单列阶段）。
# 实测 2026-09-23（旧版几何骨架）：改地块面宽 12→15 时自检全过、几何指纹可复现。
# 实测 2026-09-24（**check_tools 60 通过 / 0 失败**，用户喂回的原始输出）：
#   工具面仍是 9 个、本工具经 MCP 端到端调用正常；confirm_assets 两条"该拒就拒"的断言也绿。
# ⚠ 指纹计算必须内部净化 -0.0（内存对象带 -0.0、落盘是 0.0，两边哈希就不等，
#   于是对着刚写进去的数据喊"文件被改过"）—— 预检第 ⑤ 项就是这条的回归检查。
# ⚠ 2026-09-24（四）按用户新规格整段换掉（只记元素放置，路网/地块骨架删除）。
# ✅ 2026-09-24（六）实测：「空表落盘」这条分支已验（generate_plan() 不带参数 → 两张表空、
#   自检 7/0；同 payload 重跑后指纹逐位回到 6674990729…）。
# ⚠ 2026-09-24（八）收尾清单 #1/#2/#3/#4/#6 落地：新增对账三条 + cube 高度一条自检（7 → 11 项）、
#   白膜 `height_m`、清单与指纹同源检查、**落盘改为 `write_plan()`（唯一写入点 + 上一版留档
#   + 自动清除不认账的图）**、**覆盖 world.size 时自动把新值回填进阶段一的 map_size 行**
#   （回填放在两份表校验通过之后），**初始化态（两张表都空）下覆盖类自检标"不适用"**，
#   保证 `generate_plan()` 空跑依旧全绿。
# ✅ 2026-09-24（八）实测（新代码上线后经 MCP 调用观察到）：
#   · 自检 **11 项 0 失败**在真数据上跑通（含 ⑧⑨⑩ 对账、⑪ cube 高度）；
#   · **自动回填阶段一**生效：catalog/elements.json 的『地图大小』= 100×60 m，
#     且数据里 `previous_elements.scene` 也是 100×60（回填后"重读阶段一"的直接证据）；
#   · **自动留档 + 自动清图**生效：views/archive/ 里 6 份 plan_v1_* 数据 + 4 张被清走的旧图；
#   · 新写入的数据里**没有 `status` 字段**（#5 生效）；
#   · 使用清单与资产库指纹快照**同一次签字**（captured_at == delivered_at = 12:17:50.970736Z）→ #3 无告警。
# ⚠ **仍未实测**：`confirm_plan`（MCP / CLI）改完后没被调过 —— "图不认数据就拒收"那条闸
#   与"确认时留档 / 清图"等第一次真确认再补证据。

# --- 工具 7：记录平面放置表（阶段二 · 写）--------------------------------------  【模块：planning】
# 本工具不依赖 UE：纯 2D 几何校验，编辑器关着也能跑。

@mcp.tool()
async def generate_plan(
    assets: Annotated[
        list[dict] | None,
        Field(description=(
            "**已有资产**的放置表（一物一行）。每行："
            "`element_key`（要与阶段一的元素名一致）、`label`（同类多个要能区分，如「行道树 #7」）、"
            "`asset_path`、`pos`（平面中心坐标 [X, Y]，米）、`footprint_m`（占地 [宽, 深]，米）、"
            "`rot_deg`（绕 Z 逆时针，度）、`scale`（模型缩放倍率）、`note`；"
            "**选填 `scale_z`**（**Z 方向倍率**，默认 1.0）= **只拉高 / 压低、占地不动** —— "
            "要「楼别一样高、但街道布局不变」就用它（改 `scale` 是整体缩放，**占地会跟着变**）；"
            "到 UE 那层是 `RelativeScale3D = [scale, scale, scale × scale_z]`。"
            "⚠ **没有 count 字段**：摆 20 栋就写 20 行 —— 一条记录只能有一个 pos。"
        )),
    ] = None,
    whiteboxes: Annotated[
        list[dict] | None,
        Field(description=(
            "**白膜占位**的放置表（一物一行）。每行："
            "`element_key`、`label`、`shape`（cube / plane）、`pos`、`footprint_m`、"
            "`rot_deg`、`size_source`（**必须写明是「预估」**）、`note`；`asset_path` 留空。"
            "⚠ `shape=cube` 的**必须再给 `height_m`**（高度，米）—— 阶段一的白膜本来就有第三维"
            "（`size_cm` 的 Z），阶段三要靠它把 cube 实例化出来；缺了这一维，阶段三只能靠人读 note。"
            "`plane`（覆盖面）不用给高度。"
            "⚠ 只许放**阶段一里没有可用资产**的元素（没找到的 / 只有材质实例顶着的）。"
            "⚠ **必须等已有资产放完之后再填这张表** —— 先占位，才知道白膜剩哪儿、该多大。"
        )),
    ] = None,
    world: Annotated[
        dict | None,
        Field(description=(
            "覆盖世界信息（一般不用传）：`center`（平面中心坐标，默认 **[0, 0]**）、"
            "`size`（[X, Y] 米，默认取阶段一记录的「地图大小」）。"
            "不传 = 用默认。传送了会在数据里标成「调用方覆盖」。"
        )),
    ] = None,
    params: Annotated[
        dict | None,
        Field(description=(
            "**这一版是怎么定的**（自由文本，记进数据的 `params` 段，用于留痕与复现）："
            "如 `{\"依据\": \"参考图纵深/沿街长估计\", \"用户意见\": \"主楼再往左 3 m\"}`。"
            "⚠ 改它会让几何指纹变化 → 上一版确认自动作废。"
        )),
    ] = None,
    patch: Annotated[
        list[dict] | None,
        Field(description=(
            "**局部改（推荐用于「只改几行」）**：只发改动的那几行 + 一条指令，**不用重发整张表**。"
            "每项一个操作："
            "`{\"op\":\"set\",\"label\":\"住宅 #1（house1）\",\"set\":{\"pos\":[-53,-32]}}`（改已有行）/ "
            "`{\"op\":\"add_asset\",\"row\":{...整行...}}` / "
            "`{\"op\":\"add_whitebox\",\"row\":{...整行...}}` / "
            "`{\"op\":\"remove\",\"label\":\"...\"}`。"
            "⚠ 定位只认 `label`，必须**唯一命中**（找不到 / 多行同名都拒收，不猜）；"
            "字段有白名单（资产行不能写 `height_m`，白膜行不能写 `scale`/`asset_path`）。"
            "⚠ 与整表（`assets`/`whiteboxes`）**不能同时给**；第一版（还没有 plan_v1.json）只能整表给。"
            "⚠ 补丁改完**照样**过原来的整表校验 + 落盘 + 归档 —— 没有「宽松通道」。"
        )),
    ] = None,
) -> PlanResult:
    """把**平面放置表**落盘成 `views/plan_v1.json` —— 阶段二主工具（记录，不是规划）。

    三件事：
      ① 校验并规整：一物一行、每行必须有 `pos` 与 `footprint_m`（米）；
      ② 落盘 + 钉**几何指纹**（除 `confirmation` 外全部内容，`params` 也算进去）
         + **给上一版留档**（`views/archive/`）。
      ⚠ **不再自检**（2026-09-25 用户指令：「阶段二不要这个自检阶段了」）：原来那 11 项具名自检
        （两两不重叠 / 边界 / 元素归属 / 阶段一对账 / cube 高度）连同 `build_checks()` 整段删除，
        `plan_v1.json` 里不再写 `checks`。**碰撞检测与布局修正已整段删除**（2026-09-27 用户指令：不再单列阶段）。

    ⚠ **本工具不规划坐标** —— 坐标由**你**看图+按用户要求规划好再传进来。
    ⚠ **本工具不出图**（2026-09-24 用户拍板：别用定死的模板画）。`image_path` 恒为空；
      要给人看，**你自己拿 `data_path` 里的数据画** —— 每条记录就是「中心 + 占地 + 朝向」，
      画法由你定，不写死在代码里。画完记得**图内写上当前几何指纹**：`confirm_plan` 会拿它
      核对"图认不认这份数据"（收尾清单 #8）。
    ⚠ **图有两种，可能要出两张**（2026-09-30 用户定的最终口径；前缀是**代码找图的约定**，
      换成别的名字确认时会报"views/ 里没有**那一路**的图"）：
        · **顶视图**（平面图，俯视）→ `views/plan_v1_overview*.svg`；
        · **正视图或左右视图**（立面图）→ `views/plan_v1_elevation*.svg`。
      **口径：位移 / 朝向 → 顶视图；大小 / 高度（含 Z 标高 / Z 倍率）→ 正视图或左右视图**；
      两样都动 → 两张都要；**只动高度时顶视图看不出高低，那就只要立面**。
      要出哪几张：`get_plan().acceptance.change_set.required_views`；画图**直接用**
      `get_plan().drawing` 里的 `x_m` / `y_m` / `z_base_m` / `z_top_m`（**别自己推 Z**）。
      判据是**图的内容**（当前几何指纹前 10 位 + 该覆盖的每一行 `label`），**位图一律不认账**。
      2026-09-26 客户反馈实测：agent 的图画对了、只是名字不合约定，
      它读到"没有图"就**去把文件改了个名** —— 约定没写在工具描述里，只能靠撞墙学，这里补上。
    ⚠ **单位是米**（到 UE / Blender 那层才 ×100 转厘米）。
    ⚠ **顺序不能反**：先放**已有资产**（占地大小 = 阶段一实测包围盒 × `scale`，是**定的**），
      再放**白膜**（占地要等资产占完位在剩余空间里**重新估**）。
    ⚠ **一个物体一行**：摆多少写多少行。用一条记录 + 数量代表多个不同位置的物体 =
      报文里**看不见**那些没记录的物体（数据全绿、实际重叠）。
    ⚠ **谁能进白膜表**：`plan_assets` 报 missing 的、或只有材质实例顶着的元素
      （`placeholder_hint` 非空的那些）。
    ⚠ **本工具不联网、不碰 UE**（离线是刻意的）：所以"资产库**后来**变过没有"它查不出来 ——
      它只保证使用清单与资产库指纹**是同一次签字**；要查实况请调 `get_asset_list()`。
    ⚠ **覆盖 `world.size` 会顺手回填阶段一**：阶段一记的『地图大小』是目测值，装不下时你覆盖
      世界大小 —— 本工具会把新值**自动写回 `catalog/elements.json`** 的 `map_size` 行（并在
      `warnings` 里报出来），免得阶段一 / 阶段二两份使用物对不上。**这件事不许靠人记得。**
      （顺序上：两份放置表**校验通过之后**才回填 —— 负载被拒收时一个字节都不动。）
    ⚠ **落盘会顺手清除不认这份数据的图**（别的版本的图移进 `views/archive/`）：图是"用户确认过
      那一版"的凭据，数据一改旧图就没人认过 —— 留在 `views/` 只会误导人。**同样不许靠人记得。**

    **局部改：`patch`（2026-09-26 用户要求：「所有都局部改，不要全部重做」）**：
    改一行不必重发整张表 —— 用 `patch=[{"op":"set","label":"住宅 #1（house1）","set":{"pos":[...]}}]`
    只发改动的那几行（`set` / `add_asset` / `add_whitebox` / `remove` 四种操作）。
    补丁与整表走**同一条**校验 + 落盘 + 归档路径（不存在"补丁通道"），
    并在 `params` 里自动记一条"这一版打了哪几条补丁"，方便追溯。
    ⚠ 定位只认 `label` 且必须唯一命中；⚠ 与整表不能同时给；⚠ 第一版必须整表给。
    ⚠ 改完先看 `get_plan().acceptance.change_set` —— 那告诉你**图里只需画哪几行**。
    """
    planning = _planning_modules()

    # ⚠ 第 1 道闸：**上一版还没回读** → 先拒收（把文档里的软约束变硬，见 _readback_guard）
    _readback_guard()

    # ⚠ 第 2 道闸：阶段一没走完不许规划 —— 没有**签过字**的资产清单就直接拒收（堵"跳步"）。
    #    为什么：放置表里的 `asset_path` 与占地都来自那份清单；跳过阶段一等于**凭空编资产**。
    #    空表（初始化态）仍放行 —— 那是"先把结构立起来"，不涉及任何资产。
    if (assets or whiteboxes or patch) and not ASSET_LIST_PATH.exists():
        raise ToolError(
            "拒收：还没有阶段一签过字的资产清单（`catalog/asset_list.json`）。"
            "顺序是：`plan_assets()` 找资产 → `confirm_assets()`（**并让用户确认清单**）→ 再规划。"
        )

    # --- 读阶段一：世界大小从这儿来，元素的"合法性"也按它判 ---
    previous = planning.load_previous_elements()
    if not previous["rows"]:
        raise ToolError(
            "读不到阶段一的元素清单（catalog/elements.json）—— "
            "世界大小与『元素是否来自阶段一』都要靠它。请先在阶段一 confirm_elements。"
        )

    # --- 局部补丁：把"只发改动的那几行"解析成**完整的**两份表（2026-09-26 用户要求）------
    # 补丁不是另一条通道：它只是"拿当前表当底稿、把改动盖上去"，之后**照走原来的整表路径**
    # （normalize → 校验 → 落盘 → 归档 → 清图 → 进验收）。
    if patch is not None and not patch:
        raise ToolError(
            "`patch` 是**空表** —— 没有要改的东西。要么给至少一条补丁，要么整表重发。"
            "⚠ 提醒：不带任何参数（也不给 patch）调用本工具，会把放置表写成**空的初始化态**。"
        )
    patch_mode = bool(patch)
    patch_notes: list[str] = []
    cur_world: dict = {}
    patched_params: dict | None = None
    if patch_mode:
        if assets or whiteboxes:
            raise ToolError(
                "`patch` 与整表（`assets` / `whiteboxes`）**不能同时给** —— "
                "要打补丁就别重发整张表（这正是补丁的意义），要重发整表就别给 patch。"
            )
        if not planning.OUT_JSON.exists():
            raise ToolError("还没有 `views/plan_v1.json` —— 补丁只能改**已有**的放置表；"
                            "第一版请整表给（`assets` / `whiteboxes`）。")
        try:
            cur_plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ToolError(f"读不动 views/plan_v1.json（{exc}）—— 补丁要有底稿才能打。") from exc
        if not (cur_plan.get("assets") or cur_plan.get("whiteboxes")):
            raise ToolError("当前放置表是**空的**（初始化状态）—— 没有行可以打补丁；"
                            "第一版请整表给（`assets` / `whiteboxes`）。")
        try:
            assets, whiteboxes, patch_notes = _apply_plan_patch(cur_plan, patch)
        except ToolError:
            raise
        except Exception as exc:                     # noqa: BLE001 —— 补丁解析异常一律拒收
            raise ToolError(f"补丁拒收（{type(exc).__name__}: {exc}）—— 一个字节都没写。") from exc
        cur_world = dict(cur_plan.get("world") or {})
        if params is None:
            patched_params = dict(cur_plan.get("params") or {})
            patched_params["本版变更（补丁）"] = (
                f"按 label 打了 {len(patch)} 条补丁，几何只动了这几处：" + "；".join(patch_notes))

    try:
        world_full, world_changed = planning.normalize_world(world, previous["scene"], previous)
    except ValueError as exc:
        raise ToolError(f"世界信息不成立：{exc}") from exc
    if patch_mode and world is None and cur_world:
        # 补丁没动世界 → **原样沿用当前这份**。不许让 normalize_world 从阶段一"重新推导"：
        # 阶段一那个『地图大小』是目测值，推导回来等于**悄悄改了世界大小**，
        # 还会误报"世界被覆盖"（进而在阶段一那份文件里做一次不必要的回填）。
        world_full, world_changed = cur_world, []

    # --- 规整两份放置表（不合规当场拒收，一个都不落盘）---
    norm_assets: list[dict] = []
    norm_boxes: list[dict] = []
    try:
        for raw in (assets or []):
            norm_assets.append(planning.normalize_asset(raw))
        for raw in (whiteboxes or []):
            norm_boxes.append(planning.normalize_whitebox(raw))
    except ValueError as exc:
        raise ToolError(f"放置表拒收：{exc}") from exc

    # --- 世界被覆盖过 → **顺手把新大小回填进阶段一**（2026-09-24 收尾清单 #6）---
    # 为什么放在"两份表校验通过之后"：负载要是被拒收，就**一个字节都不该动** ——
    #   包括阶段一那份文件（之前放在 normalize_world 后面，等于被拒的调用也改了阶段一）。
    # 为什么由代码做：不这么做，阶段一的『地图大小』永远停在目测值上，而阶段二的世界是另一个数 ——
    #   两份使用物对不上，且**只能靠人手改**（换个 agent 就忘）。
    # 回填之后重新读一次阶段一，好让本版数据里的「阶段一记录」跟着更新（别留一个假来源）。
    sync_note = ""
    if world_changed and "world.size" in world_changed:
        try:
            sync_note = _sync_scene_map_size(
                world_full["size"],
                why=f"generate_plan 按确认过的元素把世界覆盖为 "
                    f"{world_full['size'][0]:g}×{world_full['size'][1]:g} m")
        except OSError as exc:
            sync_note = (f"⚠ 想把新世界大小回填进阶段一（catalog/elements.json）但写不进去：{exc}"
                         " —— 阶段一那份表还是旧值，两份使用物暂时对不上。")
        else:
            if sync_note:
                previous = planning.load_previous_elements()

    # --- ⚠ 允许两份表都空（2026-09-24 用户要求）---
    # 为什么允许：结构要能"先立起来、内容是空的" —— 图还没来的时候，坐标根本没有依据
    # （pos 的依据就是参考图）。所以空表不是错误，它是**初始化状态**。
    # 但它**不是可确认的规划**：`confirm_plan` 会拒收（空表），`get_plan` 报 status=initialized
    # 依然不许在没给用户看过图的情况下确认（见那边的 docstring）。
    empty = not norm_assets and not norm_boxes

    # 「阶段一记过、但使用清单里没有」这类**提示**（见 _planning_warnings）
    # ⚠ 2026-09-25：原来这里还取 `known` / `no_asset` / 实测尺寸给 11 项自检用 —— 自检删了，
    #   那三样也就没有下游了（`_planning_measured()` 整个函数一起删掉）。
    warnings = _planning_warnings(previous)

    # 清单与资产库指纹是不是同一次签字（收尾清单 #3；本工具离线，查不了"库后来变过没有"）
    warnings.extend(_planning_snapshot_check())

    plan = planning.build_plan(
        norm_assets, norm_boxes, world_full,
        params=(patched_params if patched_params is not None else (params or {})),
        previous=previous)

    # ⚠ **改几何之前先有"改什么 / 谁要的"的记录**（2026-09-26 加，把软约束变硬）——
    #   见 `_change_request_guard()`：拒收时**一个字节都没写**（本闸放在 `_write_plan()` 之前）。
    _change_request_guard(planning, plan)

    if patch_mode:
        warnings.append(
            f"**这是补丁版**（{len(patch)} 条，只改了这几处）：" + "；".join(patch_notes)
            + "。⚠ 补丁与整表走同一条校验/落盘路径 —— 它没绕过任何闸门。"
        )
    if world_changed:
        warnings.append(f"世界信息被覆盖过：{world_changed}（已在数据里标成『调用方覆盖』）。")
    if sync_note:
        warnings.append(sync_note)
    if empty:
        warnings.append(
            "这是**初始化状态**（两张表都空）：`assets` / `whiteboxes` 里没有任何 pos —— "
            "坐标的依据（参考图）还没来。填完内容之后它就是一份 draft（`status` 由 get_plan "
            "现算，文件里不再写这个字段），那时才谈得上确认。"
        )
    elif not norm_boxes:
        warnings.append(
            "白膜表是空的：如果阶段一里还有『没找到资产 / 只有材质实例顶着』的元素，"
            "它们**还没落位** —— 别当成「摆完了」。"
        )
    # 一物一行：同类多个时提醒 agent 报的是个体数，不是阶段一的目视个数
    from collections import Counter as _Counter
    _by_key = _Counter(a["element_key"] for a in norm_assets)
    if any(c > 1 for c in _by_key.values()):
        warnings.append(
            "同类资产有多条（个体级放置）："
            + "、".join(f"{k}×{c}" for k, c in sorted(_by_key.items()) if c > 1)
            + "。⚠ 阶段一的 `count` 是**画面目视估计**，跟这里的个体数不是一回事 —— "
            "数字对不上是允许的，但要请用户确认过。"
        )

    # 几何一模一样 → 沿用上次确认（重算一遍不该把确认弄丢）
    if planning.OUT_JSON.exists():
        old_conf = (json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
                    .get("confirmation", {}) or {})
        if (old_conf.get("confirmed")
                and old_conf.get("plan_hash") == plan["confirmation"]["plan_hash"]):
            plan["confirmation"].update({
                "confirmed": True,
                "confirmed_by": old_conf.get("confirmed_by", ""),
                "confirmed_at": old_conf.get("confirmed_at", ""),
                "note": "几何未变，沿用上一次确认。",
            })
        elif old_conf.get("confirmed"):
            warnings.append(
                "这一版与上一次确认的**不是同一版**：上次确认**已作废**，"
                "必须重新拿给用户/客户确认。"
            )

    planning.VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    written = _write_plan(plan)

    # ---------- 阶段二**唯一那条检查**：越界（2026-09-27 用户指令；只报红、不拦）----------
    # 用户原话：「第二阶段得改一下，只有一个检查，是否有物体超出世界边界」。
    # ⚠ 它**不改数据、不拦落盘**（plan 已经写上去了），也不往 plan 里写 `checks`
    #   （写进去会改几何指纹 → 把上一次确认无谓作废）。只把"哪几行越界、超多少"摆到眼前。
    _bounds_note = planning.bounds_check_text(plan)
    if _bounds_note:
        warnings.append(_bounds_note)

    # 落盘时**代码自动清除**了不认这份数据的图（收尾清单 #8 加码）—— 报出来，别静默
    if written.get("removed_figures"):
        warnings.append(
            "已自动清除**不认这份数据**的图（移进 views/archive/）："
            + "；".join(written["removed_figures"])
            + "。要拿去让用户确认，请**按当前数据重画一张**（图内写明几何指纹 "
            + f"{plan['confirmation']['plan_hash'][:10]}…）。"
        )
    if written.get("archived"):
        warnings.append(f"上一版 plan_v1.json 已留档：views/archive/{written['archived']}。")

    # --- 阶段二验收（2026-09-25）：落盘那一步已自动把这一版记进台账，这里只**现算**状态 ---
    # 「生图 = 阶段二结束」：数据落盘时通常图还没画，所以状态多半是 awaiting_figure ——
    # 这不是错误，是提醒"该出图了"（等 AI 画完，get_plan 会自己变成 awaiting_user）。
    acceptance = _acceptance_model(plan)
    if acceptance.state == "awaiting_figure":
        warnings.append(
            "**阶段二（数据部分）已结束，现在在【阶段二验收】的「等出图」**："
            "views/ 里还没有认这份数据的图 —— 拿 `data_path` 自己画一张，"
            f"图内写明几何指纹 {acceptance.plan_hash[:10]}…，再使用给用户"
            "（**没有图不许让用户确认**）。"
        )
        cs = acceptance.change_set if isinstance(acceptance.change_set, dict) else {}
        req = [str(x) for x in (cs.get("required_labels") or [])]
        if cs.get("local"):
            warnings.append(
                f"**这一轮是局部一轮**（基线：{cs.get('baseline')}）：新增 {len(cs.get('added') or [])} / "
                f"改动 {len(cs.get('changed') or [])} / 删除 {len(cs.get('removed') or [])} —— "
                f"图里**只要**出现这 {len(req)} 行："
                + "、".join(req[:8]) + ("…" if len(req) > 8 else "")
                + f"；另外 {cs.get('same')} 行**没动、不必重画**（用户要求：局部改就全局部）。"
                  "要画哪些行也可以跑 `python -m mcp_server.planning.plan --figure-checklist`。"
            )
        else:
            warnings.append(
                "**还没有『确认过的基线』** → 这一版按**全量**画：图里要出现**每一行**的 label"
                f"（共 {len(req)} 行），一行都不能漏。"
            )
    elif acceptance.state == "awaiting_user":
        warnings.append(
            "**阶段二已结束，现在在【阶段二验收】：图与数据一致，可以使用给用户了**"
            "（图：" + "、".join(acceptance.figures) + "）。"
            "⚠ 使用方式：把图做成**用户能点开的卡片**、附一张验收清单，然后**停下等他打字** —— "
            "不要往问题框里塞「确认吗」。"
        )

    return PlanResult(
        stage="阶段二 · 平面放置规划",
        data_path=str(planning.OUT_JSON),
        image_path="",
        summary=_summarize(plan),
        gate=_gate_model(planning.gate_check(plan)),
        acceptance=acceptance,
        changed_params=world_changed,
        next_step=_stage2_next_step(plan, acceptance, empty=empty),
        warnings=warnings,
    )


# --- [完工-11] 工具 8：get_plan（阶段二 · 读）---  【模块：planning】
# ⚠ 2026-09-25：本工具**不再报自检**（用户指令，11 项自检 + `build_checks()` 整段删除）——
#   下面的实测记录如实保留，但不再描述当前行为。
# 实测 2026-09-23（重启后经 MCP 调用）：has_plan=true、自检 14 项 0 失败、
# gate.confirmed=false（闸门正确拦着）；预检 ⑤ 绿。
# 实测 2026-09-24（**check_tools 60 通过 / 0 失败**，用户喂回的原始输出）：工具面 9 个、本工具在链上正常。
# ⚠ 2026-09-24（三、四）改过本块逻辑与文案（闸门只剩几何指纹一道；不再有图；新增 status 判断
#   `initialized` / `draft` / `confirmed`）。其中 **status=initialized 那条分支尚未实测**。
# ⚠ 2026-09-24（五）更正本工具参数说明书里残留的旧口径（原先写着「这份几何只是等宽骨架 /
#   地块按固定面宽等宽剖分」—— 那套几何已删除，本数据是放置表）。**纯注释，不影响行为**。
# ✅ 2026-09-24（八）实测（新代码上线后经 MCP 调用观察到）：
#   · 三值由本工具**现算**：文件里已经**不写 `status`**，本工具照样报 draft / initialized；
#   · 真数据上跑通 `checks_total=11 / failed=0`（比旧版多 4 项：对账三条 + cube 高度）；
#   · 新增的"图认不认这份数据"告警生效：当前那张图内写有当前指纹前 10 位 → **不报警**；
#     初始化态按设计跳过图检查（不喊）。

# --- 工具 8：取规划（阶段二 · 读）----------------------------------------------  【模块：planning】

@mcp.tool()
async def get_plan(
    include_plan: Annotated[
        bool, Field(description="true = 连完整计划数据一起带出（默认）；false = 只给闸门状态与摘要")
    ] = True,
) -> PlanStatus:
    """取当前规划几何 + 判定**它还作不作数**。**动第三阶段之前必须先调它。**
    ⚠ **闸的钥匙**（不是可选步骤）：`_readback_guard` 要它 —— 这一版 plan 写盘之后没调它，`generate_plan` / `execute_build` 一律拒收。

    回答三件事：
      ① 有没有算过规划；
      ② **确认了没有**；
      ③ 确认之后**几何改过没有**（几何指纹对不对得上 —— 唯一那道闸）。
      （④ 老版还问"几何自检过没过" —— **2026-09-25 起没有自检了**，见下。）
      （⑤ 更老那版还问"几何是否只来自参数、不读那张 PNG" —— 现在没图了，这条自动成立。）

    ⚠ **阶段二不再自检**（2026-09-25 用户指令：「阶段二不要这个自检阶段了，后面阶段再做自检」）：
      原来那 11 项具名自检连同 `build_checks()` 已整段删除，`plan_v1.json` 不再写 `checks`，
      本工具的摘要里也不再报 `checks_failed` / `failed_names`。**碰撞检测与布局修正已整段删除**（2026-09-27：不再单列阶段）。
      旧 `plan_v1.json` 里残留的 `checks` 字段会被**忽略**（不再是判定依据）。

    ⚠ **这份数据是「平面放置表」，不是几何骨架**（2026-09-24 用户重定义）：世界中心 / 大小
      + `assets[]`（已有资产，占地 = 阶段一实测包围盒 × `scale`）+ `whiteboxes[]`（白膜占位，
      占地要等资产占完位在剩余空间里重估），**一个物体一行、每行自带 `pos`**；
      旧版那套路网 / 地块 / 绿化区 / 禁放区的写死几何**已整段删除**，本数据里没有它们的字段。
    ⚠ `status` 三个取值：`initialized`（两张表都空 —— 一份坐标都没有，谈不上确认）/
      `draft`（有内容、还没确认过）/ `confirmed`（闸门放行）。
    ⚠ 客户说「宽窄不一 / 间距不匀 / 错落」时：**那不在阶段二里**（属阶段三的资产尺寸差异 +
      ±5° 抖动、阶段四的换小一号户型 / 微调边界，**这两个阶段都还没实现**），
      **照实说明，别拿「改参数」糊弄**（「错落」没有任何参数能表达）。

    ⚠ **图有两种，可能要出两张**（2026-09-30 用户定的最终口径）：**顶视图**（平面图，俯视，前缀
      `views/plan_v1_overview*.svg`）+ **正视图或左右视图**（立面图，前缀 `views/plan_v1_elevation*.svg`）。
      **口径：位移 / 朝向 → 顶视图；大小 / 高度（含 Z 标高 / Z 倍率）→ 正视图或左右视图**；
      两样都动 → 两张都要；**只动高度时顶视图看不出高低，那就只要立面**。
      要出哪几张看图：`acceptance.change_set.required_views`；**画图直接读本工具返回的 `drawing` 段**
      （每行带 `x_m` / `y_m` / `z_base_m` / `z_top_m` / `w_m` / `d_m` / `h_m`，Z 口径与阶段三**同源**）
      —— **别自己推 Z**，推错就是两套口径（图与数据不一致 = 等于没确认）。

    ⚠ **开场先查状态**：任何「走到哪了 / 下一步干什么 / 怎么搭」的回答**之前**先调本工具
      （与 get_asset_list 配套）。
    ⚠ **只读**（**不改几何、不确认、不猜**）—— 唯一例外：它会在**验收台账**里记一条
      「这一版你回读过了」，用来放行**回读闸**（见 `_readback_guard()`）。它**不碰 UE、
      不改 `plan_v1.json`**，所以不会让任何确认作废。
    ⚠ **调本工具 = 回读**：写盘之后不调它，`generate_plan` 与 `execute_build` 都会被拒收。
    ⚠ `gate.confirmed = false` → **一律不许进第三阶段**（AGENTS.md「规划图闸门」）。
    """
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        return PlanStatus(
            has_plan=False,
            next_step=(
                "还没算过规划：先 generate_plan() 算一版，再让用户/客户过目确认。"
                "**没有确认过的规划之前，不许往关卡里摆任何东西。**"
            ),
            warnings=["views/plan_v1.json 不存在。"],
        )

    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    gate = _gate_model(planning.gate_check(plan))
    summary = _summarize(plan)
    status = str(plan.get("status") or ("initialized" if not (
        plan.get("assets") or plan.get("whiteboxes")) else "draft"))
    if gate.confirmed:
        status = "confirmed"

    warnings: list[str] = []
    if status == "initialized":
        warnings.append(
            "**这是初始化状态**（`assets` / `whiteboxes` 都是空的）—— 结构立好了，"
            "但**一份坐标都没有**：坐标的依据（参考图）还没来。此时谈不上确认，也不许进第三阶段。"
        )
    if not gate.confirmed:
        warnings.append("这份规划**还没被确认** —— 不许进第三阶段。")
    if not gate.plan_hash_ok:
        warnings.append("plan_v1.json 的数据被手改过，与写进去时不一致 —— 这份规划作废。")
    # 指令表（留痕件）是不是这一版的（B 项 · 2026-09-27 · **尚未实测**）：它**不参与闸门**
    #   （`execute_build` 按 plan 自己重算，不读它），但"手上那份表是旧版"会让人看错版本 ——
    #   本次真跑就踩过：表是第 22 轮指纹、plan 已经是第 23 轮。所以顺手报一句，不拦。
    if status != "initialized":
        _orders_doc = load_json(BUILD_ORDERS_PATH) or {}
        if _orders_doc and str(_orders_doc.get("plan_hash") or "") != str(gate.plan_hash or ""):
            warnings.append(
                f"⚠ 指令表 `{BUILD_ORDERS_PATH.name}` 与当前规划**指纹不一致**（表里 "
                f"{str(_orders_doc.get('plan_hash') or '(空)')[:10]}… ≠ 当前 "
                f"{str(gate.plan_hash or '')[:10]}…）—— 它只是留痕件、**不挡搭建**，"
                "但别拿它当这一版看；要更新就调 `generate_build_orders()`。")
    # ---------- 阶段二**唯一那条检查**：越界（2026-09-27 用户指令；只报红、不拦）----------
    # 为什么放在"读状态"里也报一遍：`get_plan()` 是开场必调的那一个 —— 越界这种事
    #   不该只在写盘那一次出现（写盘之后谁还回头看那条 warning）。空表态不谈，先跳过。
    if status != "initialized":
        _bounds_note = planning.bounds_check_text(plan)
        if _bounds_note:
            warnings.append(_bounds_note)
    # ⚠ 2026-09-25：这里原来会报「自检有 N 项没过」—— 阶段二自检整段删除（用户指令），
    #   碰撞检测与布局修正**已整段删除**（2026-09-27：不再单列阶段）。旧 plan_v1.json 里残留的 `checks` 字段不再被读。
    # 图与数据一致吗（收尾清单 #8）—— 空规划阶段不谈图，不喊
    if status != "initialized":
        try:
            fig = planning.figure_check(plan)
        except Exception as exc:        # noqa: BLE001 —— 图检查失败不该把读工具带崩
            warnings.append(f"图检查没跑成（{type(exc).__name__}: {exc}）。")
        else:
            if not fig["ok"]:
                warnings.append(
                    "⚠ 图不认这份数据：" + fig["note"]
                    + "（AGENTS：图与数据不一致 = 等于没确认；确认时会被拒。）"
                )

    # --- 「改完必须回读」：**看过就算回读**（这是那道闸唯一的清除途径，见 _readback_guard）------
    # ⚠ 本工具仍然**不碰 UE、不改 plan_v1.json**；它只往验收台账记一条「你回读过了」。
    cleared: dict = {}
    try:
        cleared = planning.mark_readback()
    except Exception as exc:        # noqa: BLE001 —— 台账写不动不该把读工具带崩
        warnings.append(
            f"⚠ 没能在台账里记下「这一次回读」（{type(exc).__name__}: {exc}）—— "
            "回读闸可能还会拦你一次。")
    if cleared:
        cs0 = cleared.get("change_set") if isinstance(cleared.get("change_set"), dict) else {}
        parts: list[str] = []
        for key, word in (("added", "新增"), ("changed", "改动"), ("removed", "删除")):
            vals = cs0.get(key) or []
            if vals:
                parts.append(f"{word} {len(vals)}：" + "、".join(str(x) for x in vals[:6]))
        warnings.append(
            "✅ 已记下「你回读过这一版」（回读闸放行；指纹 "
            + str(cleared.get("plan_hash") or "")[:10] + "…）。"
            + ("该版变化：" + "；".join(parts) + "。" if parts else "")
            + " ⚠ 请逐行核对：**你预期的改动与上面这几行一致吗？**不一致就先查，别接着往下做。"
        )

    # --- 阶段二验收（2026-09-25）：状态**现算**，不读台账里那个旧快照 ---------------
    # 为什么：图常常是"落盘之后"才画好的，读快照就会拿着「等出图」的旧字样回答「走到哪了」。
    acceptance = _acceptance_model(plan)
    if acceptance.state == "changes_requested":
        warnings.append(
            "**用户在【阶段二验收】提了要改的地方**（还没落到数据里）："
            + "；".join(acceptance.pending_changes)
            + "。改完 `pos` / `footprint_m` / `scale` 再调 `generate_plan`"
            "（几何一变自动进新一轮、上一版自动留档）。"
        )
    elif acceptance.state == "awaiting_user":
        warnings.append(
            "**阶段二已结束，现在在【阶段二验收】：图与数据一致，该使用给用户了**"
            "（图：" + "、".join(acceptance.figures) + "）。"
            "使用后**停下等他打字** —— 他说改哪儿就调 `request_plan_change`，他说行就 `confirm_plan`。"
        )
    elif acceptance.state == "awaiting_figure":
        cs = acceptance.change_set if isinstance(acceptance.change_set, dict) else {}
        if cs.get("note"):
            warnings.append("⚠ **还在等出图** —— " + str(cs["note"]))
    elif acceptance.state == "accepted":
        warnings.append(
            "**阶段二验收已通过**（台账 `views/acceptance.json` 有留痕）—— 可以进第三阶段了。"
            "⚠ 第三阶段**第一步是只读检查**：先调 `check_build_target()` 问清搭哪儿，"
            "**拿到用户答复之前不许往关卡里放任何东西**；"
            "第二步是批量搭建：`generate_build_orders()`（翻指令表）→ `execute_build()`"
            "（整批校验 → 清 `UEMCP/` 旧 Actor → 一次性全落 → 读回对账），想先看就传 `dry_run=true`。"
            "⚠ 落关卡前会先过**回读闸**：这一版若还没 `get_plan()` 过，`execute_build` 会拒收 —— "
            "先看一眼再搭。"
        )

    # --- 画图几何（2026-09-30 加）：一份**可直接画**的逐行数据（含 Z = 底 / 顶绝对标高）--------
    # 为什么放在这里：出图是阶段二验收的前置，而 `get_plan()` 是"开口之前必调"的那一个 ——
    #   把 Z 口径直接给出来，画图的人就不用自己推（推错就是两套口径，而图是用户唯一的判断依据）。
    # ⚠ 纯离线计算（**不碰 UE**）；`include_plan=false` 时不带（那是"只要状态"的用法）。
    drawing: dict | None = None
    if include_plan and status != "initialized":
        try:
            drawing = _drawing_geometry(plan)
        except Exception as exc:        # noqa: BLE001 —— 画图数据算不动不该把读工具带崩
            warnings.append(
                f"⚠ 画图几何（`drawing`）这次没算出来（{type(exc).__name__}: {exc}）—— "
                "**别自己推 Z**：先查为什么算不出来（`z_base` / `z_top` 就来自那段）。")
    if drawing is not None and drawing.get("required_views"):
        _need = [str(v) for v in (drawing.get("required_views") or [])]
        _need_cn = "、".join(
            ("顶视图（平面图，存成 `views/plan_v1_overview*.svg`）" if v == "top"
             else "正视图或左右视图（立面图，存成 `views/plan_v1_elevation*.svg`）")
            for v in _need)
        warnings.append(
            "⚠ **这一版要出的图**：" + _need_cn
            + "。口径（用户定的）：**位移 / 朝向 → 顶视图；大小 / 高度 → 正视图或左右视图**。依据："
            + str(drawing.get("views_why") or "")
            + " ⚠ 要几张就给几张**卡片**使用给用户，然后停下等他打字。"
        )

    ok = (status != "initialized") and gate.confirmed and gate.plan_hash_ok
    return PlanStatus(
        has_plan=True,
        status=status,
        data_path=str(planning.OUT_JSON),
        image_path="",
        summary=summary,
        gate=gate,
        acceptance=acceptance,
        plan=plan if include_plan else None,
        drawing=drawing,
        next_step=_stage2_next_step(plan, acceptance, empty=(status == "initialized")),
        warnings=warnings,
    )


# --- 工具 10：request_plan_change（阶段二验收 · 记「用户要改什么」）-------------  【模块：planning】
# 为什么要有它（2026-09-25 用户要求新增）：
#   原来的工具面里**只有"确认"这一条路** —— 用户在验收阶段说「第 3 栋往左 5 m」，
#   这句话只能留在对话里：会话一长就翻不到，换个 agent 就彻底丢了。
#   本工具把它**落进验收台账**（views/acceptance.json），状态变 `changes_requested`，
#   于是"用户说了什么"和"数据变成什么"两件事各自留痕、事后对得上账。
# ⚠ 它**不改几何、不进指纹**：用户提的是**要求**，不是坐标 —— 改坐标是 generate_plan 的事。

@mcp.tool()
async def request_plan_change(
    items: Annotated[
        list[str],
        Field(description=(
            "用户看图后提出要改的地方，**一条一项、尽量用他的原话**"
            "（如「左侧第 3 栋往 -X 挪 5 m」「树再密一点」「车只要 5 辆」）。至少一条。"
        )),
    ],
    by: Annotated[str, Field(description="谁提的（用户 / 客户的称呼，必填）")] = "用户",
    reason: Annotated[
        str, Field(description="为什么改 / 上下文（选填；能记原话就记原话）")
    ] = "",
) -> PlanResult:
    """记下「用户在 **阶段二验收** 提出要改的地方」—— 验收阶段的入口之一。

    用在哪：`get_plan().acceptance.state == "awaiting_user"` 时，你把图**使用成卡片**给用户
    然后停下；他看完说「这里要改」→ 调本工具把**他要改的点**记下来（别只留在对话里）。

    记完会发生什么：
      · 台账 `views/acceptance.json` 多一条 `change_requested`（带**当时那一版的几何指纹**）；
      · `acceptance.state` → `changes_requested`，`pending_changes` 就是这些条目；
      · 你按条目改 `pos` / `footprint_m` / `scale` → 重调 `generate_plan`
        （**几何指纹一变 = 自动进新一轮**，上一版自动留档）→ **重画图**（图内写新指纹）
        → 再把图使用给用户。**循环到他说无误为止**，那时才 `confirm_plan`。

    ⚠ 本工具**不改几何**：它只记「要求」；改坐标是 `generate_plan` 的事。
    ⚠ 不许拿它当"确认"用 —— 它只会让状态**更远离**验收通过。
    ⚠ **它是「改动窗口」的钥匙（2026-09-26 起，代码硬拦）**：一份规划**被确认过之后**，
      要再改内容**必须先走本工具** —— 否则 `generate_plan` 直接拒收（一个字节都不写）。
      窗口一直开到下一次 `confirm_plan()`，所以一轮里可以连着改好几次。
      ⚠ **自查修正也要记**（`by="agent 自查"`，`items=["自查：…"]`）——
      要的不是"等用户开口"，是留下「**改什么 / 谁要的**」这层对应关系，
      好让台账事后答得了「用户说了什么 ↔ 数据变成什么」。
      （客户 agent 使用后自评原话：「用户提改动要调 request_plan_change —— **一次都没调过**，
      你每次说要改，我直接 patch，没走变更台账」。）
    """
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划数据：先 generate_plan() 记一版、出图给用户看过，再记改动。")

    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    if not (plan.get("assets") or plan.get("whiteboxes")):
        raise ToolError(
            "拒绝记录：这是**初始化状态**（`assets` / `whiteboxes` 都空）—— "
            "还没有任何坐标可看，用户也没法提改动。先按参考图规划 `pos` 并落盘。"
        )
    clean = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not clean:
        raise ToolError("至少要给一条**具体**的改动要求（用户原话最好）；空条目等于没记。")

    planning.record_change_request(plan, clean, by=by, reason=reason)
    acceptance = _acceptance_model(plan)
    return PlanResult(
        stage="阶段二 · 平面放置规划",
        data_path=str(planning.OUT_JSON),
        image_path="",
        summary=_summarize(plan),
        gate=_gate_model(planning.gate_check(plan)),
        acceptance=acceptance,
        changed_params=[],
        next_step=_stage2_next_step(plan, acceptance),
        warnings=[
            f"已把 {len(clean)} 条改动记进验收台账（`views/acceptance.json`）—— "
            "**几何一个字节没动**（用户提的是要求，不是坐标）。",
            "下一步：按这些条目改 `pos` / `footprint_m` / `scale` → 重调 `generate_plan` "
            "→ **重画图（图内写新指纹）** → 再把图使用给用户，循环到他说无误为止。",
        ],
    )


# --- [完工-13] 工具 9：confirm_plan（阶段二 · 闸门）---  【模块：planning】
# 实测 2026-09-23T13:56:45Z（经 MCP 调用，用户本人确认）：
# gate.confirmed=true、confirmed_by="用户本人"、指纹一致、
# next_step 写出"阶段二闭环 —— 可以进第三阶段"；
# 并已核实写进了磁盘（plan_v1.json 的 confirmation 段）。
# 这一条是用真实确认验的，不是伪造的 —— 闸门的意义就在于没人能绕过它。
# 实测 2026-09-24（**check_tools 60 通过 / 0 失败**）：工具面 9 个、本工具在链上正常。
# ✅ 2026-09-24（六）实测：新增的"**空规划拒收**"一条已验 ——
#   空结构（generate_plan() 不带参数 → status=initialized、两张表都空）落盘后，
#   调本工具 confirm_plan("验收探针") → 被 ToolError 拒（原文：「拒绝确认：这是**初始化状态**
#   （`assets` / `whiteboxes` 都空、一份坐标都没有）…」）；
#   拒收后 plan_v1.json **未被写**（get_plan 复查 plan_hash 未变、confirmed 仍 false），
#   且用**同一份 payload** 重跑 generate_plan 后几何指纹逐位回到探针前的值（6674990729…）。
# ⚠ 2026-09-24（八）本块改了：新增**第三条闸**（图不认这份数据 → 拒收）+ 确认落盘改走
#   `write_plan()`（确认前那一版自动留档、不认账的图自动清走）+ 空规划判据简化为"两张表都空"。
#   **尚未实测** —— 改完之后**没有确认过任何一版**，第一次真确认（用户看图画押）时补证据。

# --- 工具 9：确认规划（阶段二 · 闸门）------------------------------------------  【模块：planning】

@mcp.tool()
async def confirm_plan(
    confirmed_by: Annotated[str, Field(description="谁确认的（用户/客户的称呼，必填）")],
    user_quote: Annotated[str, Field(
        description=(
            "**用户点头的原话（必填）**：他看过图之后回你的那句，如「行」「可以，就这样」「没问题」。"
            "⚠ 这是**唯一**能证明「人看过了」的东西 —— 图是你画的、确认是你调的，"
            "光有 `confirmed_by` 证明不了有人点过头。**不许自己编**：拿不出原话，就说明你还没问过他。"
        ))],
) -> PlanResult:
    """把「这份规划被确认了」写回 `plan_v1.json`：**谁 / 何时 / 哪一版 / 用户的哪句话**。

    闸门不过就**拒绝确认**（这就是闸门的意义）：
      ① **空规划不许确认**（两张表都空：一份坐标都没有，确认它毫无意义）；
      ② 数据指纹对不上 → `plan_v1.json` 被手改过，与写进去时不一致；
      ③ **图不认这份数据**（`views/` 里没有图 / 图里没有当前几何指纹 / 图没覆盖该覆盖的行）——
         AGENTS：图与数据不一致 = 等于没确认。图由 AI 手绘，**画完把当前指纹写进图里**；
         ⚠ **要出哪几张图由 `change_set.required_views` 定**（2026-09-30 加，用户最终口径）：
         **位移 / 朝向 → 顶视图**（`views/plan_v1_overview*.svg`）；**大小 / 高度 → 正视图或
         左右视图**（`views/plan_v1_elevation*.svg`）；两样都动 → 两张都要；
         **只动高度时顶视图看不出高低，那就只要立面**；
         存成别的名字会被读成"views/ 里没有**那一路**的图" ——
         **改名可以让它被找到，但内容不对照样过不了**；
      ④ **证据链**（2026-09-26 加，堵最大的那个洞）：必须给 `user_quote`（用户原话），
         且那张图**出炉至少 `MIN_CONFIRM_DELAY_S` 秒** —— 画完就自己确认 = 没人看过。

    **局部确认（2026-09-26 用户要求：「用户想局部改，所有都局部改，不要全部重做」）**：
    这一版相对**上一次确认过的基线**（验收台账里的 `accepted_rows`）只要改了 N 行，
    图里就**只需**出现那 N 行的 label —— 没动的行不必重画，整张图也不用重做。
    覆盖是**传递**的：第一版全量画过，之后每轮只画改动过的行，并起来就是每一行。
    ⚠ 第一次搭（还没有基线）时仍是**全量**：图里要出现每一行。
    ⚠ 确认成功时会把**这一版的逐行几何签名**记进台账 —— 它就是下一轮算变更集的基线。

    ⚠ 确认是**人的动作**：只有用户/客户**看过图并点头**之后，才准调它。
      你自己看着"差不多"就确认 = 把闸门作废（实测被这么绕过一次，客户当场质问"图都不先让我确认就摆"）。
    ⚠ **必须先给用户看图** —— 他读不懂坐标，图是他唯一能判断的依据。
    ⚠ 确认之后坐标一改，指纹就对不上，本次确认**自动作废**。
    ⚠ 落盘走 `planning.write_plan()`：确认前的那一版会**自动留档**到 `views/archive/`；
      **不认这份数据的图会被代码自动清除**（也移进 `views/archive/`）—— 图这件事不许靠人记得。
    """
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        raise ToolError("还没有规划数据：先 generate_plan() 记一版，让用户过目之后再确认。")

    quote = str(user_quote or "").strip()
    if len(quote) < 1:
        raise ToolError(
            "拒绝确认：**没给 `user_quote`（用户点头的原话）**。\n"
            "· 图是你画的、确认是你调的 —— 光写 `confirmed_by=\"用户\"` 证明不了有人看过。\n"
            "· 正确顺序：**把图使用成用户能点开的卡片 → 停下等他打字 → 把他的原话填进 `user_quote`**。\n"
            "· 他还没回话，就先别确认（也不许接着搭建）。"
        )

    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    if not (plan.get("assets") or plan.get("whiteboxes")):
        raise ToolError(
            "拒绝确认：这是**初始化状态**（`assets` / `whiteboxes` 都空、一份坐标都没有）。"
            "先把放置表填上（先已有资产、后白膜），出图给用户看过，再确认。"
        )
    gate = planning.gate_check(plan)
    if not gate["plan_hash_ok"]:
        raise ToolError("拒绝确认：plan_v1.json 的数据与写进去时不一致（被改过）。")

    # --- ⚠ 图与数据必须一致（2026-09-24 收尾清单 #8；2026-09-25 加严；2026-09-26 改为按变更集）---
    # 判据在 planning.figure_check()：views/ 里要有图，且图认这份数据 ——
    #   **svg**：正文里要有当前几何指纹前 10 位，**且覆盖"这一轮该覆盖的行"**
    #   （有基线 = 本次改动的那几行；第一次搭 = 全部行）；**位图**一律不认账。
    fig = planning.figure_check(plan)
    if not fig["ok"]:
        _miss = [str(x) for x in (fig.get("missing_views") or [])]
        _views_msg = ""
        if _miss:
            _views_msg = (
                "\n· **缺的视图**：" + "、".join(str(x) for x in (fig.get("view_labels") or _miss))
                + " —— 平面图存 `views/plan_v1_overview*.svg`、"
                  "**立面图存 `views/plan_v1_elevation*.svg`**；"
                  "**每一张**图里都要写当前几何指纹前 10 位 + 该覆盖的那几行的 label"
                  "（位图一律不认账）。改高度的那一版**只给俯视图是过不了的** —— "
                  "俯视图看不出高低。"
            )
        if fig.get("local"):
            raise ToolError(
                "拒绝确认：图不认这份数据 —— " + fig["note"] + _views_msg
                + "\n· 这是**局部一轮**：只画上面那几行（共 "
                  + str(len(fig.get("required_labels") or [])) + " 行）"
                  "（在**每一张**要出的图里）—— 没动的行不必重画。"
                  "要画哪些行 / 要出哪几张图可跑："
                  "python -m mcp_server.planning.plan --figure-checklist"
            )
        raise ToolError(
            "拒绝确认：图不认这份数据 —— " + fig["note"] + _views_msg
            + "\n· 请拿 views/plan_v1.json 重画（**图内写明当前几何指纹，"
              "且每一行的 label 都要出现**，每张图都要），再确认。"
              "要画哪些行 / 要出哪几张图可跑："
              "python -m mcp_server.planning.plan --figure-checklist"
        )

    # --- ⚠ 证据链：图出炉多久了？（防"画完立刻自己确认" = 没人看过）------------------
    # ⚠ 2026-09-30 改：**认账的图可能不止一张**（平面图 + 立面图）—— 原先只拿第一张的图龄，
    #   那就能用"早就画好的那张平面图"把"刚刚才画完的立面图"蒙过去。
    #   现在取**最新画好的那张**（年龄最小）当判据：要确认，**每一张**都得等够时间。
    fig_names = [str(x) for x in (planning.accepted_figures(plan) or [])]
    now_ts = datetime.now(timezone.utc).timestamp()
    fig_ages: list[tuple[str, float]] = []
    for _n in fig_names:
        try:
            fig_ages.append((_n, now_ts - (planning.VIEWS_DIR / _n).stat().st_mtime))
        except OSError:
            continue
    fig_name = ""
    fig_age: float | None = None
    if fig_ages:
        fig_name, fig_age = min(fig_ages, key=lambda x: x[1])
    if fig_age is not None and fig_age < MIN_CONFIRM_DELAY_S:
        raise ToolError(
            f"拒绝确认：认账的那张图（`{fig_name}`）是 **{fig_age:.0f} 秒前**才画好的"
            + (f"（认账的图共 {len(fig_ages)} 张，这里挑的是**最新画好的**那一张）"
               if len(fig_ages) > 1 else "")
            + f" —— 用户不可能已经看过（这道下限是 {MIN_CONFIRM_DELAY_S:.0f} 秒）。\n"
            "正确顺序：**把图使用给用户（做成他能点开的卡片）→ 停下等他打字 → 带着他的原话回来**。\n"
            "（若你确实刚把图给过他、他也回话了，那就**等够时间再调本工具**。）"
        )

    plan = planning.mark_confirmed(plan, confirmed_by, quote)
    written = planning.write_plan(plan)
    notes: list[str] = [
        f"证据链已留痕：确认人 `{confirmed_by}` ｜ 用户原话「{quote}」"
        + (f" ｜ 图 `{fig_name}`（出炉 {fig_age:.0f} 秒）" if fig_age is not None else "")
        + "。"]
    # --- 阶段二验收台账：记「这一轮通过了」（2026-09-25 用户要求）---------------
    # 为什么 plan_v1.json 的 confirmation 之外还要记一份：confirmation 只说"确认了"，
    #   回答不了「这是第几轮 / 当时用户提过什么 / 验的是哪一版几何」—— 那些在台账里。
    try:
        planning.record_acceptance(plan, confirmed_by, user_quote=quote,
                                   figure=fig_name, figure_age_s=fig_age)
    except OSError as exc:
        notes.append(f"⚠ 验收台账没记上（{exc}）—— 规划数据已落盘，台账可以重记。")
    if written.get("archived"):
        notes.append(f"确认前的那一版已留档：views/archive/{written['archived']}。")
    if written.get("removed_figures"):
        notes.append("已清除不认这份数据的图（移进 views/archive/）："
                     + "；".join(written["removed_figures"]) + "。")
    acceptance = _acceptance_model(plan)
    return PlanResult(
        stage="阶段二 · 平面放置规划",
        data_path=str(planning.OUT_JSON),
        image_path="",
        summary=_summarize(plan),
        gate=_gate_model(planning.gate_check(plan)),
        acceptance=acceptance,
        changed_params=[],
        next_step=_stage2_next_step(plan, acceptance),
        warnings=notes,
    )


# --- [完工-17] 阶段六 · 环境搭建（`setup_environment`）· ⚠ 相机预览已划归阶段七 --------------  【模块：environment】
# 2026-09-29 用户指令：「阶段6改一下，叫环境搭建和相机预，不只有灯光，其他环境因素全部要做」。
# ⚠ 同一天用户**收回**了"相机预览"那半句：本阶段定名「环境搭建」，出图归**阶段七**
#   （`capture_preview()` 代码保留、已在下面那个工具区块里改成"阶段七"）。
# 本区块的机制**全是当天直连官方链路探出来的原始返回值**，逐条附在下面 ——
# 免得以后有人把这些当"大概是吧"随手改掉。
#
# 实测证据（2026-09-29，全部是原文，不是推断）：
#   ① 按**类**找得到：`find_actors(actor_type=…)` 对 5 个环境类**各回 1 个**
#      （DirectionalLight_0 / SkyAtmosphere_0 / SkyLight_0 / ExponentialHeightFog_0 /
#        VolumetricCloud_0）；`PostProcessVolume` 回 **0 个** —— 关卡里本来就没有。
#   ② 参数在**组件**上：`list_properties(DirectionalLight_0)` 只回 Actor 级字段 + 一个
#      `directionalLightComponent` 引用；而组件**对象名**是 `LightComponent0`（雾：`HeightFogComponent0`）
#      —— 属性名 ≠ 对象名，所以只能"枚举组件 + 按对象名前缀挑"。
#   ③ 属性写得进去：`set_properties(组件, {"intensity": 5.5, "lightColor": {…}})` 读回
#      `5.5` / `(0.902, 0.800, 0.702)`（= 0.9/0.8/0.7 的 **8-bit 量化**，颜色对不齐 1% 是正常的）。
#   ④ ⚠ **结构体是整体替换、不是合并**：只写 `atmosphereSunDiskColorScale={"r":0.5}`，
#      读回**连合法 JSON 都不是**（没给的 g/b/a 被打坏）→ 所以 dict 值一律"先读现值、合并、整份写"。
#   ⑤ ⚠ **`set_actor_transform` 也把没给的字段打到默认值**：只传 `rotation`，太阳的
#      **location 从 (0,0,400) 变成 (0,0,0)**（官方 schema 写的是"未设置=保持不变"，**实测不是**）
#      → 所以改朝向时**一律整份传 location + rotation + scale**。
#   ⑥ 后处理卷建得出来（`add_to_scene_from_class`）、`bUnbound` 能写、嵌套 `settings` 能写；
#      `FPostProcessSettings` 实测 **463 个字段**（`bloomIntensity` / `autoExposureMinBrightness` /
#      `whiteTemp` …，覆盖开关是 `bOverride_<首字母大写>`）。⚠ 结合 ④：`settings` 也必须**整份写**。
#   ⑦ `CaptureViewport` 的 `annotations` **必须整个给全**（少给任一子字段 → RuntimeError
#      "needs a default value"）；`captureTransform` **精确生效且不动用户的视口相机**
#      （出图前后 `GetCameraTransform` 一字不变）；一张干净 PNG 的 base64 = **695,632 字符**。
#
# ★ 2026-09-29 **真跑实测**（用户重启后的第一次真配，原始返回值见报告）：
#   · `setup_environment()`（空 preset → 自动取阶段一记的 `sunset`）→ **applied 6 / already 0 /
#     failed 0**；新建 `PostProcessVolume_1`（落在 `UEMCP_env`，官方 `get_actors_in_folder`
#     复核 = 只回它一个）；`official_calls = 37` 逐项对平；台账 `views/environment_state_v1.json`
#     6 个因素齐全（`props` / `settings` / 太阳整份 `transform`）→ **可回滚**。
#   · `capture_preview(annotations=true)` → 3/3 成，每张 `labeled_actors = 12`（顶到 `maxLabels`）；
#     `capture_preview()`（干净图）→ 3/3 成，1133×693、0.87 / 0.94 / 1.07 MB。
#   · 这一步**抓出并修掉**了 `_env_same` 的容差 bug（`max(1.0, …)` 让所有 <1 的量拿到 0.01
#     绝对容差 → `mieScatteringScale` 0.003996→0.006 被**静默跳过**；证据 = 那次演练报告里
#     `sky_atmosphere.wrote` 少了它）。
#   · 同一次真跑还暴露两处"回灌过量"：报文与台账把**整份 460 字段**的 `settings` 抄了一遍
#     （一条报文 ≈8k token、台账 941 行）→ 已改成"写整份、**只报改动**"；默认俯视机位太远
#     （世界只占画面一半）→ 0.55 收到 0.35。
#
# ★ 修完**复验**（用户第二次重启后，全部是原始返回值）：
#   ① **幂等**：`setup_environment(factors=[sun, sky_atmosphere, sky_light, fog, cloud])` →
#      **applied 0 / already 5 / failed 0**、`official_calls 17`（1 取关卡 + 5 按类找 + 11 读）
#      —— 重跑**一个属性都没写**。
#   ② **"只报改动"生效**：把配置里 `sunset` 的 `bloomIntensity` 由 `0.9` 改成 `0.95`，
#      再 `setup_environment(factors=[post_process])` → `wrote.settings = {"bloomIntensity": 0.95}`
#      **只有这一条**（不再是那份 460 字段）、`property_count 1`、`official_calls 7` 对平；
#      把配置改回 `0.9` 再跑一次 → 写回并读回 `0.9` ✓ 回到预设状态。
#   ③ ②顺带证明**配置是热重载的**：这两次调用之间用户**没有重启**，改文件就生效。
#   ④ 俯视机位收紧后重拍 → 3/3 成、相机 `(-4200,-2800,5047.8)`（离中心 69.3 m，原 112 m）、
#      图里世界占满画面（原先只占一半）。
#   · 遗留（如实记）：台账 `history[0]` 里那次真跑写在修复**之前**，仍是整份回灌 ——
#     它只作**留痕**、**不参与回滚**（回滚读的是 `factors`），累计 20 条后会自然滚掉。
#   · ⚠ **尚未实测**：同一轮补的 `overrides` 第二种写法（`{"post_process": {"settings": {...}}}`）——
#     要验的是"确实覆盖到 `settings` 字段、且报文里只报改动的那几条"。等下一轮真跑补证据。
#
# ★★ 2026-09-29 **第三批（用户"统统授权"）—— 全部尚未实测，第一次真跑请先 `dry_run=true`**：
#   ① **因素表搬进配置**：新增 `ENV_FACTORS_DEFAULT`（对象表：cn / class / component / spawn /
#      folder / material_prop），`_env_config()` 返回 `(因素表, 预设表, 提示)` 且**按键覆盖** ——
#      以后加自己的环境因素（`LocalFogVolume` / 自定义蓝图类）**改配置即可，不用改代码**。
#      `ENV_COMPONENT_HINT` 已删除（并进因素表的 `component` 字段）。
#   ② **`actors` 参数**：点名改哪一个 Actor（label 片段或完整引用）；命中多个**跳过并列出来**，
#      不猜。`_env_locate()` 多了一个 `picks`。
#   ③ **`material` 段**：`_env_apply_material()` —— 把我们自己的 MI（不在就 `create`）指到组件的
#      `material` 上，再用 `MaterialInstanceTools.set_scalar_parameter` /
#      `set_vector_parameter` 调参数（云量 `Cloud_GlobalCoverage` / 云密度 `Cloud_GlobalDensity`）。
#      与别处同一条纪律：先读现值 → 幂等 → 写 → 读回核对；`dry` 不写。
#      ⚠ 回滚只把 `material` **指回原样**、**不回写材质参数**（原值属于引擎 MI，不能碰）——
#        台账里单独记 `material: {prop, was, params}`。
#   ④ 后处理卷的新建从"写死 post_process"改成**看因素表的 `spawn`**（`_env_spawn_actor`），
#      所以以后任何因素想"没有就建"只要在配置里写 `spawn: true`。
#   ⚠ **以上 ①②③④ 一行都还没跑过** —— 只做了静态核对（括号/引号配平、`config/environments.json`
#     用 PowerShell `ConvertFrom-Json` 验过能解析、18 个工具仍在）。
#
#   · 2026-09-29 **复验**（用户重启后；前三条**已实测**，原始返回值见报文）：
#     ① **表驱动的老路没坏**：6 个因素全 `already`、**0 写入**、`official_calls 20`
#        （1 取关卡 + 6 按类找 + 13 读）逐项对平；
#     ② **配置里新加的因素被认出**：`local_fog`（局部体积雾）→ `status: dry`（不是 failed）+
#        "真做时会**新建**一个并放进 `UEMCP_env/`"、`official_calls 2`；
#     ③ **`actors` 点名生效**：`fog` 按名字片段 `ExponentialHeightFog` 命中
#        `ExponentialHeightFog_0`、`already`、`official_calls 4`。
#   · ⚠ **仍未实测**：`material` 段整条（自建 MI + 材质参数）。而且自查时补掉一个洞：
#     原来"MI 不存在 + `create: true`"那条分支**没被 `dry` 拦住** —— 一次演练就会真去 `create`
#     （已改成：演练只报"真做时会新建"、**不建资产、不调参数**）。
#
#   · 2026-09-29 **material 段真跑**（用户点头后）—— 抓到**两个互补的"路径形状"bug**（已修，见 `mi_ref`）：
#     ① **走 create 那一次**：`create` 回来的**本来就是对象路径**，我又套了一次 `to_object_path()`
#        → 后缀重复成 `X.X.X`，UE 读回 `包.对象:对象.对象`，于是工具**误报"读回不是它"**；
#        而那次三个材质参数**其实全调成功了**（`Cloud_GlobalCoverage` -0.2 → 0.55 等，原始读回为证）。
#        ⚠ 教训：**假失败比没验证更坏** —— 会让人不敢用这条路。
#     ② **第二次（MI 已存在、路径来自配置）**：`asset` 是**包路径** → `MaterialInstanceTools`
#        直接报原文 `Parameter error: … is not a valid object path for property 'instance'`，
#        参数**一个都没调**。
#     → 修法：`_env_object_path()`（最后一段已有 `.` 就不重复拼）+ `_env_same_object()`
#       （容得下 `包.对象` 与 `包.对象:对象.对象` 两种写法）+ **统一用 `mi_ref` 调参数**、
#       `exists` 仍用包路径（两种形状必须分开用，注释写在 `_env_apply_material` 里）。
#   · 同一次真跑**实测到一条参数语义**：`Cloud_GlobalCoverage` 引擎默认 **-0.2**（有云），
#     我给 **0.55** → **云整个没了**（两版出图对比可见）→ 这个参数**越大云越少**。
#     预设已改成 `-0.35 / 0.012`（**待下一轮真跑看图确认**，我按参考图猜的方向）。
#   · `local_fog`（局部体积雾）**真跑全绿**：新建 `LocalFogVolume_0`（进 `UEMCP_env/`）、
#     6 个参数里 4 个写入并读回（`heightFogFalloff` **1000 → 0.2**、`heightFogExtinction` 1 → 0.02、
#     `radialFogExtinction` 1 → 0.01、`fogAlbedo` 偏冷白），另 2 个本来就是对的值 → **幂等跳过**；
#     `applied 1 / failed 0`、`official_calls 8` 逐项对平。
#     ⚠ 当时的限制（**已修，见紧接的第四批**）：`transform` 只认 pitch/yaw/roll → 它落在世界原点、
#       默认大小。第四批起 `transform` 支持 `location_m` / `scale`，原因与修法见下。
#
#   · 2026-09-29 **第四批（用户"你继续完善，其他先别动"）—— 把 `transform` 从"只认朝向"扩成整份变换**：
#     `_env_apply_factor` 现在认三种输入 —— 老写法 `{pitch,yaw,roll}`（5 档预设继续可用）、
#     新写法 `{location_m:[x,y,z](米), rotation:{...}, scale:[x,y,z]}`、以及回滚用的
#     `{location:{x,y,z}(厘米), rotation, scale}`（台账里存的就是 UE 原样厘米）。
#     · 单位边界**分开命名**：配置给人写 `location_m`（米，内部 ×100）；台账/回滚是厘米 —— 差 100 倍
#       是本工程踩过的老账，不许混。
#     · 写入仍是**整份**（location + rotation + scale）：实测只传 rotation 会把 location 打到 0。
#     · 读回核对扩到三项（`_transform.location/rotation/scale` 逐字段比）。
#     · `overrides` 多了**第三个保留键** `transform`（"临时把它挪到那儿看看"不该逼人改配置文件）。
#     · 台账/回滚：`_env_ledger_save` 记 `transform` 整份；`restore=true` 按同一份写回。
#     ⚠ **这一批同样尚未真跑** —— 第一次请先用 `dry_run=true` 看"会写哪几项"。
#
#   · 2026-09-29 **第四批真跑**（用户重启后）—— `transform` 整份变换**成了**，同时再抓两个 bug：
#     ① `local_fog` 真跑：`location.z 0 → 300`（3 米，单位没混）、`scale 1,1,1 → 40,20,6`、
#        `heightFogExtinction 0.02 → 0.03`、`radialFogExtinction 0.01 → 0.02`，`fogAlbedo` 本来就对
#        → **幂等跳过**；`property_count 6`、`applied 1 / failed 0`、`official_calls 9` 逐项对平。
#     ② 云的新数值真跑：`Cloud_GlobalCoverage 0.55 → -0.35`、`Cloud_GlobalDensity 0.02 → 0.012`
#        写入并读回；**出图确认云回来了**（0.55 那次云整个没了）→ 该参数**越大云越少**得到实证。
#     ③ **如实性 bug（已修）**：材质段是 `_env_apply_factor` **返回之后**才写的 —— 那一行的状态会
#        停在 `already`，于是报文出现"`wrote` 里有两条 / 状态 `already` / 总结说 0 项写入"的自相矛盾。
#        已改成：材质段真写了就把状态提到 `applied`。
#     ④ **易用性 bug（已修）**：`overrides` 原先不认 `props` 键 —— 而**预设里就是
#        `props/settings/transform` 这个形状**，用户照抄（我自己就照抄了）→ 官方原文
#        `could not be read: props`（把 "props" 当成了一个叫 props 的属性）。已加为第四个保留键。
#
#   · 2026-09-29 **第五批（用户"继续"）—— 材质参数四种类型补齐（texture / static switch）**：
#     `_env_apply_material` 的参数循环改成**按值的形状分派**：
#       数字 → scalar · 对象/数组 → vector · **字符串 → texture**（值是纹理资产路径，按对象路径写）
#       · **true/false → static switch**。
#     ⚠ 官方明说**改 static switch 会触发 shader 重编译**，报文里会带这句提醒。
#     ⚠ **本批尚未真跑**：要验的是"四种都读得到现值、写得进、读得回"。
#   · 天气（雨/雪）：**用户 2026-09-29 拍板「不加了」** —— 相关编排代码
#     （`_env_apply_niagara()` / `TS_NIAGARA` 常量 / 因素表的 `niagara` 段调用点）**已删干净**
#     （删的时候它**零副作用**：`config/environments.json` 里**没有任何预设**用到 `niagara` 段）。
#     ⚠ **官方那侧的能力还在**（实测 2026-09-29：`NiagaraToolset_Component` 有 `SetSystem` /
#     `GetUserVariables` / `SetVariable`，官方文档明说"用 SetSystem、别直接设资产属性"）——
#     按表述纪律：**不是"我们没有这个能力"，是"这一版没把它编排进来"**。真要接，按「先给定
#     谁消费 / 要什么闸门，再开一条**带闸门的通道**」办（dry_run + 先读现值 + 读回核对 + 台账）。
#     ⚠ 实测本工程 `/Game` 下**没有雨/雪系统**（2026-09-29 两次实测：跳板特效
#     `LevelPrototyping/.../NS_JumpPad` + `FreeNiagaraPack` 那 5 个特效，**用户变量全空**）
#     —— 要真做天气，得先有那类资产（用户补）。
#
#   · 2026-09-29 **第六批（接手会话）—— 把整改版的执行顺序写进代码 + 扫旧名**（用户拍板 A 案）：
#     ① 新增常量 `ENV_FACTOR_ORDER`（太阳 → 大气 → 天光 → 高度雾 → **局部体积雾** → 体积云 → 后处理），
#        `setup_environment()` 在 ③-c 处对 `spec_all` **稳定重排**；表外因素排最后并**点名**。
#        理由：改动前顺序**完全由 `config/environments.json` 的键序决定**，用户把 `post_process`
#        挪到第一行就能打乱它、工具不拦也不报（那次核对确认的）。顺序归代码 = 内容归配置，
#        与阶段三 `BUILD_LAYERS` 同一条纪律。
#     ② `local_fog` 从"cloud 之后"挪到"**fog 之后**"（归雾族，按外部流程「大气与体积雾」那一步归类）
#        —— 这是**判断、不是实测**：位置对最终画面无影响（都是逐帧生效的属性写入）。
#     ③ 整改版**第 8 步**（重编译类排最后）**没实现**：材质参数（含 static switch）仍**就地**写，
#        只把 `_env_apply_material` 的 static-switch 提示**补全**成"它没排最后，如实报"。
#     ④ 旧名扫描：本文件内 4 处「环境搭建与相机预览 / 阶段六 · 相机预览」→ 定名「环境搭建」、
#        相机预览改记**阶段七**；`next_step` 不再指向 `capture_preview()`（那是阶段七的事）。
#     ✅ **①②④ 已实测**（2026-09-29 用户跑预检绿 + 重启后，两次调用的**原始报文**为证）：
#       · 靶子是**次序**，打中了 —— `setup_environment(dry_run=true)` 的 `rows` =
#         `sun → sky_atmosphere → sky_light → fog → **local_fog** → cloud → post_process`，
#         **与 `ENV_FACTOR_ORDER` 逐条相同**；`planned 7 / applied 0 / already 7 / failed 0`、
#         `official_calls 31`。
#       · **反向证据**（比正向更有说服力）：`warnings` 里**没有**"不在执行顺序表里、已排到最后"
#         那条点名 —— 若排序没生效或 `local_fog` 没被认下，它必然出现。
#       · 紧接着又**真做**一次（同一档 `sunset`）→ `applied 0 / already 7 / failed 0`、
#         `official_calls 31`、台账 `views/environment_state_v1.json` 重写；**0 条属性写入**
#         （关卡现值与预设一致 → 幂等全跳过），所以用户关卡**这一轮一个字节都没被改**。
#       · `next_step` 也实测到了新文案（旧文案那句"下一步 `capture_preview()`"已消失）。
#     ⚠ **仍未实测**：① 表外因素的**点名分支**（当前配置里没有表外因素，**没靶子**）；
#       ② static switch 那句新提示（预设里没有布尔参数，同样**没靶子**）。
#
#   · 2026-09-29 **第七批 —— 修一个"如实性"bug（由上面那次真跑实测复现）**：
#     **现象（实测）**：`cloud` 行 `status: already` / `property_count: 0`，`wrote` 却是
#       `{"_material": {}}` —— 与用户定的"报文与台账里**只记改动**"口径冲突。
#     **根因**：材质段（以及当时还在的 Niagara 段）无条件把 `{**row.wrote, "_material": m_wrote}` 挂上去，
#       即使 `m_wrote` 是**空字典**。
#     **危害（读代码推断，不是实测）**：`_env_ledger_save()` 的判据是 `not row.wrote` ——
#       `{}` 为假（该跳过）、`{"_material": {}}` **为真**（被当成"写了东西"）→
#       "某因素**第一次**被处理时恰好全幂等"（台账被删 / 换张图之后第一次跑，而我们的 MI 与
#       参数都已经在）会往**回滚台账**记一条"我们改过这个因素"的**假记录**，记下就按
#       "只记第一次"再也纠正不了。⚠ 现有台账里**看不出**它（`views/environment_state_v1.json`
#       的 7 个因素都是在"真写了东西"的那次被合法记下的）—— 所以只能算推断。
#     **修法**：`wrote` 只在 `m_wrote` / `n_wrote` **非空**时才挂那一段（两处同构，一起改）；
#       `before` / `after` 不动（它们是"读到了什么"的记录，报告里要看）。
#     ✅ **本批已实测**（2026-09-29 用户**真正**重启后 —— 见第八批那条"进程比文件旧"的教训：
#       `cloud` 行的 `wrote` 是 **`{}`** 而不是 `{"_material": {}}`，靶子打中）。
#       ⚠ 留一笔：这一批**第一次"重启"没生效**（跑着的进程 17:41:52 比改过的文件 19:09:29 旧），
#       是**用户追着问"是不是连的最新"**才查出来的 —— 症状只有一个字段的差别，不报错。
#
#   · 2026-09-29 **第八批 —— 回滚（`restore=true`）这条安全网：第一次被走就发现是坏的，三处全修**
#     （用户拍板 A 案）。**发现过程本身就是证据**：此前只验过"台账齐全 / 可回滚"，
#     从没真跑过回滚 —— 这次拿零副作用的演练（`restore=true, dry_run=true`）第一次真走这条路，
#     报文是 `planned 7 / failed 1`。三处洞：
#     ① **台账里的 Actor 引用会过期，而代码先信后不验**：
#        台账记的是当时的引用（实测 `PostProcessVolume_1`），用户存盘 / 重开关卡之后我们建的那个卷
#        会被重建并**换名**（实测现存 `PostProcessVolume_0`）→ 那个引用指向不存在的对象，
#        官方回原文 `... is not valid Object for property 'instance'`。
#        改动前它把过期引用直接塞进 `refs`、还顺手 `missing.remove(k)` —— **既没回退到"按类找"，
#        也永远不会走 `spawn` 重建**，于是失败到底。
#        修法：**`get_label` 先验活**；不活就当"没有它"，让按类找 / `spawn` 兜底（每项 +1 次调用）。
#     ② **对"我们自己建的 Actor"，"退回原样"这个词兑现不了**：
#        台账里那行的"动手前的值"是**我们建它时读到的引擎默认值**（演练里 `local_fog` 要写回的是
#        `heightFogFalloff 1000` / `heightFogExtinction 1` / `radialFogExtinction 1` ≈ **没有雾**），
#        可它动手前**根本不存在** —— 写回默认值只会留一个空壳。
#        修法：回滚时**把 `UEMCP_env/` 下的那些（我们自己建的）`remove_from_scene` 删掉**。
#        "哪些是我们建的"靠**分组**认、不靠名字（与 `_env_spawn_actor` 同口径）。
#      报文侧：删除项记 `status=applied` + `wrote={"_deleted": <引用>}`（`dry` 时只报"会删"），
#        并单独数出 `deleted` 用在 `next_step` 里 —— 免得"删了一个 Actor"和"写回一批属性"混成一个数。
#     ③ **回滚时不许"新建"**（修 ② 时自查出来的，同源）：`spawn: true` 的因素在"我们动手前"不存在 ——
#        回滚时若它已经不在了（用户删了 / 没存盘重开），旧逻辑会**再 spawn 一个**、把引擎默认值写进去
#        = 退着退着又多留一个空壳，与"退回原样"正好相反。修法：回滚时这一类**什么都不做**，
#        并且**报 `already` 而不是 `failed`**（它现在不在 == 正合原样；报 failed 就是一次假失败）。
#     ✅ **本批三处全部已实测**（2026-09-29，用户一句"跑"之后，四份原始报文为证）：
#       ① 演练 `restore=true, dry_run=true` → `planned 7 / applied 0 / failed 0`（改前 `failed 1`）；
#          `warnings` 原文承认「台账里给 `后处理` 记的 Actor 引用 `PostProcessVolume_1` **已经不在了**
#          —— 这次**不用它**：改用**按类找到的那个**」→ **验活生效**。
#       ② 真跑 `restore=true` → **`applied 7 / already 0 / failed 0`**、`official_calls 41`；
#          两个卷真删（`wrote._deleted` = `.../LocalFogVolume_0` 与 `.../PostProcessVolume_0`）；
#          太阳 / 大气 / 天光 / 雾 / 云 五行写回并**读回核对通过**。台账历史里第一条 `(回滚)`。
#          ⚠ 顺带证到"只动该动的"：太阳的 `location (0,0,400)` 与 `scale (1,1,1)` 一字未改。
#       ③ 删完**再**演练一次 → `already 5 / failed 0`（**没有假失败**），warnings 原文
#          「回滚：局部体积雾 / 后处理 是**我们建的** …… **这次不新建**」→ **③ 生效**。
#       ④ 收尾：不带参数再跑一次（`sunset`）→ `applied 7 / failed 0`、
#          `spawned = [LocalFogVolume_1, PostProcessVolume_1]` → **删掉之后能重新建起来**（`spawn` 也验到了）。
#     ⚠ **一条残留风险（实测看到，别当它不存在）**：台账里的 Actor 引用是**名字、不是身份** ——
#       删了再建**可能撞回同一个名字**（实测：`PostProcessVolume_0` 删掉后新建的又叫
#       `PostProcessVolume_1`，而台账里记的**正是** `_1`）。所以"验活通过"只说明**那个名字上有东西**，
#       不保证是**同一个** Actor。对回滚仍可接受（它要删/要写的本来就是"我们建的那些"），
#       但**不许把引用当唯一标识**用（要真解决得给自建 Actor 打标记 / 记 spawn 序号）。
#
#   · 2026-09-29 **第九批（小修，与第八批的证据同一轮）**：演练的计数口径。
#     现象：③ 那次 `next_step` 说"演练：这次会改 **2** 个因素（局部体积雾、后处理）"，
#     可那两项真跑**什么都不做**（既不新建也不写值）。根因：判据是 `r.status == "dry"`，
#     而演练里有三类 `dry` 行、只有"有 `wrote` 的"才真会改。
#     修法：判据改成 **`r.wrote` 非空**（"会删"的行仍有 `_deleted` → 仍算数）。
#     ⚠ **本批尚未实测**（改这句时我差点写成"已实测"—— 那是**推断**：①③ 那两份报文都是**旧判据**
#       跑出来的，新判据一次都没跑过。如实标"尚未实测"）。靶子两条：
#       · ① 那种情形（演练里两行有 `_deleted`）→ 仍应报 **7 个因素**（与旧判据同）；
#       · ③ 那种情形（那两行不新建、`wrote` 为空）→ 应报 **0 个因素**（旧判据错报成 2）。


def _env_write_value(current: Any, wanted: Any) -> Any:
    """把配置里的值转成**能整份写下去**的形状。

    ⚠ 为什么必须整份（实测 2026-09-29 踩过）：`set_properties` 对结构体是**整体替换** ——
      只写 `{"r": 0.5}` 会把没给的 `g/b/a` 打坏，读回都不再是合法 JSON。
      所以现值是 dict 时：**以现值为底、把要改的键盖上去**，整份写。
    ⚠ 为什么按**现值形状**判、不按属性名判：名字是 UE 的、会随版本改（这个工程里
      `fogInscatteringColor` 在这个版本已经叫 `fogInscatteringLuminance`）——
      按形状判不用维护白名单，也不会因为改名把值写给错的对象。
    """
    if isinstance(current, dict):
        if isinstance(wanted, dict):
            merged = dict(current)
            merged.update(wanted)
            return merged
        if isinstance(wanted, (list, tuple)) and current and len(wanted) >= 3:
            # 按**现值自己的键序**逐位对上：`{r,g,b,a}` 或 `{x,y,z,w}`（Vector4 那种）都吃。
            # 不假设键名 —— 假设错了就会给 Vector4 塞出四个多余的 `r/g/b/a` 键，把结构体写坏。
            keys = list(current)
            return {k: (float(wanted[i]) if i < len(wanted) else float(current[k]))
                    for i, k in enumerate(keys)}
        return wanted          # 形状对不上 = 原样传，让官方报错，不替它猜
    return wanted


COLOR_QUANT_TOL = 0.002
"""结构体 / 颜色分量读回时的**量化**误差上限。

实测 2026-09-29：写 `0.9` 读回 `0.9019608`（差 0.00196，8-bit 量化）—— 0.002 留一点余量。
低于这个数不算"改了"，否则每次运行都会把颜色重写一遍、台账里堆一堆假变更。"""


def _env_leaf_same(a: Any, b: Any, abs_tol: float) -> bool:
    """比两个**叶子**值。`abs_tol > 0` = 用**绝对**容差（量化过的分量）；否则用相对容差。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_env_leaf_same(a.get(k), b.get(k), COLOR_QUANT_TOL) for k in set(a) | set(b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        af, bf = float(a), float(b)
        if abs_tol > 0:
            return abs(af - bf) <= abs_tol
        if abs(af) < 1e-9 and abs(bf) < 1e-9:
            return True
        return abs(af - bf) <= 1e-4 * max(abs(af), abs(bf))
    return a == b


def _env_same(a: Any, b: Any) -> bool:
    """两个值一样吗 —— **幂等**判据（决定"跳过"还是"写"）。

    两种情形口径不同，各有实测依据：
      · **结构体 / 颜色**（dict）：分量是**量化过**的（8-bit），用**绝对**容差 `COLOR_QUANT_TOL`；
      · **标量**：用**相对**容差 1e-4（float32 回读精度，实测写 0.045 读回 0.04500000178813934）。

    ⚠ **不许**写成"1% 相对 + `max(1.0, |a|, |b|)` 下限" —— 2026-09-29 实测踩过（**演练里抓到的**）：
      `mieScatteringScale` 现值 `0.003996`、预设 `0.006`（差 50%），被那个下限变成的
      **0.01 绝对容差**判成"本来就一样"、**静默跳过**。散射系数 / 雾密度这类小量级参数全都会中招，
      而"静默少写一条"正是本工程最不能接受的事（证据：那次演练报告里 `sky_atmosphere.wrote`
      就是少了 `mieScatteringScale`）。
    """
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_env_leaf_same(a.get(k), b.get(k), COLOR_QUANT_TOL) for k in set(a) | set(b))
    return _env_leaf_same(a, b, 0.0)


async def _env_locate(
    ctx: Context[AppContext], want: dict[str, str], picks: dict[str, str] | None = None
) -> tuple[dict[str, str], list[str], list[str], int]:
    """找每个因素要改的那个 Actor：**默认按类找第一个**；调用方点名了（`picks`）就按名字找。

    返回 `(因素→Actor 引用, 缺哪些, 备注, 官方调用次数)`。

    `picks`（2026-09-29 用户授权加的）＝ `{因素: 名字片段 或 Actor 引用}`：
      · 看着像引用（含 `:` 或 `.`）→ **直接用它**，不再查；
      · 否则当 **label 片段** 找：命中**恰好 1 个**才用；命中 0 个记 missing；
        命中多个 → **这一项跳过并列出来**，让用户自己说清（**不猜**）。
    为什么加它：默认按类找只拿第一个 —— 同一类有好几个时（比如你后加的第二个体积雾），
    要改的那个恰恰可能是被忽略的那个。**"你手动放的东西"必须是能点到名的。**
    """
    found: dict[str, str] = {}
    missing: list[str] = []
    notes: list[str] = []
    used = 0
    picks = picks or {}
    for key, cls in want.items():
        pick = str(picks.get(key) or "").strip()
        if pick and (":" in pick or "." in pick):
            found[key] = pick
            notes.append(f"`{key}` 用**点名给的引用**：`{pick.rsplit('.', 1)[-1]}`。")
            continue
        if pick:
            used += 1
            hits = await call_official(ctx, "find_actors", {
                "name": pick, "tag": "", "collision_channels": [],
            }, toolset=TS_SCENE)
            refs = [r for r in (_ref_path(h) for h in (hits or [])) if r]
            if len(refs) == 1:
                found[key] = refs[0]
                notes.append(f"`{key}` 按名字片段 `{pick}` 命中 `{refs[0].rsplit('.', 1)[-1]}`。")
            else:
                missing.append(key)
                names = "、".join(r.rsplit(".", 1)[-1] for r in refs) or "一个都没有"
                notes.append(f"⚠ `{key}` 点名要 `{pick}`，但按名字命中 **{len(refs)} 个**（{names}）"
                             " —— **这一项跳过**（不猜是哪一个）。要么名字写全，要么直接给完整引用。")
            continue
        used += 1
        hits = await call_official(ctx, "find_actors", {
            "actor_type": {"refPath": cls}, "name": "", "tag": "", "collision_channels": [],
        }, toolset=TS_SCENE)
        refs = [r for r in (_ref_path(h) for h in (hits or [])) if r]
        if not refs:
            missing.append(key)
            continue
        found[key] = refs[0]
        if len(refs) > 1:
            notes.append(
                f"关卡里有 {len(refs)} 个 {cls.rsplit('.', 1)[-1]}"
                f"（{'、'.join(r.rsplit('.', 1)[-1] for r in refs)}）—— "
                f"这次改的是**第一个** {refs[0].rsplit('.', 1)[-1]}；"
                f"要改别的那个，用 `actors` 给 `{key}` 点名（名字片段或完整引用）。")
    return found, missing, notes, used


async def _env_component(
    ctx: Context[AppContext], actor_ref: str, hint: str
) -> tuple[str, int]:
    """取这个 Actor 上**承载参数的那个组件**（`hint` = 组件对象名的前缀）。

    实测 2026-09-29 的组件清单（`get_components`）：
      · `DirectionalLight_0` → `LightComponent0` / `ArrowComponent0` / `BillboardComponent_0`
      · `SkyAtmosphere_0` → `Sprite` / `SkyAtmosphereComponent` / `ArrowComponent`
      · `SkyLight_0` → `Sprite` / `SkyLightComponent0` / `BillboardComponent_0`
      · `ExponentialHeightFog_0` → `Sprite` / `HeightFogComponent0`
      · `VolumetricCloud_0` → `Sprite` / `VolumetricCloudComponent`
    所以"对象名以 hint 开头"对 5 个因素**都唯一命中**（`Sprite` / `ArrowComponent0` 这类
    辅助组件不会误中）。**挑不到就报错、不猜**：只有恰恰一个组件时才退而用它。
    """
    comps = await call_official(ctx, "get_components", {"actor": {"refPath": actor_ref}},
                                toolset=TS_ACTOR)
    refs = [r for r in (_ref_path(c) for c in (comps or [])) if r]
    hits = [r for r in refs if r.rsplit(".", 1)[-1].startswith(hint)]
    if len(hits) == 1:
        return hits[0], 1
    if not hits and len(refs) == 1:
        return refs[0], 1
    names = "、".join(r.rsplit(".", 1)[-1] for r in refs) or "（一个都没有）"
    raise ToolError(
        f"`{actor_ref.rsplit('.', 1)[-1]}` 上找不到承载参数的那个组件"
        f"（要对象名以 `{hint}` 开头的，命中 {len(hits)} 个；现有组件：{names}）—— "
        "**没写任何属性**。组件名对不上说明这个引擎版本的组件改名了，"
        "先 `list_properties` 看一眼真名，别猜。")


async def _env_apply_factor(
    ctx: Context[AppContext], key: str, name_cn: str, spec: dict,
    actor_ref: str, hint: str, dry: bool,
) -> tuple[EnvRowResult, int]:
    """把一个因素写进关卡：**先读现值（回滚依据）→ 幂等比对 → 写 → 读回核对**。

    顺序不许反（与 `apply_surfaces` 同一条纪律）：
      · **先读现值** —— 它同时就是台账（回滚靠它）；没读到就写 = 没有退路；
      · **幂等** —— 现值就是要写的值 → 跳过（`already`），不白写；
      · **写后读回** —— 写进去但读回不是它 = 没成，如实报（不掩饰）。

    `spec` 三个段（配置里的形状）：
      · `props`     —— 写**组件**（或 Actor，后处理那种没组件的）上的属性；
      · `settings`  —— 后处理专有：写 Actor 的 `settings`（结构体，**整份写**，见 ④）；
      · `transform` —— 只给 `pitch/yaw/roll` 三个键，代码会和**当前**的 location/scale 拼成
                       **整份**变换再写（见 ⑤：只传 rotation 会把 location 打到 0）。

    返回 `(这一行的结果, 官方调用次数)`。
    """
    used = 0
    row = EnvRowResult(factor=key, name_cn=name_cn, actor=actor_ref.rsplit(".", 1)[-1])

    # ① 参数在哪：有 hint = 在组件上；没 hint = 就写 Actor 自己（后处理卷的 bUnbound 等）
    target = actor_ref
    if hint:
        try:
            target, c_used = await _env_component(ctx, actor_ref, hint)
            used += c_used
        except ToolError as exc:
            used += 1
            row.status, row.error = "failed", str(exc)
            return row, used
        row.component = target.rsplit(".", 1)[-1]

    props = dict(spec.get("props") or {})
    settings = dict(spec.get("settings") or {})
    # `transform` 支持两种写法（2026-09-29 从"只认朝向"扩成**整份变换**）：
    #   · 老写法（5 档预设都在用）：`{"pitch": -8, "yaw": -22, "roll": 0}`
    #   · 新写法：`{"location_m": [x, y, z], "rotation": {pitch, yaw, roll}, "scale": [x, y, z]}`
    #     ⚠ `location_m` 是**米**（与人看的其它表一致），内部 ×100 成厘米再写 UE；
    #       `location`（对象形式）与 `location_cm` 按**厘米**解 —— 回滚路径走这两个。
    #       两种单位**分开命名、绝不许混**（差 100 倍是本工程踩过的老账）。
    xf_spec = dict(spec.get("transform") or {})
    rot_in: dict[str, float] = {k: v for k, v in xf_spec.items() if k in ("pitch", "yaw", "roll")}
    if isinstance(xf_spec.get("rotation"), dict):
        rot_in.update({str(k): v for k, v in xf_spec["rotation"].items()
                       if str(k) in ("pitch", "yaw", "roll")})
    loc_cm_in: list[float] | None = None
    if isinstance(xf_spec.get("location_m"), (list, tuple)) and len(xf_spec["location_m"]) == 3:
        loc_cm_in = [float(v) * 100.0 for v in xf_spec["location_m"]]
    elif isinstance(xf_spec.get("location_cm"), (list, tuple)) and len(xf_spec["location_cm"]) == 3:
        loc_cm_in = [float(v) for v in xf_spec["location_cm"]]
    elif isinstance(xf_spec.get("location"), dict):
        loc_cm_in = [float(xf_spec["location"].get(k) or 0.0) for k in ("x", "y", "z")]
    scl_in: list[float] | None = None
    if isinstance(xf_spec.get("scale"), (list, tuple)) and len(xf_spec["scale"]) == 3:
        scl_in = [float(v) for v in xf_spec["scale"]]
    want_xf = bool(rot_in or loc_cm_in or scl_in)

    # ② 先读现值 —— 要写的属性 / settings 字段 / 整份变换，一次读齐（**这就是台账**）
    before: dict[str, Any] = {}
    cur_settings: dict[str, Any] = {}
    cur_xf: dict[str, Any] = {}
    try:
        if props:
            used += 1
            raw = await call_official(ctx, "get_properties",
                                      {"instance": {"refPath": target},
                                       "properties": sorted(props)}, toolset=TS_OBJECT)
            got = json.loads(raw) if isinstance(raw, str) else (raw or {})
            before.update(got if isinstance(got, dict) else {})
        if settings:
            used += 1
            raw = await call_official(ctx, "get_properties",
                                      {"instance": {"refPath": target},
                                       "properties": ["settings"]}, toolset=TS_OBJECT)
            got = json.loads(raw) if isinstance(raw, str) else (raw or {})
            cur_settings = ((got or {}).get("settings") or {}) if isinstance(got, dict) else {}
            if not isinstance(cur_settings, dict):
                raise ToolError("`settings` 读回来不是对象 —— 后处理卷形状不对，**没写任何属性**")
            before["_settings"] = {k: cur_settings.get(k) for k in settings}
        if want_xf:
            used += 1
            cur_xf = await call_official(ctx, "get_actor_transform",
                                         {"actor": {"refPath": actor_ref}}, toolset=TS_ACTOR) or {}
            if not isinstance(cur_xf, dict):
                cur_xf = {}
            before["_transform"] = {
                "location": dict(cur_xf.get("location") or {}),
                "rotation": dict(cur_xf.get("rotation") or {}),
                "scale": dict(cur_xf.get("scale") or {}),
            }
    except (ToolError, ValueError) as exc:
        row.status = "failed"
        row.error = f"读现值就失败了（{exc}）—— **没写任何属性**"
        return row, used
    row.before = before

    # ③ 幂等比对：要写的值是不是**已经就是**现值
    changed_props = {k: _env_write_value(before.get(k), v) for k, v in props.items()
                     if not _env_same(before.get(k), _env_write_value(before.get(k), v))}
    seen_settings = before.get("_settings") or {}
    changed_set = {k: v for k, v in settings.items()
                   if not _env_same(seen_settings.get(k), v)}
    cur_tf = before.get("_transform") or {}
    cur_loc = dict(cur_tf.get("location") or {})
    cur_rot = dict(cur_tf.get("rotation") or {})
    cur_scl = dict(cur_tf.get("scale") or {})
    changed_rot = {k: float(v) for k, v in rot_in.items() if not _env_same(cur_rot.get(k), v)}
    changed_loc = ({k: v for k, v in zip(("x", "y", "z"), loc_cm_in)
                    if not _env_same(cur_loc.get(k), v)} if loc_cm_in else {})
    changed_scl = ({k: v for k, v in zip(("x", "y", "z"), scl_in)
                    if not _env_same(cur_scl.get(k), v)} if scl_in else {})
    changed_xf = bool(changed_rot or changed_loc or changed_scl)
    row.property_count = (len(changed_props) + len(changed_set)
                          + len(changed_rot) + len(changed_loc) + len(changed_scl))
    if not row.property_count:
        row.status, row.after = "already", dict(before)
        return row, used
    if dry:
        row.status = "dry"
        row.wrote = dict(changed_props)
        if changed_set:
            row.wrote["settings"] = changed_set
        if changed_xf:
            row.wrote["_transform"] = {
                **({"location": changed_loc} if changed_loc else {}),
                **({"rotation": changed_rot} if changed_rot else {}),
                **({"scale": changed_scl} if changed_scl else {}),
            }
        return row, used

    # ④ 写：属性 + settings 一次写完；变换单独一次（走 set_actor_transform，**整份**传）
    wrote: dict[str, Any] = {}
    try:
        if changed_props or changed_set:
            values: dict[str, Any] = dict(changed_props)
            if changed_set:
                values["settings"] = {**cur_settings, **changed_set}
            used += 1
            await call_official(ctx, "set_properties",
                                {"instance": {"refPath": target},
                                 "values": json.dumps(values, ensure_ascii=False)},
                                toolset=TS_OBJECT)
            # ⚠ 报文与台账里只记**改动的那几条**（`changed_set`），**不是**整份 460 字段的
            #   `settings`：实测 2026-09-29 真跑时整份回灌把一条报文撑到 ≈8k token、台账 941 行
            #   里九成都是它。"**写下去**的是整份（结构体必须整份写）、**报出来**的只是改动" ——
            #   这两件事必须分开。
            wrote.update(changed_props)
            if changed_set:
                wrote["settings"] = dict(changed_set)
        if changed_xf:
            # ⚠ **整份传**（location + rotation + scale）：实测只传 `rotation`，官方会把没给的字段
            #   **打到默认值** —— 太阳的 location 从 (0,0,400) 变成 (0,0,0)（原文见 `[完工-17]` ⑤）。
            full_xf = {
                "location": {**cur_loc, **changed_loc},
                "rotation": {**cur_rot, **changed_rot},
                "scale": {**cur_scl, **changed_scl},
            }
            used += 1
            await call_official(ctx, "set_actor_transform",
                                {"actor": {"refPath": actor_ref}, "xform": full_xf,
                                 "worldspace": True}, toolset=TS_ACTOR)
            wrote["_transform"] = {
                **({"location": changed_loc} if changed_loc else {}),
                **({"rotation": changed_rot} if changed_rot else {}),
                **({"scale": changed_scl} if changed_scl else {}),
            }
    except ToolError as exc:
        row.status, row.wrote = "failed", wrote
        row.error = f"写的时候报错：{exc}"
        return row, used
    row.wrote = wrote

    # ⑤ **读回核对**：写进去但读回不是它 = 没成（与阶段三读回对账同一条纪律）
    try:
        after: dict[str, Any] = {}
        if props:
            used += 1
            raw = await call_official(ctx, "get_properties",
                                      {"instance": {"refPath": target},
                                       "properties": sorted(props)}, toolset=TS_OBJECT)
            got = json.loads(raw) if isinstance(raw, str) else (raw or {})
            after.update(got if isinstance(got, dict) else {})
        if changed_set:
            used += 1
            raw = await call_official(ctx, "get_properties",
                                      {"instance": {"refPath": target},
                                       "properties": ["settings"]}, toolset=TS_OBJECT)
            got = json.loads(raw) if isinstance(raw, str) else (raw or {})
            back = ((got or {}).get("settings") or {}) if isinstance(got, dict) else {}
            after["_settings"] = {k: ((back or {}).get(k) if isinstance(back, dict) else None)
                                  for k in changed_set}
        if changed_xf:
            used += 1
            xf = await call_official(ctx, "get_actor_transform",
                                     {"actor": {"refPath": actor_ref}}, toolset=TS_ACTOR) or {}
            after["_transform"] = {
                "location": dict((xf or {}).get("location") or {}),
                "rotation": dict((xf or {}).get("rotation") or {}),
                "scale": dict((xf or {}).get("scale") or {}),
            }
    except (ToolError, ValueError) as exc:
        row.status = "failed"
        row.error = f"写了，但**读回失败**（{exc}）—— 这一项算没成（不假装成了）"
        return row, used
    row.after = after

    bad = [k for k, v in changed_props.items() if not _env_same(after.get(k), v)]
    seen_after = after.get("_settings") or {}
    for k, v in changed_set.items():
        if not _env_same(seen_after.get(k), v):
            bad.append(k)
    seen_tf = after.get("_transform") or {}
    for part, changed in (("location", changed_loc), ("rotation", changed_rot), ("scale", changed_scl)):
        back_part = seen_tf.get(part) or {}
        for k, v in changed.items():
            if not _env_same(back_part.get(k), v):
                bad.append(f"_transform.{part}.{k}")
    if bad:
        row.status = "failed"
        row.error = ("写了但**读回不是它**：" + "、".join(sorted(bad))
                     + f"（读回 {row.after!r}）—— 如实报，别当成了")
        return row, used
    row.status = "applied"
    return row, used


def _env_object_path(path: str) -> str:
    """把资产路径规范成 UE 的**对象路径** `包.对象`（**已经是的就别再补**）。

    ⚠ 2026-09-29 真跑踩到的：`MaterialInstanceTools.create` 回来的**本来就是对象路径**
      （`/Game/UEMCP/env/MI_Cloud_Sunset.MI_Cloud_Sunset`），我却又套了一次 `to_object_path()`
      → 后缀被重复拼成 `…Sunset.MI_Cloud_Sunset.MI_Cloud_Sunset.MI_Cloud_Sunset`；
      写进去 UE 读回成另一种写法（`包.对象:对象.对象`），于是工具**误报"读回不是它"** ——
      而实际上那次三个材质参数**全都调成功了**。**假失败比没验证更坏**（会让人不敢用这条路）。
    规则：最后一段里**已经有 `.`** → 就当对象路径、原样用；没有才补 `.对象名`。
    """
    p = str(path or "").strip()
    if not p:
        return p
    return p if "." in p.rsplit("/", 1)[-1] else to_object_path(p)


def _env_same_object(a: Any, b: Any) -> bool:
    """两个"对象引用"是不是同一个 —— **按规范化后的路径比**（实测 UE 会换写法）。

    实测 2026-09-29：同一个材质，读回来可能是 `{"refPath": "包.对象"}`，
      也可能是 `"包.对象:对象.对象"` 这种带冒号的字符串。直接字符串比会**误判成"不一样"**。
    这里的口径：取冒号**前面**那半（`包.对象`）再比 —— 既容得下两种写法，
      又不会把"同名但在别的目录"的资产混为一谈（路径仍在比较里）。
    """
    def _norm(v: Any) -> str:
        s = str((v or {}).get("refPath") or "") if isinstance(v, dict) else str(v or "")
        s = s.strip()
        return s.split(":", 1)[0].strip() if ":" in s else s
    na, nb = _norm(a), _norm(b)
    return bool(na) and na == nb


async def _env_apply_material(
    ctx: Context[AppContext], mat: dict, actor_ref: str, hint: str,
    material_prop: str, dry: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str], int, str]:
    """**材质段**：把组件上那个"指向材质"的属性换成**我们自己的 MI**，再调它的参数。

    这是"云量 / 云密度改不了"的正解（2026-09-29 用户授权做）：
    实测云量/云密度**在材质上**（引擎 MI `m_SimpleVolumetricCloud_Inst` 的参数
    `Cloud_GlobalCoverage` / `Cloud_GlobalDensity`），而这个云组件的 **`material`** 属性
    （因素表里的 `material_prop`）可以指向**我们自己的 MI** —— 于是变成"只改这一张图"，
    而不是"改引擎资产、所有用它的关卡一起变"。

    `mat` 段形状：
      `asset`   我们自己的 MI 路径（`/Game/.../MI_xxx`）；不在时若 `create: true` + `parent` 就**新建**
      `create`  是否允许新建（**会新建 UE 资产**，要用户自己存盘才持久）
      `parent`  新建时的父级（如 `/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst`）
      `name` / `folder`  新建时的资产名 / Content 目录（默认 `ENV_MATERIAL_FOLDER`）
      `params`  `{参数名: 值}` —— 数字 = scalar、`{r,g,b,a}` 或 `[r,g,b]` = vector

    返回 `(before, after, wrote, 错误列表, 官方调用次数, 新建的 MI 路径)`；
    `before`/`after`/`wrote` 里**材质参数用 `@参数名` 前缀**，与属性名分开。
    与别处同一条纪律：**先读现值 → 幂等 → 写 → 读回核对**；`dry` 时一个参数都不写。
    ⚠ 只碰**我们自己的 MI 上的参数**，**绝不**去写引擎自带的 MI。
    """
    errs: list[str] = []
    used = 0
    mi_new = ""
    asset = str(mat.get("asset") or "").strip()
    parent = str(mat.get("parent") or "").strip()
    params = dict(mat.get("params") or {}) if isinstance(mat.get("params"), dict) else {}
    if not (asset or params):
        return {}, {}, {}, errs, used, ""
    if not material_prop:
        errs.append("这个因素**没有可指向材质的属性**（因素表里没写 `material_prop`）—— 材质段跳过")
        return {}, {}, {}, errs, used, ""
    if not hint:
        errs.append("这个因素的参数写在 Actor 上（没有组件）—— 材质段跳过")
        return {}, {}, {}, errs, used, ""
    try:
        comp, c_used = await _env_component(ctx, actor_ref, hint)
        used += c_used
    except ToolError as exc:
        errs.append(f"找承载材质的组件失败：{exc}")
        return {}, {}, {}, errs, used + 1, ""

    # ① 要不要换成我们自己的 MI？先 `exists()` 确认；不在就按 `create` 建一个
    if asset:
        used += 1
        try:
            ok = bool(await call_official(ctx, "exists", {"path": asset}, toolset=TS_ASSET))
        except ToolError:
            ok = False
        if not ok:
            if not (mat.get("create") and parent):
                errs.append(f"材质 `{asset}` **不存在**（官方 exists() 返回否），而且没让我建它"
                            "（要建就给 `parent` 并置 `create: true`）—— 材质段跳过")
                return {}, {}, {}, errs, used, ""
            if dry:
                # ⚠ **演练绝不建资产**（2026-09-29 自查补的洞：这条分支原先没被 `dry` 拦住，
                #   一次演练就会真去 `create` —— 那已经不是演练了）。
                # 参数也一并跳过：它们要写在**那个还不存在的 MI** 上，没有它就无从谈起。
                errs.append(
                    f"演练：材质 `{asset}` 不存在 —— 真做时会**新建**一个"
                    f"（父级 `{parent}`，放进 `{str(mat.get('folder') or ENV_MATERIAL_FOLDER)}`），"
                    "再用它调后面的参数；演练**不建资产、不调参数**。")
                return {}, {}, {}, errs, used, ""
            folder = str(mat.get("folder") or ENV_MATERIAL_FOLDER).strip()
            name = str(mat.get("name") or asset.rsplit("/", 1)[-1]).strip()
            try:
                made = await call_official(ctx, "create", {
                    "folder_path": folder, "asset_name": name,
                    "parent": {"refPath": to_object_path(parent)},
                }, toolset=TS_MATINST)
            except ToolError as exc:
                used += 1
                errs.append(f"新建材质实例失败：{exc} —— 材质段跳过")
                return {}, {}, {}, errs, used, ""
            used += 1
            mi_new = _ref_path(made) or ""
            asset = mi_new or asset
            errs.append(f"⚠ **新建**了材质实例 `{asset}` —— **没存盘**，你不存就没了（本工具绝不存盘）")

    # ⚠ **两种路径形状，必须分开用**（2026-09-29 两次真跑各踩一半，正好互补）：
    #   · `exists()` 与配置里写的 → **包路径**（`/Game/UEMCP/env/MI_Cloud_Sunset`）；
    #   · 组件上那个 `material` 属性、以及 `MaterialInstanceTools` 的 `instance` → **对象路径**
    #     （`…MI_Cloud_Sunset.MI_Cloud_Sunset`）—— 传包路径它直接报
    #     "is not a valid object path for property 'instance'"（原文，实测）。
    #   所以下面统一用 `mi_ref`（规范成对象路径）去指材质、去调参数；`exists` 仍用 `asset`。
    mi_ref = _env_object_path(asset) if asset else ""

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    wrote: dict[str, Any] = {}

    # ② 把那个"指向材质"的属性改成我们的 MI（整份写：实测结构体是整体替换）
    if asset:
        used += 1
        try:
            raw = await call_official(ctx, "get_properties",
                                      {"instance": {"refPath": comp},
                                       "properties": [material_prop]}, toolset=TS_OBJECT)
            cur = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (ToolError, ValueError) as exc:
            errs.append(f"读 `{material_prop}` 现值失败：{exc}")
            return before, {}, {}, errs, used, mi_new
        cur_val = (cur or {}).get(material_prop) if isinstance(cur, dict) else None
        before[material_prop] = cur_val
        want = {"refPath": mi_ref}
        if _env_same_object(cur_val, want):
            after[material_prop] = cur_val
        elif dry:
            wrote[material_prop] = want
        else:
            used += 1
            try:
                await call_official(ctx, "set_properties", {
                    "instance": {"refPath": comp},
                    "values": json.dumps({material_prop: want}, ensure_ascii=False),
                }, toolset=TS_OBJECT)
            except ToolError as exc:
                errs.append(f"把 `{material_prop}` 指向 `{asset}` 失败：{exc}")
                return before, {}, {}, errs, used, mi_new
            wrote[material_prop] = want
            used += 1
            try:
                raw = await call_official(ctx, "get_properties",
                                          {"instance": {"refPath": comp},
                                           "properties": [material_prop]}, toolset=TS_OBJECT)
                back = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (ToolError, ValueError) as exc:
                errs.append(f"读回 `{material_prop}` 失败：{exc}")
                return before, {}, wrote, errs, used, mi_new
            back_val = (back or {}).get(material_prop) if isinstance(back, dict) else None
            after[material_prop] = back_val
            if not _env_same_object(back_val, want):
                errs.append(f"`{material_prop}` 写了但**读回不是它**（读回 {back_val!r}）—— 如实报")

    # ③ 逐参数：读 → 幂等 → 写 → 读回。**按值的形状分派四种**（2026-09-29 齐全）：
    #     数字 → `scalar`（`set_scalar_parameter`）
    #     对象/数组 → `vector`（`set_vector_parameter`，`{r,g,b,a}` 或 `[r,g,b]`）
    #     字符串 → `texture`（`set_texture_parameter`，值是**纹理资产路径**）
    #     布尔 → `static switch`（`set_static_switch_parameter`）
    #   ⚠ 官方明说：**改 static switch 会触发 shader 重编译**（`get_static_switch_parameter`
    #     的文档里写着），所以它比另外三种"重"——报文里会带上这句提醒。
    #   为什么按**形状**分派：参数名是资产作者起的、我们不可能有白名单；而值的形状是确定的。
    for pname, pval in params.items():
        name = str(pname or "").strip()
        if not name or not asset:
            continue
        is_switch = isinstance(pval, bool)
        is_tex = isinstance(pval, str)
        is_vec = isinstance(pval, (dict, list, tuple))
        if is_switch:
            kind, getter, setter = "static switch", "get_static_switch_parameter", "set_static_switch_parameter"
        elif is_tex:
            kind, getter, setter = "texture", "get_texture_parameter", "set_texture_parameter"
        elif is_vec:
            kind, getter, setter = "vector", "get_vector_parameter", "set_vector_parameter"
        else:
            kind, getter, setter = "scalar", "get_scalar_parameter", "set_scalar_parameter"
        used += 1
        try:
            got_p = await call_official(ctx, getter,
                                        {"instance": {"refPath": mi_ref}, "name": name},
                                        toolset=TS_MATINST)
        except ToolError as exc:
            errs.append(f"读材质参数 `{name}`（{kind}）失败：{exc}")
            continue
        if isinstance(got_p, dict) and "returnValue" in got_p:
            got_p = got_p.get("returnValue")
        before[f"@{name}"] = got_p
        # 纹理的值是**对象引用**：`{"refPath": "<包.对象>"}`（与材质属性同一套写法）
        want_p = ({"refPath": _env_object_path(pval)} if is_tex
                  else _env_write_value(got_p, pval))
        same = _env_same_object(got_p, want_p) if is_tex else _env_same(got_p, want_p)
        if same:
            after[f"@{name}"] = got_p
            continue
        if dry:
            wrote[f"@{name}"] = want_p
            continue
        used += 1
        try:
            await call_official(ctx, setter,
                                {"instance": {"refPath": mi_ref}, "name": name,
                                 "value": want_p}, toolset=TS_MATINST)
        except ToolError as exc:
            errs.append(f"写材质参数 `{name}`（{kind}）失败：{exc}")
            continue
        wrote[f"@{name}"] = want_p
        if is_switch:
            errs.append(f"⚠ 材质参数 `{name}` 是 **static switch** —— 改它触发了 **shader 重编译**"
                        "（官方文档明说），本工具照做，但这个开关比另外三种'重'。"
                        "⚠ 它**没有**按整改版**第 8 步**（「重编译类排最后」）排到最后：材质参数是"
                        "**就地**在它所属因素那一步写的（`_env_apply_material` 从阶段六起就没实现"
                        "那一步）—— 如实报，不假装顺序是对的。")
        used += 1
        try:
            back_p = await call_official(ctx, getter,
                                         {"instance": {"refPath": mi_ref}, "name": name},
                                         toolset=TS_MATINST)
        except ToolError as exc:
            errs.append(f"读回材质参数 `{name}`（{kind}）失败：{exc}")
            continue
        if isinstance(back_p, dict) and "returnValue" in back_p:
            back_p = back_p.get("returnValue")
        after[f"@{name}"] = back_p
        ok_p = _env_same_object(back_p, want_p) if is_tex else _env_same(back_p, want_p)
        if not ok_p:
            errs.append(f"材质参数 `{name}`（{kind}）写了但**读回不是它**（读回 {back_p!r}）—— 如实报")
    return before, after, wrote, errs, used, mi_new


async def _env_spawn_actor(ctx: Context[AppContext], cls: str, folder: str,
                           label: str = "") -> tuple[str, int]:
    """关卡里没有这个因素的原生 Actor 时**建一个空的**（因素表里标了 `spawn: true` 的那些）。

    实测 2026-09-29：`find_actors(actor_type=/Script/Engine.PostProcessVolume)` 回 **0 个** → 必须建；
    `add_to_scene_from_class` 建得出来（建出来的对象名是 `PostProcessVolume_1`）。
    ⚠ 官方 `name` 参数**不改对象名**（实测：传了名字，回来仍是 `PostProcessVolume_0`）——
      所以"这个是我们的"靠**分组**认（`folder`），不靠名字。
    ⚠ **不存盘**：建完只活在当前关卡里，关掉不存就没了（报文里也会提醒）。
    """
    ref = _ref_path(await call_official(ctx, "add_to_scene_from_class", {
        "actor_type": {"refPath": cls},
        "name": label or f"{OUR_FOLDER_ROOT}_env_actor",
        "xform": {"location": {"x": 0.0, "y": 0.0, "z": 0.0}},
    }, toolset=TS_SCENE))
    if not ref:
        raise ToolError(f"建 `{cls}` **没成**（官方 `add_to_scene_from_class` 没回 Actor 引用）"
                        "—— 这一项跳过，其余照常。")
    try:
        await call_official(ctx, "set_actor_folder",
                            {"actor": {"refPath": ref},
                             "folder_path": folder or ENV_ACTOR_FOLDER},
                            toolset=TS_SCENE)
        return ref, 2
    except ToolError:
        # 分组没设上不算失败：Actor 已经建好了，只是"我们的"这条线索弱一点 —— 如实记在备注里
        return ref, 1


def _env_ledger() -> dict:
    """读环境台账（没有就是空 dict）。"""
    doc = load_json(ENV_LEDGER_PATH)
    return doc if isinstance(doc, dict) else {}


def _env_ledger_save(level: str, preset: str, rows: list[EnvRowResult],
                     refs: dict[str, str]) -> str:
    """把**第一次改之前的现值**记进台账（同一因素**只记第一次**）。

    为什么"只记第一次"：台账的用途是**回滚到用户原来的样子**。第二次、第三次改如果把
    "改之前"覆盖上去，回滚就只能退到上一次，退不回原地 —— 那这本台账就没用了。
    另外每次运行都往 `history` 追一条（谁、什么时候、哪档预设、写了什么），用于留痕。
    """
    doc = _env_ledger()
    doc["level"] = level
    doc.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    doc["_doc"] = [
        "环境台账：**第一次改之前**每个环境因素的现值（含太阳的整份变换）。",
        "用途：`setup_environment(restore=true)` 照着它把关卡写回原样。",
        "⚠ 同一因素只记第一次 —— 覆盖式记录会让回滚退不回原地。",
        "⚠ 这是我们自己的 JSON，不是 UE 存盘。",
    ]
    factors = doc.setdefault("factors", {})
    for row in rows:
        # 只记**真的动过东西**的行：`applied`，或者"写了但读回没对上"的 `failed`
        # （那种行的 `before` 正是回滚需要的 —— 漏记它，用户就退不回去了）。
        if not row.before or (row.status != "applied" and not row.wrote):
            continue
        if row.factor in factors:      # 同一因素**只记第一次**（覆盖式记录会让回滚退不回原地）
            continue
        before = dict(row.before)
        tf_before = dict(before.pop("_transform", {}) or {})
        settings_before = dict(before.pop("_settings", {}) or {})
        mat_before = dict(before.pop("_material", {}) or {})
        # 材质段单独记一条：**属性名 + 它原来的值 + 我们改过的材质参数的原值**
        # ⚠ 回滚时**只把那个属性指回去**（原来的材质一般是引擎 MI）、**不回写参数** ——
        #   参数的原值属于**引擎 MI**，往它上面写就是"动引擎资产"（本项目纪律不允许）。
        mat_entry: dict[str, Any] = {}
        if mat_before:
            prop = next((str(k) for k in mat_before if not str(k).startswith("@")), "")
            mat_entry = {
                "prop": prop,
                "was": mat_before.get(prop),
                "params": {k: v for k, v in mat_before.items() if str(k).startswith("@")},
            }
        factors[row.factor] = {
            "actor": refs.get(row.factor, ""),
            "component": row.component,
            "first_preset": preset,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "props": before,                 # 我们动过的**属性**的现值
            "settings": settings_before,     # 我们动过的 **settings 字段**的现值（后处理）
            "transform": tf_before,     # **整份**（location / rotation / scale，UE 原样：厘米）
            "material": mat_entry,           # 我们动过的**材质段**（自建 MI / 材质参数）
        }
    history = doc.setdefault("history", [])
    history.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "level": level, "preset": preset,
        "wrote": {r.factor: r.wrote for r in rows if r.status == "applied"},
    })
    del history[:-20]                      # 只留最近 20 次，台账别无限长
    save_json(ENV_LEDGER_PATH, doc)
    return str(ENV_LEDGER_PATH)


# --- 阶段七 · 相机预览的机位计算（⚠ 本工具 2026-09-29 在阶段六期间做出来，已划归阶段七）----  【模块：workflow（出图部件）】


def _preview_rotation(pos_m: list[float], look_at_m: list[float]) -> dict:
    """由「站哪儿 + 看哪儿」（**米**）**算**出 UE 的 `pitch/yaw/roll`（度）。

    口径（与 `plan_v1.json.world.coordinate_system` 同源：左手系 Z-up、X 前进 / Y 右 / Z 上）：
      `yaw = atan2(dy, dx)`；`pitch = atan2(dz, 水平距离)`；`roll = 0`。
    ⚠ 这是**推导口径、不是实测值**。两条旁证（2026-09-29）：官方 `CaptureViewport` 回报的
      `cameraRotation` 就是我们传进去的那三个数（原样）；带标签那张图里 12 个中文标签都落在
      画面内 —— 说明这套角度的**朝向没反**。构图好不好看仍要人看。
    """
    dx = float(look_at_m[0]) - float(pos_m[0])
    dy = float(look_at_m[1]) - float(pos_m[1])
    dz = float(look_at_m[2]) - float(pos_m[2])
    horiz = math.hypot(dx, dy)
    pitch = (-90.0 if dz < 0 else 90.0) if horiz <= 1e-9 else math.degrees(math.atan2(dz, horiz))
    return {"pitch": pitch, "yaw": math.degrees(math.atan2(dy, dx)), "roll": 0.0}


def _preview_default_shots(plan: dict) -> list[dict]:
    """plan 没给机位时，按**世界范围**推三个：俯视 3/4 + 两端人视。

    口径来自 `plan_v1.json`：`world.center` / `world.size`（都是米）。
    ⚠ 高度与退距是**推的**（按世界尺寸的比例给），不是实测值 —— 构图不满意就显式给 `shots`。
    """
    world = plan.get("world") or {}
    center = list(world.get("center") or [0.0, 0.0])
    size = list(world.get("size") or [0.0, 0.0])
    cx = float(center[0]) if len(center) >= 2 else 0.0
    cy = float(center[1]) if len(center) >= 2 else 0.0
    sx = float(size[0]) if len(size) >= 2 else 0.0
    sy = float(size[1]) if len(size) >= 2 else 0.0
    diag = math.hypot(sx, sy)
    return [
        # 0.35（原先写的 0.55）：实测 0.55 时相机离世界中心 112 m，90° FOV 下世界只占画面宽约一半、
        # 上方一大片空雾；0.35 把距离收到约 69 m，世界占到约 85% 宽。
        {"name": "aerial", "pos_m": [cx - sx * 0.35, cy - sy * 0.35, diag * 0.35],
         "look_at_m": [cx, cy, 0.0], "fov": 0.0},
        {"name": "street_a", "pos_m": [cx - sx * 0.45, cy, 1.7],
         "look_at_m": [cx + sx * 0.30, cy, 1.2], "fov": 0.0},
        {"name": "street_b", "pos_m": [cx + sx * 0.45, cy, 1.7],
         "look_at_m": [cx - sx * 0.30, cy, 1.2], "fov": 0.0},
    ]


def _safe_file_stem(name: str) -> str:
    """机位名 → 安全文件名（`\\w` 含中日韩，其余字符换 `_`）。"""
    keep = re.sub(r"[^\w\-]+", "_", str(name or "").strip(), flags=re.UNICODE)
    return (keep.strip("_") or "shot")[:60]


def _png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 头里读像素尺寸（不装图像库）。

    PNG 开头固定：8 字节签名 + 4 字节块长 + `IHDR` + 宽(4) + 高(4)，宽高都是**大端**。
    读不到就给 `(0, 0)` —— 尺寸只是给人看的元数据，读不到不该让整次出图失败。
    """
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    return (0, 0)


async def _preview_one(
    ctx: Context[AppContext], shot: dict, annotations: bool, idx: int
) -> tuple[PreviewShotResult, int]:
    """拍一张：算朝向 → **整份**给 `annotations` → 拿 base64 → 解码落盘成 PNG。

    ⚠ 图**不回传给模型**（实测一张 0.87~1.07 MB PNG）：只回路径 + 官方回报的机位元数据。
    ⚠ 机位用 `captureTransform` 传 —— 实测它**不动用户的视口相机**，所以可以放心连拍。
    """
    name = str(shot.get("name") or f"shot_{idx + 1}")
    pos = [float(x) for x in (shot.get("pos_m") or [0.0, 0.0, 1.7])]
    look = [float(x) for x in (shot.get("look_at_m") or [pos[0] + 1.0, pos[1], pos[2]])]
    want_cm = [pos[0] * 100.0, pos[1] * 100.0, pos[2] * 100.0]
    res = PreviewShotResult(name=name)
    args = {
        "captureTransform": {
            "location": {"x": want_cm[0], "y": want_cm[1], "z": want_cm[2]},
            "rotation": _preview_rotation(pos, look),
        },
        # ⚠ 实测：`annotations` 少给任一子字段 → 官方 RuntimeError（"needs a default value"）
        "annotations": dict(PREVIEW_ANN_LABELS if annotations else PREVIEW_ANN_CLEAN),
        "bShowUI": False,
    }
    try:
        got = await call_official(ctx, "CaptureViewport", args, toolset=TS_EDITOR_APP)
    except ToolError as exc:
        res.status, res.error = "failed", f"官方截视口报错：{exc}"
        return res, 1
    got = got if isinstance(got, dict) else {}
    loc = got.get("cameraLocation") or {}
    rot = got.get("cameraRotation") or {}
    res.camera_location = [float(loc.get(k, 0.0)) for k in ("x", "y", "z")]
    res.camera_rotation = [float(rot.get(k, 0.0)) for k in ("pitch", "yaw", "roll")]
    res.fov = float(got.get("cameraFOV") or 0.0)
    res.labeled_actors = len(got.get("labeledActors") or [])
    img = got.get("image") if isinstance(got.get("image"), dict) else {}
    data_b64 = str((img or {}).get("data") or "")
    if not data_b64:
        res.status, res.error = "failed", "官方回了截图对象但**里面没有图像数据**（`image.data` 为空）"
        return res, 1
    try:
        raw = base64.b64decode(data_b64)
    except (ValueError, TypeError) as exc:
        res.status, res.error = "failed", f"base64 解码失败（{exc}）"
        return res, 1
    path = PREVIEW_DIR / f"{_safe_file_stem(name)}.png"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    except OSError as exc:
        res.status, res.error = "failed", f"图落盘失败（{type(exc).__name__}: {exc}）"
        return res, 1
    width, height = _png_size(raw)
    res.file, res.bytes, res.width, res.height = str(path), len(raw), width, height
    res.status = "ok"
    # 读回核对（与阶段三同一条纪律）：官方回报的机位跟我们要的**对不上**就得说出来
    if any(abs(a - b) > 1.0 for a, b in zip(res.camera_location, want_cm)):
        res.error = (f"⚠ 官方回报的机位 {res.camera_location} 与要的 {want_cm} 对不上（>1 cm）—— "
                     "图可能不是从这个机位拍的")
    return res, 1


# --- [完工-17] 工具 17：阶段六 · 环境搭建 -----------------------------------------  【模块：environment】


@mcp.tool()
async def setup_environment(
    ctx: Context[AppContext],
    preset: Annotated[str, Field(
        description=("用哪一档时段预设（如 `sunset` / `blue_hour` / `noon` / `night` / `overcast`，"
                     "可选项来自 `config/environments.json`）。留空 = 用**阶段一记录的那个时段**"
                     "（`catalog/elements.json` 的 `scene_time_of_day`）；那里也空就退到 `noon`"))] = "",
    factors: Annotated[list[str] | None, Field(
        description=("只配这几个环境因素；留空 = 预设里有什么配什么。"
                     "**因素表本身在 `config/environments.json` 的 `factors` 段**（按键覆盖内置兜底）——"
                     "所以你以后加自己的体积雾（`LocalFogVolume`）/ 自己的类，**改配置就行、不用改代码**"))] = None,
    actors: Annotated[dict | None, Field(
        description=("**点名要改哪一个 Actor**，形如 `{\"fog\": \"局部雾_1\"}`（label 片段）"
                     "或 `{\"fog\": \"/Game/...MCP:PersistentLevel.ExponentialHeightFog_2\"}`（完整引用）。"
                     "⚠ 不点名时按类找**第一个**（会在 warnings 里说明）；点名了但**命中多个**时"
                     "这一项**跳过并列出来**（不猜是哪一个）"))] = None,
    overrides: Annotated[dict | None, Field(
        description=("临时微调，**四个保留键**（其余键都当属性）：`{\"fog\": {\"fogDensity\": 0.03}}` "
                     "盖**组件 / Actor 属性**；`{\"post_process\": {\"settings\": {\"bloomIntensity\": 1.2}}}` "
                     "盖**后处理的 `settings` 字段**；`{\"local_fog\": {\"transform\": {\"location_m\": "
                     "[0,0,3], \"scale\": [40,20,6]}}}` 盖**摆放**（`location_m` 是**米**）；"
                     "`{\"local_fog\": {\"props\": {...}}}` 也认（**预设那三段的形状可以直接粘过来**）。"
                     "**只盖这一次**，不写回配置文件（要长期生效请改 `config/environments.json`）"))] = None,
    dry_run: Annotated[bool, Field(
        description="true = 只读现值 + 算「哪几条会变」，**一个属性都不写**（先看一遍再动手用这个）")] = False,
    restore: Annotated[bool, Field(
        description=("true = **回滚**：把 `views/environment_state_v1.json` 里记的**现值**写回去"
                     "（我们第一次改之前的样子）。此时 `preset` / `overrides` 都不用给。"
                     "⚠ 两条口径（由 2026-09-29 的**实测失败**反推得出；**修法本身尚未复验**）："
                     "① 台账里记的 **Actor 引用会先验活** —— 它可能已经不在"
                     "（存盘 / 重开关卡之后重建会换名），不在就退回「按类找」、仍没有就按因素表"
                     "的 `spawn` 重建；② **我们自己建的** Actor（在 `UEMCP_env/` 下的后处理卷 /"
                     "局部体积雾）会被**删掉** —— 那才是「动手前它不存在」的原样"))] = False,
) -> EnvReport:
    """**阶段六 · 环境搭建**：按时段配**整套环境** —— 不只灯光。

    ⚠ 2026-09-29 用户指令：本阶段定名「**环境搭建**」（早先那句「环境搭建与相机预览」**当天被收回**），
      范围**不只有灯光** —— 天空 / 大气 / 天光 / 高度雾 / 体积云 / 后处理 / 时段，**全都要**。
      ⚠ **本阶段不出图**：出图归**阶段七**（`capture_preview()` 代码保留、归那边用）；
      验收 = **用户在 UE 里自己看**。

    做六件事：
      ① 读**环境配置** `config/environments.json`（**每次调用都重读** —— 改它不用改代码、不用重启）；
         里面有两段：`factors`（**有哪些环境因素**：key / 中文名 / 类路径 / 组件名 / 是否新建 / 材质属性名，
         按键覆盖内置兜底）与 `presets`（时段 → 各因素写什么）；
      ② 找 Actor：默认**按类**找第一个（不写死名字）；调用方用 `actors` 点了名就按名字/引用找 ——
         命中多个时**跳过并列出来**，不猜；
      ③ **先读现值** —— 它同时就是台账（`views/environment_state_v1.json`，回滚依据）；
      ④ **幂等**：现值就是要写的值 → 跳过（`already`）；
      ⑤ 写 → **读回核对**（写进去但读回不是它 = 没成，如实报）；
      ⑥ 因素表里标了 `spawn` 的、关卡里又没有 → **建一个**（后处理卷实测 0 个 → 会建，放进 `UEMCP_env/`）。

    ⚠ **处理顺序写死在代码里**（`ENV_FACTOR_ORDER`，2026-09-29 用户拍板 A 案）：
      载体的**找 / 建**全部在写之前 → 太阳 → 大气 → 天光 → 高度雾 → **局部体积雾** → 体积云 → 后处理。
      出处 = `docs/阶段六-环境搭建.md`「乙 · 施工顺序」的整改版（**太阳排在天空之前是实测**：
      本工程太阳 `bAtmosphereSunLight = true`，天空的颜色由太阳决定，先定天空等于白定）。
      ⚠ 不在表里的因素（你自己在配置里加的）**排到最后**，并在 `warnings` 里**点名**。
      ⚠ 整改版**第 8 步**（"会触发 shader 重编译的写入排最后"，典型是材质 static switch）
        **当前未实现**：材质参数是就地写的；真用到 static switch 时那一行会带提示（如实报，不掩饰）。

    ⚠ 走的是「**复用并改关卡原生的环境 Actor**」这条路（2026-09-29 用户在两条路里选的 a）——
      代价就是那本台账：`restore=true` 照着它写回。⚠ 两条实情（2026-09-29 实测）：
      ① 台账里记的 **Actor 引用会先验活**再用（存盘 / 重开关卡后重建会换名，引用会失效）；
      ② **我们自己建的** Actor（`UEMCP_env/` 下的后处理卷 / 局部体积雾）回滚时是**删掉**，
         不是"写回引擎默认值" —— 它动手前不存在，只有删掉才算回到原样。
    ⚠ **绝不存盘** —— 配完在 UE 里看，存不存由你定。
    ⚠ 两条**必须整份写**的实测坑（不然会把状态写坏）：结构体属性是**整体替换**（只写一个键
      会打坏其余键）、`set_actor_transform` 会把没给的字段**打到默认值**（只传 rotation 会把
      location 清零）。本工具按「先读现值 → 合并 → 整份写」处理，见 `_env_write_value`。

    ⚠ **云量 / 云密度**（2026-09-29 更正 + 用户授权做）：实测它们**在材质上**
      （引擎 MI `/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst` 的参数
      `Cloud_GlobalCoverage` / `Cloud_GlobalDensity`）。所以**不是改不了，而是不能直接改引擎 MI**
      （那会让所有用它的关卡一起变）。现在支持走干净的第三条路：预设里给因素加一个 **`material` 段** ——
      `create` 一个**我们自己的 MI**（父级 = 那个引擎 MI）→ 把云组件的 `material` 属性指过去 →
      再用 `MaterialInstanceTools.set_scalar_parameter` 调参数（**先读现值 → 幂等 → 写 → 读回**）。
      ⚠ 这条路**会新建 UE 资产**，而且**要你自己存盘**才持久（本工具不存盘）；
      `restore=true` 只把 `material` 属性**指回原样**，**不回写材质参数**（原值属于引擎 MI，不能碰）。
      ✅ **这条路已实测**（2026-09-29 真跑）：`create` 新建 MI → 指到云组件 → `Cloud_GlobalCoverage` /
      `Cloud_GlobalDensity` / `Cloud_AlbedoColor` 写入并读回；`_material` 段也是靠它读的。
      ⚠ **尚未实测的是其中两类参数**：`texture`（靶子不是 Texture2D 那次失败已定性为**靶子问题**）
      与 `static switch`（预设里没有布尔参数）—— 那两类第一次用请先 `dry_run=true`。
      ⚠ 一个实测到的参数语义：`Cloud_GlobalCoverage` **越大云越少**（引擎默认 -0.2 有云，
      给 0.55 云整个没了 —— 两版出图对比可见）。
    """
    calls = 0
    warns: list[str] = []

    # ① 环境配置**每次调用都重新读**（与材质表同一条纪律：配置改完立刻生效、不用重启）
    factors_tbl, presets, src_note = _env_config()
    if src_note:
        warns.append(src_note)
    known = set(factors_tbl)
    cn = {k: str(v.get("cn") or k) for k, v in factors_tbl.items()}
    cls_of = {k: str(v.get("class") or "") for k, v in factors_tbl.items()}

    # ①-b 调用方点名要改哪个 Actor（2026-09-29 加：默认按类只能拿第一个）
    picks: dict[str, str] = {}
    if actors:
        for k, v in actors.items():
            key = str(k).strip()
            if key not in known:
                raise ToolError(f"`actors` 里有不认识的因素 `{key}` —— 可用的是 {sorted(known)}"
                                "（**一个属性都没写**）。")
            picks[key] = str(v or "").strip()

    level = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
    calls += 1

    # ② 这次用哪一档 —— `restore` 不用预设（它照台账写回）
    chosen = ""
    spec_all: dict[str, dict] = {}
    ledger_refs: dict[str, str] = {}
    if restore:
        ledger = _env_ledger()
        if not (ledger.get("factors") or {}):
            raise ToolError(
                f"没有环境台账（`{ENV_LEDGER_PATH.name}`）—— **没有可回滚的东西**。"
                "台账是 `setup_environment()` **真跑一次**时记下的（演练不记）。")
        if str(ledger.get("level") or "") not in ("", level):
            raise ToolError(
                f"台账记的关卡是 `{ledger.get('level')}`，当前关卡是 `{level}` —— **换图了**，"
                "台账里的 Actor 引用在这儿没有意义 —— **一个属性都没写**。")
        for key, entry in (ledger.get("factors") or {}).items():
            if not isinstance(entry, dict):
                continue
            mat_entry = entry.get("material") if isinstance(entry.get("material"), dict) else {}
            mat_spec: dict[str, Any] = {}
            was = (mat_entry or {}).get("was")
            if isinstance(was, dict) and was.get("refPath"):
                # 把那个"指向材质"的属性**指回原样**。
                # ⚠ **不回写材质参数**：它们的原值属于**引擎 MI**，往引擎 MI 上写就是"动引擎资产"
                #   （本项目纪律不允许）—— 这一条在报文里也要如实说清，不假装全退回去了。
                mat_spec = {"asset": str(was["refPath"]).rsplit(".", 1)[0],
                            "create": False, "params": {}}
            # **整份**变换回写（台账里存的是 UE 原样的**厘米**）：
            #   · `rotation`  → 角度，直接给；
            #   · `location`  → 对象形式 `{x,y,z}`（**厘米**，走 `_env_apply_factor` 的厘米分支）；
            #   · `scale`     → 列成 `[x,y,z]`（台账里存的是对象）。
            # ⚠ 单位的边界就在这儿：**配置给人写的是米（`location_m`），台账/回滚是厘米** ——
            #   两者分开命名，不许混（差 100 倍是本工程踩过的老账）。
            tf_entry = entry.get("transform") if isinstance(entry.get("transform"), dict) else {}
            tf_restore: dict[str, Any] = {}
            if isinstance(tf_entry.get("rotation"), dict) and tf_entry["rotation"]:
                tf_restore["rotation"] = dict(tf_entry["rotation"])
            if isinstance(tf_entry.get("location"), dict) and tf_entry["location"]:
                tf_restore["location"] = dict(tf_entry["location"])
            if isinstance(tf_entry.get("scale"), dict) and tf_entry["scale"]:
                tf_restore["scale"] = [tf_entry["scale"].get(k, 1.0) for k in ("x", "y", "z")]
            spec_all[str(key)] = {
                "props": dict(entry.get("props") or {}),
                "settings": dict(entry.get("settings") or {}),
                "transform": tf_restore,
                "material": mat_spec,
            }
            ledger_refs[str(key)] = str(entry.get("actor") or "")
        warns.append(
            f"**回滚**：照台账写回 {len(spec_all)} 个因素"
            f"（记于 {ledger.get('created_at') or '未知时间'}）。"
            "⚠ 台账只记**第一次改之前**的样子 —— 它退的是「我们动手前」那一版，不是「上一次」。")
    else:
        chosen = str(preset or "").strip()
        if not chosen:
            chosen = str((load_json(ELEMENTS_PATH) or {}).get("scene_time_of_day") or "").strip()
            if chosen:
                warns.append(f"没指定 `preset` —— 用了**阶段一记录的那个时段**：`{chosen}`"
                             "（`catalog/elements.json` 的 `scene_time_of_day`）。")
        if chosen not in presets:
            fallback = "noon" if "noon" in presets else sorted(presets)[0]
            if chosen:
                warns.append(f"预设表里没有 `{chosen}` —— 退到 `{fallback}`。"
                             f"可选项：{'、'.join(sorted(presets))}。")
            chosen = fallback
        raw_spec = presets.get(chosen) or {}
        preset_note = str(raw_spec.get("_note") or "").strip()
        if preset_note:
            warns.append(f"预设 `{chosen}` 的说明：{preset_note}")
        for k, v in raw_spec.items():
            key = str(k)
            if key.startswith("_"):
                continue                  # `_note` 这类是给人看的说明，不是环境因素
            if not isinstance(v, dict):
                warns.append(f"⚠ 预设 `{chosen}` 里的 `{key}` 不是对象"
                             f"（收到 {type(v).__name__}）—— **跳过了**。")
                continue
            spec_all[key] = dict(v)

    # ③ 过滤 + 微调 + **点名**不认识的键（不静默忽略）
    for k in list(spec_all):
        if k not in known:
            warns.append(f"⚠ 预设 `{chosen}` 里有 `{k}`，但它不在环境因素表里 —— **跳过了**。"
                         f"表里是：{'、'.join(sorted(known))}。")
            spec_all.pop(k)
    if factors:
        picked = {str(k).strip() for k in factors if str(k).strip()}
        unknown = sorted(picked - known)
        if unknown:
            raise ToolError(f"`factors` 里有不认识的因素：{unknown} —— 可用的是 {sorted(known)}"
                            "（**一个属性都没写**）。")
        spec_all = {k: v for k, v in spec_all.items() if k in picked}
    if overrides:
        for k, kv in overrides.items():
            key = str(k).strip()
            if key not in known:
                raise ToolError(f"`overrides` 里有不认识的因素 `{key}` —— 可用的是 {sorted(known)}"
                                "（**一个属性都没写**）。")
            if not isinstance(kv, dict) or not kv:
                raise ToolError(f"`overrides[{key!r}]` 得是一个「属性 → 值」的对象，收到 {kv!r}"
                                "（**一个属性都没写**）。")
            entry = dict(spec_all.get(key) or {})
            # ⚠ **四个保留键**（2026-09-29 逐步补齐；后两个都是真跑踩出来的）：
            #   `{"fog": {"fogDensity": 0.03}}`            → 其余键都当**属性**盖（组件 / Actor 上的）
            #   `{"post_process": {"settings": {...}}}`    → 盖**后处理的 `settings` 字段**
            #   `{"local_fog": {"transform": {...}}}`      → 盖**摆放**（location_m / rotation / scale）
            #   `{"local_fog": {"props": {...}}}`          → 盖**属性**（**预设那三段的形状直接粘过来**）
            # 为什么必须有后三个：后处理在意的旋钮住在 `settings` 结构体里、**不是 Actor 属性**；
            # "临时挪到那儿看看"只能靠 `transform`；而**预设里就是 `props/settings/transform` 这个
            # 形状**，用户照着写再自然不过 —— 不认 `props` 就会把"props"当成**一个叫 props 的属性**
            # 去读，官方原文报 `could not be read: props`（实测踩过，一次调用白跑）。
            # ⚠ 本工具的因素里没有哪个属性真叫 `settings` / `transform` / `props`，所以这四个保留键
            #   不会误吞属性。
            rest = dict(kv)
            sub = rest.pop("settings", None)
            if isinstance(sub, dict) and sub:
                entry["settings"] = {**(entry.get("settings") or {}), **sub}
            tf = rest.pop("transform", None)
            if isinstance(tf, dict) and tf:
                entry["transform"] = {**(entry.get("transform") or {}), **tf}
            pr = rest.pop("props", None)
            if isinstance(pr, dict) and pr:
                entry["props"] = {**(entry.get("props") or {}), **pr}
            if rest:
                entry["props"] = {**(entry.get("props") or {}), **rest}
            spec_all[key] = entry
    if not spec_all:
        raise ToolError("这次没有任何因素要处理（`factors` 过滤后为空，或这一档预设是空的）—— "
                        "**一个属性都没写**。")

    # ③-c **执行顺序**：按整改版（`docs/阶段六-环境搭建.md`）的次序**稳定重排**。
    #   为什么在这一步重排（而不是在别处）：下面 ④ 找 Actor / 建 Actor 与 ⑤ 逐因素写，
    #   用的都是 `spec_all` 的**顺序** —— 在这一处排完，三处的次序就一起对齐了（单一入口）。
    #   ⚠ 顺序为什么归代码、`ENV_FACTOR_ORDER` 的出处与三条注意事项，全写在那个常量的说明里。
    _rank = {k: i for i, k in enumerate(ENV_FACTOR_ORDER)}
    _off = sorted(k for k in spec_all if k not in _rank)
    if _off:
        # **点名，不静默**：表外因素排最后，但必须让人知道它为什么排在最后。
        warns.append(
            f"⚠ 这些因素不在**执行顺序表** `ENV_FACTOR_ORDER` 里，已**排到最后**："
            f"{'、'.join(_off)}（顺序表：{'、'.join(ENV_FACTOR_ORDER)}）。"
            "要让某个因素插到中间，就在 `main.py` 的 `ENV_FACTOR_ORDER` 里补上它。")
    spec_all = dict(sorted(spec_all.items(), key=lambda kv: _rank.get(kv[0], len(_rank))))

    # ④ 找 Actor：默认**按类**找；调用方点名了（`actors`）就按名字/引用找
    hints = {k: str((factors_tbl.get(k) or {}).get("component") or "") for k in spec_all}
    refs, missing, notes, used = await _env_locate(
        ctx, {k: cls_of[k] for k in spec_all}, picks=picks)
    calls += used
    warns.extend(notes)

    # 回滚时**优先用台账里记的 Actor 引用**：同一个类有好几个时，按类找只会拿第一个，
    # 回滚就可能写到别人身上。台账里的引用还在就用它，不看"第一个是谁"。
    # ⚠ **但必须先验活**（2026-09-29 实测复现的 bug，用户拍板 A 案）：
    #   台账记的是**当时那个 Actor 的引用**（实测记的是 `PostProcessVolume_1`）—— 用户存盘 /
    #   重开关卡之后，我们建的那个卷会被重建并**换名**（实测现存的是 `PostProcessVolume_0`），
    #   于是台账里那个引用**指向一个不存在的对象**。
    #   改动前是"先信后不验"：直接把过期引用塞进 `refs`，还顺手 `missing.remove(k)` ——
    #   结果**既没回退到"按类找"，也永远不会走到 `spawn` 重建**，回滚当场失败
    #   （官方原文 `... is not valid Object for property 'instance'`，实测于
    #   `restore=true, dry_run=true`；这就是"回滚这条路从没真跑过"暴露出来的第一处洞）。
    #   现在：**`get_label` 验一下** —— 不活就当"没有它"，让按类找的结果 / `spawn` 兜底。
    for k, ref in ledger_refs.items():
        if k not in spec_all or not ref:
            continue
        calls += 1                              # 验活那一次官方调用（无论结果都算）
        try:
            alive = bool(await call_official(
                ctx, "get_label", {"actor": {"refPath": ref}}, toolset=TS_ACTOR))
        except ToolError:
            alive = False                       # 读不到 = 已经不在（或不是 Actor），一律当"不活"
        if not alive:
            warns.append(
                f"⚠ 台账里给 `{cn.get(k, k)}` 记的 Actor 引用 `{ref.rsplit('.', 1)[-1]}` "
                "**已经不在了**（存盘 / 重开关卡之后重建会换名）—— 这次**不用它**："
                + ("改用**按类找到的那个**。" if refs.get(k) else
                   "回退到按类找（找不到就按因素表的 `spawn` 规则处理）。"))
            continue
        if k in missing:
            missing.remove(k)
        refs[k] = ref

    # 回滚时：**我们自己建的** Actor 要**删掉**，不是"写回引擎默认值"（同上 · A 案的第二处洞）。
    # 为什么：台账里记的"动手前的值"是**我们建它时读到的引擎默认值** —— 那不是"原样"，是个空壳
    #   （实测：局部体积雾会退化成 `heightFogFalloff 1000` / `heightFogExtinction 1` ≈ 没有雾）。
    #   它动手前**根本不存在**，所以"退回动手前那一版"只能靠**删掉它**。
    # "哪些是我们建的"靠**分组**认、不靠名字 —— 与 `_env_spawn_actor` 同一条口径
    #   （实测：官方 `add_to_scene_from_class` 给的 `name` 不生效，Actor 名会随重建变）。
    ours: set[str] = set()
    if restore:
        try:
            calls += 1
            in_our_folder = await call_official(
                ctx, "get_actors_in_folder",
                {"folder_path": ENV_ACTOR_FOLDER, "recursive": True}, toolset=TS_SCENE)
            ours = {r for r in (_ref_path(a) for a in (in_our_folder or [])) if r}
        except ToolError as exc:
            # "文件夹不存在" = 我们没建过任何环境 Actor，正常
            if "does not exist" not in str(exc).lower():
                warns.append(
                    f"⚠ 回滚时读不到 `{ENV_ACTOR_FOLDER}/` 里有谁（{exc}）—— 这次**一个都不删**，"
                    "只用台账的值写回（我们自己建的那些会留下一个空壳）。")

    # 因素表里标了 `spawn` 的（实测：后处理卷关卡里 0 个）→ **没有就建一个**；演练时只说不动手
    spawned: list[str] = []
    # 回滚时"本来就不该在、所以不新建"的那几个（用来在 ⑤ 里**不报假失败**，见下）
    skipped_respawn: set[str] = set()
    for k in list(missing):
        row_tbl = factors_tbl.get(k) or {}
        if not row_tbl.get("spawn"):
            continue
        folder = str(row_tbl.get("folder") or ENV_ACTOR_FOLDER)
        # ⚠ **回滚时绝不新建**（2026-09-29 自查补的第三处，与上面两处洞同源）：
        #   `spawn: true` 的因素（后处理卷 / 局部体积雾）**在"我们动手前"根本不存在** ——
        #   回滚时若它已经不在（用户删了 / 没存盘重开），再 `spawn` 一个、把引擎默认值写进去，
        #   就是"退着退着又多留一个空壳"，与"退回原样"**正好相反**。
        #   所以回滚时这一类**什么都不做**（它本来就不该在）；若它还在关卡里，上面算 `ours`
        #   那段已经把它收进来，会在 ⑤ 里按分组**删掉**。
        if restore:
            missing.remove(k)
            refs.pop(k, None)
            skipped_respawn.add(k)
            warns.append(f"回滚：{cn[k]} 是**我们建的**（因素表里 `spawn: true`）—— 它动手前"
                         f"不存在，**这次不新建**；若关卡里还有我们建的那个，会按 "
                         f"`{ENV_ACTOR_FOLDER}/` 分组**删掉**。")
            continue
        if dry_run:
            missing.remove(k)
            refs[k] = ""
            warns.append(f"演练：{cn[k]} 关卡里没有 —— 真做时会**新建**一个并放进 `{folder}/`。")
            continue
        try:
            ref, s_used = await _env_spawn_actor(ctx, cls_of[k], folder,
                                                 label=f"{OUR_FOLDER_ROOT}_env_{k}")
            calls += s_used
            refs[k] = ref
            missing.remove(k)
            spawned.append(ref)
            warns.append(f"{cn[k]}：关卡里没有，**新建**了一个 `{ref.rsplit('.', 1)[-1]}`"
                         f"（放进 `{folder}/`）—— ⚠ 未存盘，不存就没了。")
        except ToolError as exc:
            calls += 1
            warns.append(f"⚠ {cn[k]} 没建成（{exc}）—— 这一项跳过。")
    for k in missing:
        warns.append(f"⚠ 关卡里**没有** {cn[k]} 的 Actor（`{cls_of[k]}`）—— 这一项跳过。"
                     "要配它得先在关卡里放一个（因素表里标了 `spawn: true` 的才会自动建）。")

    # ⑤ 逐因素：读现值 → 幂等 → 写 → 读回（有 `material` 段的，再走一遍自建 MI + 材质参数）
    rows: list[EnvRowResult] = []
    for key, spec in spec_all.items():
        if not refs.get(key):
            # 回滚 + "这一类本来就不该在、所以没新建" → **不是失败**：它现在不在 == 正合原样。
            # （报成 failed 就是一次**假失败** —— 与下面那条"演练不记 failed"同一个道理。）
            if key in skipped_respawn:
                rows.append(EnvRowResult(
                    factor=key, name_cn=cn[key],
                    status="dry" if dry_run else "already"))
                continue
            # 演练时**不记 failed**：什么都没失败，只是这个 Actor 不在（真做时会新建 / 跳过）。
            # 记成 failed 会让一次干跑报出假失败 —— 那是骗人。
            rows.append(EnvRowResult(
                factor=key, name_cn=cn[key],
                status="dry" if dry_run else "failed",
                error=("演练：关卡里没有承载它的 Actor —— 真做时会新建（标了 `spawn` 的）或跳过这一项"
                       if dry_run else
                       "关卡里没有承载它的 Actor —— 这一项跳过")))
            continue
        # 回滚时，**我们自己建的**那个 Actor（在 `UEMCP_env/` 下）→ **删掉它**，不写值。
        # 这一段就是 A 案第二处洞的修法，理由写在上面算 `ours` 的地方。
        if restore and refs[key] in ours:
            short = refs[key].rsplit(".", 1)[-1]
            if dry_run:
                warns.append(f"演练：{cn[key]} 的 `{short}` 是**我们自己建的**"
                             f"（在 `{ENV_ACTOR_FOLDER}/`）—— 真回滚时会**删掉**它，而不是写回值。")
                rows.append(EnvRowResult(
                    factor=key, name_cn=cn[key], actor=short, status="dry",
                    wrote={"_deleted": refs[key]}))
                continue
            calls += 1
            try:
                await call_official(ctx, "remove_from_scene",
                                    {"actor": {"refPath": refs[key]}}, toolset=TS_SCENE)
            except ToolError as exc:
                rows.append(EnvRowResult(
                    factor=key, name_cn=cn[key], actor=short, status="failed",
                    error=f"删掉这个**我们自己建的** Actor 失败：{exc}（**它还在关卡里**）"))
                continue
            warns.append(f"{cn[key]}：`{short}` 是**我们自己建的**（在 `{ENV_ACTOR_FOLDER}/`），"
                         "已**删掉** —— 那才是「我们动手前」的原样（动手前它不存在）。"
                         "⚠ **未存盘**：存不存由你定。")
            rows.append(EnvRowResult(
                factor=key, name_cn=cn[key], actor=short, status="applied",
                wrote={"_deleted": refs[key]}))
            continue
        try:
            row, used = await _env_apply_factor(
                ctx, key, cn[key], spec, refs[key], hints.get(key, ""), dry_run)
            calls += used
            mat_spec = spec.get("material") if isinstance(spec.get("material"), dict) else {}
            if mat_spec:
                m_before, m_after, m_wrote, m_errs, m_used, m_new = await _env_apply_material(
                    ctx, mat_spec, refs[key], hints.get(key, ""),
                    str((factors_tbl.get(key) or {}).get("material_prop") or ""), dry_run)
                calls += m_used
                if m_before or m_after or m_wrote:
                    row.before = {**row.before, "_material": m_before}
                    row.after = {**row.after, "_material": m_after}
                    # ⚠ `wrote` **只在真写了东西时才挂**（2026-09-29 实测复现的**如实性 bug**）：
                    #   以前无条件挂 `{"_material": m_wrote}`，于是"这一行本来就是对的值、一个属性都没写"
                    #   时，报文里会出现 `wrote: {"_material": {}}` —— 与"只报改动"的口径矛盾。
                    #   **现象是实测的**（证据 = 2026-09-29 那次 `setup_environment()` 真做：`cloud` 行
                    #   `status: already` / `property_count: 0`，`wrote` 却是 `{"_material": {}}`）。
                    #   **危害是读代码推断的（不是实测）**：`_env_ledger_save()` 的判据是 `not row.wrote`
                    #   —— `{}` 为假（该跳过）、`{"_material": {}}` **为真**（被当成"写了东西"），
                    #   于是"某因素**第一次**被处理时恰好全幂等"这种情况（台账被删 / 换张图之后第一次跑，
                    #   而我们的 MI 与参数都已经在）会往**回滚台账**记一条"我们改过这个因素"的**假记录**，
                    #   记下就按"只记第一次"再也纠正不了。⚠ 本工程现有台账里**看不出**它
                    #   （7 个因素都是在真写了东西的那次被合法记下的）—— 所以这条只能算推断。
                    if m_wrote:
                        row.wrote = {**row.wrote, "_material": m_wrote}
                    row.property_count += len(m_wrote)
                    # ⚠ **状态要跟着改**（2026-09-29 真跑抓到的**如实性 bug**）：材质段是在
                    #   `_env_apply_factor` **返回之后**才写的 —— 那时行状态可能已经是 `already`
                    #   （这个因素自己的属性本来就对），而材质段**确实写了东西**。不改状态就会出现
                    #   "`wrote` 里有两条、状态却说 `already`、报文总结说 0 项写入" 这种自相矛盾的报文。
                    if m_wrote and not dry_run and row.status in ("already", "dry"):
                        row.status = "applied"
                warns.extend(m_errs)
                if m_errs and any("失败" in e or "读回不是它" in e for e in m_errs):
                    if row.status in ("applied", "already", "dry"):
                        row.status = "failed"
                    row.error = (row.error + "；" if row.error else "") + "；".join(m_errs)
        except ToolError as exc:
            row = EnvRowResult(factor=key, name_cn=cn[key], status="failed", error=str(exc))
        rows.append(row)

    applied = sum(1 for r in rows if r.status == "applied")
    already = sum(1 for r in rows if r.status == "already")
    failed = sum(1 for r in rows if r.status == "failed")
    # 回滚时"**删掉**我们自建的 Actor"那几项也记在 `wrote` 里（键 `_deleted`）——
    # 单独数出来，免得报文里把"删了一个 Actor"和"写回一批属性"混成同一个数字。
    deleted = sum(1 for r in rows if isinstance(r.wrote, dict) and "_deleted" in r.wrote)
    if failed:
        warns.append(f"⚠ 有 {failed} 个因素**没成** —— 逐条原因在 `rows` 里（不掩饰）。")

    ledger_path = ""
    if not dry_run:
        try:
            ledger_path = _env_ledger_save(level, chosen or "(回滚)", rows, refs)
        except OSError as exc:
            warns.append(f"⚠ 环境台账没记上（{exc}）—— 环境已改，但**回滚依据没落盘**，"
                         "要退回原样得手工在 UE 里改。")
    if dry_run:
        warns.append("演练：**一个属性都没写**。要真配就去掉 `dry_run` 重调。")
    else:
        warns.append("**全程未存盘** —— 配完的样子在 UE 里看，存不存由你定。")
        if not restore:
            warns.append("想退回我们动手前那一版：`setup_environment(restore=true)`。")

    if dry_run:
        # ⚠ 判据是 **`r.wrote` 非空**，不是 `r.status == "dry"`（2026-09-29 实测暴露的口径错）：
        #   演练里有三类 `dry` 行，但**只有前两类真会改**：
        #     · 有 `wrote` 的（要写属性 / 要删我们自建的 Actor）→ **会改** ✓
        #     · `skipped_respawn` 的（回滚 + 这东西本来就不该在）→ **什么都不做** ✗
        #     · "关卡里没有承载它的 Actor" 那类（真做时按 `spawn` 新建或跳过，warnings 里已单独说明）✗
        #   改动前一律按 `dry` 数，于是出现过"演练：这次会改 2 个因素（局部体积雾、后处理）"——
        #   可那两项真跑**什么都不会做**。计数不准 = 报文在骗人，哪怕只骗两个数。
        todo = [cn.get(r.factor, r.factor) for r in rows if r.wrote]
        next_step = (f"演练：这次会改 {len(todo)} 个因素（{'、'.join(todo) or '一个都没有'}）。"
                     "要真做就去掉 `dry_run` 重调。")
    elif restore:
        next_step = (f"回滚完成：{applied} 项**动了**（写回 / 删除）、{already} 项本来就一样、"
                     f"{failed} 项没成"
                     + (f"；其中 **{deleted} 个 Actor 是删掉的**（我们自己建的，动手前不存在）"
                        if deleted else "")
                     + "。在 UE 里看一眼是否回到原样。⚠ 存不存盘由你定。")
    else:
        next_step = (f"环境配好了：{applied} 项写入、{already} 项本来就一样、{failed} 项没成"
                     f"（预设 `{chosen}`）。**请在 UE 里自己看** —— 本阶段**不出图**；不满意就改 "
                     "`config/environments.json` 再跑一次（不用改代码、不用重启）。"
                     "⚠ 出图（`capture_preview()`）归**阶段七**，不属本阶段。")

    return EnvReport(
        stage="阶段六 · 环境搭建（灯光 ＋ 天空/大气/天光/雾/云/后处理/时段）",
        level=level, preset=chosen or "(回滚)",
        preset_source=str(ENV_CONFIG_PATH) if not src_note else "内置兜底（配置没读到，见 warnings）",
        dry_run=dry_run, restore=restore, planned=len(spec_all),
        applied=applied, already=already, failed=failed, spawned=spawned,
        ledger_path=ledger_path, rows=rows, official_calls=calls,
        next_step=next_step, warnings=warns,
    )


# --- [完工-17] 工具 18：阶段七 · 相机预览（⚠ 2026-09-29 在阶段六期间做出来，已划归阶段七）------  【模块：workflow】


@mcp.tool()
async def capture_preview(
    ctx: Context[AppContext],
    shots: Annotated[list[PreviewShot] | None, Field(
        description=("机位列表：`pos_m`（站哪儿）/ `look_at_m`（看哪儿）/ `fov`，**都是米**、世界坐标"
                     "（与 `plan_v1.json` 同一口径）。留空 = 按 plan 的世界范围自动出三个"
                     "（俯视 3/4 + 两端人视）"))] = None,
    annotations: Annotated[bool, Field(
        description=("true = 图上叠**世界网格 + Actor 标签**（给我核对构图用）；"
                     "false（默认）= 干净图（给你看效果用）"))] = False,
    dry_run: Annotated[bool, Field(
        description="true = 只算机位（站哪儿 / 朝哪 / 会写成哪个文件），**一张都不拍**")] = False,
) -> PreviewReport:
    """**阶段七 · 相机预览**（⚠ 原挂在阶段六，2026-09-29 用户把出图收回后划给阶段七）：
    按**世界坐标**摆机位出图，构图**不靠图像识别**。

    做四件事：
      ① 机位：调用方给了就用给的（`pos_m` / `look_at_m`，**米**）；没给就按 `plan_v1.json` 的
         世界范围**推三个**（俯视 3/4 + 两端人视）；
      ② 由「站哪儿 + 看哪儿」**算**出 UE 的 `pitch/yaw`（推导口径见 `_preview_rotation`）；
      ③ 官方 `CaptureViewport` 出图 —— 用 `captureTransform` 指定机位（实测**不动你的视口相机**）；
      ④ PNG 落盘到 `views/preview/`，返回**路径 + 官方回报的机位**。

    ⚠ 图**不回传给模型**（实测三张干净图 0.87 / 0.94 / 1.07 MB PNG）—— 要看图让Agent读那个文件。
    ⚠ `annotations=true` 那档会叠世界网格 + Actor 标签：官方实测能标出 12 个（顶到上限），
      那是「这一帧到底框住了谁」的证据。
    ⚠ **两条别误会**（2026-09-29 实测）：① 官方**画不出中文** —— 标签在图上渲染成乱码（JSON 里
      回报的字符串是对的，是 UE 侧字体没有中文字形），**十字准星与引线仍有效**，别拿它认名字；
      ② `bShowUI=false` **挡不住**编辑器角落的坐标轴指示与 `PlayerStart` 的图标（两张干净图里都在）。
    ⚠ **不存盘、不改关卡**（只读式子图 + 只写我们自己的 PNG）。
    """
    calls = 0
    warns: list[str] = []
    level = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
    calls += 1

    raw_shots: list[dict] = []
    if shots:
        for s in shots:
            item = s if isinstance(s, dict) else s.model_dump()
            raw_shots.append({
                "name": str(item.get("name") or ""),
                "pos_m": [float(x) for x in (item.get("pos_m") or [])],
                "look_at_m": [float(x) for x in (item.get("look_at_m") or [])],
                "fov": float(item.get("fov") or 0.0),
            })
        pose_source = f"调用方给的 {len(raw_shots)} 个机位"
    else:
        planning = _planning_modules()
        if not planning.OUT_JSON.exists():
            raise ToolError(
                f"没有 `{planning.OUT_JSON.name}` —— 自动机位要靠它的**世界范围**"
                "（`world.center` / `world.size`，米）来推。要么先跑阶段二（`generate_plan`），"
                "要么自己给 `shots`（`pos_m` / `look_at_m`，**米**）。")
        plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
        raw_shots = _preview_default_shots(plan if isinstance(plan, dict) else {})
        pose_source = "按 plan 的世界范围推的 3 个机位（俯视 3/4 + 两端人视）"
        warns.append("机位是**推的**（高度 / 退距按世界尺寸的比例给）—— 构图不满意就自己给 `shots`。")

    bad = [i for i, s in enumerate(raw_shots)
           if len(s["pos_m"]) != 3 or len(s["look_at_m"]) != 3]
    if bad:
        raise ToolError(
            "有机位的 `pos_m` / `look_at_m` 不是三个数（`[X, Y, Z]`，**米**）："
            + "、".join(f"#{i + 1} {raw_shots[i].get('name') or ''}" for i in bad)
            + " —— **一张都没拍**。")

    if dry_run:
        plan_rows: list[PreviewShotResult] = []
        for i, s in enumerate(raw_shots):
            nm = str(s["name"] or f"shot_{i + 1}")
            rot = _preview_rotation(s["pos_m"], s["look_at_m"])
            plan_rows.append(PreviewShotResult(
                name=nm,
                file=str(PREVIEW_DIR / f"{_safe_file_stem(nm)}.png"),
                camera_location=[round(x * 100.0, 2) for x in s["pos_m"]],
                camera_rotation=[round(float(rot[k]), 3) for k in ("pitch", "yaw", "roll")],
                fov=float(s["fov"] or 0.0), status="dry"))
        return PreviewReport(
            stage="阶段七 · 相机预览", level=level, pose_source=pose_source,
            planned=len(plan_rows), ok=0, failed=0, shots=plan_rows, official_calls=calls,
            next_step=f"演练：会拍 {len(plan_rows)} 张、落盘到 `{PREVIEW_DIR}`。"
                      "要真拍就去掉 `dry_run` 重调。",
            warnings=warns + ["演练：**一张都没拍**。"])

    results: list[PreviewShotResult] = []
    for i, s in enumerate(raw_shots):
        res, used = await _preview_one(ctx, s, annotations, i)
        calls += used
        results.append(res)
    ok = sum(1 for r in results if r.status == "ok")
    failed = len(results) - ok
    if failed:
        warns.append(f"⚠ 有 {failed} 张**没拍成** —— 逐条原因在 `shots` 里（不掩饰）。")
    flagged = [r.name for r in results if r.status == "ok" and r.error]
    if flagged:
        warns.append("⚠ 图拍出来了但**机位对不上**（官方回报 ≠ 要的）：" + "、".join(flagged)
                     + " —— 看 `shots[].error`。")
    if annotations:
        blank = [r.name for r in results if r.status == "ok" and not r.labeled_actors]
        if blank:
            warns.append("⚠ 这几张**一个 Actor 标签都没框到**：" + "、".join(blank)
                         + " —— 要么机位偏了，要么场景不在这个方向（标签只画可见的）。")
    warns.append("**未存盘、未改关卡** —— 图在 `views/preview/` 下（PNG）；"
                 "图**没回传给模型**（实测 0.87~1.07 MB/张），要看图就读那个文件。")

    return PreviewReport(
        stage="阶段七 · 相机预览", level=level, pose_source=pose_source,
        planned=len(raw_shots), ok=ok, failed=failed, shots=results, official_calls=calls,
        next_step=(f"出了 {ok} 张图（落盘 `{PREVIEW_DIR}`）。看图判断环境配得对不对 —— "
                   "不对就改 `config/environments.json` 再 `setup_environment()` 重配，"
                   "或换 `shots` 换个机位重拍。"),
        warnings=warns,
    )


# --- [完工-18] 工具 19：阶段七 · 评估与闭环迭代 —— 落位对账（`evaluate_layout`）---------------  【模块：workflow】
# ⚠ **定位**（用户 2026-09-30 在选项里选的 A）：只加「评估」这一个工具，回答一句话 ——
#   「**现在关卡里的东西，跟当初确认的那一版还对得上吗**」。
#   对的是三份东西：
#     ① `views/plan_v1.json`        = 确认过的放置表（权威几何，米）；
#     ② `views/build_state_v1.json` = 阶段三搭建台账（上次到底搭了什么 + Actor 引用）；
#     ③ **关卡现状**                = 官方读回来的（变换 / 白膜组件尺寸 / 材质覆盖 / 分组）。
# ⚠ **只读**：不碰关卡、不存盘、不改 plan；唯一写的是我们自己的报告 `views/evaluate_v1.json`。
# ⚠ **闭环不新造工具**（这是我给用户的反驳意见，他采纳了）：`request_plan_change`（记用户要改什么）
#   → `generate_plan(patch=…)` → **重画图** → `confirm_plan`（用户看图像点头）→
#   `execute_build()`（增量只重摆差异行）—— 这条链**已经通了**，本工具只负责把差异说清、
#   并把它写进 `next_step`。再造一个「一键闭环」工具，等于把「**用户看图点头**」那道闸
#   从中间绕过去 —— **不做**。
# ⚠ **判据一律复用现有那几处**，绝不另造一套（两套口径 = 两处会说不同的话；阶段二那 11 项
#   自检就是这么废掉的）：`_compose_build_rows()`（plan → 指令行）、`_row_changes()`（plan vs 台账）、
#   `_live_vs_ledger()`（台账 vs 关卡）、`_surface_matches()` / `_surface_read_row()`（材质）。
# ✅ **实测 2026-09-30T05:54:54Z（`evaluate_layout()` 首次真跑，UE 在线）**：
#   关卡 `/Game/demo/Landscape/MCP`（台账记的是同一个）；plan 指纹 `7ab0bdeccc…`（已确认）
#   = 台账指纹（`ledger_stale=false`）；`level_checked=true`；报告落 `views/evaluate_v1.json`；
#   `official_calls` 26。7 行 → `ok` 4 / `material_missing` 3；`drifted` / `never_built` /
#   `plan_changed` / `edited` **全 0**；5 个分组（UEMCP/ground·houses·road·shrubs·trees）
#   应到 = 实到、`stray_actors` 0。那 3 行 `material_missing` 是**真实差异**（三行白膜组件上
#   没有材质覆盖，阶段五还没跑），不是本工具的毛病 —— 它的 `next_step` 也照着报了 `apply_surfaces()`。
# ⚠ **仍未实测的两条**（这次没验到，别当成已验）：① UE 连不上时"只出 `plan ↔ 台账` 那半 +
#   在 `warnings` 里点名关卡那一维没查"；② "台账记的关卡 ≠ 当前关卡 → 关卡那一维整段跳过"。
#   这两条都要等一次真断连、或换一张图才验得了。


_VERDICT_CN: dict[str, str] = {
    "ok": "三方一致",
    "never_built": "台账里没有这一行（没搭过）",
    "plan_changed": "plan 改过、没重搭",
    "drifted": "台账说在、关卡里没了",
    "edited": "关卡现状与台账不符（人工改过？）",
    "material_missing": "材质对不上 / 没贴",
    "material_unreadable": "材质读不到（不判没贴）",
    "unchecked": "没比成（缺依据）",
}
"""每种结论给人看的中文名（报告的正文与清单都用它，免得读的人自己去猜英文键）。"""

_VERDICT_ORDER = ["never_built", "drifted", "plan_changed", "edited",
                  "material_missing", "material_unreadable", "unchecked", "ok"]
"""抬头结论的**优先级**（一行可能同时命中几条，取这里排最前的那个当抬头）。

排序依据 = **要动手的紧迫度**：没搭过 / 关卡里没了 → 得重摆；plan 改过没重搭 → 得重摆；
人工改过 → 得先跟用户确认；材质 → 单独一步；读不到 / 没比成 → 只是"没查到"，不算问题。
⚠ `ok` 永远在最后：它是"没命中任何一条"的兜底，不是"检查通过"的合格证。"""


async def _surface_read_row(
    ctx: Context[AppContext], actor_ref: str
) -> tuple[str | None, str, int]:
    """**只读**读一个白膜 Actor 上 `cube` 组件现在贴的是哪块材质 → `(材质 或 None, 说明, 官方调用次数)`。

    与阶段五那个 `_surface_apply_row()`（**会写**的那个）的区别：这里**一个字节都不写**。
    ⚠ 三种返回要分清楚（**别把「读不到」说成「没贴」**）：
      · `("", "", n)`        —— 读到了，组件上 `overrideMaterials` **是空的**（= 没贴）；
      · `("<对象路径>", "", n)` —— 读到了，现在贴的就是这块；
      · `(None, "为什么", n)` —— **读不到**（没有 `cube` 组件 / 属性读不动 / 不是合法 JSON），
        **不下"没贴"的结论**。
    """
    used = 1
    try:
        comps = await call_official(
            ctx, "get_components", {"actor": {"refPath": actor_ref}}, toolset=TS_ACTOR)
    except ToolError as exc:
        return None, f"读组件失败（{exc}）", used
    cube = ""
    for item in (comps or []):
        ref = _ref_path(item)
        if ref and ref.rsplit(".", 1)[-1] == "cube":        # `add_cube` 时给组件起的名字
            cube = ref
            break
    if not cube:
        return None, "这个 Actor 上没有名为 `cube` 的组件", used
    used += 1
    try:
        raw = await call_official(ctx, "get_properties", {
            "instance": {"refPath": cube}, "properties": ["overrideMaterials"]},
            toolset=TS_OBJECT)
    except ToolError as exc:
        return None, f"读 `overrideMaterials` 失败（{exc}）", used
    doc = raw
    if isinstance(doc, str):                                # 官方把属性包成 JSON 字符串（实测）
        try:
            doc = json.loads(doc)
        except (ValueError, TypeError):
            return None, "`overrideMaterials` 读回来的不是合法 JSON", used
    arr = doc.get("overrideMaterials") if isinstance(doc, dict) else None
    if not isinstance(arr, list) or not arr:
        return "", "", used
    first = arr[0]
    got = str((first or {}).get("refPath") or "") if isinstance(first, dict) else str(first)
    return got, "", used


@mcp.tool()
async def evaluate_layout(
    ctx: Context[AppContext],
    include_materials: Annotated[bool, Field(
        description=("true（默认）= 白膜行再读一次组件的 `overrideMaterials`，比 "
                     "`config/surface_materials.json` 里那张表（**每行约 2 次官方调用**）；"
                     "false = 跳过这一维（省调用，但**材质没贴这类问题就查不出来**）"))] = True,
    all_rows: Annotated[bool, Field(
        description="true = 连**一致**的行也列出来（默认只列不一致的；落盘的报告里两种都全）")] = False,
    dry_run: Annotated[bool, Field(
        description="true = 只算、**不落盘报告**（`views/evaluate_v1.json` 不写）")] = False,
) -> EvaluateReport:
    """**阶段七 · 落位对账**（只读）：`plan` ↔ 搭建台账 ↔ **关卡现状** 三方逐行比，把差异说清。

    回答的问题就一句：**「现在关卡里的东西，跟当初确认的那一版还对得上吗」**。

    比五样（每一样的判据都**复用现有那几处**，不另造一套）：
      ① **行**：plan 的每一行（一物一行，`uid = element_key|label`）在台账里有没有（没有 = 没搭过）；
      ② **plan ↔ 台账**：这一版改过 plan 却还没重搭（`_row_changes`，**离线就能比**）；
      ③ **台账 ↔ 关卡**：位置 / 朝向 / 缩放 / **白膜厚薄**（`_live_vs_ledger`，**要 UE 在线**）；
      ④ **分组**：每个 `UEMCP/<类>` 分组该有几个、实际几个、谁缺谁多（分组丢了 = 下次增量会拒收）；
      ⑤ **材质**（`include_materials=true` 时）：白膜行现在贴的是不是材质表里那块。

    ⚠ **只读**：不碰关卡、不存盘、不改 plan —— 唯一写的是我们自己的报告 `views/evaluate_v1.json`。
    ⚠ **UE 连不上时不报错**，而是**只出 `plan ↔ 台账` 那半**，并在 `warnings` 里点名
      「关卡那一维这次没查」—— **不许把「没报」当成「没问题」**。
    ⚠ **它只报数、不判合格不合格，也不改任何东西**（判据错的时候，绿灯比红灯更坏 ——
      阶段二那 11 项自检就是因为替用户做决定被整段删掉的）。
    ⚠ **闭环**（查出来有差异怎么办）**不靠新工具**：`request_plan_change`（记用户原话）→
      `generate_plan(patch=…)` → **重画图** → `confirm_plan`（用户看图点头）→
      `execute_build()`（增量只重摆差异行）。`next_step` 会按这次查到的差异指出该走哪条。
    """
    calls = 0
    warns: list[str] = []
    planning = _planning_modules()
    if not planning.OUT_JSON.exists():
        raise ToolError(
            "还没有 `views/plan_v1.json` —— 落位对账对的是**阶段二确认过的放置表**。"
            "先走完阶段二（`generate_plan` → 出图 → 用户确认 → `confirm_plan`）。")

    plan = json.loads(planning.OUT_JSON.read_text(encoding="utf-8-sig"))
    plan_hash = planning.plan_geometry_hash(plan)
    gate = planning.gate_check(plan)
    if not gate.get("confirmed"):
        warns.append("⚠ 这一版规划**还没确认过**（阶段二闸门是 false）—— 下面比的是**草稿**，只作参考。")

    rows, _z_rules, compose_warns = _compose_build_rows(plan, load_json(ASSET_LIST_PATH) or {})
    warns.extend(compose_warns)         # 含「五层顺序」「白膜厚度兜底」「越界体检」那几条（同一份口径）

    ledger = _load_build_state()
    ledger_rows = [x for x in (ledger.get("rows") or []) if isinstance(x, dict)]
    ledger_by_uid = {str(x.get("uid") or ""): x for x in ledger_rows if str(x.get("uid") or "")}
    ledger_level = str(ledger.get("level") or "")
    ledger_hash = str(ledger.get("plan_hash") or "")
    ledger_stale = bool(ledger_hash) and ledger_hash != plan_hash
    if not ledger_rows:
        warns.append(
            f"⚠ **没有搭建台账**（`{BUILD_STATE_PATH.name}`）—— 「上次搭了什么」就没有凭据，"
            "**每一行都判不了**（结论一律 `unchecked`）。台账由 `execute_build()` 落完自动写；"
            "关卡里其实已经搭好了、只是没有台账 → 用 "
            "`execute_build(mode=\"incremental\", adopt=true)` 认领现状。")
    elif ledger_stale:
        warns.append(
            f"⚠ **台账比 plan 旧**：台账记的几何指纹是 `{ledger_hash[:10]}…`，当前 plan 是 "
            f"`{plan_hash[:10]}…` —— 说明这一版 plan 改过、**还没重搭**（`plan_changed` 那几行就是差异）。")

    # ---------- 关卡那一维：连不上就**只出离线那半**（如实报，不假装）----------
    level = ""
    level_checked = False
    live_refs: list[str] = []
    live_set: set[str] = set()
    manual_by_uid: dict[str, dict] = {}
    groups: dict[str, dict] = {}
    stray_actors: list[str] = []
    try:
        level = str(await call_official(ctx, "get_current_level", {}, toolset=TS_SCENE) or "")
        calls += 1
        level_checked = True
    except ToolError as exc:
        warns.append(
            f"⚠ **官方链路不通，关卡那一维这次没查**（原文：{exc}）—— 下面只有 `plan ↔ 台账` 那半；"
            "**变换 / 白膜厚薄 / 分组 / 材质 / 人工痕迹一个都没查**，"
            "别把「这次没报」当成「没问题」。")

    if level_checked and ledger_level and level and level != ledger_level:
        warns.append(
            f"⚠ 台账记的关卡是 `{ledger_level}`，当前是 `{level}` —— **不是同一张图**，"
            "台账里的 Actor 引用在这儿没有意义：**关卡那一维整段跳过**（只出 `plan ↔ 台账`）。"
            "要在这张图上对账，先用 `adopt=true` 把现状登记成新基线，或者切回台账那张图。")
        level_checked = False

    if level_checked:
        try:
            found = await call_official(
                ctx, "get_actors_in_folder",
                {"folder_path": OUR_FOLDER_ROOT, "recursive": True}, toolset=TS_SCENE)
            calls += 1
            live_refs = [r for r in (_ref_path(i) for i in (found or [])) if r]
            live_set = set(live_refs)
        except ToolError as exc:
            if "does not exist" not in str(exc).lower():
                warns.append(f"读 `{OUR_FOLDER_ROOT}/` 现有 Actor 失败（{exc}）—— 关卡那一维**不完整**。")

    if level_checked:
        # ③ 台账 ↔ 关卡：**全表扫**。这台工具就是「看清楚」的那一步；
        #    `execute_build()` 增量只扫"会动的行"是为了省调用，那是它的取舍，不是这里的。
        #    判据与容差全在 `_live_vs_ledger()` 里 —— 不在这里重写一遍。
        manual, used = await _live_vs_ledger(ctx, ledger_rows, live_set)
        calls += used
        manual_by_uid = {str(m.get("uid") or ""): m for m in manual if str(m.get("uid") or "")}

        # ④ 分组：每个涉及的分组读一次（**不递归**），比引用集合 ——
        #    「分组丢了 / 进错分组」只有这么查得出来（根递归清点只能证明"在 UEMCP/ 树下"）。
        want_by_folder: dict[str, set[str]] = {}
        label_of_ref: dict[str, str] = {}
        for r in rows:
            old = ledger_by_uid.get(r["uid"]) or {}
            ref = str(old.get("actor") or "")
            if ref:
                want_by_folder.setdefault(str(r["folder"]), set()).add(ref)
                label_of_ref[ref] = str(r["label"] or r["name"])
        for folder in sorted(want_by_folder):
            want = want_by_folder[folder]
            calls += 1
            try:
                got_items = await call_official(
                    ctx, "get_actors_in_folder",
                    {"folder_path": folder, "recursive": False}, toolset=TS_SCENE)
            except ToolError as exc:
                groups[folder] = {
                    "应到": len(want), "实到": 0,
                    "note": ("分组不存在（这一层还没搭过？）" if "does not exist" in str(exc).lower()
                             else f"读分组失败：{exc}")}
                continue
            have = {r for r in (_ref_path(i) for i in (got_items or [])) if r}
            missing = sorted(want - have)
            extra = sorted(have - want)
            groups[folder] = {
                "应到": len(want), "实到": len(have),
                "缺": [label_of_ref.get(x, x) for x in missing][:8],
                "多": [label_of_ref.get(x, x) for x in extra][:8],
            }

        # 关卡里**台账解释不了**的活 Actor（可能是有人手摆的）—— 判据与 `execute_build()` 一致：
        # 先按 label 试着认领（指令表里有这个名字 = 落成了却没登记），认不出才叫"解释不了"。
        known_refs = {str(x.get("actor") or "") for x in ledger_rows}
        name_to_row = {str(r["name"]): r for r in rows}
        for ref in live_refs:
            if ref in known_refs:
                continue
            calls += 1
            try:
                label = str(await call_official(
                    ctx, "get_label", {"actor": {"refPath": ref}}, toolset=TS_ACTOR) or "")
            except ToolError as exc:
                stray_actors.append(f"{ref}（读 label 失败：{exc}）")
                continue
            hit = name_to_row.get(label)
            if hit is None:
                stray_actors.append(
                    f"{ref}（label = `{label}`：**台账里没有它、指令表里也没有这个名字** —— "
                    "多半是有人手动摆的）")
            else:
                stray_actors.append(
                    f"{ref}（label = `{label}`：**指令表里有这一行、台账里却没有** —— "
                    "像是「落成了没登记」，跑一次 `execute_build()`（增量）就会把它补进台账）")

    # ---------- 逐行判决（抬头结论 + 全部原因）----------
    every: list[LayoutRowVerdict] = []
    counts: dict[str, int] = {}
    for r in rows:
        uid = str(r["uid"])
        old = ledger_by_uid.get(uid)
        actor = str((old or {}).get("actor") or "")
        v: list[str] = []
        why: list[str] = []
        found_vals: dict[str, Any] = {}
        if old is None:
            if ledger_rows:
                v.append("never_built")
                why.append("台账里没有这一行 —— 这一版**还没搭过**（或搭完之后 plan 又加了这一行）")
            else:
                v.append("unchecked")
                why.append("没有搭建台账 —— 这一行**没比成**")
        else:
            chg = _row_changes(r, old)          # plan ↔ 台账（离线，永远比得了）
            if chg:
                v.append("plan_changed")
                why.extend(chg)
            if not level_checked:
                v.append("unchecked")
                why.append("关卡那一维这次没查（UE 连不上 / 台账不是这张图）—— "
                           "这一行**只比了 `plan ↔ 台账`**")
            elif not actor or actor not in live_set:
                v.append("drifted")
                why.append("台账记它在这个 Actor 上，**关卡里找不到它**（引用失效）—— 得重摆")
            else:
                m = manual_by_uid.get(uid)      # 台账 ↔ 关卡（人工痕迹 / 官方没照做）
                if m:
                    v.append("edited")
                    why.extend(list(m.get("why") or []))
                if include_materials and r["kind"] == "whitebox" and r.get("surface_material_hint"):
                    want_m = str(r["surface_material_hint"])
                    got_m, note, used = await _surface_read_row(ctx, actor)
                    calls += used
                    found_vals["material"] = got_m
                    if got_m is None:
                        v.append("material_unreadable")
                        why.append(f"材质读不到：{note}（**不判没贴**）")
                    elif not got_m:
                        v.append("material_missing")
                        why.append(f"组件上**没有材质覆盖** —— 材质表里给的是 `{want_m}`")
                    elif got_m != to_object_path(want_m):
                        v.append("material_missing")
                        why.append(f"贴的不是材质表里那块：现在 `{got_m}`，表里给的是 `{want_m}`")
        headline = next((k for k in _VERDICT_ORDER if k in v), "ok")
        counts[headline] = counts.get(headline, 0) + 1
        every.append(LayoutRowVerdict(
            uid=uid, label=str(r["label"]), kind=str(r["kind"]),
            element_key=str(r["element_key"]), layer=str(r.get("layer") or ""),
            folder=str(r["folder"]), actor=actor, verdict=headline, why=why,
            expected={"loc_cm": r["loc_cm"], "yaw": (r.get("rot") or {}).get("yaw"),
                      "scale": r["scale"], "size_cm": r.get("size_cm"), "folder": r["folder"]},
            ledger=({"loc_cm": old.get("loc_cm"), "yaw": old.get("yaw"), "scale": old.get("scale"),
                     "size_cm": old.get("size_cm"), "folder": old.get("folder"),
                     "readback": old.get("readback")} if old else {}),
            found=found_vals,
        ))
    for _k in _VERDICT_ORDER:
        counts.setdefault(_k, 0)
    shown = every if all_rows else [x for x in every if x.verdict != "ok"]
    bad = {k: counts.get(k, 0) for k in _VERDICT_ORDER if k != "ok" and counts.get(k, 0)}

    # ---------- 给人看的清单（使用正文；表只列前 30 行，明细在报告文件里）----------
    _kind_cn = {"asset": "资产", "whitebox": "白膜"}
    head = [
        "## 落位对账（阶段七 · 只读）",
        f"- 关卡：`{level or '（没读到）'}`　台账记的关卡：`{ledger_level or '（无）'}`"
        + ("　⚠ **不是同一张图**" if ledger_level and level and ledger_level != level else ""),
        f"- plan 指纹：`{plan_hash[:10]}…`（{'已确认' if gate.get('confirmed') else '**未确认**'}）　"
        f"台账指纹：`{ledger_hash[:10]}…`" if ledger_hash else
        f"- plan 指纹：`{plan_hash[:10]}…`（{'已确认' if gate.get('confirmed') else '**未确认**'}）　"
        "台账指纹：（没有台账）",
        f"- 计划 **{len(rows)}** 行 → 一致 **{counts.get('ok', 0)}**"
        + ("；" + "、".join(f"{_VERDICT_CN[k]} **{n}**" for k, n in bad.items())
           if bad else "；**没有差异**")
        + ("　⚠ **关卡那一维这次没查**" if not level_checked else ""),
    ]
    if shown:
        head.append("")
        head.append("| 行 | 类型 | 结论 | 为什么 |")
        head.append("|---|---|---|---|")
        for x in shown[:30]:
            head.append(f"| {x.label} | {_kind_cn.get(x.kind, x.kind)} | {_VERDICT_CN[x.verdict]} | "
                        + "；".join(x.why).replace("|", "｜")[:200] + " |")
        if len(shown) > 30:
            head.append(f"| … | | | 还有 **{len(shown) - 30}** 行没列全 —— 明细在 "
                        f"`{EVALUATE_PATH.name}` 里 |")
    _g_bad = {k: v for k, v in groups.items() if v.get("缺") or v.get("多") or v.get("note")}
    if _g_bad:
        head.append("")
        head.append("**分组**：" + "；".join(
            f"`{k}` 应到 {v.get('应到')} / 实到 {v.get('实到')}"
            + (f"，缺 {len(v['缺'])}（{'、'.join(v['缺'][:3])}…）" if v.get("缺") else "")
            + (f"，多 {len(v['多'])}（{'、'.join(v['多'][:3])}…）" if v.get("多") else "")
            + (f"，{v['note']}" if v.get("note") else "")
            for k, v in sorted(_g_bad.items())))
    if stray_actors:
        head.append("")
        head.append(f"**台账解释不了的活 Actor（{len(stray_actors)} 个）**：" + "；".join(stray_actors[:6])
                    + ("…" if len(stray_actors) > 6 else ""))
    deliverable = "\n".join(head)

    # ---------- 闭环：按查出来的差异**指出该走哪条路**（不新造工具）----------
    fixes: list[str] = []
    _re = counts.get("never_built", 0) + counts.get("plan_changed", 0) + counts.get("drifted", 0)
    if _re:
        fixes.append(
            f"**该重摆的 {_re} 行**（没搭过 {counts.get('never_built', 0)} / plan 改过 "
            f"{counts.get('plan_changed', 0)} / 关卡里没了 {counts.get('drifted', 0)}）："
            "`generate_build_orders()` → `execute_build()`（默认就是**增量**，只动这几行）")
    if counts.get("edited", 0):
        fixes.append(
            f"**有人改过的 {counts['edited']} 行**：两条路 —— ①**保住他的改动**（推荐）：把它写回阶段二"
            "（`request_plan_change(items=[…], by=…)` → `generate_plan(patch=[…])` → **重画图** → "
            "用户看图点头 → `confirm_plan`）再增量落；②**按 plan 覆盖**：跟用户确认过再 "
            "`execute_build(accept_user_edits=true)`")
    if counts.get("material_missing", 0):
        fixes.append(f"**材质**：`apply_surfaces()` 按 `config/surface_materials.json` 重贴"
                     f"（{counts['material_missing']} 行；已经是那块材质的行会幂等跳过）")
    if stray_actors:
        fixes.append(f"**{len(stray_actors)} 个台账解释不了的 Actor**：`adopt=true` 认领、"
                     "或在下次增量前清理掉（增量对账对不上会**拒收**）")
    if not fixes:
        fixes.append("**没有差异** —— 可以往下走：`capture_preview()` 出图看效果，"
                     "或改 `config/environments.json` 再 `setup_environment()` 调氛围")
    next_step = ("；".join(fixes) + "。　⚠ 本工具**没存盘、没改关卡、没改 plan**"
                 + ("（演练：报告也没落盘）" if dry_run else f"（报告：`{EVALUATE_PATH}`）"))

    report_path = ""
    if dry_run:
        warns.append("演练（`dry_run=true`）：**报告没落盘**，关卡与 plan 一个字都没动。")
    else:
        try:
            EVALUATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            save_json(EVALUATE_PATH, {
                "stage": "阶段七 · 落位对账（我们自己的报告；不碰关卡、不存盘、不改 plan）",
                "at": datetime.now(timezone.utc).isoformat(),
                "level": level, "level_checked": level_checked,
                "plan_hash": plan_hash, "plan_confirmed": bool(gate.get("confirmed")),
                "ledger_path": str(BUILD_STATE_PATH), "ledger_level": ledger_level,
                "ledger_plan_hash": ledger_hash, "ledger_stale": ledger_stale,
                "rows_total": len(rows), "counts": counts,
                "rows": [x.model_dump() for x in every],     # 报告里**全量**（含一致的行）
                "groups": groups, "stray_actors": stray_actors,
                "note": ("每一行的 `expected` = plan 现算的指令值，`ledger` = 搭建台账记的，"
                         "`found` = 关卡现读的（空 = 没读到，**不等于没问题**）。"
                         "`readback=false` 表示台账那几个数是**指令值**、没从关卡核实过。"),
            })
            report_path = str(EVALUATE_PATH)
        except OSError as exc:
            warns.append(f"⚠ 报告没落盘（{exc}）—— 对账结果只在这次报文里，不掩饰。")
    warns.append("**未存盘、未改关卡、未改 plan** —— 这是一台只读工具。")

    return EvaluateReport(
        stage="阶段七 · 评估与闭环迭代（落位对账）",
        level=level, plan_hash=plan_hash, plan_gate=_gate_model(gate),
        ledger_path=str(BUILD_STATE_PATH), ledger_level=ledger_level,
        ledger_plan_hash=ledger_hash, ledger_stale=ledger_stale,
        level_checked=level_checked, rows_total=len(rows), counts=counts,
        rows=shown, groups=groups, stray_actors=stray_actors,
        report_path=report_path, deliverable=deliverable,
        official_calls=calls, next_step=next_step, warnings=warns,
    )


# --- 启动区 -------------------------------------------------------------------  【模块：启动区（workflow）】
# 只有"直接运行这个文件"时才启动；被 mcp dev / mcp run / Agent导入时不启动。
if __name__ == "__main__":
    # 不带参数 = stdio（进程直连），由Agent负责启动这个进程
    mcp.run()
