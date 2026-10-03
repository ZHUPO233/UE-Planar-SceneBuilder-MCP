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
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- 常量 ---------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
"""仓库根目录。本文件在 src/mcp_server/planning/ 下，往上三层即根。"""

VIEWS_DIR = REPO_ROOT / "views"
OUT_JSON = VIEWS_DIR / "plan_v1.json"
ARCHIVE_DIR = VIEWS_DIR / "archive"
"""每一版 plan_v1.json 的留档目录（2026-09-24 收尾清单 #4：确认版必须留得下来）。"""

# 给用户看的那张图的文件名候选（**图由 AI 手绘，代码不出图**）—— figure_check() 按这些模式找
FIGURE_GLOBS = ("plan_v1_overview*.svg", "plan_v1_overview*.png",
                "plan_v1_overview*.jpg", "plan_v1_overview*.jpeg",
                "plan_v1_overview*.webp", "plan_v1_overview*.gif")
"""**平面图**（俯视）—— 前缀是代码找图的约定（图由 AI 手绘，代码不出图）。"""

ELEVATION_GLOBS = ("plan_v1_elevation*.svg", "plan_v1_elevation*.png",
                   "plan_v1_elevation*.jpg", "plan_v1_elevation*.jpeg",
                   "plan_v1_elevation*.webp", "plan_v1_elevation*.gif")
"""**立面图**（正视图 X 向 / 侧视图 Y 向）—— 2026-09-30 用户要求加。

原话：「我要求的是改高度……但是这个图画的还是顶视图，我觉得得改一下，**当调整的是高度时
生成的图得是正视图或者左右视图**」。实测场景：外部 agent 按流程改了 25 栋楼的高度、也回读、
也出图，但那张图是**俯视图** —— 高度变化在俯视图上**完全看不见**，于是那张图作为
"用户唯一能判断的依据"**等于没有**（他被要求对着一张看不出变化的图点头）。

⚠ 为什么**单开一路前缀**、而不是并进 `FIGURE_GLOBS`：并进去的话**一张立面图就能顶掉平面图**，
判据会从"两张都要"松成"有一张就行"。分开之后 `figure_check()` 按视图**逐项**判，缺哪张报哪张。
⚠ 判据与平面图**完全一样**（当前指纹前 10 位 + 该覆盖的每一行 label）—— 同一份判据只写在
`figure_verdicts()` 一处，用 `kind` 参数选前缀；**位图在两路里都不认账**（读不出文字）。
"""

ELEMENTS_PATH = REPO_ROOT / "catalog" / "elements.json"
"""阶段一的元素清单 —— 本阶段从这里读「地图大小」和「有哪些元素」。"""

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

    ⚠ 判据把 `footprint_m` 当**世界轴对齐尺寸**（`rot_deg` **不叠**）：plan 的 `params`
      写明"已有资产占地 = 阶段一实测包围盒 × scale，`footprint_m` 是**旋转之后的世界 X / Y
      占地**" —— 再叠一次 rot 就是转两遍。**代价照实说**：若某行 `rot_deg != 0` 而它的
      `footprint_m` 其实是**未旋转**的尺寸，这一条会**少报**（宁可少报，不假报）。

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
        corners = rect_corners((cx, cy), (w, d), 0.0)
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
        why.append(f"越界 {over:.2f} m（占地 {w:g}×{d:g} m）")
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
                     "判的是物体**中心**与**占地四角**（`footprint_m` 当世界轴对齐尺寸用，"
                     "**不叠 `rot_deg`**）。"),
        "note": ("⚠ 只报红、不拦；结果**不写进 plan**（不碰几何指纹）。"
                 "⚠ 越界的物理含义：`world.bounds` = **世界地基的范围**，"
                 "跑到外面 = 悬空 / 穿帮。"),
    }


def bounds_check_text(plan: dict, limit: int = 12) -> str:
    """把 `bounds_check()` 的结果压成**一段话**（调用方直接塞进 warnings）。没越界就给空串。

    格式（**硬在前、软在后**，附三级判据原文 —— 让读的人知道"为什么这条算硬、那条算软"）：
      ⚠ **阶段二检查 · 越界**：N 处超界（边界 …）
      　判据：…
      　【硬 · 实体 2 处】…
      　【软 · 铺装 13 处】…（还有 N 处没列全）
    """
    res = bounds_check(plan)
    if res.get("ok"):
        return ""
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
    lines.append("　⚠ **只报红、不拦** —— 改不改由用户定（要么挪物体、要么把世界放大再重画图确认）。")
    return "\n".join(lines)


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

