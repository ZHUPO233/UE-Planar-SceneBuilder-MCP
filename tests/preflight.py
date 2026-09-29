# =============================================================================
# 重启前预检：验「能不能起来 / 规划层能不能导入 / 工具注册对不对 / 阶段二链通不通」
# —— **不启动 server**。
#
# 【为什么要有这个文件】（2026-09-23 两次真实事故，每次白花十几分钟重连）
#   ① `NameError: name 'FoundAsset' is not defined`
#      —— Python 里函数**签名上的注解**是在 def 执行那一刻求值的，定义顺序摆错就炸。
#   ② `ImportError: attempted relative import with no known parent package`
#      —— 宿主是按**脚本方式**起 main.py 的（`python ...\src\mcp_server\main.py`），
#         此时 `__package__` 为空，文件顶部的相对导入 `from .planning import ...` 必然失败。
#   ⚠ ② 之所以漏网，是因为当时的预检用 `import mcp_server.main`（**模块方式**）跑，
#     **没有复现宿主的启动方式**，于是给了个假绿。所以本脚本第一条就是复现它。
#
# 【怎么跑】
#   & "<仓库根>\.venv\Scripts\python.exe" "<仓库根>\tests\preflight.py"
#   退出码 0 = 全绿 → 可以重启；非 0 = **别重启**，先修。
#
# ⚠ **必须用上面那条 venv 的解释器，别用 PATH 上的 `python`。**
#   2026-09-26 实测踩到：PATH 上第一个 `python` 是**微软商店的"应用执行别名"空壳**
#   （`C:\Users\<用户>\AppData\Local\Microsoft\WindowsApps\python.exe`）——
#   它**静默退出、一个字都不打印**。现场表现：`python tests/preflight.py` 敲下去
#   立刻回到提示符，什么都没有，看着像"跑完了"。
#   → **空输出 ≠ 通过。** 判据只有两个：**退出码**（`echo "exit=$LASTEXITCODE"`）
#     加那几行 `[通过]` / `[失败]`。
#   → 怀疑解释器不对时，先跑 `python -c "print('hello')"`：
#     连 hello 都不打印，就说明那个 `python` 根本没在执行你的文件。
# =============================================================================

import asyncio
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "src" / "mcp_server" / "main.py"

# 当前应有的工具（改动工具面时**必须同步这里和 tests/check_tools.py**）
# ⚠ 2026-09-25：新增 `request_plan_change`（阶段二验收：记「用户要改什么」）→ 9 → 10 个。
# ⚠ 2026-09-26：新增 `generate_build_orders` + `execute_build`
#   （阶段三第 2 步：放置表 → 搭建指令表 → 批量落关卡）→ 10 → **12** 个；
#   同一批还加了 `check_build_target`（阶段三第 1 步的只读检查）→ 那一批 **13** 个。
# ⚠ 2026-09-27：曾短暂加过 `generate_layout`（阶段四 · 布局序列化），**当天按用户指令整块回退** ——
#   理由：阶段一资产表 + 阶段二位置表已是全局权威，那份只是派生件，先不做。
# ⚠ 2026-09-27（晚）：新增 `apply_surfaces`（**阶段五 · 第 1 步**：给白膜贴表面材质）
#   —— 13 → **14** 个。机制是实测出来的（组件级 `overrideMaterials`），见 main.py 那段说明。
# ⚠ 2026-09-27（晚二）：第四阶段**按用户指令不跳了**，实现成**导出/交换**（消费者明确了：
#   要在 Blender / UE 之间搬同一套布局）→ 新增 `export_layout`（导出）+ `check_exchange`
#   （回读对账，只出报告、不改 plan）—— 14 → **16** 个。
#   ⚠ `tests/check_tools.py` 里那份名单必须与这里逐字一致（本次已同步）。
# ⚠ 2026-09-29：阶段六**按用户指令扩范围**（不只有灯光 —— 天空/大气/天光/雾/云/后处理/时段
#   全都要）→ 新增 `setup_environment`（配整套环境：读现值台账 → 幂等 → 写 → 读回核对 →
#   可回滚、绝不存盘）+ `capture_preview`（按世界坐标摆机位出图，PNG 落 views/preview/，
#   **不动用户的视口相机**）—— 16 → **18** 个。
#   ⚠ 阶段六**最终定名「环境搭建」**（当天用户又收回了"相机预览"）：出图归**阶段七** ——
#     `capture_preview` 仍在工具面里（代码保留、归阶段七用），所以这里**仍是 18 个**。
EXPECTED_TOOLS = [
    "apply_surfaces", "capture_preview",
    "check_build_target", "check_exchange", "confirm_assets", "confirm_elements",
    "confirm_plan", "execute_build", "export_layout",
    "generate_build_orders", "generate_plan",
    "get_asset_list", "get_plan",
    "official_status", "plan_assets", "rename_assets",
    "request_plan_change", "setup_environment",
]

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """打印一条结果。⚠ 细节只在**失败**时打印 —— 那些文案是"失败原因"，
    挂在绿行上会自相矛盾（实测踩过：`[通过] … PNG 没生成`）。"""
    line = f"[{'通过' if ok else '失败'}] {name}"
    if not ok and detail:
        line += f"   {detail}"
    print(line)
    if not ok:
        FAILED.append(name)


