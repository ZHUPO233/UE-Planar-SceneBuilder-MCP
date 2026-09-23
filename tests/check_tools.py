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
# 用法： & F:\MCP_Server\.venv\Scripts\python.exe tests\check_tools.py
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


# --- 第一组：纯逻辑（不需要 UE）-----------------------------------------------

async def test_pure() -> None:
    print("=== 第一组：纯函数（不需要 UE）===")

    # --- 工具面：确认注册的工具就是这几个（多一个都说明"清空"没做干净）---
    # ⚠ 2026-09-23：get_asset_list（取清单）→ 6 个；阶段二三个工具 → 9 个。
    result = M.mcp.list_tools()
    if asyncio.iscoroutine(result):
        result = await result
    tools = getattr(result, "tools", result)
    names = sorted(t.name for t in tools)
    check(
        "只注册了 9 个工具（阶段一 + 取清单 + 阶段二三个）",
        names == ["confirm_assets", "confirm_elements", "confirm_plan",
                  "generate_plan", "get_asset_list", "get_plan",
                  "official_status", "plan_assets", "rename_assets"],
        f"实际={names}",
    )

    # --- 要求必须写在【工具描述】里 ---
    # ⚠ 2026-09-23 改：原先这两条断言 `M.mcp.instructions`，但实测本宿主（DSH 的 dsh-mcp-client）
    #   **不读** MCP 的 instructions —— `connect()` 的返回值（InitializeResult，instructions 在里面）
    #   连赋值都没有，全包搜 "instructions" 是 0 次命中。那段文字发出去也没人接，已删。
    #   真正到模型眼前的是 **tools/list**（工具名 + 描述 + schema），所以改成断言**描述**。
    #   这样以后谁把某条要求从描述里删掉，这里会立刻失败。
    # ⚠ 匹配前先把 markdown 加粗标记 `**` 剔除 —— 否则断言串一旦跨过 `**` 就会**假红**。
    #   实测踩过：描述里写的是「宿主**不许**用上下文里记得的旧表」，
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
        ("get_plan", "failed_names"),                      # 报「自检过了」要报具名项
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

    # --- num：负零/非有限值归一（这是实测踩出来的宿主拒收 bug）---
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

    # 关键：归一后的结果必须能过 allow_nan=False（宿主就是这么拒收的）
    try:
        import json as _json
        _json.dumps([M.num(v) for v, _ in nums], allow_nan=False)
        check("num() 输出能通过 allow_nan=False（宿主不拒收）", True)
    except ValueError as exc:
        check("num() 输出能通过 allow_nan=False（宿主不拒收）", False, str(exc))

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


# --- catalog/ 快照与还原（第二组会写交付物）-----------------------------------

def snapshot_catalog() -> Path:
    """把整个 catalog/ 拷进一个临时目录。**必须在第二组之前调。**

    为什么需要：第二组会真的调 confirm_elements / confirm_assets，它们都落盘到 catalog/。
    没有快照的话，跑一次测试就等于用测试数据覆盖用户签过字的交付物（2026-09-23 实测踩过）。
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

        # --- confirm_elements：记录元素清单 ---
        r = await c.call_tool("confirm_elements", {
            "elements": [
                {"element": "house", "note": "左侧近处的白色大房子"},
                {"element": "road", "note": "右侧沥青路"},
                {"element": "sidewalk", "note": "中间水泥步道"},
                {"element": "tree", "note": "草带上的小树"},
                {"element": "grass", "note": "草坪"},
            ],
            "source_image": "views/reference_lawn_sunset.jpg",
        })
        d = r.structured_content or {}
        check("confirm_elements 记录成功", d.get("total") == 5, f"实际={d.get('total')}")
        check("元素清单已落盘", M.ELEMENTS_PATH.exists(), str(M.ELEMENTS_PATH))

        # --- plan_assets：不传 keywords，应读回刚确认的清单 ---
        r2 = await c.call_tool("plan_assets", {})
        d2 = r2.structured_content or {}
        check("plan_assets 读到了用户确认的清单",
              "用户确认过的元素清单" in (d2.get("source") or ""),
              f"实际 source={d2.get('source')}")
        check("查了 5 个元素", d2.get("total_elements") == 5)
        check("恒定要求用户确认", d2.get("needs_user_confirmation") is True)

        elems = {e["element"]: e for e in d2.get("elements", [])}

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

        # --- confirm_assets：验证存在性 + 交付清单 ---
        r4 = await c.call_tool("confirm_assets", {
            "items": [
                {"element": "road",
                 "asset_path": "/Game/Fab/Megascans/Surfaces/Fine_American_Road_sjfnch0a/Medium/sjfnch0a_tier_2/Materials/MI_sjfnch0a",
                 "asset_type": "material_instance"},
                {"element": "grass",
                 "asset_path": "/Game/Fab/Megascans/Surfaces/Uncut_Grass_oeeb70/High/oeeb70_tier_1/Materials/MI_oeeb70",
                 "asset_type": "material_instance"},
                {"element": "不存在的",
                 "asset_path": "/Game/This/Path/Does/Not/Exist",
                 "asset_type": "mesh"},
            ],
        })
        d4 = r4.structured_content or {}
        check("confirm_assets 验证出 2 个存在 / 1 个不存在",
              d4.get("verified") == 2 and len(d4.get("invalid_paths") or []) == 1,
              f"实际 verified={d4.get('verified')} invalid={d4.get('invalid_paths')}")
        check("confirm_assets 生成了给人看的清单正文",
              "资产清单（阶段一交付）" in (d4.get("deliverable") or ""),
              "deliverable 里没有清单标题")
        check("存在性不通过时明确要求先处理",
              "先" in (d4.get("note") or "") or "处理" in (d4.get("note") or ""),
              f"实际={d4.get('note')}")
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
