# Copyright (c) 2026 ZHUPO233
# =============================================================================
# 阶段二 · 平面放置规划（纯 2D 几何，单位统一米）
#
# 【这个阶段产出什么】
#   一份**放置数据结构** views/plan_v1.json：平面世界中心/大小 + **每个物体一行**的
#   平面坐标与占地。**不是**一套写死的几何骨架 —— 图不同、地图不同，这份数据就不同。
#
# 【谁提供什么】
#   world.size / world.center … 从阶段一的元素清单里读（`catalog/elements.json` 的
#                                「地图大小」场景项）；调用方可以覆盖。
#   每个物体的 pos / footprint … **agent 自己规划**（这里只做校验与落盘，不替它摆）。
#   已有资产的占地大小 …… = 阶段一**实测**的包围盒 × scale（大小是定的）。
#   白膜的占地大小 ……… 必须在**放完已有资产之后**再估（先占位才知道剩哪儿）。
#
# 【为什么没有出图和参数文件】
#   2026-09-24 用户拍板：① 不用固定模板画图（图由 AI 自己拿这份数据画）；
#   ② 参数不再落盘成单独文件，而是**记进本数据的 `params` 段**（哪一版是拿什么算的）。
#
# 【坐标】UE 左手系 Z-up：X 前进、Y 右、Z 上；平面内**右法线 = (-dy, dx)**（唯一口径）
#
# 【怎么跑】纯离线，不碰 UE：
#     $env:PYTHONPATH='<仓库根>\src'
#     & "<仓库根>\.venv\Scripts\python.exe" -m mcp_server.planning.plan          # 算默认一版
#     & "<仓库根>\.venv\Scripts\python.exe" -m mcp_server.planning.plan --confirm "确认人"
# =============================================================================

# --- [完工-10] 阶段二几何 + 确认留痕 ---
# ⚠ 2026-09-25：**自检整段删除**（用户指令：「阶段二不要这个自检阶段了，跟它有关的都记得改」）——
#   `build_checks()` 与它那 11 项（含 ③④⑤ 碰撞类、⑥⑦ 元素归属类、⑧⑨⑩ 对账类、⑪ cube 高度）
#   全部拿掉；`plan_v1.json` 不再写 `checks`；CLI 不再打印自检；那个 `return 0 if not failed else 1`
#   的退出码也跟着没了。碰撞检测与布局修正**已整段删除**（2026-09-27 用户指令：不再单列阶段）。
#   下面这些 2026-09-23/24 的实测记录**如实保留**（它们记的是"当时那版有自检时验过什么"），
#   但**不再是当前行为的描述** —— 读的时候请对照上面这条。
# 旧记录：实测 2026-09-23：旧版（路网/地块骨架）自检 14/14 全过、确认留痕六条路径全过。
# 实测 2026-09-24（**check_tools 60 通过 / 0 失败**，用户喂回的原始输出）：
#   本文件被整段换成"放置表"之后，main.py 仍能起来、9 个工具全部注册成功
#   （check_tools 第一组就是在 import main.py 之后验工具面）；
#   阶段二那三个工具经 MCP 端到端调用：拒收路径验不过的行 / 拒收没写 shape 的白膜行，两条都绿。
# ⚠ 2026-09-24（四）整段换掉（用户新规格：只留元素放置，路网/地块/绿化区全由 agent 规划）。
#   旧版的多边形骨架算法（offset_polyline / band / lots / green_zones …）已随规格一起删除；
#   要恢复见 git 历史。
# ✅ 2026-09-24（六）实测补齐 —— 原先那两条「尚未实测」的分支，现在都有证据了：
#   ① 「空结构（status=initialized）落盘」：generate_plan() 不带参数 →
#      7 项自检 0 失败、world=[120,30]（读当次 elements.json 的『地图大小』场景项）、
#      两张表 []、gate.confirmed=false；get_plan() 复查 status=initialized。
#   ② 「拒收空规划（CLI 闸）」：`python -m mcp_server.planning.plan --confirm "验收探针"`
#      → 打印「拒绝确认：这是**初始化状态**…」、**退出码 4**；
#      随后 get_plan() 复查 plan_hash 与拒收前一致 —— 证明这次拒收**没写盘**。
#   ③ 「拒收空规划（MCP 闸，main.py 那条）」：confirm_plan("验收探针")
#      → ToolError「拒绝确认：这是**初始化状态**（`assets` / `whiteboxes` 都空…）」；
#      再用**同一份 payload** 重跑 generate_plan，几何指纹逐位回到探针前的值
#      （6674990729…），证明拒收无副作用、且 draft 可原样恢复。
# ✅ 2026-09-24（八）实测（新代码上线后经 MCP 调用观察到 —— 收尾清单 #1~#8 那一批）：
#   · 自检 **11 项 0 失败**在真数据上跑通（get_plan 返回 checks_total=11 / failed=0、
#     plan_hash=19cd2005a0…）：⑧⑨⑩ 阶段一↔阶段二对账三条、⑪ cube 高度都在链上；
#   · `write_plan()` 的**自动留档**生效：views/archive/ 里已有 6 份
#     `plan_v1_<UTC时间戳>_{draft|confirmed}.json`（其中 1 份是用户确认过的那版）；
#   · `clear_stale_figures()` 的**自动清图**生效：4 张旧图被移进 views/archive/，
#     views/ 里只剩当前那一张（且图内写有当前几何指纹）。
# ⚠ **仍未实测**：改完之后的 `confirm_plan`（MCP 与本文件 CLI 两条）**一次都没被调过** ——
#   它新加的"图不认数据就拒收"那条闸、确认时的留档与清图，等第一次真确认再补证据。

import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- 常量 ---------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
"""仓库根目录。本文件在 src/mcp_server/planning/ 下，往上三层即根。"""

VIEWS_DIR = REPO_ROOT / "views"
OUT_JSON = VIEWS_DIR / "plan_v1.json"
ARCHIVE_DIR = VIEWS_DIR / "archive"
"""每一版 plan_v1.json 的留档目录（2026-09-24 收尾清单 #4：确认版必须留得下来）。

⚠ 纪元 2 开了之后，`plan_v2.json` 的留档也进这里 —— 归档名带纪元前缀
（`plan_v1_<时间戳>_<confirmed|draft>.json` / `plan_v2_...`），两边**不会互相覆盖**。
"""

VIEW_TEMPLATE_PATH = Path(__file__).resolve().parent / "web" / "preview3d.html"
"""**阶段二那张 three.js 页的模板**（2026-10-11 用户原话：「整改2阶段，不出图，出three.js」）。

⚠⚠ **本常量在插件里会被 `init_unreal.py` 搬飞 —— 别拿它当模板路径用**（2026-10-11 实测）：
  插件入口的 `_relocate()` 判据是「凡在 `REPO_ROOT` 底下的 Path 常量整体搬到 `Saved/UEPLANE`」，
  而它把 `plan.py` 的 `REPO_ROOT` 搬到了 `Saved/UEPLANE`。实测（演示工程 `Saved/Logs/demo.log`，
  那次 23:18:34 插件加载）：
    `VIEW_TEMPLATE_PATH -> …/Saved/UEPLANE/Python/ueplane/planning/web/preview3d.html`
  ⚠ 路径一律写**正斜杠**：反斜杠紧跟 `U`（例如 `Saved` 后面跟 `UEPLANE` 那种写法）在非 raw
    字符串里是 **Unicode 转义**，会直接 `SyntaxError: truncated … escape`（2026-10-11 本文件
    真的这么躺了一次 —— `init_unreal.py` 文件头那条纪律就是这么来的；**连"警告它"的这行也算**）。
  —— 那文件**根本不存在**（模板在插件里，不在 Saved 里）⇒ 照它去读会报"模板不存在"。
  **这就是"新增 Path 常量"的坑**：仓库里（`src/mcp_server/planning/web/`）它是对的，
  只有插件环境会把 `REPO_ROOT` 搬走、连累它。

⇒ 所以模板路径一律**运行时**问出图器：`figure3d.default_template()`（它每次调用按 `__file__`
  现算同级 `web/`，仓库与插件两处都成立，而且**函数不是 Path 常量、`_relocate` 搬不动它**）
  —— 见 `_write_view()`。⚠ 出图器那条路上**没有一个**模块级路径常量：`DEFAULT_TEMPLATE` /
  `WEB_DIR` 就是这么被搬飞的（同一次实测），所以它们是函数。
  本常量**只留给仓库侧/CLI 看**，不作为出图的实际输入。
"""

PLAN_V2_PATH = VIEWS_DIR / "plan_v2.json"
"""**纪元 2 的 plan** —— 阶段三结束、用户确认之后开出来，接管后续所有阶段（四~八）。

【为什么要另起一份，而不是继续用 `plan_v1.json`】（2026-10-04 用户拍板「大换血」）
  `plan_v1.json` 带的是**阶段二那套确认**（一页 three.js + 用户原话）—— 那份确认的对象是
  「要搭成什么样」。搭完之后的所有改动（四 · 微调 / 五 表面材质 / 六 环境搭建 / 七 对账）
  都不该再逼着用户重出那一页（他要的是「改一行就说一行」，不是每次都走一遍看图闸）。
  于是阶段三一结束就**换纪元**：`plan_v1.json` **冻结只读**留档，`plan_v2.json` 从
  「旧 plan 那一刻的整表」起接管；之后改行走
  `request_plan_change` → `generate_plan(patch=…)` → `execute_build(only_labels=[…])`
  —— **不画图、不重确认**（依据是 `confirmation.user_quote` + `rows` 那套留痕）。

⚠ **别名口径只有一处**：`active_plan_path()` / `active_plan()` / `active_epoch()` ——
  读 plan 的地方一律走它们，**别在别处各拼一次路径**（两套口径迟早两处说不同的话，
  本项目最忌讳的就是这个）。
⚠ **几何指纹故意不变**：换纪元只改 `confirmation`（`plan_geometry_hash()` 排除它），
  所以 `plan_v2.json` 的指纹与 `plan_v1.json` **逐位相同** —— 现场对账报告
  （`views/evaluate_v1.json`）不用因为换纪元而重跑，`G15` / `G16` 那两道闸也不受影响。
"""

# 给用户看的那张图 —— **由代码在落盘时自动生成**（`figure3d.py` + `web/preview3d.html`），
# `figure_check()` 按这些模式找它。命名见 `_write_view()`：`views/<plan 名>_view.html`。
# ⚠ **2026-10-11 改：从"agent 手绘两张矢量图"变成"一张落盘自动生成的 three.js 页"**
#   （用户原话：「整改2阶段，不出图，出three.js」+「而且段我想彻底改为用three.js了」）。
#   旧的两路 glob（`plan_v1_overview*` / `plan_v1_elevation*`）**已删** ——
#   留着就是两套口径（判据认旧格式、实际产出 HTML ⇒ 状态永远卡在"等出图"）。
FIGURE_GLOBS = ("plan_*_view.html", "plan_*_view.htm")
"""**给用户看的那张 three.js 页**（可转 / 可缩 / 可点）—— 落盘时自动生成。

判据（`figure_verdicts()` 里那一份，全工程只此一处）：
  正文里有**当前几何指纹前 10 位** + **该覆盖的每一行 `label`** 都在。
⚠ 为什么还能"核验一页 HTML"：它是**文本**，指纹与行名都写在正文里（`json.dumps(..., ensure_ascii=False)`
  注入）—— 与当年核那张图的办法逐字相同。位图（png/jpg…）**一律不认账**（读不出文字）。
⚠ 前缀刻意是 `plan_*_view`：`_write_view()` 按 `<plan 名>_view.html` 命名
（`plan_v1.json` → `plan_v1_view.html`；纪元 2 → `plan_v2_view.html`），
**不写死 v1** —— 换了纪元照样命中。
"""

ELEMENTS_PATH = REPO_ROOT / "catalog" / "elements.json"
"""阶段一的元素清单 —— 本阶段从这里读「地图大小」和「有哪些元素」。"""

ASSET_LIST_V2_PATH = REPO_ROOT / "catalog" / "asset_list_v2.json"
"""**纪元 2 的资产清单**（2026-10-12 用户要求：资产表与位置表**同一种方式**进纪元 2）。

为什么要它（用户原话）：「把资产表和位置表一样的方式进纪元 2」：
  · **位置表**：纪元 1 = `plan_v1.json` → 换纪元时整表照抄成 `plan_v2.json`，v1 冻结只读；
  · **资产表**：以前**只有一份** `catalog/asset_list.json`，换纪元不碰它 ——
    这就出现一个不一致：**纪元 2 的位置表在改，可它引用的资产表还是阶段一那一份** ——
    阶段五 / 阶段四拿它查"这个资产还在不在 / 实测多大"时，读到的是**纪元 1 时代的清单**，
    而这中间资产库可能已经变过（本工程实测过一次：`/Game/Fab/Urban_Street_Pack`
    整个文件夹消失、材质实例 58 → 56）。
  ⇒ 现在与位置表**逐条同形**：换纪元时整表照抄成 `asset_list_v2.json`，
    v1 那份从此**冻结只读**（`main.py` 的写入闸），活动的那份走咽喉 `active_asset_list_path()`。

⚠ **判据只有"那个文件在不在"这一条**（与 `active_plan_path()` **逐字同形**）：
  `asset_list_v2.json` 在 ⇒ 就它；不在 ⇒ 纪元 1 的 `asset_list.json`。**没有"能不能解析"那一层**。
⚠ 与位置表唯一的差别：位置表的 v2 由 `promote_to_epoch2()` 写；资产表的 v2 也在那一步照抄
  （同一处、同一次动作）—— 两边的"纪元切换点"必须**同一个**，否则读的人会看到
  "位置表已是 v2、资产表还是 v1"这种半切换状态。
"""


ASSET_LIST_PATH = REPO_ROOT / "catalog" / "asset_list.json"
"""**纪元 1 的资产清单**（阶段一签字的那一份）。

⚠ **2026-10-14 新增这个常量，是为了掐掉两处「现拼路径」**（`active_asset_list_path()` 的
  else 支，与 `promote_to_epoch2()` 里那行）。它们此前写的是 `REPO_ROOT / "catalog" / …`
  —— **函数里现拼，`_relocate()` 搬不动它**。切场景（见 `ueplane/scene.py`）只把
  `catalog/` 与 `views/` 两条根指进场景目录，**`REPO_ROOT` 本身不动**（它同时挂着
  `config/`，那份是全工程一份）⇒ 现拼的那两处会指到**没人再写的老地方**，**静默读空** ——
  最坏那种错：报文说"清单在这儿"，读的人拿到空表。
⚠ 全工程**仅此两处**现拼（2026-10-14 逐个 grep 过）：本文件那两处；其余全是模块级常量，
  或者指向 `config/` 因此本就该留在原地（`surfaces.py` 的 `REPO_ROOT / "config" / …`）。
"""


def active_asset_list_path() -> Path:
    """**现在算数的那份资产清单是哪个文件** —— 全工程读写资产表的唯一咽喉（与 `active_plan_path()` 同形）。

    口径一句话：**`asset_list_v2.json` 在 ⇒ 就它一份；不在 ⇒ 纪元 1 的 `asset_list.json`**。
    """
    return ASSET_LIST_V2_PATH if ASSET_LIST_V2_PATH.exists() else ASSET_LIST_PATH


def active_asset_list() -> dict:
    """读活动那份资产清单（读不动按"没有"处理，返回空 dict —— 不拿另一份顶替）。"""
    doc = load_json(active_asset_list_path())
    return doc if isinstance(doc, dict) else {}

Point = tuple[float, float]
Poly = list[Point]

# 场景项（不是物体）：它们的 element_key 不许出现在放置表里
SCENE_KEYS = ("map_size", "time_of_day")
# 白膜形状：cube（体）/ plane（覆盖面）
WHITEBOX_SHAPES = ("cube", "plane")
# 白膜行的尺寸来源必须含这两个字（预估值不许伪装成实测值）
ESTIMATED_MARK = "预估"


def load_json(path: Path):
    """读 JSON；不存在或读不动 → None（第一次跑本来就没有，不是错误）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


# --- 纪元（epoch）与「活动 plan 的唯一咽喉」--------------------------------------  【模块：planning】
# 背景：阶段三一结束就**换纪元** —— 旧 plan / 旧台账冻结留档，新 plan + 新台账接管后续阶段。
# 于是"读哪一份 plan"成了一个**必须只有一处判据**的问题：凡是读 plan 的地方都调下面这三个，
# 谁都不许自己拼一次路径（拼歪了就是"两套口径说不同的话"，本项目已经为此吃过多次亏）。

def active_plan_path() -> Path:
    """**现在算数的那份 plan 是哪个文件** —— 全工程读写 plan 的唯一咽喉。

    口径一句话：**`plan_v2.json` 在 ⇒ 就它一份；不在 ⇒ 纪元 1 的 `plan_v1.json`**。

    ⚠ 判据只有**"那个文件在不在"**这一条 —— **没有"能不能解析"这一层**（2026-10-07 拆的就是它）。

    ⚠ **拆掉的是哪条**（用户原话：「进入纪元 2 后，V1 要不直接删除得了，以后增删改查都靠 V2」，
      以及「别结束了前三阶段后面又**失忆用 V1** 而忘记只允许使用 V2」）：
      原来这里写的是"v2 在、**且能解析** ⇒ v2；否则退回 `views/plan_v1.json`"。那条回退有两个害处：
      ① **"v2 被写坏"会静默退回 v1** —— 表现为"看起来没有规划"或"拿着一份旧表继续干"，
         **坏状态长得像好状态**（本项目最忌的一条）；
      ② 它给"失忆用 V1"留了一条路 —— 而 V1 是已冻结、只读的那一份，拿它当权威就是错的。
      **现在只要 `plan_v2.json` 在，就永远是它**，读不动也不拿任何东西顶替
      （`active_plan()` 按"没有规划"说话，各闸自己拒收、如实报）。

    ⚠ **保留的"v2 不在 ⇒ v1"不是回退，是纪元 1 的正常目标**：从零搭建时磁盘上本来就没有
      `plan_v2.json` —— 阶段二写的就是 `plan_v1.json`，阶段三搭的也是它，换纪元那一步
      （`execute_build(confirm_stage3=…)`）再把整表照抄进 v2。**把这一支也砍掉会砸掉从零搭建**：
      阶段二会把 plan 直接写进 `plan_v2.json` ⇒ 换纪元闸的出生条件①（"`plan_v2.json` 不存在"，
      `main.py::_promote_epoch2`）**永远不成立** ⇒ `confirm_stage3` 从此对新工程一律拒收。
      两句话的分界：**"v2 在 ⇒ 只认 v2"（防失忆用 V1）；"v2 不在 ⇒ 才轮到 v1"（从零搭建）**。
    """
    return PLAN_V2_PATH if PLAN_V2_PATH.exists() else OUT_JSON


def active_plan() -> dict:
    """读**当前算数的那份 plan**（`active_plan_path()` 指的那一份）→ dict。

    ⚠ **读不动按「没有」处理、不猜**：返回空 dict（调用方按"没有规划"说话）；
      不拿上一版、也不拿另一份顶替 —— 那正是硬规则 6「预估值不许伪装成实测值」的同一条纪律。
      ⚠ 2026-10-07 起**"v2 读不动"不再退回 v1**（那条回退已拆，见 `active_plan_path()`）。
    """
    doc = load_json(active_plan_path())
    return doc if isinstance(doc, dict) else {}


def active_epoch() -> int:
    """当前纪元：**`views/plan_v2.json` 在 = 2，不在 = 1**（与 `active_plan_path()` **同一条判据**）。

    ⚠ **不读文件里的任何标记**（`confirmation.epoch` 那种是留给人看的留痕，不是判据 —— 两处判据迟早分叉）。
    ⚠ 为什么用 `exists()` 而不是"`active_plan_path()` 指向谁"：**两份都不在**时咽喉会指向 v1 那条
      路径（见那里的说明）—— 而那种状态**就是纪元 1**（还没搭），`exists()` 给出的正是它；
      "v2 在"时两种算法又必然一致。⇒ 写成 `exists()` **少一层推理**，也不容易与咽喉分叉。
    """
    return 2 if PLAN_V2_PATH.exists() else 1


def _epoch_of_path(path: Path) -> int:
    """这个 plan **文件**属于哪个纪元（`plan_v2.json` ⇒ 2，其余 ⇒ 1）。

    只给**归档命名**用（`write_plan(target=…)` 靠它把 v1 / v2 的留档分开，免得互相覆盖）；
    对外要问"现在纪元几"请用 `active_epoch()`。
    """
    return 2 if Path(path) == PLAN_V2_PATH else 1


# --- 占地矩形（阶段二特有的"最小几何"）----------------------------------------

def rect_corners(pos: Point, footprint: tuple[float, float],
                 rot_deg: float = 0.0) -> Poly:
    """把一个物体的**占地矩形**算成四个角（逆时针）。

    占地的定义：以 `pos`（平面中心坐标）为中心，`footprint = (宽, 深)`，
    绕 Z 轴逆时针转 `rot_deg` 度。返回 `[左下, 右下, 右上, 左上]`（旋转后）。

    ⚠ 为什么中心化 + 单独一个 rot：agent 规划时改"位置"和改"朝向"是两件事，
      绑在一起会让"往右挪 2 m"这种修改变成要重算四个角 —— 那是给 agent 下绊子。
    """
    hw, hd = float(footprint[0]) / 2.0, float(footprint[1]) / 2.0
    a = math.radians(float(rot_deg))
    ca, sa = math.cos(a), math.sin(a)
    out: Poly = []
    for dx, dy in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
        out.append((pos[0] + dx * ca - dy * sa, pos[1] + dx * sa + dy * ca))
    return out


def rect_inside_bounds(corners: Poly, bounds: dict) -> bool:
    """四个角是不是都在世界边界内（含边界，留 1e-6 容差）。"""
    x0, y0 = bounds["min"]
    x1, y1 = bounds["max"]
    return all(x0 - 1e-6 <= p[0] <= x1 + 1e-6 and y0 - 1e-6 <= p[1] <= y1 + 1e-6
               for p in corners)


BOUNDS_TIERS: dict[str, str] = {
    # 「硬」实体：立起来的东西 —— **四角都要在界内**。它们不承担"铺满"的角色：
    #   房子压线 = 半截悬空；树的占地 = **树冠包围盒 × scale**，出界 = 树冠探到世界外。
    "house": "solid",
    "tree": "solid",
    "shrub": "solid",
    # 「软」铺装：**铺在世界地基上的面** —— 允许贴边，但**不许超出**（超出 = 有一段悬在地基外）。
    #   车行道 120 m 铺满街长、草坪带铺到边界，都是设计意图。
    "road": "paved",
    "sidewalk": "paved",
    "path": "paved",
    "grass": "paved",
    # 「豁免」边界元素：**它自己就是世界边界**（世界地基）—— 四角正好落在边界 = 合法。
    #   ⚠ 豁免的只有"压线"；**超出照样报**（地基给得比世界大，那就是真错）。
    "ground": "boundary",
}
"""越界判据的**分级表**（2026-09-27 用户要求：把"哪些大类不能越界"的判断写进检查里）。

