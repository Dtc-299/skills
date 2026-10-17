# 保存协议 v1.1 · 单聊默认，选定多聊可合并

## 包结构

每个明确选择的聊天集合有独立入口：`.dtc/handoffs/<集合标识>/CURRENT.json`，指向其快照并保存 manifest SHA-256。单聊标识为聊天 ID；多聊标识由排序去重后的 ID 集合确定。同一集合换顺序不会重复建档；不同集合不覆盖彼此。没有 CURRENT 的草稿不算完成，已发布快照不改写、不自动删除。归档不覆盖当前指令或重新授权外部操作。

manifest.scope 必须保存 `kind`（thread/selection）、`thread_ids`、`key`、`label`。每个 manifest.sources 条目都要记录其真实 thread_id，且必须属于所选集合。默认仅当前 ID；指定多聊时先列出完整范围，不能按目录补进其他聊天。全部选中聊天都应尝试从历史起点读到截止时刻；读取不到的早期轮次明确记缺口。

合并时保留每个聊天的来源索引，分清同一事实、重复继承内容和矛盾。不要把同名局部任务或 R001 强行视为同一项，跨来源 ID 必要时使用聊天 ID 前缀。新集合第一次建立时可复用已验证的单聊证据，但必须复制所需资料到包内并重新核对；不能导入未选聊天的事实。分叉聊天中本来包含的继承历史可保留并标注，不自动额外抓取未选中的父聊天。

快照内容：

- `START_HERE.md`：任务目标、精确断点、必须遵守的约束 ID、当前任务 ID、第一步具体动作、阅读路径及关键缺口。通常 1000–2500 中文字；不能为限字删关键项。
- `MEMORY.md`：按主题组织长期事实、方案与理由、文件索引、接口约定、失败原因、待核实矛盾。可分 `topics/*.md`，在入口列出按任务必读的主题。
- `ledger.json`：可逐项核对的事实与工作台账，见下。
- `review.json`：来源阅读进度、逐项反查结果、已知缺口及语义审核声明。
- `sources/`：采集的用户和助手可见消息 Markdown；保留来源绝对路径、原记录行号、时间和 SHA-256。源码、图片、数据库、大型工具输出仅索引，不整份塞入记忆。
- `manifest.json`：脚本产生的项目标识、基线 Git 状态、采集截止时间、源文件清单、来源范围和哈希。不要把其局部范围改成“全部账户历史”。

## 台账数据约定

`ledger.json` 是一个对象，包含以下键。无该类别用空数组，不能用假数据填充。

```json
{
  "schema_version": 1,
  "project_root": "项目绝对路径",
  "items": [
    {
      "id": "R001",
      "kind": "requirement",
      "text": "用户已确认的具体要求",
      "state": "active",
      "basis": "user_confirmed",
      "refs": [{"file": "sources/某文件.md", "anchor": "L27"}]
    }
  ],
  "tasks": [
    {
      "id": "T001",
      "text": "具体未完成动作及验收条件",
      "status": "in_progress",
      "refs": [{"file": "sources/某文件.md", "anchor": "L30"}],
      "next_action": "打开哪个文件、检查什么、预期看到什么"
    }
  ],
  "next_task_id": "T001",
  "read_next": ["MEMORY.md"],
  "recall_checks": [
    {"question": "恢复后必须答对的问题", "answer": "准确答案", "item_ids": ["R001"]}
  ]
}
```

这段只是格式示例，不可直接作为交付内容。真实 refs 必须能在包内定位。脚本生成的 source 索引可用 `anchor: "L原始JSONL行号"`；其他 Markdown 用实际存在的标题/唯一摘录作 anchor。项目文件证据：先写入 `evidence/*.md`，记录实际文件路径、哈希、读取时间与关键摘录；台账引用这份证据。不要把项目目录外的任意路径当包内 ref。

items 的 kind：`requirement / constraint / preference / fact / value / decision / interface / failure / evidence / unknown`。

items 的 basis：`user_confirmed / observed / inferred / proposed / unknown`。助手旧陈述无外部佐证时，记录为 inferred 或未核实，不升级为事实。

items 的 state：`active / superseded / unresolved`。替代时保留旧 ID、原值与 refs，添加 `superseded_by` 指向新 ID。矛盾未能用明确用户修正或实际证据解决时保留双方，标 unresolved。新快照的 items/tasks ID 不能复用表示另一个内容；上一版未完成工作不能悄悄消失，取消也要保留 cancelled 状态和原因。

kind=value 必须额外包含 `value`（字符串，保留小数位及原记法）、`unit`、`as_of`、`value_type`（original/calculated/estimated）；无单位/日期时写明确的“不适用/来源未注明”。计算值附公式和输入 ID。不要把 0.010 改成 0.01、12ms 改成 12s；不要把预算上限变成实际花费。ID、分支名、路径、版本、报错原文也需准确保存。

tasks 的 status：`pending / in_progress / blocked / done / cancelled`。done 必须有 `verification` 非空字符串说明可定位的验收证据；只说“已完成”不是验证。blocked 必须记录实际 blocker；无未完成任务时 next_task_id 为 null，不虚构继续工作。

recall_checks 为 5–10 个有来源的问题（资料少可以更少），覆盖当前目标、不可违反的约束、关键数据与单位、为什么采用当前方案、确切断点、下一步和已知陷阱。每个 item_ids 必须能在台账定位。它们检测恢复理解，不等同于质量保证。

## 阅读和覆盖报告

`review.json`：

```json
{
  "schema_version": 1,
  "read_files": ["sources/某文件.md"],
  "checked_item_ids": ["R001"],
  "coverage": "partial",
  "gaps": ["云端历史无法访问；图片尚未检查"],
  "source_inventory_notes": "枚举了哪些聊天、分页到哪里、哪些范围尚未确认",
  "semantic_review": "逐项对照已读取来源后的实际审核结论"
}
```

所有 manifest.sources 的文件必须完整读过并列入 read_files；否则该草稿暂不发布。读不完可换批次、分段落盘继续，不能把未读改成已读。临时关键事实应随阅读及时写入台账，不等到窗口快满才总结。

coverage 仅为：`partial / accessible_selected_sources`。后者只表示已读取所选聊天的可访问来源，不宣称历史、附件或云端完整。脚本发现选中聊天未找到、坏行、未知格式、附件或来源截断时必须 partial；额外 app 缺口也加 gaps。对正在增长的会话说明截止时间；冻结字节数之后的消息不属于本版本。旧交接若只读了最近30条，必须保留这个限制，不能因用户同意合并就升级为全历史。

反向核对：逐条检查所有 active constraint/value/decision/interface/failure 和未完成任务，且对用户最新需求逐句检查是否有对应台账或明确不适用。将每个已核对 items/tasks ID 写入 checked_item_ids。保留上一版台账并从原来源更新，不能仅二次压缩上一版 MEMORY。

## 写完后

运行脚本 check，再 publish。check 验证必需文件、引用、数值字段、已读来源、审核 ID、任务状态和上一版 ID 留存；publish 再做校验，核对父指针未变，封存所有文件哈希，最后原子替换 CURRENT.json。校验失败保持草稿并修复。

增量版本仍保留完整有效台账；归档可重新采集或带来源复制旧证据，不能留下失效的相对引用。若源记录消失，旧快照可回查；不能凭记忆声称验证通过。脚本不会代替模型检查语义一致性。
