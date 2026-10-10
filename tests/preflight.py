# =============================================================================
# 重启前预检：验「能不能起来 / 规划层能不能导入 / 工具注册对不对 / 阶段二链通不通」
# —— **不启动 server**。
#
# 【为什么要有这个文件】（2026-09-23 两次真实事故，每次白花十几分钟重连）
#   ① `NameError: name 'FoundAsset' is not defined`
#      —— Python 里函数**签名上的注解**是在 def 执行那一刻求值的，定义顺序摆错就炸。
#   ② `ImportError: attempted relative import with no known parent package`
#      —— Agent是按**脚本方式**起 main.py 的（`python ...\src\mcp_server\main.py`），
#         此时 `__package__` 为空，文件顶部的相对导入 `from .planning import ...` 必然失败。
#   ⚠ ② 之所以漏网，是因为当时的预检用 `import mcp_server.main`（**模块方式**）跑，
#     **没有复现Agent的启动方式**，于是给了个假绿。所以本脚本第一条就是复现它。
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
import importlib
import runpy
import sys
from pathlib import Path
from typing import Any          # ⚠ ⑫n 的桩要用（2026-10-04 晚：漏了这行 ⇒ NameError，预检当场红）

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
#   ⚠ **2026-10-04：阶段数从七个改成八个** —— 插入「**四 · 微调**」（**不新造工具**：复用
#   `request_plan_change` / `generate_plan(patch)` / `execute_build(only_labels)`）；上面那两个
#   导出/交换的工具**从「四」挪到「八」**（**按需 · 往后排**，这一版没把它编排进流程）。
#   **工具面一个都没动，总数仍是 20**。
# ⚠ 2026-09-29：阶段六**按用户指令扩范围**（不只有灯光 —— 天空/大气/天光/雾/云/后处理/时段
#   全都要）→ 新增 `setup_environment`（配整套环境：读现值台账 → 幂等 → 写 → 读回核对 →
#   可回滚、绝不存盘）+ `capture_preview`（按世界坐标摆机位出图，PNG 落 views/preview/，
#   **不动用户的视口相机**）—— 16 → **18** 个。
#   ⚠ 阶段六**最终定名「环境搭建」**（当天用户又收回了"相机预览"）：出图归**阶段七** ——
#     `capture_preview` 仍在工具面里（代码保留、归阶段七用），所以那一批是 **18 个**。
# ⚠ 2026-09-30：阶段七**只加「评估」这一个工具** —— `evaluate_layout`（落位对账：
#   plan ↔ 搭建台账 ↔ 关卡现状，**只读**、不碰关卡、不改 plan）→ **19 个**。
#   ⚠ 闭环**不新造工具**（用户拍板的 A 案）：`request_plan_change` → `generate_plan(patch=…)`
#     → 重画图 → `confirm_plan` → `execute_build()`（增量）这条链早就通了。
# ⚠ 2026-10-04：阶段五加**第 0 步** —— `create_surfaces`（缺的材质从零建出来；官方
#   `MaterialTools` 本来就有 create_material / add_expression / connect_to_output / recompile，
#   缺的是编排）→ **20 个**。⚠ 该工具**尚未实测** —— 要用就先 `only=[…]` 点名一小批。
# ⚠ 2026-10-04（第二批）：阶段三加 `adopt_user_edits`（**认领手改**：把"人工改过"的那几行按
#   关卡现状写回 plan 与台账，**不摆 Actor、不重画图、不走阶段二循环**）→ **21 个**。
#   ⚠ 该工具**尚未实测**，第一次真跑请先 `dry_run=true`。
# ⚠ 2026-10-04（第三批 · 合并）：`generate_build_orders` **从工具面撤掉**（用户拍板
#   「那就一个吧」）—— 它是**纯翻译件**（plan → 指令表），不参与任何闸门，却和
#   `execute_build` 读同一份 plan、复用同一条 `_compose_build_rows()` 口径。
#   现在它是 `main.py` 里的**内部件** `_orders_snapshot()`，由 `execute_build(dry_run=true)`
#   顺手落一份留痕件（`views/build_orders_v1.json`）—— **21 → 20 个**。
EXPECTED_TOOLS = [
    "adopt_user_edits",
    "apply_surfaces", "capture_preview",
    "check_build_target", "check_exchange", "confirm_assets", "confirm_elements",
    "confirm_plan", "create_surfaces", "evaluate_layout", "execute_build", "export_layout",
    "generate_plan",
    "get_asset_list", "get_plan",
    "official_status", "plan_assets", "rename_assets",
    "request_plan_change", "setup_environment",
]

