# 新增 Metabase 看板页面（固定流程）

本文档依据提交 `46c1902b59c560cba9441994bdcab2dfddd3638d`（`feat: Add data-hedge-profit feature and navigation entry`）归纳。按顺序改即可，无需猜路由命名规则。

---

## 总原则（必读本节）

本次改动**全部是「新增 / 追加」**，**禁止**用新页面**替换**或**删除**已有配置。

| 位置 | 正确做法 | 错误做法（禁止） |
|------|----------|------------------|
| `src/config/metabase.js` → `dashboardIds` | **追加**一行 `'<新 slug>': <新 Dashboard ID>,'`，保留对象内**所有**原有键值对 | 删掉、改名、覆盖别人的 slug（例如把 `'data-hedge-profit': 115` 改成 `'my-board': 110`） |
| `src/navigation/vertical/index.js` → MetaBase 的 `children` | **追加**一个新的 `{ id, title, auth_name, to, ... }` 对象，保留数组里**所有**原有菜单项 | 删掉、覆盖某个已有菜单块（例如把 `id: '314'` 那条整段换成新页面） |
| `src/pages/metabase/<slug>.vue` | **新建**一个 `.vue` 文件 | 覆盖别人的页面文件（除非产品明确要求改该页） |

- **菜单项 `id`**：每个新菜单项使用**新的、全项目唯一**的字符串（如 `'315'`），**不要**复用或改已有项的 `id`。
- **slug / 路由**：新页面使用**新的** `slug`（如 `my-board`），对应**新的** `to` / `auth_name`（如 `metabase-my-board`），与旧页面**并存**。

---

## 命名约定（务必统一）

| 概念 | 规则 | 示例（对冲盈亏） |
|------|------|------------------|
| 页面文件名 / 配置键 `slug` | kebab-case，与 Metabase 页面对应 | `data-hedge-profit` |
| 页面路径 | `src/pages/metabase/<slug>.vue` | `src/pages/metabase/data-hedge-profit.vue` |
| 路由 **name**（文件系统自动生成） | `metabase-<slug>`（路径段用 `-` 连接） | `metabase-data-hedge-profit` |
| `dashboardIds` 的 key | 与 `slug` **完全一致** | `'data-hedge-profit'` |
| 导航 `to` | 等于路由 name | `'metabase-data-hedge-profit'` |
| 导航 `auth_name` | 与权限系统约定一致；本仓库 Metabase 页多数与 `to` 相同 | `'metabase-data-hedge-profit'` |

> 路由由基于文件的方案生成：`pages/metabase/foo-bar.vue` → 路径大致为 `/metabase/foo-bar`，name 为 `metabase-foo-bar`。若 name 不确定，本地跑起来后在 Vue DevTools / `router.getRoutes()` 里核对一次。

## 前置条件（仓库外）

1. Metabase 中已创建 Dashboard，记下 **数字 ID**（如 `115`）。
2. 后端/权限：为新菜单配置 `auth_name`（与导航里一致），保证 `marketAPI.getMetabaseToken` 仍可用且用户组与 `metabaseConfig` 一致。

---

## 步骤 1：`src/config/metabase.js`

在 `metabaseConfig.dashboardIds` **对象中追加**一行（**不要**删除或修改任何已有 `slug` 与 ID）：

- **key** = 新页面的 `slug`（与 `*.vue` 文件名一致）
- **value** = 新 Dashboard 的 **数字 ID**

写法说明：

- **固定单一看板**：在现有若干行**后面**追加：`'<slug>': <id>,`
- **随现货/合约切换不同看板**（参考 `data-profit`、`data-exchange`）：`'<slug>': { 1: idSpot, 2: idFuture },`，页面里用 `useBaseStore().tradeTypeValue` 解析（可复制 `data-profit.vue` 的写法）。

**追加示例**（假设已有 `data-hedge-profit` 等，仅新增 `my-board`）：

```js
// dashboardIds 内 — 在保留其它键的前提下，追加最后一行（注意逗号）
// ... 保留 'data-hedge-profit': 115, 等所有旧项 ...
'my-board': 110,
```

---

## 步骤 2：新建页面 `src/pages/metabase/<slug>.vue`

**最简单**：**复制** `src/pages/metabase/data-asset.vue`（或同目录已有单看板页），**另存为新文件名** `<slug>.vue`，只改 **一处**：

