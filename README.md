# mdflow

**工作流就是一个 Markdown 文件。** 不用搭平台、不用学节点编辑器：写几段 prompt，用 `---` 分成步骤，一条命令跑完。

## 为什么不用 dify？

|  | dify | mdflow |
|---|---|---|
| 工作流长什么样 | 平台里的节点图 + 数据库 | 一个 `.md` 文件 |
| 运行环境 | Docker / 云服务 | 1 个 Python 文件，零依赖 |
| 分享工作流 | 导出 DSL / 邀请进 workspace | 直接把 md 贴到聊天里 |
| 版本管理 | 平台内版本 | git 本来就是 |
| 适合 | 团队生产级 Agent 应用 | 个人快速拼 prompt 流水线 |

dify 是好平台；mdflow 是给"我就想连跑 3 个 prompt"的轻量答案。

## 安装

零依赖，Python 3.10+ 自带一切：

```bash
git clone https://github.com/ljiang9/mdflow.git
cd mdflow
```

## 快速开始

```bash
export OPENAI_API_KEY=你的key   # 或 MDFLOW_API_KEY；也支持 OPENAI_BASE_URL 换兼容接口

# 先免费预览：只渲染 prompt，不调 API
python -m mdflow run examples/blog-writer.md \
  --set topic="AI 创业" --set audience=新手 --dry-run

# 真跑
python -m mdflow run examples/blog-writer.md \
  --set topic="AI 创业" --set audience=新手

# 看这个工作流需要哪些变量
python -m mdflow run examples/blog-writer.md --list-vars
```

## 工作流格式

```markdown
---
model: gpt-4o-mini          # 模型
base_url: https://api.openai.com/v1   # 可选，默认读 $OPENAI_BASE_URL
temperature: 0.7             # 可选
system: You are helpful.    # 可选，系统提示词
output: ./out               # 可选，每步输出存为 out/<步骤名>.md
---

# 大纲
根据主题 {{topic}} 列出 5 个要点...

---

# 初稿
根据大纲：
{{prev}}
写成一篇 800 字中文博客...
```

- 开头 `--- ... ---` 是配置（只支持 `key: value` 的小解析器：字符串/数字/布尔）。
- 正文按整行 `---` 切成步骤；每步第一个 `# 标题` 是步骤名。
- 每步按顺序调 `{base_url}/chat/completions`，非流式，超时 120s。

## 变量

| 写法 | 含义 |
|---|---|
| `{{name}}` | `--set name=value` 传入（可重复） |
| `{{prev}}` | 上一步的原始输出 |
| `{{step_大纲}}` | 任意之前步骤的输出（`step_` + 步骤 slug） |
| `{{env:HOME}}` | 环境变量 |

未知变量不会静默吞掉：原样保留并在 stderr 警告。

## License

MIT © 2026 ljiang9