def main() -> int:
    # --- ① 按**脚本方式**执行 main.py：复现宿主的启动方式，但不会启动 server ---
    # run_name 不是 "__main__"，所以文件末尾那句 `if __name__ == "__main__": mcp.run()` 不触发。
    # ⚠ 忠实复现：`python ...\main.py` 时 CPython 会把**脚本所在目录**放进 sys.path[0]，
    #   而 runpy.run_path **不会**。所以这里手动补上，否则预检会比真实启动更严（假红）。
    script_dir = str(MAIN_PY.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        mod = runpy.run_path(str(MAIN_PY), run_name="not_main")
        check("① main.py 能按【脚本方式】执行（宿主就是这么起它的）", True)
    except BaseException as exc:                      # BaseException：连 CancelledError 也接住
        check("① main.py 能按【脚本方式】执行（宿主就是这么起它的）", False,
              f"{type(exc).__name__}: {exc}")
        print("\n**别重启**：先把这个错修掉 —— 它会让整个 server 起不来。")
        return 1

    # --- ② 把阶段二的**惰性导入**也走一遍（上次就是这条路径没被走到） ---
    try:
        planning = mod["_planning_modules"]()
        check("② 规划层惰性导入成功", True)
        print(f"        plan = {planning.__name__}")
    except BaseException as exc:
        check("② 规划层惰性导入成功", False, f"{type(exc).__name__}: {exc}")
        print("\n**别重启**：阶段二工具会报错（阶段一还能用，但不该带病上）。")
        return 1

    # --- ③ 工具注册：个数和名字都要对 ---
    result = mod["mcp"].list_tools()
    if hasattr(result, "__await__"):                  # 这个 SDK 有时返回协程
        result = asyncio.run(result)
    names = sorted(t.name for t in getattr(result, "tools", result))
    check("③ 注册的工具与预期一致", names == EXPECTED_TOOLS, f"实际={names}")
    print(f"        {names}")

    # --- ④ 阶段二那条链：装配 + 形状校验（全在内存里算，不碰 views/）---
    # ⚠ 2026-09-25 **按用户指令大改**：阶段二原来有 11 项具名自检（含"白膜不压已有资产"
    #   "占地 = 阶段一实测包围盒 × scale"等），连同 `build_checks()` 已整段删除
    #   （用户：「阶段二不要这个自检阶段了，后面阶段再做自检」；碰撞检测与布局修正
    #    **已整段删除**：2026-09-27 用户指令，不再单列阶段）。
    #   所以本段**不再验"自检该报红的会不会报红"**（那些项已经不存在了），改成验**还活着的契约**：
    #     ① 装配链跑得通，且数据里**不再有 `checks`**（防止自检被悄悄加回来而没人发现）；
    #     ② 两条**拒收**仍然有效 —— 它们是**形状校验**、不属自检，删自检时不许连它们一起删：
    #        · 白膜行带了 `asset_path`（自相矛盾）
    #        · 白膜的 `size_source` 没写明是「预估」（预估值伪装成实测值）
    #        · 资产行没给 `asset_path`
    #     ③ 空表（初始化态）依然**允许**，不许抛错。
    try:
        world = {
            "center": [0.0, 0.0], "size": [100.0, 40.0], "size_source": "预检",
            "bounds": {"min": [-50.0, -20.0], "max": [50.0, 20.0]},
            "coordinate_system": {"handedness": "left"},
        }
        ok_assets = [{"element_key": "building_fake", "label": "假楼",
                      "asset_path": "/Game/Fake", "pos": [0.0, 0.0],
                      "footprint_m": [10.0, 8.0], "rot_deg": 0.0, "scale": 1.0, "note": ""}]
        ok_boxes = [{"element_key": "road_fake", "label": "假路", "shape": "plane",
                     "pos": [0.0, -15.0], "footprint_m": [90.0, 6.0], "rot_deg": 0.0,
                     "size_source": "预估（预检）", "note": ""}]
        plan = planning.build_plan(ok_assets, ok_boxes, world)
        check("④ 阶段二装配链跑通，且数据里不再有 `checks`（阶段二已不自检）",
              ("checks" not in plan)
              and len(plan.get("assets", [])) == 1 and len(plan.get("whiteboxes", [])) == 1,
              f"plan 顶层键={sorted(plan)}")

        # ④b 拒收：白膜行带了 asset_path（自相矛盾 —— 白膜就是"没有资产"）
        try:
            planning.normalize_whitebox(dict(ok_boxes[0], asset_path="/Game/Fake"))
            ok_b, why_b = False, "没拒"
        except ValueError as exc:
            ok_b, why_b = True, str(exc)[:70]
        check("④b 白膜行带 asset_path 时被拒收（形状校验还活着）", ok_b, why_b)

        # ④c 拒收：白膜尺寸来源没写明"预估"（预估值不许伪装成实测值）
        try:
            planning.normalize_whitebox(dict(ok_boxes[0], size_source="实测"))
            ok_c, why_c = False, "没拒"
        except ValueError as exc:
            ok_c, why_c = True, str(exc)[:70]
        check("④c 白膜尺寸来源不是「预估」时被拒收", ok_c, why_c)

        # ④d 拒收：资产行没给 asset_path
        try:
            planning.normalize_asset(dict(ok_assets[0], asset_path=""))
            ok_d, why_d = False, "没拒"
        except ValueError as exc:
            ok_d, why_d = True, str(exc)[:70]
        check("④d 资产行没给 asset_path 时被拒收", ok_d, why_d)

        # ④e 初始化态：两张表都空是**允许**的（"先立结构、内容是空的"），不许抛错
        try:
            plan_empty = planning.build_plan([], [], world)
            ok_e, why_e = (not plan_empty.get("assets")
                           and not plan_empty.get("whiteboxes")), ""
        except BaseException as exc:                  # noqa: BLE001
            ok_e, why_e = False, f"{type(exc).__name__}: {exc}"
        check("④e 两张表都空（初始化态）依然允许、不抛错", ok_e, why_e)

        # --- ⑤ 闸门指纹自洽：刚算出来的数据，不该被判成"被手改过" ---
        # ⚠ 这是 2026-09-23 经 MCP 实测抓到的 bug 的回归检查：
        #   内存对象带 -0.0、落盘的是净化后的 0.0，两边哈希就会不等，
        #   于是对着刚算出的数据喊"文件被改过"。指纹计算必须内部净化。
        gate = planning.gate_check(plan)
        check("⑤ 闸门指纹自洽（刚算出的数据不该被判成'被改过'）",
              bool(gate["plan_hash_ok"]), f"gate={gate}")
    except BaseException as exc:
        check("④ / ⑤ 阶段二装配、形状校验与闸门指纹能跑通", False,
              f"{type(exc).__name__}: {exc}")

    print()
    if FAILED:
        print(f"预检没过（{len(FAILED)} 项）：{FAILED}")
        print("**别重启**：先修。")
        return 1
    print("预检全绿：可以重启。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
