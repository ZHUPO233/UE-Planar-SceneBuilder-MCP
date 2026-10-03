# =============================================================================
# schemas.py —— 工具返回结构（Pydantic 模型）
#
# 2026-09-27 从 main.py 拆出来（用户授权的那次结构优化的第 1 步）。**纯搬运**：
#   25 个模型的定义一个字没改，只把它们从那个 6000 行的文件里挪出来。
#
# 为什么值得单独一个文件：这些类是 `@mcp.tool()` 的**返回标注** —— 也就是"模型看到的
#   参数说明书"。它们与主文件里的编排逻辑（闸门 / 官方调用 / 落关卡）是两种东西。
#
# ⚠ 本文件**不许** import main.py 里的任何东西，也不许用相对导入：Agent是按**脚本方式**
#   起 main.py 的（`python ...\src\mcp_server\main.py`），那时 `__package__` 为空，相对
#   导入必抛 ImportError。main.py 顶部用"先相对、失败再 sys.path + 顶层导入"两段式兜底
#   来引本模块；本文件只要保持"只用标准库 + pydantic"，两种启动方式都进得来。
#
# ⚠ `OUR_FOLDER_ROOT` 定义在本文件：下面几个模型的描述里要用它（f-string 在**定义时**求值），
#   而 main.py 的 `FOLDER_BY_ELEMENT` 也要用它 —— 只能有一个定义处，就放在需要它的模型旁边，
#   main.py 从本模块再导出（见那里顶部那段注释）。
# =============================================================================

from pydantic import BaseModel, Field

# 我们自己摆的东西都挂在 outliner 的这个根文件夹下（阶段三幂等对账的判据）
OUR_FOLDER_ROOT = "UEMCP"

# --- [完工-04] 模型（工具返回结构 = 模型看到的"参数说明书"）---
# 实测 2026-09-23 12:12 / 12:24：confirm_assets 与 get_asset_list 完整序列化往返，
# 含新增的 size_cm / bbox_volume_cm3 / size_source / items。

class OfficialStatus(BaseModel):
    """官方链路自检结果。"""

    reachable: bool = Field(description="能不能连上官方 Unreal MCP")
    protocol_version: str | None = Field(default=None, description="协商出的 MCP 协议版本")
    toolset_count: int = Field(default=0, description="官方一共有多少个工具集")
    error: str | None = Field(default=None, description="连不上时的错误原文")


class BuildTargetCheck(BaseModel):
    """阶段三第 1 步（**只读**）：先问清"搭哪儿"，不碰用户的关卡。"""

    stage: str = Field(description="阶段名")
    readonly: bool = Field(
        description=("恒 True：本工具**不碰关卡** —— 不新建/不切换关卡、不摆不删任何 Actor、不存 UE。"
                     "唯一会写的是我们自己的 `views/build_target_v1.json`（答复台账）"))
    official_calls: list[str] = Field(
        default_factory=list,
        description="这次只读地问了官方哪些东西（留痕，方便复核「它真没动手」）")
    current_level: str = Field(description="UE 当前打开的关卡路径")
    candidate_levels: list[str] = Field(
        default_factory=list, description="项目里可选的关卡资产（供用户指定「用哪张图」）")
    our_folders: list[str] = Field(
        default_factory=list, description=f"当前关卡里属于 {OUR_FOLDER_ROOT} 的 outliner 文件夹")
    our_actor_count: int = Field(default=0, description=f"当前关卡里 {OUR_FOLDER_ROOT}/ 下的 Actor 数")
    total_actors: int = Field(default=0, description="当前关卡里的 Actor 总数")
    can_create_level: bool = Field(
        default=False, description="官方有没有「新建关卡」的工具（实测：**没有**，只有 load_level）")
    decision: str = Field(
        default="",
        description=("用户答复的目标：`current`（就用当前这张）/ `existing`（指定的既有图）/ "
                     "`new`（开新图）；**空 = 还没拿到答复**（那 `execute_build` 会被拒收）"))
    answer_recorded: bool = Field(
        default=False, description="用户的答复有没有落盘（`views/build_target_v1.json`）")
    record_path: str = Field(default="", description="答复台账的落盘位置")
    warnings: list[str] = Field(default_factory=list, description="要提醒用户的事")
    question: str = Field(description="要问用户的话")
    options: list[str] = Field(default_factory=list, description="给用户选的选项")
    next_step: str = Field(description="拿到用户答复后下一步做什么")


# --- 阶段三 · 步骤 2：搭建指令表 / 批量落关卡（2026-09-26）----------------------


class BuildOrderRow(BaseModel):
    """**搭建指令表里的一行** = 一个要落进关卡的东西（阶段三第 2 步）。

    ⚠ 这是"翻译件"不是"重新规划"：`loc_cm` 由 plan 的 `pos`（米）×100 得来，
      **一个字都不许在这里改**。plan 里没有的只有两样，都在这里补上：
      ① **Z**（竖直标高，规则见 `WHITEBOX_VERTICAL` / `ASSET_PIVOT_LIFT_CM`）；
      ② 白膜的**图元**（`primitive`）与**尺寸**（`size_cm`）—— 官方 PrimitiveTools
         没有 `add_plane`，所以 `plane` 一律压成极薄的 cube（口径见 docs/阶段三 §5）。
    """

    index: int = Field(description="序号（1 起，**按五层搭建顺序重新编号**，见 `layer`）")
    layer: str = Field(
        default="",
        description=("这一行属于哪一层（**搭建顺序**，2026-09-27 用户指令）："
                     "① 世界地基 → ② 地皮 → ③ 建筑层 → ④ 设施层 → ⑤ 植被层；"
                     "不在五层表里的归「（未分层）」并排在最后。"))
    label: str = Field(description="plan 里的 label（原样带出，便于追溯）")
    uid: str = Field(
        default="",
        description=("**稳定身份**（`element_key`|`label`）—— 增量搭建靠它把这一行与台账那一行对齐。"
                     "⚠ 不要用 `index` 对账：中间插一行会让后面的行号全部平移。"))
    name: str = Field(description="真正给 UE 的 Actor 名（由 label 洗成可当对象名的字符串）")
    kind: str = Field(description="`asset`（用资产摆）/ `whitebox`（用图元搭）")
    element_key: str = Field(default="", description="元素关键词（与阶段一/二一致）")
    asset_path: str = Field(default="", description="资产包路径；**白膜行为空**")
    primitive: str = Field(default="", description="白膜用哪个图元：`cube`（plane 也压成 cube）")
    loc_cm: list[float] = Field(default_factory=list, description="UE 世界坐标 [X, Y, Z]（厘米）")
    rot: dict = Field(default_factory=dict, description="UE 旋转 {pitch, yaw, roll}（度）")
    scale: list[float] = Field(default_factory=list, description="UE 缩放（资产行取 plan.scale；白膜恒 [1,1,1]）")
    size_cm: list[float] | None = Field(
        default=None, description="白膜图元尺寸 [X, Y, Z]（厘米）；资产行为 null"
    )
    folder: str = Field(description=f"outliner 分组（幂等对账与一键清理的判据），如 {OUR_FOLDER_ROOT}/trees")
    surface_material_hint: str = Field(
        default="", description="阶段五贴表面要用的材质实例（**阶段三不贴**，且未验证存在性）"
    )
    note: str = Field(default="", description="plan 里那行的 note（原样带出）+ 本工具补的 Z 依据")


