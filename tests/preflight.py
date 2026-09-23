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
#   & F:\MCP_Server\.venv\Scripts\python.exe tests\preflight.py
#   退出码 0 = 全绿 → 可以重启；非 0 = **别重启**，先修。
# =============================================================================

import asyncio
import runpy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "src" / "mcp_server" / "main.py"

# 当前应有的工具（改动工具面时**必须同步这里和 tests/check_tools.py**）
EXPECTED_TOOLS = [
    "confirm_assets", "confirm_elements", "confirm_plan",
    "generate_plan", "get_asset_list", "get_plan",
    "official_status", "plan_assets", "rename_assets",
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
        planning, plan_render = mod["_planning_modules"]()
        check("② 规划层惰性导入成功", True)
        print(f"        plan = {planning.__name__}")
        print(f"        render = {plan_render.__name__}")
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

    # --- ④ 阶段二那条链：几何 + 出图（内存里算，图渲到临时文件后删掉，不动 views/） ---
    try:
        params = planning.load_params()
        plan = planning.build_plan(params)
        bad = [c.get("name", "?") for c in plan.get("checks", []) if not c.get("ok")]
        check("④ 阶段二几何自检全过", not bad, f"没过的={bad}")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "preflight.png"
            plan_render.render(plan, params, out)
            check("④ 阶段二出图能跑通", out.exists() and out.stat().st_size > 0,
                  "PNG 没生成")

        # --- ⑤ 闸门指纹自洽：刚算出来的图，不该被判成"被手改过" ---
        # ⚠ 这是 2026-09-23 经 MCP 实测抓到的 bug 的回归检查：
        #   内存对象带 -0.0、落盘的是净化后的 0.0，两边哈希就会不等，
        #   于是对着一张刚出的图喊"文件被改过"。指纹计算必须内部净化。
        gate = planning.gate_check(params, plan)
        check("⑤ 闸门指纹自洽（刚出的图不该被判成'被改过'）",
              bool(gate["plan_hash_ok"]) and bool(gate["params_hash_ok"]),
              f"gate={gate}")
    except BaseException as exc:
        check("④ / ⑤ 阶段二几何、出图、闸门指纹能跑通", False,
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
