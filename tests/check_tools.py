# =============================================================================
# 阶段一（资产确认）自检测试
#
# 规矩（沿用本项目一贯要求）：
#   - 不接受"我跑通了"，只认原始输出
#   - 分两组：第一组纯逻辑（不需要 UE，必须全过）；
#             第二组端到端（需要 UE + 官方 MCP，连不上就标 [跳过]）
#   - **危险动作只测预览路径**（rename_assets 只测 confirm=false）
#   - ⚠ 第二组**会写** catalog/（confirm_elements 落 elements.json；
#     confirm_assets 落 asset_list.json + library_snapshot.json）—— **它不是只读测试**。
#     所以 main() 先把整个 catalog/ 快照到临时目录，跑完**原样还原**。
#     2026-09-23 实测踩过：跑一次就把用户确认过的 14 元素 elements.json 覆盖成了测试用的 5 元素。
#
# 用法： & "<仓库根>\.venv\Scripts\python.exe" "<仓库根>\tests\check_tools.py"
# （不要用 uv run —— 实测在沙箱里会因缓存目录权限失败）
# =============================================================================

import asyncio
import logging
import math
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import mcp_server.main as M  # noqa: E402
from mcp import Client  # noqa: E402

# 把 httpx 的 INFO 噪音压掉，否则满屏 HTTP 日志看不清结果
logging.getLogger().setLevel(logging.WARNING)

FAILED: list[str] = []
PASSED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"[通过] {name}")
    else:
        FAILED.append(name)
        print(f"[失败] {name}   {detail}")


def skip(name: str, why: str = "") -> None:
    print(f"[跳过] {name}   {why}")


async def call_expect_error(c, tool: str, args: dict) -> tuple[bool, str]:
    """调一个**预期会被拒收**的工具 → `(是否被拒, 错误原文)`。

    ⚠ 为什么不直接 `try/except`（2026-09-24 实测 + 查 SDK 源码定案）：
      服务端 `raise ToolError(...)` 最后变成 **`CallToolResult(is_error=True)`**，
      错误原文在 `content` 的文本块里（`mcp/server/mcpserver/server.py:447`）；
      客户端 `call_tool` **把这个结果直接返回、不抛异常**（`mcp/client/client.py:813-814`）。
      所以"抛异常"这条路根本不会走到 —— 第一版就因此断言永假。
    ⚠ 反过来**也不能**把"content 里有文本"当成错误：正常结果也有文本。
      那样会让断言**恒真**（"等于没测"），比假红更坏。
      所以这里只认 `is_error`，拿不到就由调用方按"没被拒"处理。
    """
    try:
        result = await c.call_tool(tool, args)
    except Exception as exc:                      # noqa: BLE001 —— 兜底：万一某版会抛
        return True, f"(客户端抛异常) {exc}"

    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        text = "".join(str(getattr(b, "text", "") or "")
                       for b in (getattr(result, "content", None) or []))
        return True, text or str(getattr(result, "structured_content", "") or "")
    return False, "(没有 is_error 标记 —— 这个调用**没被拒**)"


# --- 第一组：纯逻辑（不需要 UE）-----------------------------------------------

