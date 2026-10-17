# 来源适配

## 项目范围

当前工作目录是存储位置，不是聊天选择器。默认只处理当前聊天，用户明确点名多个聊天才处理其集合。不要因同项目、同 cwd、目录名相似或 runtime_workspace_roots 相同而合并其他聊天。目录改变也不意味着当前聊天的早期问答要被排除。

App 工具可用时：list_threads 解析用户点名标题的真实 ID，list_archived_threads 补充被选中的归档聊天。重名时结合项目/时间或提问确认。read_thread 仅针对已选 ID，用游标向前读到历史起点；记录返回的是全文、摘要还是截断输出、页数与边界。只有用户明确要求项目所有聊天时才用 list_projects 枚举项目成员。不同 host/cloud 不一定有本地 JSONL。

本地采集：只扫描 `$CODEX_HOME/sessions` 和 archived_sessions，未指定时为 `~/.codex`。按 session_meta.id/session_id 精确匹配所选 ID；其他聊天只检查元数据头，不提取其消息。匹配聊天内部切换 cwd 的前后内容都保留。找不到当前 ID 时停止猜测；找不到某个选中聊天的本地日志时记录缺口，并尝试 app 指定 ID 历史。绝不以允许访问目录、同项目或 cwd 代替聊天身份，也不读取 auth.json/浏览器数据。

## 支持与限制

解析本机观察到的 Codex JSONL：session_meta；turn_context；response_item/message 的 user/assistant 可见文本；event_msg/user_message、agent_message 作为缺少对应 response 消息时的回退；compacted 事件只登记存在压缩。排除 analysis、reasoning、系统/开发者指令、工具调用与工具原始输出。环境上下文、注入的 AGENTS/工具配置不是用户任务。

工具输出可能含关键证据。按台账所需，用原始行定位或当前实际命令结果补充到 evidence/；不要声称对话导出涵盖工具证据。脚本不会自动读取图片、PDF、音视频、二进制、云端附件或理解输出中的数值。附件/不支持格式/坏行均登记缺口，原始日志只读保留。

导出的文本会遮盖常见密钥、令牌、PEM 和 credential 赋值，并记录脱敏计数；这不是通用敏感数据检测器。模型在封存前检查摘要与证据，密码/密钥只记“用途与安全来源位置”，不记值。不要打开 .env 或认证存储来完善记忆。资料只写本地 .dtc，不上传第三方服务。

## 无本地记录时

优先 app 指定 ID 分页读取；只有当前可见上下文时，保存为 `sources/current-visible.md`，manifest.sources 添加 file、origin、真实 thread_id，注明不是完整历史，coverage=partial。多聊集合分别导出，不把缺失聊天假称已读。无来源可保留草稿，但不能发布虚构交接。

未知 JSONL 格式不猜。查看匹配项目的少量记录结构（先只看类型和字段名），再调整解析或使用已知的 app 工具。不得通过解析不相关项目的内容推断本项目事实。
