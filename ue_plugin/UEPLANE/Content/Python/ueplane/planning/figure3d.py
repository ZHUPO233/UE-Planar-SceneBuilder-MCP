#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 阶段二 · 出图（**数据层**）：plan 数据 → 一张自足的 3D 页 HTML
#
# 【它替掉的是什么】原先阶段二的"图"是**两张手绘的矢量图、由 agent 画**
#   （前一版放在 `views/` 下的两个固定前缀文件里）。
#   2026-10-11 用户原话：「整改2阶段，不出图，出three.js」+「而且段我想彻底改为用three.js了」
#   ⇒ 改成**一张能转、能缩、能点的 three.js 页**，而且**由代码在落盘那一刻生成**。
#
# 【为什么"落盘时自动生成"是最好的解】手画那套的失败模式是「图与数据不一致」
#   （数据改了、图还是上一版；或图画好了、指纹忘了写）。生成收在 `write_plan()` 里
#   （全工程唯一写入点）之后，**这个失败模式在结构上消失** —— 不存在"数据是新的、图是旧的"。
#
# 【本文件只做三件事】
#   ① 读 plan（+ 资产清单，用来把资产行的实测包围盒换算成高度）→ 一行一个方块的数据；
#   ② 把数据注入模板槽位 `/*__DATA__*/` → 写出 HTML；
#   ③ **当场自检**：写出来的 HTML 里必须真的有「当前几何指纹前 10 位」+「该覆盖的每一行 label」；
#      不满足就**不写文件**、把问题原样报回去。
#
# 【判据不在这里另写一套】**硬要求**：指纹与"这一版要覆盖哪些行"都从
#   **闸门自己那个模块**（`mcp_server.planning.plan`）取 ——
#   `plan_geometry_hash(plan)` / `change_set(plan)["required_labels"]`。
#   两处各写一遍判据 = 迟早两处说不同的话（本项目最忌讳的就是这个）。
#   ⇒ 所以本模块 `import` 的是那个模块，而不是自己再实现一遍覆盖率检查。
#
# ⚠ **一个真实踩到的坑**：`json.dumps` 默认 `ensure_ascii=True`，中文会被写成 `\uXXXX`。
#   而闸门的 label 判据是**在文件正文里找那串中文**（`figure_label_coverage()`）——
#   转义之后**一个 label 都找不到**：页面明明画对了，验收时也会被判"漏了 78 行"。
#   所以 `render_html()` 里**必须** `ensure_ascii=False`。
#
# 【与本文件同级的 `web/` 目录】模板与 three.js 库都在那儿：
#   · 仓库里 = `src/mcp_server/planning/web/`；
#   · 插件里 = `Content/Python/ueplane/planning/web/`（由 `ue_plugin/build_plugin.ps1` 同步）；
#   运行时按**本文件自己的位置**找 `web/` ⇒ 两边都成立，不用各拼一套路径。
# =============================================================================
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

SLOT = "/*__DATA__*/"
"""模板里的数据槽位 —— 模板与生成器之间的**唯一契约**（注入后正文里不许再出现它）。"""

# ⚠⚠ **模板路径必须是"函数、不能是模块级 Path 常量"**（2026-10-11 实测踩到，别改回去）：
#   插件入口 `init_unreal.py` 的 `_relocate()` 会把**模块里所有在 `REPO_ROOT` 底下的 Path 常量**
#   整体搬到 `<工程>/Saved/UEPLANE/`（它连 `plan.py` 的 `REPO_ROOT` 一起搬）。
#   实测（本机模拟插件加载 + 真跑 write_plan）：
#     `DEFAULT_SRC` / `DEFAULT_TEMPLATE` / `WEB_DIR` **三个都被搬飞** ⇒ 模板指向
#     `…/Saved/UEPLANE/Python/ueplane/planning/web/preview3d.html`（**那文件不存在**）
#     ⇒ 出图静默降级成"模板不存在、没出页"。
#   ⚠ 上面路径写**正斜杠**不是笔误：反斜杠紧跟 `U`（例如 `Saved` 后面跟 `UEPLANE` 那种写法）
#     在非 raw 字符串里是 **Unicode 转义** ⇒ 直接 `SyntaxError`（2026-10-11 本仓库真的躺过一次）。
#   `_relocate()` 只扫**模块属性里的 Path 值** ⇒ 把路径算在**函数体内**（`Path(__file__)`
#   每次调用现算）就搬不动了。**这是"新增路径"唯一的稳形态。**
#   ⚠ `DEFAULT_SRC` 同样是"看着无害"的那种：它只服务 CLI（`import mcp_server.planning.plan`），
#     插件里不用；但既然它也会被搬，改成函数更省心（见 `_default_src()`）。

_WEB_DIRNAME = "web"
_TEMPLATE_NAME = "preview3d.html"