def load_previous_elements(path: Path = ELEMENTS_PATH) -> dict:
    """读阶段一的元素清单（`catalog/elements.json`）→ `(全部行, 人话场景项)`。

    为什么要读它（**不是多此一举**）：
      ① 「地图大小」是阶段一记的场景项 → 本阶段的世界大小默认值从这儿来，不靠人再报一遍；
      ② 放置表里的 `element_key` **必须**在阶段一确认过的清单里有记录
         （不许凭空多出一个元素 —— 那样等于规划了图里没有的东西）。
    读不到就返回空表：由调用方决定是报错还是只警告（这里**不抛**）。
    """
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
    # `z_m`（可选，米）= 这一行的**中心绝对标高** —— 不给就按阶段三的竖直口径推
    #   （`main.py` 的 `WHITEBOX_VERTICAL` / `GROUND_Z_M` / `ASSET_PIVOT_LIFT_CM`）。
    # 为什么加它（2026-09-30 用户拍板 D 案）：白膜的竖直位置**原本只由代码口径算**
    #   （`ground: ("top", GROUND_Z_M)`），于是"用户把世界地基手动挪低 10 cm"这件事
    #   **在 plan 里表达不出来** —— 写不回 plan，下一轮增量还会按口径把它拉回去。
    #   ⚠ 它是**几何**（进指纹、进逐行签名），不是说明文字。
    if raw.get("z_m") is not None:
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
    # --- 高度（Z）—— 选填，但给了就要校验（2026-09-24 收尾清单 #2）---
    # 为什么必须补这个字段：阶段一的白膜本来就有三维（size_cm = [X, Y, Z]，例如消防栓
    # [30, 30, 75]），可阶段二以前只记平面占地 —— **高度在阶段二被丢掉了**，只活在 note 里；
    # 阶段三要拿它把 cube 实例化出来，那时只能靠人读中文备注。
    # cube（有体积的）缺高度时，阶段三只能靠人读 note 里的中文 —— 必填。plane（覆盖面）不需要。
    h = raw.get("height_m")
    if h is not None and str(h).strip() != "":
        try:
            height_m = float(h)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{item['element_key']!r} 的 height_m 不是数字：{h!r}") from exc
        if height_m <= 0:
            raise ValueError(f"{item['element_key']!r} 的 height_m 必须为正（米）")
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

CONFIRMED_ARCHIVE_GLOB = "plan_v1_*_confirmed.json"
"""归档里"**被人确认过**那一版"的文件名模式（`write_plan()` 归档时按这个后缀命名）。"""


def row_signature(row: dict, kind: str) -> str:
    """一行的**几何签名** —— 只装"改了就得重画图"的字段。

    ⚠ 故意**不含** `note` / `size_source`：那是说明文字，改字不该逼人重画图。
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

⚠ 2026-09-30 用户定的**最终口径**：「涉及**位移**的就顶视图，涉及**大小、高度**的就正视图或左右视图」。
按这条把字段分三组：
  · `plan`（**位移 / 朝向**）→ 顶视图：`element_key` / `asset_path`|`shape` / `pos` / `rot_deg`；
    ⚠ `rot_deg` **归顶视**是我加的判断（朝向从上面看得最清楚）—— 要它也出立面就说一声。
  · `size`（**大小**）→ 立面：`footprint_m` / `scale`；
  · `height`（**高度 / Z**）→ 立面：资产 `z_m` / `scale_z`、白膜 `height_m` / `z_m`。
⚠ 资产行签名末位 `scale_z` **只在 ≠1 时才存在** ⇒ 下标越界一律当 `None`（= 没写），不是错误。
"""