class BuildOrdersResult(BaseModel):
    """搭建指令表（`views/build_orders_v1.json`）—— **离线算的**，一个 Actor 都没落。"""

    stage: str = Field(description="阶段名")
    data_path: str = Field(description="指令表落盘位置（阶段三换执行器时读它，不用重做）")
    plan_hash: str = Field(description="算这张表时 plan_v1.json 的几何指纹（变了这张表就作废）")
    world: dict = Field(default_factory=dict, description="世界中心 / 大小 / 包围盒（都是米）")
    counts: dict = Field(default_factory=dict, description="行数对账：资产行 / 白膜行 / 合计 / 各元素各几行")
    z_rules: list[str] = Field(
        default_factory=list, description="Z 是怎么推出来的（逐条列给人看，方便他一句『这个不对』就能改）"
    )
    rows: list[BuildOrderRow] = Field(default_factory=list, description="指令表全文（一物一行）")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")
    next_step: str = Field(description="下一步做什么")


class BuildRowResult(BaseModel):
    """一行**执行结果**（落没落成、落在哪、读回来对不对）。"""

    index: int = Field(description="与指令表的 `index` 对应")
    label: str = Field(default="", description="指令表里的 label")
    kind: str = Field(default="", description="`asset` / `whitebox`")
    actor: str = Field(default="", description="落成后官方返回的 Actor 引用（失败为空）")
    loc_cm: list[float] = Field(default_factory=list, description="**读回来**的坐标（厘米；没读为空）")
    yaw: float | None = Field(default=None, description="**读回来**的 yaw（度）")
    scale: list[float] = Field(default_factory=list, description="**读回来**的缩放")
    ok: bool = Field(default=True, description="这一行落成了吗")
    error: str = Field(default="", description="没落成 / 读回对不上的原文说明")