def web_dir() -> Path:
    """模板与渲染层（`three.min.js`）所在目录 = **本文件同级的 `web/`**。

    ⚠ 每次调用现算（见文件头那段实测说明）：仓库里 = `src/mcp_server/planning/web/`，
    插件里 = `Content/Python/ueplane/planning/web/` —— 同一份代码两处都成立，
    而且**不会被 `init_unreal.py` 的 `_relocate()` 搬走**。
    """
    return Path(__file__).resolve().parent / _WEB_DIRNAME


def default_template() -> Path:
    """阶段二那张页的模板 = `web_dir()/preview3d.html`。**出图时现算，别缓存成常量。**"""
    return web_dir() / _TEMPLATE_NAME


def _default_src() -> Path:
    """CLI 直接跑时用来把 `src/` 放进 `sys.path`（插件里不用这条路）。"""
    return Path(__file__).resolve().parents[2]

# 模板里引用的**同级**文件（如 `three.min.js`）——要跟着图一起搬到输出目录。
# 为什么必须搬：产品里图落在 `Saved/UEPLANE/views/`、库留在插件里，
#   不搬过去页面一打开就是 `THREE is not defined`（一个只有人眼能发现的空白页）。
# 绝对 URL / 根路径 / data: 都不算同级文件，跳过。
_SIBLING = re.compile(r'<script[^>]+src="([^"]+)"', re.I)


# -----------------------------------------------------------------------------
# 数据层
# -----------------------------------------------------------------------------
def _num(v, dflt: float) -> float:
    """JSON 里可能是 null / 缺字段 —— 给个兜底，别在出图这一步抛异常。"""
    if v is None:
        return float(dflt)
    return float(v)


def build_rows(plan: dict, asset_list: dict) -> list[dict]:
    """一行一个方块。资产行的 h 用**实测包围盒**（资产清单的 `size_cm`）算，白膜行用 `height_m`。

    Z 口径与阶段三**同源**（这条口径与上一套生成器逐字相同）：
      · `z_m` = 该行**底面**绝对标高（米）—— 不是中心、不是原点；
      · 资产 h = 包围盒 Z(厘米)/100 × `scale` × `scale_z`；白膜 h = `height_m`。
    ⚠ 资产清单里**查不到**那一行时 h = 0（画出来是个薄片）—— **不猜尺寸**：
      清单缺项该由阶段一报，这里只是不让出图这一步崩掉。页面上照样有它这一行。
    """
    bbox: dict[str, list] = {}
    for it in (asset_list or {}).get("items", []) or []:
        ap, sz = it.get("asset_path"), it.get("size_cm")
        if ap and sz:
            bbox[str(ap)] = sz

    rows: list[dict] = []
    for a in plan.get("assets") or []:
        scale = _num(a.get("scale"), 1.0)
        scale_z = _num(a.get("scale_z"), 1.0) if a.get("scale_z") else 1.0
        h = 0.0
        sz = bbox.get(str(a.get("asset_path")))
        if sz:
            h = float(sz[2]) / 100.0 * scale * scale_z
        fp = a.get("footprint_m") or [0.1, 0.1]
        pos = a.get("pos") or [0.0, 0.0]
        rows.append({
            "label": str(a.get("label") or ""), "key": str(a.get("element_key") or ""),
            "kind": "asset",
            "x": _num(pos[0], 0), "y": _num(pos[1], 0),
            "w": _num(fp[0], 0.1), "d": _num(fp[1], 0.1),
            "zb": _num(a.get("z_m"), 0), "h": round(h, 3),
            # 2026-10-17 加：页面「点选元素 → 详情」要显示的字段。
            #   全是**已有字段的原样搬运**，不新造判据；缺省给安全值（老 plan 没有 rot/scale 也能画）。
            "rot": _num(a.get("rot_deg"), 0), "scale": round(scale, 4),
            "path": str(a.get("asset_path") or ""),
            "note": str(a.get("note") or ""),
            "src": "资产实测包围盒 × scale",
        })
    for b in plan.get("whiteboxes") or []:
        fp = b.get("footprint_m") or [0.1, 0.1]
        pos = b.get("pos") or [0.0, 0.0]
        rows.append({
            "label": str(b.get("label") or ""), "key": str(b.get("element_key") or ""),
            "kind": "whitebox",
            "x": _num(pos[0], 0), "y": _num(pos[1], 0),
            "w": _num(fp[0], 0.1), "d": _num(fp[1], 0.1),
            "zb": _num(b.get("z_m"), 0), "h": _num(b.get("height_m"), 0.1),
            "rot": _num(b.get("rot_deg"), 0), "scale": 1.0,
            "path": "", "shape": str(b.get("shape") or ""),
            "note": str(b.get("note") or ""),
            "src": str(b.get("size_source") or ""),
        })
    return rows


