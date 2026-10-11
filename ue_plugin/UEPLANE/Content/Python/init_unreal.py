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
def _map_path(_val, _pairs):
    """一个 `Path` 若在某个旧根底下，返回搬过去之后的新 Path；否则原样返回。"""
    if not isinstance(_val, Path):
        return _val
    for _old, _new in _pairs:
        try:
            _rel = _val.relative_to(_old)
        except ValueError:
            continue
        return _new if str(_rel) == "." else (_new / _rel)
    return _val


def _relocate_defaults(_mod, _pairs, _tag):
    """把**函数默认参数**里那份"被搬走的路径常量"也换掉。

    ⚠ 为什么必须有它（2026-10-11 **在干净工程里撞到的真 bug**，阶段二被它整个堵死）：
      Python 的**函数默认参数在 `def` 执行那一刻就求值绑定**了。
      `setattr(mod, name, new)` 只改模块属性，**改不动已经绑死的默认参数**。
      实测：`plan.py::load_previous_elements(path: Path = ELEMENTS_PATH)` ——
      模块属性 `ELEMENTS_PATH` 日志里明明白白写着搬到了 `Saved\UEPLANE\catalog\elements.json`
      （"搬了 7 个路径常量"看着全对），可 `main.py` 两处调 `load_previous_elements()` 都不传参
      ⇒ 读的永远是**搬走之前**那个路径 ⇒ 报「读不到阶段一的元素清单」，而那份文件**明明在**。
      **静默失败最难查** —— 所以在这里把机制补完整，而不是只去改产品那一个函数。
    """
    _n = 0
    for _name in dir(_mod):
        _fn = getattr(_mod, _name, None)
        if not callable(_fn) or not hasattr(_fn, "__defaults__"):
            continue                      # 类 / 内建 / 非函数，跳过
        _defs = _fn.__defaults__
        if _defs:
            _new = tuple(_map_path(_d, _pairs) for _d in _defs)
            if _new != _defs:
                try:
                    _fn.__defaults__ = _new
                    _n += 1
                except (AttributeError, TypeError) as _exc:
                    _say("[{}] ⚠ {} 的默认参数搬不动（{!r}）—— 它可能还指着旧路径".format(
                        _tag, _name, _exc))
        _kw = getattr(_fn, "__kwdefaults__", None)
        if isinstance(_kw, dict):
            _newkw = {_k: _map_path(_v, _pairs) for _k, _v in _kw.items()}
            if _newkw != _kw:
                try:
                    _fn.__kwdefaults__ = _newkw
                    _n += 1
                except (AttributeError, TypeError) as _exc:
                    _say("[{}] ⚠ {} 的关键字默认参数搬不动（{!r}）".format(_tag, _name, _exc))
    _say("[{}] 顺带搬了 {} 个**函数默认参数**里的路径".format(_tag, _n))
    return _n


def _relocate(_mod, _pairs, _tag):
    """把 `_mod` 里所有"在某个旧根底下"的 `Path` 常量，整体搬到对应的新根。

    ⚠ 只搬**指到的那些根**（`catalog` / `views` / `config`）—— 别的 Path 常量一个字不动。
    ⚠ 搬完**必须**再过一遍函数默认参数（见 `_relocate_defaults` 那段实测说明）——
      只 `setattr` 模块属性会漏掉已经绑死的默认值。
    """
    _out = []
    for _name in dir(_mod):
        if _name.startswith("_"):
            continue
        _val = getattr(_mod, _name, None)
        if not isinstance(_val, Path):
            continue
        _tgt = _map_path(_val, _pairs)
        if _tgt is _val:
            continue
        setattr(_mod, _name, _tgt)
        _out.append("{}  ->  {}".format(_name, _tgt))
    _say("[{}] 搬了 {} 个路径常量".format(_tag, len(_out)))
    for _line in _out:
        _say("   " + _line)
    _relocate_defaults(_mod, _pairs, _tag)
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
