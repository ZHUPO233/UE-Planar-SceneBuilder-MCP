# UEPLANE · 场景身份（2026-10-14 新增）                                    【模块：scene（场景）】
#
# 一句话：**场景 = 一张关卡（level 包路径）**。
#
# 由来（是实测出来的，不是假想）：纪元判据 `planning.active_epoch()` 此前**只看**
#   `views/plan_v2.json` 在不在 —— 与"现在开的是哪张图"**完全无关**。于是搭完第一个场景的
#   那一刻起，这个工程**永远是纪元 2**：换一张新图也一样；而纪元 2 里前三阶段的写工具
#   （`confirm_elements` / `confirm_assets` / `check_build_target` / `confirm_plan`）
#   一律拒收 ⇒ **一个工程里再也开不了第二个场景**。
#   （2026-10-14 在 demo 工程实测撞到；用户原话「这估计是插件BUG」。）
#   ⚠ 更难看的是：全工程**只有纪元这一个状态不认图** —— 搭建台账 / 环境台账 / 材质台账 /
#     对账报告 / 「搭哪张图」答复**每一件都记了 `level` 并逐个比对**
#     （`main.py:3464` / `4607` / `13637` / `surfaces.py:2491` / `main.py:14379` / `2245-2269`）。
#     补上"场景"这一层，就是让纪元**跟其余每一件一样认图**。
#
# 本模块**不 import `unreal`、不 import `main`**（避免循环）：它只做纯路径与 JSON 的事 ——
#   `project_fingerprint()` 里那个 `import unreal` 是**函数内**的，且失败就返回 `{}`（不假装）。
#
# ⚠ 为什么这里全是**函数**、没有模块级 Path 常量：插件入口 `init_unreal.py` 的 `_relocate()`
#   会把模块里所有在 `REPO_ROOT` 底下的 Path 常量整体搬走 —— 那是它的设计，别跟它抢。
#   （血账见 `planning/plan.py` 里 `VIEW_TEMPLATE_PATH` 那段实测说明。）

import hashlib
import json
import re
from pathlib import Path

SCENES_DIRNAME = "scenes"
"""场景目录名（`<base>/scenes/<场景 key>/`）。"""

CARD_NAME = "scene.json"
"""**身份卡**的文件名 —— 每个场景目录一份，记"这张图 + 这个工程 + 这一版"。"""

ACTIVE_NAME = "active.json"
"""**当前场景指针**（`<base>/active.json`）—— 只当**缓存 + 审计**用，**不是权威**。

⚠ 权威永远是**UE 现读的当前关卡**（见 `main.py::ensure_scene()`）。这一份只在
  "UE 连不上、读不到当前关卡"时顶上，而且那时必须在报文里点名"这一轮是按缓存判的"。
"""

RESET_HISTORY_NAME = "reset_history.json"
"""**重置履历**（`<base>/reset_history.json`）—— 刻意放在**场景目录之外**：
   场景被 `reset_scene()` 归档之后，这份履历要在**外面**还看得见。"""

SCENE_SWITCH_MSG = (
    "**换图 = 换场景**（每个场景各记各的：元素 / 资产 / 规划 / 台账 / 材质 / 环境 / 对账 / 验收）。"
    "同一张图上要从头再来 ⇒ `reset_scene()`（要**用户本人原话**）。"
)

_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")
_KEY_MAX = 100


def scene_key(level: str) -> str:
    """关卡包路径 → **场景 key**（目录名）。

    口径：`/Game/demo/Landscape/MCP100` → `Game_demo_Landscape_MCP100`。
      · 去前导斜杠；`/` 与 `\\` → `_`；`:` → `_`；其余非 `[A-Za-z0-9_.-]` 的字符 → `_`；
      · 空串 / 只有分隔符 ⇒ `"_unbound"`（**不抛错** —— 判据"读不到就说明读不到"，不是崩）；
      · 超过 `_KEY_MAX` 字符 ⇒ 截断 + 拼一段短哈希（**截断必须带哈希**，
        否则两个长路径会撞同一个 key —— 那正是本项目最忌讳的"两处说不同的话"）。

    ⚠ 包路径是**工程内相对**的（`/Game/Maps/Main` 在两个工程里都合法）—— 所以
      **工程隔离不靠 key**，靠"数据树本身就在 `<工程>/Saved/UEPLANE/` 底下"+ 身份卡里的
      工程指纹（见 `project_fingerprint()`）。
    """
    raw = str(level or "").strip()
    if not raw:
        return "_unbound"
    s = raw[1:] if raw.startswith("/") else raw
    s = s.replace("/", "_").replace("\\", "_").replace(":", "_")
    s = _KEY_SAFE.sub("_", s)
    s = s.strip("_")
    if not s:
        return "_unbound"
    if len(s) > _KEY_MAX:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        s = s[:_KEY_MAX] + "_" + digest
    return s


