# UEMCP · 参考图驱动的场景搭建（L0 · 阶段一：资产确认）

自建 MCP（Model Context Protocol，模型上下文协议）server。它是**编排层**，自己不碰 UE：

```
宿主(agent / DSH) → 本 server（stdio）→ 官方 Unreal MCP（HTTP 127.0.0.1:8000）→ UE 编辑器
```

## 当前阶段：阶段一 · 资产确认

读参考图 → 提取画面元素 → **让用户确认** → 找资产（**名字对得上才算找到**）
→ 缺的如实报告并请用户给【路径 + 名字】→ 改名 → 逐个验证存在性 → 交付资产清单。

流程原文见 `docs/阶段一-资产确认.md`。

## 工具（5 个）

| 工具 | 干什么 | 落盘 |
|---|---|---|
| `official_status` | 链路自检：能否连上官方、协商出的协议版本、工具集数量 | — |
| `confirm_elements` | 记录**用户已确认**的元素清单 | `catalog/elements.json` |
| `plan_assets` | 按【资产名】+【文件夹名】两路找资产，报找到 / 没找到 | — |
| `rename_assets` | 改名（默认只预览，`confirm=true` 才真改） | — |
| `confirm_assets` | 逐个 `exists()` 验证 → 生成给人看的清单 | `catalog/asset_list.json` |

## 怎么跑

由 DSH 以 stdio 启动（配置见 DSH profile 的 `cordis.patch.yml` 里 `mcp-mine` 那节），
**不需要手动起**。手动自测：

```
& F:\MCP_Server\.venv\Scripts\python.exe tests\check_tools.py
```

（不要用 `uv run` —— 实测在沙箱里会因缓存目录权限失败。）

## 硬规则

- **禁止猜"疑似资产"**：名字对得上就是找到，对不上就是没找到。
  （"命名是否规范"的判据做过两版，两版都在实测里误报；根因是"没有词典时，
  随机哈希和英文单词拼接在字符层面无法区分" —— 猜不准就不猜。）
- 用户确认前：不落盘、不替换、不删资产。
- 工具返回的数字必须能**无损穿过 JSON**：负零 `-0.0`、`NaN`、`Infinity` 会被宿主
  判定 `value is not lossless JSON` 并**整条拒收**（不是丢一个字段，是工具直接不可用）。

## 已知坑（都踩过，别再踩）

- **官方连接必须由一个专用 task 建/关。** anyio 的取消域只能由进入它的那个 task 退出；
  在 A 的 task 里建、在 B 的 task 里关，会抛
  `RuntimeError: Attempted to exit a cancel scope ...`，并穿透到 `mcp.run()`
  **直接炸掉 server 进程**（宿主侧只看到 `connection lost; reconnecting`）。
  机制、修法与原始日志证据都写在 `src/mcp_server/main.py` 顶部。
- **官方 `find_assets` 的名字过滤只匹配资产名，不匹配文件夹名**（实测）。所以哈希名资产
  `MI_sjfnch0a` 只能靠文件夹名 `Fine_American_Road_sjfnch0a` 找到。
- **`find_assets` 的 `folder_path` 传 `/Game` 不覆盖插件内容**，传空串才搜整个工程。
- **`name=""` 才是"全都要"**（枚举）；传别的值就是子串过滤。
- **`list_folders` 会列出空目录**：`/Game/Megaplant_Library/` 存在，但里面 0 个资产。
- **`plan_assets` 的报告只显示"命中的"资产，不显示枚举到的全量清单**，
  别拿命中数去推断枚举数（我干过，结论是错的）。
