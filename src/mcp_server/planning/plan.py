# =============================================================================
# 阶段二 · 生成平面布局规划（纯 2D 几何，单位统一米）
#
# 【产出两样，各司其职】
#   ① views/plan_v1.json     —— 结构化几何数据：**道路多边形 / 地块包围盒 / 地块朝向 /
#                               禁止放置区域**。所有后续计算**只读它**。
#   ② views/plan_wire_v1.png —— 顶视图【线框】预览图，**仅作人工校验 / 发客户确认**。
#      为什么是 PNG 不是 SVG：客户能直接打开看（SVG 发给非技术客户常只看到一堆源码），
#      智能体这边也读得了（读图工具认位图，不认 SVG 文本标记）。
#      图是从数据单向画出来的；**数据不从图读** —— 全流程不做图像识别推理。
#
# 【输入】config/plan_params.json（路网走向、地块密度等参数）
# 【坐标】UE 左手系 Z-up：X 前进、Y 右、Z 上；平面内**右法线 = (-dy, dx)**（唯一口径）
#
# 【怎么跑】
#   出图（用户已窄授权：只许跑这一条命令，其它一律不跑）：
#     $env:PYTHONPATH='F:\MCP_Server\src'
#     & F:\MCP_Server\.venv\Scripts\python.exe -m mcp_server.planning.plan
#   确认这张图（闸门；把「谁 / 何时 / 哪份参数 / 哪份几何」写回 plan_v1.json）：
#     & F:\MCP_Server\.venv\Scripts\python.exe -m mcp_server.planning.plan --confirm "确认人"
#   ⚠ 参数或几何一改，两个指纹就对不上，本次确认**自动作废**，必须重新出图后重新确认。
# =============================================================================

# --- [完工-10] 阶段二几何（路网 / 地块 / 公共区域绿化区 / 禁放区）+ 确认留痕 + 14 项自检 ---
# 实测 2026-09-23：
#   · 自检 14/14 全过（含 2 项绿化区检查）；产物 plan_v1.json 已逐字段核过。
#   · 确认留痕六条路径全过：未确认→确认成功→不改沿用确认→改几何拒签(3)→改参数拒签(2)→还原。
# 追加记录：公共区域绿化区由用户拍板 A 补入；确认留痕机制由用户拍板「加！」补入。
# ⚠ 冻结区：改动前先看 AGENTS.md「完工标记」那节。

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import plan_render      # 客户面向的出图（骨架 + 示意占位），见 plan_render.py

# --- 常量 ---------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
"""仓库根目录。本文件在 src/mcp_server/planning/ 下，往上三层即根。"""

PARAMS_PATH = REPO_ROOT / "config" / "plan_params.json"
VIEWS_DIR = REPO_ROOT / "views"
OUT_JSON = VIEWS_DIR / "plan_v1.json"
OUT_PNG = VIEWS_DIR / "plan_wire_v1.png"

Point = tuple[float, float]
Poly = list[Point]

# 出图配色/画幅常量已移到 plan_render.py（本文件只管几何）。



# --- 纯 2D 基础（无业务；阶段三/四的碰撞检测也要用）----------------------------

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


def lateral_sign(side: str) -> float:
    """把 "left"/"right" 翻成正负号：left → -1（沿 -右法线），right → +1。"""
    return -1.0 if side == "left" else 1.0


def line_intersect(p: Point, d: Point, q: Point, e: Point):
    """参数直线 `p+t·d` 与 `q+u·e` 的交点；平行/反向返回 None。"""
    den = cross(d, e)
    if abs(den) < 1e-12:
        return None
    t = cross(v_sub(q, p), e) / den
    return v_add(p, v_mul(d, t))