class BuildReport(BaseModel):
    """阶段三第 2 步的执行报告 —— 落完（或拒收 / 演练）之后的**如实交代**。"""

    stage: str = Field(description="阶段名")
    level: str = Field(description="落在哪张图（当前关卡）")
    dry_run: bool = Field(description="true = 只校验对账，**一个 Actor 都没落**")
    refused: bool = Field(default=False, description="被拒收了吗（拒收 = 一个 Actor 都没落）")
    reason: str = Field(default="", description="拒收原因（原文）")
    plan_hash: str = Field(default="", description="执行的是哪一版几何指纹")
    mode: str = Field(default="full",
                      description="这次是 `full`（全量重摆）还是 `incremental`（只动与台账比变了的行）")
    ledger_path: str = Field(default="", description="搭建台账（我们自己的文件，记上次搭了什么）")
    ledger_written: bool = Field(default=False, description="这次有没有更新台账")
    diff: dict = Field(
        default_factory=dict,
        description="增量差异：要删 / 要摆 / 没动 / 认领回来的 / 台账说在关卡里却没了 的行")
    planned: int = Field(default=0, description="指令表一共几行")
    removed: int = Field(default=0, description=f"开摆前从 {OUR_FOLDER_ROOT}/ 清掉几个旧 Actor（幂等）")
    placed: int = Field(default=0, description="成功落了几个")
    verified: int = Field(default=0, description="读回对账通过几个")
    mismatches: list[str] = Field(default_factory=list, description="读回来对不上的行（对不上就是没摆对）")
    groups: dict = Field(default_factory=dict, description="各 outliner 分组各落了几个")
    rows: list[BuildRowResult] = Field(default_factory=list, description="逐行结果（含官方返回的 Actor 引用）")
    official_calls: int = Field(default=0, description="这次一共调了官方几次（留痕，方便复核）")
    next_step: str = Field(description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class ElementItem(BaseModel):
    """参考图里需要的一个元素。"""

    element: str = Field(description="元素名，如 house / road / tree / sidewalk")
    category: str = Field(
        default="",
        description=(
            "**大类 key**（照 `config/asset_categories.json` 那张表填，如 building / nature / "
            "environment / furniture / props）。这是 2026-09-24 用户要求的：先有大类表，"
            "再按参考图把表填好，然后才去找资产。"
            "⚠ 填表里没有的 key 会被**拒收**（表就是词表）；实在没把握就填 uncategorized，"
            "并在 note 里写明为什么"
        ),
    )
    subcategory: str = Field(
        default="",
        description="更细的一层（自由文本，可留空；如 building 下的 house / garage）。**不参与判定**，只给人看",
    )
    count: int | None = Field(
        default=None,
        description=(
            "**预估数量**（画面里能看到几个）。2026-09-24 用户要求：**有数量的就要填**。"
            "⚠ 材质 / 面这种没有『个数』概念的行可以留空，但要在 note 里写明为什么没有数量。"
            "⚠ 场景项（地图大小 / 地图对应时间）不用填。"
        ),
    )
    count_source: str = Field(
        default="",
        description=(
            "数量是怎么来的：`预估（参考图目测）` / `实测（图上数出来的）` 等 —— "
            "**是估的就必须写明是估的**，不许让预估值看起来像实测值"
        ),
    )
    size_m: list[float] | None = Field(
        default=None,
        description=(
            "**只给『地图大小』那一行填**：`[X, Y]`（米，预估）—— 从上传图估的场景范围。"
            "其余行留空；是预估就在 note 里写明依据"
        ),
    )
    time_of_day: str = Field(
        default="",
        description=(
            "**只给『地图对应时间』那一行填**：从图里光线判断的时段，如 sunset（黄昏）/ "
            "blue_hour（蓝调）/ noon（正午）/ night（夜）/ overcast（阴天）。其余行留空"
        ),
    )
    note: str = Field(default="", description="备注：在画面里的位置、为什么没有数量、估的依据等")


class ElementList(BaseModel):
    """**用户确认过**的元素清单（= 按大类填好的那张表）。"""

    confirmed_at: str = Field(description="确认时间")
    source_image: str = Field(description="参考图路径（留档）")
    total: int = Field(description="几个元素")
    elements: list[ElementItem] = Field(description="元素清单")
    groups: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "**按大类分组**：`{中文大类名: [元素名, ...]}`，顺序照大类表，未分类排最后 —— "
            "这就是「根据参考图填好的那张表」，拿它逐类跟用户确认"
        ),
    )
    categories_defined: int = Field(
        default=0, description="大类表里一共定义了几项（读不到表时会是 0 —— 那就该去查表在不在）"
    )
    scene: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "**场景项**（表里 `kind=scene` 那两行，**不是资产**）：`{中文名: 人话值}`，"
            "如 `{\"地图大小\": \"80×80 m（预估）\", \"地图对应时间\": \"sunset（黄昏）\"}`"
        ),
    )
    scene_map_size_m: list[float] | None = Field(
        default=None,
        description="场景项『地图大小』的结构化值 `[X, Y]`（米，预估）；没填就是 null",
    )
    scene_time_of_day: str = Field(
        default="", description="场景项『地图对应时间』的值（如 sunset / blue_hour）；没填就是空"
    )
    no_count: list[str] = Field(
        default_factory=list,
        description=(
            "**没填预估数量的元素**。材质 / 面这种没有『个数』概念的可以没有数量 —— "
            "但要在该行 note 里写明；其余的行请补上 count"
        ),
    )
    next_step: str = Field(description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class FoundAsset(BaseModel):
    """按某个元素找到的一个候选资产。"""

    name: str = Field(description="资产名（可能没有语义，如 MI_sjfnch0a）")
    path: str = Field(description="包路径")
    asset_type: str = Field(description="类别：mesh / material / material_instance")
    size_cm: list[float] | None = Field(
        default=None, description="实测尺寸 [X,Y,Z]（厘米）。只有网格有，材质是 null"
    )
    matched_by: str = Field(description="怎么命中的：资产名 / 文件夹名")
    matched_segment: str = Field(
        default="", description="按文件夹命中时，命中的那一段（这就是它的语义来源）"
    )


class ElementPlan(BaseModel):
    """一个元素的查找结果。"""

    element: str = Field(description="元素名")
    status: str = Field(description="found = 名字对得上；missing = 没找到")
    assets: list[FoundAsset] = Field(description="找到的候选（缺失时为空）")
    placeholder_hint: str = Field(
        default="",
        description=(
            "**白膜提示**（2026-09-24 用户要求）：这个元素**没有可用资产**，"
            "必须在 confirm_assets 里登记成**白膜占位**（`is_placeholder=true` + "
            "`placeholder_shape` + **预估** `size_cm`），再让用户确认清单。"
            "空串 = 有可用的网格资产，按普通行登记。"
            "⚠ 有值时**不许**把它当普通资产行交出去（那样清单会带一条不存在的路径）。"
        ),
    )
    question: str = Field(description="**要原样问用户的话**")


class PlanReport(BaseModel):
    """阶段一的查找报告。"""

    stage: str = Field(description="当前阶段")
    needs_user_confirmation: bool = Field(description="恒为 true —— 用户确认前不许往下走")
    source: str = Field(description="元素清单从哪来的：用户确认过的清单 / 本次传入的关键词")
    total_elements: int = Field(description="一共查了几个元素")
    found: int = Field(description="找到了几个")
    missing: int = Field(description="缺了几个")
    elements: list[ElementPlan] = Field(description="逐元素结果")
    by_category: dict[str, dict[str, list[str]]] = Field(
        default_factory=dict,
        description=(
            '**按大类分组的结果**：`{中文大类名: {"found": [...], "missing": [...]}}`（顺序照大类表）'
            " —— 拿它**逐类核对有没有漏**。"
            "⚠ 大类**不是搜索过滤参数**：官方 find_assets 只能按资产名/文件夹名/类路径找，"
            "这里只是把结果归到类里，不许说成『按类搜索』"
        ),
    )
    scene: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "**场景项**（表里 `kind=scene` 的那两行，**不是资产**、本次没去搜）："
            "`{中文名: 人话值}`，如 `{'地图大小': '80×80 m（预估）', '地图对应时间': 'sunset'}`"
        ),
    )
    official_calls: int = Field(description="调了几次官方工具")
    next_step: str = Field(description="下一步做什么")
    warnings: list[str] = Field(description="要提醒的坑（不藏着）")


class RenameItem(BaseModel):
    """要改名的一个资产（路径和名字由**用户**提供）。"""

    path: str = Field(description="现在的完整包路径")
    new_name: str = Field(description="新的资产名（**只给名字**，不要带路径和斜杠）")


class RenameResult(BaseModel):
    """一个资产的改名结果。"""

    old_path: str = Field(description="改名前路径")
    new_path: str = Field(description="改名后路径")
    referencers: list[str] = Field(description="谁引用了它（改名前查的，看影响面）")
    ok: bool = Field(description="成功没有")
    error: str = Field(default="", description="失败原因（官方原文）")


class RenameReport(BaseModel):
    """一批改名的结果。"""

    dry_run: bool = Field(description="true = 只预览，**没有真改**")
    requested: int = Field(description="要求改几个")
    renamed: int = Field(description="真改成功几个（dry_run 时恒为 0）")
    failed: int = Field(description="几个不合法或失败")
    items: list[RenameResult] = Field(description="逐个结果")
    note: str = Field(description="说明与风险提示")


