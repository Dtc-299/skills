# 设计依据与边界

2026-10-17 阅读以下一手资料，仅参考机制；实现为原创标准库脚本，未复制项目代码或安装后台服务。

- [OpenAI：Build skills](https://developers.openai.com/plugins/build/skills)：触发条件明确；主 SKILL.md 精简；细节和确定性处理下沉到 references/scripts。
- [OpenAI：Iterating Development Workflows with Codex](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)：把影响后续任务的决定、理由、证据和未知项存入项目文件。
- [Claude-Mem](https://github.com/thedotmack/claude-mem)：分层检索，先索引再取相关原始观察，保留来源 ID。DTC 使用本地 Markdown 和结构化台账，无远程记忆服务依赖。
- [GSD pause-work](https://github.com/gsd-build/get-shit-done/blob/main/get-shit-done/workflows/pause-work.md) 与 [resume-project](https://github.com/gsd-build/get-shit-done/blob/main/get-shit-done/workflows/resume-project.md)：结构化交接、精确下一步、检查未提交文件和中断工作。DTC 不继承其自动提交、自动删除交接或委派行为。

为什么不用单一短摘要：短摘要易丢掉准确值、边界条件、失败原因与证据；反复压缩摘要会累积误差。DTC 将入口、稳定台账和来源归档分开，新窗口只加载当前任务需要的层，必要时回查原文。

哈希只检测封存后变化，不能证明总结正确，也不是防恶意签名。结构检查不能证明语义无遗漏；自测问题不能证明模型能力保持不变。真实保障是明确覆盖、逐项反查、原文可恢复、变更可检测，并且不宣称超出实际证据的完成状态。