def scene_dir(base, level: str) -> Path:
    """这张图的场景目录（`<base>/scenes/<key>/`）。"""
    return Path(base) / SCENES_DIRNAME / scene_key(level)


def scene_subdirs() -> tuple:
    """一个场景目录里**必须存在**的子目录（`ensure_scene()` 建它们时用同一份清单）。"""
    return ("catalog", "views", "views/archive", "views/preview", "views/exchange")


# --- 通用"搬路径常量"（**唯一出处**）------------------------------------------------
# ⚠ 这三个函数**原来住在 `init_unreal.py`**（`_map_path` / `_relocate_defaults` / `_relocate`）。
#   2026-10-14 搬到本模块，`init_unreal.py` 改成 import —— 因为现在有**两次**搬运
#   （启动一次：插件目录 → `Saved/UEPLANE`；切场景一次：`Saved/UEPLANE` → 场景目录），
#   两份实现就是两套口径，迟早分叉。

def map_path(val, pairs):
    """一个 Path 若在某个旧根底下，返回搬过去之后的新 Path；否则原样返回。"""
    if not isinstance(val, Path):
        return val
    for old, new in pairs:
        try:
            rel = val.relative_to(old)
        except (ValueError, TypeError):
            continue
        return new if str(rel) == "." else (new / rel)
    return val


def relocate_defaults(mod, pairs, tag: str, say=None) -> int:
    """把「函数默认参数」里那份被搬走的路径常量也换掉。

    为什么必须有它（2026-10-11 在干净工程里撞到的真 bug，阶段二被它整个堵死）：
      Python 的函数默认参数在 def 执行那一刻就求值绑定了。
      `setattr(mod, name, new)` 只改模块属性，**改不动已经绑死的默认参数**。
      实测：`plan.py` 的 `load_previous_elements(path: Path = ELEMENTS_PATH)` —— 模块属性
      `ELEMENTS_PATH` 日志里明明白白写着搬到了 `Saved/UEPLANE/catalog/elements.json`
      （「搬了 7 个路径常量」看着全对），可 `main.py` 两处调 `load_previous_elements()`
      都不传参，于是读的永远是搬走之前那个路径，报「读不到阶段一的元素清单」，
      而那份文件明明在。静默失败最难查，所以在这里把机制补完整。

    ⚠ 本 docstring 里**不许出现反斜杠路径**（`init_unreal.py` 文件头那条纪律：
      非 raw 字符串里的 `\\U` 是 Unicode 转义，直接 SyntaxError）。
    """
    n = 0
    for name in dir(mod):
        fn = getattr(mod, name, None)
        if not callable(fn) or not hasattr(fn, "__defaults__"):
            continue                      # 类 / 内建 / 非函数，跳过
        defs = fn.__defaults__
        if defs:
            new = tuple(map_path(d, pairs) for d in defs)
            if new != defs:
                try:
                    fn.__defaults__ = new
                    n += 1
                except (AttributeError, TypeError) as exc:
                    if say:
                        say("[{}] {} 的默认参数搬不动（{!r}）—— 它可能还指着旧路径".format(
                            tag, name, exc))
        kw = getattr(fn, "__kwdefaults__", None)
        if isinstance(kw, dict):
            newkw = {k: map_path(v, pairs) for k, v in kw.items()}
            if newkw != kw:
                try:
                    fn.__kwdefaults__ = newkw
                    n += 1
                except (AttributeError, TypeError) as exc:
                    if say:
                        say("[{}] {} 的关键字默认参数搬不动（{!r}）".format(tag, name, exc))
    if say:
        say("[{}] 顺带搬了 {} 个函数默认参数里的路径".format(tag, n))
    return n