依据（用户 2026-09-27 问「那些大类不能越界」时定下的口径）：**加了世界地基之后，
`world.bounds` 就等于地基的范围** —— 于是"越界"的物理含义是**跑到地基外面 = 悬空 / 穿帮**。
按这个含义分三级：

| 级 | 大类 | element_key | 判据 |
|---|---|---|---|
| **硬** | building / nature | `house` / `tree` / `shrub` | 四角必须在界内（实体，压线就悬空） |
| **软** | environment（铺装） | `road` / `sidewalk` / `path` / `grass` | 允许贴边、不许超出（它们铺在地基上） |
| **豁免** | 边界元素 | `ground` | 它自己就是边界，压线合法（超出仍报） |

⚠ **表里没有的 element_key 一律按「硬 · 实体」处理**（宁可严）：新元素（路灯 / 车 / 栅栏…）
   本来就该完整站在世界里。真要是"铺满型"的新元素，**往这张表里加一行**，别改默认值。
⚠ **不给植物开后门**（2026-09-27 我的判断）：树冠"探出去一点"看着自然，但一旦按类开口子，
   这条检查就没有一致判据了 —— 真要让树冠出界，正确做法是**把世界放大**，不是豁免某一类。"""

BOUNDS_TIER_CN: dict[str, str] = {
    "solid": "硬 · 实体（四角必须都在界内）",
    "paved": "软 · 铺装（允许贴边、不许超出）",
    "boundary": "豁免 · 边界元素（它自己就是世界边界，压线合法）",
}
"""三级的**中文名**（给人看的原文，报告里直接用）。"""

DEFAULT_BOUNDS_TIER = "solid"
"""表里没有的 element_key 归到哪一级 —— `solid`（最严）。别改这个默认值。"""


def bounds_check(plan: dict) -> dict:
    """**阶段二唯一的那条检查**：有没有物体超出世界边界（2026-09-27 用户指令加回）。

    为什么只有这一条（用户 2026-09-27 原话：「第二阶段得改一下，只有一个检查，是否有物体
      超出世界边界」）：原来那 11 项具名自检 2026-09-25 已整段删除（它们在替人做决定，
      把摆法带偏过）。这一条是**唯一**被要回来的 —— 它判的是**硬事实**（坐标 vs 世界边界），
      不评价摆法好坏。

    ⚠ **只报红、不拦**（用户 2026-09-27 选的）：越界不是错误，是"你得知道这件事"。
      所以本函数**不改数据、不抛错**，返回值交给调用方去 warn；
      `plan_v1.json` 里**不写 `checks` 字段** —— 写进去会改几何指纹，
      等于把用户上一次确认无谓地作废掉。

    ⚠ **分三级报**（2026-09-27 用户要求"关于你的判断进行完善"）：判据与理由见 `BOUNDS_TIERS`
      的说明 —— 硬（实体，四角必须在界内）/ 软（铺装，允许贴边、不许超出）/ 豁免（`ground`
      自己就是边界）。**表里没有的一律按硬的算**（宁可严）。

    ⚠ **判据把 `footprint_m` 当"物体自身的 [宽, 深]"用，并**按 `rot_deg` 把四角转过去**
      （2026-10-07 修）：它与 `_compose_build_rows()` 交给 cube 的 `size_cm` **同源**，
      `rot_deg` 就是那之后绕 Z 的 yaw ⇒ 世界占地 = 旋转后的那个矩形。
      ⚠ 原来这里**不叠 `rot_deg`**，理由是"plan 的 `params` 写明 footprint 是旋转之后的世界
      X / Y 占地，再叠一次就是转两遍" —— 那句**已核不成立**：本版 plan 的 `params` 里没有这句，
      而 AGENTS / 本文件都把 `footprint_m` 记作 `[宽, 深]`（物体自身的）。
      代价也实测出来了：**转过 90° 的行会假报越界**（22×70 的路 rot 90 ⇒ 假报 8.00 m）。

    返回 `{"ok", "total", "outside": [{"label","kind","element_key","tier","tier_cn","why","over_m"}],
    "by_tier", "criteria", "note"}` —— 纯读。
    """
    world = plan.get("world") or {}
    bounds = world.get("bounds") or {}
    lo, hi = bounds.get("min"), bounds.get("max")

    rows: list[tuple[str, dict]] = []
    for kind, key in (("asset", "assets"), ("whitebox", "whiteboxes")):
        for r in (plan.get(key) or []):
            if isinstance(r, dict):
                rows.append((kind, r))

    if not (isinstance(lo, (list, tuple)) and isinstance(hi, (list, tuple))
            and len(lo) >= 2 and len(hi) >= 2):
        return {"ok": True, "total": len(rows), "outside": [], "by_tier": {},
                "criteria": "", "note": "世界没有 bounds（空表初始化态 / 数据不完整）—— 没得可比。"}

    x0, y0, x1, y1 = float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])
    outside: list[dict] = []
    for kind, r in rows:
        pos = list(r.get("pos") or [0.0, 0.0])
        fp = list(r.get("footprint_m") or [0.0, 0.0])
        cx, cy = float(pos[0]), float(pos[1])
        w, d = float(fp[0]), float(fp[1])
        label = str(r.get("label") or r.get("element_key") or "(没名字)")
        element_key = str(r.get("element_key") or "")
        tier = BOUNDS_TIERS.get(element_key, DEFAULT_BOUNDS_TIER)

        why: list[str] = []
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            why.append(f"中心 ({cx:g}, {cy:g}) 已在界外")
        # ⚠ **2026-10-07 修：四角要按 `rot_deg` 转过去再比**。
        #   `footprint_m` 是 `[宽, 深]` —— **物体自身坐标系**里的尺寸（与 `_compose_build_rows()`
        #   交给 cube 的 `size_cm` 同源），`rot_deg` 是那之后绕 Z 的 yaw。
        #   原来这里硬写 `0.0`（= 当世界轴对齐尺寸用），于是**转过 90° 的行会假报越界**：
        #   实测「沥青车行道（主路）」22×70、rot 90 ⇒ 报「出界 8.00 m」，
        #   而它建成后世界占地是 **70×22**（沿 X 贯通 70 m、宽 22 m），**完全在界内**。
        #   ⚠ 那句"plan 的 params 写明 footprint 是旋转之后的世界 X/Y 占地"**已核不成立**：
        #     本版 plan 的 `params` 里没有这句（是旧版残留的说法），而 AGENTS / 本文件都写着
        #     `footprint_m` = `[宽, 深]`（物体自身的）—— 所以叠 rot 才是对的，不是"转两遍"。
        rot = float(r.get("rot_deg") or 0.0)
        corners = rect_corners((cx, cy), (w, d), rot)
        if not rect_inside_bounds(corners, bounds):
            if not why:
                why.append("占地四角出界（中心仍在界内）")
            else:
                why.append("占地四角也出界")
        if not why:
            continue

        over = 0.0
        for px, py in list(corners) + [(cx, cy)]:
            over = max(over, x0 - px, px - x1, y0 - py, py - y1, 0.0)
        why.append(f"越界 {over:.2f} m（占地 {w:g}×{d:g} m"
                   + (f"，已按 rot {rot:g}° 转" if abs(rot) > 1e-9 else "") + "）")
        outside.append({
            "label": label, "kind": kind, "element_key": element_key,
            "tier": tier, "tier_cn": BOUNDS_TIER_CN.get(tier, tier),
            "why": why, "over_m": round(over, 2),
        })

    # 排序：**先按级别（硬 → 软 → 豁免）、同级内按越界多少从大到小** —— 报告里最该看的排最前。
    _order = {"solid": 0, "paved": 1, "boundary": 2}
    outside.sort(key=lambda o: (_order.get(str(o.get("tier")), 9), -float(o["over_m"])))

    by_tier = {
        "solid": sum(1 for o in outside if o["tier"] == "solid"),
        "paved": sum(1 for o in outside if o["tier"] == "paved"),
        "boundary": sum(1 for o in outside if o["tier"] == "boundary"),
    }
    return {
        "ok": not outside,
        "total": len(rows),
        "outside": outside,
        "by_tier": by_tier,
        "criteria": ("判据分三级（理由见 `BOUNDS_TIERS`）：**硬·实体**（house / tree / shrub，"
                     "以及**表里没有的一切 element_key**）四角必须都在界内；"
                     "**软·铺装**（road / sidewalk / path / grass）允许贴边、不许超出；"
                     "**豁免·边界元素**（ground）它自己就是世界边界，压线合法（超出仍报）。"
                     "判的是物体**中心**与**占地四角**（`footprint_m` 是物体自身的 [宽, 深]，"
                     "**按 `rot_deg` 转过之后**再比世界边界 —— 2026-10-07 修）。"),
        "note": ("⚠ 只报红、不拦；结果**不写进 plan**（不碰几何指纹）。"
                 "⚠ 越界的物理含义：`world.bounds` = **世界地基的范围**，"
                 "跑到外面 = 悬空 / 穿帮。"),
    }


def _ground_missing_note(plan: dict) -> str:
    """**plan 里有没有「世界地基」那一层** → 缺了就返回一段话（没缺给空串）。

    为什么单开这一条（2026-10-12 实测 bug：整条街的楼与树**悬空**）：
      阶段三**不凭空生底板** —— `docs/阶段三` 与代码都写着「世界地基要靠 plan 里真有一行
      `ground` 白膜」。可它**一路没有闸**：
        · `plan.py` 的工具说明写着"不许凭空多出一个元素，没有代码闸"——那管的是**多**，不管**少**；
        · `_planning_warnings()` 只查反方向（阶段一记过、清单里没有），而且它**只在阶段一开工时算一次**；
        · `_save_build_orders` 遇到 `element_key` 不在阶段一清单里会**拒收**（`plan.py:598`），
          可那也拦不住"**少了一整层**"。
      ⇒ 结果：漏了 `ground` 之后**一路绿灯**，直到用户在 UE 里看见东西悬在空中。
      ⚠ 判据只看"有没有 ground 这一层"，**不看它多大/多厚**（那是阶段二自己的事）；
        `ground` 属于 `BOUNDS_TIERS` 的**豁免·边界元素**（它自己就是世界边界）。
      ⚠ **只报不拦**（与越界同一条口径）：已经落过的场景不能因为补了这条就作废；
        但它必须**显形** —— 所以并进 `bounds_check_text()`，于是 `generate_plan` 与 `get_plan`
        **两处都会报**（那就是"漏在阶段二、却在阶段三才炸"的那个缺口的堵法）。
    """
    rows = list(plan.get("whiteboxes") or []) + list(plan.get("assets") or [])
    has_ground = any(
        str((r or {}).get("element_key") or "").strip().lower() == "ground"
        for r in rows if isinstance(r, dict)
    )
    if has_ground:
        return ""
    return (
        "⚠ **阶段二检查 · 缺「世界地基」**：plan 里**没有 `ground` 这一行**（白膜）。\n"
        "　· 阶段三**不凭空生底板**（`docs/阶段三` 与 `_compose_build_rows()` 都是这个口径）"
        "⇒ 关卡里就没有地基，**楼 / 树 / 铺装会悬在空中**。\n"
        "　· 补法：把它当一行**白膜**加进 plan（`element_key=\"ground\"`，`shape=\"cube\"`，"
        "`z_m` 与 `height_m` 照阶段三的口径：顶面 = 两侧地面 15 cm、厚 20 cm 是口径值）。\n"
        "　⚠ **只报红、不拦** —— 但这一条**必须显形**：实测就是这么漏到用户眼里的。"
    )


def bounds_check_text(plan: dict, limit: int = 12) -> str:
    """把 `bounds_check()` 的结果压成**一段话**（调用方直接塞进 warnings）。没越界就给空串。

    格式（**硬在前、软在后**，附三级判据原文 —— 让读的人知道"为什么这条算硬、那条算软"）：
      ⚠ **阶段二检查 · 越界**：N 处超界（边界 …）
      　判据：…
      　【硬 · 实体 2 处】…
      　【软 · 铺装 13 处】…（还有 N 处没列全）

    ⚠ **2026-10-12 加**：这里**同时**带上「缺世界地基」那一条（`_ground_missing_note()`）——
      两件事都是"阶段二该看出来、但以前一路绿灯"的，而且**共用同一个出口**
      （`generate_plan` / `get_plan` 都调本函数）⇒ 一处加、两处都会报。
      ⚠ 所以**没越界也不再恒为空串**：`ground` 缺了就有话要说。
    """
    missing = _ground_missing_note(plan)
    res = bounds_check(plan)
    if res.get("ok"):
        return missing
    outs = res.get("outside") or []
    world = plan.get("world") or {}

    lines = [
        f"⚠ **阶段二检查 · 越界**：{len(outs)} 处超出世界边界"
        f"（边界 {world.get('bounds', {}).get('min')} ～ {world.get('bounds', {}).get('max')}，米）。",
        "　判据：" + str(res.get("criteria") or ""),
    ]
    left = limit
    for tier in ("solid", "paved", "boundary"):
        mine = [o for o in outs if o.get("tier") == tier]
        if not mine:
            continue
        shown = mine[:max(left, 0)]
        left -= len(shown)
        if shown:
            body = "；".join(f"「{o['label']}」{'，'.join(o['why'])}" for o in shown)
            more = f"（还有 {len(mine) - len(shown)} 处没列全）" if len(mine) > len(shown) else ""
        else:
            # 名额被上一级用完了：**仍然报这一级有几处**（数量不能因为"没展开"就消失）
            body, more = "（上面的名额已用完，这一级不展开 —— 要全列就把 `limit` 调大）", ""
        lines.append(f"　【{BOUNDS_TIER_CN.get(tier, tier)} {len(mine)} 处】{body}{more}")
    lines.append("　⚠ **只报红、不拦** —— 改不改由用户定（要么挪物体、要么把世界放大再重落盘确认）。")
    return ("\n".join(lines) + ("\n" + missing if missing else ""))


# --- 纯 2D 基础（无业务；重叠判定要用 —— 但**碰撞检测那一步 2026-09-27 已删除**，这里只留工具）---

def v_add(a: Point, b: Point) -> Point:
    """向量相加：逐分量相加。"""
    return (a[0] + b[0], a[1] + b[1])


def v_sub(a: Point, b: Point) -> Point:
    """向量相减 a-b：求「从 a 指向 b」的方向。"""
    return (a[0] - b[0], a[1] - b[1])


def v_mul(a: Point, k: float) -> Point:
    """向量乘标量：把方向按长度缩放。"""
    return (a[0] * k, a[1] * k)


def v_len(a: Point) -> float:
    """向量长度（hypot 比 sqrt(x²+y²) 稳，中间不溢出）。"""
    return math.hypot(a[0], a[1])


def v_unit(a: Point) -> Point:
    """单位化；零向量原样返回（由调用方自己判，这里不抛异常）。"""
    n = v_len(a)
    return (0.0, 0.0) if n == 0.0 else (a[0] / n, a[1] / n)


def v_dot(a: Point, b: Point) -> float:
    """点积：判朝向是否一致（>0 = 大致同向）。"""
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    """2D 叉积（标量）：>0 表示 b 在 a 的逆时针侧；=0 共线。"""
    return a[0] * b[1] - a[1] * b[0]


def right_normal(d: Point) -> Point:
    """【全局唯一口径】右手法线：d = (dx, dy) → (-dy, dx)。

    ⚠ 定错方向，人行道/地块会整体镜像到马路另一边，且**不报错**。
    """
    return (-d[1], d[0])


def polygon_area(poly: Poly) -> float:
    """鞋带公式算**带符号**面积（逆时针为正）。⚠ 不许在外面偷偷取绝对值。"""
    total = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]      # % n 让末点接回首点，闭合
        total += x1 * y2 - x2 * y1
    return total / 2.0


def aabb_of(poly: Poly) -> dict:
    """多边形的轴对齐包围盒（AABB）：min/max 两个角。"""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)]}


def _seg_hit(a: Point, b: Point, c: Point, d: Point) -> bool:
    """线段 ab 与 cd 是否「真交叉」（仅共端点/共线不算）。"""
    def side(p: Point, q: Point, r: Point) -> int:
        val = cross(v_sub(q, p), v_sub(r, p))
        if abs(val) < 1e-12:
            return 0
        return 1 if val > 0 else -1

    s1, s2 = side(a, b, c), side(a, b, d)
    s3, s4 = side(c, d, a), side(c, d, b)
    return s1 != s2 and s3 != s4 and 0 not in (s1, s2, s3, s4)


def point_in_polygon(p: Point, poly: Poly) -> bool:
    """射线法判点是否在多边形内（"一个东西有没有套在另一个里"要靠它）。"""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):        # 这条边跨过 p 的水平线
            hit_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < hit_x:
                inside = not inside
        j = i
    return inside


def polygons_overlap(a: Poly, b: Poly) -> bool:
    """两个多边形是否有**面积重叠**（仅共边、仅共点 → 不算重叠）。

    为什么不用"角点在不在对方里面"来判：**贴边摆放是允许的**（相邻两栋楼共墙、
    马路与人行道相接），而射线法对"边界上的点"判定不确定 —— 2026-09-23 实测因此误报过。
    所以判两步，都不依赖"边界点算内还是算外"：
      ① 任意一条边与对方任意一条边**真交叉**（`_seg_hit` 已排除共线/共端点）→ 重叠；
      ② 没有交叉时，再用**形心**判包含关系（一个完全套在另一个里也算重叠）。
         用形心不用顶点：凸多边形的形心一定在内部，不会落在边上。
    """
    na, nb = len(a), len(b)
    for i in range(na):
        for j in range(nb):
            if _seg_hit(a[i], a[(i + 1) % na], b[j], b[(j + 1) % nb]):
                return True
    cen_a = (sum(p[0] for p in a) / na, sum(p[1] for p in a) / na)
    cen_b = (sum(p[0] for p in b) / nb, sum(p[1] for p in b) / nb)
    return point_in_polygon(cen_a, b) or point_in_polygon(cen_b, a)


def clean_numbers(obj):
    """输出前把 **-0.0 归一成 0.0**（递归处理 dict / list / tuple）。

    为什么非做不可（2026-09-23 实测踩过，写在 main.py 的 num() 注释里）：
      Python 把 -0.0 序列化成 "-0.0"，而 JS 的 JSON.stringify(-0) 得到 "0" —— **往返不无损**，
      Agent会判定整条结果 "value is not lossless JSON" 并**整条拒绝**（不是丢一个字段，是整条不可用）。
      本文件里 -0.0 的来源很具体：`right_normal((1,0)) = (-0.0, 1.0)`（对 0 取负），
      再顺着 `facing_vector` 之类的字段漏进 JSON。

    ⚠ 非有限值（inf / nan）**不在这里静默修正** —— 那是真错误，应该炸出来看得见
      （配合 json.dumps(..., allow_nan=False)，它会直接抛异常而不是写出非法 JSON）。
    """
    if isinstance(obj, bool):        # bool 是 int 子类，先挡掉，别被当成数字处理
        return obj
    if isinstance(obj, float):
        return 0.0 if obj == 0.0 else obj
    if isinstance(obj, dict):
        return {k: clean_numbers(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_numbers(v) for v in obj]
    return obj


# --- 放置表装配与校验 ---------------------------------------------------------

def load_previous_elements(path: Path | None = None) -> dict:
    """读阶段一的元素清单（`catalog/elements.json`）→ `(全部行, 人话场景项)`。

    为什么要读它（**不是多此一举**）：
      ① 「地图大小」是阶段一记的场景项 → 本阶段的世界大小默认值从这儿来，不靠人再报一遍；
      ② 放置表里的 `element_key` **必须**在阶段一确认过的清单里有记录
         （不许凭空多出一个元素 —— 那样等于规划了图里没有的东西）。
    读不到就返回空表：由调用方决定是报错还是只警告（这里**不抛**）。

    ⚠ **`path` 的默认值必须是 `None`、在函数体里解析**（2026-10-11 修；插件化时撞到的**真 bug**）：
      Python 的**函数默认参数在 `def` 执行那一刻就求值绑定**了。原来写的是
      `path: Path = ELEMENTS_PATH` —— 于是"把落盘根搬到别处"这类动作（插件版 `init_unreal.py`
      用 `setattr(module, name, new_path)` 重算路径常量）**改得动模块属性、改不动已经绑死的默认参数**：
      `main.py` 两处调 `load_previous_elements()` 都不传参 ⇒ 读的永远是**搬走之前**那个路径
      ⇒ 报「读不到阶段一的元素清单」，而那份文件**明明在**（`Saved/UEPLANE/catalog/elements.json`）。
      **阶段二被它整个堵死**，而日志里"搬了 7 个路径常量"看着全对 —— 静默失败最难查。
      ⇒ 口径：**路径常量不许进函数默认参数**，一律在函数体内解析。
    """
    path = ELEMENTS_PATH if path is None else path
    doc = load_json(path) or {}
    rows = [e for e in (doc.get("elements") or []) if isinstance(e, dict)]
    scene: dict[str, str] = {}
    for e in rows:
        cat = str(e.get("category") or "")
        if cat == "map_size" and isinstance(e.get("size_m"), (list, tuple)) \
                and len(e["size_m"]) >= 2:
            scene["size_m"] = f"{float(e['size_m'][0]):g}×{float(e['size_m'][1]):g} m（阶段一记录）"
        elif cat == "time_of_day" and str(e.get("time_of_day") or "").strip():
            scene["time_of_day"] = str(e["time_of_day"]).strip()
    return {"rows": rows, "scene": scene, "confirmed_at": doc.get("confirmed_at", "")}


def normalize_world(given: dict | None, scene: dict, previous: dict) -> tuple[dict, list[str]]:
    """定出世界中心与大小 → `(world, 说明改了哪几处)`。

    默认值：**中心 = 世界原点 (0,0)**（2026-09-24 用户定）；**大小 = 阶段一记录的**
    「地图大小」场景项。调用方给的值优先，且**必须写清来源**（是阶段一的记录、还是你改过的）。
    """
    center = [0.0, 0.0]
    size: list[float] | None = None
    size_source = ""
    changed: list[str] = []

    for e in previous.get("rows") or []:
        if str(e.get("category") or "") == "map_size" and isinstance(e.get("size_m"), (list, tuple)) \
                and len(e["size_m"]) >= 2:
            size = [float(e["size_m"][0]), float(e["size_m"][1])]
            size_source = "阶段一 elements.json 的『地图大小』场景项"
    if size is None:
        raise ValueError(
            "世界大小没有来源：阶段一 elements.json 里没有『地图大小』场景项 —— "
            "请先在阶段一用 confirm_elements 填它，或由调用方显式给 world.size。"
        )

    g = given or {}
    if "center" in g:
        c = g["center"]
        if not (isinstance(c, (list, tuple)) and len(c) >= 2):
            raise ValueError("world.center 要写成 [X, Y]（米）")
        if [float(c[0]), float(c[1])] != center:
            changed.append("world.center")
        center = [float(c[0]), float(c[1])]
    if "size" in g:
        s = g["size"]
        if not (isinstance(s, (list, tuple)) and len(s) >= 2):
            raise ValueError("world.size 要写成 [X, Y]（米）")
        if min(float(s[0]), float(s[1])) <= 0:
            raise ValueError("world.size 必须都是正数（米）")
        if [float(s[0]), float(s[1])] != size:
            changed.append("world.size")
        size = [float(s[0]), float(s[1])]
        size_source = "调用方覆盖（不是阶段一记录）"

    hx, hy = size[0] / 2.0, size[1] / 2.0
    bounds = {
        "min": [center[0] - hx, center[1] - hy],
        "max": [center[0] + hx, center[1] + hy],
    }
    world = {
        "center": center,
        "size": size,
        "size_source": size_source,
        "bounds": bounds,
        "coordinate_system": {
            "handedness": "left",
            "height_axis": "Z",
            "forward_axis": "X",
            "right_axis": "Y",
            "right_normal_rule": "right_normal(d) = (-dy, dx)",
        },
    }
    return world, changed


def _norm_placement(raw: dict) -> dict:
    """把一行放置记录的数字字段规整成浮点/列表（缺字段直接报错，不猜）。"""
    if not isinstance(raw, dict):
        raise ValueError(f"放置记录必须是对象，收到 {type(raw).__name__}")
    key = str(raw.get("element_key") or "").strip()
    if not key:
        raise ValueError("放置记录缺 element_key（要与阶段一的元素名一致）")
    if key in SCENE_KEYS:
        raise ValueError(
            f"{key!r} 是**场景项**、不是物体 —— 它不该出现在放置表里"
            "（地图大小 / 时间已记在本数据的 world / 阶段一清单里）"
        )
    pos = raw.get("pos")
    if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
        raise ValueError(f"{key!r} 缺 pos —— 每一条放置都必须给平面中心坐标 [X, Y]（米）")
    fp = raw.get("footprint_m")
    if not (isinstance(fp, (list, tuple)) and len(fp) >= 2):
        raise ValueError(f"{key!r} 缺 footprint_m —— 每一条放置都必须给占地 [宽, 深]（米）")
    item = {
        "element_key": key,
        "label": str(raw.get("label") or "").strip(),
        "pos": [float(pos[0]), float(pos[1])],
        "footprint_m": [float(fp[0]), float(fp[1])],
        "rot_deg": float(raw.get("rot_deg") or 0.0),
        "note": str(raw.get("note") or ""),
    }
    # `z_m`（**必填**，米）= 这一行的**底面绝对标高** —— 2026-10-08 起**每行都要写**
    #   （用户指令：「关于竖直我之前就说了改位置表结构你说不用，现在又来问我，
    #   赶紧改位置表结构增加Z」）：
    #   **Z 只从这一行取**，阶段三**不再**按 `element_key` 查表推（那张 `WHITEBOX_VERTICAL` 已删）。
    # 语义 = **底面标高**（2026-10-07 用户定案 B）：资产行与白膜行**同一个量**，
    #   "中心 / 原点"由阶段三从它推出来（`_compose_build_rows()`）。
    #   ⚠ 本文件**不做任何 Z 换算**（只存这个数）—— 推 Z 的地方只有阶段三那一处。
    #   ⚠ 它是**几何**（进指纹、进逐行签名），不是说明文字。
    # 参照值（填的时候对表用）：路面上的 0.00、站在两侧地面上的 0.15、人行道底面贴路面 0.00、
    #   世界地基 −0.05（顶面 0.15、厚 0.20）、车行道 −0.15（顶面 0、厚 0.15）。
    if raw.get("z_m") is None:
        raise ValueError(
            f"{key!r} **缺 `z_m`**（这一行的**底面绝对标高**，米）—— 2026-10-08 起**每行必填**："
            "Z 只从这一行取，阶段三不猜（用户指令「改位置表结构增加Z」）。"
            "参照值：路面上的 0.00、站在两侧地面上的 0.15、人行道底面贴路面 0.00、"
            "世界地基 −0.05（顶面 0.15、厚 0.20）、车行道 −0.15（顶面 0、厚 0.15）。")
    try:
        item["z_m"] = float(raw["z_m"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key!r} 的 z_m 不是数字：{raw.get('z_m')!r}") from exc
    if not (item["footprint_m"][0] > 0 and item["footprint_m"][1] > 0):
        raise ValueError(f"{key!r} 的 footprint_m 必须都是正数（米）")
    return item


def normalize_asset(raw: dict) -> dict:
    """规整一行**已有资产**放置；校验它是"有资产的元素"。"""
    item = _norm_placement(raw)
    path = str(raw.get("asset_path") or "").strip()
    if not path:
        raise ValueError(
            f"{item['element_key']!r} 在 assets[] 里却没给 asset_path —— "
            "**没有资产的元素请写进 whiteboxes[]**，不要混进已有资产表"
        )
    scale = raw.get("scale")
    if scale is None:
        raise ValueError(f"{item['element_key']!r} 缺 scale（模型缩放倍率）")
    if float(scale) <= 0:
        raise ValueError(f"{item['element_key']!r} 的 scale 必须为正")
    item["asset_path"] = path
    item["scale"] = float(scale)
    # --- Z 方向倍率（`scale_z`，2026-09-30 加）------------------------------------
    # 用户原话：「楼别一样高」而**占地不能跟着变** —— 改 `scale` 是整体缩放（占地同时变），
    # 所以单开这一维。语义 = **相对 `scale` 的 Z 倍率**（1.0 = 三轴同倍率）；
    # 到 UE 那层就是 `RelativeScale3D = [scale, scale, scale × scale_z]`。
    # ⚠ `1.0`（含 1e-9 内的浮点噪声）**不写进数据**：与"没这个字段"同义 ——
    #   免得只是显式写了个 1.0 就把几何指纹改掉、把上一次确认无谓作废。
    zs = raw.get("scale_z")
    if zs is not None and str(zs).strip() != "":
        try:
            z_val = float(zs)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{item['element_key']!r} 的 scale_z 不是数字：{zs!r}") from exc
        if not math.isfinite(z_val) or z_val <= 0:
            raise ValueError(
                f"{item['element_key']!r} 的 scale_z 必须是**正的有限数**（现在是 {zs!r}）—— "
                "它是 Z 方向的倍率，0 / 负数 / inf 都不是合法的高度"
            )
        if abs(z_val - 1.0) > 1e-9:
            item["scale_z"] = z_val
    return item


def normalize_whitebox(raw: dict) -> dict:
    """规整一行**白膜**放置；校验"没有资产"的三样（路径空 / 有形状 / 尺寸是预估）。"""
    item = _norm_placement(raw)
    if str(raw.get("asset_path") or "").strip():
        raise ValueError(
            f"{item['element_key']!r} 是白膜行，asset_path 必须留空 —— "
            "有资产就该写进 assets[]，不能两边都写"
        )
    shape = str(raw.get("shape") or "").strip().lower()
    if shape not in WHITEBOX_SHAPES:
        raise ValueError(
            f"{item['element_key']!r} 的白膜缺 shape，只能是 {' / '.join(WHITEBOX_SHAPES)}"
            "（cube = 有体积的，plane = 覆盖面）"
        )
    src = str(raw.get("size_source") or "").strip()
    if ESTIMATED_MARK not in src:
        raise ValueError(
            f"{item['element_key']!r} 的白膜必须写明尺寸是**预估**"
            f"（size_source 里要有『{ESTIMATED_MARK}』二字，现在写的是 {src!r}）—— "
            "预估值不许伪装成实测值"
        )
    item["shape"] = shape
    item["size_source"] = src
    # --- 厚度（`height_m`）—— **必填，plane 也不例外**（2026-10-03 用户定案）---
    # 为什么必须补：阶段三拿它把白膜实例化成 cube；缺了只能靠人读 note 里的中文。
    # ⚠ **为什么 `plane` 也要厚度**（2026-10-03 实测事故）：UE 的图元工具只有
    #   `add_cube` / `add_cone` / `add_cylinder` / `add_sphere` —— **没有 plane 工具**
    #   （调官方 `PrimitiveTools` 工具集清单确认）。所以"面"落进 UE **只能是 cube**，
    #   `plane` 只是"它是个覆盖面"的语义标签，**不减免厚度**。
    #   事故经过：清单里 plane 那行 `size_cm[2] = 0`（覆盖面只登记长宽的**登记习惯**）
    #   被阶段三当成厚度 → 0 厚的 cube → `add_cube` 拒收 `dimensions.z must be positive, got 0.0`
    #   → **旧的被删掉了、新的没建出来**（人行道 / 沥青路面 / 水面三层消失）。
    h = raw.get("height_m")
    if h is None or str(h).strip() == "":
        raise ValueError(
            f"{item['element_key']!r} 的白膜**必须给 `height_m`（厚度，米，> 0）** —— "
            "`plane` 也不例外：\n"
            "· UE 的图元工具只有 `add_cube` / `add_cone` / `add_cylinder` / `add_sphere` —— "
            "**没有 plane 工具**（实测官方工具集清单）。所以「面」在这个项目里**只能是 cube**，"
            "`plane` 只是说「它是个覆盖面」，**不减免厚度**。\n"
            "· 照抄写法：`{{\"shape\": \"plane\", \"height_m\": 0.15}}`（= 15 cm 厚）"
            "或 `{{\"shape\": \"cube\", \"height_m\": 0.15}}` —— 两者落进 UE **一模一样**。\n"
            "· 阶段一清单里 plane 那行的 `size_cm[2] = 0` 是**登记习惯**（覆盖面只登记长宽），"
            "**不是厚度** —— 阶段三不看它。"
        )
    try:
        height_m = float(h)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{item['element_key']!r} 的 height_m 不是数字：{h!r}") from exc
    if height_m <= 0:
        raise ValueError(
            f"{item['element_key']!r} 的 height_m 必须为正（米），现在写的是 {height_m!r} —— "
            "0 厚的 cube 官方会拒收（`dimensions.z must be positive`，实测），"
            "要一个面就给它一个薄厚度（如 0.15 = 15 cm）。")
    item["height_m"] = height_m
    return item


def build_plan(assets: list, whiteboxes: list, world: dict,
               params: dict | None = None, previous: dict | None = None) -> dict:
    """把「世界 + 两份放置表」装配成 plan_v1.json 的内容（含几何指纹）。

    这个函数**不规划坐标** —— 坐标由 agent 给（那是它看图+规划的活）。
    它只做三件事：装配、校验形状、钉指纹。

    ⚠ **2026-09-25：11 项具名自检整段删除**（用户指令：「阶段二不要这个自检阶段了，后面阶段再做自检」）——
      连同 `build_checks()` 一起拿掉，`plan_v1.json` 里**不写 `checks` 字段**。
    ⚠ **2026-09-27：要回来"有且只有一条"**（用户原话：「第二阶段得改一下，只有一个检查，
      是否有物体超出世界边界」）—— 就是 `bounds_check()`：越界**只报红、不拦**，
      而且**不写进 plan**（写进去会改几何指纹、把上一次确认无谓作废）。
      结论：判断"摆得好不好"的东西仍然一概没有；只有"越没越界"这一个硬事实会被报出来。
      碰撞检测与布局修正**已整段删除**（2026-09-27 用户指令：不再单列阶段）：重叠**不检、不修**。
      影响面：`plan_geometry_hash()` 算的是除 `confirmation` 外的全部内容 —— 少一段就是另一个
      指纹，所以**落地后上一次确认自动作废**（这一版落地时确实作废过一次）。

    ⚠ **两份表都空 = 允许**（2026-09-24 用户要求）：那是**初始化状态** —— 结构先立起来、
      内容是空的，因为坐标的依据（参考图）还没来。它不是一份可确认的规划。
    ⚠ **文件里不再写 `status`**（2026-09-24 收尾清单 #5）：以前 `build_plan` 写 `draft`、
      `mark_confirmed` 却不动它，于是确认之后文件里是 `status: "draft"` +
      `confirmation.confirmed: true` —— 同一件事两个真值来源，读文件的人会被误导。
      现在三值（`initialized` / `draft` / `confirmed`）**只由 `get_plan` 现算**：
      空表 = initialized、有内容 = draft、闸门确认 = confirmed。
    """
    previous = previous or {"rows": [], "scene": {}}
    empty = not assets and not whiteboxes

    # --- 出口再挡一次：白膜行**必须**有正厚度（2026-10-03 加）---
    # 为什么在装配这儿也挡：`generate_plan` 走的是 `normalize_whitebox()`，但**别的调用方
    #   （含 CLI / 以后的脚本）可能直接装配** —— 上面那道校验不该只有一条路经过。
    # 判据只有一句：`height_m` 必须是正的有限数 —— 0 厚的 cube 官方直接拒收（实测）。
    for _w in (whiteboxes or []):
        _k = str((_w or {}).get("element_key") or "?")
        _h = (_w or {}).get("height_m")
        try:
            _hv = float(_h)                                  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{_k!r} 的白膜缺 `height_m`（厚度，米，> 0）—— UE **没有 plane 工具**，"
                "「面」也只能落成 cube，所以厚度必须明写（如 `height_m: 0.15`）") from exc
        if not math.isfinite(_hv) or _hv <= 0:
            raise ValueError(f"{_k!r} 的 `height_m` 必须是正的有限数，现在是 {_h!r}")

    # --- 出口再挡一次：`label` **必须唯一**（2026-10-05 加）--------------------------------
    # 为什么（实测缺陷链）：`label` 是**匹配键**（`uid = element_key|label`）—— 两行同名会让
    #   ① `plan_row_signatures()` 按 label 建字典 ⇒ **后一条静默覆盖前一条**：验收基线与
    #      `change_set()` 少一行，而 `figure_label_coverage()` 只要那个名字在正文里出现过一次
    #      就算覆盖 ⇒ **图里永远看不出漏了一行**（正是本文件别处警告过的那类"真漏了没人看得出来"）；
    #   ② `uid` 撞车 ⇒ `_write_ledger_rows()` 的 `res_by_uid` 按 uid 折叠，一行的落位结果被顶掉；
    #   ③ 之后再点这个名字的 `patch` / `only_labels` 会"命中多行"拒收（吵的那一半）。
    # 判据：**只查非空 label** —— 空 label 是"没写名字"，那不算撞名（图里另有占位说明）。
    _seen_lab: dict[str, str] = {}
    _dup_lab: list[str] = []
    for _kind_cn, _rows_cn in (("资产", assets), ("白膜", whiteboxes)):
        for _r in (_rows_cn or []):
            _lab = str((_r or {}).get("label") or "").strip()
            if not _lab:
                continue
            if _lab in _seen_lab:
                _dup_lab.append(f"`{_lab}`（{_seen_lab[_lab]}行 与 {_kind_cn}行）")
            else:
                _seen_lab[_lab] = _kind_cn
    if _dup_lab:
        raise ValueError(
            "放置表里有**重名**的 label：" + "、".join(_dup_lab)
            + " —— label 是**匹配键**（`uid = element_key|label`：补丁定位 / 点名重摆 / 逐行签名"
              "都认它），重名会让两行共用一个 uid：图里看不出漏了一行、台账对账会折叠。"
              "请把这几行的 label 改成互不相同（同类多行必须能区分）。")

    plan = {
        "stage": "阶段二 · 平面放置规划",
        "unit": "m",
        "world": world,
        "assets": assets,
        "whiteboxes": whiteboxes,
        "params": dict(params or {}),
        "previous_elements": {
            "path": "catalog/elements.json",
            "confirmed_at": previous.get("confirmed_at", ""),
            "scene": previous.get("scene", {}),
        },
        "note": (
            "一个物体一行、自带 pos（平面中心坐标，米）。**没有 count 字段** —— "
            "摆几个就写几行，不许用一条记录代表多个不同位置的物体（那样谁也看不见它们）。"
            "已有资产先放（大小是阶段一实测的）、白膜后放（大小要等资产占完位再估）。"
            "本数据不依赖任何图片；要图就由 AI 拿这份数据自己画（代码不出图）。"
            + ("⚠ **当前是初始化状态：两张表都是空的** —— 坐标的依据（参考图）还没来，"
               "所以这里没有任何 pos。" if empty else "")
        ),
        "confirmation": {
            "required": True,
            "confirmed": False,
            "confirmed_by": "",
            "confirmed_at": "",
            "note": "未确认前不许进入第三阶段（AGENTS.md「规划图闸门」）",
        },
    }
    plan["confirmation"]["plan_hash"] = plan_geometry_hash(plan)
    return plan



# --- 确认与留痕（闸门）--------------------------------------------------------

def plan_geometry_hash(plan: dict) -> str:
    """几何指纹：对 plan_v1.json 里**除 confirmation 之外**的全部内容做哈希。

    为什么排除 confirmation：确认信息要写回同一个文件，不能把自己算进去。
    这个指纹同时覆盖两种情况 —— **参数变了**、**代码改了**，只要几何不一样指纹就不一样。

    ⚠ **内部先做数字净化（-0.0 → 0.0）**，这是必须的：
      落盘写的是净化后的值，而内存里的对象可能带 -0.0（例如 facing_vector 的 (-0.0, 1.0)），
      两边直接比就会**对着一张刚算出的图喊"文件被改过"**（2026-09-23 经 MCP 实测踩到）。
      净化收在这里、不指望每个调用方记得做 —— 单点收口才不会再漏。
    """
    body = {k: v for k, v in clean_numbers(plan).items() if k != "confirmation"}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def gate_check(plan: dict) -> dict:
    """闸门状态：这张规划图**还作不作数**。只读，不改任何东西。

    谁都能调（MCP 工具 / CLI / 别的 agent），判定口径只有这一份。

    ⚠ 2026-09-24 起**只有几何指纹一道闸**：参数文件删了（参数由调用方给），
      没有"哪份参数"可比。闸门管的是**这张图**，不是"这组参数"。
      `params_hash_ok` 字段保留是为了兼容旧 plan_v1.json —— 老文件里存过 params_hash，
      这里**照它比**（比不了就判 ok），免得旧确认被无理由判死。
    """
    conf = plan.get("confirmation", {}) or {}
    now_geom = plan_geometry_hash(plan)
    stored_params = conf.get("params_hash", "")
    return {
        "params_hash_ok": not stored_params,     # 新文件不存 params_hash → 恒 true
        "plan_hash_ok": (not conf.get("plan_hash")) or conf["plan_hash"] == now_geom,
        "confirmed": bool(conf.get("confirmed")),
        "confirmed_by": conf.get("confirmed_by", ""),
        "confirmed_at": conf.get("confirmed_at", ""),
        "params_hash": "",
        "plan_hash": now_geom,
        "stored_params_hash": stored_params,
        "stored_plan_hash": conf.get("plan_hash", ""),
    }


def mark_confirmed(plan: dict, who: str, user_quote: str = "") -> dict:
    """返回一份**带确认信息的新 plan**（不改传入的那个对象）。

    调用方负责先过 `gate_check`：闸门不过就别确认。
    ⚠ `user_quote` = **用户点头的原话**（2026-09-26 加，堵"agent 自己把闸门走完"这个洞，见
      `main.py` 的 `confirm_plan` 说明）：图是 agent 自己画的、确认也是它自己调的 ——
      **只有"用户说了什么"这一条能区分"人看过了"和"agent 自己拍的"**。它跟着确认一起留痕。
    """
    out = dict(plan)
    out["confirmation"] = {
        **(plan.get("confirmation", {}) or {}),
        "required": True,
        "confirmed": True,
        "confirmed_by": who,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "user_quote": str(user_quote or ""),
        "plan_hash": plan_geometry_hash(plan),
        "note": "已确认。几何一变，指纹就对不上，本次确认自动作废。",
    }
    # 老文件里可能带着 params_hash —— 它是"上一版机制"的残留，确认时顺手清掉，
    # 免得读的人以为现在还有"参数指纹"这道闸。
    out["confirmation"].pop("params_hash", None)
    # ⚠ **2026-10-04 修**：`mode` / `rows` 是**微调版**（`mark_tuned()`）留下的那两笔 —— 原来
    #   没清。后果：`acceptance_state()` **最先认 `mode`** ⇒ 微调版走完正路
    #   「重落盘（那一页由代码自动重生成）→ `confirm_plan()`」之后**仍然返回 `tuned`**，`main.py` 的 `_tuned_plan`
    #   仍为 True ⇒ `mode="full"` 继续被拒、真跑继续强制点名 —— 而报文里恰恰把那条正路说成
    #   **唯一出路**（用户/agent 照它做，永远回不到正常验收）。**清掉 = 正常确认才是回到正常验收。**
    out["confirmation"].pop("mode", None)
    out["confirmation"].pop("rows", None)
    return out


def mark_tuned(plan: dict, user_quote: str, rows: list[dict] | None = None,
               by: str = "用户") -> dict:
    """返回一份**带「微调版确认」信息的新 plan**（不改传入的那个对象）—— **第四阶段 · 微调**用。

    用在哪（阶段八之前的第 4 个阶段）：**整体搭建之后**用户说「把电线杆挪近一点」这类**小改** ——
    不画图、不二次确认（阶段二那套"一页 + confirm_plan"只属于前三阶段），
    但**改的是同一份 plan_v1.json**，所以必须留痕：谁让改的、改了哪几行、为什么。

    ⚠ **它不画图、也不检查图**（这是第四阶段与阶段二验收的**唯一区别**）——
      `acceptance_state()` 见到 `mode == "tuned"` 就直接返回 `"tuned"`，
      **不走"图认不认这份数据"那条路**（那次没有图，如实记着）。
    ⚠ `plan_hash` 写的是**改完之后**这一版的指纹（照抄 `mark_confirmed()` 的口径：
      先 `dict(plan)` 拷出 `out`、再用 `plan_geometry_hash(plan)` 算 ——
      指纹覆盖的是**除 confirmation 外的全部内容**，所以改完几何后算它才作数）。
    ⚠ `user_quote` 为空 = **没有凭据**（与 `confirm_plan` 同一条纪律：拿不出原话就说明没问过人）——
      那种情况**本函数拒收**（不动传进来的对象、**一个字节都不写**），
      由调用方把「先去 `request_plan_change()` 记下用户原话」摆给用户。
    ⚠ `rows` = **改了哪几行 / 为什么**（`[{"label": …, "why": …}, …]`）：`label` 与
      `plan_v1.json` 里的一致，`why` 能记用户原话就记原话。**只留痕，不做校验**
      （它不参与几何指纹，也不是闸门；目的是台账事后答得了「用户说了什么 ↔ 数据变成什么」）。

    ⚠ `by` = **这句话是谁说的**（`用户` / `agent 自查`，2026-10-05 加）：它决定 `confirmed_by`
      与新增的 `provenance`。**`agent 自查` 的那句话不算"用户点头"** —— `_precheck_build()` 见到
      `provenance != 用户` 会**拒收这一版落关卡**（要落就得让他本人开口，重记一条
      `request_plan_change(items=[他的原话], by="用户")`）。默认 `"用户"` 只为兼容旧调用方。

    落盘路径照旧是 `write_plan()`（本函数只造对象、**不写盘**）——
    它会自动留档上一版、并清掉不认新指纹的旧图（微调版**本来就没有图**）。
    """
    if not str(user_quote or "").strip():
        raise ValueError(
            "微调版留痕**必须有用户原话**（`user_quote`）—— 拿不出原话就说明还没问过他："
            "先 `request_plan_change(items=[…], by=用户, reason=原话)` 把「要改什么 / 谁要的」记下来，"
            "再改 `pos` / `footprint_m` / `scale`，最后带他的原话回来。")
    out = dict(plan)
    changed_rows: list[dict] = []
    for item in (rows or []):
        if not isinstance(item, dict):
            continue
        changed_rows.append({
            "label": str(item.get("label") or ""),
            "why": str(item.get("why") or ""),
        })
    out["confirmation"] = {
        **(plan.get("confirmation", {}) or {}),
        "required": True,
        "confirmed": True,
        # `mode` 是"这是哪一种确认"的唯一判据 —— `acceptance_state()` 见到它就直接给 `tuned`。
        "mode": "tuned",
        # ⚠ **`confirmed_by` 不再写死**（2026-10-05 修）：`main.py::_pending_change_quote()` 现在把
        #   `request_plan_change()` 记下的 `by` 一起带出来 —— 用户提的（`by="用户"`）才记成
        #   「用户（微调）」；agent 自查的（`by="agent 自查"`）**只能**记成它自己。
        #   以前这里写死，于是一次纯自查的改动在 plan 里长得像**用户确认过**；而
        #   `_precheck_build()` 又拿 `confirmation.user_quote` 非空当"用户点头"的凭据
        #   ⇒ **系统把 agent 的话洗成了人的确认**。现在它按新增的 `provenance` 拦。
        "confirmed_by": ("用户（微调）" if str(by or "").strip() in ("", "用户")
                         else f"{str(by).strip()}（微调）"),
        # `provenance` = **这句话是谁说的**（`用户` / `agent 自查`）—— 「能不能当用户点头」的判据，
        # 由 `main.py::_precheck_build()` 读。⚠ 它落在 `confirmation` 段里，而
        # `plan_geometry_hash()` **排除 `confirmation`** ⇒ 补这一笔**不作废任何已有确认**。
        "provenance": str(by or "").strip() or "用户",
        "confirmed_at": _now(),
        "user_quote": str(user_quote or ""),
        "rows": changed_rows,
        "plan_hash": plan_geometry_hash(plan),
        "note": ("微调版确认（第四阶段 · 微调）：几何已按"
                 + ("**用户原话**" if str(by or "").strip() in ("", "用户")
                    else f"**{str(by).strip()}**的话")
                 + "改过（`provenance` 记着这句话是谁说的），"
                 "**这一次没有图** —— 依据是 `user_quote` 与 `rows`。"
                 "几何一变，指纹就对不上，本次留痕自动作废。"),
    }
    # 老文件里可能带着 params_hash（上一版机制的残留）—— 与 `mark_confirmed()` 同样顺手清掉。
    out["confirmation"].pop("params_hash", None)
    # ⚠ 台账侧同一轮收口（与 `confirm_plan()` → `record_acceptance()` 对齐，**只多这一处**）：
    #   ① 关掉「改动窗口」（`record_change_request()` 打开的那个）—— 不关的话下一轮微调会被
    #      `_change_request_guard()` 拒收（它会说"台账里没有记录"，而用户明明刚说过原话）；
    #   ② 把**这一版的逐行几何签名**记成新基线 —— 不记的话 `change_set()` 会拿**微调之前**的
    #      基线去比，把这一轮"改过哪几行"算错（图有没有是另一回事，基线是给下一次算差异用的）。
    # ⚠ 这里传的是 **`out`**（不是入参 `plan`）：`plan_geometry_hash()` **排除 `confirmation`**，
    #   两者算出的指纹本来是同一个；但 `out` 才是"改完之后"的那一份 —— 别给它留出第二种读法。
    #   ⚠ 这一句会把**基线**更新成"微调之后"那一版 ⇒ 同一次写入里 `write_plan()` 再算
    #     `change_set()` 就会得到"没有改动"（它拿新基线比新数据）。**这不是丢信息**：
    #     这一轮改的是哪几行记在 `confirmation.rows` 与下面的 `tuned_history` 里（两处都在）。
    record_acceptance(out, "用户（微调）", user_quote=str(user_quote or ""),
                      figure="", figure_age_s=None)
    # ⚠ **累计微调计数：只报数、不设闸**（2026-10-04 用户拍板「按需」）——
    #   微调一次次累积下去，plan 与"用户当初看过的那张图"会越差越远；这条数就是拿来让他
    #   **按需**决定"要不要回一次全图"的依据。**不设自动阈值**（那是替他做决定）。
    #   记账在验收台账里（`views/acceptance.json`，不进几何指纹）。
    try:
        _acc = load_acceptance()
        _hist = [e for e in (_acc.get("tuned_history") or []) if isinstance(e, dict)]
        _hist.append({
            "at": out["confirmation"]["confirmed_at"],
            "plan_hash": out["confirmation"]["plan_hash"],
            "rows": [r["label"] for r in changed_rows if r["label"]],
            "user_quote": str(user_quote or ""),
        })
        _acc["tuned_history"] = _hist
        _acc["tuned_count"] = len(_hist)
        _acc["tuned_rows_total"] = int(_acc.get("tuned_rows_total") or 0) + len(
            [r for r in changed_rows if r["label"]])
        _acc["tuned_note"] = ("微调累计（**只报数、不设闸**）：次数与行数在这里累加 —— "
                              "要不要为它回一次全图验收，由用户按需决定，代码不替他定阈值。")
        save_acceptance(_acc)
    except OSError:
        pass                                  # 台账写不动不该把这一版微调整个搞失败（它已经过了闸）
    return out


def figure_row_labels(plan: dict) -> list[str]:
    """放置表里**每一行**的 `label`（已有资产在前、白膜在后，与数据同序）。

    这是"图不许漏"的那份**对账清单** —— 一行都不能少（2026-09-25 用户要求：
    「画图让 Agent 不要漏位置坐标表任何一个元素」）。
    某行没写 `label` 的，也给一条占位说明，**不静默跳过**（静默跳过 = 漏了没人知道）。
    """
    rows = list(plan.get("assets") or []) + list(plan.get("whiteboxes") or [])
    out: list[str] = []
    for i, row in enumerate(rows, 1):
        lab = ""
        if isinstance(row, dict):
            lab = str(row.get("label") or "").strip()
        out.append(lab if lab else f"<第 {i} 行没写 label>")
    return out


# --- 局部确认：变更集（2026-09-26 用户要求「用户想局部改，**所有都局部改，不要全部重做**」）----
# 原来每改一行都要**重画整张图**（图里得出现全部行的 label）再确认整份表 —— 改一处等于重做一遍。
# 现在按**变更集**判：图里只要出现"这一次动了的那几行"的 label。安全靠**传递**：
# 第一版全量确认过，之后每轮只确认改动过的行，把各轮覆盖的行并起来 = 每一行都被某张图点到过。

CONFIRMED_ARCHIVE_GLOB = "plan_v*_confirmed.json"
"""归档里"**被人确认过**那一版"的文件名模式（`write_plan()` 归档时按这个后缀命名）。