def build_meta(plan: dict, rows: list[dict], fp_full: str, plan_name: str) -> dict:
    """页头那几行字要的数据（世界大小 / 行数 / 指纹 / 生成时间）。"""
    world = plan.get("world") or {}
    size = world.get("size") or [0, 0]
    center = world.get("center") or [0, 0]
    return {
        "fp_full": fp_full,
        "fp_short": fp_full[:10],
        "world": [_num(size[0], 0), _num(size[1], 0)],
        "center": [_num(center[0], 0), _num(center[1], 0)],
        "assets": len(plan.get("assets") or []),
        "whiteboxes": len(plan.get("whiteboxes") or []),
        "rows": len(rows),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "plan_file": plan_name,
    }


def render_html(plan: dict, asset_list: dict, template_text: str, fp_full: str,
                plan_name: str = "plan_v1.json") -> str:
    """把数据注入模板槽位。⚠ `ensure_ascii=False` 是硬要求（理由见文件头）。

    ⚠ 模板没有槽位时抛的是 **`ValueError`、不是 `SystemExit`**（2026-10-11 实测抓到）：
      以前抛 `SystemExit`，而 `SystemExit` **不是 `Exception` 的子类** ⇒
      `render_page()` 里那圈 `except Exception` 拦不住它 ⇒ 会**一路穿到 `write_plan()`**，
      把落盘那一整次调用带崩 —— 那正好违反"出图失败**不拦落盘**"这条定案。
      现在抛 `ValueError`：`render_page()` 接得住，转成 `{ok: False, errors: [...]}`。
    """
    if SLOT not in template_text:
        raise ValueError(f"模板里没有槽位 {SLOT} —— 没法注入")
    rows = build_rows(plan, asset_list)
    payload = {"meta": build_meta(plan, rows, fp_full, plan_name), "rows": rows}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return template_text.replace(SLOT, data)


# -----------------------------------------------------------------------------
# 自检 —— 用**闸门自己的判据**，不另立一套
# -----------------------------------------------------------------------------
def self_check(html: str, fp_head: str, required_labels: list[str]) -> list[str]:
    """返回问题清单（空 = 通过）。**只有空了才允许落盘。**

    三条判据（与 `plan.figure_verdicts()` 认的那两条**同一套**）：
      ① 槽位必须被替换掉（正文里不许还留着 `/*__DATA__*/`）；
      ② 正文里必须有当前几何指纹前 10 位；
      ③ 该覆盖的每一行 `label` 都必须在正文里。
    ⚠ 第 ③ 条对中文 label 有效的前提是注入时没把中文转义成 `\\uXXXX`（见文件头）。
    """
    problems: list[str] = []
    if SLOT in html:
        problems.append(f"槽位没被替换 —— 正文里还留着 {SLOT}")
    if fp_head and fp_head not in html:
        problems.append(f"正文里找不到当前几何指纹前 10 位「{fp_head}」")
    missing = [lab for lab in required_labels if lab and lab not in html]
    if missing:
        head = "、".join(missing[:6])
        tail = "" if len(missing) <= 6 else f" 等 {len(missing)} 行"
        problems.append(f"漏了 {len(missing)}/{len(required_labels)} 行的 label：{head}{tail}")
    return problems


# -----------------------------------------------------------------------------
# 出口
# -----------------------------------------------------------------------------
def _is_sibling(ref: str) -> bool:
    """同级文件 = 相对路径。`https://…` / `/abs` / `data:` / `//cdn` 都不算。"""
    return not (ref.startswith(("/", "//", "data:", "http:", "https:")) or "://" in ref)


def copy_siblings(template_path: Path, out_path: Path, template_text: str) -> list[str]:
    """把模板引用的同级文件复制到输出目录（同一路径就不重复复制）。返回复制了哪些。"""
    tdir, odir = Path(template_path).parent, Path(out_path).parent
    copied: list[str] = []
    for m in _SIBLING.finditer(template_text):
        ref = m.group(1)
        if not _is_sibling(ref):
            continue
        src = tdir / ref
        if not src.is_file():
            continue
        dst = odir / Path(ref).name
        try:
            if src.resolve() != dst.resolve():
                shutil.copyfile(src, dst)
            copied.append(dst.name)
        except OSError:
            pass
    return copied