class AssetListItem(BaseModel):
    """使用清单里的一行：一个元素用哪个资产。"""

    element: str = Field(description="画面元素名（给人看的标签，可带说明，如 'house 主屋'）")
    element_key: str = Field(
        default="",
        description=(
            "**元素关键词**（如 house / sidewalk），必须和 elements.json 里的元素名一致 —— "
            "get_asset_list 靠它判断哪些元素已经落位；"
            "留空会退回『标签首词推断』（不可靠，会明说是推断）"
        ),
    )
    asset_path: str = Field(
        default="",
        description=(
            "确认使用的资产包路径。⚠ **白膜占位行留空**（见 is_placeholder）—— "
            "那种元素没有可用资产，没有路径可验证"
        ),
    )
    category: str = Field(
        default="",
        description=(
            "**大类 key**（照 `config/asset_categories.json` 那张表，与该元素在 elements.json 里"
            "记的一致）。**不填也行**：本工具会去 elements.json 那张表里按关键词取；"
            "两边都没有才记成 uncategorized。中文名由本工具查表回填，调用方**不用填中文名**"
        ),
    )
    category_cn: str = Field(
        default="",
        description="大类中文名（**本工具查表回填**，调用方不用填）",
    )
    asset_type: str = Field(default="", description="类别：mesh / material_instance / material / whitebox")
    is_placeholder: bool = Field(
        default=False,
        description=(
            "**白膜占位**（whitebox）：这个元素**没有可用资产** —— 找不到、用户确认没有，"
            "或者现在只有**材质实例顶着**（材质没有包围盒、摆不出实体）。"
            "这类行：不做 exists() 验证、尺寸用调用方给的**预估值**（不被实测覆盖）、"
            "asset_type 记 whitebox。⚠ **只是清单登记** —— 阶段一不许往关卡里摆任何东西。"
        ),
    )
    placeholder_shape: str = Field(
        default="",
        description=(
            "白膜形状：`cube`（体，如窗户/车道这种有体积的）/ `plane`（面，如马路/草坪这种覆盖面）。"
            "非白膜行留空。阶段三按这个形状用官方 PrimitiveTools 加图元实例化。"
        ),
    )
    exists: bool = Field(
        # ⚠ `default` 不能删：本字段由工具自己回填（见函数体里的 model_copy），调用方不填。
        #   2026-09-23 曾因缺 default 被 pydantic 判成 required，与下面那句描述自相矛盾 ——
        #   照描述办事的调用方一律被参数校验拒收，工具连函数体都进不去（check_tools 实测复现）。
        default=False,
        description=(
            "官方 exists() 验证结果 —— **必须为 true**（白膜占位行为 false：没有路径可验）。"
            "⚠ 调用方**不用填**：本工具会逐个调官方 exists() 把它覆盖掉"
        ),
    )
    size_cm: list[float] | None = Field(
        default=None,
        description=(
            "**实测**包围盒尺寸 [X, Y, Z]（厘米）—— 由 confirm_assets 调官方 get_bounds 回填，"
            "调用方填什么都会被覆盖；非网格（材质等）为 null。"
            "⚠ **白膜占位行例外**：那种行没有东西可测，必须由调用方给**预估尺寸**（至少 X、Y），"
            "预估值**不会被覆盖** —— 但必须在 size_source 里写明是预估"
        ),
    )
    bbox_volume_cm3: float | None = Field(
        default=None,
        description="**包围盒体积**（厘米³）= X×Y×Z；⚠ 是包围盒，不是网格实际体积",
    )
    size_source: str = Field(
        default="",
        description=(
            "尺寸是怎么来的 / 为什么没有：`官方 get_bounds 实测（包围盒）` / "
            "`<类别> 没有包围盒` / `预估（参考图目测，待核实）` / 量取失败原因"
        ),
    )
    note: str = Field(default="", description="备注（谁改的名、为什么选它、白膜为什么用这个尺寸等）")


class UnresolvedElement(BaseModel):
    """**元素清单里有、使用表里没有**对应资产的元素 —— 免得它静默消失。

    为什么要有这个结构（2026-09-23 实测暴露）：
      `elements.json` 确认了 14 条 / 9 个关键词，使用表只有 6 行 ——
      `window` / `sidewalk` / `driveway` 三个关键词**在使用表里没有任何交代**。
      信息其实没丢（`get_asset_list` 每次都会报 `missing_elements`），
      但**单看使用物本身看不出清单不完整** —— 读表的人会以为一共就 6 个元素。
    """

    element_key: str = Field(description="元素关键词（与 elements.json 里的元素名一致）")
    occurrences: int = Field(default=0, description="该关键词在元素清单里出现几处")
    note: str = Field(
        default="",
        description="元素清单里对它的说明 —— **原样带出，不改写**",
    )


class AssetListDelivery(BaseModel):
    """**最终使用物**：给用户的资产清单。"""

    delivered_at: str = Field(description="使用时间")
    source_image: str = Field(description="参考图路径")
    total: int = Field(description="一共几个元素")
    verified: int = Field(description="路径验证通过几个")
    placeholders: int = Field(
        default=0,
        description=(
            "**白膜占位**几行（没有可用资产的元素 / 只有材质实例顶着的元素）—— "
            "这些行的尺寸是**预估**，请用户重点确认"
        ),
    )
    invalid_paths: list[str] = Field(
        default_factory=list,
        description=(
            "**验证不存在的路径**。⚠ 2026-09-24 整改后**恒为空**："
            "路径验不过的行会被 confirm_assets **当场拒收**（要求改成白膜占位或给对路径），"
            "根本走不到使用这一步。字段保留是为了不破坏使用物结构"
        ),
    )
    items: list[AssetListItem] = Field(description="资产清单")
    unresolved_elements: list[UnresolvedElement] = Field(
        default_factory=list,
        description=(
            "**元素清单里有、本表里没有对应资产**的元素 —— 不许静默消失。"
            "note 是元素清单原话（可能已写明归属，如『按光照环境处理』）。"
            "⚠ 本工具**不推断**没落位的原因：只说事实，要原因得问用户。"
            "⚠ 已经用**白膜占位**登记过的元素**不算未落位**（它已经有交代了）。"
        ),
    )
    deliverable: str = Field(description="给人看的清单正文（直接贴给用户）")
    note: str = Field(description="说明与下一步")


class AssetListRow(BaseModel):
    """使用表里的**一行**（get_asset_list 用）—— 原样带出表里记的尺寸信息。"""

    element: str = Field(default="", description="画面元素标签")
    element_key: str = Field(default="", description="元素关键词（表里显式记的那个）")
    category: str = Field(default="", description="大类 key")
    category_cn: str = Field(default="", description="大类中文名（使用时查表回填的）")
    asset_path: str = Field(default="", description="资产包路径")
    asset_type: str = Field(
        default="", description="类别：mesh / material_instance / material / whitebox"
    )
    is_placeholder: bool = Field(
        default=False, description="**白膜占位**行（没有可用资产；尺寸是**预估**、不是实测）"
    )
    placeholder_shape: str = Field(
        default="", description="白膜形状：cube（体）/ plane（面）；非白膜行留空"
    )
    size_cm: list[float] | None = Field(
        default=None,
        description=(
            "使用时记录的包围盒尺寸 [X, Y, Z]（厘米）。"
            "⚠ 白膜占位行这里是**预估尺寸**（见 size_source），不是实测"
        ),
    )
    bbox_volume_cm3: float | None = Field(
        default=None, description="包围盒体积（厘米³）；⚠ 是包围盒，不是网格实际体积"
    )
    size_source: str = Field(default="", description="尺寸来源 / 为什么没有尺寸")
    note: str = Field(default="", description="使用时的备注")


class PathCheck(BaseModel):
    """清单里**一条路径**的验活结果（get_asset_list 用）。"""

    element: str = Field(default="", description="这一行对应的元素标签")
    asset_path: str = Field(description="清单里记的资产路径")
    ok: bool = Field(description="官方 exists() 是否通过")
    error: str = Field(default="", description="验证失败时的官方原文")


