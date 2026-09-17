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

- `primary` 只做决策与只读验收，永不改文件，也不做机械执行。执行器不可用时修复路由，禁止把实现交回 primary。
- 由本地 executor routing 选中的执行器负责有界实现与相关验证。`native_only` 是完整的 Codex 路径。后端身份、receipt 与可选 supervision 只从选中 backend 的 skill 加载。
- `reviewer` 使用新上下文，以只读方式审查当前变更。Pi-only 路由可用全新的只读 Pi session 完成审查，不要求 native reviewer。

本机安装会把 primary、executor 和 reviewer 映射到该机器可用的模型和工具。Native 模型 slug、账号映射、凭据、开场指令和绝对路径仍属于本机配置。共享策略使用逻辑 executor ID 与 backend，不写入当前 native 版本号。

## Executor routing

复制 `package/local.example.toml` 并设置 `[routing].selection`：

- `native_only`：只需 Codex，不要求 Pi 可执行文件或配额 receipt。
- `paid_preferred`：优先匹配的 paid/included executor；仅在 `[routing.fallback]` 允许的原因（如 `quota_exhausted`）下 fallback。
- `paid_strict`：同样优先；付费候选不合适时阻断，除非有明确允许的 fallback 原因。仅配置 Pi executor 且 `[routing.fallback].permit` 为空时即为 Pi-only：候选不可用会被阻断，不会替换为 native executor。
- 省略 `[routing]`：兼容既有 Grok 优先、实际模型 receipt、仅配额 native fallback。

支持的适配器是 native Codex 与通用 Pi adapter（`backend = grok` 或 `backend = pi`）。cost_preference 是用户声明，不是已核验余额。未知配额保持 unknown。模型更新只改本机映射，不因新 native slug 自动迁移。禁止回退到 GrokCLI。

```text
python scripts/executor_routing.py select --local-config <local-file> --capability implementation --tool workspace-write
```

## 交付

讨论任务保持只读。发布遵循明确的 `[delivery]`（默认 `local_only`，或 `pull_request` / `merge_if_ready` 加授权仓库）。当前用户对指定仓库的明确 PR 请求可用仅含 `[delivery]` 的临时 `--local-config` 覆盖 standing `local_only`，全程复用同一文件并在交付后删除，不改持久文件。安装或配置凭据不是发布授权。

作者和 Reviewer 在同一台机器上使用不同的 GitHub 身份。这是审计与工作流边界，不宣称进程或凭据隔离。GitHub Pull Request、当前 head、检查、评论和 Review 是交付的持久记录。

每次新提交都会改变审查对象。只有当前 head 拥有有效 approval、必要检查和仓库规则允许时才能合并。

## 安装边界

安装器只修改明确拥有的文件和标记区块，保留无关的个人配置、工具、凭据和用户规则。目标文件已有用户内容但没有 ownership marker 时不得覆盖。它只安装一个 UserPromptSubmit hook，用于注入已安装说明和本地完整性检查（`install.json` 与指令/配置）。CodeGraph、Semble、RTK 与 daemon 探针需显式 Doctor 或任务相关。不安装 Stop hook、后台服务、daemon 或项目跟踪的 index。卸载时只删除本项目拥有的内容。

## 本机启用

将 `package/local.example.toml` 复制到仓库外的本机路径。仅 Codex 安装只需 `[models]` 与 `native_only`；开场、Pi/Grok、GitHub 和可选工具可留空。需要外送时再设 `[delivery]`。运行 `python scripts/install.py install --local-config <local-file>`。模型名只写在本机配置中。用 `codex --profile v23-primary` 启动，并在 hook browser 中 trust V23 UserPromptSubmit hook。

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