# ⚠ 2026-10-04（第四批 · 阶段五扩流程）：**工具面一个都没动，总数仍是 20** ——
#   阶段五的九步流程全挂在现有两件工具上（用户已定：**不许新造第 21 件**）：
#     · `apply_surfaces` 加 `mode` 四档：`probe`（只读找材质、出草稿清单）/ `confirm`（定稿 + 签字）/
#       `apply`（**默认，旧行为不变**：按材质清单贴组件级覆盖）/ `asset_slots`（改网格资产的材质槽）；
#     · `create_surfaces` 加 `tune`（调已有材质实例的参数，读-改-写 + 记旧值）与 `ops`
#       （材质图编辑原语，**每次调用 = 一批，整批校验不过 ⇒ 一个 op 都不执行**）。
#   ⚠ 新的权威使用物 = `catalog/material_list.json`（`config` 的 `materials` 段**退役**）。
#   ⚠ **2026-10-04（第五批 · 用户指令）**：阶段五那两件工具（`apply_surfaces` /
#     `create_surfaces`）的**演练 / `dry_run` 这一步整段删掉**（原话「把演练这一步删掉，
#     不需要演练」）—— 它们现在**一调就是真做**；`mode="probe"` 保留（它是**功能上的只读**，
#     不是演练档）。⚠ **别的阶段的 `dry_run` 一个都没动**（`execute_build` 的人工痕迹两步闸
#     第 1 步就靠 `execute_build(dry_run=true)` 留痕）。总数仍是 **20**。
#   ⚠ 本批的离线回归见下面 ⑫a~⑫g。

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
    # --- ① 按**脚本方式**执行 main.py：复现Agent的启动方式，但不会启动 server ---
    # run_name 不是 "__main__"，所以文件末尾那句 `if __name__ == "__main__": mcp.run()` 不触发。
    # ⚠ 忠实复现：`python ...\main.py` 时 CPython 会把**脚本所在目录**放进 sys.path[0]，
    #   而 runpy.run_path **不会**。所以这里手动补上，否则预检会比真实启动更严（假红）。
    script_dir = str(MAIN_PY.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        mod = runpy.run_path(str(MAIN_PY), run_name="not_main")
        check("① main.py 能按【脚本方式】执行（Agent就是这么起它的）", True)
    except BaseException as exc:                      # BaseException：连 CancelledError 也接住
        check("① main.py 能按【脚本方式】执行（Agent就是这么起它的）", False,
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
                      "footprint_m": [10.0, 8.0], "rot_deg": 0.0, "scale": 1.0, "note": "",
                      # ⚠ **`z_m` 必填**（2026-10-08 用户指令「改位置表结构增加Z」）：
                      #   阶段二的字段校验缺它就拒收 ⇒ 假数据也必须带（否则 ④ 整片红）。
                      "z_m": 0.15}]
        ok_boxes = [{"element_key": "road_fake", "label": "假路", "shape": "plane",
                     "height_m": 0.15,
                     "pos": [0.0, -15.0], "footprint_m": [90.0, 6.0], "rot_deg": 0.0,
                     "size_source": "预估（预检）", "note": "", "z_m": 0.0}]
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

    # --- ⑥ 阶段三「官方脚本批处理」通道（2026-10-04 加 · ⚠ **真跑尚未实测**）---
    # 为什么预检要管它：这一段是**唯一**能把"落位"整批做完的路径，而它有两个静默失效的坑：
    #   ① `batch` 参数要是被谁改没了 / 改了默认值，行为会**悄悄**退回逐行（报文里看得出来，但没人会天天看）；
    #   ② 脚本是**拼字符串**拼出来的（payload 走两层 `json.dumps`）——拼坏了只有真跑时才炸，
    #      那时关卡里可能已经落了一半。所以这里**只验能离线验的东西**：
    #      脚本是合法 Python、且它内嵌的 payload 能原样解回来（中文 label 也要能穿过去）。
    # ⚠ **不调任何官方工具**：只 `exec` 脚本的**模块体**（`run()` 不被调用），碰不到 UE。
    try:
        import inspect as _inspect
        try:
            _params = _inspect.signature(mod["execute_build"]).parameters
            has_batch = "batch" in _params
            batch_default = _params["batch"].default if has_batch else None
        except (TypeError, ValueError):               # 装饰器万一不返回可自省的函数 → 退回读源码
            _src = MAIN_PY.read_text(encoding="utf-8")
            has_batch = "    batch: Annotated[bool, Field(" in _src
            batch_default = True if has_batch else None

        fake = [{
            "uid": "ground|假地基", "index": 1, "kind": "whitebox", "label": "假地基",
            "loc_cm": [0.0, 0.0, 5.0], "rot": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
            "scale": [1.0, 1.0, 1.0], "size_cm": [100.0, 100.0, 20.0],
            "folder": "UEMCP/ground", "name": "fake_ground",
        }]
        script = mod["_batch_place_script"](fake)
        env: dict = {}
        exec(script, env)                             # noqa: S102 —— 只跑模块体，`run()` 不调
        payload = env.get("PAYLOAD")
        ok_payload = (isinstance(payload, list) and len(payload) == 1
                      and payload[0].get("uid") == "ground|假地基"      # 中文穿得过去
                      and payload[0].get("size_cm") == [100.0, 100.0, 20.0])
        ok_calls = all(s in script for s in (
            ".add_to_scene_from_asset", ".add_to_scene_from_class", ".add_cube",
            ".set_actor_folder")) and "def run()" in script

        # ⚠ **2026-10-04 加：把 `run()` 也在一套假工具上真跑一遍** —— 这一步是"第一次真跑翻车"
        #   那次的**回归检查**。事故经过（实测原件：D 侧 `views/archive/build_state_20261004T090954Z.json`）：
        #   白膜行原来写 `got = {"refPath": spawned}` 再 `actor = _ref(got)`，而 `_ref()` **只认
        #   `returnValue`** ⇒ `actor` 恒为空 ⇒ 抛 `no actor ref` ⇒ **`set_actor_folder` 从未被调到**
        #   （那次台账 `inner_calls=2` = 只调了 spawn + add_cube；Actor 建出来了、引用丢了 ⇒ 永久游离）。
        #   所以这里断言四件事：① spawn 被调；② `add_cube` 被调；③ **`set_actor_folder` 被调**；
        #   ④ 那一行回的 `actor` **非空**且 `ok=True`。⚠ **全离线**（假 `execute_tool`，不碰 UE）。
        import json as _json
        calls: list[str] = []

        def _fake_execute_tool(tool: str, args: str):
            calls.append(str(tool).rsplit(".", 1)[-1])
            _json.loads(args)                          # 参数必须仍是合法 JSON（拼坏了要当场炸）
            if str(tool).endswith("add_to_scene_from_class"):
                return {"returnValue": {"refPath": "/Game/Fake.Fake:PersistentLevel.Actor_9"}}
            if str(tool).endswith("add_cube"):
                return {"returnValue": {"refPath": "/Game/Fake.Fake:PersistentLevel.Actor_9.cube"}}
            return {"returnValue": None}

        env["execute_tool"] = _fake_execute_tool
        _res = env["run"]()
        _row0 = ((_res or {}).get("rows") or [{}])[0]
        ok_row_logic = (calls[:3] == ["add_to_scene_from_class", "add_cube", "set_actor_folder"]
                        and bool(_row0.get("actor")) and bool(_row0.get("ok"))
                        and bool(_row0.get("folder_ok")))
        check("⑥ 阶段三批处理通道：`batch` 参数在（默认 true）+ 脚本能编出来、payload 能解回来"
              " + **白膜行三步都走到、引用没丢**（回归 2026-10-04 那次真跑事故）",
              bool(has_batch and batch_default is True and ok_payload and ok_calls and ok_row_logic),
              f"has_batch={has_batch} default={batch_default!r} payload_ok={ok_payload} "
              f"tools_ok={ok_calls} calls={calls[:4]} row0={_row0}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑥ 阶段三批处理通道：`batch` 参数在（默认 true）+ 脚本能编出来、payload 能解回来"
              " + **白膜行三步都走到、引用没丢**（回归 2026-10-04 那次真跑事故）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑦ 两处加固（**不写任何状态文件、不调官方**）---
    # ⚠ 2026-10-04 改：`_answer_code` **已按用户指令撤掉**（「把这个什么码删掉，太离谱了」），
    #   所以这里不再验它 —— 改成验**它真的没了**（防止哪天又被加回来）+ `_live_vs_ledger` 三元组。
    # ① 应答码必须**不存在**（撤了就撤干净：函数没了、留痕也不再写 `answer_code` 字段）；
    # ② `_live_vs_ledger` 的返回值必须是**三元组**：第二个是「没比成的行」。
    #    以前是两元组，于是"引用失效 / 读失败 / 白膜尺寸读不回来"那几行被**静默跳过** ——
    #    "没查到"与"查了没问题"在报文里长得一样。这条断言就是防它被改回去。
    try:
        ok_code = ("_answer_code" not in mod)
        import inspect as _inspect2
        _gate_src = _inspect2.getsource(mod["_user_edits_gate"])
        ok_gate = ("answer_code" not in _gate_src)          # 闸里不再读码
        _ret = _inspect2.signature(mod["_live_vs_ledger"]).return_annotation
        ok_ret = (getattr(_ret, "__origin__", None) is tuple
                  and len(getattr(_ret, "__args__", ())) == 3)
        check("⑦ 应答码已撤干净（函数不在 + 闸不读码）+ `_live_vs_ledger` 回三元组（含「没比成」那一维）",
              bool(ok_code and ok_gate and ok_ret),
              f"ok_code={ok_code} ok_gate={ok_gate} return={_ret} ok_ret={ok_ret}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑦ 应答码已撤干净（函数不在 + 闸不读码）+ `_live_vs_ledger` 回三元组（含「没比成」那一维）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑧ 阶段五第 0 步「建材质」的**离线**部分（2026-10-04 加 · ⚠ 真跑尚未实测）---
    # 只验"读表 + 配方校验"这两件**不碰 UE** 的事：
    #   · `_surface_creates()` 能把 `config/surface_materials.json` 的 `create` 段读出来；
    #   · 一条合法配方能过校验（参数名收进 `params`、`parent` 为空）；
    #   · **非法**配方必须被拒：输出名不在允许集 / 同时给 `parent` 与材质输出键 / MI 那条带 `_flags`。
    # ⚠ **2026-10-07 改判据**：原来这三条打的是 `_create_plan_item()`（建材质的**旧实现**）——
    #   那批死代码已按用户指令删除，改打**活路径** `surfaces._validate_recipe()`
    #   （`create` 档真正用的那道整批校验）。覆盖面因此更强，不是变弱。
    # ⚠ 真跑（create_material / add_expression / connect_to_output 的属性名）**不敢在这里验** ——
    #   那要 UE 在线，而且那几个属性名**尚未实测**（见 main.py 顶部 MAT_PARAM_* 常量的说明）。
    try:
        _tbl, _tbl_note = mod["_surface_creates"]()
        _ok_tbl = isinstance(_tbl, dict)
        _vr = mod["_surfaces_module"]()._validate_recipe
        _spec_ok, _errs_ok = _vr("/Game/Preflight/M_Road",
                                 {"BaseColor": [0.1, 0.1, 0.12, 1.0], "Roughness": 0.9})
        _ok_item = bool(not _errs_ok and isinstance(_spec_ok, dict)
                        and set((_spec_ok.get("params") or {})) == {"BaseColor", "Roughness"}
                        and not _spec_ok.get("parent"))
        _s1, _e1 = _vr("/Game/Preflight/M_Bad", {"NotAnOutput": 1.0})
        _s2, _e2 = _vr("/Game/Preflight/MI_Bad",
                       {"parent": "/Game/Preflight/M_Road", "BaseColor": [0.1, 0.2, 0.3, 1.0]})
        _s3, _e3 = _vr("/Game/Preflight/MI_Bad2",
                       {"parent": "/Game/Preflight/M_Road", "_flags": {"twoSided": True}})
        _ok_bad, _ok_bad2 = bool(_e1), bool(_e2 and _e3)
        check("⑧ 建材质：`create` 段读得出 + 合法配方过校验 + 三种非法配方都被拒（真跑尚未实测）",
              bool(_ok_tbl and _ok_item and _ok_bad and _ok_bad2),
              f"table_ok={_ok_tbl} note={_tbl_note!r} item_ok={_ok_item} "
              f"bad_ok={_ok_bad}({_e1[:1]}) bad2_ok={_ok_bad2}({_e2[:1]}+{_e3[:1]})")
    except BaseException as exc:                      # noqa: BLE001
        check("⑧ 建材质：`create` 段读得出 + 合法配方过校验 + 三种非法配方都被拒（真跑尚未实测）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑨ 阶段四「微调」的状态机（2026-10-04 加 · **纯离线、不碰 UE、不写盘**）---
    # 为什么必须在这里钉住（这一条是**回归靶子**）：
    #   · `mark_tuned()` 写的 `plan_hash` 必须是**改完之后**那一版的指纹 —— 写错的话，微调版会在
    #     `execute_build` 的"几何指纹对不上"那一关被拒（fail-closed，但用户会觉得工具坏了）；
    #   · `acceptance_state()` 必须**先**认 `confirmation.mode == "tuned"` 直接回 `tuned`，
    #     否则它会走"图认不认这份数据"那条路 → 微调版永远显示"等出图" → `execute_build` 拒收；
    #   · `ACCEPTANCE_STATES` 里得有 `tuned` 的中文说明（`get_plan` 直接展示它）。
    # ⚠ 只调**规划层的纯函数**（`planning`），不碰 `main.py` 的工具、不调官方、不落盘：
    #   这里构造的假 plan 只在内存里，`mark_tuned` 会顺手写验收台账 —— 所以先记下原文件的
    #   字节内容，验完**原样写回**（不留痕、不改用户数据）。
    _acc_path = None
    try:
        import json as _json3
        _pl = mod["_planning_modules"]()             # 规划层：与工具面**同一个**模块对象
        _acc_path = _pl.ACCEPTANCE_PATH
        _acc_before = _acc_path.read_bytes() if _acc_path.exists() else None
        _fake = {
            "unit": "m", "world": {"center": [0.0, 0.0], "size": [10.0, 10.0]},
            "assets": [],
            "whiteboxes": [{"element_key": "road", "label": "沥青车行道", "shape": "cube",
                            "height_m": 0.15, "pos": [0.0, 0.0], "footprint_m": [10.0, 4.0],
                            "rot_deg": 0.0, "size_source": "预估（参考图目测，待核实）"}],
            "confirmation": {"required": True, "confirmed": True, "mode": "tuned",
                             "confirmed_by": "用户（微调）", "confirmed_at": "t",
                             "user_quote": "把电线杆挪近一点", "rows": [], "plan_hash": "x"},
        }
        _ok_state = (_pl.acceptance_state(_fake) == "tuned"
                     and "tuned" in _pl.ACCEPTANCE_STATES
                     and bool(_pl.ACCEPTANCE_STATES["tuned"]))
        _tuned_plan = _pl.mark_tuned(
            {k: v for k, v in _fake.items() if k != "confirmation"},
            "把电线杆挪近一点", rows=[{"label": "沥青车行道", "why": "挪近 1 m"}])
        _ok_hash = (str(_tuned_plan["confirmation"]["plan_hash"])
                    == str(_pl.plan_geometry_hash(_tuned_plan)))
        _ok_quote = bool(str(_tuned_plan["confirmation"].get("user_quote") or "").strip())
        _ok_rows = [r.get("label") for r in (_tuned_plan["confirmation"].get("rows") or [])] \
            == ["沥青车行道"]
        # 没原话必须**拒收**（与 confirm_plan 同一条纪律：拿不出原话 = 没问过人）
        try:
            _pl.mark_tuned({"whiteboxes": [], "assets": []}, "   ")
            _ok_noquote = False
        except ValueError:
            _ok_noquote = True
        check("⑨ 微调：`mark_tuned` 指纹对得上 + 认 `tuned` 状态 + 留痕（原话/行）+ **没原话拒收**",
              bool(_ok_state and _ok_hash and _ok_quote and _ok_rows and _ok_noquote),
              f"state_ok={_ok_state} hash_ok={_ok_hash} quote_ok={_ok_quote} "
              f"rows_ok={_ok_rows} noquote_ok={_ok_noquote}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑨ 微调：`mark_tuned` 指纹对得上 + 认 `tuned` 状态 + 留痕（原话/行）+ **没原话拒收**",
              False, f"{type(exc).__name__}: {exc}")
    finally:
        # 把验收台账**原样写回**（上面那次 `mark_tuned` 会动它）—— 预检不许留下自己的痕迹。
        try:
            if _acc_path is not None:
                if _acc_before is None:
                    _acc_path.unlink(missing_ok=True)
                else:
                    _acc_path.write_bytes(_acc_before)
        except OSError:
            pass

    # --- ⑩ 八阶段总览 `_stage_overview()`（2026-10-04 加 · **纯离线**）---
    # 为什么要有它：阶段名以前只散在各工具各自的报文里，**没有一处回答得了「我现在整体在第几
    #   阶段」** —— 接手的人（新对话 / 换 agent）只能自己拼。现在唯一判据在
    #   `main.py::_stage_overview()`，由 `get_plan()` / `get_asset_list()` 的 `stages` 带出。
    # ⚠ 这里断言的是**结构**，不是内容：
    #   · 正好 8 条、`no` 是 1..8 不缺不重；
    #   · 每条都带 `name` / `state` / `evidence` / `next` 四个键（且都不是 None）；
    #   · **不调用任何官方工具**（用 `inspect.getsource` 断言函数体里不含 `call_official`）——
    #     它必须能在 UE 没开的时候照样回答（get_plan 是"开场必调、完全不碰 UE"的那一个）。
    # ⚠ **磁盘上缺文件不算失败**：缺文件是正常状态，那一行应当写「未开始」（断言的是结构）。
    try:
        import inspect as _inspect3
        # 先把「唯一判据」钉住：函数体里不许出现官方调用（也不许出现在它两个辅助件里）
        _ov_src = _inspect3.getsource(mod["_stage_overview"])
        _aux_src = (_inspect3.getsource(mod["_safe_json"])
                    + _inspect3.getsource(mod["_stage_acceptance_rows"])
                    + _inspect3.getsource(mod["_stage_last_at"]))
        ok_offline = ("call_official" not in _ov_src) and ("call_official" not in _aux_src)

        _stages = mod["_stage_overview"]()
        _need = ("name", "state", "evidence", "next")
        ok_rows = (isinstance(_stages, list) and len(_stages) == 8)
        ok_no = ok_rows and ([s.get("no") for s in _stages] == list(range(1, 9)))
        ok_keys = ok_rows and all(
            isinstance(s, dict)
            and all(k in s and s.get(k) not in (None, "") for k in _need)
            for s in _stages
        )
        # 状态取值只允许那四个（不许悄悄多造一种 —— 多一种就意味着别处在替它判断）
        _ok_states = {"未开始", "进行中", "已完成", "需你确认"}
        ok_state = ok_rows and all(s.get("state") in _ok_states for s in _stages)
        check("⑩ 八阶段总览：正好 8 条 / 编号 1..8 / 四个键齐 / 状态取值合法 / **不调官方工具**",
              bool(ok_offline and ok_rows and ok_no and ok_keys and ok_state),
              f"offline={ok_offline} rows={len(_stages) if isinstance(_stages, list) else _stages!r} "
              f"no_ok={ok_no} keys_ok={ok_keys} state_ok={ok_state}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑩ 八阶段总览：正好 8 条 / 编号 1..8 / 四个键齐 / 状态取值合法 / **不调官方工具**",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑪ 纪元（epoch 2）：活动 plan 的解析 + 取原话那条判据的**回归**（2026-10-04 加 · 纯离线）---
    # 为什么要有它：
    #   ① 「阶段三结束 ⇒ 换新 plan + 新台账」之后，"读哪一份 plan"全靠 `active_plan_path()`
    #      这一处判据（全工程十几个读点都走它）—— 它解析错一步，后面全错，而且是**静默**的
    #      （会拿着冻结的 v1 当权威，或反过来）；
    #   ② 2026-10-04 修的那个 bug：微调分支原先用 `planning.pending_changes(plan)` 取用户原话，
    #      而那个函数按**改完之后**的新指纹筛台账事件（`request_plan_change` 记要求时写的是
    #      **改之前**的旧指纹）⇒ **永远筛不到** ⇒ 微调从不生效（现场 `tuned_count=0`）。
    #      所以这里钉住：那条回归的判据是 **`pending_changes` 不许出现在
    #      `_pending_change_quote` 实际引用的全局名里**（真回退成 `planning.pending_changes(plan)`
    #      这个名字必然进 `co_names` ⇒ 立刻红）；顺带挡住「自己按 `plan_geometry_hash` 筛」。
    #      ⚠ docstring / 注释里提到这些名字**都不算**（那句留痕正是要的）。
    #   ③ **2026-10-07 加的那条（这条是"失忆用 V1"的回归）**：判据从"v2 在**且能解析** ⇒ v2，
    #      否则退回 v1"改成 **"v2 在 ⇒ 永远是它；不在 ⇒ 才轮到 v1"**（只认"文件在不在"）——
    #      于是要同时钉住**两个方向**：**v2 在时任何一档都不许退回 v1**（含"v2 读不动"那一档），
    #      **v2 不在时仍然指 v1**（从零搭建那条路，砍了就砸掉换纪元闸的出生条件①）。
    # ⚠ **不真建 `views/plan_v2.json`**（那是运行时状态 —— 预检不许碰，更不许留下痕迹）：
    #   把规划层的 `PLAN_V2_PATH` **与 `OUT_JSON`** 都临时指到 `tmp` 目录里的假路径上验五档，
    #   验完**原样还原**。
    #   ⚠ 解析的是 `active_plan_path()`（它在**调用时**读模块全局，所以能这样打桩）。
    # ⚠ **磁盘上没有 `plan_v2.json` 是正常状态**，这一项**不会**因此失败（断言的是解析行为）。
    try:
        import inspect as _inspect4
        import json as _json4
        import tempfile as _tempfile

        _pl2 = mod["_planning_modules"]()
        _orig_v2 = _pl2.PLAN_V2_PATH
        _orig_v1 = _pl2.OUT_JSON
        _ok_fresh = _ok_v1only = _ok_v2 = _ok_broken = _ok_both = False
        try:
            with _tempfile.TemporaryDirectory() as _td:
                _fake_v2 = Path(_td) / "plan_v2.json"
                _fake_v1 = Path(_td) / "plan_v1.json"
                _pl2.PLAN_V2_PATH = _fake_v2
                _pl2.OUT_JSON = _fake_v1
                # ⚠ **2026-10-07 改**（用户指令「进入纪元 2 后，V1 要不直接删除得了，以后增删改查都靠 V2」
                #   +「别结束了前三阶段后面又**失忆用 V1**」）：判据从"v2 在**且能解析** ⇒ v2，否则退回 v1"
                #   改成 **"v2 在 ⇒ 永远是它；不在 ⇒ 才轮到 v1"**（只认"文件在不在"这一条）。
                #   ⇒ 下面钉两件事：① **v2 在时任何一档都不许退回 v1**（那正是"失忆用 V1"）；
                #      ② **v2 不在时仍然指 v1**（那是从零搭建时纪元 1 的正常目标 —— 也砍掉的话，
                #         阶段二会把 plan 直接写进 v2，换纪元闸的出生条件①就永远不成立了）。
                # ① **两份都不在**（从零搭建最开始）⇒ v1、纪元 1
                _ok_fresh = bool(_pl2.active_plan_path() == _fake_v1
                                 and _pl2.active_epoch() == 1
                                 and _pl2.active_plan_path() != _fake_v2)
                # ② **v1 在、v2 不在**（阶段二 / 阶段三）⇒ 还是 v1、纪元 1
                _fake_v1.write_text(_json4.dumps({"unit": "m", "assets": [], "whiteboxes": []}),
                                    encoding="utf-8")
                _ok_v1only = bool(_pl2.active_plan_path() == _fake_v1
                                  and _pl2.active_epoch() == 1)
                # ③ v2 在且能解析 ⇒ 它、纪元 2
                _fake_v2.write_text(_json4.dumps({"unit": "m", "assets": [], "whiteboxes": []}),
                                    encoding="utf-8")
                _ok_v2 = bool(_pl2.active_plan_path() == _fake_v2
                              and _pl2.active_epoch() == 2)
                # ④ v2 在、但**读不动** ⇒ **仍然是它**（**不许**退回 v1；"文件在、内容坏"由各闸拒收）
                _fake_v2.write_text("{ 这不是 JSON", encoding="utf-8")
                _ok_broken = bool(_pl2.active_plan_path() == _fake_v2
                                  and _pl2.active_epoch() == 2
                                  and _pl2.active_plan_path() != _fake_v1)
                # ⑤ **v2 与 v1 都在**（本工程换纪元之后的常态）⇒ 只认 v2，绝不拿冻结的 v1 当权威
                _ok_both = bool(_pl2.active_plan_path() == _fake_v2
                                and _pl2.active_epoch() == 2
                                and _pl2.active_plan_path() != _fake_v1)
        finally:
            _pl2.PLAN_V2_PATH = _orig_v2        # ⚠ 原样还原：预检不留痕
            _pl2.OUT_JSON = _orig_v1

        # ⚠ 判据用**代码对象**、不用源码文本：`co_names` = 这个函数**真的引用了哪些全局名**。
        #   主判据是 `pending_changes`（那个 bug 的形状：回退成 `planning.pending_changes(plan)`）；
        #   `plan_geometry_hash` 只是顺带挡「自己按指纹筛」。
        #   反之，docstring / 注释里提到这些名字**都不算问题**（那句留痕正是要的）。
        _q_src = _inspect4.getsource(mod["_pending_change_quote"])            # 正项仍看源码
        _q_names = mod["_pending_change_quote"].__code__.co_names             # 真的引用了哪些全局名
        _ok_quote = ("pending_changes" not in _q_names          # ⚠ 这条才是那个 bug 的回归：
                                                            #   回退成 planning.pending_changes(plan)
                                                            #   这个名字必然进 co_names ⇒ 立刻红
                     and "plan_geometry_hash" not in _q_names   # 顺带挡住「自己按指纹筛」
                     and "change_window_open" in _q_src)        # 它是字符串字面量，只在源码/co_consts 里
        check("⑪ 纪元：活动 plan 的取向（**v2 在 ⇒ 只认 v2**：读不动也不退回 v1；"
              "**v2 不在 ⇒ 纪元 1 的 v1**：从零搭建要靠这一支）"
              " + 取原话**不按几何指纹筛**",
              bool(_ok_fresh and _ok_v1only and _ok_v2 and _ok_broken and _ok_both and _ok_quote),
              f"两份都不在⇒v1/纪1={_ok_fresh} v1在v2不在⇒v1/纪1={_ok_v1only} "
              f"v2在⇒v2/纪2={_ok_v2} v2读不动⇒仍v2（不回退）={_ok_broken} "
              f"v1v2都在⇒只认v2={_ok_both} quote_ok={_ok_quote}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑪ 纪元：活动 plan 的取向（**v2 在 ⇒ 只认 v2**：读不动也不退回 v1；"
              "**v2 不在 ⇒ 纪元 1 的 v1**：从零搭建要靠这一支）"
              " + 取原话**不按几何指纹筛**",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑫ 阶段五（2026-10-04 扩）· **材质清单 + 三道闸**（**全离线**：一套假官方调用）-----------
    # 为什么要这一组：阶段五这一版把「贴什么」从 `config` 换成了**材质清单**，并新增三档
    #   （`probe` / `confirm` / `asset_slots`）与两个新动作（`tune` / `ops`）。这几件事的**闸门**
    #   都是"拒收"型的 —— 拒收错了方向（该拒的放行）在生产里表现为**悄悄写坏东西**，
    #   所以必须在这里用**假的官方调用**把它们逐条钉住。
    #
    # ⚠ **全离线**：`mod["call_official"]` 被换成一个查表函数（假 `execute_tool`），
    #   不 import 任何新东西、不连 UE、不写任何文件（下面每个断言都只调"会拒收"的那条路，
    #   而拒收发生在**写盘之前** —— 所以连 catalog/ 都不会被碰）。
    #   ⚠ 预先 `_mark_link_state(True)`：否则"碰 UE 的工具"会被链路闸先拒掉，
    #     那样就测不到我们要测的判据了（链路闸本身另有归属，见 ⑦/⑩）。
    _real_call_official = mod["call_official"]
    # ⚠⚠ **必须写"真"模块全局，不能写 `mod`**（2026-10-04 **实测踩到**，这条是这一组的命门）：
    #   `runpy.run_path()` 返回的是 globals 的**副本** —— 标准库 `_run_module_code()` 末尾就是
    #   `return mod_globals.copy()`（原话；不是推测）。所以往 `mod[...]` 里写，**对运行中的代码
    #   毫无影响**：函数查的仍是它自己的 `__globals__`（那个临时模块的 `__dict__`）。
    #   后果有两层，**第二层更坏**：
    #     ① 桩从来没生效 ⇒ 真 `call_official` 照跑 ⇒ ⑫c 撞上假 ctx 报
    #        `AttributeError: 'object' object has no attribute 'request_context'`（本轮预检就是这个）；
    #     ② **假绿**：那些"一次官方调用都没发生"的断言，因为 `_seen` 永远是空的而**必过** ——
    #        即这一组此前**从来没测过任何东西**。判据错的时候，绿灯比红灯更坏。
    #   所以：下面所有**打桩 / 还原**一律经 `_G`（= 某个 main.py 函数的 `__globals__`，那就是原件）。
    #   ⚠ **只读**（`mod["ToolError"]` / `mod["_mark_link_state"](True)` 这类）照旧走 `mod` 无妨 ——
    #     取到的是同一个函数对象，它自己写状态时写的也是真字典。
    _G = mod["apply_surfaces"].__globals__
    try:
        import asyncio as _aio5

        # 假的"官方调用"：`(toolset, tool_name, arguments)` → 返回值；查不到就抛。
        # ⚠ 记下**被调用过哪些官方工具** —— "整批拒收"那条断言要靠它证明**一个写 op 都没执行**。
        def _mk_stub(table: dict):
            seen: list[str] = []

            async def _stub(ctx, tool_name, arguments, toolset=None, check_link=True):
                seen.append(str(tool_name))
                key = str(tool_name)
                if key not in table:
                    raise mod["ToolError"](f"（预检桩）没给 `{key}` 准备返回值")
                got = table[key]
                return got(arguments) if callable(got) else got
            return _stub, seen

        mod["_mark_link_state"](True)
        _fake_ctx = object()          # 桩不碰 ctx，给个占位就行

        # ⑫a `probe`：**没有资产清单就拒收**（不许静默出一份空清单）
        #     判据：① 抛 ToolError；② 报错里点名"资产清单"；③ **一次官方调用都没发生**。
        # ⚠ 2026-10-04 晚（阶段五整层搬进 `surfaces.py`）之后，这条闸在 `surfaces.probe()` 里，
        #   测法随之改成：**直接调那一层的 `probe()`**（那不是"绕开工具"，因为它就是那档实现）；
        #   注入面用一个最小的桩 repo —— 只要它有 `official` / `load_json` 两个字段就够
        #   （其余字段用 dataclass 默认值：本档不需要 IO / 路径换算）。
        _sv = importlib.import_module("surfaces")
        _saved_sv_asset = _sv.ASSET_LIST_PATH
        # ⚠ 2026-10-10：这里**曾**有一段补丁 —— 在系统临时目录里造一份同名 `material_list.json`，
        #   只为让 `surfaces.manifest_rows()` 第一步的磁盘检查 `MATERIAL_LIST_PATH.exists()` 通过
        #   （它当时**绕过**了 `repo.load_json()` 那个注入面，于是本文件各段注入的假清单被挡在门外）。
        #   **根因已于同日就地根治**：`surfaces.load_manifest()` 改成"**先问注入面、再问磁盘**" ——
        #   与同文件 `snapshot_stale()` 的写法对齐（那一条本来就承诺"比的是注入进来的那一份"）。
        #   ⇒ 补丁**已撤销**。⚠ 别再往这里加回来：那是**测试去迁就产品怪癖**，不是修问题。
        try:
            import tempfile as _tf5
            with _tf5.TemporaryDirectory() as _td5:
                _sv.ASSET_LIST_PATH = Path(_td5) / "no_such_asset_list.json"
                _stub, _seen = _mk_stub({})
                _G["call_official"] = _stub
                _repo = _sv.SurfacesRepo(
                    official=lambda tool_name, arguments, toolset="": _stub(
                        None, tool_name, arguments, toolset),
                    # ⚠ `match_assets` 是**必填**的注入（阶段一那份唯一实现）——
                    #   连"没资产清单 ⇒ 拒收"这一条也要给全注入面（不给就当场 TypeError）。
                    match_assets=mod["match_assets_by_keyword"],
                    fingerprint=mod["library_fingerprint"],
                    load_json=lambda p: None)
                try:
                    _aio5.run(_sv.probe(_repo, max_per_element=5))
                    _ok_guard, _why_guard = False, "没拒"
                except mod["ToolError"] as exc:
                    _ok_guard, _why_guard = ("资产清单" in str(exc)), str(exc)[:60]
                _ok_nocall = not _seen
        finally:
            _sv.ASSET_LIST_PATH = _saved_sv_asset
        check("⑫a `probe` 在没有资产清单时**拒收**（不静默出空清单、且一次官方调用都没发生）",
              bool(_ok_guard and _ok_nocall), f"guard={_ok_guard} calls={_seen} why={_why_guard}")

        # ⑫b `confirm`：**缺用户原话 ⇒ 拒收**；**清单里还有 `pending_user` ⇒ 拒收**；
        #      而 `missing_self_build`（待自建）**允许**存在（那正是第 ⑤ 步要建的）。
        # ⚠ 2026-10-04 晚：这几条闸的实现搬进了 `surfaces.validate_rows()`（**纯函数**，
        #   不碰官方 / 不碰磁盘），所以这里直接调它 —— **判据仍是同一份实现**，不许因为
        #   "搬了个家"就把这条回归删掉（拒收错了方向 = 悄悄写坏东西）。
        # ⚠ 新口径多一条：**每行必须有一块材质实例**（`instance`）—— 没有实例又没标待自建的，
        #   **拒收**（用户 2026-10-04：「清单里都只记下材质实例」）。所以下面每一行都带上
        #   `instance` / `instance_status`。
        _sv_b = importlib.import_module("surfaces")
        _mk = mod["MaterialConfirmItem"]
        _rows_ok = [_mk(element_key="road", label="车行道", target="whitebox",
                        instance="/Game/Preflight/MI_Road",
                        instance_status=_sv_b.INST_IS_INSTANCE,
                        material="/Game/Preflight/MI_Road", status="found", source="search")]
        _rows_pend = [_mk(element_key="shrub", label="灌木", target="whitebox", status="pending_user")]
        _rows_build = [_mk(element_key="shrub", label="灌木", target="whitebox",
                           material="/Game/Preflight/M_Shrub",
                           status="missing_self_build")]
        _rows_build_nopath = [_mk(element_key="shrub", label="灌木", target="whitebox",
                                  status="missing_self_build")]
        # ⚠ 这一条要测的是"**既没实例、也没母材质** ⇒ 拒收"。
        #   2026-10-04 晚修正：它原来带着 `material="/Game/Preflight/M"` —— 按**改正后**的判据
        #   那正是**合法**的 `from_material` 行（第 ⑤ 步照它派生实例），所以它**不该**被拒。
        #   样本本身错了 ⇒ 现在**把 material 去掉**，才真的测到了那条闸。
        _rows_noinst = [_mk(element_key="road", label="车行道", target="whitebox",
                            status="found", source="search")]
        _r1, _b1 = _sv_b.validate_rows(_rows_ok, "")
        _r2, _b2 = _sv_b.validate_rows(_rows_ok, "就这么贴")
        _r3, _b3 = _sv_b.validate_rows(_rows_pend, "就这么贴")
        _r4, _b4 = _sv_b.validate_rows(_rows_build, "确实没有，你建吧")
        _r5, _b5 = _sv_b.validate_rows([], "就这么贴")
        _r6, _b6 = _sv_b.validate_rows(_rows_noinst, "就这么贴")
        _r7, _b7 = _sv_b.validate_rows(_rows_build_nopath, "确实没有，你建吧")
        _ok_quote_gate = bool(_b1) and not _b2          # 缺原话拒 / 有原话过
        _ok_pend_gate = bool(_b3)                        # pending_user 拒
        _ok_found = bool(not _b2 and len(_r2) == 1 and _r2[0].instance.endswith("MI_Road"))
        _ok_build_ok = bool(not _b4 and _r4 and _r4[0].instance_status == _sv_b.INST_TO_CREATE)
        _ok_empty_gate = bool(_b5)                          # 空表拒
        # ⚠ 这两条的**判据在 2026-10-04 晚被改正过**（真跑当场撞到）：
        #   原来写的是"没有 `instance` 就拒收"，那是**错的** —— `from_material`（有材质、缺实例）
        #   是第 ④ 步**合法**的行（第 ⑤ 步才派生实例）。正确判据是：
        #   **既没有实例、也没有 `material`/`parent`** 才拒收（那种行既贴不了也派生不出）。
        _ok_nomat_gate = bool(_b6)        # 既没实例、也没母材质 ⇒ 拒
        _ok_buildpath_gate = bool(_b7)    # 待自建却没说建到哪 ⇒ 拒

        # ⑫b-2 **靶子判据**（2026-10-04 晚**真跑撞到的 bug 的离线回归**）：
        #   第 ④ 步清单里"有材质、缺实例"（`from_material`）的行**没有 `instance`** ——
        #   `confirm()` 当时拿空串去问官方 `exists()` ⇒ **整批被拒收**，而报文里只剩一串空路径
        #   （人看不出拒的是什么）。改正后的判据收在两个纯函数里，这里把它们钉住：
        #   ① 这种行的**验活靶子 = 它的母材质**（不许是空串）；
        #   ② 第 ⑤ 步要派生到哪儿**是可复算的**（第 ⑥ 步贴的时候调同一支，名字不会两样）；
        #   ③ 已经有实例的行，靶子**永远是那个实例**（母材质只是退路）。
        _fm_row = _mk(element_key="sidewalk", label="人行道", target="whitebox",
                      material="/Game/Preflight/M_Sidewalk", status="found", source="search")
        _rf, _bf = _sv_b.validate_rows([_fm_row], "就这么贴")
        _rowd = _rf[0].model_dump() if _rf else {}
        _tgt = _sv_b.surface_target(_rowd)
        _si = _sv_b.surface_instance(_rowd)
        _ok_target = bool(_bf == [] and _tgt == "/Game/Preflight/M_Sidewalk"
                          and _si == "/Game/Preflight/MI_M_Sidewalk"
                          and _rf and _rf[0].instance_status == _sv_b.INST_FROM_MATERIAL)
        _ok_suffix = bool(_sv_b.derived_instance_of({"parent": "/Game/Preflight/MI_sjfnch0a"})
                          == "/Game/Preflight/MI_sjfnch0a_1")
        _ok_prefer = bool(_sv_b.surface_instance({"instance": "/Game/Preflight/MI_A",
                                                  "material": "/Game/Preflight/M_B"})
                          == "/Game/Preflight/MI_A")
        check("⑫b-2 阶段五靶子判据：**有材质缺实例的行靶子 = 母材质（不是空串）** + "
              "**派生名可复算**（`M_X → MI_M_X`、`MI_X → MI_X_1`）+ **已有实例优先**",
              bool(_ok_target and _ok_suffix and _ok_prefer),
              f"target={_tgt!r} inst={_si!r} suffix={_ok_suffix} prefer={_ok_prefer} "
              f"bad={_bf[:1]}")
        check("⑫b `confirm`：缺用户原话拒收 + `pending_user` 拒收 + 空表拒收 + "
              "`missing_self_build`（待自建）**允许** + **既没实例也没母材质的行拒收** + "
              "**待自建没说目标路径也拒收**",
              bool(_ok_quote_gate and _ok_pend_gate and _ok_found
                   and _ok_build_ok and _ok_empty_gate and _ok_nomat_gate
                   and _ok_buildpath_gate),
              f"quote={_ok_quote_gate} pend={_ok_pend_gate} found={_ok_found} "
              f"build_ok={_ok_build_ok} empty={_ok_empty_gate} nomat={_ok_nomat_gate} "
              f"buildpath={_ok_buildpath_gate} why1={_b1[:1]}")

        # ⑫c `asset_slots`：写一个**不在 `get_material_slots` 返回里**的槽名 ⇒ 该行 failed，
        #      且**一个 `set_material` 都没调**（槽名只认官方返回，不许自己拼）。
        _saved_ledger = mod["_load_build_state"]
        _stub, _seen = _mk_stub({
            "get_material_slots": [{"name": "Body"}, {"name": "Glass"}],
            "get_material": {"refPath": "/Game/Preflight/MI_Old.MI_Old"},
        })
        _G["call_official"] = _stub
        _G["_load_build_state"] = lambda: {"rows": []}
        try:
            _rep = _aio5.run(mod["apply_surfaces"](
                _fake_ctx, mode="asset_slots",
                asset_slots=[{"mesh": "/Game/Preflight/mesh1", "slot_name": "NotASlot",
                              "material": "/Game/Preflight/MI_New"}]))
            _slot = _rep.slot_rows[0]
            _ok_slot = (_slot.status == "failed" and "不在" in _slot.error
                        and "set_material" not in _seen)
        finally:
            _G["_load_build_state"] = _saved_ledger
        check("⑫c `asset_slots` 写不在 `get_material_slots` 返回里的槽名 ⇒ **拒收该行、"
              "且没调用 `set_material`**",
              bool(_ok_slot), f"slot={_slot.status} err={_slot.error[:60]} calls={_seen}")

        # ⑫d `tune`：写一个 **`list_parameters` 没返回过**的参数名 ⇒ 拒收（**一个参数都没写**）
        # ⚠ **2026-10-07 改判据**：原来打的是 `_tune_parameters()`（**旧实现**，已按用户指令删除）——
        #   现在打**活路径** `surfaces.tune()`（同一套注入式 `SurfacesRepo` 写法，见 ⑬）。
        #   两条断言分开：① 拒收原因点名那个参数；② **一个 `set_*` 都没调**（不是"报错了就算"）。
        _svd = mod["_surfaces_module"]()
        _seen4: list[str] = []

        async def _stub_d(_c, tool_name, arguments, toolset=None, check_link=True):
            _seen4.append(str(tool_name))
            if tool_name == "list_parameters":
                return [{"name": "BaseColor", "type": "vector"},
                        {"name": "Roughness", "type": "scalar"}]
            raise mod["ToolError"](f"（预检桩）⑫d 没给 `{tool_name}` 准备返回值")

        _repo_d = _svd.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": _stub_d(
                None, tool_name, arguments, toolset),
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            load_json=lambda p: ({"items": [{
                "element_key": "road", "label": "车行道", "target": "whitebox",
                "material": "/Game/Preflight/M_Road",
                "instance": "/Game/Preflight/MI_X",
                "instance_status": _svd.INST_IS_INSTANCE,
                "parent": "/Game/Preflight/M_Road", "status": _svd.MAT_FOUND,
            }]} if str(p).endswith("material_list.json") else None),
            save_json=lambda p, d: None,
            archive_json=lambda p, x: "",
            to_object_path=lambda s: f"{s}.{s.rsplit('/', 1)[-1]}",
        )
        _t_bad_reason = ""
        try:
            _aio5.run(_svd.tune(_repo_d, {"/Game/Preflight/MI_X": {"NotAParam": 0.5}}))
        except mod["ToolError"] as exc:
            _t_bad_reason = str(exc)
        _ok_tune_gate = bool("NotAParam" in _t_bad_reason and "不在" in _t_bad_reason
                             and "set_scalar_parameter" not in _seen4
                             and "set_vector_parameter" not in _seen4)
        check("⑫d `tune` 写 `list_parameters` 没返回过的参数名 ⇒ **拒收**（一个参数都没写）",
              bool(_ok_tune_gate), f"reason={_t_bad_reason[:90]} calls={_seen4}")

        # ⑫e `ops`：`set_node` 的属性名**不在 `list_properties` 返回里** ⇒ **整批一个 op 都不执行**
        #      （判据：抛出的原因里点名属性；且**没调用 `set_properties` / `recompile`**）。
        _stub, _seen = _mk_stub({
            "exists": True,
            "get_expressions": [{"refPath": "/Game/Preflight/M_X.M_X:Expression0",
                                 "name": "Expression0"}],
            "list_properties": [{"name": "parameterName"}],
        })
        # ⚠ 2026-10-04：`_run_material_ops()` 的 `dry_run` 形参已随阶段五的演练一并删除
        #   （用户指令「把演练这一步删掉，不需要演练」）—— 这里少传最后一个位置参数。
        #   ⚠ 断言的判据**一条都没放松**：仍然是"整批校验没过 ⇒ 一个 op 都不执行"。
        _G["call_official"] = _stub
        _op_rows, _op_bad, _op_rb, _op_calls = _aio5.run(mod["_run_material_ops"](
            _fake_ctx, "/Game/Preflight/M_X",
            [{"op": "set_node", "node": "Expression0", "properties": {"NotAProperty": 1}}]))
        _ok_op_prop = bool(_op_bad and not _op_rows
                           and "set_properties" not in _seen and "recompile" not in _seen)
        check("⑫e `ops`：`set_node` 的属性名不在 `list_properties` 返回里 ⇒ **整批一个 op 都不执行**",
              bool(_ok_op_prop), f"bad={_op_bad[:1]} rows={len(_op_rows)} calls={_seen}")

        # ⑫f `ops`：`add_node` 的 `class` **不在 `list_expression_classes` 返回里** ⇒ 拒收
        _stub, _seen = _mk_stub({
            "exists": True,
            "get_expressions": [],
            "list_expression_classes": [{"refPath":
                                         "/Script/Engine.MaterialExpressionScalarParameter",
                                         "name": "MaterialExpressionScalarParameter"}],
        })
        _G["call_official"] = _stub
        _op_rows2, _op_bad2, _op_rb2, _op_calls2 = _aio5.run(mod["_run_material_ops"](
            _fake_ctx, "/Game/Preflight/M_X",
            [{"op": "add_node", "class": "MaterialExpressionTextureSample", "as": "tex"}]))
        _ok_op_cls = bool(_op_bad2 and not _op_rows2 and "add_expression" not in _seen)
        check("⑫f `ops`：`add_node` 的 `class` 不在 `list_expression_classes` 返回里 ⇒ **拒收**"
              "（不自己拼 `/Script/Engine.…`）",
              bool(_ok_op_cls), f"bad={_op_bad2[:1]} rows={len(_op_rows2)} calls={_seen}")

        # ⑫g 工具总数**仍是 20**（这一版四档都挂在现有两件工具上，**不新造第 21 件**）
        _n_tools = len(names)
        _ok_count = (_n_tools == 20)
        check("⑫g 工具总数仍是 **20**（阶段五四档都挂在 `apply_surfaces` / `create_surfaces` 上）",
              bool(_ok_count), f"实际={_n_tools}")

        # ⑫h **分派不能掉**（2026-10-04 晚**复查代码时抓到的真 bug 的回归**）：
        #   `create_surfaces()` 里 `tune` 那三行原来是**裸的**（前面没有 `if mode_key == "tune":`）
        #   ⇒ 它对**每一档**都先跑一遍 `tune()` 并 `return`：`create` 撞 `tune` 的空表拒收、
        #   `ops` 永远到不了 —— **两档全废**，而报错看起来只是"tune 拒收"（很难想到是分派掉了）。
        #   ⚠ 这里**用源码结构钉住**而不是离线驱动整支工具：走真入口会经 `_surfaces_repo()`
        #   落到**真盘**（台账 / 清单），预检不该写工作区。判据：`tune` 的调用必须包在
        #   `if mode_key == "tune":`（缩进更深）里，且 `ops` / `create` 的调用都在它**之后**。
        _src_cs = MAIN_PY.read_text(encoding="utf-8")
        _body_cs = _src_cs.split("async def create_surfaces(", 1)[-1].split("@mcp.tool()", 1)[0]
        _bl_cs = _body_cs.splitlines()

        def _line_at(pred, start=0):
            for _i in range(start, len(_bl_cs)):
                if pred(_bl_cs[_i]):
                    return _i
            return -1

        _i_guard = _line_at(lambda s: s.strip() == 'if mode_key == "tune":')
        # ⚠ 判据别判**排版**（2026-10-07 改）：原来找的是 `.tune(repo`（要求 `repo` 与 `.tune(` 同一行）——
        #   我把那次调用拆成多行（`.tune(` 换行后写 `repo, tune,`）就**假红**了一次。
        #   这条钉的**实质**是"那次调用在 `if mode_key == "tune":` 里面"⇒ 用**缩进**判（下面那条），
        #   函数名只认 `.tune(`。**力量没减**：裸调用是 4 空格缩进，照样被抓。
        _i_tune = _line_at(lambda s: ".tune(" in s)
        _i_ops = _line_at(lambda s: s.strip() == 'if mode_key == "ops":')
        _i_create = _line_at(lambda s: ".create(repo" in s)
        _ok_disp = bool(0 <= _i_guard < _i_tune < _i_ops < _i_create
                        and _bl_cs[_i_tune].startswith("        ")
                        and _bl_cs[_i_create].startswith("    ")
                        and not _bl_cs[_i_create].startswith("        "))
        check("⑫h `create_surfaces()` 的分派：`tune` 包在 `if mode_key == \"tune\":` 里、"
              "`ops` / `create` 在它之后（**裸 tune 会让 `create` / `ops` 两档全废**）",
              bool(_ok_disp),
              f"tune_guard={_i_guard} tune={_i_tune} ops={_i_ops} create={_i_create}")

        # ⑫i **第一步的搜不到规则（2026-10-04 晚用户口径）**：
        #   原话：「是白膜还是保留原来的，搜不到材质或者材质实例就标记待自建，
        #          不是白膜的问完没有就标记为无，毕竟别人建好的资产我们也不可能做材质或者材质实例」
        #   离线钉四件事（全离线：一条假资产清单 + 一个不碰官方的桩）：
        #     ① 白膜搜不到 ⇒ `missing_self_build`（**不是"问用户"**）+ 一个**提议落点**；
        #     ② `none`（无）**非白膜**行 ⇒ 允许；
        #     ③ **白膜**标 `none` ⇒ 拒收（白膜该自建）；
        #     ④ `none` 却带着路径 ⇒ 拒收（自相矛盾）。
        _sv_i = importlib.import_module("surfaces")
        _doc_fake = {"items": [{"element": "preflightbox", "element_key": "preflightbox",
                                "is_placeholder": True, "asset_path": ""}]}
        _stub_i, _seen_i = _mk_stub({})
        _repo_i = _sv_i.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": _stub_i(
                None, tool_name, arguments, toolset),
            # ⚠ 注入**生产那一份**匹配实现（阶段一 `match_assets_by_keyword`）——
            #   预检里也走同一条路子，才测得到"白膜搜不到 ⇒ 待自建"这条真判据。
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            load_json=lambda p: _doc_fake)
        _rep_i = _aio5.run(_sv_i.probe(_repo_i, max_per_element=3))
        _row_i = _rep_i.probe_rows[0]
        _ok_i_self = bool(_row_i.status == _sv_i.MAT_SELF_BUILD
                          and _row_i.instance == ""
                          and _row_i.material == "/Game/UEMCP/Materials/M_Preflightbox"
                          and _row_i.material == _sv_i.proposed_self_build_path("preflightbox"))
        _mk_i = mod["MaterialConfirmItem"]
        _none_ok = [_mk_i(element_key="house", label="住宅", target="/Game/Preflight/house1",
                          status="none")]
        _none_white = [_mk_i(element_key="road", label="车行道", target="whitebox", status="none")]
        _none_path = [_mk_i(element_key="house", label="住宅", target="/Game/Preflight/house1",
                            status="none", instance="/Game/Preflight/MI_X")]
        _ri1, _bi1 = _sv_i.validate_rows(_none_ok, "确实没有")
        _ri2, _bi2 = _sv_i.validate_rows(_none_white, "确实没有")
        _ri3, _bi3 = _sv_i.validate_rows(_none_path, "确实没有")
        _ok_i_none = bool(not _bi1 and _ri1 and _ri1[0].status == _sv_i.MAT_NONE)
        _ok_i_white = bool(_bi2)
        _ok_i_path = bool(_bi3)
        # 照阶段一补的两样也要钉住（2026-10-04 晚对比之后）：
        #   ① 每行都带**要原样问用户的话**（`ElementPlan.question` 同款）；② **缺口清单**。
        _ok_i_q = bool(_row_i.question and "M_Preflightbox" in _row_i.question)
        _ok_i_gap = bool(_sv_i.manifest_gaps({"a", "b"}, {"a", "b", "c"}) == ["c"]
                         and _sv_i.manifest_gaps({"a"}, {"a"}) == [])
        check("⑫i 第①~③步（用户 2026-10-04 晚口径）：**白膜搜不到 ⇒ 待自建 + 提议落点**；"
              "**非白膜可记「无」** + **白膜不许记「无」** + **「无」不许带路径**；"
              "＋照阶段一：**每行带 `question`** + **`manifest_gaps()` 算缺口**",
              bool(_ok_i_self and _ok_i_none and _ok_i_white and _ok_i_path
                   and _ok_i_q and _ok_i_gap),
              f"self={_ok_i_self} none={_ok_i_none} white={_ok_i_white} path={_ok_i_path} "
              f"q={_ok_i_q}({_row_i.question[:40]!r}) gap={_ok_i_gap} "
              f"row={(_row_i.status, _row_i.material)} "
              f"b={(_bi1[:1], _bi2[:1], _bi3[:1])}")

        # ⑫j **搜到多个 ⇒ 交给用户自己确认**（用户 2026-10-04 晚原话：「搜到多个让用户自己确认」）：
        #   白膜行在材质库里匹到 **2 个**候选时，`probe` 必须落 `pending_user`（**不许替他挑第一个**），
        #   并把两个候选都列出来。这条闸防的是"工具自己挑了一块、用户根本不知道还有第二个"。
        _inv_fake = {"material_instance": ["/Game/Preflight/MI_Preflightbox_A",
                                           "/Game/Preflight/MI_Preflightbox_B"]}

        # ⚠ `library_inventory` 在 `probe()` 里是**被 await 的**（`inventory, used = await
        #   repo.library_inventory()`）—— 所以这个桩**必须是 async 函数**。
        #   2026-10-04 晚实跑预检就栽在这儿：写成同步 lambda 会报
        #   `TypeError: object tuple can't be used in 'await' expression`（⑫ 那一组整组红）。
        async def _inv_j():
            return _inv_fake, 0

        _stub_j, _seen_j = _mk_stub({"get_asset_class": "MaterialInstanceConstant"})
        _repo_j = _sv_i.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": _stub_j(
                None, tool_name, arguments, toolset),
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            load_json=lambda p: _doc_fake,
            library_inventory=_inv_j)
        _rep_j = _aio5.run(_sv_i.probe(_repo_j, max_per_element=5))
        _row_j = _rep_j.probe_rows[0]
        _ok_j_ask = bool(_row_j.status == _sv_i.MAT_PENDING
                         and len(_row_j.candidates) == 2
                         and _row_j.instance == "" and _row_j.material == "")
        # 照阶段一 `plan_assets` 的两样（**第四轮**对比补的）：
        #   ① 问题话术里要有"**存在但名字对不上 ⇒ 给路径，我来改名**"这条出路（配 `rename_assets`）；
        #   ② 候选带**结构化**明细（`matched_by` / `matched_segment`）——"为什么命中"是字段。
        _ok_j_esc = "改名" in _row_j.question
        _ok_j_detail = bool(len(_row_j.candidates_detail) == 2
                            and all(d.get("matched_by") == "资产名"
                                    and d.get("path") for d in _row_j.candidates_detail))
        check("⑫j `probe`：白膜行**同一栏搜到 2 块实例 ⇒ `pending_user`（交用户自己确认）**，"
              "**不自动挑第一个**、两个候选都列出来；＋照阶段一：问题里带"
              "「**名字对不上就给路径、我来改名**」＋候选带**结构化明细**",
              bool(_ok_j_ask and _ok_j_esc and _ok_j_detail),
              f"status={_row_j.status} cands={len(_row_j.candidates)} "
              f"inst={_row_j.instance!r} mat={_row_j.material!r} "
              f"esc={_ok_j_esc} detail={_ok_j_detail}")

        # ⑫k **清单是两栏**（用户 2026-10-04 晚：「材质清单是两栏，一个是材质，一个是材质实例，
        #   你分不清这俩？」）—— 匹到 **1 块材质 + 1 块实例** 时**不是二选一**，而是
        #   **两栏各填一个**、状态 `found`、**不拿去问用户**。这条防的正是我犯过的错：
        #   把"材质和它的实例"当成"两个候选"让人挑。
        _inv_fake2 = {"material": ["/Game/Preflight/M_Preflightbox2"],
                      "material_instance": ["/Game/Preflight/MI_M_Preflightbox2"]}

        async def _inv_k():
            return _inv_fake2, 0

        def _cls_k(arguments):
            # 资产名以 `M_` 开头 ⇒ 材质；否则当实例（预检桩，够用就行）
            name = str((arguments or {}).get("asset_path") or "").rsplit("/", 1)[-1]
            return "Material" if name.startswith("M_") else "MaterialInstanceConstant"

        _stub_k, _seen_k = _mk_stub({"get_asset_class": _cls_k})
        _repo_k = _sv_i.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": _stub_k(
                None, tool_name, arguments, toolset),
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            load_json=lambda p: {"items": [{"element": "preflightbox2",
                                            "element_key": "preflightbox2",
                                            "is_placeholder": True, "asset_path": ""}]},
            library_inventory=_inv_k)
        _rep_k = _aio5.run(_sv_i.probe(_repo_k, max_per_element=5))
        _row_k = _rep_k.probe_rows[0]
        _ok_k_two = bool(_row_k.status == _sv_i.MAT_FOUND
                         and _row_k.material == "/Game/Preflight/M_Preflightbox2"
                         and _row_k.instance == "/Game/Preflight/MI_M_Preflightbox2"
                         and _row_k.instance_status == _sv_i.INST_IS_INSTANCE)
        # 照阶段一：`found` 的行也带一句**要原样问用户的话**（"就用这块吗"），不是留空
        _ok_k_q = bool(_row_k.question and "MI_M_Preflightbox2" in _row_k.question)
        # 照阶段一 `ElementPlan.assets`：候选带**结构化**明细（"为什么命中"是字段）
        _ok_k_detail = bool(len(_row_k.candidates_detail) == 2
                            and all(d.get("matched_by") == "资产名"
                                    for d in _row_k.candidates_detail))
        check("⑫k `probe`：**1 块材质 + 1 块实例 ⇒ 两栏各填一个、状态 `found`（不问用户）**"
              " ＋ 该行**也带 `question`** ＋ 候选带**结构化明细**（`candidates_detail`，照 `FoundAsset`）",
              bool(_ok_k_two and _ok_k_q and _ok_k_detail),
              f"status={_row_k.status} mat={_row_k.material!r} inst={_row_k.instance!r} "
              f"istatus={_row_k.instance_status!r} q={_row_k.question[:40]!r} "
              f"detail={_row_k.candidates_detail}")

        # ⑫m **待自建行的「配方闸」在 `confirm` 里**（照阶段一 `placeholder_errors()`：
        #    **闸在用户正看着的那一步** —— 白膜 / 待自建行填错就**当场拒收**，
        #    别让他点完头、走到第 ⑤ 步才撞墙）。
        #    这一条驱动的是**整支 `confirm()`**：桩里故意不给配方 ⇒ 必须拒收、且**一个字节都不写**。
        _wrote_m: list[str] = []

        async def _inv_m():
            return ({}, 0)

        def _load_m(p):
            ps = str(p)
            if ps.endswith("surface_materials.json"):
                return {"create": {}}          # ⚠ **故意没有配方**
            if ps.endswith("asset_list.json"):
                return {"items": []}
            return None

        _stub_m, _seen_m = _mk_stub({"exists": True, "list_parameters": []})
        _repo_m = _sv_i.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": _stub_m(
                None, tool_name, arguments, toolset),
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            category_cn=lambda k: k,
            load_json=_load_m,
            save_json=lambda p, d: _wrote_m.append(str(p)),
            archive_json=lambda p, x: "",
            library_inventory=_inv_m)
        try:
            _aio5.run(_sv_i.confirm(
                _repo_m,
                [_mk_i(element_key="preflightbox3", label="白膜三", target="whitebox",
                       status=_sv_i.MAT_SELF_BUILD,
                       material="/Game/UEMCP/Materials/M_Preflightbox3")],
                "确实没有，你建吧"))
            _ok_m_gate, _why_m = False, "没拒收"
        except mod["ToolError"] as exc:
            _ok_m_gate, _why_m = ("配方" in str(exc)), str(exc)[:80]
        _ok_m_nowrite = not _wrote_m
        check("⑫m `confirm`：**待自建行缺配方 ⇒ 当场拒收**（照阶段一「闸在用户正看着的那一步」），"
              "且**一个字节都没写**",
              bool(_ok_m_gate and _ok_m_nowrite),
              f"gate={_ok_m_gate} wrote={_wrote_m} why={_why_m}")

        # ⑫n **`exists()` 的判法照阶段一**（2026-10-04 晚第三轮对比抓到的真错）：
        #   阶段一是 `exists is True or str(exists).lower() == "true"`；我原来写的是 `bool(...)`
        #   —— ⚠ **`bool("false")` 在 Python 里是 `True`**！官方回**字符串** `"false"` 时，
        #   一个**不存在的资产**会被判成"在"，而这道闸正是"验不过就整批拒收"。所以钉死：
        #   `"false"` ⇒ **不在**；`"true"` / `True` / `1` ⇒ 在；`None` / `0` ⇒ 不在。
        _case_n: list[tuple[Any, bool]] = []

        async def _exists_probe(value: Any) -> bool:
            async def _o(tool_name, arguments, toolset=""):
                return value
            _repo_n = _sv_i.SurfacesRepo(official=_o,
                                         match_assets=mod["match_assets_by_keyword"],
                                         fingerprint=mod["library_fingerprint"])
            return await _sv_i.asset_exists(_repo_n, "/Game/Preflight/X")

        _cases = {"false": False, "FALSE": False, "true": True, "True": True,
                  True: True, 1: True, None: False, 0: False, "": False}
        for _val, _want in _cases.items():
            _case_n.append((_val, _aio5.run(_exists_probe(_val)) == _want))
        _ok_n = all(ok for _v, ok in _case_n)
        check("⑫n `asset_exists()` 照阶段一：**字符串 `\"false\"` 必须判成「不在」**"
              "（`bool(\"false\")` 是 `True` —— 那就是闸门失守）；`\"true\"` / `True` / `1` 判「在」",
              bool(_ok_n),
              "；".join(f"{v!r}→{r}" for v, r in _case_n))

        # ⑫o **签字指纹的粒度照阶段一**（同一轮抓到的第二处）：阶段一是
        #   `library_fingerprint()`（**sha256，把每条路径都算进去**）；我原来只算"每类数量"
        #   ⇒ **同类里一增一删、数量不变就判不出过期**。这里就构造这种情形：数量一样、路径变了。
        _inv_a = {"material_instance": ["/Game/Preflight/MI_A", "/Game/Preflight/MI_B"]}
        _inv_b = {"material_instance": ["/Game/Preflight/MI_A", "/Game/Preflight/MI_C"]}
        _digest_a, _counts_a = mod["library_fingerprint"](_inv_a)

        async def _inv_o():
            return _inv_b, 0

        _fake_list_o = {"items": [{"element_key": "x", "instance_status": "is_instance"}]}

        def _load_o(p):
            ps = str(p)
            if ps.endswith("material_list.json"):
                return _fake_list_o
            if ps.endswith("material_snapshot.json"):
                return {"hash": _digest_a, "counts": _counts_a}
            return None

        _repo_o = _sv_i.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": None,
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            load_json=_load_o,
            library_inventory=_inv_o)
        _stale_o = _aio5.run(_sv_i.snapshot_stale(_repo_o))
        check("⑫o `snapshot_stale()`：**同类里换了一个资产（数量不变、路径变了）⇒ 判「过期」**"
              "（指纹是路径级的 sha256，照阶段一 `library_fingerprint`）",
              bool(_stale_o is True),
              f"stale={_stale_o!r}（数量：签字 {_counts_a} / 现在 "
              f"{ {k: len(v) for k, v in _inv_b.items()} }）")

        # ⑬ 阶段五第 ⑤ 步（**2026-10-04 晚用户更正后的新行为**）——**有材质、缺实例 ⇒ 派生实例**，
        #    且**把父级的参数现值提升为新实例的覆盖**（全离线：一套假官方调用 + 假清单）。
        #    为什么要这条：这正是用户当场纠正的那一步（原实现是"照着清单建材质"，
        #    没有"派生实例 + 数值提升"）；它**一次都没真跑过**，所以至少要有一条离线回归钉住
        #    它调的是**哪些官方工具**、参数是**从父级读、往实例写**。
        _sv13 = importlib.import_module("surfaces")
        _seen13: list[str] = []

        async def _stub13(ctx, tool_name, arguments, toolset=None, check_link=True):
            _seen13.append(str(tool_name))
            if tool_name == "exists":
                # 父级（那块裸材质）存在；要派的实例**还不存在** ⇒ 走"创建"那条路
                return not str(arguments.get("path") or "").endswith("MI_M_Preflight")
            if tool_name == "create":
                return {"refPath": "/Game/Preflight/MI_M_Preflight.MI_M_Preflight"}
            if tool_name == "list_parameters":
                return [{"name": "Tiling", "type": "Scalar"}]
            if tool_name == "get_scalar_parameter":
                return 1.0
            if tool_name == "set_scalar_parameter":
                return None
            if tool_name == "get_properties":
                # ⚠ 2026-10-07 加：`create` 现在**写完参数节点要读回**（修"12 个全 ok、其中 6 个是垃圾"）。
                #   这一档（`from_material` ⇒ 只派生实例）**不会**走到参数节点那条路，所以本来不需要它；
                #   加在这儿是为了**别让将来某天多一行材质、预检就红在一个"桩没准备返回值"上**。
                return {"returnValue": {"parameterName": "Tiling", "defaultValue": 1.0}}
            raise mod["ToolError"](f"（预检桩）⑬ 没给 `{tool_name}` 准备返回值")

        # ⚠ `official` 的形参名要**显式写成三个**（`tool_name` / `arguments` / `toolset`）：
        #   调用方（`surfaces.asset_exists()` 等）把 `toolset` 当**关键字**传，
        #   写成 `lambda t, a, toolset=""` 的话，`arguments` 会接不到值 ⇒
        #   `TypeError: missing 1 required positional argument: 'arguments'`
        #   —— **2026-10-04 真跑预检时就栽在这儿（⑬ 唯一一条红）**，记在这儿免得再犯。
        _repo13 = _sv13.SurfacesRepo(
            official=lambda tool_name, arguments, toolset="": _stub13(
                None, tool_name, arguments, toolset),
            match_assets=mod["match_assets_by_keyword"],
            fingerprint=mod["library_fingerprint"],
            load_json=lambda p: ({"items": [{
                "element_key": "road", "label": "车行道", "target": "whitebox",
                "material": "/Game/Preflight/M_Preflight",
                "instance": "", "instance_status": _sv13.INST_FROM_MATERIAL,
                "parent": "/Game/Preflight/M_Preflight", "status": _sv13.MAT_FOUND,
            }]} if str(p).endswith("material_list.json") else None),
            save_json=lambda p, d: None,
            archive_json=lambda p, x: "",
            to_object_path=lambda s: f"{s}.{s.rsplit('/', 1)[-1]}",
        )
        _rep13 = _aio5.run(_sv13.create(_repo13))
        _row13 = next((r for r in _rep13.rows if r.kind == "instance"), None)
        _ok13_derived = bool(_row13 and _row13.status == "ok"
                             and _row13.path.endswith("MI_M_Preflight")
                             and _row13.parent.endswith("M_Preflight"))
        _ok13_promoted = bool(_row13 and "Tiling" in (_row13.promoted or {}))
        _ok13_tools = ("create" in _seen13 and "set_scalar_parameter" in _seen13
                       and "create_material" not in _seen13)   # 缺实例**不建材质**，只派生
        check("⑬ 第 ⑤ 步：**有材质缺实例 ⇒ 只派生实例**（父级=那块材质）+ "
              "**父级参数现值提升为新实例覆盖** + **不建材质**",
              bool(_ok13_derived and _ok13_promoted and _ok13_tools),
              f"derived={_ok13_derived} promoted={_ok13_promoted} tools={_ok13_tools} "
              f"row={_row13 and (_row13.path, _row13.status)} seen={_seen13}")
    except BaseException as exc:                      # noqa: BLE001
        # ⚠ 2026-10-04 加：这一组失败时，**只报异常类型 + 消息是定位不了的** ——
        #   实测那次报 `AttributeError: 'object' object has no attribute 'request_context'`，
        #   只说明"假 ctx（`object()`）太弱"，**没说清是哪个子项、哪一行摸的**。
        #   所以这里把**完整回溯**打出来（含 preflight 自己的行号 + main.py 的行号）。
        import traceback as _tb5
        print(_tb5.format_exc(), end="")
        check("⑫ 阶段五（材质清单 + probe/confirm/asset_slots/tune/ops 五道闸）能离线验收",
              False, f"{type(exc).__name__}: {exc}")
    finally:
        _G["call_official"] = _real_call_official     # ⚠ 原样还原：预检不留副作用

    # --- ⑭ 2026-10-07 补的三处（**纯源码 pin**：不落盘、不碰 UE、不改任何状态）----------------------
    # 为什么这里用**源码 pin** 而不是行为测：这三处的"行为"都要真写一份 plan / 真跑一次落位
    #   （会动 `views/` 与验收台账）—— 预检**不许**留下那种痕迹（与 ⑪ 的顾虑同源）。
    #   所以钉的是「**这几条判据还在、旧的坏形状没回来**」：
    #     ① 缺口 #2：`generate_plan` 里那道「纪元 2 + 没给 `patch` ⇒ 拒收」的闸；
    #     ② 缺口 #3：`execute_build` 报文的凭据改成**三处回退**（`_evidence_quote`），
    #        且**旧的坏形状**（`str(_quote_ok or user_quote).strip()` —— 会打印一对空引号）**必须消失**；
    #     ③ `z_m` = **底面标高**（2026-10-07 用户定案 B）：资产 `z_base_cm = float(a["z_m"]) * 100.0`、
    #        白膜 `z_m = _base_m + height_m / 2.0`；且旧口径（`z_base_cm = z_cm - lift_cm`）**必须消失**。
    # ⚠ **它证明不了行为对**（那要真跑）—— 别把这条绿读成"验过了"。
    try:
        import inspect as _inspect6
        _gp_src = _inspect6.getsource(mod["generate_plan"])
        _eb_src = _inspect6.getsource(mod["execute_build"])
        _cb_src = _inspect6.getsource(mod["_compose_build_rows"])
        _ok14_patch = ("not patch and planning.active_epoch() >= 2" in _gp_src)
        _ok14_quote = bool("_evidence_quote" in _eb_src
                           and "str(_quote_ok or user_quote).strip()" not in _eb_src)
        _ok14_zm = bool('z_base_cm = float(a["z_m"]) * 100.0' in _cb_src
                        and "z_m = _base_m + height_m / 2.0" in _cb_src
                        and "z_base_cm = z_cm - lift_cm" not in _cb_src)
        check("⑭ 2026-10-07 三处补丁还在（源码 pin · **不是行为验证**）："
              "纪元 2 必须用 `patch` / 报文凭据三处回退 / `z_m` = 底面标高",
              bool(_ok14_patch and _ok14_quote and _ok14_zm),
              f"patch闸={_ok14_patch} 凭据回退={_ok14_quote} z_m底面={_ok14_zm}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑭ 2026-10-07 三处补丁还在（源码 pin · **不是行为验证**）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑮ 2026-10-07 阶段五那批修复的**便宜回归**（纯函数行为 + 两处源码 pin）----------------------
    # 为什么这组能真验行为：`linear_color_value()` / `_ref_path()` / `WHITEBOX_VERTICAL` 都是**纯的**
    #   （不碰 UE、不写盘、无副作用）⇒ 直接断言返回值就够了，不需要假官方调用。
    #   另两处（`_promote_values` 读哪个靶子、ops 写阶段的延迟白名单）只有在真跑里才算数
    #   ⇒ 那两条是**源码 pin**（钉住"判据还在、旧的坏形状没回来"），**证明不了行为对**。
    try:
        import inspect as _inspect7
        _sv = mod["_surfaces_module"]()
        _lc = _sv.linear_color_value
        _ok15_vec = (_lc([0.65, 0.06, 0.06, 1.0]) == {"r": 0.65, "g": 0.06, "b": 0.06, "a": 1.0})
        _ok15_passthru = bool(_lc(0.4) == 0.4 and _lc("M_X") == "M_X"
                              and _lc([1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]
                              and _lc(True) is True)
        _ok15_ref = bool(
            mod["_ref_path"]({"returnValue": {"expression": {"refPath": "/Game/X.Y"}}}) == "/Game/X.Y"
            and mod["_ref_path"]({"refPath": "/Game/A.B"}) == "/Game/A.B"
            and mod["_ref_path"]({"nope": 1}) == "")
        _pv_src = _inspect7.getsource(_sv._promote_values)
        _ok15_promote = bool("list_parameters(repo, inst)" in _pv_src
                             and 'repo.to_object_path(inst)}, "name": name}' in _pv_src
                             and 'repo.to_object_path(parent)}, "name": name}' not in _pv_src)
        _ops_src = _inspect7.getsource(mod["_run_material_ops"])
        _ok15_ops = bool(_ops_src.count("list_properties") >= 2 and "本批新加的节点" in _ops_src)
        _cr_src = _inspect7.getsource(_sv.create)
        _ok15_recipe = bool('it.get("params")' in _cr_src          # ④ 配方覆盖参数往下传了
                            and "linear_color_value(_pv)" in _cr_src
                            and "derived_instance_of(row)" in _cr_src)  # ⑤ 与 ⑥ 同一个输入
        # ⑤ 写后读回（create 那一档原来**没有**这道纪律 —— "12 个全 ok、其中 6 个是垃圾"能瞒过去）
        _ok15_readback = bool("prop_of(_back" in _cr_src and "get_properties" in _cr_src)
        # ② ops 的顺序 / 形状归一（**纯函数 ⇒ 真行为断言**）
        _np = mod["_normalize_node_props"]({"defaultValue": [0.65, 0.06, 0.06, 1.0],
                                            "parameterName": "BaseColor"})
        _ok15_norm = bool(list(_np.keys())[0] == "parameterName"       # 名提到最前
                          and _np["defaultValue"] == {"r": 0.65, "g": 0.06, "b": 0.06, "a": 1.0}
                          and "parameterName" not in mod["_normalize_node_props"]({}))
        # ⑦ refPath 寻址（纯函数 ⇒ 真行为断言）
        _lr = mod["_looks_like_ref"]
        _ok15_ref2 = bool(_lr("/Game/UEMCP/Materials/M_Car.M_Car:MaterialExpressionVectorParameter_0")
                          and not _lr("VectorParameter_0") and not _lr("")
                          and not _lr("/Game/NoDot") and not _lr("M_Car.M_Car"))
        # ⑤ 取字段（`get_properties` 那个套了几层的返回）—— 纯函数，两种形状都断言
        _po = _sv.prop_of
        _ok15_prop = bool(
            _po({"returnValue": {"parameterName": "BaseColor",
                                 "defaultValue": {"r": 1.0}}}, "defaultValue") == {"r": 1.0}
            and _po([{"name": "defaultValue", "value": [0.5, 0.5, 0.5, 1.0]}],
                    "defaultValue") == [0.5, 0.5, 0.5, 1.0]
            and _po({"a": 1}, "defaultValue") is None)
        # ⚠ **2026-10-08 改判据**（用户指令「赶紧改位置表结构增加Z」）：原来这里钉的是
        #   `WHITEBOX_VERTICAL["car"] == ("bottom", 0.0)` —— **那张表已删**（Z 现在只从 plan 每行的
        #   `z_m` 取）。新判据钉三件事（**纯源码/属性，不 import 规划层**，免得预检自己引入副作用）：
        #   ① main 里**没有**那个常量了（删干净，不留死表）；
        #   ② 阶段三白膜那一处**读的是行里的 `z_m`**（`float(w["z_m"])`）、**不再查表**
        #      （`WHITEBOX_VERTICAL.get(` 不出现）；
        #   ③ 阶段二的字段校验**缺 `z_m` 就拒收**（`_norm_placement` 里那句"缺 `z_m`"）。
        #   ⚠ **教训（当天真跑踩到）**：第一版判据写的是「`WHITEBOX_VERTICAL` 这个词不出现在
        #   `_compose_build_rows` 源码里」—— 而 `getsource` **连注释一起给**，我在注释里写了
        #   "删掉的是 `WHITEBOX_VERTICAL` 那张表" ⇒ **pin 自己把自己判红**。
        #   判据别去匹配"某个词出现过没有"，要匹配**真用它的那种写法**（`.get(` / `float(w["z_m"])`）。
        _compose_src15 = _inspect7.getsource(mod["_compose_build_rows"])
        _plan_py15 = MAIN_PY.parent / "planning" / "plan.py"
        _plan_src15 = (_plan_py15.read_text(encoding="utf-8")
                       if _plan_py15.exists() else "")
        _ok15_vz = bool(not hasattr(mod, "WHITEBOX_VERTICAL")
                        and 'float(w["z_m"])' in _compose_src15
                        and "WHITEBOX_VERTICAL.get(" not in _compose_src15
                        and "缺 `z_m`" in _plan_src15
                        and "每行必填" in _plan_src15)
        _label15 = ("⑮ 2026-10-07 阶段五那批修复（向量形状 / ops 顺序归一 / ops refPath 寻址 / "
                    "写后读回 / 配方覆盖参数）")
        check(_label15,
              bool(_ok15_vec and _ok15_passthru and _ok15_ref and _ok15_promote
                   and _ok15_ops and _ok15_recipe and _ok15_readback and _ok15_norm
                   and _ok15_ref2 and _ok15_prop and _ok15_vz),
              f"向量形状={_ok15_vec} 非4元不换={_ok15_passthru} 读回解包={_ok15_ref} "
              f"提升靶子({_ok15_promote}) ops白名单({_ok15_ops}) 配方覆盖({_ok15_recipe}) "
              f"写后读回({_ok15_readback}) 顺序归一({_ok15_norm}) refPath({_ok15_ref2}) "
              f"取字段({_ok15_prop}) Z只在行里({_ok15_vz})")
    except BaseException as exc:                      # noqa: BLE001
        check("⑮ 2026-10-07 阶段五那批修复（向量形状 / ops 顺序归一 / ops refPath 寻址 / "
              "写后读回 / 配方覆盖参数 / 车·货车坐路面）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑯ 2026-10-07 第二批（越界叠 rot / 派生落点认配方 / `none` 桶 / ops 两条）------------------
    # 前三条**都是纯函数**（`bounds_check` / `derived_instance_of` / `_surface_by_category`）⇒
    #   这里做的是**真行为断言**，不是 pin。后两条（ops 读回 connect、枚举只读一次）仍然是源码 pin。
    try:
        import inspect as _inspect8
        import types as _types8
        _pl3 = mod["_planning_modules"]()

        def _mk16(rot: float) -> dict:
            return {"world": {"center": [0, 0], "size": [70.0, 54.0],
                              "bounds": {"min": [-35.0, -27.0], "max": [35.0, 27.0]}},
                    "assets": [], "whiteboxes": [
                        {"element_key": "road", "label": "路", "shape": "cube", "height_m": 0.15,
                         "pos": [0.0, 0.0], "footprint_m": [22.0, 70.0], "rot_deg": rot,
                         "size_source": "预估"}]}

        # ① 越界：22×70、rot 90 ⇒ 世界占地 70×22 ⇒ **在界内**（原来会假报 8.00 m）；
        #    同一行 rot 0 ⇒ 真的出界 8 m（对照组，证明这条检查没被"改没了"）。
        _b90 = _pl3.bounds_check(_mk16(90.0))
        _b0 = _pl3.bounds_check(_mk16(0.0))
        _ok16_rot = bool(not (_b90.get("outside") or []) and (_b0.get("outside") or [])
                         and abs(float((_b0["outside"][0].get("over_m") or 0)) - 8.0) < 0.01)

        # ② 派生落点认配方表里那个键（原来只按父级目录起名 ⇒ 清单点的路径根本不存在）
        _recipes16 = {"/Game/UEMCP/Materials/MI_SakuraLeaves":
                      {"path": "/Game/UEMCP/Materials/MI_SakuraLeaves",
                       "parent": "/Game/Fab/Tree/realistic_tree/Materials/normal_leaves",
                       "params": {}, "flags": {}}}
        _sv16 = mod["_surfaces_module"]()
        _derived16 = _sv16.derived_instance_of(
            {"parent": "/Game/Fab/Tree/realistic_tree/Materials/normal_leaves"}, _recipes16)
        _ok16_derived = bool(_derived16 == "/Game/UEMCP/Materials/MI_SakuraLeaves"
                             # 不传配方表时行为与以前一致（按父级目录起名）
                             and _sv16.derived_instance_of(
                                 {"parent": "/Game/Fab/Tree/realistic_tree/Materials/normal_leaves"}
                             ).endswith("/Materials/MI_normal_leaves"))

        # ③ `none`（无）**不许**被算进 `missing`（那是"问过用户确实没有"，不是缺口）
        _f = _types8.SimpleNamespace
        _bycat16 = mod["_surface_by_category"]([
            _f(element_key="house", category="", status="found"),
            _f(element_key="road", category="", status="missing_self_build"),
            _f(element_key="sign", category="", status="none")])
        _b16 = (list(_bycat16.values())[0] if _bycat16 else {})
        _ok16_none = bool(_b16 and "none" in _b16
                          and "sign" in (_b16.get("none") or [])
                          and "sign" not in (_b16.get("missing") or [])
                          and "road" in (_b16.get("missing") or [])
                          and "house" in (_b16.get("found") or []))

        # ④ ops：`connect` 也读回（原来只核 `connect_output`）+ 枚举只读一次（校验那次带出来复用）
        _ops_src16 = _inspect8.getsource(mod["_run_material_ops"])
        _val_src16 = _inspect8.getsource(mod["_validate_material_ops"])
        _ok16_ops = bool("connected_expr" in _ops_src16
                         and "get_expression_inputs" in _ops_src16
                         and "calls, existing" in _val_src16
                         and "_get_existing_expressions(ctx, target)" not in _ops_src16)
        # ⑤ 重摆后自动补材质：**判据必须走 `surface_instance()`**（2026-10-07 实测 16 行受害 ——
        #    只认清单 `instance` 字段 ⇒ 待自建 / 缺实例那两类行**重摆之后静默失去材质**）。
        #    两条一起核：`_surface_hint_by_key()` 走 `surface_instance`；`execute_build` 那段
        #    **复用它**、不再自己内联一份（判据只许有一处）。
        _hint_src16 = _inspect8.getsource(mod["_surface_hint_by_key"])
        _eb_src16 = _inspect8.getsource(mod["execute_build"])
        _ok16_heal = bool("surface_instance(r, _recipes)" in _hint_src16
                          and "_surface_hint_by_key()" in _eb_src16
                          and "_instance_row(_mrow)" not in _eb_src16)
        # ⑥ `list_properties` 的**真实形状**（2026-10-07 实测：`{"returnValue": "<JSON 字符串>"}`，
        #    顶层键才是属性名）—— 原来当列表遍历 ⇒ 拿到字符串就**逐字符**迭代 ⇒ 属性名集合变成一堆
        #    单字 ⇒ **在材质图"已有的节点"上写属性全被判"属性不在返回里"**（那正是缺口 #2 的另一面）。
        #    纯函数 ⇒ 真行为断言。
        _pn = mod["_property_names"]
        _ok16_props = bool(_pn({"returnValue": '{"defaultValue":{}, "parameterName":{"type":"string"}}'})
                           == {"defaultValue", "parameterName"}
                           and _pn("这不是 JSON") == set()
                           and _pn([{"name": "parameterName"}]) == {"parameterName"}
                           and _pn(None) == set())
        _label16 = ("⑯ 2026-10-07 第二批（越界按 rot 转四角 / 派生落点认配方键 / `none` 单列一桶 / "
                    "ops 的 connect 也读回 + 枚举只读一次 / **自动补材质走 `surface_instance`** / "
                    "**`list_properties` 返回形状**）")
        check(_label16, bool(_ok16_rot and _ok16_derived and _ok16_none and _ok16_ops
                             and _ok16_heal and _ok16_props),
              f"越界rot={_ok16_rot} 派生落点={_ok16_derived}({_derived16}) "
              f"none桶={_ok16_none} ops={_ok16_ops} 补材质判据={_ok16_heal} 属性名解析={_ok16_props}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑯ 2026-10-07 第二批（越界按 rot 转四角 / 派生落点认配方键 / `none` 单列一桶 / "
              "ops 的 connect 也读回 + 枚举只读一次 / **自动补材质走 `surface_instance`** / "
              "**`list_properties` 返回形状**）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑰ 2026-10-07 ops 的**写批次整条路**（真跑撞出过 `NameError: name 'aliases'`）----------------
    # 为什么必须有它（血账）：⑫e / ⑫f 两条 ops 用例**都在校验阶段就被拒了** ⇒ 写阶段的 `set_node`
    #   分支**一次都没跑到** —— 于是那里把**校验层**的变量名（`aliases`）写进了**写阶段**
    #   （写阶段叫 `alias_ref`）也没人发现。真跑（拿 ops 去修 `M_Car` 的参数节点）撞出
    #   `NameError: name 'aliases' is not defined`，报文里只剩一句没有正文的
    #   `Error executing tool create_surfaces`（完整栈在 harness 日志里挖出来的）。
    # 这条用**假官方调用**把「add_node → set_node → connect_output → 自动 recompile → 读回」整条写路径跑一遍。
    try:
        _seen17: list[str] = []

        async def _stub17(ctx, tool_name, arguments, toolset=None, check_link=True):
            _seen17.append(str(tool_name))
            if tool_name == "exists":
                return True
            if tool_name == "get_expressions":
                return [{"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionScalarParameter_0",
                         "name": "MaterialExpressionScalarParameter_0"}]
            if tool_name == "list_expression_classes":
                return [{"refPath": "/Script/Engine.MaterialExpressionVectorParameter",
                         "name": "MaterialExpressionVectorParameter"}]
            if tool_name == "add_expression":
                return {"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionVectorParameter_9"}
            if tool_name == "list_properties":
                # ⚠ **照真实形状**：`{"returnValue": "<JSON 字符串>"}`，顶层键 = 属性名
                return {"returnValue": '{"parameterName":{"type":"string"},'
                                       ' "defaultValue":{"type":"object"}, "group":{"type":"string"}}'}
            if tool_name in ("set_properties", "connect_to_output", "connect_expressions",
                             "recompile", "delete_expression"):
                return None
            if tool_name == "get_property_input":
                return {"returnValue": {"expression": {
                    "refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionVectorParameter_9"}}}
            if tool_name == "get_properties":
                # ⚠ **照真实形状**：`get_properties` 回来的是一条 **JSON 字符串**
                #   （`call_official` 的 `parse_return()` 只解外层那个 `returnValue`）——
                #   值和写进去的一样（`set_node` 那条给了 [0.1, 0.2, 0.3, 1.0]）。
                return ('{"parameterName": "BaseColor", "defaultValue": '
                        '{"r": 0.1, "g": 0.2, "b": 0.3, "a": 1.0}}')
            raise mod["ToolError"](f"（预检桩）⑰ 没给 `{tool_name}` 准备返回值")

        _G["call_official"] = _stub17
        try:
            _r17, _bad17, _rb17, _c17 = _aio5.run(mod["_run_material_ops"](
                _fake_ctx, "/Game/Preflight/M_X", [
                    {"op": "add_node", "class": "MaterialExpressionVectorParameter", "as": "tex"},
                    {"op": "set_node", "node": "tex",
                     "properties": {"parameterName": "BaseColor",
                                    "defaultValue": [0.1, 0.2, 0.3, 1.0]}},
                    {"op": "connect_output", "from": "tex", "output": "RGB",
                     "property": "BaseColor"},
                ]))
        finally:
            _G["call_official"] = _real_call_official     # ⚠ 原样还原：预检不留副作用
        _ok17 = bool(not _bad17
                     and [r["op"] for r in _r17] == ["add_node", "set_node", "connect_output", "recompile"]
                     and all(r["status"] == "ok" for r in _r17)
                     and "接上了" in _rb17
                     # ⚠ **2026-10-07 用户定 A 案**：`set_node` 写完要**自动把值读回来核**
                     #   （官方 `get_properties`）—— 这两条钉住"真的读了、而且报了值"。
                     and "读回" in str((_r17[1] if len(_r17) > 1 else {}).get("detail") or "")
                     and "值核过 1 个" in _rb17)
        check("⑰ ops 写批次整条路（add_node → set_node → connect_output → 自动 recompile → 读回）",
              _ok17, f"bad={_bad17[:1]} rows={[(r['op'], r['status']) for r in _r17]} rb={_rb17[:100]}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑰ ops 写批次整条路（add_node → set_node → connect_output → 自动 recompile → 读回）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑱ 2026-10-07 第三批：`set_node` 的**写后读值**（用户定 A 案）------------------------------
    # 为什么必须有它：这一档原来只核「节点数 + 连线」，**值从来没被核过** ⇒「向量参数写不进去」
    #   那个 BUG 瞒过了 12 个 `status: ok`。⑰ 只覆盖"值一样"那一档（桩返回同一个值）；
    #   这里补**值对不上**那一档（桩返回别的颜色 ⇒ 这条必须判 `failed`，不许报 ok），
    #   外加"官方返回是 JSON 字符串时 `prop_of()` 认不认"（不认就永远只能报"没核成"）。
    try:
        _sv18 = mod["_surfaces_module"]()
        _vocab18 = bool(_sv18.prop_of('{"parameterName": "BaseColor"}', "parameterName") == "BaseColor"
                        and _sv18.prop_of("这不是 JSON", "parameterName") is None)

        async def _stub18(ctx, tool_name, arguments, toolset=None, check_link=True):
            if tool_name == "exists":
                return True
            if tool_name == "get_expressions":
                # ⚠ 名字要和下面 `set_node` 点的那个一致（节点名寻址靠这次枚举）
                return [{"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionVectorParameter_0",
                         "name": "MaterialExpressionVectorParameter_0"}]
            if tool_name == "list_properties":
                return {"returnValue": '{"parameterName":{"type":"string"},'
                                       ' "defaultValue":{"type":"object"}}'}
            if tool_name in ("set_properties", "connect_to_output", "recompile", "delete_expression",
                             "add_expression"):
                return None
            if tool_name == "get_properties":
                # 值**故意不一样**（写进去的是 0.1/0.2/0.3）⇒ 必须判 failed
                return ('{"parameterName": "BaseColor", "defaultValue": '
                        '{"r": 0.9, "g": 0.9, "b": 0.9, "a": 1.0}}')
            if tool_name == "get_property_input":
                return {"returnValue": {"expression": {
                    "refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionVectorParameter_0"}}}
            raise mod["ToolError"](f"（预检桩）⑱ 没给 `{tool_name}` 准备返回值")

        _G["call_official"] = _stub18
        try:
            _r18, _bad18, _rb18, _c18 = _aio5.run(mod["_run_material_ops"](
                _fake_ctx, "/Game/Preflight/M_X", [
                    {"op": "set_node", "node": "MaterialExpressionVectorParameter_0",
                     "properties": {"parameterName": "BaseColor",
                                    "defaultValue": [0.1, 0.2, 0.3, 1.0]}},
                ]))
        finally:
            _G["call_official"] = _real_call_official     # ⚠ 原样还原：预检不留副作用
        _ok18 = bool(not _bad18 and _vocab18
                     and _r18 and _r18[0]["op"] == "set_node"
                     and _r18[0]["status"] == "failed"
                     and "读回不是" in str(_r18[0].get("error") or "")
                     and "读回不是写进去的" in _rb18)
        check("⑱ `set_node` 写后读值：值对不上 ⇒ 判 failed（+ `prop_of` 认官方那条 JSON 字符串）",
              _ok18, f"vocab={_vocab18} rows={[(r['op'], r['status']) for r in _r18]} "
                     f"err={str(_r18[0].get('error') if _r18 else '')[:80]} rb={_rb18[:80]}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑱ `set_node` 写后读值：值对不上 ⇒ 判 failed（+ `prop_of` 认官方那条 JSON 字符串）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑲ 2026-10-07 `delete_unused`（用户指令「1」：官方 `delete_unused_expressions`）-------------
    # 为什么要有它：新接一个 op **必须有一条走完整条路的回归** ——「新通道没被实测覆盖」在这个项目里
    #   翻过车（缺口 #9 那个 `NameError` 就是写阶段的 `set_node` 一次都没跑到）。这里还要钉住新增的
    #   那句**按名字报"删掉了谁"**（只报"少了 3 个"读不出删的是不是你想删的；`delete_unused`
    #   删的范围本来就是官方按"接没接到输出"自己判的）。
    try:
        _seen19: list[str] = []

        async def _stub19(ctx, tool_name, arguments, toolset=None, check_link=True):
            _seen19.append(str(tool_name))
            if tool_name == "exists":
                return True
            if tool_name == "get_expressions":
                # ⚠ **两次调用给不同答案**：校验那次 = "批前"（2 个节点）；
                #   批次末尾读回那次 = "批后"（游离的那个没了）。
                if _seen19.count("get_expressions") <= 1:
                    return [{"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionScalarParameter_0",
                             "name": "MaterialExpressionScalarParameter_0"},
                            {"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionVectorParameter_9",
                             "name": "MaterialExpressionVectorParameter_9"}]
                return [{"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionScalarParameter_0",
                         "name": "MaterialExpressionScalarParameter_0"}]
            if tool_name in ("delete_unused_expressions", "recompile"):
                return None
            raise mod["ToolError"](f"（预检桩）⑲ 没给 `{tool_name}` 准备返回值")

        _G["call_official"] = _stub19
        try:
            _r19, _bad19, _rb19, _c19 = _aio5.run(mod["_run_material_ops"](
                _fake_ctx, "/Game/Preflight/M_X", [{"op": "delete_unused"}]))
        finally:
            _G["call_official"] = _real_call_official     # ⚠ 原样还原：预检不留副作用
        _ok19 = bool(not _bad19
                     and [r["op"] for r in _r19] == ["delete_unused", "recompile"]
                     and all(r["status"] == "ok" for r in _r19)
                     and "delete_unused_expressions" in _seen19
                     and "节点现在 1 个（批前 2 个）" in _rb19
                     and "删掉了" in _rb19
                     and "MaterialExpressionVectorParameter_9" in _rb19)
        check("⑲ `delete_unused`（官方一次删光游离节点）+ 读回**按名字**报删了谁",
              _ok19, f"seen={sorted(set(_seen19))} rows={[(r['op'], r['status']) for r in _r19]} "
                     f"rb={_rb19[:120]}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑲ `delete_unused`（官方一次删光游离节点）+ 读回**按名字**报删了谁",
              False, f"{type(exc).__name__}: {exc}")

    # --- ⑳ 2026-10-07 `layout`（用户指令「不是还有个整理节点的吗，也接到对应位置」）------------------
    # 两个要点：① 它走完整条路（官方 `layout_expressions` 真被调到）；
    #   ② **它不触发自动 `recompile`** —— 这条是刻意的设计（排版只改节点位置、跟 shader 无关；
    #   补重编译既白花时间，又可能因别的 shader 报错把一次"整理"整批判红）⇒ 用断言钉住，
    #   免得以后有人"顺手"把它并回 `_MAT_OPS_WRITE` 而没人发现。
    try:
        _seen20: list[str] = []
        _args20: list[dict] = []

        async def _stub20(ctx, tool_name, arguments, toolset=None, check_link=True):
            _seen20.append(str(tool_name))
            _args20.append(dict(arguments or {}))
            if tool_name == "exists":
                return True
            if tool_name == "get_expressions":
                return [{"refPath": "/Game/Preflight/M_X.M_X:MaterialExpressionScalarParameter_0",
                         "name": "MaterialExpressionScalarParameter_0"}]
            if tool_name == "layout_expressions":
                return None
            raise mod["ToolError"](f"（预检桩）⑳ 没给 `{tool_name}` 准备返回值")

        _G["call_official"] = _stub20
        try:
            _r20, _bad20, _rb20, _c20 = _aio5.run(mod["_run_material_ops"](
                _fake_ctx, "/Game/Preflight/M_X", [{"op": "layout"}]))
        finally:
            _G["call_official"] = _real_call_official     # ⚠ 原样还原：预检不留副作用
        _lay20 = next((a for t, a in zip(_seen20, _args20) if t == "layout_expressions"), {})
        _ok20 = bool(not _bad20
                     and [r["op"] for r in _r20] == ["layout"]        # ⚠ **没有**自动补的 recompile
                     and all(r["status"] == "ok" for r in _r20)
                     and "layout_expressions" in _seen20
                     and "recompile" not in _seen20
                     and "material_or_function" in _lay20
                     and "节点现在 1 个（批前 1 个）" in _rb20
                     # ⚠ 报文里必须**点名"位置没核"** —— 否则"读回核对：…"会被读成"排版成功了"
                     and "位置本档核不了" in _rb20)
        check("⑳ `layout`（官方自动排版）+ **不触发自动 recompile**（刻意设计，钉住）",
              _ok20, f"seen={_seen20} rows={[(r['op'], r['status']) for r in _r20]} rb={_rb20[:120]}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑳ `layout`（官方自动排版）+ **不触发自动 recompile**（刻意设计，钉住）",
              False, f"{type(exc).__name__}: {exc}")

    # --- ㉑ 2026-10-07 `tune` 的「切到那个物体视角」（用户实测反馈：改了看不见）--------------------
    # 由来（用户原话）：「你在调节为蓝色时**没有视角锁定到跑车**，我根本察觉不到你改了它，
    #   你说变成了蓝色我才去找到它」—— 第 ⑥ 步的"切视角"只接在 `apply` 上，`tune` 那条路**没有**。
    # 这条钉三件事：① 走官方 `FocusOnActors` + `SelectActors`；② Actor 从**搭建台账**按
    #   `element_key` 取（不按名字猜）；③ **没有台账 ⇒ 如实报"没切"**，不静默正常结束。
    try:
        _sv21 = mod["_surfaces_module"]()
        _seen21: list[tuple] = []
        _st21 = {"set": False}

        async def _stub21(_c, tool_name, arguments, toolset=None, check_link=True):
            _seen21.append((str(tool_name), dict(arguments or {})))
            if tool_name == "list_parameters":
                return [{"name": "BaseColor", "type": "vector"}]
            if tool_name == "get_vector_parameter":
                return ({"r": 0.05, "g": 0.25, "b": 0.85, "a": 1.0} if _st21["set"]
                        else {"r": 0.65, "g": 0.06, "b": 0.06, "a": 1.0})
            if tool_name == "set_vector_parameter":
                _st21["set"] = True
                return None
            if tool_name in ("FocusOnActors", "SelectActors"):
                return None
            raise mod["ToolError"](f"（预检桩）㉑ 没给 `{tool_name}` 准备返回值")

        def _repo21():
            return _sv21.SurfacesRepo(
                official=lambda tool_name, arguments, toolset="": _stub21(
                    None, tool_name, arguments, toolset),
                match_assets=mod["match_assets_by_keyword"],
                fingerprint=mod["library_fingerprint"],
                load_json=lambda p: ({"items": [{
                    "element_key": "road", "label": "车行道", "target": "whitebox",
                    "material": "/Game/Preflight/M_Road",
                    "instance": "/Game/Preflight/MI_X",
                    "instance_status": _sv21.INST_IS_INSTANCE,
                    "parent": "/Game/Preflight/M_Road", "status": _sv21.MAT_FOUND,
                }]} if str(p).endswith("material_list.json") else None),
                save_json=lambda p, d: None,
                archive_json=lambda p, x: "",
                to_object_path=lambda s: f"{s}.{s.rsplit('/', 1)[-1]}",
            )

        _ACT21 = "/Game/Preflight/Maps/L.PersistentLevel.cube_1"
        _tune21 = {"/Game/Preflight/MI_X": {"BaseColor": [0.05, 0.25, 0.85, 1.0]}}
        _r21a = _aio5.run(_sv21.tune(_repo21(), _tune21, focus=True, focus_max=3,
                                     ledger={"level": "", "rows": [
                                         {"element_key": "road", "label": "车行道", "actor": _ACT21}]}))
        _seen_a = list(_seen21)
        _foc_args = next((a for t, a in _seen_a if t == "FocusOnActors"), {})
        _foc_ok = bool("FocusOnActors" in [t for t, _a in _seen_a]
                       and "SelectActors" in [t for t, _a in _seen_a]
                       and any(str((x or {}).get("refPath") or "") == _ACT21
                               for x in (_foc_args.get("actors") or []))
                       and list(getattr(_r21a, "focused", []) or []) == [_ACT21])
        # ② 没有台账 ⇒ 不许静默：不调 FocusOnActors，且 warnings 里要说"没切视角"
        _seen21.clear()
        _st21["set"] = False
        _r21b = _aio5.run(_sv21.tune(_repo21(), _tune21, focus=True, ledger=None))
        _no_led_ok = bool("FocusOnActors" not in [t for t, _a in _seen21]
                          and not list(getattr(_r21b, "focused", []) or [])
                          and any("没切视角" in str(w) for w in (getattr(_r21b, "warnings", None) or [])))
        check("㉑ `tune` 调完**切到那个物体视角**（台账取 Actor）+ 没台账时如实报「没切」",
              bool(_foc_ok and _no_led_ok),
              f"聚焦={_foc_ok}（args={_foc_args}）无台账={_no_led_ok} "
              f"warns={list(getattr(_r21b, 'warnings', None) or [])[:1]}")
    except BaseException as exc:                      # noqa: BLE001
        check("㉑ `tune` 调完**切到那个物体视角**（台账取 Actor）+ 没台账时如实报「没切」",
              False, f"{type(exc).__name__}: {exc}")

    print()
    if FAILED:
        print(f"预检没过（{len(FAILED)} 项）：{FAILED}")
        print("**别重启**：先修。")
        return 1
    print("预检全绿：可以重启。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