class AssetListStatus(BaseModel):
    """**「当前这张资产清单还作不作数」**的判定结果（get_asset_list 用）。

    字段分成四组，对应它回答的四个问题：
      有没有表   → has_list / delivered_at / source_image / total
      表变没变   → stale / signed_* / live_* / changed_types
      路径还活吗 → path_check / dead_paths
      缺的有着落吗 → missing_elements / new_candidates / coverage_mode
    """

    has_list: bool = Field(description="有没有使用过清单（false 时其余字段无意义）")
    delivered_at: str = Field(default="", description="清单是什么时候使用的")
    source_image: str = Field(default="", description="当初那张参考图")
    total: int = Field(default=0, description="清单里共几条")
    placeholders: int = Field(
        default=0,
        description="其中**白膜占位**几条（这些行的尺寸是**预估**，不是实测 —— 别当实测值用）",
    )
    items: list[AssetListRow] = Field(
        default_factory=list,
        description="表里的**每一行**（含实测尺寸/包围盒体积），原样带出，不做二次加工",
    )
    stale: bool | None = Field(
        default=None,
        description="指纹对不上 = true（表已过期）；没有指纹可比 = null（旧版本落的表）",
    )
    signed_at: str = Field(default="", description="指纹是什么时候记的")
    signed_hash: str = Field(default="", description="签字时的资产库指纹")
    signed_counts: dict[str, int] = Field(
        default_factory=dict, description="签字时每类资产各多少个"
    )
    live_hash: str = Field(default="", description="刚测出来的资产库指纹")
    live_counts: dict[str, int] = Field(
        default_factory=dict, description="刚测出来每类资产各多少个"
    )
    changed_types: list[str] = Field(
        default_factory=list, description="变了的那几类，人话写法，如 'material_instance: 58 → 56'"
    )
    path_check: list[PathCheck] = Field(
        default_factory=list, description="逐条验活结果（verify_paths=false 时为空）"
    )
    dead_paths: list[str] = Field(
        default_factory=list, description="**已经验不存在的路径**（必须处理掉才能往下走）"
    )
    missing_elements: list[str] = Field(
        default_factory=list, description="还没有资产落位的元素关键词"
    )
    missing_by_category: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "**上面那些缺失元素按大类分组**：`{中文大类名: [元素关键词...]}`（顺序照大类表）—— "
            "一眼看出哪一类还缺东西"
        ),
    )
    scene: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "**场景项**（从 `catalog/elements.json` 原样带出，**不是资产**）："
            "`{中文名: 人话值}`，如 `{'地图大小': '80×80 m（预估）', '地图对应时间': 'sunset'}`；"
            "没填过就是空"
        ),
    )
    new_candidates: dict[str, list[str]] = Field(
        default_factory=dict, description="缺的元素在实况库存里**名字对得上**的新候选（元素 → 路径）"
    )
    coverage_mode: str = Field(
        default="", description="『元素已落位』是怎么判的：element_key 显式标注 / 标签首词推断"
    )
    official_calls: int = Field(default=0, description="本次调了几次官方工具")
    needs_user_confirmation: bool = Field(
        default=True, description="恒为 true —— 用户确认前不许往下走"
    )
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class PlanGate(BaseModel):
    """规划图的**闸门状态**：这张图还作不作数。"""

    confirmed: bool = Field(description="用户/客户是否已确认这张图")
    params_hash_ok: bool = Field(
        default=True,
        description=(
            "⚠ 2026-09-24 起**恒为 true**：参数文件已删（参数由调用方给），没有"
            "『哪份参数』可比。字段保留只为兼容旧 plan_v1.json（老文件里存过 params_hash）。"
            "闸门现在只有 `plan_hash_ok` 一道。"
        ),
    )
    plan_hash_ok: bool = Field(
        description="几何指纹是否仍与算出来时一致（false = 文件被手改过 → 这张图作废）"
    )
    confirmed_by: str = Field(default="", description="谁确认的")
    confirmed_at: str = Field(default="", description="什么时候确认的（UTC）")
    params_hash: str = Field(default="", description="（已废弃，恒空）")
    plan_hash: str = Field(default="", description="当前几何指纹")


class PlanSummary(BaseModel):
    """放置规模摘要（给人看的几个数）。"""

    world_center: list[float] = Field(default_factory=list, description="平面世界中心 [X, Y]（米）")
    world_size: list[float] = Field(default_factory=list, description="平面世界大小 [X, Y]（米）")
    assets: int = Field(default=0, description="已有资产的放置条数（一物一行）")
    whiteboxes: int = Field(default=0, description="白膜占位的条数（一物一行）")
    # ⚠ 2026-09-25：原来这里有 checks_total / checks_failed / failed_names —— 阶段二自检整段删除
    #   （用户指令），字段一并去掉。碰撞检测与布局修正**已整段删除**（2026-09-27 用户指令：不再单列阶段）。