async def test_pure() -> None:
    print("=== 第一组：纯函数（不需要 UE）===")

    # --- 工具面：确认注册的工具就是这几个（多一个都说明"清空"没做干净）---
    # ⚠ 2026-09-23：get_asset_list（取清单）→ 6 个；阶段二三个工具 → 9 个。
    # ⚠ 2026-09-25：阶段二验收加一个 request_plan_change → 10 个。
    # ⚠ 2026-09-26：阶段三加 check_build_target + generate_build_orders + execute_build
    #   → **13 个**（与 tests/preflight.py 的 EXPECTED_TOOLS 必须一致）。
    # ⚠ 2026-09-27：阶段四 `generate_layout` 加了又**当天整块回退**（阶段一资产表 + 阶段二位置表
    #   已是全局权威，那份只是派生件）。
    # ⚠ 2026-09-27（晚）：阶段五第 1 步 `apply_surfaces`（给白膜贴表面材质）→ **14 个**
    # ⚠ 2026-09-27（晚二）：第四阶段**不跳了**，实现成导出/交换 → `export_layout` + `check_exchange`
    #   → **16 个**（与 tests/preflight.py 的 EXPECTED_TOOLS 必须一致）。
    #   ⚠ **2026-10-04：阶段数从七个改成八个** —— 插入「**四 · 微调**」（**不新造工具**：复用
    #   `request_plan_change` / `generate_plan(patch)` / `execute_build(only_labels)`），
    #   上面那两个导出/交换的工具**从「四」挪到「八」**（**按需 · 往后排**，这一版没编排进流程）
    #   —— **工具面一个都没动，总数仍是 20**。
    # ⚠ 2026-09-29：阶段六扩范围（不只有灯光：天空/大气/天光/雾/云/后处理/时段）→
    #   `setup_environment` + `capture_preview` → **18 个**（与 tests/preflight.py 的
    #   EXPECTED_TOOLS 必须一致）。⚠ 阶段六最终定名「环境搭建」，出图归**阶段七** ——
    #   `capture_preview` 仍在工具面里（代码保留、归阶段七用），所以那一批是 18 个。
    # ⚠ 2026-09-30：阶段七只加「评估」这一个工具 —— `evaluate_layout`（落位对账：
    #   plan ↔ 搭建台账 ↔ 关卡现状，只读、不碰关卡、不改 plan）→ **19 个**
    #   （与 tests/preflight.py 的 EXPECTED_TOOLS 必须一致）。闭环**不新造工具**。
    # ⚠ 2026-10-04：阶段五加**第 0 步** `create_surfaces`（缺的材质从零建）→ **20 个**
    #   （与 tests/preflight.py 的 EXPECTED_TOOLS 必须一致）。
    # ⚠ 2026-10-04（第三批 · 合并）：`generate_build_orders` 从工具面撤掉（纯翻译件、不参与闸门）
    #   —— 内核改成内部件 `_orders_snapshot()`，由 `execute_build(dry_run=true)` 顺手落留痕件
    #   → **20 个**（与 tests/preflight.py 的 EXPECTED_TOOLS 必须一致）。
    result = M.mcp.list_tools()
    if asyncio.iscoroutine(result):
        result = await result
    tools = getattr(result, "tools", result)
    names = sorted(t.name for t in tools)
    check(
        "只注册了 20 个工具（阶段一 4 + 取清单 1 + 阶段二 4 + 阶段三 3（`check_build_target` / "
        "`adopt_user_edits` / `execute_build`）+ 阶段四（微调）0（**不新造工具**：复用 `request_plan_change` / "
        "`generate_plan(patch)` / `execute_build(only_labels)`）+ 阶段五 2 + "
        "阶段六 1（`setup_environment`）+ 阶段七 2（`capture_preview`，原挂在阶段六；"
        "`evaluate_layout`）+ 阶段八 2（导出/交换 · 按需 · 往后排：`export_layout` / `check_exchange`，"
        "原来记在阶段四）+ 自检 1）",
        names == ["adopt_user_edits", "apply_surfaces", "capture_preview",
                  "check_build_target", "check_exchange", "confirm_assets", "confirm_elements",
                  "confirm_plan", "create_surfaces", "evaluate_layout", "execute_build",
                  "export_layout", "generate_plan",
                  "get_asset_list", "get_plan",
                  "official_status", "plan_assets", "rename_assets",
                  "request_plan_change", "setup_environment"],
        f"实际={names}",
    )

    # --- 要求必须写在【工具描述】里 ---
    # ⚠ 2026-09-23 改：原先这两条断言 `M.mcp.instructions`，但实测本Agent（DSH 的 dsh-mcp-client）
    #   **不读** MCP 的 instructions —— `connect()` 的返回值（InitializeResult，instructions 在里面）
    #   连赋值都没有，全包搜 "instructions" 是 0 次命中。那段文字发出去也没人接，已删。
    #   真正到模型眼前的是 **tools/list**（工具名 + 描述 + schema），所以改成断言**描述**。
    #   这样以后谁把某条要求从描述里删掉，这里会立刻失败。
    # ⚠ 匹配前先把 markdown 加粗标记 `**` 剔除 —— 否则断言串一旦跨过 `**` 就会**假红**。
    #   实测踩过：描述里写的是「Agent**不许**用上下文里记得的旧表」，
    #   而断言找的是「不许用上下文里记得的旧表」，中间隔着一对 `**`，匹配不上。
    descs = {t.name: (getattr(t, "description", "") or "").replace("**", "") for t in tools}
    for tool_name, must_have in [
        ("official_status", "没有对应工具的能力"),        # 没实现的阶段要如实说「还没做」
        ("official_status", "不许存盘"),                  # 硬性禁止：不许存 UE 关卡 / 资产
        ("confirm_elements", "元素粒度由资产库定"),        # 粒度由资产库定，不按画面部件数（`**` 已剔除）
        ("get_asset_list", "开场先查状态"),                # 开口之前就要查，不是"动东西之前"
        ("get_asset_list", "不许用上下文里记得的旧表"),
        ("generate_plan", "单位是米"),                     # 单位错 100 倍那次
        ("get_plan", "开场先查状态"),
        ("get_plan", "阶段二不再自检"),                    # 2026-09-25：自检整段删除，描述须如实说
    ]:
        check(
            f"{tool_name} 的描述里有『{must_have}』",
            must_have in descs.get(tool_name, ""),
            f"实际描述前 80 字={descs.get(tool_name, '')[:80]!r}",
        )

    # --- token_match：词边界匹配（对冲官方的纯子串缺陷）---
    cases = [
        # (关键词, 文本, 期望)
        ("tree", "street_lamp_01_4k", False),   # 官方会命中这个，我们必须挡住
        ("tree", "realistic_tree", True),
        ("tree", "tree", True),
        ("house", "house1", True),               # 词 + 数字后缀
        ("house", "house2", True),
        ("house", "greenhouse", False),          # 不能是子串
        ("road", "Fine_American_Road_sjfnch0a", True),   # 文件夹名匹配的基础
        ("grass", "Uncut_Grass_oeeb70", True),
        ("grass", "Uncut_Grass_oeeb70", True),
        ("lamp", "streetLamp", True),            # 驼峰边界
        ("", "anything", False),
    ]
    bad = [(k, t, e, M.token_match(k, t)) for k, t, e in cases if M.token_match(k, t) != e]
    check("token_match 词边界匹配全对", not bad, f"错例={bad}")

    # --- num：负零/非有限值归一（这是实测踩出来的Agent拒收 bug）---
    nums = [
        (-0.0, "负零必须归一成 0.0"),
        (0.0, "正常零"),
        (float("inf"), "inf 必须归一"),
        (float("-inf"), "-inf 必须归一"),
        (float("nan"), "nan 必须归一"),
        ("12.345", "字符串数字要能转"),
    ]
    num_bad = []
    for v, why in nums:
        r = M.num(v)
        if r == 0 and math.copysign(1.0, r) < 0:
            num_bad.append((v, "仍是负零"))
        if not math.isfinite(r):
            num_bad.append((v, "仍非有限"))
    check("num() 负零/非有限值全部归一", not num_bad, f"错例={num_bad}")

    # 关键：归一后的结果必须能过 allow_nan=False（Agent就是这么拒收的）
    try:
        import json as _json
        _json.dumps([M.num(v) for v, _ in nums], allow_nan=False)
        check("num() 输出能通过 allow_nan=False（Agent不拒收）", True)
    except ValueError as exc:
        check("num() 输出能通过 allow_nan=False（Agent不拒收）", False, str(exc))

    # --- size_from_box ---
    box = {"isValid": True, "min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 100, "y": 50, "z": 25}}
    check("size_from_box 换算正确", M.size_from_box(box) == [100.0, 50.0, 25.0],
          f"实际={M.size_from_box(box)}")
    check("size_from_box 对无效盒子返回 None",
          M.size_from_box({"isValid": False}) is None)
    # 负零穿过 size_from_box 也要被归一
    zbox = {"isValid": True, "min": {"x": 0, "y": 0, "z": 0},
            "max": {"x": 0, "y": 0, "z": -0.0}}
    sz = M.size_from_box(zbox)
    check("size_from_box 不产出负零", all(math.copysign(1.0, v) > 0 or v != 0 for v in sz),
          f"实际={sz}")

    # --- to_object_path / parse_return ---
    check("to_object_path 拼对象路径",
          M.to_object_path("/Game/A/B/house1") == "/Game/A/B/house1.house1")
    check("parse_return 拆 returnValue",
          M.parse_return('{"returnValue": 42}') == 42)
    check("parse_return 解析失败返回原文",
          M.parse_return("not json") == "not json")

    # --- 白膜占位四道校验（2026-09-24 用户要求：先给预估尺寸、登记白膜，再让用户确认）---
    # ⚠ 这是**纯逻辑**：不碰 UE，必须全过。四道校验一条都不能松 ——
    #   松一条，使用物里就会出现"看着像资产、其实是估的"的行（硬规则 6）。
    def _ph(**over):
        """造一行白膜占位；over 覆盖默认值（默认值是**合格**的一行）。"""
        base = {
            "element": "window", "is_placeholder": True, "placeholder_shape": "cube",
            "size_cm": [120.0, 20.0, 100.0],
            "size_source": M.SIZE_SOURCE_ESTIMATED,
        }
        base.update(over)
        return M.AssetListItem(**base)

    check("白膜：合格的一行没有错误", M.placeholder_errors(_ph()) == [],
          f"实际={M.placeholder_errors(_ph())}")
    check("白膜：带 asset_path 被拒（白膜就是没有资产）",
          any("asset_path" in e for e in M.placeholder_errors(
              _ph(asset_path="/Game/demo/Item/ItemMesh/House/house1"))))
    check("白膜：没写形状被拒（阶段三没法实例化）",
          any("placeholder_shape" in e for e in M.placeholder_errors(_ph(placeholder_shape=""))))
    check("白膜：形状写错也被拒",
          any("placeholder_shape" in e for e in M.placeholder_errors(_ph(placeholder_shape="sphere"))))
    check("白膜：没给尺寸被拒",
          any("size_cm" in e for e in M.placeholder_errors(_ph(size_cm=None))))
    check("白膜：cube 只给两个数（没有体积）被拒",
          any("三个数" in e for e in M.placeholder_errors(_ph(size_cm=[120.0, 20.0]))))
    check("白膜：plane 给两个数就够（覆盖面）",
          M.placeholder_errors(_ph(placeholder_shape="plane", size_cm=[800.0, 700.0])) == [])
    check("白膜：尺寸来源写成实测被拒（预估值不许伪装成实测值）",
          any("size_source" in e for e in M.placeholder_errors(
              _ph(size_source="官方 get_bounds 实测（包围盒）"))))
    check("白膜：旧文案『预估（待核实）』算合格（同样是「估的」）",
          M.placeholder_errors(_ph(size_source=M.SIZE_SOURCE_ESTIMATED_LEGACY)) == [])
    check("白膜：没写 size_source 不算错（工具会回填成预估文案）",
          M.placeholder_errors(_ph(size_source="")) == [])


# --- catalog/ 快照与还原（第二组会写使用物）-----------------------------------

def snapshot_catalog() -> Path:
    """把整个 catalog/ 拷进一个临时目录。**必须在第二组之前调。**

    为什么需要：第二组会真的调 confirm_elements / confirm_assets，它们都落盘到 catalog/。
    没有快照的话，跑一次测试就等于用测试数据覆盖用户签过字的使用物（2026-09-23 实测踩过）。
    """
    snap = Path(tempfile.mkdtemp(prefix="check_tools_catalog_"))
    if M.STATE_DIR.exists():
        for p in M.STATE_DIR.iterdir():
            if p.is_file():
                # copy2 连修改时间一起带过去，还原后时间戳不变、便于人工比对
                shutil.copy2(p, snap / p.name)
    return snap


def restore_catalog(snap: Path) -> list[str]:
    """把 catalog/ 还原成快照时的样子；返回**被测试改动过**的文件名（已排序）。

    两步：
      ① 测试新建的文件（快照里没有的）删掉
      ② 快照里的文件一律覆盖回去 —— 不看测试有没有改，简单且幂等
    返回空列表 = 测试根本没写盘。
    """
    restored: list[str] = []
    current: dict[str, Path] = {}
    if M.STATE_DIR.exists():
        current = {p.name: p for p in M.STATE_DIR.iterdir() if p.is_file()}
    for name, path in current.items():
        if not (snap / name).exists():
            path.unlink()
            restored.append(name)
    for src in snap.iterdir():
        dst = M.STATE_DIR / src.name
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            continue                      # 内容没变，不算改动
        shutil.copy2(src, dst)
        restored.append(src.name)
    return sorted(set(restored))


# --- 第二组：端到端（需要 UE + 官方 MCP）-------------------------------------

async def test_live() -> None:
    print("\n=== 第二组：端到端（需要 UE 编辑器 + 官方 MCP 在跑）===")

    async with Client(M.mcp) as c:
        # --- official_status ---
        st = (await c.call_tool("official_status", {})).structured_content or {}
        if not st.get("reachable"):
            skip("端到端全部", f"官方连不上：{st.get('error')}")
            return
        check("official_status 连上官方", st.get("reachable") is True)
        check("拿到官方工具集数量", (st.get("toolset_count") or 0) > 5,
              f"实际={st.get('toolset_count')}")

        # --- confirm_elements：记录**按大类填好的那张表**（含预估数量 + 两个场景项）---
        # ⚠ 2026-09-24 契约变了：每个元素必须填 category（表外的 key 会被拒收）；
        #   有数量的填 count；表里还必须出现 map_size / time_of_day 两个**场景项**。
        r = await c.call_tool("confirm_elements", {
            "elements": [
                {"element": "house", "category": "building", "count": 1,
                 "count_source": "预估（参考图目测）", "note": "左侧近处的白色大房子"},
                {"element": "road", "category": "environment",
                 "note": "右侧沥青路（面 —— 没有『个数』概念，故不填 count）"},
                {"element": "sidewalk", "category": "environment",
                 "note": "中间水泥步道（面，同上）"},
                {"element": "tree", "category": "nature", "count": 6,
                 "count_source": "预估（参考图目测）", "note": "草带上的小树"},
                {"element": "grass", "category": "nature", "note": "草坪（面，同上）"},
                # ⚠ 故意放一个**资产库里不可能有**的元素：用来验「找不到 → 给白膜提示」这条
                {"element": "windmill_zzz", "category": "props",
                 "note": "测试用：资产库里不存在，用来验 placeholder_hint"},
                {"element": "map_size", "category": "map_size", "size_m": [80, 80],
                 "note": "预估：参考图看不出边界，按街景纵深估 80×80 m（待核实）"},
                {"element": "time_of_day", "category": "time_of_day",
                 "time_of_day": "sunset", "note": "黄昏：长影 + 暖色天光"},
            ],
            "source_image": "views/reference_lawn_sunset.jpg",
        })
        d = r.structured_content or {}
        check("confirm_elements 记录成功（6 个资产元素 + 2 个场景项 = 8）",
              d.get("total") == 8, f"实际={d.get('total')}")
        check("元素清单已落盘", M.ELEMENTS_PATH.exists(), str(M.ELEMENTS_PATH))
        check("返回里给出了按大类分组的『填好的表』",
              bool(d.get("groups")) and "建筑物与建筑" in (d.get("groups") or {}),
              f"实际 groups={d.get('groups')}")
        check("两个场景项带出来了（地图大小 / 地图对应时间）",
              (d.get("scene") or {}).get("地图大小") and (d.get("scene") or {}).get("地图对应时间"),
              f"实际 scene={d.get('scene')}")
        check("场景项的结构化值给对了（80×80 m / sunset）",
              d.get("scene_map_size_m") == [80, 80] and d.get("scene_time_of_day") == "sunset",
              f"实际 size={d.get('scene_map_size_m')} time={d.get('scene_time_of_day')}")
        check("没填数量的元素被列出来（面类如实登记）",
              "road" in (d.get("no_count") or []) and "house" not in (d.get("no_count") or []),
              f"实际 no_count={d.get('no_count')}")

        # --- plan_assets：不传 keywords，应读回刚确认的清单 ---
        r2 = await c.call_tool("plan_assets", {})
        d2 = r2.structured_content or {}
        check("plan_assets 读到了用户确认的清单",
              "用户确认过的元素清单" in (d2.get("source") or ""),
              f"实际 source={d2.get('source')}")
        check("查了 6 个元素", d2.get("total_elements") == 6,
              f"实际={d2.get('total_elements')}")
        check("恒定要求用户确认", d2.get("needs_user_confirmation") is True)
        # ⚠ 2026-09-24：场景项**不是资产**，不许被当成元素去搜（搜「地图大小」没有意义）
        check("场景项没被当成元素搜索",
              all(e.get("element") not in ("map_size", "time_of_day")
                  for e in d2.get("elements", [])),
              f"实际={[e.get('element') for e in d2.get('elements', [])]}")
        check("场景项原样带出在 scene 里",
              bool(d2.get("scene")), f"实际 scene={d2.get('scene')}")

        # ⚠ 2026-09-24 整改的验收点：找不到资产的元素，要被明确指成**白膜占位**，
        #   而且**不许**再用"跳过这个元素"那种会让人静默丢信息的说法。
        elems = {e["element"]: e for e in d2.get("elements", [])}
        probe = elems.get("windmill_zzz") or {}
        check("找不到的元素带出 placeholder_hint（要求登记白膜占位）",
              probe.get("status") == "missing"
              and "白膜占位" in (probe.get("placeholder_hint") or ""),
              f"实际 status={probe.get('status')} hint={(probe.get('placeholder_hint') or '')[:60]!r}")
        check("找不到元素时不再说『跳过这个元素』",
              "跳过这个元素" not in (probe.get("question") or ""),
              f"实际={probe.get('question')}")


        # 关键：road 必须靠【文件夹名】命中（官方按资产名搜不到哈希名 MI_sjfnch0a）
        road = elems.get("road") or {}
        road_ok = road.get("status") == "found" and any(
            a["matched_by"] == "文件夹名" for a in road.get("assets", [])
        )
        check("road 靠【文件夹名】命中（这正是官方搜不到的情况）", road_ok,
              f"实际={road.get('status')} / {[a.get('matched_by') for a in road.get('assets', [])]}")

        # 关键：缺失元素必须【不猜】—— 判定方式是**字段集合必须严格受限于 schema**。
        # 这样将来谁往 ElementPlan 里加一个 guesses/suspects 之类字段，这条会立刻失败。
        # ⚠ 旧版这里查的是 "suspects" / "candidate_folders" 两个字段名，但这两个名字
        #   在 src 里根本不存在（grep 零命中）—— 那条断言**恒为真**，等于没测。
        missing = [e for e in d2.get("elements", []) if e["status"] == "missing"]
        allowed_keys = set(M.ElementPlan.model_fields)
        extra = [sorted(set(e) - allowed_keys) for e in d2.get("elements", [])]
        check("元素返回字段严格受限于 schema（没有猜测字段的容身之处）",
              all(not x for x in extra),
              f"多出的字段={extra}")
        if missing:
            q = missing[0].get("question") or ""
            check("缺失元素的提问是『请用户给路径』",
                  "路径" in q and "名字" in q,
                  f"实际={q[:120]}")
            check("缺失元素不返回任何候选资产",
                  all(not e.get("assets") for e in missing),
                  "缺失元素却带了候选资产")
        else:
            skip("缺失元素的提问", "本次没有缺失元素")

        # --- rename_assets：**只测预览路径**（绝不真改用户资产）---
        r3 = await c.call_tool("rename_assets", {
            "items": [
                {"path": "/Game/Fab/Megascans/Surfaces/Uncut_Grass_oeeb70/High/oeeb70_tier_1/Materials/MI_oeeb70",
                 "new_name": "MI_Lawn"},
                {"path": "/Game/demo/Whatever", "new_name": "bad/name"},   # 非法名，应被本地拦下
            ],
            "confirm": False,       # ⚠ 永远是 false
        })
        d3 = r3.structured_content or {}
        check("rename_assets 默认是 dry_run", d3.get("dry_run") is True)
        check("rename_assets 一个都没真改", d3.get("renamed") == 0, f"实际={d3.get('renamed')}")
        check("rename_assets 拦下了带斜杠的非法名",
              d3.get("failed") == 1 and "斜杠" in (d3["items"][1].get("error") or ""),
              f"实际={d3.get('items')[1].get('error')}")
        check("rename_assets 给出了改名前后路径",
              d3["items"][0]["new_path"].endswith("/MI_Lawn"),
              f"实际={d3['items'][0]['new_path']}")

        # --- confirm_assets：**白膜契约**（2026-09-24 整改后的新规矩）---
        # ⚠ 契约变了（这次整改的核心）：
        #   ① 路径验不过的行**不再**当"invalid_paths"使用出去 —— 直接**整表拒收**，
        #      并告诉调用方二选一：给对路径 / 登记成白膜占位；
        #   ② 白膜行必须把 asset_path / placeholder_shape / size_cm / size_source 四样填齐；
        #   ③ **只有材质实例顶着**的行要点名（摆不出实体）—— 但只点名，不替用户改。
        # ⚠ 判"有没有被拒"用 `call_expect_error()`（见它 docstring：**本 SDK 报错时不抛异常**）。
        #   断言**只看"被拒了没有"** —— 错误原文长什么样、以哪种形式回来，**不在这里断言**：
        #   那是"客户端怎么送错误"的实现细节，我在这个环境里**没法复现**（不能跑 MCP 客户端）。
        #   把"错误原文"也写进 detail，红了就能一眼看见它到底回了什么。
        rejected, err = await call_expect_error(c, "confirm_assets", {
            "items": [{"element": "shadow_probe", "element_key": "shadow_probe",
                       "asset_path": "/Game/This/Path/Does/Not/Exist",
                       "asset_type": "mesh"}],
        })
        check("confirm_assets 拒收路径验证不过的行（不再当 invalid_paths 使用）",
              rejected, f"rejected={rejected} 原文={err[:200]!r}")

        # 填错的白膜行（没写形状）也要被拒 —— 光有尺寸不算数
        rejected, err = await call_expect_error(c, "confirm_assets", {
            "items": [{"element": "window", "element_key": "window",
                       "is_placeholder": True, "size_cm": [120.0, 20.0, 100.0],
                       "size_source": M.SIZE_SOURCE_ESTIMATED}],
        })
        check("confirm_assets 拒收没写 placeholder_shape 的白膜行",
              rejected, f"rejected={rejected} 原文={err[:200]!r}")

        # 走通的一批：真资产（road=材质实例，**摆不出实体**要被打上 ⚠）
        #             + 登记好的白膜（window / driveway）
        r4 = await c.call_tool("confirm_assets", {
            "items": [
                {"element": "road", "element_key": "road",
                 "asset_path": "/Game/Fab/Megascans/Surfaces/Fine_American_Road_sjfnch0a/Medium/sjfnch0a_tier_2/Materials/MI_sjfnch0a",
                 "asset_type": "material_instance"},
                {"element": "window", "element_key": "window",
                 "is_placeholder": True, "placeholder_shape": "cube",
                 "size_cm": [120.0, 20.0, 100.0],
                 "size_source": M.SIZE_SOURCE_ESTIMATED,
                 "note": "预估：参考图目测（待核实）"},
                {"element": "driveway", "element_key": "driveway",
                 "is_placeholder": True, "placeholder_shape": "plane",
                 "size_cm": [300.0, 600.0], "size_source": M.SIZE_SOURCE_ESTIMATED},
            ],
        })
        d4 = r4.structured_content or {}
        check("confirm_assets 验证出 1 个真资产 / 2 个白膜占位",
              d4.get("verified") == 1 and d4.get("placeholders") == 2,
              f"实际 verified={d4.get('verified')} placeholders={d4.get('placeholders')}")
        check("confirm_assets 里 invalid_paths 恒为空（验不过的进不来）",
              d4.get("invalid_paths") == [], f"实际={d4.get('invalid_paths')}")
        check("confirm_assets 生成了给人看的清单正文",
              "资产清单（阶段一使用）" in (d4.get("deliverable") or ""),
              "deliverable 里没有清单标题")
        # ⚠ 这两条是本轮整改的**验收点**：材质行必须在**清单里**就看得出来，不能只在脑子里
        check("清单里点明了『只有材质实例，摆不出实体』",
              "摆不出实体" in (d4.get("deliverable") or ""),
              "deliverable 里没点名材质行")
        check("note 里点名了只拿材质顶着的元素",
              "road" in (d4.get("note") or ""), f"实际 note={d4.get('note')}")
        check("白膜行在清单里标成 ◻ 白膜，且不带资产路径",
              "◻ 白膜" in (d4.get("deliverable") or "")
              and not any(it.get("asset_path") for it in d4.get("items", [])
                          if it.get("is_placeholder")),
              "白膜行没标出来，或带了路径")
        check("资产清单已落盘", M.ASSET_LIST_PATH.exists(), str(M.ASSET_LIST_PATH))


async def main() -> int:
    await test_pure()

    # ⚠ 第二组会写 catalog/，先快照。还原放在 finally 里 ——
    #   不能只在"跑成功"时还原：失败或中断时更需要还原，那时候文件已经被改掉一半了。
    snap = snapshot_catalog()
    try:
        await test_live()
    except BaseException as exc:
        # ⚠ 必须是 BaseException：CancelledError 不是 Exception 的子类，
        #   用 except Exception 接不住（实测被这个坑过一次）。
        print(f"\n[异常] 端到端测试抛出：{type(exc).__name__}: {exc}")
    finally:
        changed = restore_catalog(snap)
        if changed:
            print(f"[还原] catalog/ 已从快照还原，测试改动过：{changed}")
        else:
            print("[还原] catalog/ 本次没有变化")

    print(f"\n结果：通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败清单：" + str(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
