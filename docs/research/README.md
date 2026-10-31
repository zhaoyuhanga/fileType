# 调研报告（仅影视设计依据）

本目录只剩**一份仍在生效**的调研：

| 文件 | 用途 | 生效范围 |
|---|---|---|
| `MoonTV-LunaTV-技术调研报告.md` | 「墨软影视」的**代理 / 测速 / 下载 / 缓存**设计依据，含"宣传口径 vs. 代码事实"反证表 | **有效**，对应代码 `services/media_proxy/`、`core/video/sources/`、`core/video/segment_downloader.py` |

## 已删除的三类内容（别再找）

v2.1.0 清理文档时删掉了 4 份 Kazumi 报告（合计约 375 KB）与一份 v1.0.0 重构评审稿：

- `kazumi_research_report.md` / `kazumi_extras_report_v2.md` /
  `kazumi_issue_mining_report.md` / `kazumi_danmaku_timetable_report.md` ——
  它们是**已移除的第七板块「墨软动漫」**的取证材料。那个板块为什么移除，
  `CHANGELOG.md` 的 v2.0.0 小节已经用一句话说清了
  （KazumiRules 全量 16 条规则：搜索 11/16、详情 11/16、**取到播放地址 0/16**，
  全部依赖 `useWebview`；本项目不内置浏览器内核，因此播不出来）。
  **留着一份"如何实现一个已经删掉的功能"的 375 KB 材料，只会误导后来者。**
- `docs/REFACTOR_PLAN.md` —— v1.0.0 的重构评审稿，其中的目录结构与依赖规则
  已全部落地，并**收敛进** `docs/ARCHITECTURE.md`（单一来源）。

## 怎么判断一份调研还该不该留

一条标准：**它描述的东西现在还有代码吗？**

- 有 → 留在本目录，并在 `docs/BOARDS/<板块>.md` 或方案文档里注明"哪一段对应哪个模块"；
- 没有 → 结论写进 `CHANGELOG.md`（为什么做、为什么删），原始材料删除。
  历史在 git 里，不需要用 375 KB 的 Markdown 存着。