class PlanAcceptance(BaseModel):
    """**阶段二验收**的状态（2026-09-25 用户要求新增）。

    位置：**阶段二（规划 + 生图）之后、第三阶段之前**。生图 = 阶段二结束，进这一段；
    用户在图上看过说"行"才放行进第三阶段，说"要改"就回到「改参数 → 重落盘 → 重画图」。

    ⚠ `state` 是**现算**的（`planning.acceptance_state()`），不是台账里那个旧快照 ——
      因为图常常是"落盘之后"才画好的，读快照会拿着"等出图"的旧字样回答"走到哪了"。
    """

    stage: str = Field(default="阶段二验收", description="阶段名")
    round: int = Field(default=0, description="第几轮（**几何指纹变一次 = 新的一轮**，同一版重复落盘不算）")
    state: str = Field(
        default="",
        description=(
            "`not_started` 空表 / `awaiting_figure` 等出图 / `awaiting_user` 等用户看图确认 / "
            "`changes_requested` 用户提了要改 / `accepted` 验收通过。"
            "⚠ `awaiting_user` 的含义是「**该出的每一路图都认账**」（见 `REQUIRED_VIEWS`：恒两张）"
            "—— 只画了一张（缺另一路）会退回 `awaiting_figure`，因为那时 `confirm_plan` 会拒收。"
        ),
    )
    state_cn: str = Field(default="", description="上面那个状态的中文说明（给人看）")
    plan_hash: str = Field(default="", description="这一轮验的是**哪个几何指纹**")
    figures: list[str] = Field(
        default_factory=list,
        description="**认这份数据**的图（图名）—— 就是该**使用成卡片给用户看**的那几张；空 = 还没画或图与数据不一致",
    )
    pending_changes: list[str] = Field(
        default_factory=list,
        description="用户在验收时提出、**还没落到数据里**的改动点（用户原话）",
    )
    change_window_open: bool = Field(
        default=False,
        description=(
            "**「改动窗口」开着没有**（2026-09-26 加）：开 = 台账里已经有「要改什么 / 谁要的」的记录，"
            "`generate_plan` 才准改几何。`request_plan_change()` 打开它（对象是用户原话或 `agent 自查`），"
            "`confirm_plan()` 关上它。关着的时候直接改几何 → `generate_plan` **拒收**"
            "（客户 agent 自评：他一次都没调过 `request_plan_change`，直接 patch）"
        ),
    )
    change_set: dict = Field(
        default_factory=dict,
        description=(
            "**这一版相对『上一次用户确认过的那一版』改了什么**（2026-09-26 起）："
            "`{local, baseline, added, changed, removed, same, required_labels, changed_dims, "
            "required_views, views_why, note}`。"
            "`local=true` = 局部一轮：**图里只要出现 `required_labels` 那几行**，"
            "没动的行不必重画（用户要求：局部改就全局部，不要全部重做）；"
            "`local=false` = 还没有基线（第一次搭）→ 图里要出现**每一行**。"
            "⚠ **`required_views`（2026-09-30 加）决定要出哪几张图**：`top` = 顶视图（平面图，俯视）；"
            "`elevation` = 「原图视角」透视示意图。**口径（用户最终定的）：位移 / 朝向 → 顶视图；"
            "大小 / 高度（含 Z 标高 / Z 倍率）→ 「原图视角」透视示意图**；两样都动 → 两张都要。"
            "为什么是这几张，看 `views_why`。"
        ),
    )
    awaiting_readback: dict = Field(
        default_factory=dict,
        description=(
            "**「这一版还没回读」的登记**（2026-09-26 把软约束变硬）：非空 = 在你调 `get_plan()` "
            "之前，`generate_plan` 与 `execute_build` 都会被**拒收**。里面带 "
            "`plan_hash` / `at` / `change_set`（改了什么，逐行）。调一次 `get_plan()` 即清掉。"
        ),
    )
    updated_at: str = Field(default="", description="台账最后更新时间（UTC）")
    ledger_path: str = Field(default="", description="验收台账文件：views/acceptance.json")
    history: list[dict] = Field(
        default_factory=list, description="台账最近几条事件（谁 / 何时 / 第几轮 / 哪个指纹 / 干了什么）"
    )


class PlanResult(BaseModel):
    """算几何 / 确认的结果。"""

    stage: str = Field(description="阶段二 · 平面放置规划")
    unit: str = Field(default="m", description="单位（米制）")
    data_path: str = Field(description="几何数据文件 —— **后续计算只读它**")
    image_path: str = Field(
        default="",
        description=(
            "⚠ 2026-09-24 起**恒为空**：不再由代码按固定模板画图（用户拍板）。"
            "要图就自己拿 `data_path` 的几何数据去画 —— 画法不写死在代码里。"
        ),
    )
    summary: PlanSummary = Field(description="规模摘要")
    gate: PlanGate = Field(description="闸门状态")
    acceptance: PlanAcceptance | None = Field(
        default=None,
        description=(
            "**阶段二验收**状态（生图后进这一段）：第几轮、等谁、要使用哪几张图、"
            "用户提了哪些要改的地方。`figures` 就是**要使用给用户打开的图**。"
        ),
    )
    changed_params: list[str] = Field(
        default_factory=list, description="本次覆盖了哪些参数（点号路径；空 = 没改）"
    )
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class PlanStatus(BaseModel):
    """『当前规划图还作不作数』+ 计划本体（get_plan 用）。"""

    has_plan: bool = Field(description="有没有算过规划")
    status: str = Field(
        default="",
        description=(
            "`initialized` = **两张表都空**（结构立好了，但还没有任何坐标 —— 图的依据没来）；"
            "`draft` = 有内容、等着用户确认；`confirmed` = 已确认"
        ),
    )
    data_path: str = Field(default="", description="几何数据文件")
    image_path: str = Field(
        default="", description="（恒空 —— 图由 AI 拿 data_path 自己画，代码不出图）"
    )
    summary: PlanSummary = Field(default_factory=PlanSummary, description="规模摘要")
    gate: PlanGate | None = Field(
        default=None, description="闸门状态；**confirmed=false 时不许进第三阶段**"
    )
    acceptance: PlanAcceptance | None = Field(
        default=None,
        description=(
            "**阶段二验收**状态（现算）：`awaiting_user` = 图与数据一致、该使用给用户了；"
            "`changes_requested` = 用户提了改动、还没落到数据里；`accepted` = 验收通过、闸门开。"
        ),
    )
    plan: dict | None = Field(
        default=None, description="完整计划数据（include_plan=true 时带出）"
    )
    drawing: dict | None = Field(
        default=None,
        description=(
            "**画图用的逐行几何**（2026-09-30 加；`include_plan=true` 且规划非空时带出）："
            "`{unit, world, coordinate_system, required_views, views_why, required_labels, "
            "changed_dims, rows, removed_labels, z_rules, note}`，"
            "每行 = `{label, kind, element_key, layer, x_m, y_m, z_base_m, z_top_m, w_m, d_m, h_m, "
            "rot_deg, changed}`（**单位米**）。"
            "它的 Z 口径与阶段三**同源**（`_compose_build_rows()`）⇒ 平面图 / 立面图都用它画，"
            "**别自己推 Z**（推错 = 图与数据不一致 = 等于没确认）。"
            "⚠ 它是**只读派生值**：不写进 `plan_v1.json`、**不进几何指纹**（读状态不会作废确认）。"
        ),
    )
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑")


class SurfaceRowResult(BaseModel):
    """**一行贴材质的结果**（阶段五：给白膜贴表面）。"""

    label: str = Field(default="", description="台账里这一行的 label")
    element_key: str = Field(default="", description="元素关键词（决定贴哪块材质）")
    material: str = Field(default="", description="要贴的材质实例（包路径）")
    actor: str = Field(default="", description="台账里记的 Actor 引用")
    status: str = Field(
        default="",
        description=("`applied` 贴上了 / `already` 本来就是这块材质（幂等跳过）/ "
                     "`failed` 没成（看 error）/ `dry` 演练，没写"),
    )
    error: str = Field(default="", description="没成的原因（官方原文）")