def render_page(plan: dict, plan_path: Path, asset_path: Path, out_path: Path,
                planning, template_path: Path | None = None) -> dict:
    """读数据 → 出图 → 自检 → 落盘。**自检不过抛 `SystemExit`、一个字节都不写。**

    `planning` = 闸门那个模块（`mcp_server.planning.plan`）—— 由调用方传进来，
    这样本模块**不自己 import 包**（插件里与仓库里都能用，不必猜 sys.path）。
    返回 `{out, path, bytes, rows, fp_short, required_labels, siblings, errors, ok}` ——
    ⚠ `ok=False` 时 `errors` 里有原因，`out` 为空串（**没有写任何文件**）。
    """
    plan_path, out_path = Path(plan_path), Path(out_path)
    template_path = Path(template_path) if template_path else default_template()
    plan_name = plan_path.name          # ⚠ 先取出名字：下面 `plan_name=` 传的是**字符串**
    fp_full = planning.plan_geometry_hash(plan)
    req = [str(x) for x in (planning.change_set(plan).get("required_labels") or [])]

    # 读模板 / 资产清单：这两个"读不到"**不该**抛出去把落盘带崩 ——
    #   调用方（write_plan）要的是"plan 已经安全落盘 + 一条如实的报告"。
    if not template_path.is_file():
        return {"out": "", "path": str(out_path), "bytes": 0, "rows": 0,
                "fp_short": fp_full[:10], "required_labels": len(req), "siblings": [],
                "ok": False, "errors": [f"模板不存在：{template_path}"]}
    try:
        template_text = template_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return {"out": "", "path": str(out_path), "bytes": 0, "rows": 0,
                "fp_short": fp_full[:10], "required_labels": len(req), "siblings": [],
                "ok": False, "errors": [f"模板读不动：{exc}"]}

    asset_list: dict = {}
    try:
        asset_list = json.loads(Path(asset_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # 没有资产清单**不是错误**：资产行的高度会算成 0（画出来是薄片），页面上照样有这一行。
        # 宁可画薄一点，也不让整个阶段二因为阶段一那份清单缺失就出不了图。
        asset_list = {}

    try:
        html = render_html(plan, asset_list, template_text, fp_full, plan_name=plan_name)
    except (ValueError, TypeError, KeyError) as exc:
        # 槽位缺失 / 数据形状不对 —— 收成一条报告，**不往外抛**（落盘不该被出图带崩）。
        return {"out": "", "path": str(out_path), "bytes": 0,
                "rows": len(build_rows(plan, asset_list)),
                "fp_short": fp_full[:10], "required_labels": len(req), "siblings": [],
                "ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    problems = self_check(html, fp_full[:10], req)
    if problems:
        return {"out": "", "path": str(out_path), "bytes": 0,
                "rows": len(build_rows(plan, asset_list)),
                "fp_short": fp_full[:10], "required_labels": len(req), "siblings": [],
                "ok": False, "errors": problems}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8", newline="\n")
    copied = copy_siblings(template_path, out_path, template_text)
    return {"out": str(out_path), "path": str(out_path), "bytes": len(html.encode("utf-8")),
            "rows": len(build_rows(plan, asset_list)), "fp_short": fp_full[:10],
            "required_labels": len(req), "siblings": copied,
            "ok": True, "errors": [],
            "scope": "局部一轮" if planning.change_set(plan).get("local") else "全量一轮"}


# -----------------------------------------------------------------------------
# CLI —— 手动重出这一页（真流程里由 `write_plan()` 自动调，平时不用跑）
# -----------------------------------------------------------------------------
def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="阶段二出图（数据层）：plan → three.js 页")
    ap.add_argument("--plan", default="", help="plan JSON（留空 = 走活动那份 plan）")
    ap.add_argument("--assets", default="", help="资产清单 JSON（留空 = 活动 plan 同级 catalog/）")
    ap.add_argument("--template", default=str(default_template()))
    ap.add_argument("--out", default="", help="输出 HTML（留空 = 与 plan 同级的 <plan>_view.html）")
    ap.add_argument("--src", default=str(_default_src()))
    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.src).resolve()))
    from mcp_server.planning import plan as planning          # 闸门自己那个模块

    plan_path = Path(args.plan) if args.plan else planning.active_plan_path()
    if not plan_path.is_file():
        print(f"读不到 plan：{plan_path}")
        return 1
    if args.assets:
        asset_path = Path(args.assets)
    else:
        asset_path = planning.active_asset_list_path()   # 走咽喉：纪元 2 = asset_list_v2.json
    out_path = (Path(args.out) if args.out
                else plan_path.parent / f"{plan_path.stem}_view.html")

    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    info = render_page(plan, plan_path, asset_path, out_path, planning,
                       template_path=Path(args.template))
    print(f"ok              = {info['ok']}")
    print(f"rows            = {info['rows']}")
    print(f"fingerprint     = {info['fp_short']}...")
    print(f"required labels = {info['required_labels']}")
    print(f"siblings copied = {info['siblings']}")
    print(f"wrote           = {info['out'] or '（没写：自检不过）'}  ({info['bytes']} bytes)")
    for p in info["errors"]:
        print(f"  - {p}")
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