REQUIRED_VIEWS = ("top", "elevation")
"""**每一版都出这两张图**（2026-09-30 用户定案 —— 先做过"按维度决定出哪几张"，当天下午定为恒出两张）。

用户原话：「算了直接生成原图视角正视图和顶视图算了，太麻烦了」。
为什么恒出两张比"按维度决定"更好（我的判断，也是采纳的理由）：
  · **少一类往返失败**：条件视图的失败模式是「判错维度 → 要的那张没画 → `confirm_plan` 拒收 → 再画一遍」；
    恒出两张把这个失败模式整个消掉；
  · **规则少一条**：常量不需要向 agent 解释，也少一处"口径写在文档里、代码里忘跟"的分叉点
    （与 `BUILD_LAYERS` / `ENV_FACTOR_ORDER` 同一条纪律：**能变成常量的，别留成条件**）；
  · 代价只有"多画一张 svg"（agent 本来就是拿 `get_plan().drawing` 写脚本渲染）。

**两张各画哪个平面**（写死，别让画图的人猜）：
  · `top`（**顶视图**）= **X-Y 平面**（俯视）；
  · `elevation`（**原图视角正视图**）= **Y-Z 平面** —— 横轴 Y、纵轴 Z。
    依据：本工程坐标系 `world.coordinate_system` 定的是 **X 前进**（承载参考图的纵深链），
    所以"原图视角"就是**沿 X 看** ⇒ 屏幕上剩下 Y（右）与 Z（上）。
⚠ 正视图是**立面展开图**：X 方向被压掉了，不同 X 上的东西会叠在一起 ——
  它是**给用户核对体量与高度用的示意图，不是严格投影**；`rot_deg` 对它的影响按轴对齐近似。
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
    不能反推的话，这些行会永远落进"判不出维度 → 两张图都要"的严判里（多一道无用功）；
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

    用途只有一个：回答「**这一版动的是位移 / 大小 / 高度里的哪几样**」——
    因为它决定**要出哪几张图**（`VIEW_FOR_DIM`：位移 → 顶视图；大小 / 高度 → 立面）。

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
      · `required_labels` = 这一版**图里必须出现**的 label（新增 + 改动 + 删掉的行）；
      · `required_views` = **恒为两张**（`["top", "elevation"]`，2026-09-30 用户定案）：
        **顶视图**（X-Y）+ **原图视角正视图**（Y-Z）。依据与"为什么不做条件视图"见 `REQUIRED_VIEWS`；
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
            # 位移、大小、高度**三样都算动过**（维度只用于"重点标哪几行"；两张图本来就恒出）。
            "changed_dims": ["plan", "size", "height"],
            "required_views": list(REQUIRED_VIEWS),
            "views_why": ("还没有『被确认过的基线』（第一次搭 / 换了图）⇒ **全量一轮**："
                          "整张表都是新摆的，图里要出现每一行；**两张图照出**"
                          "（顶视图 + 原图视角正视图 —— 每一版都出这两张）。"),
            "note": ("还没有『被确认过的基线』—— 这一版按**全量**判：图里要出现放置表"
                     f"**每一行**的 label（共 {len(labels)} 行），且**平面图与立面图都要**。"),
        }

    added = [l for l in cur if l not in base_rows]
    removed = [l for l in base_rows if l not in cur]
    changed = [l for l in cur if l in base_rows and cur[l] != base_rows[l]]
    same = [l for l in cur if l in base_rows and cur[l] == base_rows[l]]
    changed_set, added_set = set(changed), set(added)
    req = [l for l in labels if l in changed_set or l in added_set]
    req += [l for l in removed if l not in req]        # 删掉的行也要点到（用户得看见"它没了"）

    # --- 「变的是哪一维」→ 决定**要出哪几张图**（2026-09-30 加）------------------------
    # 用户原话：「我要求的是改高度……但是这个图画的还是顶视图，我觉得得改一下，
    #   **当调整的是高度时生成的图得是正视图或者左右视图**」。
    # 根因就在这儿：变更集原先只回 label 列表，**平面改和高度改的输出一模一样** ——
    # 于是 agent 一律按老习惯画俯视图，而高度变化在俯视图上完全看不见（那张图白给）。
    # 判据：把"改动 + 新增"的行按**维度签名**再比一次（`row_aspects()`）。
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
        dims.add("plan")                    # 删掉的行要在顶视图上点到"它没了"

    # --- 视图要求 = **恒出两张**（2026-09-30 用户定案，见 `REQUIRED_VIEWS`）----------------
    # 原先做过"按动的是哪一维决定出哪几张"，当天下午用户定为恒出两张：「太麻烦了」。
    # 所以这里不再做映射 —— 维度只用来**说清这一版动了什么**（报文里给重点）。
    required_views = list(REQUIRED_VIEWS)
    if changed or added or removed:
        views_why = ("**每一版都出两张**：顶视图（X-Y：摆在哪 / 占地多大）+ "
                     "原图视角正视图（Y-Z：多高 / 多大）。这一版动到的维度"
                     "（**只用来决定图里重点标哪几行**，不再决定出哪几张图）："
                     + "、".join(DIM_CN[d] for d in ("plan", "size", "height") if d in dims)
                     + "。")
        if dims_blind:
            views_why += (f"；⚠ 其中 {len(dims_blind)} 行在基线里**没有维度签名**"
                          "（旧台账，判不出动的是哪一维）—— 两张图照出、照验。")
    else:
        views_why = ("**每一版都出两张**（顶视图 + 原图视角正视图）——这一版与基线逐行一致，"
                     "两张照出、照验（「没有图不许让用户确认」这条闸不因变更集为空而失效）。")
    return {
        "local": True, "baseline": base_where,
        "added": added, "changed": changed, "removed": removed,
        "same": len(same), "required_labels": req,
        "changed_dims": [d for d in ("plan", "size", "height") if d in dims],
        "required_views": required_views,
        "views_why": views_why,
        "note": (f"**局部一轮**（基线：{base_where}）：新增 {len(added)} / 改动 {len(changed)} / "
                 f"删除 {len(removed)}；图里只需出现这 **{len(req)} 行**的 label；"
                 f"另外 {len(same)} 行**没动**、不必重画（它们早已确认过）。"
                 f"视图要求：{views_why}"),
    }


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

    `kind` 选**哪一路图**（2026-09-30 加）：
      · `"plan"`（默认）= **平面图**（俯视），文件名前缀 `plan_v1_overview*`；
      · `"elevation"` = **立面图**（正视图 / 侧视图），文件名前缀 `plan_v1_elevation*`。
    ⚠ 两路的判据**一字不差**（就是下面这份代码），只有文件名前缀不同 ——
      判据只写一处，免得"平面图严、立面图松"这种两套口径。

    判据（两条**都要过**）：
      - **正文里要有当前几何指纹的前 10 位** —— 证明"这张图是拿这份数据画的"；
      - **正文里要出现"该覆盖的每一行"的 `label`** —— 证明"这张图**没漏东西**"
        （判据实现在 `figure_label_coverage()`）。

    ⚠ **要覆盖哪些行，2026-09-26 起按"变更集"算**（用户要求：「用户想局部改，**所有都局部改，
      不要全部重做**」）：有已确认的基线时，只要求出现**这一次动了的那几行**的 label；
      **没有基线（第一次搭）时仍是全量**（每一行都要出现）。覆盖率因此是**传递**成立的：
      第一版全量画过，之后每轮只画改动过的那几行，并起来就是每一行。见 `change_set()`。

    ⚠ **位图（png / jpg / jpeg / webp / gif）一律不认账**：读不出文字，就核验不了"漏没漏"。
      原来的位图口径是"文件比 plan_v1.json 新就放行"—— 那条口子与本条要求**直接冲突**
      （一张漏了 20 行的截图，只要比数据新就能过关），故一并撤掉。要使用就出 **svg**。

    ⚠ `plan_mtime` 是**保留形参**：`clear_stale_figures()` 还在传它（签名不动、少改调用方）。
      位图不再按时间放行之后，它**不参与任何判定** —— 只用来在理由里写一句"这张比数据新/老"，
      当诊断信息（好让人一眼看出那张位图是不是上一轮的）。

    它是 `figure_check()`（确认时把关）、`clear_stale_figures()`（落盘时清除）、
    `accepted_figures()`（验收状态）**共用的唯一判据** —— 判据只写一处，免得几条路各说各话。
    """
    head = plan_geometry_hash(plan)[:10]
    if plan_mtime is None:
        try:
            plan_mtime = OUT_JSON.stat().st_mtime if OUT_JSON.exists() else 0.0
        except OSError:
            plan_mtime = 0.0

    # 这一版**图里要覆盖哪些行**（局部轮 = 变更集；没有基线 = 全量）—— 唯一入口见 change_set()
    cs = change_set(plan)
    req = list(cs.get("required_labels") or [])
    scope = ("局部一轮" if cs.get("local") else "全量一轮")

    found: list[Path] = []
    for pattern in (ELEVATION_GLOBS if kind == "elevation" else FIGURE_GLOBS):
        try:
            found += [p for p in VIEWS_DIR.glob(pattern) if p.is_file()]
        except OSError:
            continue

    out: list[dict] = []
    for path in sorted(set(found)):
        if path.suffix.lower() != ".svg":
            # 位图**不再认账**（理由见本函数 docstring）：这里只把"新旧"当诊断信息写进理由，
            # 绝不用它放行 —— 用时间放行的话，"漏了 20 行的截图"只要比数据新就照样过关。
            try:
                age = ("比 plan_v1.json 新" if path.stat().st_mtime >= plan_mtime
                       else "比 plan_v1.json 老")
            except OSError:
                age = "取不到修改时间"
            out.append({"name": path.name, "path": path, "ok": False,
                        "why": (f"位图读不出文字，**核验不了有没有漏元素**（这张{age}）；"
                                "要使用就出 svg —— 图内写当前指纹 + 该覆盖的那些行的 label")})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            out.append({"name": path.name, "path": path, "ok": False,
                        "why": f"读不动（{exc}）"})
            continue
        if head not in text:
            out.append({"name": path.name, "path": path, "ok": False,
                        "why": f"正文里没有当前指纹 {head}…（是别的版本的图）"})
            continue
        cov = figure_label_coverage(plan, text, labels=req)
        if not cov["ok"]:
            shown = "、".join(cov["missing"][:6])
            tail = "" if len(cov["missing"]) <= 6 else f" 等 {len(cov['missing'])} 行"
            out.append({"name": path.name, "path": path, "ok": False,
                        "why": (f"指纹对得上，但（{scope}）**漏了 {len(cov['missing'])}/{cov['total']} 行**："
                                f"{shown}{tail}")})
            continue
        out.append({"name": path.name, "path": path, "ok": True,
                    "why": (f"正文里有当前指纹 {head}…，"
                            + (f"且本次该覆盖的 {cov['total']} 行**一行不漏**（{scope}，认账）"
                               if cov["total"] else f"且这一版**没有任何改动**（{scope}，认账）"))})
    return out