def offset_polyline(pts: Poly, dist: float, miter_limit: float) -> Poly:
    """折线整体沿右法线偏移 dist 米；拐点斜接（miter），超上限退化成斜切（bevel）。

    Args:
        pts: 折线点列（≥ 2 点）。
        dist: 偏移距离（米），正 = 右侧、负 = 左侧。
        miter_limit: 斜接长度上限（米）。超了就斜切 —— 否则锐角处会刺出长尖角。
    """
    n = len(pts)
    if n < 2:
        raise ValueError("折线至少要 2 个点")
    dirs = [v_unit(v_sub(pts[i + 1], pts[i])) for i in range(n - 1)]
    out: Poly = []
    for i in range(n):
        if i == 0:
            out.append(v_add(pts[0], v_mul(right_normal(dirs[0]), dist)))
        elif i == n - 1:
            out.append(v_add(pts[-1], v_mul(right_normal(dirs[-1]), dist)))
        else:
            d0, d1 = dirs[i - 1], dirs[i]
            p1 = v_add(pts[i], v_mul(right_normal(d0), dist))
            p2 = v_add(pts[i], v_mul(right_normal(d1), dist))
            cut = line_intersect(p1, d0, p2, d1)
            if cut is None or v_len(v_sub(cut, pts[i])) > miter_limit:
                out.append(p1)          # 斜切：两个偏移点都放上
                out.append(p2)
            else:
                out.append(cut)         # 斜接：一个交点干净收口
    return out


def band(pts: Poly, s0: float, s1: float, miter_limit: float) -> Poly:
    """折线两侧各偏移 s0 / s1 米，围成**闭合带**：一侧正向 + 另一侧反向拼接。"""
    return offset_polyline(pts, s0, miter_limit) + list(
        reversed(offset_polyline(pts, s1, miter_limit))
    )


def polygon_area(poly: Poly) -> float:
    """鞋带公式算**带符号**面积（逆时针为正）。⚠ 不许在外面偷偷取绝对值。"""
    total = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]      # % n 让末点接回首点，闭合
        total += x1 * y2 - x2 * y1
    return total / 2.0


def orient_ccw(poly: Poly) -> Poly:
    """保证逆时针；顺时针就整体翻转（只改点序，不改坐标）。"""
    return poly if polygon_area(poly) >= 0 else list(reversed(poly))


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


def self_intersects(poly: Poly) -> bool:
    """自交检测：O(n²) 两两比，跳过相邻边（相邻边天生共享端点）。"""
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if (j + 1) % n == i or j == (i + 1) % n:
                continue
            c, d = poly[j], poly[(j + 1) % n]
            if _seg_hit(a, b, c, d):
                return True
    return False


def point_in_polygon(p: Point, poly: Poly) -> bool:
    """射线法判点是否在多边形内（用于"地块有没有压到人行道"这类检查）。"""
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

    为什么不用"角点在不在对方里面"来判：
      地块前沿与人行道外边线是**完全重合**的（设计如此），角点正好落在边界上，
      而射线法对"边界上的点"判定是不确定的 —— 2026-09-23 实测就因此误报了 4 个地块。
    所以改判两步，两步都不依赖"边界点算内还是算外"：
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


def polyline_length(pts: Poly) -> float:
    """折线总长。"""
    return sum(v_len(v_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1))


def point_at_distance(pts: Poly, s: float) -> tuple[Point, Point]:
    """沿折线走 s 米后的位置与所在段的方向（用于按面宽沿线切地块）。"""
    remaining = max(0.0, s)
    for i in range(len(pts) - 1):
        seg = v_sub(pts[i + 1], pts[i])
        seg_len = v_len(seg)
        if remaining <= seg_len or i == len(pts) - 2:
            t = 0.0 if seg_len == 0.0 else min(1.0, remaining / seg_len)
            return (v_add(pts[i], v_mul(seg, t)), v_unit(seg))
        remaining -= seg_len
    return (pts[-1], v_unit(v_sub(pts[-1], pts[-2])))


def quad_point(q: Poly, u: float, v: float) -> Point:
    """四边形双线性插值取点。q = [前左, 前右, 后右, 后左]，u 沿宽、v 沿深（0=前沿）。"""
    p0, p1, p2, p3 = q
    x = ((1 - u) * (1 - v) * p0[0] + u * (1 - v) * p1[0]
         + u * v * p2[0] + (1 - u) * v * p3[0])
    y = ((1 - u) * (1 - v) * p0[1] + u * (1 - v) * p1[1]
         + u * v * p2[1] + (1 - u) * v * p3[1])
    return (x, y)


def quad_inset(q: Poly, u0: float, u1: float, v0: float, v1: float) -> Poly:
    """把一个四边形按比例内缩成一个更小的四边形（用来切「房前草坪」「建筑区」）。"""
    return [quad_point(q, u0, v0), quad_point(q, u1, v0),
            quad_point(q, u1, v1), quad_point(q, u0, v1)]