class SurfaceReport(BaseModel):
    """**阶段五 · 第 1 步**的执行报告：给白膜贴表面材质。

    ⚠ 只贴**白膜行**（台账里 `kind=whitebox`）—— 资产行（house / tree）**自带材质**，不碰。
    ⚠ 贴的是**组件级覆盖**（`StaticMeshComponent.overrideMaterials`），**不改资产**：
      改资产会把 `/Engine/BasicShapes/Cube` 这种引擎自带网格一起改掉（所有实例跟着变）。
    """

    stage: str = Field(description="阶段名")
    level: str = Field(default="", description="在哪张图上贴的")
    dry_run: bool = Field(default=False, description="true = 只算 + 校验，**一个组件都没写**")
    planned: int = Field(default=0, description="这次要处理几行")
    applied: int = Field(default=0, description="真贴上了几行")
    already: int = Field(default=0, description="本来就是这块材质、跳过几行（幂等）")
    failed: int = Field(default=0, description="几行没成（看 rows 里的 error）")
    asset_rows_untouched: int = Field(
        default=0, description="资产行**没碰**几个（它们自带材质）"
    )
    rows: list[SurfaceRowResult] = Field(default_factory=list, description="逐行结果")
    official_calls: int = Field(default=0, description="这次调了官方几次（留痕，方便复核）")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class ExchangeReport(BaseModel):
    """**阶段四 · 导出/交换**的报告：把布局导成一份与 DCC 无关的场景描述。

    ⚠ 只能导**场景描述**，导不了几何（官方没有场景导出工具，实测）—— 消费端是照着这份
      描述**重摆一遍**，不是"把模型搬过去"。
    ⚠ 这份文件是**派生物**：权威几何永远是 `views/plan_v1.json`。
    """

    stage: str = Field(description="阶段名")
    json_path: str = Field(default="", description="主文件落盘位置（机器读）")
    csv_path: str = Field(default="", description="表格视图落盘位置（人看 / 表格工具）")
    plan_hash: str = Field(default="", description="导出时 plan 的几何指纹（对得上才说明是同版）")
    level: str = Field(default="", description="当时 UE 开着哪张图（读不到就是空）")
    objects: int = Field(default=0, description="导出几个物体（一物一条）")
    stats: dict = Field(default_factory=dict, description="统计：按类 / 按层 / 面积等")
    ue_written: list[str] = Field(default_factory=list, description="写进 UE 工程的路径（没写就是空）")
    ue_error: str = Field(default="", description="写 UE 那一步失败的原文（不影响我们自己的文件）")
    official_calls: int = Field(default=0, description="调了官方几次（留痕）")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class ExchangeDiffReport(BaseModel):
    """**阶段四 · 回读对账**的报告。

    ⚠ 它**不改 plan、不写任何文件**，也**不是"用户确认"** —— 只是把外部改过的地方列出来。
      要落到 plan 必须回阶段二：`request_plan_change` → `generate_plan(patch=…)` → 重画图 → 用户确认。
    """

    stage: str = Field(description="阶段名")
    json_path: str = Field(default="", description="比的哪份交换文件")
    plan_hash: str = Field(default="", description="现在这版 plan 的几何指纹")
    file_plan_hash: str = Field(default="", description="这份文件**导出时**记的 plan 指纹")
    hash_match: bool = Field(default=False, description="文件是不是按**现在这一版**导出的")
    same: int = Field(default=0, description="两边一致的几条")
    changed: int = Field(default=0, description="改过的几条")
    added: int = Field(default=0, description="文件里有、plan 里没有的几条")
    removed: int = Field(default=0, description="plan 里有、文件里没有的几条")
    rows: list[dict] = Field(default_factory=list, description="逐条差异（id / label / 为什么）")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


# --- 阶段六 · 环境搭建（＋ 阶段七 · 相机预览，2026-09-29）-------------------------


class EnvRowResult(BaseModel):
    """一个**环境因素**的处理结果（太阳 / 大气 / 天光 / 雾 / 云 / 后处理 各一条）。"""

    factor: str = Field(description="因素 key（sun / sky_atmosphere / sky_light / fog / cloud / post_process）")
    name_cn: str = Field(default="", description="中文名（给人看）")
    actor: str = Field(default="", description="改的是哪个 Actor（参数在组件上时也记它的Agent Actor）")
    component: str = Field(default="", description="参数写在哪个组件上（后处理写在 Actor 上就留空）")
    status: str = Field(
        default="",
        description="applied 写了 / already 本来就一样（幂等跳过）/ failed 没成 / dry 演练 / spawned 新建了 Actor",
    )
    error: str = Field(default="", description="没成的原因原文（不掩饰）")
    wrote: dict = Field(default_factory=dict, description="这次**真正写下去**的属性（值）")
    before: dict = Field(default_factory=dict, description="**改之前的现值** —— 回滚依据，也进台账")
    after: dict = Field(default_factory=dict, description="**读回**的值（写进去但读回不是它 = 没成）")
    property_count: int = Field(default=0, description="这个因素动了几条属性（含朝向）")


class EnvReport(BaseModel):
    """**阶段六 · 环境搭建**的报告。

    ⚠ 走的是「**复用并改关卡原生的环境 Actor**」这条路（2026-09-29 用户在两条路里选的 a）——
      所以**改前先记现值**，`views/environment_state_v1.json` 是**可回滚台账**。
    ⚠ **绝不存盘**：配完在 UE 里看，存不存由用户定。
    """

    stage: str = Field(description="阶段名")
    level: str = Field(default="", description="当时 UE 开着哪张图")
    preset: str = Field(description="用的哪个时段预设（如 sunset）")
    preset_source: str = Field(default="", description="预设表从哪读的（配置路径 / 内置兜底）")
    dry_run: bool = Field(default=False, description="是不是演练（演练时一个属性都没写）")
    restore: bool = Field(default=False, description="这次是不是**回滚**（把台账里的现值写回去）")
    planned: int = Field(default=0, description="这次要处理几个因素")
    applied: int = Field(default=0, description="真写了几个因素")
    already: int = Field(default=0, description="本来就一样、跳过几个（幂等）")
    failed: int = Field(default=0, description="几个没成（看 rows 里的 error）")
    spawned: list[str] = Field(default_factory=list, description="这次新建的 Actor（后处理卷通常要新建）")
    ledger_path: str = Field(default="", description="环境台账落盘位置（回滚依据）")
    rows: list[EnvRowResult] = Field(default_factory=list, description="逐因素结果")
    official_calls: int = Field(default=0, description="这次调了官方几次（留痕）")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


class PreviewShot(BaseModel):
    """一个**机位**：站哪儿 + 看哪儿，单位**米**、世界坐标（与 `plan_v1.json` 同一口径）。"""

    name: str = Field(default="", description="这张图叫什么（文件名用它；留空按序号命名）")
    pos_m: list[float] = Field(default_factory=list, description="相机站哪儿 [X, Y, Z]（米）")
    look_at_m: list[float] = Field(default_factory=list, description="看向哪个点 [X, Y, Z]（米）")
    fov: float = Field(default=0.0, description="视野角（度）；0 = 用官方默认（实测 90）")


