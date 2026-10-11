# Copyright (c) 2026 ZHUPO233
# UEPLANE 插件 · 插件入口
#
# 做三件事，顺序不能反：
#   ① **种配置** —— 插件自带的 `Content/config/*.json` 是**出厂配置**；第一次运行拷一份到
#      `<工程>/Saved/UEPLANE/config/`，之后**都读 Saved 里那份**（用户改配置不用动插件目录，
#      而且已装到引擎里的插件目录可能是只读的）。
#   ② **改落盘根** —— 业务层（`main.py`）与规划层（`planning/plan.py`）的路径常量是**模块级**的，
#      import 时就算好了。用**通用重算**：扫模块里所有 `Path` 常量，凡是以某个旧根开头的整体搬到新根
#      —— **不会漏**（上一版一个个手改，就漏过 `OUT_JSON`）。
#   ③ **注册 toolset**。
#
# ⚠ 每一步都 log_warning：静默失败最难查（这个项目已经为它吃过三次亏，实测记录见 `移植实测记录.md`）。

import shutil
from pathlib import Path

import unreal


def _say(msg):
    unreal.log_warning("[UEPLANE] " + str(msg))


_say("init_unreal.py 开始执行")

# 插件自己的目录（= Content/）：`__file__` 在 `Content/Python/init_unreal.py`
_CONTENT = Path(__file__).resolve().parents[1]
_base = Path(unreal.Paths.project_saved_dir()) / "UEPLANE"

for _sub in ("views", "views/archive", "views/preview", "views/exchange",
             "catalog", "config"):
    (_base / _sub).mkdir(parents=True, exist_ok=True)

# ── ① 种配置（只在缺的时候拷，**绝不覆盖用户改过的**）──────────────────────────
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


# ── ② 落盘根：通用重算 ────────────────────────────────────────────────────────
def _relocate(_mod, _pairs, _tag):
    """把 `_mod` 里所有"在某个旧根底下"的 `Path` 常量，整体搬到对应的新根。

    ⚠ 只搬**指到的那些根**（`catalog` / `views` / `config`）—— 别的 Path 常量一个字不动。
    """
    _out = []
    for _old, _new in _pairs:
        for _name in dir(_mod):
            if _name.startswith("_"):
                continue
            _val = getattr(_mod, _name, None)
            if not isinstance(_val, Path):
                continue
            try:
                _rel = _val.relative_to(_old)
            except ValueError:
                continue
            _tgt = _new if str(_rel) == "." else (_new / _rel)
            setattr(_mod, _name, _tgt)
            _out.append("{}  ->  {}".format(_name, _tgt))
    _say("[{}] 搬了 {} 个路径常量".format(_tag, len(_out)))
    for _line in _out:
        _say("   " + _line)
    return _out


# ②a 业务层 main.py：catalog / views / config 三样都从插件目录搬到 Saved
from ueplane import main as M                                     # noqa: E402

_relocate(M, [(_CONTENT / "catalog", _base / "catalog"),
              (_CONTENT / "views", _base / "views"),
              (_CONTENT / "config", _base / "config")], "main")

# ②b 规划层 plan.py：它自己的 REPO_ROOT 就是插件 Content 目录
from ueplane.planning import plan as P                            # noqa: E402

_relocate(P, [(P.REPO_ROOT, _base)], "plan.py")
P.REPO_ROOT = _base
_say("active_plan_path() = " + str(P.active_plan_path()))

# ②c 阶段五那一层 surfaces.py：它有自己的 CATALOG_DIR / VIEWS_DIR，
#     ⚠ 而且 `REPO_ROOT / "config" / …` 是**调用时**才拼的 ⇒ 只搬常量不够，
#       搬完必须把 `REPO_ROOT` 本身也指到新根（与 plan.py 同一招）。
from ueplane import surfaces as S                                 # noqa: E402

_relocate(S, [(_CONTENT / "catalog", _base / "catalog"),
              (_CONTENT / "views", _base / "views"),
              (_CONTENT / "config", _base / "config")], "surfaces")
S.REPO_ROOT = _base

# ②d 自检：三层的落盘根必须**指着同一处**（指着两处就是两套口径，那是本项目最忌讳的）
_say("落盘根自检：")
_say("   main.STATE_DIR      = " + str(M.STATE_DIR))
_say("   main.VIEWS_DIR      = " + str(M.VIEWS_DIR))
_say("   main.CONFIG_DIR     = " + str(M.CONFIG_DIR))
_say("   surfaces.CATALOG_DIR= " + str(S.CATALOG_DIR))
_say("   surfaces.VIEWS_DIR  = " + str(S.VIEWS_DIR))
_say("   plan.VIEWS_DIR      = " + str(P.VIEWS_DIR))

# ── ③ 注册 ────────────────────────────────────────────────────────────────────
try:
    from ueplane import tools
    _say("import tools 成功")
    _say("register() = " + repr(tools._registration.register()))
except Exception as exc:                                          # noqa: BLE001
    _say("注册失败: " + repr(exc))
    raise