- 将模板里读取的 `metabaseConfig.dashboardIds['xxx']` 改为 `metabaseConfig.dashboardIds['<你的 slug>']`（与步骤 1 的 key 一致）

逻辑说明（一般无需改）：

- 用 `metabaseConfig.queryParams` 拼 `top_nav` / `left_nav`。
- 调用 `marketAPI.getMetabaseToken`，`return_to` 形如 `/dashboard/${dashboardId}?${queryParams}`（与现有页面一致）。
- 成功后将 `res.data.href`（iframe HTML）赋给 `iframeHtml`，模板里 `v-html` 渲染。

若需要随 `tradeTypeValue` 切换看板，则复制并改写 `data-profit.vue`（`watch` + 解析 map）。

---

## 步骤 3：导航 — `src/navigation/vertical/index.js`

在 **MetaBase** 分组的 `children` **数组中追加**一项（位置按产品要求插入，**通常插在数组末尾或同类项旁边**）：

- **禁止**删除或覆盖已有 `{ ... }` 菜单项（例如保留 `id: '314'` 的「对冲盈亏」等）。
- **必须**新增**独立**的 `{ ... }` 块，并分配**新的** `id`（全项目唯一字符串）。

结构示例（字段名与 `46c1902` 一致，数值请按你的新页面替换）：

```js
// 在 MetaBase 的 children 数组里「追加」一项，例如：
{
  id: '315', // 新菜单项专用 id，勿与现有任意菜单 id 重复
  title: getI18n().global.t('外盘盈亏分析'),
  auth_name: 'metabase-my-board',
  to: 'metabase-my-board',
  showOnlyInFuture: true, // 按需：showOnlyInSpot / showOnlyInFuture / 两者都不写
},
```

- **`to`**：必须与上文「命名约定」表格中的路由 **name** 一致。
- **`auth_name`**：与后端权限配置一致（可与 `to` 相同，也可能像 `risk_data_user` 单独命名，以现有同组菜单为准）。
- **可见范围**：`showOnlyInSpot`、`showOnlyInFuture` 与现有 MetaBase 子项保持一致。

**横向菜单**：若要在顶栏也出现入口，在 `src/navigation/horizontal/index.js` 的同一 MetaBase `children` 里**同样追加**一项（结构相同；**不要**覆盖 horizontal 里已有项）。

---

## 步骤 4：自检清单

- [ ] `metabase.js` 中 **`dashboardIds` 为追加**：新 slug 已加入，**原有**所有 slug 与 ID **仍存在且未被改名/删除**。
- [ ] 新 `.vue` 为**新建文件**，文件名（不含 `.vue`）等于该次 `slug`。
- [ ] `vertical/index.js` 中 MetaBase **children 为追加**：新 `{ ... }` 已加入，**原有**菜单项**仍存在**。
- [ ] 新菜单项的 `id` 在 vertical（及 horizontal，若添加）中**全局唯一**，且**未**占用旧菜单的 `id`。
- [ ] 导航 `to` 等于 `metabase-<新 slug>`。
- [ ] i18n：`title` 若用 `$t('某中文')` 风格，确保语言包有对应条目（项目规范建议用语义化 key，此处以现有导航写法为准）。
- [ ] 权限：后端已为**新** `auth_name` 开通；无权限时菜单应被隐藏或拦截。
- [ ] 浏览器中**旧菜单**仍可打开，**新菜单**能打开对应 Metabase Dashboard。

---

## 参考文件（本仓库）

| 用途 | 文件 |
|------|------|
| Dashboard ID 与各页 slug 映射 | `src/config/metabase.js` |
| 单一看板页面模板 | `src/pages/metabase/data-asset.vue`、`data-hedge-profit.vue` |
| 随 `tradeTypeValue` 切换看板 | `src/pages/metabase/data-profit.vue`、`data-exchange.vue` |
| 侧栏菜单 | `src/navigation/vertical/index.js` |
| 顶栏菜单 | `src/navigation/horizontal/index.js` |

---

## 参考提交涉及文件一览（`46c1902`）

1. `src/config/metabase.js` — **追加** `'data-hedge-profit': 115`（不删旧键）
2. `src/pages/metabase/data-hedge-profit.vue` — **新建**
3. `src/navigation/vertical/index.js` — MetaBase **children 追加** 1 项

新需求只需把 `data-hedge-profit` / `115` / 菜单文案与 `id` 换成**本次**新页面的值，并遵守上文「**全部为追加、不替换**」原则。