⚠ `plan_v*` 里的 `*` 是**纪元号**（2026-10-04 加）：v1 与 v2 的归档同放 `views/archive/`，
名字带纪元前缀，`latest_confirmed_plan()` 才认得全 —— 写死成 `plan_v1_*` 的话，
纪元 2 之后这条回退基线会一直指着纪元 1 的旧版（那正是"两套口径"）。
"""


def row_signature(row: dict, kind: str) -> str:
    """一行的**几何签名** —— 只装"改了就得重出那一页"的字段。

    ⚠ 故意**不含** `note` / `size_source`：那是说明文字，改字不该逼人重出那一页。
    ⚠ `label` 也不在里面 —— 它是**匹配键**；label 一改就等于"删一行 + 加一行"，那是真的变了。
    """
    row = row if isinstance(row, dict) else {}
    if kind == "asset":
        fields = [row.get("element_key"), row.get("asset_path"), row.get("pos"),
                  row.get("footprint_m"), row.get("rot_deg"), row.get("scale"), row.get("z_m")]
        # ⚠ Z 倍率（`scale_z`）**只在真的不等于 1 时才追加到签名末尾**（2026-09-30 加）：
        #   这样"旧数据（没这个字段）"与"显式写 1.0"的签名**逐字节相同** ——
        #   历史基线（台账里的 `accepted_rows`）不会因为这次改动被误判成"每一行都变了"
        #   （那会逼着全量重画）。而真的改了 Z 倍率时签名会变 →
        #   **上一次确认自动作废**（那是必须的：高度变了，那张图就不是这一版了）。
        zs = _scale_z_of(row)
        if zs is not None and abs(zs - 1.0) > 1e-9:
            fields.append(zs)
    else:
        fields = [row.get("element_key"), row.get("shape"), row.get("pos"),
                  row.get("footprint_m"), row.get("rot_deg"), row.get("height_m"), row.get("z_m")]
    return json.dumps(clean_numbers(fields), ensure_ascii=False)


def _scale_z_of(row: dict) -> float | None:
    """资产行的 **Z 方向倍率**（`scale_z`）—— 读不出来给 `None`（= 与 XY 同倍率）。

    语义（2026-09-30 加，用户要求「加」）：**相对 `scale` 的 Z 倍率**，`1.0` = 三轴同倍率。
    到 UE 那层就是 `RelativeScale3D = [scale, scale, scale × scale_z]` ⇒
    **只拉高 / 压低，占地一个数都不动**（改 `scale` 是整体缩放，占地会跟着变 —— 那正是
    "楼别一样高、但街道布局不动"这个诉求表达不了的地方）。
    ⚠ `1.0` 与"没写"同义：`normalize_asset()` 不把它写进数据（免得白改指纹）。
    """
    row = row if isinstance(row, dict) else {}
    try:
        val = float(row.get("scale_z"))
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None


ASPECT_FIELDS = {
    "asset": {"plan": (0, 1, 2, 4), "size": (3, 5), "height": (6, 7)},
    "whitebox": {"plan": (0, 1, 2, 4), "size": (3,), "height": (5, 6)},
}
"""**维度 → 逐行签名里的下标**（下标顺序的唯一出处是 `row_signature()`）。

