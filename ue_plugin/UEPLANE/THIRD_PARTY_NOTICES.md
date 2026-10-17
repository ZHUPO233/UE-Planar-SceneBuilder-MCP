# UEPLANE · Third-Party Notices

本插件随包包含下列第三方组件。各自的许可证全文附在下方。

## 1. three.js（r1xx，minified）

- 文件：`Content/Python/ueplane/planning/web/three.min.js`
- 用途：生成阶段②那一页可旋转 / 可缩放 / 可点选的平面验收页（内联进 `preview3d.html`）
- 许可证：**MIT**
- 版权声明（原文件 `@license` 头逐字）：

```
Copyright 2010-2023 Three.js Authors
SPDX-License-Identifier: MIT
```

**MIT License 全文：**

```
Copyright 2010-2023 Three.js Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## 2. 不随包分发的第三方资源

本插件**不包含**任何第三方美术资产（网格 / 材质 / 贴图 / 音频）。
插件运行时会读取**你自己工程**里的资产（通过官方 ToolsetRegistry 的 `find_assets` /
`get_bounds` 等接口），这些资产的许可由你与它们的提供方约定 —— 与本插件无关。

## 3. 不包含的引擎代码

本插件 `Modules: []`，**不含任何 C++ 模块或编译产物**；全部逻辑是 `Content/Python/` 下的
Python 源码，运行在 Epic 官方 `PythonScriptPlugin` 之上，受 Unreal Engine EULA 约束。
