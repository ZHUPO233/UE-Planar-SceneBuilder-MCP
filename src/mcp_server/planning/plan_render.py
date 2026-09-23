# =============================================================================
# 客户面向的平面规划图渲染（PNG）
#
# 【分工】
#   plan.py        = 几何：只算数据，产出 plan_v1.json（阶段二的契约）
#   plan_render.py = 画图：只读「数据 + 出图参数」，**不产生任何数据**
#   png.py         = 画布/编码（零依赖 PNG）
#
# 【图上两类东西，必须分得开】
#   ① 骨架（线框）：路面 / 人行道 / 绿化带 / 地块 / 禁放区 / 世界边界 —— 来自 plan_v1.json
#   ② 示意图（SCHEMATIC）：房屋占位块、行道树、背景树线 —— **只为让客户看得懂**，
#      尺寸取自已确认资产的实测值，**不进 plan_v1.json、不参与任何计算**
#      ⚠ 图上必须写明 SCHEMATIC，不许让人误当最终摆位。
#
# 【朝向】preview.up_axis = "X" → +X 朝上（街道往画面深处走，像参考图那样），+Y 朝右。
#   映射：屏幕 x ← 世界 y，屏幕 y ← −世界 x。判定过行列式 > 0，**不是镜像**。
# =============================================================================

import math
from pathlib import Path

from .png import Canvas

# --- 配色 ---------------------------------------------------------------------

C_INK = (34, 34, 34)          # 主结构线 / 文字
C_GRID = (232, 232, 232)      # 网格
C_BOUNDS = (170, 170, 170)    # 世界边界
C_ROAD = (34, 34, 34)         # 路面
C_WALK = (95, 95, 95)         # 人行道
C_GREEN = (75, 139, 59)       # 绿化带
C_LOT = (31, 95, 191)         # 宅基地
C_ZONE = (204, 51, 51)        # 禁止放置区 / 免责红字
C_RED = (204, 0, 0)           # 场景中心 / 道路中心线
C_NOTE = (110, 110, 110)      # 次要文字
C_HOUSE = (222, 219, 210)     # 房屋占位块（填充）
C_HOUSE_LINE = (120, 116, 108)
C_TREE = (198, 226, 186)      # 树冠（填充）
C_TREE_LINE = (86, 140, 66)

PAD_SIDE = 70                 # 左右留白（像素）
PAD_TOP = 86                  # 顶部留白（标题两行）
PAD_BOTTOM = 168              # 底部留白（图例 + 比例尺 + 免责）


# --- 小工具（只在画图这一层用；口径与 plan.py 的右法线一致）------------------

def _perp(d: tuple[float, float]) -> tuple[float, float]:
    """平面内右法线：d=(dx,dy) → (-dy,dx)。与 plan.py 同一口径，别在这里另立规矩。"""
    return (-d[1], d[0])


def _unit(d: tuple[float, float]) -> tuple[float, float]:
    """单位化；零向量原样返回。"""
    n = math.hypot(d[0], d[1])
    return (0.0, 0.0) if n == 0.0 else (d[0] / n, d[1] / n)


def _band_edges(polygon: list) -> tuple[list, list]:
    """从一个「带」多边形里拆出两条边线（与 plan.py 的 band() 构造方式对应）。

    band() 的构造是「一侧正向 + 另一侧反向」，所以：
      前半 = 一侧（s0）边线；后半反过来 = 另一侧（s1）边线。
    这样不必再算一次偏移，直接拿现成的多边形拆。
    """
    n = len(polygon) // 2
    side_a = [(p[0], p[1]) for p in polygon[:n]]
    side_b = [(p[0], p[1]) for p in polygon[n:]][::-1]
    return side_a, side_b