⚠ 2026-09-30 用户定的**最终口径**：「涉及**位移**的就顶视图，涉及**大小、高度**的就「原图视角」透视示意图」。
按这条把字段分三组：
  · `plan`（**位移 / 朝向**）→ 顶视图：`element_key` / `asset_path`|`shape` / `pos` / `rot_deg`；
    ⚠ `rot_deg` **归顶视**是我加的判断（朝向从上面看得最清楚）—— 要它也出立面就说一声。
  · `size`（**大小**）→ 立面：`footprint_m` / `scale`；
  · `height`（**高度 / Z**）→ 立面：资产 `z_m` / `scale_z`、白膜 `height_m` / `z_m`。
⚠ 资产行签名末位 `scale_z` **只在 ≠1 时才存在** ⇒ 下标越界一律当 `None`（= 没写），不是错误。
"""

REQUIRED_VIEWS = ("page",)
"""**每一版都出这一张**（2026-10-11 用户定案：「整改2阶段，不出图，出three.js」）。

历史（**别再照旧口径讲**）：原先恒出**两张 agent 手绘的图**（`top` 顶视图 + `elevation` 原图视角
透视示意图，2026-09-30 定案）。改成 three.js 页之后**一张就够**，理由是能力包含关系：
  · 那张页**可转、可缩、可点** —— 顶视图、原图视角、沿街侧视都是**页上的按钮**（`#bar` 里四个机位），
    所以"只动高度时顶视图看不出高低"这个**当年要两张的理由已经不成立**：转一下或按「原图视角」即可；
  · 而且它**由代码在落盘那一刻生成**（`_write_view()`）⇒ 当年"图与数据不一致"那一类事故
    （数据改了、图还是上一版；或图画好了、指纹忘了写）**在结构上消失**。

⚠ **保留这个常量、不写死单路**：`change_set()` / `figure_check()` / `acceptance_state()` 都按它循环 ——
  将来若用户改口要第二路（他原话：「若他改口要两路，照第 4 节 A 的备选走」），
  在这里加一个值 + 在 `FIGURE_GLOBS` 加一组前缀即可，**判据一行都不用改**。
⚠ 取值 `"page"`（不是 `"top"`）：它既不是顶视图也不是立面图 —— 它是一张**能转的页**，
  名字照实写，免得读的人拿"顶视图"的旧想象去理解它。
"""

ORIGIN_VIEW_LABEL = "原图视角（相机沿 +X 看）"
"""那张页上**要写出来**的那行字（**一字不差**）—— 就是 3D 场景里「原图视角」箭头的文字部分。

为什么要有这个箭头（2026-10-04 用户原话：「生成顶视图加个原图视角箭头，不然用户看不懂」）：
俯视时画面**看不出哪边是「原图往里看」的方向**。本工程坐标系 `world.coordinate_system`
定的是 **X 前进**（承载参考图的纵深链），所以在世界 Y 最小边外侧画一条沿 +X 的箭头、
写上这句话，用户才分得清朝向（`web/preview3d.html` 的 `buildOriginArrow()` / `buildOriginLabel()`）。

⚠ 位置口径仍由 `origin_view_spec()` 给死（**别让画图的人自己摆** —— 那是第二套口径）；
  它现在只服务"缺这四个字会记一条提示"（`figure_verdicts()`）与人工核对，**不再指挥谁去画图**。
"""

ORIGIN_VIEW_ARROW_GAP_M = 2.0
"""箭头离世界范围**外沿**让出多少米（贴在**世界 Y 的最小边之外**，免得压到任何一行）。

⚠ 这是**口径值，不是实测值**。为什么 2 m 够：布局**都在世界范围之内**（`ground` 白膜顶到世界边界，
见 `bounds_check()` 那三级判据），往外让 2 m 就落在空地上，又不至于跑到图框外面去。
位置由 `origin_view_spec()` 算 —— 与 `_compose_build_rows()` 算 `z_base_cm` / `z_top_cm` 同一条纪律：
**口径只能有一份**（`drawing` 段删了，页由代码自动生成，谁都不用再照着画）。
"""

ORIGIN_VIEW_MISSING_WARNING = "顶视图缺「原图视角」箭头 —— 用户会看不懂朝向"
"""顶视图正文里没有「原图视角」这四个字时记的那条**提示**（⚠ **只报不拦**，见 `figure_verdicts()`）。

⚠ 第一版**只报不拦**是用户拍板的：先看效果，**别把已有旧图一变就作废**
  —— 所以它**不进** `ok`、不进拒收判据、不影响 `missing_views`。
"""

DIM_CN = {"plan": "位移 / 朝向", "size": "大小（占地 / 缩放）",
          "height": "高度 / Z 标高 / Z 倍率"}
"""维度的中文名（话术一处写清，免得报文里几种说法）。