def figure_check(plan: dict) -> dict:
    """判「views/ 里**该有的图**是不是都认这份数据」→
    `{ok, figures, required_views, missing_views, required_labels, local, changed_dims, note[, needs_labels]}`。

    **按视图逐项判**（2026-09-30 加）：`change_set()["required_views"]` 说这一版要出哪几张
    （**位移 / 朝向 → 顶视图**；**大小 / 高度 → 正视图或左右视图**；见 `VIEW_FOR_DIM`），这里逐项验：
      · `top` → 平面图（俯视），前缀 `plan_v1_overview*`；
      · `elevation` → 立面图（正视图 / 侧视图），前缀 `plan_v1_elevation*`。
    **缺哪一张就报哪一张**（`missing_views`），不合并成一句含糊的"图不认数据"。

    判据见 `figure_verdicts()`：**指纹对得上 + 该覆盖的每一行 `label` 都在图里**。
    ⚠ 「该覆盖的行」= `change_set()["required_labels"]`：**局部轮 = 本次改动的那几行**；
      没有基线（第一次搭）= **全量每一行**（2026-09-26 用户要求「所有都局部改，不要全部重做」）。

    为什么要有它（2026-09-24 收尾清单 #8）：AGENTS 的口径是 **图与数据必须一致 ——
    不一致 = 等于没确认**，但以前全靠人自觉（实测踩到过：图里还写着"本图未经用户确认"，
    而数据早已确认）。2026-09-25 又按用户要求加严：**光有指纹不算数，还得一行都不漏**。
    2026-09-30 再加一层：**漏的是哪一维**也算"漏" —— 只改高度却只给俯视图，
    用户根本看不出改了什么，那张图作为凭据等于没有（用户原话见 `ELEVATION_GLOBS`）。

    ⚠ 判不过时**不猜**：说清是哪张图、为什么不算数，让调用方（confirm_plan）拒收；
      缺图时额外附 `needs_labels`（这一版**必须出现在图里**的 label ——
      局部轮就是那几行，**不用重画整张图**），让画图的人照着补，而不是来回猜"到底漏了哪个"。
    """
    cs = change_set(plan)
    needs = list(cs.get("required_labels") or [])
    views = [str(v) for v in (cs.get("required_views") or ["top"])]
    view_cn = {"top": "平面图（俯视）", "elevation": "立面图（正视图 / 侧视图）"}
    view_prefix = {"top": "plan_v1_overview*.svg", "elevation": "plan_v1_elevation*.svg"}

    per_view: dict[str, list[dict]] = {}
    for view in ("top", "elevation"):
        try:
            per_view[view] = figure_verdicts(
                plan, kind=("elevation" if view == "elevation" else "plan"))
        except OSError:
            per_view[view] = []

    figures = [str(v["name"]) for view in views for v in per_view.get(view, []) if v.get("ok")]
    missing_views = [view for view in views
                     if not any(v.get("ok") for v in per_view.get(view, []))]

    # 逐路说清现状：哪张认账 / 认不了的话是为什么 / **一张都没有**（并给出命名约定）
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
            #   **图已经画好了**（名字不是 plan_v1_overview*），它读到的却是「没有图」，
            #   于是**去把文件改了个名**再来确认。两个坏处：
            #     ① 事实说错了（有图，只是没被命名约定命中）；
            #     ② 既没说约定是什么、也没说正确出路 —— agent 只能靠撞墙学（AGENTS：不许靠人记得）。
            #   现在分成两句说：**一张图都没有** / **有图，但没被约定命中**（并说明改名是允许的）。
            try:
                stray = [p.name for p in VIEWS_DIR.glob("*.svg") if p.is_file()]
            except OSError:
                stray = []
            if stray:
                stray_hint = ("（views/ 里有 " + str(len(stray)) + " 张 svg 没被任何一路前缀命中："
                              + "、".join(stray[:6]) + ("…" if len(stray) > 6 else "")
                              + "。⚠ 命名只是**代码找图的约定**，真正的判据是**图的内容**"
                                "（当前几何指纹前 10 位 + 该覆盖的每一行 label）—— "
                                "所以**把它改名成上面那个前缀是允许的**；"
                                "但内容不对的话，改名也过不了，那得重画。）")
            else:
                stray_hint = ("⚠ 画完请**按上面那两种前缀命名**"
                              "（平面图 `plan_v1_overview*.svg` / 立面图 `plan_v1_elevation*.svg`）"
                              "—— 那是代码找图的约定；**必须是 svg**，位图核验不了有没有漏。")
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
        "note": note + (f"（{cs['note']}）" if cs.get("note") else ""),
    }
    if missing_views:
        out["needs_labels"] = needs
    return out


