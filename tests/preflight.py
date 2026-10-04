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
#   缺的是编排）→ **20 个**。⚠ 该工具**尚未实测**，第一次真跑请先 `dry_run=true`。
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
                      "footprint_m": [10.0, 8.0], "rot_deg": 0.0, "scale": 1.0, "note": ""}]
        ok_boxes = [{"element_key": "road_fake", "label": "假路", "shape": "plane",
                     "height_m": 0.15,
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
    # 只验"读表 + 配置翻译"这两件**不碰 UE** 的事：
    #   · `_surface_creates()` 能把 `config/surface_materials.json` 的 `create` 段读出来；
    #   · 一条合法配置能翻译成"建哪几个参数节点、连哪几个输出"；
    #   · 一条**非法**配置（参数名不在允许集 / 值既不是数字也不是 4 元数组）**必须被拒**。
    # ⚠ 真跑（create_material / add_expression / connect_to_output 的属性名）**不敢在这里验** ——
    #   那要 UE 在线，而且那几个属性名**尚未实测**（见 main.py 顶部 MAT_PARAM_* 常量的说明）。
    try:
        _tbl, _tbl_note = mod["_surface_creates"]()
        _ok_tbl = isinstance(_tbl, dict)
        _item, _why = mod["_create_plan_item"](
            "/Game/Preflight/M_Road", {"BaseColor": [0.1, 0.1, 0.12, 1.0], "Roughness": 0.9})
        _ok_item = (not _why and isinstance(_item, dict)
                    and [p["kind"] for p in _item["params"]] == ["vector", "scalar"]
                    and _item["folder"] == "/Game/Preflight" and _item["name"] == "M_Road")
        _, _why_bad = mod["_create_plan_item"]("/Game/Preflight/M_Bad", {"NotAnOutput": 1.0})
        _ok_bad = bool(_why_bad)
        _, _why_bad2 = mod["_create_plan_item"]("/Game/Preflight/M_Bad2", {"BaseColor": [0.1, 0.2]})
        _ok_bad2 = bool(_why_bad2)
        check("⑧ 建材质：`create` 段读得出 + 合法配置翻译得对 + 非法配置被拒（真跑尚未实测）",
              bool(_ok_tbl and _ok_item and _ok_bad and _ok_bad2),
              f"table_ok={_ok_tbl} note={_tbl_note!r} item_ok={_ok_item} why={_why!r} "
              f"bad_ok={_ok_bad} bad2_ok={_ok_bad2}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑧ 建材质：`create` 段读得出 + 合法配置翻译得对 + 非法配置被拒（真跑尚未实测）",
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
    # ⚠ **不真建 `views/plan_v2.json`**（那是运行时状态 —— 预检不许碰，更不许留下痕迹）：
    #   把规划层的 `PLAN_V2_PATH` 临时指到一个 `tmp` 目录里的假路径上验三档，验完**原样还原**。
    #   ⚠ 解析的是 `active_plan_path()`（它在**调用时**读模块全局，所以能这样打桩）。
    # ⚠ **磁盘上没有 `plan_v2.json` 是正常状态**，这一项**不会**因此失败（断言的是解析行为）。
    try:
        import inspect as _inspect4
        import json as _json4
        import tempfile as _tempfile

        _pl2 = mod["_planning_modules"]()
        _orig_v2 = _pl2.PLAN_V2_PATH
        _ok_none = _ok_v2 = _ok_broken = False
        try:
            with _tempfile.TemporaryDirectory() as _td:
                _fake_v2 = Path(_td) / "plan_v2.json"
                _pl2.PLAN_V2_PATH = _fake_v2
                # ① v2 不在 ⇒ 纪元 1（`OUT_JSON`）
                _ok_none = bool(_pl2.active_plan_path() == _pl2.OUT_JSON
                                and _pl2.active_epoch() == 1)
                # ② v2 在且能解析 ⇒ 它、纪元 2
                _fake_v2.write_text(_json4.dumps({"unit": "m", "assets": [], "whiteboxes": []}),
                                    encoding="utf-8")
                _ok_v2 = bool(_pl2.active_plan_path() == _fake_v2
                              and _pl2.active_epoch() == 2)
                # ③ v2 在、但**读不动** ⇒ 退回 v1（"文件在、内容坏"由 `generate_plan` 那条闸拒收）
                _fake_v2.write_text("{ 这不是 JSON", encoding="utf-8")
                _ok_broken = bool(_pl2.active_plan_path() == _pl2.OUT_JSON
                                  and _pl2.active_epoch() == 1)
        finally:
            _pl2.PLAN_V2_PATH = _orig_v2        # ⚠ 原样还原：预检不留痕

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
        check("⑪ 纪元：活动 plan 解析对（v2 在⇒v2 / 不在或读不动⇒v1）+ 取原话**不按几何指纹筛**",
              bool(_ok_none and _ok_v2 and _ok_broken and _ok_quote),
              f"缺文件⇒v1={_ok_none} 有文件⇒v2={_ok_v2} 坏文件⇒v1={_ok_broken} "
              f"quote_ok={_ok_quote}")
    except BaseException as exc:                      # noqa: BLE001
        check("⑪ 纪元：活动 plan 解析对（v2 在⇒v2 / 不在或读不动⇒v1）+ 取原话**不按几何指纹筛**",
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
