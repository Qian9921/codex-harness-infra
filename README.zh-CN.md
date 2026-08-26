# Codex Harness Infra

Codex Harness Infra 是一层小而可移植的 Codex 策略与交付层。它定义稳定的工程质量标准，只在需要时加载详细指导，并把经过授权的仓库修改连接到带独立 Review 的 GitHub Pull Request。它不是另一套 Agent Runtime。

## 仓库内容

```text
AGENTS.md                         仓库级常驻规则
WORKFLOW.md                       工作与交付约定
.agents/skills/                   按需加载的交付指导
package/agents/                   可移植的逻辑角色模板
scripts/                          小型本机和 GitHub 辅助工具
tests/                            聚焦的行为与安装测试
.github/                          仓库 Review 与 CI 配置
docs/                             架构和操作参考
```

Codex 原生提供 Agent Loop、权限、Skill 和 Subagent 能力。本仓库只提供项目特有的工作规范，以及 GitHub 流程所需的小型适配层。

## 角色

可移植角色为 `primary`、`executor` 和 `reviewer`：

- `primary` 负责需求、范围、决策和最终沟通。
- 外部 Grok bridge 以 low effort 负责边界清晰的实现与相关验证；native `executor` 只有在收到可验证的 Grok 额度耗尽 receipt 后才能作为 fallback 使用。
- `reviewer` 使用新上下文，以只读方式审查当前变更。

本机安装会把 primary、fallback executor 和 reviewer 映射到该机器可用的 native 模型和工具。Native 模型 slug、账号映射、凭据、开场指令和绝对路径仍属于本机配置；外部 Grok 执行身份是 portable 产品 contract。

## 交付

讨论任务保持只读。经过授权的仓库修改按以下流程进行：

```text
理解 → 实现 → 验证 → commit → push → Pull Request
    → 独立 Review → 反馈/修改 → approval → merge
```

作者和 Reviewer 在同一台机器上使用不同的 GitHub 身份。这是审计与工作流边界，不宣称进程或凭据隔离。GitHub Pull Request、当前 head、检查、评论和 Review 是交付的持久记录。

每次新提交都会改变审查对象。只有当前 head 拥有有效 approval、必要检查和仓库规则允许时才能合并。

## 安装边界

安装器只修改明确拥有的文件和标记区块，保留无关的个人配置、工具、凭据和用户规则。目标文件已有用户内容但没有 ownership marker 时不得覆盖。它只安装一个 UserPromptSubmit hook，用于每个新任务实际检查并使用 CodeGraph、Semble 和 RTK，并注入一份有界的当前运行时状态（来自 `install.json` 与现场 daemon 探针，而不是任务记忆）。不安装 Stop hook、后台服务、daemon 或项目跟踪的 index。卸载时只删除本项目拥有的内容。

## 本机启用

将 `package/local.example.toml` 复制到仓库外的本机路径，填写模型、开场指令、GitHub、Python runtime 和三个必需工具字段，然后运行 `python scripts/install.py install --local-config <local-file>`。在 Codex 的 hook browser 中一次性 review 并 trust V23 UserPromptSubmit hook 后，用 `codex --profile v23-primary` 启动 V23 主 profile；它选择本机主模型，并把原生 `/review` 映射到本机 review 模型。V23 executor 与 reviewer 仍作为独立 custom agent 注册。

## 从这里开始

- [架构](docs/architecture.md)
- [工作流](WORKFLOW.md)
- [GitHub 交付](docs/github-flow.md)
- [工具选择](docs/tool-routing.md)
- [工程规范](docs/engineering-standards.md)
- [安装边界](docs/installation-boundary.md)

## 开发

使用仓库支持的 Python 环境和聚焦测试。每个变更保持内聚，并控制在一次独立 Review 可以理解的范围内。不可见的架构决策记录到 `docs/decisions/`。

## 许可证

见 [LICENSE](LICENSE)。