def clear_stale_figures(plan: dict, plan_mtime: float | None = None) -> tuple[list[str], list[str]]:
    """把 `views/` 里**不认这份数据**的图移进 `views/archive/` → `(移走的, 留下的)`。

    为什么要**代码自动清除**（2026-09-24 用户要求，收尾清单 #8 加码）：图是"用户确认过的那一版"
    的凭据。数据一改（指纹就变），旧图就变成**没人确认过的图** —— 留在 `views/` 里只会让人
    （和下一个 agent）误以为"这就是当前这版"。以前只能靠人记得删。
    规则写死：**只留认账的图**（判据同 `figure_verdicts()`），其余移到 archive。
    当前正在给用户看的那张只要认账就**不会被删** —— 被清掉的永远是**别的版本**的图。

    ⚠ 是"移走"不是"销毁"：进 `views/archive/`（带原文件名），免得把人家画过的图无声无息弄丢。
    """
    removed: list[str] = []
    kept: list[str] = []
    # 两路图**都要清**（2026-09-30 加：平面图 + 立面图）—— 判据仍只有 `figure_verdicts()` 一份。
    for kind in ("plan", "elevation"):
        for verdict in figure_verdicts(plan, plan_mtime, kind=kind):
            if verdict["ok"]:
                kept.append(verdict["name"])
                continue
            path: Path = verdict["path"]
            try:
                ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                target = ARCHIVE_DIR / path.name
                n = 1
                while target.exists():
                    target = ARCHIVE_DIR / f"{path.stem}_{n}{path.suffix}"
                    n += 1
                path.replace(target)                 # 同盘移动，原子
                removed.append(f"{verdict['name']}（{verdict['why']}）")
            except OSError as exc:
                kept.append(f"{verdict['name']}（清不掉：{exc}）")
    return removed, kept