⚠ 它现在**只用于说明**（"这一版动了哪一维" → 图里重点标哪几行）——
  **不再决定出哪几张图**（那是 `REQUIRED_VIEWS` 的常事）。"""


def _aspects_from_signature(sig: str, kind: str) -> dict[str, str]:
    """把一份**逐行签名**按维度切成三组 → `{"plan": ..., "size": ..., "height": ...}`；切不开给 `{}`。

    分组规则见 `ASPECT_FIELDS`（**签名里字段的顺序由 `row_signature()` 说了算**，这里只按下标取）。
    ⚠ 长度对不上（老格式 / 手改坏了）→ `{}` = **不猜**，调用方按"每一维都变了"严判。

    为什么要能**从签名反推**：台账里的 `accepted_rows` 是分维判据上线**之前**记下的历史数据。
    不能反推的话，这些行会永远落进"判不出维度"那一档（多一道无用功）；
    能反推，旧基线也判得出"这次只动了位移"或"只动了高度"。
    """
    try:
        fields = json.loads(sig)
    except (TypeError, ValueError):
        return {}
    if not isinstance(fields, list):
        return {}
    kind = str(kind or "")
    idx = ASPECT_FIELDS.get(kind)
    if not idx:
        return {}
    if kind == "asset" and len(fields) not in (7, 8):
        return {}
    if kind == "whitebox" and len(fields) != 7:
        return {}
    out: dict[str, str] = {}
    for dim, positions in idx.items():
        vals = [fields[i] if i < len(fields) else None for i in positions]
        out[dim] = json.dumps(clean_numbers(vals), ensure_ascii=False)
    return out


def row_aspects(row: dict, kind: str) -> dict[str, str]:
    """一行的**按维度分组签名** → `{"plan": ..., "size": ..., "height": ...}`（2026-09-30 加）。

    用途只有一个：回答「**这一版动的是位移 / 大小 / 高度里的哪几样**」。
    ⚠ **它不再决定"要出哪几张图"**（2026-10-11 起恒出**一张** three.js 页，见 `REQUIRED_VIEWS`）——
      现在只用来**说清这一版动了什么**（报文的重点提示）。

    ⚠ 实现是"拿 `row_signature()` 的结果按下标切"（`_aspects_from_signature()`）——
      **不另抄一份字段清单**，免得哪天签名里加了字段、这边忘了跟，两处口径悄悄分叉。
    """
    return _aspects_from_signature(row_signature(row, kind), kind)


def plan_row_kinds(plan: dict) -> dict[str, str]:
    """`{label: "asset" | "whitebox"}` —— 从签名反推维度时要知道这一行是哪种（键与签名表一致）。"""
    out: dict[str, str] = {}
    assets = list(plan.get("assets") or [])
    for i, row in enumerate(assets, 1):
        lab = str((row or {}).get("label") or "").strip() if isinstance(row, dict) else ""
        out[lab or f"<第 {i} 行没写 label>"] = "asset"
    for j, row in enumerate(list(plan.get("whiteboxes") or []), 1):
        lab = str((row or {}).get("label") or "").strip() if isinstance(row, dict) else ""
        out[lab or f"<第 {len(assets) + j} 行没写 label>"] = "whitebox"
    return out


def plan_row_aspects(plan: dict) -> dict:
    """`{label: {"plan": 签名, "size": 签名, "height": 签名}}`（一物一行；与 `plan_row_signatures()` 同序同键）。"""
    out: dict = {}
    assets = list(plan.get("assets") or [])
    for i, row in enumerate(assets, 1):
        lab = str((row or {}).get("label") or "").strip() if isinstance(row, dict) else ""
        out[lab or f"<第 {i} 行没写 label>"] = row_aspects(row, "asset")
    for j, row in enumerate(list(plan.get("whiteboxes") or []), 1):
        lab = str((row or {}).get("label") or "").strip() if isinstance(row, dict) else ""
        out[lab or f"<第 {len(assets) + j} 行没写 label>"] = row_aspects(row, "whitebox")
    return out


def plan_row_signatures(plan: dict) -> dict:
    """`{label: 几何签名}`（一物一行；label 重复时后一条覆盖前一条 —— 撞名另有检查）。"""
    out: dict[str, str] = {}
    assets = list(plan.get("assets") or [])
    for i, row in enumerate(assets, 1):
        lab = str((row or {}).get("label") or "").strip() if isinstance(row, dict) else ""
        out[lab or f"<第 {i} 行没写 label>"] = row_signature(row, "asset")
    for j, row in enumerate(list(plan.get("whiteboxes") or []), 1):
        lab = str((row or {}).get("label") or "").strip() if isinstance(row, dict) else ""
        out[lab or f"<第 {len(assets) + j} 行没写 label>"] = row_signature(row, "whitebox")
    return out


def latest_confirmed_plan() -> tuple[Path | None, dict | None]:
    """归档里**最近一版被确认过的**数据（老数据的回退基线）。找不到给 `(None, None)`。"""
    try:
        cands = [p for p in ARCHIVE_DIR.glob(CONFIRMED_ARCHIVE_GLOB) if p.is_file()]
    except OSError:
        return (None, None)
    if not cands:
        return (None, None)
    try:
        newest = max(cands, key=lambda p: (p.stat().st_mtime, p.name))
        doc = json.loads(newest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return (None, None)
    return (newest, doc if isinstance(doc, dict) else None)


def change_set(plan: dict) -> dict:
    """**这一版相对"上一次用户确认过的那一版"改了什么** → 局部确认的判据（只读、离线）。

    输出：`{local, baseline, added, changed, removed, same, required_labels, changed_dims,`
    `required_views, views_why, note}`
      · `required_labels` = 这一版**页里必须出现**的 label（新增 + 改动 + 删掉的行）；
      · `required_views` = **恒为一张**（`["page"]`，2026-10-11 用户定案）：
        一张 three.js 页（`views/<plan 名>_view.html`，落盘时自动生成）。见 `REQUIRED_VIEWS`；
      · `local=False` 表示**没有基线**（第一次搭）→ `required_labels` 是**每一行**（全量）。

    基线按优先级取：
      ① 验收台账 `views/acceptance.json` 的 `accepted_rows`（上一次确认那一刻的逐行签名）—— **主口径**；
      ② 归档里最近的 `*_confirmed.json`（旧数据没有 `accepted_rows` 时的回退）；
      ③ 都没有 → **退回全量**。
    ⚠ 基线缺了就退回全量，**绝不宽松放行** —— 判据宁可严，不许漏。
    """
    labels = figure_row_labels(plan)
    cur = plan_row_signatures(plan)

    base_rows: dict[str, str] = {}
    base_aspects: dict = {}                 # 基线的**分维签名**（判"改的是平面还是竖直"用）
    base_where = ""
    try:
        acc = load_acceptance()
    except Exception:                       # noqa: BLE001 —— 台账读不动就当没有基线（退回全量）
        acc = {}
    acc_rows = acc.get("accepted_rows") if isinstance(acc, dict) else None
    if isinstance(acc_rows, dict) and acc_rows:
        base_rows = {str(k): str(v) for k, v in acc_rows.items()}
        # 分维签名（判"改的是平面还是竖直"）：
        #   ① 台账里有就用它（2026-09-30 起确认时会记）；
        #   ② 老台账没有 → **从逐行签名反推**（字段顺序是代码定的，见 `_aspects_from_signature()`）；
        #   ③ 连反推都失败（老格式 / 手改坏了）→ 那个 label 不在表里 → 下文按"两个维度都变"严判。
        _aspec = acc.get("accepted_aspects") if isinstance(acc, dict) else None
        if isinstance(_aspec, dict) and _aspec:
            base_aspects = {str(k): v for k, v in _aspec.items() if isinstance(v, dict)}
        else:
            _kinds = plan_row_kinds(plan)
            base_aspects = {lab: _aspects_from_signature(sig, _kinds.get(lab, ""))
                            for lab, sig in base_rows.items()}
        # ⚠ 这里**不写轮次数字**：落盘时 `round` 已经加过 1，写出来会变成
        #   "第 23 轮确认过的那一版"，而基线其实是第 22 轮那版几何 ——
        #   2026-09-26 实测抓到的文案 bug（判据是对的，错的是这句话）。
        src = str(acc.get("accepted_rows_from") or "").strip()
        base_where = ("验收台账里的已确认基线（由归档 " + src + " 回填）" if src
                      else "验收台账里『已确认』的那一版几何")
    else:
        path, doc = latest_confirmed_plan()
        if doc is not None and path is not None:
            base_rows = plan_row_signatures(doc)
            base_aspects = plan_row_aspects(doc)    # 归档里是整份数据 → 分维签名现算得出来
            base_where = f"归档 `views/archive/{path.name}`"

    if not base_rows:
        return {
            "local": False, "baseline": "", "added": [], "changed": [], "removed": [],
            "same": 0, "required_labels": labels,
            # 没有基线 = **全量一轮**（第一次搭 / 换了图）：整表都是新摆的 ——
            # 位移、大小、高度**三样都算动过**（维度只用于"重点标哪几行"；那一张页本来就恒出）。
            "changed_dims": ["plan", "size", "height"],
            "required_views": list(REQUIRED_VIEWS),
            "views_why": ("还没有『被确认过的基线』（第一次搭 / 换了图）⇒ **全量一轮**："
                          "整张表都是新摆的，页里要出现每一行；**那一张页照出**"
                          "（落盘时由代码自动生成，见 `figure3d.py`）。"),
            "note": ("还没有『被确认过的基线』—— 这一版按**全量**判：页里要出现放置表"
                     f"**每一行**的 label（共 {len(labels)} 行），**一张 three.js 页就够**。"),
        }

    added = [l for l in cur if l not in base_rows]
    removed = [l for l in base_rows if l not in cur]
    changed = [l for l in cur if l in base_rows and cur[l] != base_rows[l]]
    same = [l for l in cur if l in base_rows and cur[l] == base_rows[l]]
    changed_set, added_set = set(changed), set(added)
    req = [l for l in labels if l in changed_set or l in added_set]
    req += [l for l in removed if l not in req]        # 删掉的行也要点到（用户得看见"它没了"）

    # --- 「变的是哪一维」→ **只用来重点提示**（2026-09-30 加；2026-10-11 起不再决定出哪几张）----
    # 当年为什么有它：变更集原先只回 label 列表，**平面改和高度改的输出一模一样** ——
    #   于是 agent 一律按老习惯画俯视图，而高度变化在俯视图上完全看不见（用户原话：
    #   「我要求的是改高度……但是这个图画的还是顶视图……**当调整的是高度时生成的图得是
    #   正视图或者左右视图**」）。
    # ⚠ **现在那张页可转**（顶视图 / 原图视角 / 沿街侧视都是页上的按钮）⇒ 这个理由不成立了，
    #   所以维度**不再决定出哪几张**（恒一张，见 `REQUIRED_VIEWS`），只用来在报文里说清动了什么。
    #   判据：把"改动 + 新增"的行按**维度签名**再比一次（`row_aspects()`）。
    cur_aspects = plan_row_aspects(plan)
    dims: set[str] = set()
    dims_blind: list[str] = []
    for lab in list(changed) + list(added):
        a = cur_aspects.get(lab) or {}
        b = base_aspects.get(lab)
        if not isinstance(b, dict):
            # 基线里**没有维度签名**（这份台账是 `scale_z` / 分维判据上线**之前**确认的）。
            # ⚠ 与"基线缺了就退回全量"同一条纪律：**绝不宽松放行** —— 判不出来就按"每一维都变了"算。
            if lab in added_set:
                dims.add("plan")            # 新增行：先让人看见它摆在哪（位移口径）
            else:
                dims_blind.append(lab)
            continue
        for dim in ("plan", "size", "height"):
            if str(a.get(dim)) != str(b.get(dim)):
                dims.add(dim)
    if dims_blind:
        dims.update({"plan", "size", "height"})
    if removed:
        dims.add("plan")                    # 删掉的行要在页面里点到"它没了"

    # --- 视图要求 = **恒出一张**（2026-10-11 用户定案，见 `REQUIRED_VIEWS`）----------------
    required_views = list(REQUIRED_VIEWS)
    if changed or added or removed:
        views_why = ("**每一版都出一张 three.js 页**（落盘时由代码自动生成；可转、可缩、可点，"
                     "顶视图 / 原图视角 / 沿街侧视都是页上的按钮）。这一版动到的维度"
                     "（**只用来提示重点看哪儿**）："
                     + "、".join(DIM_CN[d] for d in ("plan", "size", "height") if d in dims)
                     + "。")
        if dims_blind:
            views_why += (f"；⚠ 其中 {len(dims_blind)} 行在基线里**没有维度签名**"
                          "（旧台账，判不出动的是哪一维）—— 那一张页照出、照验。")
    else:
        views_why = ("**每一版都出一张 three.js 页** —— 这一版与基线逐行一致，"
                     "页照出、照验（「没有图不许让用户确认」这条闸不因变更集为空而失效）。")
    return {
        "local": True, "baseline": base_where,
        "added": added, "changed": changed, "removed": removed,
        "same": len(same), "required_labels": req,
        "changed_dims": [d for d in ("plan", "size", "height") if d in dims],
        "required_views": required_views,
        "views_why": views_why,
        "note": (f"**局部一轮**（基线：{base_where}）：新增 {len(added)} / 改动 {len(changed)} / "
                 f"删除 {len(removed)}；页里只需出现这 **{len(req)} 行**的 label；"
                 f"另外 {len(same)} 行**没动**（它们早已确认过）。"
                 f"视图要求：{views_why}"),
    }


def origin_view_spec(plan: dict) -> dict:
    """**「原图视角」箭头**的规格（机器可读）—— **口径的唯一出处就是本函数**（不是 `get_plan()` 的派生段）。

    ⚠ 它前身是 `get_plan().drawing.origin_view` —— **那个 `drawing` 段 2026-10-11 已整段删除**
      （页改由代码自动生成 ⇒ 没人再需要"给画图的人用的几何"）。要看它当版的样子：
      打开 `views/<plan 名>_view.html`；要看这一版必须覆盖哪些行：跑 `--figure-checklist`。

    为什么由**代码给死**（2026-10-04 用户要求：「生成顶视图加个原图视角箭头，不然用户看不懂」）：
      顶视图是俯视，**看不出哪边是「原图往里看」的方向**；箭头的**位置与朝向**要是让画图的人
      自己估，就是**第二套口径** —— 而图是用户唯一能判断的依据
      （与 `_compose_build_rows()` 算 `z_base_cm` / `z_top_cm` 同一条纪律：**别自己推**）。

    形状与语义：
      · `axis` = `"X"` —— 原图视角 = **沿 +X 看**（就是 `world.coordinate_system.forward_axis`）；
      · `label` = 图上必须写出的文字（**一字不差**，见 `ORIGIN_VIEW_LABEL`）；
      · `arrow_from_m` / `arrow_to_m` = `[X, Y]`（**米、世界坐标**，与 `plan_v1.json` 同口径）：
        箭头**横贯整幅、指向 +X** —— X 取世界的**最小边 → 最大边**；
        Y 取**世界 Y 的最小边再往外让 `ORIGIN_VIEW_ARROW_GAP_M` 米**
        （那里在布局之外，**不会压到任何一行**）；
      · `where` = 给人看的一句话（放哪儿、别压什么、指向哪边）；`note` = 怎么画、缺了会怎样。

    ⚠ **只读、离线、不碰 UE**：世界范围只取 `plan["world"]` 的 `center` / `size`（米）；
      取不到就退回同一处的 `bounds`；两样都没有 → 两个坐标字段给 `None` + `warning` 写明原因
      （**不猜、不拿别的数顶替** —— 与硬规则 6「预估值不许伪装成实测值」同源）。
    ⚠ 它**不进几何指纹**（是派生值，不是 plan 的一部分）—— 与 `_compose_build_rows()` 算出来的 z 口径同一性质。
    """
    spec: dict = {
        "axis": "X",
        "label": ORIGIN_VIEW_LABEL,
        "arrow_from_m": None,
        "arrow_to_m": None,
        "gap_m": float(ORIGIN_VIEW_ARROW_GAP_M),
        "where": ("放在**图框下沿、世界范围之外**（世界 Y 最小边再往外 "
                  + f"{ORIGIN_VIEW_ARROW_GAP_M:g} m），**别压到任何一行**；"
                  + "箭头指向 **+X**（画面里「往原图纵深去」的方向）"),
        "note": ("它现在画在**那张 three.js 页的 3D 场景里**"
                 "（`web/preview3d.html` 的 `buildOriginArrow()` / `buildOriginLabel()`，"
                 "这条 spec 就是它的口径来源）："
                 "从 `arrow_from_m` 到 `arrow_to_m` 摆一个指向 +X 的箭头，"
                 + "旁边写上「" + ORIGIN_VIEW_LABEL + "」。"
                 "⚠ 缺「原图视角」这四个字会被 `figure_verdicts()` 记一条**提示** —— "
                 "**只报不拦**，不影响确认与任何既有判据。"),
    }

    world = plan.get("world") if isinstance(plan.get("world"), dict) else {}
    x0 = x1 = y_arrow = None
    why = ""
    try:
        center, size = world.get("center"), world.get("size")
        if (isinstance(center, (list, tuple)) and len(center) >= 2
                and isinstance(size, (list, tuple)) and len(size) >= 2
                and float(size[0]) > 0 and float(size[1]) > 0):
            cx, cy = float(center[0]), float(center[1])
            sx, sy = float(size[0]), float(size[1])
            x0, x1 = cx - sx / 2.0, cx + sx / 2.0            # 横贯整幅（X 最小边 → 最大边）
            y_arrow = cy - sy / 2.0 - float(ORIGIN_VIEW_ARROW_GAP_M)   # 世界 Y 最小边再往外让
        else:
            # 回退：同一处的 `bounds`（`normalize_world()` 一定一起写，两者互为备份）
            bounds = world.get("bounds") if isinstance(world.get("bounds"), dict) else {}
            bmin, bmax = bounds.get("min"), bounds.get("max")
            if (isinstance(bmin, (list, tuple)) and len(bmin) >= 2
                    and isinstance(bmax, (list, tuple)) and len(bmax) >= 2):
                x0, x1 = float(bmin[0]), float(bmax[0])
                y_arrow = float(bmin[1]) - float(ORIGIN_VIEW_ARROW_GAP_M)
            else:
                why = ("plan 的 `world` 里既没有可用的 `center` / `size`，也没有 `bounds` —— "
                       "箭头位置算不出来（**不猜**）")
    except (TypeError, ValueError) as exc:
        x0 = x1 = y_arrow = None
        why = (f"plan 的 `world.center` / `world.size` 不是数字"
               f"（{type(exc).__name__}: {exc}）—— 箭头位置算不出来")

    if x0 is not None and x1 is not None and y_arrow is not None:
        spec["arrow_from_m"] = [round(x0, 4), round(y_arrow, 4)]
        spec["arrow_to_m"] = [round(x1, 4), round(y_arrow, 4)]
    else:
        spec["warning"] = why or "箭头位置没算出来（原因未记录）"
    return spec


def _squeeze_label(text: str) -> str:
    """比对用：去掉**全部空白** —— 图里写 `行道树 右 #1` 还是 `行道树右#1` 算同一个。"""
    return "".join(str(text).split())


def figure_label_coverage(plan: dict, text: str, labels: list[str] | None = None) -> dict:
    """判一份**图的正文**有没有把该点到的行都点到 → `{ok, total, missing, note}`。只读、离线。

    判据（用户 2026-09-25 要求：「画图让 Agent 不要漏位置坐标表任何一个元素」）：
      要覆盖的每一行 `label` 必须出现在正文里。比对细节：
        · 去掉全部空白再比（容忍空格写法不同）；
        · 命中处**后面不能紧跟数字** —— 否则 `停放车辆 #1` 会被 `停放车辆 #10` 假命中。

    ⚠ `labels=None` = 覆盖**放置表每一行**（第一次搭、没有基线时的全量口径）；
      传 `labels=[...]` = 只覆盖这几行（**局部轮**的变更集，见 `change_set()`）。
      2026-09-26 用户要求「所有都局部改，不要全部重做」之前，这里恒等于全量。

    为什么必须有它：以前只要图里写了指纹就算"认账"，于是图上把 30 棵树缩成一句
    「行道树 30 棵」也能过关 —— **真漏了一行没人看得出来**。图是用户唯一能判断的依据，
    漏一个元素就是漏掉一次判断机会。
    """
    rows = figure_row_labels(plan) if labels is None else [str(x) for x in labels]
    hay = _squeeze_label(text)
    missing: list[str] = []
    for lab in rows:
        needle = _squeeze_label(lab)
        if not needle:
            missing.append(lab)
            continue
        found = False
        start = 0
        while True:
            i = hay.find(needle, start)
            if i < 0:
                break
            tail = hay[i + len(needle): i + len(needle) + 1]
            if not tail.isdigit():
                found = True
                break
            start = i + 1
        if not found:
            missing.append(lab)
    return {
        "ok": not missing,
        "total": len(rows),
        "missing": missing,
        "note": ("一行不漏" if not missing
                 else f"漏了 {len(missing)}/{len(rows)} 行：{'、'.join(missing[:8])}"),
    }


def figure_verdicts(plan: dict, plan_mtime: float | None = None,
                    kind: str = "plan") -> list[dict]:
    """逐张判 `views/` 里的图**认不认这份数据** → `[{name, path, ok, why}]`（只读、离线）。

    `kind` 选**哪一路**（2026-09-30 加；2026-10-11 起**只剩一路**，这个形参保留只为不改调用方）：
      · `"page"`（默认，也是唯一有意义的值）= 那张 three.js 页，文件名 `plan_*_view.html`。
    ⚠ 形参 `kind` **不再改变判据**：判据只有下面这一份（见 `FIGURE_GLOBS`）。

    判据（两条**都要过**）：
      - **正文里要有当前几何指纹的前 10 位** —— 证明"这一页是拿这份数据生成的"；
      - **正文里要出现"该覆盖的每一行"的 `label`** —— 证明"这一页**没漏东西**"
        （判据实现在 `figure_label_coverage()`）。

    ⚠ 除上面两条判据外，**多一条「只报不拦」的提示**（2026-10-04 加，用户要求
      「生成顶视图加个原图视角箭头，不然用户看不懂」）：正文里没有「原图视角」这四个字
      → 该 verdict 的 `warnings` 里记 `ORIGIN_VIEW_MISSING_WARNING`。
      ⚠ 它**不进 `ok`**、不进拒收判据、不影响 `missing_views` —— **只报不拦**；
      箭头的口径见 `origin_view_spec()`。

    ⚠ **要覆盖哪些行，2026-09-26 起按"变更集"算**（用户要求：「用户想局部改，**所有都局部改，
      不要全部重做**」）：有已确认的基线时，只要求出现**这一次动了的那几行**的 label；
      **没有基线（第一次搭）时仍是全量**（每一行都要出现）。见 `change_set()`。

    ⚠ **位图（png / jpg / jpeg / webp / gif）与旧机制产物一律不认账**：本函数只认
      `FIGURE_GLOBS` 命中的 **HTML**（那一页是文本，指纹与行名都在正文里）。
      位图读不出文字 ⇒ 核验不了"漏没漏"；旧机制那份是**上一套**的产物，已经不再生成。

    ⚠ `plan_mtime` 是**保留形参**（老调用方还在传；位图不再按时间放行 ⇒ 它不参与判定）。

    它是 `figure_check()`（确认时把关）、`accepted_figures()`（验收状态）**共用的唯一判据**
    —— 判据只写一处，免得几条路各说各话。
    """
    head = plan_geometry_hash(plan)[:10]
    # ⚠ 走咽喉（`active_plan_path()`）——纪元 2 开了之后基准是 `plan_v2.json`。
    #   它在这里**只当诊断信息用**（"这张比数据新还是老"），不参与任何判定。
    _apath = active_plan_path()
    if plan_mtime is None:
        try:
            plan_mtime = _apath.stat().st_mtime if _apath.exists() else 0.0
        except OSError:
            plan_mtime = 0.0

    # 这一版**页里要覆盖哪些行**（局部轮 = 变更集；没有基线 = 全量）—— 唯一入口见 change_set()
    cs = change_set(plan)
    req = list(cs.get("required_labels") or [])
    scope = ("局部一轮" if cs.get("local") else "全量一轮")

    found: list[Path] = []
    for pattern in FIGURE_GLOBS:
        try:
            found += [p for p in VIEWS_DIR.glob(pattern) if p.is_file()]
        except OSError:
            continue

    out: list[dict] = []
    for path in sorted(set(found)):
        if path.suffix.lower() not in (".html", ".htm"):
            # 非 HTML 一律不认账（理由见本函数 docstring）：只把"新旧"当诊断信息写进理由，
            # 绝不用它放行。
            try:
                age = (f"比 {_apath.name} 新" if path.stat().st_mtime >= plan_mtime
                       else f"比 {_apath.name} 老")
            except OSError:
                age = "取不到修改时间"
            out.append({"name": path.name, "path": path, "ok": False, "warnings": [],
                        "why": (f"不是 HTML，**核验不了有没有漏行**（这个{age}）；"
                                "认账的是落盘时自动生成的那张 `plan_<版本>_view.html`")})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            out.append({"name": path.name, "path": path, "ok": False, "warnings": [],
                        "why": f"读不动（{exc}）"})
            continue

        # --- 「原图视角」箭头：**只报不拦**（2026-10-04 用户要求）------------------------
        # 用户原话：「生成顶视图加个原图视角箭头，不然用户看不懂」—— 俯视时看不出哪边是
        #   「原图往里看」的方向（本工程 X 前进 = 参考图的纵深链）。
        # ⚠ **只报不拦**：这条**不进** `ok`、**不进**任何拒收判据、**不影响** `missing_views` ——
        #   只是挂在 verdict 的 `warnings` 里，由 `figure_check()` 汇总带出去。
        arrow_warn = ""
        if "原图视角" not in text:
            arrow_warn = ORIGIN_VIEW_MISSING_WARNING
        warns = [arrow_warn] if arrow_warn else []

        if head not in text:
            out.append({"name": path.name, "path": path, "ok": False, "warnings": warns,
                        "why": f"正文里没有当前指纹 {head}…（是别的版本生成的）"})
            continue
        cov = figure_label_coverage(plan, text, labels=req)
        if not cov["ok"]:
            shown = "、".join(cov["missing"][:6])
            tail = "" if len(cov["missing"]) <= 6 else f" 等 {len(cov['missing'])} 行"
            out.append({"name": path.name, "path": path, "ok": False, "warnings": warns,
                        "why": (f"指纹对得上，但（{scope}）**漏了 {len(cov['missing'])}/{cov['total']} 行**："
                                f"{shown}{tail}")})
            continue
        out.append({"name": path.name, "path": path, "ok": True, "warnings": warns,
                    "why": (f"正文里有当前指纹 {head}…，"
                            + (f"且本次该覆盖的 {cov['total']} 行**一行不漏**（{scope}，认账）"
                               if cov["total"] else f"且这一版**没有任何改动**（{scope}，认账）"))})
    return out


def figure_check(plan: dict) -> dict:
    """判「views/ 里**该有的那一页**认不认这份数据」→
    `{ok, figures, required_views, missing_views, required_labels, local, changed_dims, note[, needs_labels]}`。
    ⚠ **2026-10-04 多一个 `warnings`**（**只报不拦**的提示，如「页里缺「原图视角」四个字」）——
      它**不参与 `ok`**，`ok` 的判据与以前**一字不差**。

    判据见 `figure_verdicts()`：**指纹对得上 + 该覆盖的每一行 `label` 都在页里**。
    ⚠ 「该覆盖的行」= `change_set()["required_labels"]`：**局部轮 = 本次改动的那几行**；
      没有基线（第一次搭）= **全量每一行**（2026-09-26 用户要求「所有都局部改，不要全部重做」）。

    为什么要有它（2026-09-24 收尾清单 #8）：AGENTS 的口径是 **图与数据必须一致 ——
    不一致 = 等于没确认**，但以前全靠人自觉（实测踩到过：图里还写着"本图未经用户确认"，
    而数据早已确认）。2026-09-25 又按用户要求加严：**光有指纹不算数，还得一行都不漏**。

    ⚠ **2026-10-11 起只剩一路**（用户：「整改2阶段，不出图，出three.js」）：原先按
      `required_views` **逐视图**判（缺哪张报哪张）。现在那一页是**落盘时自动生成的** ——
      它该在的时候**必在**（除非出图那一步自己失败，而失败原因由 `write_plan()` 的
      `plan_view.errors` 直接报出来，不靠这里猜）。所以本函数收敛成一路；
      `required_views` / `missing_views` / `view_labels` 三个键**照旧返回**（形状不变），
      值分别是 `["page"]` / `[] 或 ["page"]` / `["three.js 页（落盘自动生成）"]`。

    ⚠ 判不过时**不猜**：说清为什么不算数，让调用方（confirm_plan）拒收；
      缺页时额外附 `needs_labels`（这一版**必须出现在页里**的 label）。
    """
    cs = change_set(plan)
    needs = list(cs.get("required_labels") or [])
    views = [str(v) for v in (cs.get("required_views") or ["page"])]
    view_cn = {"page": "three.js 页（落盘自动生成，可转 / 可缩 / 可点）"}
    view_prefix = {"page": "plan_*_view.html"}

    per_view: dict[str, list[dict]] = {}
    for view in views:
        try:
            per_view[view] = figure_verdicts(plan)
        except OSError:
            per_view[view] = []

    figures = [str(v["name"]) for view in views for v in per_view.get(view, []) if v.get("ok")]
    missing_views = [view for view in views
                     if not any(v.get("ok") for v in per_view.get(view, []))]

    # --- 只报不拦的提示：汇总 `figure_verdicts()` 记下的 `warnings`（2026-10-04 加）------
    # 当前只有一条来源：页里没有「原图视角」四个字（见 `ORIGIN_VIEW_MISSING_WARNING`）。
    # ⚠ **判据一个都没动**：上面的 `figures` / `missing_views` / `ok` 与以前逐字相同 ——
    #   这里只是把提示**带出去**，让调用方（`get_plan()` / `confirm_plan()`）看得见。
    view_warnings = [str(w) for view in views for v in per_view.get(view, [])
                     for w in (v.get("warnings") or [])]

    # 说清现状：认账 / 认不了的话是为什么 / **一张都没有**（并给出命名约定）
    parts: list[str] = []
    stray_hint = ""
    for view in views:
        vs = per_view.get(view) or []
        oks = [v for v in vs if v.get("ok")]
        if oks:
            parts.append(f"{view_cn[view]}：✅ " + "；".join(str(v["why"]) for v in oks))
            continue
        if vs:
            parts.append(f"{view_cn[view]}：❌ "
                         + "；".join(f"{v['name']}：{v['why']}" for v in vs))
            continue
        parts.append(f"{view_cn[view]}：❌ views/ 里**没有** `{view_prefix[view]}` 这张")
        if not stray_hint:
            # ⚠ 2026-09-26 改（客户反馈实测）：以前这里一律说「views/ 里没有图」—— 而客户那头的 agent
            #   **图已经画好了**（名字不合约定），它读到的却是「没有图」，于是**去把文件改了个名**。
            #   现在分开说：**一张都没有** / **有，但没被约定命中**（并说明命名只是约定）。
            # ⚠ 2026-10-11：那一页**不再由人画** ⇒ 这里的话术跟着改（别再叫 agent 去"重命名"）。
            try:
                stray = [p.name for p in VIEWS_DIR.glob("plan_*_view*.htm*") if p.is_file()]
            except OSError:
                stray = []
            if stray:
                stray_hint = ("（views/ 里有 " + str(len(stray)) + " 份没被认账的页："
                              + "、".join(stray[:6]) + ("…" if len(stray) > 6 else "")
                              + "。⚠ 命名只是**代码找页的约定**，真正的判据是**页的内容**"
                                "（当前几何指纹前 10 位 + 该覆盖的每一行 label）—— "
                                "内容不对的（上一版生成的）改名也过不了。）")
            else:
                stray_hint = ("⚠ 那一页**本该由 `write_plan()` 在落盘时自动生成** —— "
                              "它不在，说明出图那一步失败了：看落盘那次报文的 `plan_view.errors`"
                              "（原因会逐条列出来），或手工重出："
                              "`python -m mcp_server.planning.figure3d`。")
    note = "；".join(parts)
    if missing_views:
        note += "。视图要求（为什么会缺）：" + str(cs.get("views_why") or "")
    if stray_hint:
        note += stray_hint
    out = {
        "ok": not missing_views,
        "figures": figures,
        "required_views": views,
        "missing_views": missing_views,
        "view_labels": [view_cn[v] for v in views],
        "changed_dims": list(cs.get("changed_dims") or []),
        "local": bool(cs.get("local")),
        "required_labels": needs,
        # **只报不拦**的提示（2026-10-04 加）：不参与 `ok`，见上面那段注释。
        "warnings": view_warnings,
        "note": note + (f"（{cs['note']}）" if cs.get("note") else ""),
    }
    if missing_views:
        out["needs_labels"] = needs
    return out


# --- 阶段二验收（2026-09-25 用户要求新增）--------------------------------------
#
# 【为什么要单开一个阶段】
#   原来只报 draft / confirmed，中间那段「图已经送去、正在等用户看」**没有状态**，
#   全靠人记 —— 于是 AI 可能在用户还没看图时就往下走，用户说"这里要改"之后
#   也没人记得改过什么。现在改成：
#       **生图 = 阶段二结束 → 进「阶段二验收」**
#       用户在图上看过说"行" → 闸门开，进第三阶段
#       说"要改" → 改参数 → 重落盘（那一页自动重生成）→ 再使用，**循环到点头为止**
#
# 【台账为什么独立成 views/acceptance.json，不写进 plan_v1.json】
#   ① `plan_geometry_hash()` 算的是 plan_v1.json 里**除 confirmation 外**的全部内容 ——
#      往里加"验收记录"的话，每记一次"要改"都会改变指纹、把上一版验收**假作废**
#      （几何根本没变，变的只是记录）；
#   ② 台账坏了大不了重记，规划数据一点不受影响。
#   台账里每条事件都带 `plan_hash`，所以"某一轮验的是哪一版几何"照样查得到。
#
# ⚠ **权威状态由 `acceptance_state()` 现算，不读台账里那个 `state` 字段**：
#   台账里存的 `state` 只是"上次落盘时的快照"，而图常常是**落盘之后**才画好的 ——
#   读它就会拿着"等出图"的旧字样去回答"走到哪了"。这与 status 不落盘是同一条口径。

ACCEPTANCE_PATH = VIEWS_DIR / "acceptance.json"
"""阶段二验收台账（**不参与几何指纹**）：谁 / 何时 / 第几轮 / 验的是哪个几何指纹 / 要改什么。"""

STAGE_ACCEPTANCE = "阶段二验收"
"""阶段名。位置在**阶段二（规划 + 生图）之后、第三阶段之前**。"""

ACCEPTANCE_STATES: dict[str, str] = {
    "not_started": "还没开始：放置表是空的，没有坐标可看",
    "awaiting_figure": "等出图：数据已落盘，但 views/ 里没有认这份数据的图",
    "awaiting_user": "等待用户看图确认：图与数据一致，可以使用给用户了",
    "changes_requested": "用户提了要改的地方：等改参数 → 重落盘（那一页自动重生成）",
    "accepted": "验收通过：闸门已开，可以进第三阶段",
    # ⚠ 2026-10-04（第四阶段 · 微调）：`tuned` **不是阶段二验收的一种状态** —— 它说的是
    #   "这一版是**整体搭建之后按用户原话小改**过的"。与 `accepted` 的关键区别只有一条：
    #   **这一次没有图**（阶段二的"一页 + 用户看页点头"只属于前三阶段）——
    #   依据是 `confirmation.user_quote`（用户原话）+ `confirmation.rows`（改了哪几行、为什么）。
    #   验收方式 = 用户在 UE 里自己看；agent 要把**数字**报出来（例：`Y −33.0 → −32.0`）。
    "tuned": ("微调版：几何已按用户原话改过、**这一次没有图**（如实）—— "
              "可以走第四阶段的点名微调"),
}


def _now() -> str:
    """统一时间戳（UTC ISO）—— 台账里所有时间都走这一个口子，免得两种写法混着。"""
    return datetime.now(timezone.utc).isoformat()


def empty_acceptance() -> dict:
    """空台账的形状（**唯一**定义处：读盘、落盘都按它对齐）。"""
    return {
        "stage": STAGE_ACCEPTANCE,
        "note": ("阶段二验收台账。**不参与几何指纹**；"
                 "只记「这一轮验的是哪个几何指纹、用户说了什么」。"),
        "round": 0,
        "state": "not_started",   # ⚠ 只是快照；权威状态请用 acceptance_state() 现算
        "state_cn": ACCEPTANCE_STATES["not_started"],
        "plan_hash": "",
        "figures": [],
        "updated_at": "",
        "events": [],
        # 上一次 `confirm_plan` 那一刻的 `{label: 几何签名}` —— **局部确认的基线**（2026-09-26）：
        # 有了它，下一轮就能算出"这一版到底改了哪几行"，于是图只需覆盖那几行。
        # ⚠ 基线缺失时 `change_set()` 会**退回全量**（判据宁可严，不许漏）。
        "accepted_rows": {},
        # 「这一版写完之后，agent **还没回读**」的登记（2026-09-26 把软约束变硬，见 pending_readback()）：
        # `None` = 没有待办；有值时 `generate_plan` / `execute_build` 一律拒收，直到调过 `get_plan()`。
        # ⚠ 它在验收台账里、**不在 plan_v1.json 里** —— 所以**不进几何指纹**，不会把用户的确认搞作废。
        "awaiting_readback": None,
        "readback_at": "",
        # 最近一次确认时**用户的原话**（证据链，2026-09-26）：图是 agent 画的、确认也是它调的，
        # 只有这句话能区分"人看过了"和"agent 自己拍的"。见 `record_acceptance()`。
        "accepted_user_quote": "",
        # 「改动窗口」：`request_plan_change()` 打开、`confirm_plan()` 关闭；`generate_plan`
        #   改几何前**必须**核对它（见 `main.py` 的 `_change_request_guard()`）。
        # ⚠ **2026-09-27 修的一个 blocker**：这个键原先**不在本表里**，而 `load_acceptance()`
        #   是按本表的键过滤的（`out.update({k: v for k, v in data.items() if k in out})`）——
        #   于是 `record_change_request()` 写进去的 `true` **永远读不回来**，
        #   那条闸的第三判据恒为假：**凡是被确认过的规划，之后再也改不动**
        #   （实测：`request_plan_change` 记完、台账文件里确实是 `true`，
        #     `generate_plan` 仍然拒收「台账里没有记录」）。**这一行别删。**
        "change_window_open": False,
        # ⚠ **2026-10-04 修（同型 blocker 第二次）**：`tuned_count` / `tuned_rows_total` /
        #   `tuned_note` / `tuned_history` 原先**不在本表里**，而 `load_acceptance()` 是
        #   **按本表的键过滤**的（血账见上面 `change_window_open` 那段）⇒ `mark_tuned()` 写进去的
        #   微调计数**永远读不回来**：八阶段总览 ④ 恒报「tuned_count=0（还没微调过）」、
        #   `tuned_rows_total` 也永远只剩本次那几行。**这四行别删。**
        "tuned_count": 0,
        "tuned_rows_total": 0,
        "tuned_note": "",
        "tuned_history": [],
    }


def pending_readback(acc: dict | None = None) -> dict:
    """「这一版写完之后，agent **还没回读**」→ 非空 dict = 有待办。只读。

    为什么要有它（2026-09-26 用户原话：「不行文档是软约束，有没有强一点的，让 AI 不能忘了这步」）：
      "改完必须回读核对"以前只写在文档里 —— **agent 会漏**（实测漏过一次：一条该删的行残留在
      自己的 plan 里，直到下一次偶然 `get_plan()` 才发现）。现在改成**代码强制**：
      `write_plan()` 写盘时把"待回读"记进验收台账；**只有 `get_plan()`（只读）能清掉它**；
      在清掉之前，`generate_plan` 与 `execute_build` 一律拒收，并把这一版的变更集原样摆出来
      —— 就算 agent 想跳步，也得先把事实读进上下文。
    """
    ledger = acc if isinstance(acc, dict) else load_acceptance()
    pend = ledger.get("awaiting_readback")
    return pend if isinstance(pend, dict) and pend else {}


def mark_awaiting_readback(plan: dict, cs: dict) -> dict:
    """写盘之后记一条"待回读"（**由 `write_plan()` 自动调**，调用方不用记）。"""
    acc = load_acceptance()
    acc["awaiting_readback"] = {
        "plan_hash": plan_geometry_hash(plan),
        "at": _now(),
        "change_set": cs if isinstance(cs, dict) else {},
    }
    save_acceptance(acc)
    return acc["awaiting_readback"]


def mark_readback() -> dict:
    """`get_plan()` 调它：**回读发生了**，把待办清掉（并留一条时间痕）。返回被清掉的那条。"""
    acc = load_acceptance()
    pend = acc.get("awaiting_readback")
    acc["awaiting_readback"] = None
    if isinstance(pend, dict) and pend:
        acc["readback_at"] = _now()
    save_acceptance(acc)
    return pend if isinstance(pend, dict) else {}


def load_acceptance() -> dict:
    """读验收台账；没有 / 读不动 → 返回空台账（第一次跑本来就没有，不是错误）。"""
    data = load_json(ACCEPTANCE_PATH)
    if not isinstance(data, dict):
        return empty_acceptance()
    out = empty_acceptance()
    out.update({k: v for k, v in data.items() if k in out})
    if not isinstance(out.get("events"), list):
        out["events"] = []
    return out


def save_acceptance(acc: dict) -> None:
    """落盘台账。⚠ 写入点只有这一个（与 plan_v1.json 的 `write_plan()` 同理）。"""
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    body = json.dumps(clean_numbers(acc), ensure_ascii=False, indent=2, allow_nan=False)
    ACCEPTANCE_PATH.write_text(body, encoding="utf-8")


def accepted_figures(plan: dict) -> list[str]:
    """**认这份数据**的页（文件名）—— 也就是"该给用户看的那一张"。

    判据只有 `figure_verdicts()` 一份（HTML 看当前指纹前 10 位 **+ 该覆盖的行都在页里**
    —— 局部轮是本次改动的那几行，没有基线才是全部行；位图 / 旧格式一律不认），不另立标准。
    ⚠ **2026-10-11 起只剩一张**（原先两路：平面图 + 立面图；现在一张能转的 three.js 页）——
      那张页由 `write_plan()` 落盘时自动生成，所以它**该在的时候必在**。
    ⚠ 认账的页**一张都没有**时这里返回空表 ——
    验收状态就会停在 `awaiting_figure`，逼着看"出图那一步为什么失败"，而不是"嘴上说页有了"。
    """
    out: list[str] = []
    try:
        out += [v["name"] for v in figure_verdicts(plan) if v.get("ok")]
    except OSError:
        return []
    return out


def _round_events(acc: dict, plan_hash: str) -> list[dict]:
    """台账里属于**这一版几何**的事件（按发生顺序）—— 换了一版几何就自然清零。"""
    return [e for e in (acc.get("events") or [])
            if isinstance(e, dict) and e.get("plan_hash") == plan_hash]


def acceptance_state(plan: dict, acc: dict | None = None,
                     figures: list[str] | None = None) -> str:
    """现算当前验收状态（**唯一判据**）。只读、不写盘。

    判定顺序（先到先算）：
      ⓪ **微调版**（`confirmation.mode == "tuned"`）→ `tuned`（第四阶段 · 微调）
         —— **放在最前面判**，因为它是**唯一一种"没有图也算数"的确认**：
         `mark_tuned()` 会同时写 `confirmed=True`，若不先拦下来，下面第 ② 条会直接把它
         算成 `accepted`（"闸门已开"）—— 那等于**替它假装有图**，而这一版是如实没有图的。
         它**不走**下面"图认不认这份数据"那条路（`figure_check()` 会报"没有认账的页"）。
      ① 两张表都空 → `not_started`（没有坐标可看，谈不上验收）
      ② `confirmation.confirmed` → `accepted`（闸门已开）
      ③ 这一版几何上**最后一条事件**是"要改" → `changes_requested`
         （用户提了改动、还没落到数据里；下次落盘换了指纹，这条自然失效 = 进入新一轮）
      ④ **那一页认账**（`figure_check().ok`，路数见 `REQUIRED_VIEWS` —— 2026-10-11 起是一张）→
         `awaiting_user`（可以给用户看了）
      ⑤ 否则 → `awaiting_figure`（页没出来 / 不认这一版）

    ⚠ **2026-09-30 修**：判据原先只是"**有没有**认账的图"，于是缺其中一路也会报 `awaiting_user`
      —— 而 `confirm_plan` 会**按视图拒收** ⇒ **状态与闸门打架、会骗人**：
      一个不细看的 agent 会拿着一图给用户、用户点头、然后确认被拒（白跑一轮）。
      现在判据统一到 `figure_check()` —— **一处置口径**（2026-10-11 之后只有一路，这条仍然成立：
      `figure_check()` 是"页认不认这份数据"的唯一判据，本函数不另算一遍）。
    """
    if not (plan.get("assets") or plan.get("whiteboxes")):
        return "not_started"
    # ⚠ ⓪ **微调版最先判**（第四阶段 · 微调，2026-10-04 加）：`mode == "tuned"` 就直接收工 ——
    #   别往下走。它的 `confirmation.confirmed` 也是 true，落到第 ② 条会被算成 `accepted`
    #   （那会**替它假装有图**）；而它的确**没有图**（本轮不生成页、也不要求补）。
    if str((plan.get("confirmation") or {}).get("mode") or "") == "tuned":
        return "tuned"
    if bool((plan.get("confirmation") or {}).get("confirmed")):
        return "accepted"
    ledger = acc if isinstance(acc, dict) else load_acceptance()
    h = plan_geometry_hash(plan)
    figs = accepted_figures(plan) if figures is None else figures
    mine = _round_events(ledger, h)
    if mine:
        last = mine[-1]
        if last.get("kind") == "change_requested":
            return "changes_requested"
        if last.get("kind") == "accepted":
            return "accepted"
    # ⚠ 判据统一到 `figure_check()`：**那一页认账**才算"可以给用户看了"。
    #   `figs` 与 `views_ok` 现在指向同一件事（只有一路）—— 保留两者是为了**异常那一侧**
    #   仍然从严（判不动 = 按"没齐"处理，宁可让 agent 去查出图为什么失败，也不能骗它去确认）。
    try:
        views_ok = bool(figure_check(plan).get("ok"))
    except Exception:                       # noqa: BLE001
        views_ok = False
    return "awaiting_user" if (views_ok and figs) else "awaiting_figure"


def pending_changes(plan: dict, acc: dict | None = None) -> list[str]:
    """用户提了、**还没落到数据里**的改动点（当前这一版几何上的 `change_requested`）。"""
    ledger = acc if isinstance(acc, dict) else load_acceptance()
    h = plan_geometry_hash(plan)
    out: list[str] = []
    for ev in _round_events(ledger, h):
        if ev.get("kind") == "change_requested":
            out = [str(x) for x in (ev.get("items") or [])]
        elif ev.get("kind") == "plan_written":
            out = []          # 这一版是新落盘的：上一版的改动要求已过期
    return out


def enter_acceptance(plan: dict) -> dict:
    """把「这一版几何进了验收」记一笔 —— **由 `write_plan()` 自动调，调用方不用记**。

    做三件事：
      ① 几何指纹**变了 = 新的一轮**；同一版重复落盘**不重复记轮次**，只刷新图列表；
      ② 追加一条 `plan_written` 事件（带这一轮的几何指纹 + 认账的图）；
      ③ 刷新台账里的快照字段（`state` / `figures` / `plan_hash` / `updated_at`）。
    ⚠ `state` 只是快照，**权威值请用 `acceptance_state()` 现算**
      （图常在本函数跑完之后才画好，快照会滞后）。
    """
    acc = load_acceptance()
    h = plan_geometry_hash(plan)
    figs = accepted_figures(plan)
    events = list(acc.get("events") or [])
    same = [e for e in events
            if isinstance(e, dict) and e.get("plan_hash") == h
            and e.get("kind") == "plan_written"]
    if same:
        same[-1]["figures"] = figs              # 同一版重复落盘：只是图后来画好了
    else:
        acc["round"] = int(acc.get("round") or 0) + 1
        events.append({
            "at": _now(), "round": acc["round"], "kind": "plan_written",
            "plan_hash": h, "figures": figs,
            "note": "这一版几何进验收（生图 = 阶段二结束）",
        })
    acc["events"] = events
    acc["plan_hash"] = h
    acc["figures"] = figs
    acc["state"] = acceptance_state(plan, acc, figs)
    acc["state_cn"] = ACCEPTANCE_STATES.get(acc["state"], acc["state"])
    acc["updated_at"] = _now()
    save_acceptance(acc)
    return acc


def record_change_request(plan: dict, items: list[str], by: str, reason: str = "") -> dict:
    """记「用户看图后要改什么」→ 状态变 `changes_requested`，等 AI 改参数重落盘。

    为什么必须落盘（而不是只留在对话里）：用户说的是"第 3 栋往左 5 m"这种**具体条目**，
    对话一长就翻不到了；而且这些条目**不是几何**（不该改指纹），所以进台账、不进 plan_v1.json。
    """
    acc = load_acceptance()
    h = plan_geometry_hash(plan)
    acc["events"] = list(acc.get("events") or []) + [{
        "at": _now(), "round": int(acc.get("round") or 0), "kind": "change_requested",
        "by": by, "items": [str(x) for x in (items or [])], "reason": reason,
        "plan_hash": h,
        "note": "用户看图后提出要改的地方（还没落到数据里）",
    }]
    acc["plan_hash"] = h
    # ⚠ **打开「改动窗口」**（2026-09-26 加）：它表示"要改什么 / 谁要的"已经留痕 ——
    #   `generate_plan` 改几何前会核对它（见 `main.py` 的 `_change_request_guard()`）。
    #   一直开到下一次 `record_acceptance()`（用户点头）为止，所以一轮里可以改多次。
    #   为什么要有它：客户 agent 自评原话「用户提改动要调 request_plan_change —— 一次都没调过，
    #   你每次说要改，我直接 patch，没走变更台账」—— 于是台账回答不了"用户说了什么 ↔ 数据变成什么"。
    acc["change_window_open"] = True
    acc["state"] = "changes_requested"
    acc["state_cn"] = ACCEPTANCE_STATES["changes_requested"]
    acc["updated_at"] = _now()
    save_acceptance(acc)
    return acc


def record_acceptance(plan: dict, by: str, user_quote: str = "", figure: str = "",
                      figure_age_s: float | None = None) -> dict:
    """记「这一轮验收通过」—— 由 `confirm_plan()`（MCP 工具 / CLI）落痕时调。

    ⚠ 2026-09-26 起**额外记下这一版的逐行几何签名**（`accepted_rows`）：它是下一轮
      「局部确认」的**基线** —— 没有它，下一轮就无从知道"改了哪几行"，只能退回全量重画。
      签名只装几何字段（不含 `note`），所以改说明文字不会触发重画（见 `row_signature()`）。
    ⚠ 同时记下**证据链**（`user_quote` 用户原话 / `figure` 认账的那张图 / `figure_age_s` 图龄）：
      图是 agent 画的、确认是 agent 调的 —— 只有"用户说了什么 + 图使用了多久"能事后被人审计。
    """
    acc = load_acceptance()
    h = plan_geometry_hash(plan)
    rows = plan_row_signatures(plan)
    aspects = plan_row_aspects(plan)        # 分维签名（判"改的是平面还是竖直" → 要出哪几张图）
    ev = {
        "at": _now(), "round": int(acc.get("round") or 0), "kind": "accepted",
        "by": by, "plan_hash": h,
        "rows": rows,
        "user_quote": str(user_quote or ""),
        "figure": str(figure or ""),
        "note": "用户看过图并确认（阶段二验收通过）",
    }
    if figure_age_s is not None:
        ev["figure_age_s"] = round(float(figure_age_s), 1)
    acc["events"] = list(acc.get("events") or []) + [ev]
    acc["plan_hash"] = h
    acc["figures"] = accepted_figures(plan)
    acc["accepted_rows"] = rows
    acc["accepted_aspects"] = aspects
    acc["accepted_user_quote"] = str(user_quote or "")
    # 基线现在来自**这一次真实确认**，不再是"从归档回填来的" —— 把回填来源标掉，
    # 免得 change_set() 里那句话一直指着旧归档文件（会说错"基线是哪一版"）。
    acc.pop("accepted_rows_from", None)
    acc.pop("accepted_aspects_from", None)
    # ⚠ 用户点头 = 这一轮结束 → **关掉「改动窗口」**（与 `record_change_request()` 配对）。
    #   下一次要改几何，必须重新留下"改什么 / 谁要的"的记录 —— 见 `main.py`
    #   `_change_request_guard()`。用户提的就记他的原话；自查修正就 `by="agent 自查"`。
    acc["change_window_open"] = False
    acc["state"] = "accepted"
    acc["state_cn"] = ACCEPTANCE_STATES["accepted"]
    acc["updated_at"] = _now()
    save_acceptance(acc)
    return acc


def acceptance_view(plan: dict, tail: int = 8) -> dict:
    """给报告用的一份验收摘要（**现算**：不写盘、不改台账）。"""
    acc = load_acceptance()
    state = acceptance_state(plan, acc)
    return {
        "stage": STAGE_ACCEPTANCE,
        "round": int(acc.get("round") or 0),
        "state": state,
        "state_cn": ACCEPTANCE_STATES.get(state, state),
        "plan_hash": plan_geometry_hash(plan),
        "figures": accepted_figures(plan),
        "pending_changes": pending_changes(plan, acc),
        # 「改动窗口」开着 = 已经记过"要改什么/谁要的"，`generate_plan` 才允许改几何
        # （见 `main.py` 的 `_change_request_guard()`）。开：request_plan_change；关：confirm_plan。
        "change_window_open": bool(acc.get("change_window_open")),
        # 这一版相对"上一次确认过的基线"改了哪几行 —— 图只需覆盖它（局部轮），
        # 或"没有基线 → 全量"（见 change_set()）。给工具面用，好让 agent 知道该画哪几行。
        "change_set": change_set(plan),
        # "这一版还没回读"（代码强制的回读闸）：非空 = 下一步写入/搭建会被拒收，
        # 直到调过 `get_plan()`。见 `pending_readback()`。
        "awaiting_readback": pending_readback(acc),
        "updated_at": acc.get("updated_at", ""),
        "ledger_path": str(ACCEPTANCE_PATH),
        # 累计微调（第四阶段）—— **只报数、不设闸**：要不要为它回一次全图，由用户按需决定。
        "tuned_count": int(acc.get("tuned_count") or 0),
        "tuned_rows_total": int(acc.get("tuned_rows_total") or 0),
        "tuned_note": str(acc.get("tuned_note") or ""),
        "history": [e for e in (acc.get("events") or [])
                    if isinstance(e, dict)][-tail:],
    }


def write_plan(plan: dict, target: Path | None = None) -> dict:
    """落盘 plan —— **全工程唯一写入点**。返回
    `{"path": 写到哪个文件, "epoch": 纪元号, "archived": 留档文件名,`
    `"removed_figures": []（保留键，见下）, "kept_figures": []（保留键）, "acceptance": {...},`
    `"plan_view": {...}}`。

    `target`（2026-10-04 加）＝ **写到哪个文件**；默认 `active_plan_path()` —— 也就是
    「纪元 2 开了就写 `plan_v2.json`，否则写 `plan_v1.json`」。**只有换纪元那一步**
    （`promote_to_epoch2()`）会显式传 `plan_v2.json`；别处一律不传（走咽喉，别自己拼路径）。

    写盘时顺带做三件"不许靠人记得"的事：
      - **给上一版留档**（收尾清单 #4）：闸门要回答的是"我们现在搭的，是不是当初确认的那一版"，
        可每次都覆盖同一个文件 —— 数据本体没了，只剩一个指纹数字。所以覆盖前把上一版
        原样复制进 `views/archive/`（文件名带 UTC 时间戳）。内容一模一样时不重复留档。
        ⚠ **归档名带纪元前缀**（`plan_v1_<时间戳>_<confirmed|draft>.json` / `plan_v2_...`）——
        不让 v1 / v2 的留档互相覆盖，也让"这一版属于哪个纪元"一眼看得出来。
      - **进「阶段二验收」**（2026-09-25 用户要求）：落盘 = 这一版几何进验收（`enter_acceptance()`）。
        收在这里的理由与上一条一样 —— `write_plan()` 是全工程唯一写入点，
        收在这儿就**没人能忘**（MCP 的 generate_plan、CLI 的 --confirm 都走这条路）。
      - **顺手渲染阶段二那张 three.js 页**（2026-10-11 用户要求：「整改2阶段，不出图，出three.js」）
        —— `_write_view()` → `views/<plan 名>_view.html` + 同级的 `three.min.js`。
        ⚠ **它失败不拦落盘**（理由见 `_write_view()` 的 docstring），结果在返回值的
        `plan_view` 里：`{ok, out, bytes, rows, errors, ...}` —— 判据分开报，别混成一句。
        ⚠ **自动清图已经删掉**（同日）：页由本函数重生成 ⇒ `views/` 下不会有"别的版本的页"，
        `clear_stale_figures()` 整段删除；`removed_figures` / `kept_figures` 两个键
        **保留但恒为空**（不改调用方）。
    """
    path = Path(target) if target is not None else active_plan_path()

    # --- ⚠ **不可回到前三阶段**（2026-10-12 用户指令，代码硬闸）------------------------------
    # 用户原话：「三阶段只触发一次…直接冻结纪元 1 开启纪元 2…一旦落进关卡代码自动结束前三阶段
    #   且**不可回到前三阶段**，开启纪元 2 后只参考新表」。
    # 判据一句话：**`plan_v2.json` 在 ⇒ `plan_v1.json` 已冻结，任何往它的写一律拒收**。
    # ⚠ 为什么闸在**这里**：`write_plan()` 是全工程唯一写入点（MCP 的 generate_plan、CLI 的
    #   --confirm、换纪元那一步都走它）⇒ 一处拦住 = 全覆盖，不用去每个调用点各写一遍
    #   （那种"靠每个调用点自觉"的判据，本项目已经吃过几次亏）。
    # ⚠ 与 `active_plan_path()` 的分工：咽喉负责"**读**哪一份"（v2 在就只认 v2）；
    #   这里负责"**写**哪一份"——两者是同一条口径的两半，缺一半就能被绕过去
    #   （实测过的绕法：谁直接把 target 指成 `plan_v1.json` 就能改那份"冻结只读"的表）。
    if PLAN_V2_PATH.exists() and path == OUT_JSON:
        raise ValueError(
            f"拒收：**纪元 1 已结束，`{OUT_JSON.name}` 冻结只读** —— 一个字节都没写。\n"
            f"· 换纪元（阶段三那一次落地）之后，权威表是 `{PLAN_V2_PATH.name}`；"
            "**前三阶段不会再回来**（用户口径：「不可回到前三阶段，开启纪元 2 后只参考新表」）。\n"
            f"· 要改内容请改活动那份：`planning.active_plan_path()` → `{PLAN_V2_PATH.name}`"
            "（正常路径 = `request_plan_change` → `generate_plan(patch=…)`）。\n"
            f"· `{OUT_JSON.name}` 只作**留档**看（`views/archive/` 里还有一份冻结时的副本）。")

    epoch = _epoch_of_path(path)
    prefix = f"plan_v{epoch}"
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    body = json.dumps(clean_numbers(plan), ensure_ascii=False, indent=2, allow_nan=False)
    # ⚠ 先记下"写盘之前"那份数据的时间：清图时要拿它当基准（写盘之后新数据一定比图新，
    #   会把用户刚确认的那张位图也一起清掉 —— 见 figure_verdicts() 里那段）。
    prev_mtime = 0.0
    if path.exists():
        try:
            prev_mtime = path.stat().st_mtime
        except OSError:
            prev_mtime = 0.0
    archived = ""
    if path.exists():
        try:
            old = path.read_text(encoding="utf-8-sig")
        except OSError:
            old = ""
        if old.strip() and old != body:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            # 归档名里带上"这一版当初**有没有被人确认过**"（2026-09-24 用户口径：**草稿不入库**）——
            # `.gitignore` 靠这个后缀决定收不收：`*_draft.json` 不入库、`*_confirmed.json` 入库。
            # 读不出来（老文件 / 手改坏了）就按草稿算：**宁可少入库，也不把没确认的东西当凭据收进去**。
            try:
                was_confirmed = bool(
                    ((json.loads(old) or {}).get("confirmation") or {}).get("confirmed"))
            except (ValueError, TypeError):
                was_confirmed = False
            kind = "confirmed" if was_confirmed else "draft"
            # ⚠ 归档变量叫 `arch`（不叫 `target`）—— `target` 现在是本函数的形参（写到哪个文件），
            #   两个意思共用一个名字会写出"归档到归档"那种 bug。
            arch = ARCHIVE_DIR / f"{prefix}_{stamp}_{kind}.json"
            n = 1
            while arch.exists():            # 同一秒连写两次也不许互相覆盖
                arch = ARCHIVE_DIR / f"{prefix}_{stamp}_{kind}_{n}.json"
                n += 1
            arch.write_text(old, encoding="utf-8")
            archived = arch.name
            # --- 基线自动回填（2026-09-26，局部确认上线时补）------------------------
            # 台账里还没有 `accepted_rows`（旧数据是在这个口径上线**之前**确认的）时，
            # 刚被覆盖掉的这一版**当初是被确认过的**（was_confirmed）→ 正好当基线用。
            # 为什么要它：不回填的话，第一次局部改动会因为"没有基线"被迫**全量重画** ——
            # 那就等于这次改造白做。**不许靠人记得手动补。**
            if was_confirmed:
                try:
                    _acc0 = load_acceptance()
                    _old_doc = json.loads(old)
                    if not (_acc0.get("accepted_rows") or {}):
                        _acc0["accepted_rows"] = plan_row_signatures(_old_doc)
                        _acc0["accepted_rows_from"] = arch.name
                    # 分维签名一起回填（同一份旧数据现算得出来）—— 少回填它的话，
                    # 下一轮局部改会落进"判不出维度"那一档。
                    if not (_acc0.get("accepted_aspects") or {}):
                        _acc0["accepted_aspects"] = plan_row_aspects(_old_doc)
                        _acc0["accepted_aspects_from"] = arch.name
                    _acc0["updated_at"] = _now()
                    save_acceptance(_acc0)
                except (ValueError, TypeError, OSError):
                    pass          # 回填失败不该把落盘带崩：届时 change_set() 会退回全量（严的那一侧）
    path.write_text(body, encoding="utf-8")

    # --- 落盘 = 进【阶段二验收】（2026-09-25 用户要求：生图即结束阶段二）--------
    # ⚠ 放在"写数据"之后、"出页"之前：验收台账要读**刚写下去的**那份数据的指纹。
    # ⚠ 台账写不动**不该把落盘本身带崩**（数据已经安全落地了）—— 降级成一条说明。
    try:
        acceptance = enter_acceptance(plan)
    except OSError as exc:
        acceptance = {"state": "unknown", "state_cn": f"验收台账没记上：{exc}"}

    # --- 「改完必须回读」= 代码强制（2026-09-26 用户要求把软约束变硬）----------------
    # 记一条"待回读"进台账（**不进几何指纹**）：在 `get_plan()` 之前，
    # `generate_plan` / `execute_build` 都会拒收，并把这一版的变更集摆出来。
    # ⚠ 与上面同理：台账写不动**不该把落盘带崩**（数据已经安全落地了）。
    # ⚠ **换纪元那一步也会挂上这条**（它是走 `write_plan()` 写 `plan_v2.json` 的）——
    #   所以切换成功后的第一步是 `get_plan()`（只读回读），报文里会说。
    try:
        mark_awaiting_readback(plan, change_set(plan))
    except OSError:
        pass

    # ⚠ **`removed_figures` / `kept_figures` 两个键保留但恒为空**（2026-10-11）：
    #   它们原先是 `clear_stale_figures()` 的产物（把不认账的旧图移进 archive）。
    #   现在那一页**由本函数在下面自动重生成**，`views/` 下**不可能有"别的版本生成的页"** ⇒
    #   自动清图整段删除（理由与旧 `clear_stale_figures()` 的 docstring 同源）。
    #   键保留是为了**不改动调用方**（`main.py` 三处都在读它），且空表就是如实答案。
    return {"path": str(path), "epoch": epoch, "archived": archived, "removed_figures": [],
            "kept_figures": [], "acceptance": acceptance,
            "plan_view": _write_view(plan, path)}


def _write_view(plan: dict, plan_path: Path) -> dict:
    """落盘之后**顺手渲染**阶段二那张 three.js 页 → `{ok, path, out, bytes, rows, errors, ...}`。

    为什么收在 `write_plan()` 里（与"自动留档 / 自动清图 / 自动进验收"同一条纪律：
    **不许靠人记得**）：手画那套的失败模式是「**图与数据不一致**」（数据改了、图还是上一版；
    或图画好了、指纹忘了写）。由唯一写入点顺手生成 ⇒ **这个失败模式在结构上消失**。
    输出名 = 与 plan 同名 + `_view.html`（`plan_v1.json` → `views/plan_v1_view.html`；
    纪元 2 是 `plan_v2.json` → `views/plan_v2_view.html`）。

    ⚠ **生成失败不拦落盘**（2026-10-11 定的，理由写在这儿免得被当成漏掉）：
      ① 落盘是**别的所有阶段的输入**（阶段三搭建、阶段四微调、对账都读 plan）——
         为了让一张预览页好看而把 plan 挡在门外，代价与收益完全不成比例；
      ② 失败面很窄且**当场可见**：模板缺失 / 槽位没替换 / 正文里缺指纹或缺行 ——
         `figure3d.render_page()` 的**当场自检**会把原因逐条报回来（这里是 `errors`），
         再由调用方（`generate_plan` / `get_plan` / `confirm_plan`）摆到眼前；
      ③ 真拦的话，模板一坏就**整个阶段二不能用** —— 那正是交接单里点名要避免的状态
         （演示工程那份 78 行规划还等着用户验收）。
      ⚠ 所以判据是"**plan 写成了没有**"与"**页出没出来**"分开报 —— 别把后者说成前者。

    ⚠ 判据不在这里另写一套：指纹与"这一版要覆盖哪些行"都取 `figure3d.render_page()` 里那两处
      （`plan_geometry_hash()` / `change_set().required_labels`）—— 一处口径。
    """
    out_path = Path(plan_path).parent / f"{Path(plan_path).stem}_view.html"
    try:
        from . import figure3d                       # 同级模块（与 plan.py 一起被同步进插件）
    except ImportError as exc:                       # noqa: PERF203
        return {"ok": False, "out": "", "path": str(out_path), "bytes": 0, "rows": 0,
                "errors": [f"出图模块 figure3d 没进来（{exc}）"], "siblings": []}
    try:
        return figure3d.render_page(
            # ⚠ 走咽喉：纪元 2 开了之后资产表是 `asset_list_v2.json`（2026-10-12 用户要求与位置表同待遇）
    plan, Path(plan_path), active_asset_list_path(),
            out_path, sys.modules[__name__],
            # ⚠ 模板路径**运行时**问出图器，**不要**用本模块的 `VIEW_TEMPLATE_PATH` 常量 ——
            #   插件里它会被 `init_unreal.py` 的 `_relocate()` 搬飞（实测证据见那个常量的说明）。
            #   `figure3d.default_template()` 每次调用按 `__file__` 现算 ⇒ 两处都成立、也搬不动。
            template_path=figure3d.default_template())
    except Exception as exc:                         # noqa: BLE001 —— 出图失败不该把落盘带崩
        return {"ok": False, "out": "", "path": str(out_path), "bytes": 0, "rows": 0,
                "errors": [f"{type(exc).__name__}: {exc}"], "siblings": []}


def promote_to_epoch2(plan_v1_doc: dict, user_quote: str = "", *, trigger: str = "",
                      evidence: dict | None = None) -> tuple[dict, dict]:
    """**换纪元（epoch 1 → 2）**：冻结 `plan_v1.json`、另开 `plan_v2.json` 接管后续阶段。

    谁调它：`main.py` 的 `execute_build()` —— **阶段三整批落地（`mode` 解析为 `full`）自动触发**，
    或手工旁路 `execute_build(confirm_stage3=…)`。那一次调用**先切纪元、再当场落位**。
    （判据与话术在 `main.py::_promote_epoch2()`：纪元 1 还没开过 + **有触发来源**。）

    ⚠ **2026-10-15 改（用户方案：「一进批量落位就切」）**：不再要求"用户原话"当凭据 ——
    人的授权在**上游**（阶段二 `confirm_plan` 的满 5 秒闸 + 阶段三整批校验 + G16 现场对账）。
    产物**如实记录来源**：`promoted_by`（自动 / 手工旁路）+ `promoted_evidence`；
    只有手工旁路才写 `promoted_user_quote`，并同时写 `promoted_quote_attested: False`
    （工具无法核实那句话是不是人打的 —— 字段名不许再冒称事实）。

    返回 **`(新的 plan 内容, 附带动作的结果字典)`**。第二个值现在记的是**资产表搬家**的结果
      （`{"asset_list_copied", "asset_list_note", "asset_list_v1", "asset_list_v2"}`）——
      调用方要把它写进报文（`planned=False` 那种静默失败本工程最忌）。

    做四件事（顺序不能反）：
      ① **冻 v1**：给 `plan_v1.json` 的 `confirmation` 加 `frozen_at` / `frozen_by`（值为 `epoch2`）
         —— ⚠ `confirmation` **不进几何指纹**（`plan_geometry_hash()` 排除它），
         所以这一步**不会作废任何确认、也不会让现场对账报告过期**；
         写它**照走 `write_plan(target=OUT_JSON)`**（唯一写入点）⇒ 顺手把**冻结前那一版**
         归档进 `views/archive/`（`plan_v1_<戳>_<confirmed|draft>.json`，带纪元前缀）；
      ② **开 v2**：内容 = 旧 plan 那一刻的**整表**（资产表 / 白膜表一个字不改），
         只在 `confirmation` 里记 `epoch = 2` / `promoted_at` / `promoted_by` / `promoted_evidence`；
         走 `write_plan(target=PLAN_V2_PATH)` 落盘（唯一写入点：自动留档 + 进验收）；
      ③ **资产表也跟着进纪元 2**（2026-10-12 用户要求「把资产表和位置表一样的方式进纪元 2」）：
         `catalog/asset_list.json` **逐字节照抄**成 `catalog/asset_list_v2.json`，
         此后全工程读它走咽喉 `active_asset_list_path()`（v2 在 ⇒ 就它）。
         ⚠ **逐字节**：它是阶段一的**签字产物**，搬家**不许改内容**；
         ⚠ 与位置表**同一次动作** —— 分开做会留下"位置表已是 v2、资产表还是 v1"的半切换状态；
      ④ 归档里那份 v1 与 v2 的留档**共用 archive 目录但名字带纪元前缀**，不会互相覆盖。

    ⚠ **几何指纹故意逐位不变**：新旧两份只差 `confirmation`，而指纹排除它 —— 于是
      「换纪元」这件事**不会**把用户上一次确认作废，也不会让 `views/evaluate_v1.json` 变过期
      （`G15` / `G16` 那两道闸照旧认它）。这是刻意的：换的是「哪份文件算数」，不是几何。
    ⚠ **拒收**（不写任何文件）：纪元已开（`plan_v2.json` 在）/ **没有触发来源**
      （既不是整批落地、也不是手工旁路）/ 拿不到 v1 的内容（不猜）。这些抛 `ValueError`，
      由调用方转成工具报文。
    """
    _trig = str(trigger or "").strip()
    if not _trig:
        raise ValueError(
            "换纪元**必须有触发来源**（阶段三整批落地自动触发，或手工旁路 `confirm_stage3`）——"
            "拿不到来源就说明这一次既不是整批落地、也没人手工发起：不换（不猜）。")
    quote = str(user_quote or "").strip()
    if PLAN_V2_PATH.exists():
        raise ValueError(
            f"纪元 2 已经开了（`{PLAN_V2_PATH.name}` 在）—— 换纪元只做一次，"
            "之后改行走 `request_plan_change` → `generate_plan(patch=…)` → "
            "`execute_build(only_labels=[…])`（不画图、不重确认）。")
    if not isinstance(plan_v1_doc, dict) or not plan_v1_doc:
        raise ValueError("拿不到纪元 1 的 plan 内容 —— 不换（不猜、也不新建一份空表）。")

    # ① 冻 v1（**只动 confirmation**；几何一个字节不改）—— 走唯一写入点（会自动归档冻结前那一版）
    frozen = json.loads(json.dumps(clean_numbers(plan_v1_doc)))
    conf1 = dict(frozen.get("confirmation") or {})
    conf1["frozen_at"] = _now()
    conf1["frozen_by"] = "epoch2"
    conf1["frozen_note"] = (
        "**纪元 1 已冻结、只读**：阶段三结束后几何的权威已移交 `views/plan_v2.json`。"
        "这份文件从此只作留档（回答「当初确认、当初搭的是哪一版」），"
        "**不许再写**（`generate_plan` 会拒收）。")
    frozen["confirmation"] = conf1
    try:
        write_plan(frozen, target=OUT_JSON)
    except OSError as exc:
        raise ValueError(f"冻结 `{OUT_JSON.name}` 写不进去（{exc}）—— 换纪元中止，v2 没开。") from exc

    # ② 开 v2：**整表照抄**，只在 confirmation 里记纪元与凭据
    new_doc = json.loads(json.dumps(clean_numbers(plan_v1_doc)))
    conf2 = dict(new_doc.get("confirmation") or {})
    conf2["epoch"] = 2
    conf2["promoted_at"] = _now()
    conf2["promoted_by"] = _trig
    if isinstance(evidence, dict) and evidence:
        conf2["promoted_evidence"] = dict(evidence)
    if quote:
        # ⚠ 只有**手工旁路**才写这句"原话"，而且必须同时写"未经核实"——
        #   这个字段以前叫 `promoted_user_quote` 却谁也分不出它是不是人打的（实测被 agent
        #   自己写的选项文案满足过）。字段名不许再冒称事实。
        conf2["promoted_user_quote"] = quote
        conf2["promoted_quote_attested"] = False
        conf2["promoted_quote_note"] = (
            "这句是**调用方（agent）声明**的用户原话，**工具无法核实** —— 引擎侧看不见键盘。"
            "正常路径（阶段三整批落地自动触发）不写这个字段。")
    conf2["note"] = (
        "**纪元 2**（由阶段三结束时的换纪元开出）：几何 = 纪元 1 那一刻的整表（一个字没改）。"
        "此后改行走 `request_plan_change`（记原话）→ `generate_plan(patch=…)` → "
        "`execute_build(only_labels=[…])` —— **不画图、不重确认**。")
    new_doc["confirmation"] = conf2
    # 指纹照算一遍：只差 confirmation ⇒ 与 v1 逐位相同（见本函数说明）。
    new_doc["confirmation"]["plan_hash"] = plan_geometry_hash(new_doc)

    # ② 落盘 v2（唯一写入点；`target` 显式给 v2）
    write_plan(new_doc, target=PLAN_V2_PATH)

    # ③ **资产表也跟着进纪元 2**（2026-10-12 用户要求：「把资产表和位置表一样的方式进纪元 2」）------
    # 与位置表**同一次动作、同一个时点**照抄一份 —— 否则会出现"位置表已是 v2、资产表还是 v1"
    #   这种**半切换状态**：纪元 2 里改着位置表，而阶段四/五查"这资产还在不在 / 实测多大"时
    #   读到的却是阶段一那一天的清单（本工程实测过一次资产库中途变化：
    #   `/Game/Fab/Urban_Street_Pack` 整个文件夹消失、材质实例 58 → 56）。
    # ⚠ 照抄**逐字节复制**（不重新解释、不加字段）—— 它是阶段一的**签字产物**，
    #   换纪元只是"把它带过去"，**不许在搬家时改它的内容**（改了就等于伪造那张签字）。
    # ⚠ v1 那份从此**冻结只读**：写闸在 `main.py` 的资产表写入处（与 plan 的写闸同一口径）。
    # ⚠ 复制失败 **不让换纪元"半成功"**：v2 plan 已经落盘了，这里只如实回报，
    #   由调用方决定要不要补救（`asset_list_v2_copied: false`）—— 静默失败更坏。
    _asset_v1 = ASSET_LIST_PATH
    _asset_v2 = ASSET_LIST_V2_PATH
    asset_copied = False
    _asset_err = ""
    try:
        if _asset_v1.is_file():
            _asset_v2.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_asset_v1, _asset_v2)
            asset_copied = True
        else:
            # 没有资产表可搬：**不是错误**（阶段一可能还没确认清单）—— 如实记一句。
            _asset_err = f"纪元 1 里没有 {_asset_v1.name}（阶段一清单还没落盘）⇒ 没搬"
    except OSError as exc:
        _asset_err = f"{type(exc).__name__}: {exc}"

    return new_doc, {"asset_list_copied": asset_copied, "asset_list_note": _asset_err,
                     "asset_list_v1": str(_asset_v1), "asset_list_v2": str(_asset_v2)}


def confirm_plan(who: str) -> int:
    """把「这张图被确认了」写回**活动那份 plan**（纪元 2 开了就是 `plan_v2.json`）：谁、何时、哪份几何。

    闸门不过就**拒绝确认**（三条，与 MCP 工具 confirm_plan 同款）：
      ① **空规划不许确认**（两张表都空）：一份坐标都没有，确认它毫无意义 ——
         那时还没有图能给用户看；
      ② 几何指纹对不上 → 活动那份 plan 被手改过，与算出来时不一致；
      ③ **图不认这份数据**（`views/` 里没有图 / 图内没有当前指纹 / 图比数据老）——
         AGENTS：图与数据不一致 = 等于没确认。
         ⚠ **这条闸只管前三阶段 / 阶段二验收**：纪元 2 开了之后走的是「四 · 微调」
         （`request_plan_change` → `generate_plan(patch=…)` → `execute_build(only_labels=[…])`，
         不画图、不重确认），那条路由 MCP 的 `generate_plan` 写 `confirmation.mode=tuned`，
         不会经过这里。

    退出码：0 已确认 / 1 文件不存在 / 3 指纹对不上 / 4 空规划 / 5 图不认数据。
    """
    path = active_plan_path()          # ⚠ 走咽喉（纪元 2 开了就是 plan_v2.json）
    if not path.exists():
        print(f"还没有 {path.name}：先算几何，再确认。")
        return 1
    plan = json.loads(path.read_text(encoding="utf-8-sig"))

    # --- ⚠ 空规划不许确认（2026-09-24 补：CLI 这条路径原先漏了这道闸）---
    # 为什么要补：本函数是 `--confirm` 走的**唯一**入口，而空规划（初始化结构）的几何指纹
    #   是**自洽**的 —— 只比指纹就一路放行，等于"一份坐标都没有的规划"也能被确认成功，
    #   闸门就等于有个后门（MCP 工具那条 confirm_plan 拦得住，这条原先拦不住）。
    # 判据与 MCP 工具那条一致：**两张表都空**即拒；老文件里还带着 `status` 字段的，一并认。
    if str(plan.get("status") or "") == "initialized" or not (
            plan.get("assets") or plan.get("whiteboxes")):
        print("拒绝确认：这是**初始化状态**（assets / whiteboxes 都空、一份坐标都没有）。")
        print("  先把放置表填上（先已有资产、后白膜），出图给用户看过，再确认。")
        return 4

    gate = gate_check(plan)

    if not gate["plan_hash_ok"]:
        print(f"拒绝确认：{path.name} 的几何与算出来时不一致（被改过）。")
        return 3

    # --- ⚠ 页与数据必须一致（2026-09-24 收尾清单 #8）---
    # 为什么放在写盘之前：确认是"人对着一张页点头"这件事的留痕。页都没认这份数据，
    # 那次点头点的就不是这一版 —— 所以页不认账时**不写盘**。
    fig = figure_check(plan)
    if not fig["ok"]:
        print("拒绝确认：页不认这份数据 —— " + fig["note"])
        if fig.get("missing_views"):
            print("  **缺的**："
                  + "、".join(str(x) for x in (fig.get("view_labels") or fig["missing_views"])))
        print(f"  ⚠ 那一页**由 `write_plan()` 落盘时自动生成**，不用谁去画 —— "
              f"它不在 / 不认账，看落盘那次报文的 `plan_view.errors`；"
              f"要手工重出：python -m mcp_server.planning.figure3d")
        print("  要覆盖哪些行：python -m mcp_server.planning.plan --figure-checklist")
        return 5

    confirmed = mark_confirmed(plan, who, user_quote="（命令行直连确认：确认人就在键盘前）")
    # ⚠ 显式把 `target` 传成**刚读的那一份**（不靠默认值再解析一次）：两次解析之间
    #   有人建/删了 `plan_v2.json` 的话，默认值会把这一版确认写进**另一个文件**。
    result = write_plan(confirmed, target=path)
    # --- 阶段二验收台账：记"这一轮通过了"（2026-09-25）--------------------------
    # ⚠ 用**没带 confirmation 的那份** plan 算指纹 —— plan_geometry_hash() 本来就把
    #   confirmation 排除在外，两者算出来是同一个值；这样写是为了和 MCP 工具那条路一致。
    ledger = record_acceptance(plan, who, user_quote="（命令行直连确认）")
    print(f"已确认并留痕：{who}")
    print(f"  时间      {confirmed['confirmation']['confirmed_at']}")
    print(f"  几何指纹  {gate['plan_hash'][:16]}…")
    print(f"  图        {'、'.join(fig['figures'])}")
    print(f"  上一版    {result['archived'] or '（没有旧版可留档）'}")
    if result["removed_figures"]:
        print(f"  清掉的图  {'；'.join(result['removed_figures'])}（已移进 views/archive/）")
    print(f"  验收台账  第 {ledger.get('round')} 轮，{ACCEPTANCE_STATES.get(ledger.get('state'), '')} "
          f"→ {ACCEPTANCE_PATH}")
    return 0


def request_change_cli(items: list[str], by: str = "用户（命令行）") -> int:
    """`--change "把左侧第 3 栋往 -X 挪 5 m" ...` —— 记下用户要改的地方（**不改几何**）。

    退出码：0 记下了 / 1 还没有规划数据 / 2 没给内容。
    """
    if not OUT_JSON.exists() and not PLAN_V2_PATH.exists():
        print("还没有 plan（v1 / v2 都不在）：先调 generate_plan 记一版，再记改动。")
        return 1
    if not items:
        print('用法：--change "要改的地方"（要改多条就多给几个参数）')
        return 2
    plan = active_plan()             # ⚠ 走咽喉（纪元 2 开了就是 plan_v2.json）
    acc = record_change_request(plan, items, by=by)
    print(f"已记下 {len(items)} 条改动 → 验收台账 {ACCEPTANCE_PATH}")
    for it in items:
        print(f"  · {it}")
    print(f"当前状态：第 {acc.get('round')} 轮，"
          f"{ACCEPTANCE_STATES.get(acc.get('state'), acc.get('state'))}")
    print("下一步：按这些改动改数据里的 pos / 占地 → 重跑 generate_plan（自动进新一轮）"
          "→ 重落盘（那一页自动重生成、页内写新指纹）→ 再把页交给用户。")
    return 0


# --- 入口 ---------------------------------------------------------------------
#
# 出图**在代码里、但不在本文件**（2026-10-11 起）：
#   落盘时由 `write_plan()` → `_write_view()` → `figure3d.py` + `web/preview3d.html`
#   自动生成那张 three.js 页。（2026-09-24 那条"图由 AI 手绘"的口径已作废。）


def figure_checklist_cli() -> int:
    """`--figure-checklist` —— 打印"这一页**必须覆盖**的全部行"（核对用）。

    为什么留着它（2026-09-25 用户要求「画图让 Agent 不要漏位置坐标表任何一个元素」）：
    少一行，`confirm_plan` 会拒收，而它**只在出页之后**才告诉你漏了哪几行。
    现在页由代码生成、自检在**落盘那一刻**就跑（`figure3d.self_check()`），所以这条
    CLI 的用途从"画图前照抄清单"变成了**人工抽查**：想知道"这一版到底该覆盖哪些行、
    页里有没有"时跑它。

    退出码：0 有清单 / 1 还没有规划数据 / 4 放置表是空的（没有行）。
    """
    path = active_plan_path()          # ⚠ 走咽喉（纪元 2 开了就是 plan_v2.json）
    if not path.exists():
        print(f"还没有 {path.name}：先调 MCP 工具 generate_plan 记一版。")
        return 1
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    assets = list(plan.get("assets") or [])
    boxes = list(plan.get("whiteboxes") or [])
    rows = assets + boxes
    if not rows:
        print("放置表是空的（初始化状态）—— 没有行。")
        return 4
    cs = change_set(plan)
    req = list(cs.get("required_labels") or [])
    removed = set(cs.get("removed") or [])
    pos_by_label: dict = {}
    for row in rows:
        if isinstance(row, dict):
            pos_by_label[str(row.get("label") or "")] = row.get("pos")
    head = plan_geometry_hash(plan)
    print(f"几何指纹（页里必须写上它的前 10 位）：{head[:10]}")
    print(f"世界：中心 {plan['world']['center']}  大小 {plan['world']['size']} m")
    if cs.get("local"):
        print(f"**局部一轮**（基线：{cs['baseline']}）—— 页里**只要**出现下面 {len(req)} 行；"
              f"另外 {cs['same']} 行没动（它们早已确认过）。")
        print(f"  本次：新增 {len(cs['added'])} / 改动 {len(cs['changed'])} / 删除 {len(cs['removed'])}")
    else:
        print(f"页里必须出现下面**全部 {len(req)} 行**的 label（一行都不能少）：")
    for i, lab in enumerate(req, 1):
        mark = "（本版**删掉**的行：页里不该再有它）" if lab in removed else ""
        print(f"  {i:>3}. {lab}{mark}    pos={pos_by_label.get(lab)}")
    views = [str(v) for v in (cs.get("required_views") or ["page"])]
    print("**要出哪一张**（2026-10-11 起恒一张，不再按维度分）：")
    if cs.get("views_why"):
        print(f"  依据：{cs['views_why']}")
    for _v in views:
        print(f"  · `views/{path.stem}_view.html`（three.js 页）"
              "—— **落盘时由代码自动生成**，不用谁去画")
    print("⚠ 认账的判据（`figure_verdicts()`）：页正文里有当前指纹前 10 位 + 上面那几行的 label。")
    print("⚠ 页没出来时**别去手画** —— 看落盘那次报文的 `plan_view.errors`（原因会逐条列出），"
          "或手工重出：`python -m mcp_server.planning.figure3d`。")
    # --- 「原图视角」箭头（2026-10-04 用户要求）----------------------------------------
    # 它现在画在**那张页的 3D 场景里**（`web/preview3d.html` 的 `buildOriginArrow()`）；
    # ⚠ 缺这四个字**只提示不拒收**（`ORIGIN_VIEW_MISSING_WARNING`）—— 这里报出来供人工核对。
    _ov = origin_view_spec(plan)
    print("⚠ 页里还要有「原图视角」四个字（不然用户分不清哪边是原图往里看的方向）：")
    if _ov.get("arrow_from_m") and _ov.get("arrow_to_m"):
        print(f"    {_ov['arrow_from_m']} → {_ov['arrow_to_m']}（米、世界坐标，指向 +X），"
              f"文字「{_ov['label']}」")
        print(f"    {_ov.get('where')}")
    else:
        print(f"    ⚠ 位置这次没算出来（{_ov.get('warning')}）—— 口径见 `origin_view_spec()`")
    print("    ⚠ 位置与朝向**以 `origin_view_spec()` 为准**（别自己摆）；"
          "缺这四个字只会记一条提示，不影响确认。")
    return 0


def main() -> int:
    """CLI 入口 —— **本文件不再自己算一版**，只报当前状态。

    为什么改成这样（2026-09-24 用户新规格）：放置坐标要 agent 规划，**没有"默认摆法"**
    这种东西。所以 CLI 给不出一份有意义的默认数据 —— 想算，请走 MCP 工具 `generate_plan`
    （它要你把 world / assets / whiteboxes 三段给进去）。这里只负责"看状态"和"确认"。
    """
    path = active_plan_path()          # ⚠ 走咽喉（纪元 2 开了就是 plan_v2.json）
    if not path.exists():
        print(f"还没有 {path.name}。")
        print("  放置坐标要由 agent 规划，本文件不生成默认摆法 —— 请调 MCP 工具 generate_plan。")
        return 1

    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    conf = plan.get("confirmation", {}) or {}
    acc = acceptance_view(plan)      # 阶段二验收：**现算**（不读台账里的旧快照）

    print("阶段二 · 平面放置规划（现状）")
    print(f"  几何数据: {path}")
    print(f"  当前纪元: {active_epoch()}（活动 plan = {path.name}）")
    print(f"  世界    : 中心 {plan['world']['center']}  大小 {plan['world']['size']} m"
          f"（{plan['world'].get('size_source', '来源未记录')}）")
    print(f"  已有资产: {len(plan.get('assets', []))} 个")
    print(f"  白膜    : {len(plan.get('whiteboxes', []))} 个")
    print(f"  当前阶段: {acc['stage']}（第 {acc['round']} 轮）—— {acc['state_cn']}")
    print(f"  图      : " + ("、".join(acc["figures"]) if acc["figures"]
                            else "**没有认这份数据的图**（没有图不许让用户确认）"))
    cs = change_set(plan)
    print("  核对清单: python -m mcp_server.planning.plan --figure-checklist"
          + (f"（**局部一轮**：页里只要出现本次改动的 {len(cs['required_labels'])} 行；"
             f"另外 {cs['same']} 行没动）"
             if cs.get("local") else
             f"（页里必须出现全部 {len(cs['required_labels'])} 行的 label，一行都不能漏）"))
    print("  要给用户看的: " + "、".join(
        f"views/{path.stem}_view.html（three.js 页，落盘自动生成）"
        for _v in (cs.get("required_views") or ["page"])))
    if acc["pending_changes"]:
        print("  用户要改: " + "；".join(acc["pending_changes"]))
    print(f"  验收台账: {acc['ledger_path']}")
    print("  确认状态: " + (f"已确认 by {conf.get('confirmed_by')} @ {conf.get('confirmed_at')}"
                          if conf.get("confirmed") else
                          "未确认 —— 不许进第三阶段"))
    # ⚠ 2026-09-25：这里原来会打印 11 项自检 —— 自检整段已删（用户指令），
    #   碰撞检测与布局修正**已整段删除**（2026-09-27：不再单列阶段）。旧 plan_v1.json 里残留的 `checks` 字段**不再读、不再显示**。
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--confirm":
        who = argv[1] if len(argv) > 1 else "用户"
        raise SystemExit(confirm_plan(who))
    if argv and argv[0] == "--change":
        # 阶段二验收：记下用户要改的地方（不改几何、不进指纹）
        raise SystemExit(request_change_cli(argv[1:]))
    if argv and argv[0] == "--figure-checklist":
        # 画图**之前**先看"必须画进去哪些行"（2026-09-25 用户要求：图不许漏任何一行）
        raise SystemExit(figure_checklist_cli())
    raise SystemExit(main())