def _sample_line(pts: list, spacing: float) -> list:
    """沿折线等距取点（含首尾）。用来放行道树 / 背景树线。"""
    if len(pts) < 2 or spacing <= 0:
        return []
    total = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                for i in range(len(pts) - 1))
    count = max(1, int(total // spacing))
    out = []
    for k in range(count + 1):
        target = min(total, k * spacing + spacing / 2.0)
        walked = 0.0
        for i in range(len(pts) - 1):
            seg = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            if walked + seg >= target or i == len(pts) - 2:
                t = 0.0 if seg == 0 else min(1.0, (target - walked) / seg)
                out.append((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                            pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t))
                break
            walked += seg
    return out


def _house_quads(plan: dict, params: dict) -> list:
    """给每块宅基地算一个「房屋占位块」（示意用，不写回数据）。

    做法：取地块的建筑区四角 → 用它的宽/深方向摆一个矩形，
    尺寸取已确认资产的实测值（house1 实测 89.09×69.57cm × 13.47 ≈ 12.0×9.4m），
    且**不许超出建筑区**。
    """
    sch = (params.get("preview", {}) or {}).get("schematic", {}) or {}
    fw, fd = sch.get("house_footprint_m", [12.0, 9.4])
    quads = []
    for lot in plan["lots"]:
        area = [(p[0], p[1]) for p in lot["building_area"]["polygon"]]
        if len(area) != 4:
            continue
        f_l, f_r, b_r, b_l = area
        u = _unit((f_r[0] - f_l[0], f_r[1] - f_l[1]))       # 面宽方向
        v = _unit((b_l[0] - f_l[0], b_l[1] - f_l[1]))       # 进深方向（朝地块里面）
        width = math.hypot(f_r[0] - f_l[0], f_r[1] - f_l[1])
        depth = math.hypot(b_l[0] - f_l[0], b_l[1] - f_l[1])
        hw = min(float(fw), width) / 2.0
        hd = min(float(fd), depth) / 2.0
        # 房子贴着建筑区**前沿**：前面留出院前草坪、后面留后院（参考图就是这样），
        # **不居中** —— 居中会让房子缩到院子中间，看着不像住宅。
        fx = (f_l[0] + f_r[0]) / 2.0
        fy = (f_l[1] + f_r[1]) / 2.0
        cx = fx + v[0] * hd
        cy = fy + v[1] * hd
        quad = [
            (cx - u[0] * hw - v[0] * hd, cy - u[1] * hw - v[1] * hd),
            (cx + u[0] * hw - v[0] * hd, cy + u[1] * hw - v[1] * hd),
            (cx + u[0] * hw + v[0] * hd, cy + u[1] * hw + v[1] * hd),
            (cx - u[0] * hw + v[0] * hd, cy - u[1] * hw + v[1] * hd),
        ]
        quads.append((lot["lot_id"], quad))
    return quads


def render(plan: dict, params: dict, path: Path) -> dict:
    """把 plan_v1.json 画成给客户看的 PNG。返回一份「画了什么」的统计。"""
    pv = params.get("preview", {}) or {}
    sch = pv.get("schematic", {}) or {}
    up_x = str(pv.get("up_axis", "X")).upper() == "X"
    px_per_m = float(pv.get("scale_px_per_m", 11.0))
    grid_step = float(pv.get("grid_spacing_m", 10.0))
    bar_m = float(pv.get("scale_bar_m", 10.0))
    pad_m = float(pv.get("view_padding_m", 3.0))

    houses = _house_quads(plan, params) if sch.get("enabled") else []

    # 行道树：沿**绿化带中心线**等距取点（放带子中间，与参考图一致）
    street_trees: list = []
    if sch.get("enabled"):
        for surf in plan["surfaces"]:
            if surf["kind"] == "green_strip":
                a, b = _band_edges(surf["polygon"])
                mid = [((a[i][0] + b[i][0]) / 2.0, (a[i][1] + b[i][1]) / 2.0)
                       for i in range(min(len(a), len(b)))]
                street_trees += _sample_line(
                    mid, float(sch.get("street_tree_spacing_m", 12.0)))

    # 背景树线：沿路面靠外那条边线，再向外推 tree_line_offset
    tree_line: list = []
    if sch.get("enabled"):
        for surf in plan["surfaces"]:
            if surf["kind"] == "road":
                _left, right_edge = _band_edges(surf["polygon"])
                pts = _sample_line(right_edge, float(sch.get("tree_line_spacing_m", 9.0)))
                d = _unit((right_edge[-1][0] - right_edge[0][0],
                           right_edge[-1][1] - right_edge[0][1]))
                off = _perp(d)
                # 外推距离至少「树冠半径 + 1.5m」，否则树冠会盖住路面
                dist = max(float(sch.get("tree_line_offset_m", 2.0)),
                           float(sch.get("tree_line_radius_m", 3.6)) + 1.5)
                tree_line = [(p[0] + off[0] * dist, p[1] + off[1] * dist) for p in pts]

    # --- 画幅：所有内容的并集 + 留白（不用世界边界当画幅，否则内容只占一条）---
    pts_all: list = []
    for surf in plan["surfaces"]:
        pts_all += [(p[0], p[1]) for p in surf["polygon"]]
    for lot in plan["lots"]:
        pts_all += [(p[0], p[1]) for p in lot["corners"]]
    for gz in plan.get("green_zones", []):
        pts_all += [(p[0], p[1]) for p in gz["polygon"]]
    for _, quad in houses:
        pts_all += quad
    for p in plan["world"]["bounds"]:
        pts_all.append((p[0], p[1]))
    for z in plan["no_place_zones"]:
        if z["shape"] == "circle":
            r = z["radius"]
            pts_all += [(z["center"][0] + r, z["center"][1] + r),
                        (z["center"][0] - r, z["center"][1] - r)]
    for p in street_trees + tree_line:
        r = float(sch.get("street_tree_radius_m", 1.8) if p in street_trees
                  else sch.get("tree_line_radius_m", 3.6))
        pts_all += [(p[0] + r, p[1] + r), (p[0] - r, p[1] - r)]

    min_x = min(p[0] for p in pts_all) - pad_m
    max_x = max(p[0] for p in pts_all) + pad_m
    min_y = min(p[1] for p in pts_all) - pad_m
    max_y = max(p[1] for p in pts_all) + pad_m

    if up_x:
        def sp(p) -> tuple[float, float]:
            return (PAD_SIDE + (p[1] - min_y) * px_per_m,
                    PAD_TOP + (max_x - p[0]) * px_per_m)
        span_w, span_h = max_y - min_y, max_x - min_x
    else:
        def sp(p) -> tuple[float, float]:
            return (PAD_SIDE + (p[0] - min_x) * px_per_m,
                    PAD_TOP + (max_y - p[1]) * px_per_m)
        span_w, span_h = max_x - min_x, max_y - min_y

    width = int(span_w * px_per_m) + PAD_SIDE * 2
    height = int(span_h * px_per_m) + PAD_TOP + PAD_BOTTOM
    canvas = Canvas(width, height, (255, 255, 255))

    def polyline(pts, color, thick=1, dash=None, closed=False):
        """把一串世界点画成屏幕折线（closed=True 时闭合）。"""
        screen = [sp(p) for p in pts]
        n = len(screen)
        last = n if closed else n - 1
        for i in range(last):
            a, b = screen[i], screen[(i + 1) % n]
            canvas.line(a[0], a[1], b[0], b[1], color, thick, dash)

    def blob(pts, fill, edge, thick=1):
        """填色多边形：先在内部铺一层横线，再描边（零依赖画布没有填色原语）。"""
        screen = [sp(p) for p in pts]
        ys = [p[1] for p in screen]
        xs = [p[0] for p in screen]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            spans = []
            for i in range(len(screen)):
                a, b = screen[i], screen[(i + 1) % len(screen)]
                if (a[1] > y) != (b[1] > y):
                    spans.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
            spans.sort()
            for k in range(0, len(spans) - 1, 2):
                canvas.line(spans[k], y, spans[k + 1], y, fill, 1)
        polyline(pts, edge, thick, closed=True)

    # 网格（按世界坐标每 grid_step 一条）
    gx = math.ceil(min_x / grid_step) * grid_step
    while gx <= max_x:
        polyline([(gx, min_y), (gx, max_y)], C_GRID, 1)
        gx += grid_step
    gy = math.ceil(min_y / grid_step) * grid_step
    while gy <= max_y:
        polyline([(min_x, gy), (max_x, gy)], C_GRID, 1)
        gy += grid_step

    # 世界边界
    polyline([(p[0], p[1]) for p in plan["world"]["bounds"]], C_BOUNDS, 2,
             dash=(8, 6), closed=True)

    # 公共区域：绿化区（填淡绿，客户一眼看出哪块是绿地）
    for gz in plan.get("green_zones", []):
        blob([(p[0], p[1]) for p in gz["polygon"]], (230, 243, 221), C_GREEN, 1)

    # 骨架面（路面填浅灰：客户要一眼看出哪条是街；人行道留白、绿化带绿虚线）
    for surf in plan["surfaces"]:
        pts = [(p[0], p[1]) for p in surf["polygon"]]
        if surf["kind"] == "green_strip":
            polyline(pts, C_GREEN, 2, dash=(6, 5), closed=True)
        elif surf["kind"] == "sidewalk":
            polyline(pts, C_WALK, 2, closed=True)
        else:
            blob(pts, (208, 208, 208), C_ROAD, 3)

    # 道路中心线
    polyline([(p[0], p[1]) for p in plan["roads"][0]["centerline"]], C_RED, 1,
             dash=(16, 10))

    # 示意：背景树线 + 行道树（先画，压在下面）
    if sch.get("enabled"):
        for p in tree_line:
            r = float(sch.get("tree_line_radius_m", 3.6))
            circle = [(p[0] + r * math.cos(a / 16.0 * 2 * math.pi),
                       p[1] + r * math.sin(a / 16.0 * 2 * math.pi)) for a in range(16)]
            blob(circle, C_TREE, C_TREE_LINE, 1)
        for p in street_trees:
            r = float(sch.get("street_tree_radius_m", 1.8))
            circle = [(p[0] + r * math.cos(a / 16.0 * 2 * math.pi),
                       p[1] + r * math.sin(a / 16.0 * 2 * math.pi)) for a in range(16)]
            blob(circle, C_TREE, C_TREE_LINE, 1)

    # 地块轮廓 + 临街面加粗 + 短朝向线（只画进地块 3 米，不穿房子）
    for lot in plan["lots"]:
        pts = [(p[0], p[1]) for p in lot["corners"]]
        polyline(pts, C_LOT, 1, closed=True)
        polyline([pts[0], pts[1]], C_LOT, 5)          # 临街那条边加粗 = 朝向标志
        fm = sp(((pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0))
        bm = sp(((pts[2][0] + pts[3][0]) / 2.0, (pts[2][1] + pts[3][1]) / 2.0))
        seg = math.hypot(bm[0] - fm[0], bm[1] - fm[1])
        if seg > 0:
            k = min(1.0, 3.0 * px_per_m / seg)
            canvas.line(fm[0], fm[1], fm[0] + (bm[0] - fm[0]) * k,
                        fm[1] + (bm[1] - fm[1]) * k, C_LOT, 3)

    # 示意：房屋占位块（画在地块之上）
    for lot_id, quad in houses:
        blob(quad, C_HOUSE, C_HOUSE_LINE, 2)
        cx, cy = sp(((quad[0][0] + quad[2][0]) / 2.0, (quad[0][1] + quad[2][1]) / 2.0))
        canvas.text(cx - 26, cy - 7, lot_id, C_HOUSE_LINE, 2)

    # 禁放区
    for z in plan["no_place_zones"]:
        if z["shape"] == "circle":
            c = sp((z["center"][0], z["center"][1]))
            canvas.circle(c[0], c[1], z["radius"] * px_per_m, C_ZONE, 2)

    # 场景中心十字
    cc = sp((plan["world"]["center"][0], plan["world"]["center"][1]))
    canvas.line(cc[0] - 12, cc[1], cc[0] + 12, cc[1], C_RED, 2)
    canvas.line(cc[0], cc[1] - 12, cc[0], cc[1] + 12, C_RED, 2)

    # 轴向指示（画在图幅右上角，别压到标题文字）
    ax, ay = PAD_SIDE + 26, PAD_TOP + 96
    canvas.line(ax, ay, ax + 34, ay, C_INK, 2)
    canvas.line(ax, ay, ax, ay - 34, C_INK, 2)
    canvas.text(ax + 40, ay - 7, "+Y" if up_x else "+X", C_INK, 2)
    canvas.text(ax - 6, ay - 50, "+X" if up_x else "+Y", C_INK, 2)

    # 标题
    canvas.text(PAD_SIDE, 16, "SITE PLAN - ROAD AND LOT LAYOUT", C_INK, 3)
    canvas.text(PAD_SIDE, 50,
                "SCHEMATIC HOUSES/TREES FOR CLIENT REVIEW - NOT FINAL PLACEMENT",
                C_ZONE, 2)
    canvas.text(PAD_SIDE, 66,
                f"UNITS: METERS   GRID {grid_step:.0f} M   "
                f"{'X UP / Y RIGHT' if up_x else 'X RIGHT / Y UP'}",
                C_NOTE, 2)

    # 图例（排两行：7 项排一行会挤出画幅）
    legend_items = ((C_ROAD, "ROAD"), (C_WALK, "SIDEWALK"), (C_GREEN, "GREEN STRIP"),
                    (C_LOT, "LOT"), (C_HOUSE, "HOUSE"), (C_TREE, "TREE"),
                    (C_ZONE, "NO-PLACE"))
    for row, start in enumerate((0, 4)):
        legend_y = height - 142 + row * 24
        legend_x = PAD_SIDE
        for color, label in legend_items[start:start + 4]:
            canvas.line(legend_x, legend_y, legend_x + 26, legend_y, color, 4)
            canvas.text(legend_x + 34, legend_y - 7, label, C_INK, 2)
            legend_x += 34 + canvas.text_width(label, 2) + 22

    # 比例尺
    bar_y = height - 74
    bar_x1 = PAD_SIDE + bar_m * px_per_m
    canvas.line(PAD_SIDE, bar_y, bar_x1, bar_y, C_INK, 2)
    canvas.line(PAD_SIDE, bar_y - 6, PAD_SIDE, bar_y + 6, C_INK, 2)
    canvas.line(bar_x1, bar_y - 6, bar_x1, bar_y + 6, C_INK, 2)
    canvas.text(bar_x1 + 12, bar_y - 7, f"{bar_m:.0f} M", C_INK, 2)
    canvas.text(PAD_SIDE + bar_m * px_per_m + 90, bar_y - 7,
                f"SITE CENTER {plan['world']['center'][0]:.0f} "
                f"{plan['world']['center'][1]:.0f} M", C_RED, 2)

    # 免责（红字）：写进图里，免得被当成计算输入
    canvas.text(PAD_SIDE, height - 32,
                "PREVIEW ONLY - COMPUTATION READS plan_v1.json NOT THIS IMAGE",
                C_ZONE, 2)

    canvas.write_png(path)
    return {
        "image": str(path),
        "size_px": [width, height],
        "houses_schematic": len(houses),
        "street_trees_schematic": len(street_trees),
        "tree_line_schematic": len(tree_line),
        "up_axis": "X" if up_x else "Y",
    }