# --- 阶段二验收（2026-09-25 用户要求新增）--------------------------------------
#
# 【为什么要单开一个阶段】
#   原来只报 draft / confirmed，中间那段「图已经送去、正在等用户看」**没有状态**，
#   全靠人记 —— 于是 AI 可能在用户还没看图时就往下走，用户说"这里要改"之后
#   也没人记得改过什么。现在改成：
#       **生图 = 阶段二结束 → 进「阶段二验收」**
#       用户在图上看过说"行" → 闸门开，进第三阶段
#       说"要改" → 改参数 → 重落盘 → 重画图 → 再使用，**循环到点头为止**
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
    "changes_requested": "用户提了要改的地方：等改参数 → 重落盘 → 重画图",
    "accepted": "验收通过：闸门已开，可以进第三阶段",
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
    """**认这份数据**的图（图名）—— 也就是"该使用给用户看的那几张"。

    判据只有 `figure_verdicts()` 一份（svg 看当前指纹前 10 位 **+ 该覆盖的行都在图里**
    —— 局部轮是本次改动的那几行，没有基线才是全部行；位图一律不认），不另立标准。
    ⚠ **两路都算**（2026-09-30 加）：平面图 `plan_v1_overview*` + 立面图 `plan_v1_elevation*`
      —— 该给用户看的可能就是**两张卡**（改了高度时平面图看不出高低）。
    ⚠ 认账的图**一张都没有**时这里返回空表 ——
    验收状态就会停在 `awaiting_figure`，逼着出图，而不是"嘴上说图有了"。
    """
    out: list[str] = []
    try:
        for kind in ("plan", "elevation"):
            out += [v["name"] for v in figure_verdicts(plan, kind=kind) if v.get("ok")]
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
      ① 两张表都空 → `not_started`（没有坐标可看，谈不上验收）
      ② `confirmation.confirmed` → `accepted`（闸门已开）
      ③ 这一版几何上**最后一条事件**是"要改" → `changes_requested`
         （用户提了改动、还没落到数据里；下次落盘换了指纹，这条自然失效 = 进入新一轮）
      ④ **该出的每一路图都认账**（`figure_check().ok`，路数见 `REQUIRED_VIEWS`）→
         `awaiting_user`（可以使用给用户了）
      ⑤ 否则 → `awaiting_figure`（等出图 / 等补上缺的那一路）

    ⚠ **2026-09-30 修**：判据原先只是"**有没有**认账的图"，于是**只画了顶视图（缺 `elevation`）**
      也会报 `awaiting_user` —— 而 `confirm_plan` 会**按视图拒收** ⇒ **状态与闸门打架、会骗人**：
      一个不细看的 agent 会拿着一张图给用户、用户点头、然后确认被拒（白跑一轮，
      更坏的是它可能以为"已经确认过了"）。现在判据统一到 `figure_check()`
      —— 那个函数本来就是按 `REQUIRED_VIEWS` **逐视图**判的，一处置口径。
    """
    if not (plan.get("assets") or plan.get("whiteboxes")):
        return "not_started"
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
    # ⚠ 判据统一到 `figure_check()`：**每一条要求的视图都认账**才算"可以给用户看了"。
    #   （旧判据只看"有没有认账的图" → 只画一张也会说"可以给用户看了"，而确认时会按视图拒收。）
    #   判不动（异常）就按"还没齐"处理 —— 严的那一侧：宁可让 agent 再画一张，也不能骗它去确认。
    try:
        views_ok = bool(figure_check(plan).get("ok"))
    except Exception:                       # noqa: BLE001
        views_ok = False
    return "awaiting_user" if (figs and views_ok) else "awaiting_figure"


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
        "history": [e for e in (acc.get("events") or [])
                    if isinstance(e, dict)][-tail:],
    }


def write_plan(plan: dict) -> dict:
    """落盘 `plan_v1.json` —— **全工程唯一写入点**。返回
    `{"archived": 留档文件名, "removed_figures": [...], "kept_figures": [...]}`。

    写盘时顺带做三件"不许靠人记得"的事：
      - **给上一版留档**（收尾清单 #4）：闸门要回答的是"我们现在搭的，是不是当初确认的那一版"，
        可每次都覆盖同一个文件 —— 数据本体没了，只剩一个指纹数字。所以覆盖前把上一版
        原样复制进 `views/archive/`（文件名带 UTC 时间戳）。内容一模一样时不重复留档。
      - **清除不认账的图**（收尾清单 #8 加码，2026-09-24 用户要求）：数据一改，旧图就变成
        "没人确认过的图"，留在 `views/` 里只会误导人 —— 由 `clear_stale_figures()` 移进 archive。
        当前这张只要认账就留着。
      - **进「阶段二验收」**（2026-09-25 用户要求）：落盘 = 这一版几何进验收（`enter_acceptance()`）。
        收在这里的理由与上两条一样 —— `write_plan()` 是全工程唯一写入点，
        收在这儿就**没人能忘**（MCP 的 generate_plan、CLI 的 --confirm 都走这条路）。
    """
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    body = json.dumps(clean_numbers(plan), ensure_ascii=False, indent=2, allow_nan=False)
    # ⚠ 先记下"写盘之前"那份数据的时间：清图时要拿它当基准（写盘之后新数据一定比图新，
    #   会把用户刚确认的那张位图也一起清掉 —— 见 figure_verdicts() 里那段）。
    prev_mtime = 0.0
    if OUT_JSON.exists():
        try:
            prev_mtime = OUT_JSON.stat().st_mtime
        except OSError:
            prev_mtime = 0.0
    archived = ""
    if OUT_JSON.exists():
        try:
            old = OUT_JSON.read_text(encoding="utf-8-sig")
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
            target = ARCHIVE_DIR / f"plan_v1_{stamp}_{kind}.json"
            n = 1
            while target.exists():          # 同一秒连写两次也不许互相覆盖
                target = ARCHIVE_DIR / f"plan_v1_{stamp}_{kind}_{n}.json"
                n += 1
            target.write_text(old, encoding="utf-8")
            archived = target.name
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
                        _acc0["accepted_rows_from"] = target.name
                    # 分维签名一起回填（同一份旧数据现算得出来）—— 少回填它的话，
                    # 下一轮局部改会落进"判不出维度 → 两张图都要"的严判。
                    if not (_acc0.get("accepted_aspects") or {}):
                        _acc0["accepted_aspects"] = plan_row_aspects(_old_doc)
                        _acc0["accepted_aspects_from"] = target.name
                    _acc0["updated_at"] = _now()
                    save_acceptance(_acc0)
                except (ValueError, TypeError, OSError):
                    pass          # 回填失败不该把落盘带崩：届时 change_set() 会退回全量（严的那一侧）
    OUT_JSON.write_text(body, encoding="utf-8")

    # --- 落盘 = 进【阶段二验收】（2026-09-25 用户要求：生图即结束阶段二）--------
    # ⚠ 放在"写数据"之后、"清图"之前：验收台账要读**刚写下去的**那份数据的指纹。
    # ⚠ 台账写不动**不该把落盘本身带崩**（数据已经安全落地了）—— 降级成一条说明。
    try:
        acceptance = enter_acceptance(plan)
    except OSError as exc:
        acceptance = {"state": "unknown", "state_cn": f"验收台账没记上：{exc}"}

    # --- 「改完必须回读」= 代码强制（2026-09-26 用户要求把软约束变硬）----------------
    # 记一条"待回读"进台账（**不进几何指纹**）：在 `get_plan()` 之前，
    # `generate_plan` / `execute_build` 都会拒收，并把这一版的变更集摆出来。
    # ⚠ 与上面同理：台账写不动**不该把落盘带崩**（数据已经安全落地了）。
    try:
        mark_awaiting_readback(plan, change_set(plan))
    except OSError:
        pass

    try:
        removed, kept = clear_stale_figures(plan, plan_mtime=prev_mtime)
    except OSError as exc:                  # 清图失败不该把落盘本身带崩
        return {"archived": archived, "removed_figures": [],
                "kept_figures": [f"（清图没跑成：{exc}）"], "acceptance": acceptance}
    return {"archived": archived, "removed_figures": removed,
            "kept_figures": kept, "acceptance": acceptance}


def confirm_plan(who: str) -> int:
    """把「这张图被确认了」写回 plan_v1.json：谁、何时、哪份几何。

    闸门不过就**拒绝确认**（三条，与 MCP 工具 confirm_plan 同款）：
      ① **空规划不许确认**（两张表都空）：一份坐标都没有，确认它毫无意义 ——
         那时还没有图能给用户看；
      ② 几何指纹对不上 → plan_v1.json 被手改过，与算出来时不一致；
      ③ **图不认这份数据**（`views/` 里没有图 / 图内没有当前指纹 / 图比数据老）——
         AGENTS：图与数据不一致 = 等于没确认。

    退出码：0 已确认 / 1 文件不存在 / 3 指纹对不上 / 4 空规划 / 5 图不认数据。
    """
    if not OUT_JSON.exists():
        print("还没有 plan_v1.json：先算几何，再确认。")
        return 1
    plan = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))

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
        print("拒绝确认：plan_v1.json 的几何与算出来时不一致（被改过）。")
        return 3

    # --- ⚠ 图与数据必须一致（2026-09-24 收尾清单 #8）---
    # 为什么放在写盘之前：确认是"人对着一张图点头"这件事的留痕。图都没认这份数据，
    # 那次点头点的就不是这一版 —— 所以图不认账时**不写盘**。
    fig = figure_check(plan)
    if not fig["ok"]:
        print("拒绝确认：图不认这份数据 —— " + fig["note"])
        if fig.get("missing_views"):
            print("  **缺的视图**："
                  + "、".join(str(x) for x in (fig.get("view_labels") or fig["missing_views"])))
        if fig.get("local"):
            print("  这是**局部一轮**：拿 views/plan_v1.json 重画一张 —— 图内写明当前几何指纹，"
                  "**且把本次改动的那几行都画进去**"
                  f"（共 {len(fig.get('required_labels') or [])} 行）；没动的行**不必重画**。")
        else:
            print("  先拿 views/plan_v1.json 重画一张：图内写明当前几何指纹，"
                  "**且放置表每一行的 label 都要出现**（一行都不能漏）。")
        print("  要画哪些行：python -m mcp_server.planning.plan --figure-checklist")
        return 5

    confirmed = mark_confirmed(plan, who, user_quote="（命令行直连确认：确认人就在键盘前）")
    result = write_plan(confirmed)
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
    if not OUT_JSON.exists():
        print("还没有 plan_v1.json：先调 generate_plan 记一版，再记改动。")
        return 1
    if not items:
        print('用法：--change "要改的地方"（要改多条就多给几个参数）')
        return 2
    plan = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    acc = record_change_request(plan, items, by=by)
    print(f"已记下 {len(items)} 条改动 → 验收台账 {ACCEPTANCE_PATH}")
    for it in items:
        print(f"  · {it}")
    print(f"当前状态：第 {acc.get('round')} 轮，"
          f"{ACCEPTANCE_STATES.get(acc.get('state'), acc.get('state'))}")
    print("下一步：按这些改动改数据里的 pos / 占地 → 重跑 generate_plan（自动进新一轮）"
          "→ 重画图（图内写新指纹）→ 再把图交给用户。")
    return 0


# --- 入口 ---------------------------------------------------------------------
#
# 画图**不在本文件、也不在任何代码里**（2026-09-24 用户拍板）：
#   本文件只算几何数据；要图就由 AI 拿 plan_v1.json 自己画。


def figure_checklist_cli() -> int:
    """`--figure-checklist` —— 打印"这张图**必须画进去**的全部行"（画图前照着核对用）。

    为什么单开这条（2026-09-25 用户要求「画图让 Agent 不要漏位置坐标表任何一个元素」）：
    少画一行，`confirm_plan` 会拒收，但它**只在画完之后**才告诉你漏了哪几行 ——
    那就得画两遍。先在动手前把清单打出来，一遍画对。

    退出码：0 有清单 / 1 还没有规划数据 / 4 放置表是空的（没有行可画）。
    """
    if not OUT_JSON.exists():
        print("还没有 plan_v1.json：先调 MCP 工具 generate_plan 记一版。")
        return 1
    plan = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    assets = list(plan.get("assets") or [])
    boxes = list(plan.get("whiteboxes") or [])
    rows = assets + boxes
    if not rows:
        print("放置表是空的（初始化状态）—— 没有行要画。")
        return 4
    cs = change_set(plan)
    req = list(cs.get("required_labels") or [])
    removed = set(cs.get("removed") or [])
    pos_by_label: dict = {}
    for row in rows:
        if isinstance(row, dict):
            pos_by_label[str(row.get("label") or "")] = row.get("pos")
    head = plan_geometry_hash(plan)
    print(f"几何指纹（图里必须写上它的前 10 位）：{head[:10]}")
    print(f"世界：中心 {plan['world']['center']}  大小 {plan['world']['size']} m")
    if cs.get("local"):
        print(f"**局部一轮**（基线：{cs['baseline']}）—— 图里**只要**出现下面 {len(req)} 行；"
              f"另外 {cs['same']} 行没动、**不必重画**（它们早已确认过）。")
        print(f"  本次：新增 {len(cs['added'])} / 改动 {len(cs['changed'])} / 删除 {len(cs['removed'])}")
    else:
        print(f"图里必须出现下面**全部 {len(req)} 行**的 label（一行都不能少）：")
    for i, lab in enumerate(req, 1):
        mark = "（本版**删掉**的行：画上并标明『已删』）" if lab in removed else ""
        print(f"  {i:>3}. {lab}{mark}    pos={pos_by_label.get(lab)}")
    views = [str(v) for v in (cs.get("required_views") or ["top"])]
    print("**要出哪几张图**（按『这一版改的是哪一维』定）：")
    if cs.get("views_why"):
        print(f"  依据：{cs['views_why']}")
    for v in views:
        if v == "top":
            print("  · **平面图**（俯视）→ 存成 views/plan_v1_overview*.svg")
        else:
            print("  · **立面图**（正视图 X 向 或 侧视图 Y 向；两张都出也行）"
                  "→ 存成 views/plan_v1_elevation*.svg")
        print("      （图里要写当前指纹前 10 位 + 上面那几行的 label）")
    print("⚠ 每张图都**必须是 svg** —— 位图读不出文字，核验不了有没有漏；"
          "改高度的图只给俯视图 = **看不出改了什么**，确认时会被拒。")
    return 0


def main() -> int:
    """CLI 入口 —— **本文件不再自己算一版**，只报当前状态。

    为什么改成这样（2026-09-24 用户新规格）：放置坐标要 agent 规划，**没有"默认摆法"**
    这种东西。所以 CLI 给不出一份有意义的默认数据 —— 想算，请走 MCP 工具 `generate_plan`
    （它要你把 world / assets / whiteboxes 三段给进去）。这里只负责"看状态"和"确认"。
    """
    if not OUT_JSON.exists():
        print("还没有 plan_v1.json。")
        print("  放置坐标要由 agent 规划，本文件不生成默认摆法 —— 请调 MCP 工具 generate_plan。")
        return 1

    plan = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    conf = plan.get("confirmation", {}) or {}
    acc = acceptance_view(plan)      # 阶段二验收：**现算**（不读台账里的旧快照）

    print("阶段二 · 平面放置规划（现状）")
    print(f"  几何数据: {OUT_JSON}")
    print(f"  世界    : 中心 {plan['world']['center']}  大小 {plan['world']['size']} m"
          f"（{plan['world'].get('size_source', '来源未记录')}）")
    print(f"  已有资产: {len(plan.get('assets', []))} 个")
    print(f"  白膜    : {len(plan.get('whiteboxes', []))} 个")
    print(f"  当前阶段: {acc['stage']}（第 {acc['round']} 轮）—— {acc['state_cn']}")
    print(f"  图      : " + ("、".join(acc["figures"]) if acc["figures"]
                            else "**没有认这份数据的图**（没有图不许让用户确认）"))
    cs = change_set(plan)
    print("  画图清单: python -m mcp_server.planning.plan --figure-checklist"
          + (f"（**局部一轮**：图里只要出现本次改动的 {len(cs['required_labels'])} 行；"
             f"另外 {cs['same']} 行没动、不必重画）"
             if cs.get("local") else
             f"（图里必须出现全部 {len(cs['required_labels'])} 行的 label，一行都不能漏）"))
    print("  要出的图: " + "、".join(
        ("平面图 plan_v1_overview*.svg" if str(v) == "top"
         else "立面图 plan_v1_elevation*.svg")
        for v in (cs.get("required_views") or ["top"])))
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
