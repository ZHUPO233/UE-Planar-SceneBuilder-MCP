# UEPLANE 插件 · 插件入口
#
# 做三件事，顺序不能反：
#   ① 种配置：插件自带的 Content/config/*.json 是出厂配置；第一次运行拷一份到
#      <工程>/Saved/UEPLANE/config/，之后都读 Saved 里那份（用户改配置不用动插件目录，
#      而且已装到引擎里的插件目录可能是只读的）。
#   ② 改落盘根：业务层（main.py）与规划层（planning/plan.py）的路径常量是模块级的，
#      import 时就算好了。用通用重算：扫模块里所有 Path 常量，凡是以某个旧根开头的整体搬到新根，
#      搬完**再过一遍函数默认参数**（那是同一个机制的盲区，见下面 _relocate_defaults 的说明）。
#   ③ 注册 toolset。
#
# 每一步都 log_warning：静默失败最难查（这个项目已经为它吃过好几次亏）。
#
# [!] 编码与转义纪律（2026-10-11 实测踩到，整个插件因此一条工具都没注册上）：
#     本文件里**不要出现反斜杠路径**。Windows 路径写进普通字符串时，
#     "\U" 会被 Python 当成 Unicode 转义（\UXXXXXXXX），直接 SyntaxError。
#     要么写成正斜杠，要么用 raw 字符串（r"..."）。

import shutil
from pathlib import Path

import unreal


def _say(msg):
    unreal.log_warning("[UEPLANE] " + str(msg))


_say("init_unreal.py 开始执行")

# 插件自己的目录（= Content/）：__file__ 在 Content/Python/init_unreal.py
_CONTENT = Path(__file__).resolve().parents[1]
_base = Path(unreal.Paths.project_saved_dir()) / "UEPLANE"

for _sub in ("views", "views/archive", "views/preview", "views/exchange",
             "catalog", "config"):
    (_base / _sub).mkdir(parents=True, exist_ok=True)

# -- (1) 种配置（只在缺的时候拷，绝不覆盖用户改过的）--------------------------------
_seeded = []
for _src in sorted((_CONTENT / "config").glob("*.json")):
    _dst = _base / "config" / _src.name
    if _dst.exists():
        continue
    try:
        shutil.copyfile(str(_src), str(_dst))
        _seeded.append(_src.name)
    except OSError as _exc:
        _say("种配置失败 {}: {!r}".format(_src.name, _exc))
_say("种了 {} 份配置 -> {}".format(len(_seeded), _base / "config"))
for _n in _seeded:
    _say("   " + _n)


# -- (2) 落盘根：通用重算 ------------------------------------------------------------
# ⚠ 2026-10-14：`_map_path` / `_relocate_defaults` / `_relocate` **已搬到 `ueplane/scene.py`**
#   成为**唯一出处** —— 因为现在有**两次**搬运（启动一次：插件目录 -> Saved/UEPLANE；
#   切场景一次：Saved/UEPLANE -> 场景目录）。两份实现就是两套口径，迟早分叉。
#   这里只留一个两行的适配器：把日志口 `_say` 接上。
from ueplane.scene import relocate as _scene_relocate                 # noqa: E402


def _relocate(_mod, _pairs, _tag, _say_fn=None):
    """**薄适配器** —— 逻辑只有一份（`ueplane/scene.py`）；这里只把日志口接上。"""
    return _scene_relocate(_mod, _pairs, _tag, say=_say_fn or _say)


# (2a) 业务层 main.py：catalog / views / config 三样都从插件目录搬到 Saved
from ueplane import main as M                                     # noqa: E402

_relocate(M, [(_CONTENT / "catalog", _base / "catalog"),
              (_CONTENT / "views", _base / "views"),
              (_CONTENT / "config", _base / "config")], "main")

# (2b) 规划层 plan.py：它自己的 REPO_ROOT 就是插件 Content 目录
from ueplane.planning import plan as P                            # noqa: E402

_relocate(P, [(P.REPO_ROOT, _base)], "plan.py")
P.REPO_ROOT = _base
_say("active_plan_path() = " + str(P.active_plan_path()))

# (2c) 阶段五那一层 surfaces.py：它有自己的 CATALOG_DIR / VIEWS_DIR，
#      而且它的「REPO_ROOT / config / ...」是调用时才拼的，所以只搬常量不够，
#      搬完必须把 REPO_ROOT 本身也指到新根（与 plan.py 同一招）。
from ueplane import surfaces as S                                 # noqa: E402

_relocate(S, [(_CONTENT / "catalog", _base / "catalog"),
              (_CONTENT / "views", _base / "views"),
              (_CONTENT / "config", _base / "config")], "surfaces")
S.REPO_ROOT = _base

# (2d) 自检：三层的落盘根必须指着同一处（指着两处就是两套口径，那是本项目最忌讳的）
_say("落盘根自检：")
_say("   main.STATE_DIR      = " + str(M.STATE_DIR))
_say("   main.VIEWS_DIR      = " + str(M.VIEWS_DIR))
_say("   main.CONFIG_DIR     = " + str(M.CONFIG_DIR))
_say("   surfaces.CATALOG_DIR= " + str(S.CATALOG_DIR))
_say("   surfaces.VIEWS_DIR  = " + str(S.VIEWS_DIR))
_say("   plan.VIEWS_DIR      = " + str(P.VIEWS_DIR))

# -- (3) 注册 ------------------------------------------------------------------------
try:
    from ueplane import tools
    _say("import tools 成功")
    _say("register() = " + repr(tools._registration.register()))
except Exception as exc:                                          # noqa: BLE001
    _say("注册失败: " + repr(exc))
    raise