class PreviewShotResult(BaseModel):
    """一张预览图的结果。"""

    name: str = Field(default="")
    file: str = Field(default="", description="PNG 落盘路径（**图不回传模型**：一张 ≈500 KB）")
    bytes: int = Field(default=0, description="PNG 字节数")
    width: int = Field(default=0, description="像素宽（从 PNG 头里读的，不靠猜）")
    height: int = Field(default=0, description="像素高")
    camera_location: list[float] = Field(default_factory=list, description="官方**回报**的机位（厘米）—— 拿它核对构图")
    camera_rotation: list[float] = Field(default_factory=list, description="官方回报的 [pitch, yaw, roll]（度）")
    fov: float = Field(default=0.0, description="官方回报的视野角")
    labeled_actors: int = Field(default=0, description="这一帧里官方标出来的 Actor 数（>0 = 确实框住了场景）")
    status: str = Field(default="", description="ok / failed / dry")
    error: str = Field(
        default="",
        description="没成的原因原文（不掩饰）；⚠ 图拍成了但**机位对不上**时也写在这里，看 status=ok 即知",
    )


class PreviewReport(BaseModel):
    """**阶段七 · 相机预览**的报告（⚠ 原挂在阶段六，2026-09-29 用户把出图收回后划给阶段七）。

    ⚠ 出图**不动用户的视口相机**（实测 2026-09-29：`captureTransform` 精确生效，
      出图前后 `GetCameraTransform` 一字不变）—— 所以可以放心连拍。
    """

    stage: str = Field(description="阶段名")
    level: str = Field(default="", description="当时 UE 开着哪张图")
    pose_source: str = Field(default="", description="机位哪来的（调用方给的 / 按 plan 世界范围推的）")
    planned: int = Field(default=0, description="要拍几张")
    ok: int = Field(default=0, description="成功几张")
    failed: int = Field(default=0, description="失败几张")
    shots: list[PreviewShotResult] = Field(default_factory=list, description="逐张结果")
    official_calls: int = Field(default=0, description="调了官方几次（留痕）")
    next_step: str = Field(default="", description="下一步做什么")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")


# --- 阶段七 · 评估与闭环迭代（落位对账，2026-09-30）--------------------------------


class LayoutRowVerdict(BaseModel):
    """**落位对账**里的一行：plan（确认过的放置表）· 台账（上次搭了什么）· 关卡现状 三方比出来的结论。

    ⚠ `verdict` 只是**抬头结论**（一行可能同时命中几条，这里按严重度取第一条），
      `why` 里带**全部**原因 —— 只报一条不等于只有一条。
    """

    uid: str = Field(description="稳定身份（`element_key|label`）—— 增量与对账都靠它")
    label: str = Field(default="", description="这一行的标签（人看的）")
    kind: str = Field(default="", description="asset（已有资产）/ whitebox（白膜）")
    element_key: str = Field(default="", description="元素关键词")
    layer: str = Field(default="", description="五层里的哪一层（阶段三 `BUILD_LAYERS`）")
    folder: str = Field(default="", description="应该待在哪个 outliner 分组")
    actor: str = Field(default="", description="搭建台账记的 Actor 引用（没有就空）")
    verdict: str = Field(
        default="ok",
        description=("抬头结论：`ok` 三方一致 / `never_built` 台账里没有这一行（没搭过，或搭完 plan 又加了行）/ "
                     "`plan_changed` 这一版改过 plan 但没重搭 / `drifted` 台账说在、关卡里找不到（引用失效）/ "
                     "`edited` 关卡现状与台账不符（人工改过或官方没照做）/ `material_missing` 材质与材质表对不上 / "
                     "`material_unreadable` 材质读不到（**不能当成没贴**）/ "
                     "`unchecked` **没比成**（缺依据：没有台账，或关卡那一维这次没查）"),
    )
    why: list[str] = Field(default_factory=list, description="逐条差异（全部原因，不截断）")
    expected: dict = Field(default_factory=dict,
                           description="plan 现算出来的指令值（loc_cm / yaw / scale / size_cm / folder）")
    ledger: dict = Field(default_factory=dict,
                         description="台账那一行记的值（含 `readback`：那几个数是不是从关卡读回来的）")
    found: dict = Field(default_factory=dict,
                        description="关卡现读到的值（读不到就是空 —— **空 ≠ 没问题**）")


class EvaluateReport(BaseModel):
    """**阶段七 · 落位对账**的报告（只读：不碰关卡、不存盘、不改 plan）。

    ⚠ 对的是**三份东西**：`views/plan_v1.json`（确认过的放置表）· `views/build_state_v1.json`
      （阶段三搭建台账：上次到底搭了什么）· **关卡现状**（官方读回来的）。
    ⚠ 关卡那一维**要 UE 在线**：连不上时它**只出 `plan ↔ 台账` 那半**，并在 `warnings` 里说清
      "哪些维这次没查" —— **不许把"没报"当成"没问题"**。
    """

    stage: str = Field(description="阶段名")
    level: str = Field(default="", description="当前 UE 开着哪张图（读不到就空）")
    plan_hash: str = Field(default="", description="当前 plan 的几何指纹（对账比的就是这一版）")
    plan_gate: PlanGate | None = Field(default=None, description="阶段二闸门状态（没确认过 = 草稿）")
    ledger_path: str = Field(default="", description="搭建台账路径")
    ledger_level: str = Field(default="", description="台账记的关卡（与 `level` 不同 = 不是同一张图）")
    ledger_plan_hash: str = Field(default="", description="台账记的那一版 plan 指纹")
    ledger_stale: bool = Field(default=False, description="台账记的 plan 与当前 plan **不是同一版**")
    level_checked: bool = Field(default=False, description="关卡那一维这次**查了没有**")
    rows_total: int = Field(default=0, description="plan 里一共几行")
    counts: dict = Field(default_factory=dict, description="每种结论各几行（含 `ok`）")
    rows: list[LayoutRowVerdict] = Field(default_factory=list,
                                        description="**不一致**的行（`all_rows=true` 时全给）")
    groups: dict = Field(default_factory=dict,
                         description="分组核对：每个分组该有几个 / 实际几个 / 谁缺谁多")
    stray_actors: list[str] = Field(default_factory=list,
                                    description="`UEMCP/` 下**台账解释不了**的活 Actor（可能有人手摆）")
    report_path: str = Field(default="", description="报告落盘位置（我们自己的 JSON，不是 UE 存盘）")
    deliverable: str = Field(default="", description="给人看的清单正文（可直接使用）")
    official_calls: int = Field(default=0, description="这次调了官方几次（留痕）")
    next_step: str = Field(default="", description="下一步做什么（闭环那五步）")
    warnings: list[str] = Field(default_factory=list, description="要提醒的坑（不藏着）")