def clean_numbers(obj):
    """输出前把 **-0.0 归一成 0.0**（递归处理 dict / list / tuple）。

    为什么非做不可（2026-09-23 实测踩过，写在 main.py 的 num() 注释里）：
      Python 把 -0.0 序列化成 "-0.0"，而 JS 的 JSON.stringify(-0) 得到 "0" —— **往返不无损**，
      宿主会判定整条结果 "value is not lossless JSON" 并**整条拒绝**（不是丢一个字段，是整条不可用）。
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


# --- 规划生成 -----------------------------------------------------------------

def load_params(path: Path = PARAMS_PATH) -> dict:
    """读参数文件（**只读**）。改参数 = 上一张规划图作废 = 重新出图重新确认。"""
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def build_plan(P: dict) -> dict:
    """按参数算出平面布局：道路多边形 / 人行道 / 绿化带 / 地块 / 禁放区 + 自检。"""
    world = P["world"]
    road = P["road"]
    lot_cfg = P["lot"]

    cl: Poly = [(float(a), float(b)) for a, b in road["centerline"]]
    w = float(road["width"])
    strip = float(road["median_strip_width"])
    walk = float(road["sidewalk_width"])
    miter = float(road["miter_limit"])
    end_clear = float(road["end_clear_radius"])
    frontage = float(lot_cfg["frontage"])
    depth = float(lot_cfg["depth"])
    lawn_depth = float(lot_cfg["front_lawn_depth"])
    side_set = float(lot_cfg["side_setback"])
    back_set = float(lot_cfg["back_setback"])
    total_len = polyline_length(cl)

    # --- 道路多边形 + 分段信息 ---
    road_poly = orient_ccw(band(cl, -w / 2.0, w / 2.0, miter))
    segments = []
    for i in range(len(cl) - 1):
        d = v_sub(cl[i + 1], cl[i])
        segments.append({
            "index": i,
            "start": list(cl[i]),
            "end": list(cl[i + 1]),
            "heading_deg": round(math.degrees(math.atan2(d[1], d[0])) % 360.0, 3),
            "length_m": round(v_len(d), 4),
        })

    surfaces: list[dict] = [{
        "surface_id": "RD1",
        "kind": "road",
        "side": "center",
        "width_m": w,
        "polygon": road_poly,
        "centerline": [list(p) for p in cl],
    }]
    sidewalks: list[dict] = []

    # --- 绿化带与人行道（按参数指定的侧别生成）---
    all_sides = sorted(set(road["sidewalk_sides"]) | set(road["lot_sides"]))
    for side in all_sides:
        sgn = lateral_sign(side)
        strip_in = sgn * (w / 2.0)
        strip_out = sgn * (w / 2.0 + strip)
        walk_in, walk_out = strip_out, sgn * (w / 2.0 + strip + walk)
        surfaces.append({
            "surface_id": f"GS_{side}",
            "kind": "green_strip",
            "side": side,
            "width_m": strip,
            "polygon": orient_ccw(band(cl, strip_in, strip_out, miter)),
            "offset_range_m": [round(min(strip_in, strip_out), 4),
                               round(max(strip_in, strip_out), 4)],
        })
        if side in road["sidewalk_sides"]:
            walk_cl = offset_polyline(cl, (walk_in + walk_out) / 2.0, miter)
            ent = {
                "surface_id": f"SW_{side}",
                "kind": "sidewalk",
                "side": side,
                "width_m": walk,
                "polygon": orient_ccw(band(cl, walk_in, walk_out, miter)),
                "centerline": [list(p) for p in walk_cl],
                "offset_range_m": [round(min(walk_in, walk_out), 4),
                                   round(max(walk_in, walk_out), 4)],
            }
            sidewalks.append(ent)
            surfaces.append(ent)

    # --- 宅基地地块：沿中心线一侧、按面宽等分；路口/端点范围内不划 ---
    lots: list[dict] = []
    for side in road["lot_sides"]:
        sgn = lateral_sign(side)
        front_off = sgn * (w / 2.0 + strip + walk)      # 地块前沿 = 人行道外边线
        usable = total_len - 2.0 * end_clear
        if usable < frontage:
            continue
        count = int(usable // frontage)                 # 能切几块
        leftover = usable - count * frontage            # 余量：两头均摊，让整排居中
        start_s = end_clear + leftover / 2.0
        for k in range(count):
            a_s = start_s + k * frontage
            b_s = a_s + frontage
            p_a, d_a = point_at_distance(cl, a_s)
            p_b, d_b = point_at_distance(cl, b_s)
            n_a, n_b = right_normal(d_a), right_normal(d_b)
            f_a = v_add(p_a, v_mul(n_a, front_off))                      # 前沿左
            f_b = v_add(p_b, v_mul(n_b, front_off))                      # 前沿右
            b_a = v_add(p_a, v_mul(n_a, front_off + sgn * depth))        # 后沿左
            b_b = v_add(p_b, v_mul(n_b, front_off + sgn * depth))        # 后沿右
            quad = [f_a, f_b, b_b, b_a]
            facing_vec = v_mul(n_a, -sgn)               # 从地块指向道路
            deg = math.degrees(math.atan2(facing_vec[1], facing_vec[0])) % 360.0
            axis = ("+X" if abs(facing_vec[0]) >= abs(facing_vec[1]) else "+Y")
            if (abs(facing_vec[0]) >= abs(facing_vec[1]) and facing_vec[0] < 0):
                axis = "-X"
            elif (abs(facing_vec[1]) > abs(facing_vec[0]) and facing_vec[1] < 0):
                axis = "-Y"
            lots.append({
                "lot_id": f"L{len(lots) + 1}",
                "side": side,
                "frontage_m": frontage,
                "depth_m": depth,
                "corners": [list(p) for p in quad],
                "bbox": aabb_of(quad),
                "facing": axis,
                "facing_deg": round(deg, 3),
                "facing_vector": [round(facing_vec[0], 6), round(facing_vec[1], 6)],
                "arc_span_m": [round(a_s, 4), round(b_s, 4)],
                "front_lawn": {
                    "polygon": [list(p) for p in
                                quad_inset(quad, 0.0, 1.0, 0.0, lawn_depth / depth)],
                },
                "building_area": {
                    "polygon": [list(p) for p in quad_inset(
                        quad, side_set / frontage, 1.0 - side_set / frontage,
                        lawn_depth / depth, 1.0 - back_set / depth)],
                },
            })

    # --- 禁止放置区域：路面、人行道、道路端点、路口 ---
    zones: list[dict] = [
        {"zone_id": "ZR_ROAD", "kind": "road_surface", "shape": "polygon",
         "points": [list(p) for p in road_poly], "reason": "路面：禁止放置任何物体"},
    ]
    for sw in sidewalks:
        zones.append({
            "zone_id": f"ZR_{sw['surface_id']}", "kind": "sidewalk_surface",
            "shape": "polygon", "points": [list(p) for p in sw["polygon"]],
            "reason": "人行道：禁止放置任何物体",
        })
    nodes = [{"node_id": "N0", "pos": list(cl[0]), "kind": "road_end",
              "clear_radius": end_clear},
             {"node_id": f"N{len(cl) - 1}", "pos": list(cl[-1]), "kind": "road_end",
              "clear_radius": end_clear}]
    thr = float(road["turn_angle_deg_for_node"])
    for i in range(1, len(cl) - 1):
        d0 = v_unit(v_sub(cl[i], cl[i - 1]))
        d1 = v_unit(v_sub(cl[i + 1], cl[i]))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, v_dot(d0, d1)))))
        if ang > thr:
            nodes.append({"node_id": f"N{i}", "pos": list(cl[i]),
                          "kind": "intersection",
                          "clear_radius": float(road["intersection_clear_radius"]),
                          "turn_angle_deg": round(ang, 2)})
    for nd in nodes:
        zones.append({
            "zone_id": f"ZR_{nd['node_id']}", "kind": nd["kind"], "shape": "circle",
            "center": nd["pos"], "radius": nd["clear_radius"],
            "reason": f"{nd['node_id']}（{nd['kind']}）禁止放置半径 {nd['clear_radius']}m",
        })

    # --- 世界边界 ---
    cx, cy = float(world["center"][0]), float(world["center"][1])
    hx, hy = float(world["half_extent"][0]), float(world["half_extent"][1])
    bounds = [[cx - hx, cy - hy], [cx + hx, cy - hy],
              [cx + hx, cy + hy], [cx - hx, cy + hy]]

    # --- 公共区域划分：人行道外侧、地块之间的**剩余区域** → 绿化区 ---
    # （规划文件·第二阶段·核心动作 3；用途：行道树、灌木、背景绿化）
    green_cfg = P.get("green", {}) or {}
    far_depth_want = float(green_cfg.get("far_side_depth_m", 9.0))
    d0 = v_unit(v_sub(cl[1], cl[0]))
    n0 = right_normal(d0)
    offs_bounds = [v_dot(v_sub((bx, by), cl[0]), n0) for bx, by in bounds]
    green_zones: list[dict] = []

    # ① 没排人行道也没排地块的那一侧：整条带 = 背景绿化（夹进世界边界，绝不越界）
    for side in ("left", "right"):
        if side in road["lot_sides"] or side in road["sidewalk_sides"]:
            continue
        sgn = lateral_sign(side)
        bound_off = max(offs_bounds) if sgn > 0 else min(offs_bounds)
        near = sgn * (w / 2.0)
        far = sgn * min(abs(bound_off), w / 2.0 + far_depth_want)
        if abs(far - near) < 1e-6:
            continue
        green_zones.append({
            "zone_id": f"GZ_far_{side}", "kind": "green_area", "side": side,
            "purpose": "背景绿化（行道树 / 灌木）",
            "polygon": [list(p) for p in orient_ccw(band(cl, near, far, miter))],
        })

    # ② 地块那一侧：两端没排到的余量 + 地块背后到用地边界的余量
    for side in road["lot_sides"]:
        sgn = lateral_sign(side)
        front_off = sgn * (w / 2.0 + strip + walk)
        back_off = front_off + sgn * depth
        usable = total_len - 2.0 * end_clear
        count = int(usable // frontage)
        leftover = usable - count * frontage
        row_start = end_clear + leftover / 2.0
        row_end = row_start + count * frontage
        for tag, a_s, b_s in (("head", 0.0, row_start), ("tail", row_end, total_len)):
            if b_s - a_s > 1e-6:
                sub = [point_at_distance(cl, a_s)[0], point_at_distance(cl, b_s)[0]]
                green_zones.append({
                    "zone_id": f"GZ_{tag}_{side}", "kind": "green_area", "side": side,
                    "purpose": "地块两端余量（绿化 / 灌木）",
                    "polygon": [list(p) for p in orient_ccw(band(sub, front_off, back_off, miter))],
                })
        bound_off = max(offs_bounds) if sgn > 0 else min(offs_bounds)
        if abs(bound_off - back_off) > 1e-6:
            green_zones.append({
                "zone_id": f"GZ_rear_{side}", "kind": "green_area", "side": side,
                "purpose": "地块背后到用地边界的余量",
                "polygon": [list(p) for p in orient_ccw(band(cl, back_off, bound_off, miter))],
            })

    # --- 自检：把验收标准算出来放进数据，不靠嘴说 ---
    checks: list[dict] = []

    exp_area = total_len * w
    act_area = abs(polygon_area(road_poly))
    checks.append({
        "name": "道路多边形面积 ≈ 中心线长度×路宽（误差 < 1%）",
        "expected_m2": round(exp_area, 4), "actual_m2": round(act_area, 4),
        "ok": exp_area > 0 and abs(act_area - exp_area) / exp_area < 0.01,
    })

    for surf in surfaces:
        poly = surf["polygon"]
        checks.append({"name": f"{surf['surface_id']}：无自交",
                       "ok": not self_intersects(poly)})
        checks.append({"name": f"{surf['surface_id']}：面积为正（逆时针）",
                       "ok": polygon_area(poly) > 0})

    out_of_bounds: list[str] = []
    for lot in lots:
        for p in lot["corners"]:
            if not (bounds[0][0] - 1e-6 <= p[0] <= bounds[1][0] + 1e-6
                    and bounds[0][1] - 1e-6 <= p[1] <= bounds[2][1] + 1e-6):
                out_of_bounds.append(lot["lot_id"])
                break
    checks.append({"name": "每个地块的四角都在世界边界内",
                   "bad": out_of_bounds, "ok": not out_of_bounds})

    # 「地块不压路」判据：**面积重叠**才算压，仅共边不算 ——
    # 地块前沿与人行道外边线本来就重合（设计如此），
    # 用"角点在不在多边形里"判会误报（2026-09-23 实测 4 块全误报）。
    blocking = [s for s in surfaces if s["kind"] in ("road", "green_strip", "sidewalk")]
    overlap_surface: list[str] = []
    for lot in lots:
        lot_poly = [tuple(c) for c in lot["corners"]]
        for surf in blocking:
            if polygons_overlap(lot_poly, [tuple(p) for p in surf["polygon"]]):
                overlap_surface.append(f"{lot['lot_id']}×{surf['surface_id']}")
                break
    checks.append({
        "name": "地块与路面/绿化带/人行道都无面积重叠（仅共边不算）",
        "bad": overlap_surface,
        "ok": not overlap_surface,
    })

    overlap: list[str] = []
    for i in range(len(lots)):
        for j in range(i + 1, len(lots)):
            if lots[i]["side"] != lots[j]["side"]:
                continue
            a, b = lots[i]["bbox"], lots[j]["bbox"]
            if (a["min"][0] < b["max"][0] - 1e-6 and b["min"][0] < a["max"][0] - 1e-6
                    and a["min"][1] < b["max"][1] - 1e-6
                    and b["min"][1] < a["max"][1] - 1e-6):
                overlap.append(f"{lots[i]['lot_id']}×{lots[j]['lot_id']}")
    checks.append({"name": "同侧地块两两不重叠", "bad": overlap, "ok": not overlap})

    wrong_facing: list[str] = []
    for lot in lots:
        mid_s = (lot["arc_span_m"][0] + lot["arc_span_m"][1]) / 2.0
        road_pt, _ = point_at_distance(cl, mid_s)
        corners = lot["corners"]
        lot_center = (sum(p[0] for p in corners) / 4.0,
                      sum(p[1] for p in corners) / 4.0)
        to_road = v_sub(road_pt, lot_center)
        if v_dot((lot["facing_vector"][0], lot["facing_vector"][1]), to_road) <= 0:
            wrong_facing.append(lot["lot_id"])
    checks.append({"name": "地块朝向指向道路", "bad": wrong_facing,
                   "ok": not wrong_facing})

    outside_build: list[str] = []
    for lot in lots:
        for p in lot["building_area"]["polygon"]:
            if not point_in_polygon(p, [tuple(c) for c in lot["corners"]]):
                outside_build.append(lot["lot_id"])
                break
    checks.append({"name": "建筑区在地块之内", "bad": outside_build,
                   "ok": not outside_build})

    # 绿化区自检：形状合法、且**不压**地块/路面/人行道/绿化带（共边不算）
    bad_zone_shape: list[str] = []
    bad_zone_overlap: list[str] = []
    others = [("lot", [tuple(c) for c in lot["corners"]]) for lot in lots]
    others += [("surface", [tuple(p) for p in s["polygon"]]) for s in surfaces]
    for gz in green_zones:
        poly = [tuple(p) for p in gz["polygon"]]
        if self_intersects(poly) or polygon_area(poly) <= 0:
            bad_zone_shape.append(gz["zone_id"])
        for other in others:
            if polygons_overlap(poly, other[1]):
                bad_zone_overlap.append(f"{gz['zone_id']}×{other[0]}")
                break
    checks.append({"name": "绿化区：无自交且面积为正", "bad": bad_zone_shape,
                   "ok": not bad_zone_shape})
    checks.append({"name": "绿化区不压地块/路面/人行道/绿化带（仅共边不算）",
                   "bad": bad_zone_overlap, "ok": not bad_zone_overlap})

    plan = {
        "stage": "阶段二 · 平面布局规划",
        "unit": "m",
        "coordinate_system": {
            "handedness": "left",
            "height_axis": world["height_axis"],
            "forward_axis": world["forward_axis"],
            "right_axis": world["right_axis"],
            "right_normal_rule": "right_normal(d) = (-dy, dx)",
        },
        "world": {
            "origin": [float(world["origin"][0]), float(world["origin"][1])],
            "center": [cx, cy],
            "half_extent": [hx, hy],
            "bounds": bounds,
        },
        "roads": [{
            "road_id": "RD1",
            "kind": "main",
            "centerline": [list(p) for p in cl],
            "width_m": w,
            "polygon": [list(p) for p in road_poly],
            "segments": segments,
            "total_length_m": round(total_len, 4),
        }],
        "surfaces": [{**s, "polygon": [list(p) for p in s["polygon"]]}
                     for s in surfaces],
        "lots": lots,
        "green_zones": green_zones,
        "no_place_zones": zones,
        "checks": checks,
        "preview": {
            "image": "views/plan_wire_v1.png",
            "format": "png",
            "display_only": True,
            "note": ("线框预览图仅供人工校验路网形态；"
                     "本 JSON 不依赖任何图片，图是从本数据单向画出来的"),
        },
        "confirmation": {
            "required": True,
            "confirmed": False,
            "confirmed_by": "",
            "confirmed_at": "",
            "params_file": "config/plan_params.json",
            "note": "未确认前不许进入第三阶段（AGENTS.md「规划图闸门」）",
        },
    }
    # 出图时就把两个指纹钉死：参数指纹（哪份参数算的）+ 几何指纹（几何本身长什么样）。
    # 几何指纹算的是「除 confirmation 之外」的全部内容，所以下面这两行不会自己改自己。
    # （净化 -0.0 那步已经收进 plan_geometry_hash 内部，这里不用再管）
    plan["confirmation"]["params_hash"] = params_hash(P)
    plan["confirmation"]["plan_hash"] = plan_geometry_hash(plan)
    return plan


# --- 确认与留痕（闸门）--------------------------------------------------------

def params_hash(params: dict) -> str:
    """参数指纹：对参数**取值**做哈希（与文件排版无关；改任何一个值都会变）。"""
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def plan_geometry_hash(plan: dict) -> str:
    """几何指纹：对 plan_v1.json 里**除 confirmation 之外**的全部内容做哈希。

    为什么排除 confirmation：确认信息要写回同一个文件，不能把自己算进去。
    这个指纹同时覆盖两种情况 —— **参数变了**、**代码改了**，只要几何不一样指纹就不一样。

    ⚠ **内部先做数字净化（-0.0 → 0.0）**，这是必须的：
      落盘写的是净化后的值，而内存里的对象可能带 -0.0（例如 facing_vector 的 (-0.0, 1.0)），
      两边直接比就会**对着一张刚出的图喊"文件被改过"**（2026-09-23 经 MCP 实测踩到）。
      净化收在这里、不指望每个调用方记得做 —— 单点收口才不会再漏。
    """
    body = {k: v for k, v in clean_numbers(plan).items() if k != "confirmation"}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def gate_check(params: dict, plan: dict) -> dict:
    """闸门状态：这张规划图**还作不作数**。只读，不改任何东西。

    谁都能调（MCP 工具 / CLI / 别的 agent），判定口径只有这一份。
    """
    conf = plan.get("confirmation", {}) or {}
    now_params = params_hash(params)
    now_geom = plan_geometry_hash(plan)
    return {
        "params_hash_ok": (not conf.get("params_hash")) or conf["params_hash"] == now_params,
        "plan_hash_ok": (not conf.get("plan_hash")) or conf["plan_hash"] == now_geom,
        "confirmed": bool(conf.get("confirmed")),
        "confirmed_by": conf.get("confirmed_by", ""),
        "confirmed_at": conf.get("confirmed_at", ""),
        "params_hash": now_params,
        "plan_hash": now_geom,
        "stored_params_hash": conf.get("params_hash", ""),
        "stored_plan_hash": conf.get("plan_hash", ""),
    }


def mark_confirmed(plan: dict, who: str, params: dict) -> dict:
    """返回一份**带确认信息的新 plan**（不改传入的那个对象）。

    调用方负责先过 `gate_check`：两道闸不过就别确认。
    """
    out = dict(plan)
    out["confirmation"] = {
        **(plan.get("confirmation", {}) or {}),
        "required": True,
        "confirmed": True,
        "confirmed_by": who,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "params_hash": params_hash(params),
        "plan_hash": plan_geometry_hash(plan),
        "note": "已确认。参数或几何一变，这两个指纹就对不上，本次确认自动作废。",
    }
    return out


def confirm_plan(who: str) -> int:
    """把「这张图被确认了」写回 plan_v1.json：谁、何时、哪份参数、哪份几何。

    两道闸，任一不过就**拒绝确认**：
      ① 参数指纹对不上 → 参数改过，这张图作废，必须重新出图；
      ② 几何指纹对不上 → plan_v1.json 被手改过，与出图时不一致。
    """
    if not OUT_JSON.exists():
        print("还没有 plan_v1.json：先出图，再确认。")
        return 1
    params = load_params()
    plan = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    gate = gate_check(params, plan)

    if not gate["params_hash_ok"]:
        print("拒绝确认：config/plan_params.json 改过了，这张图作废 —— 重新出图后再确认。")
        return 2
    if not gate["plan_hash_ok"]:
        print("拒绝确认：plan_v1.json 的几何与出图时不一致（被改过）。")
        return 3

    plan = mark_confirmed(plan, who, params)
    OUT_JSON.write_text(
        json.dumps(clean_numbers(plan), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"已确认并留痕：{who}")
    print(f"  时间      {plan['confirmation']['confirmed_at']}")
    print(f"  参数指纹  {gate['params_hash'][:16]}…")
    print(f"  几何指纹  {gate['plan_hash'][:16]}…")
    return 0


# --- 出图与入口 -----------------------------------------------------------------
#
# 画图**不在本文件**：本文件只算几何。
#   客户面向的图（骨架 + 示意占位）→ planning/plan_render.py
#   画布 / PNG 编码                → planning/png.py


def main() -> int:
    """出图入口：产 JSON + 线框预览图，并把自检结果打出来。只读参数、只写 views/。"""
    params = load_params()
    plan = build_plan(params)
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)

    # 重跑时：几何与参数**都没变** → 沿用上一次的确认；变过就自动作废（不沿用）
    if OUT_JSON.exists():
        old_conf = (json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
                    .get("confirmation", {}) or {})
        if (old_conf.get("confirmed")
                and old_conf.get("plan_hash") == plan["confirmation"]["plan_hash"]
                and old_conf.get("params_hash") == plan["confirmation"]["params_hash"]):
            plan["confirmation"].update({
                "confirmed": True,
                "confirmed_by": old_conf.get("confirmed_by", ""),
                "confirmed_at": old_conf.get("confirmed_at", ""),
                "note": "几何与参数均未变，沿用上一次确认。",
            })

    # 写盘前过一遍数字净化（-0.0 → 0.0）；allow_nan=False 让 inf/nan **当场炸**，
    # 而不是写出 "Infinity" 这种非法 JSON 让下游某一步莫名其妙失败。
    OUT_JSON.write_text(
        json.dumps(clean_numbers(plan), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    render_stats = plan_render.render(plan, params, OUT_PNG)

    print("阶段二 · 平面布局规划已生成：")
    print(f"  几何数据: {OUT_JSON}")
    print(f"  客户图  : {OUT_PNG}（仅展示；可直接发客户确认）")
    print(f"  图上示意: 房屋占位 {render_stats['houses_schematic']} 块 / "
          f"行道树 {render_stats['street_trees_schematic']} 棵 / "
          f"背景树 {render_stats['tree_line_schematic']} 棵，"
          f"图幅 {render_stats['size_px'][0]}×{render_stats['size_px'][1]} px")
    print(f"  道路: {len(plan['roads'])} 条，总长 "
          f"{plan['roads'][0]['total_length_m']} m，宽 {plan['roads'][0]['width_m']} m")
    print(f"  面: {len(plan['surfaces'])} 个（含路面/绿化带/人行道）")
    print(f"  地块: {len(plan['lots'])} 块，每块面宽 "
          f"{plan['lots'][0]['frontage_m'] if plan['lots'] else '-'} m，"
          f"深 {plan['lots'][0]['depth_m'] if plan['lots'] else '-'} m")
    print(f"  禁止放置区域: {len(plan['no_place_zones'])} 个")
    print(f"  绿化区: {len(plan['green_zones'])} 个")
    conf = plan["confirmation"]
    print("  确认状态: " + (f"**已确认** by {conf['confirmed_by']} @ {conf['confirmed_at']}"
                          if conf.get("confirmed") else
                          "**未确认** —— 这张图还没生效，不许进第三阶段"))
    failed = 0
    for chk in plan["checks"]:
        if not chk["ok"]:
            failed += 1
        print(f"  [{'通过' if chk['ok'] else '失败'}] {chk['name']}"
              + (f"  细节={chk.get('bad')}" if chk.get("bad") else ""))
    print(f"自检：{len(plan['checks'])} 项，失败 {failed} 项")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--confirm":
        who = argv[1] if len(argv) > 1 else "用户"
        raise SystemExit(confirm_plan(who))
    raise SystemExit(main())