def relocate(mod, pairs, tag: str, say=None) -> list:
    """把 `mod` 里所有「在某个旧根底下」的 Path 常量，整体搬到对应的新根。

    只搬指到的那些根（`catalog` / `views` / `config`）—— 别的 Path 常量一个字不动。
    搬完必须再过一遍函数默认参数（见 `relocate_defaults()` 那段实测说明）：
    只 `setattr` 模块属性会漏掉已经绑死的默认值。

    ⚠ **切场景时只给 `catalog` / `views` 两条根** —— `config/` 是全工程一份，
      而且 `REPO_ROOT` 本身**不许**指进场景目录（它同时挂着 `config/`，见改动单 §1 口径 3）。
    """
    out = []
    for name in dir(mod):
        if name.startswith("_"):
            continue
        val = getattr(mod, name, None)
        if not isinstance(val, Path):
            continue
        tgt = map_path(val, pairs)
        if tgt is val:
            continue
        setattr(mod, name, tgt)
        out.append("{}  ->  {}".format(name, tgt))
    if say:
        say("[{}] 搬了 {} 个路径常量".format(tag, len(out)))
        for line in out:
            say("   " + line)
    relocate_defaults(mod, pairs, tag, say)
    return out


# --- 身份卡 -------------------------------------------------------------------------

def project_fingerprint() -> dict:
    """当前 UE 工程的指纹：`{uproject, dir, engine}`。

    ⚠ **进程内**才拿得到（插件版）。服务器版（`mine`，跑在 UE 之外）拿不到 ⇒ 返回 `{}`
      —— 那就**记空**，并在报文里点名"缺工程指纹，判不了跨工程"，**不假装**。
    ⚠ 必须是**函数**：模块级 Path 常量会被 `init_unreal.py` 的 `_relocate()` 搬飞。
    """
    try:
        import unreal                                        # noqa: PLC0415
    except Exception:                                        # noqa: BLE001
        return {}
    out = {}
    for key, getter in (("uproject", lambda: unreal.Paths.get_project_file_path()),
                        ("dir", lambda: unreal.Paths.project_dir())):
        try:
            out[key] = str(getter() or "").replace("\\", "/")
        except Exception:                                    # noqa: BLE001
            out[key] = ""
    try:
        out["engine"] = str(unreal.SystemLibrary.get_engine_version() or "")
    except Exception:                                        # noqa: BLE001
        out["engine"] = ""
    up = out.get("uproject") or ""
    out["name"] = up.rsplit("/", 1)[-1].rsplit(".", 1)[0] if up else ""
    return out


def load_json_safe(path) -> dict:
    """读一份我们自己的 JSON；**读不动按"没有"处理**（返回空 dict，不猜、不拿别的顶替）。"""
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json_safe(path, doc: dict) -> bool:
    """写一份我们自己的 JSON（目录不存在就建）→ 成了没有。"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def card_path(sdir) -> Path:
    return Path(sdir) / CARD_NAME


def read_card(sdir) -> dict:
    return load_json_safe(card_path(sdir))


def read_active(base) -> dict:
    return load_json_safe(Path(base) / ACTIVE_NAME)


def write_active(base, doc: dict) -> bool:
    return save_json_safe(Path(base) / ACTIVE_NAME, doc)


def append_reset_history(base, entry: dict) -> bool:
    """往 `<base>/reset_history.json` 追加一条（**场景目录之外** —— 归档之后还看得见）。"""
    path = Path(base) / RESET_HISTORY_NAME
    doc = load_json_safe(path)
    hist = doc.get("history")
    if not isinstance(hist, list):
        hist = []
    hist.append(entry)
    doc = {
        "_note": "`reset_scene()` 的履历。刻意放在场景目录之外 —— 场景被归档之后，这一份还在。",
        "history": hist,
    }
    return save_json_safe(path, doc)


def list_scenes(base) -> list:
    """列出 `<base>/scenes/` 下**已经有目录**的场景 key（排序；读不到就是空表）。"""
    root = Path(base) / SCENES_DIRNAME
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def orphan_state(base) -> dict:
    """**还没归属任何场景的旧状态** —— `orphan_state(base)["total"]` = 散件个数。

    为什么要有它（2026-10-14）：新布局把使用物按场景分到 `scenes/<key>/` 底下，而磁盘上
    已有的那份是**老布局**（`<base>/catalog` + `<base>/views` 平铺）。改动单 §5 定的口径是
    **不搬、不删、不隐藏** —— 那就必须**看得见**：`get_plan()` / `get_asset_list()` 会点名
    "检测到未绑定旧状态 N 个文件"。

    ⚠ **只报不搬**：本函数不移动、不删除任何文件。
    """
    out = {"catalog": [], "views": [], "total": 0}
    for sub in ("catalog", "views"):
        d = Path(base) / sub
        try:
            names = sorted(p.name for p in d.iterdir() if p.is_file())
        except OSError:
            names = []
        out[sub] = names
        out["total"] += len(names)
    return out
