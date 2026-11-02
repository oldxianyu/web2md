# web2md

把网页文章抓下来转成本地 Markdown —— 正文提取、图片本地化、frontmatter，一条命令搞定。

给 AI Agent（Claude Code / DeepSeek Harness / Cursor 等）用的 skill，也可以纯手工当命令行工具使。

## 它解决什么

网页文章存成 Markdown，直接用 `curl` + 正则会有三个麻烦：

1. **正文里混着导航、侧栏、广告、相关阅读、评论区** —— 存下来没法看
2. **图片是外链** —— 原站改版或挂掉，图就没了
3. **懒加载占位图** —— `<img src>` 里是个 1px 透明图，真图在 `data-src` 里

这个脚本把这三件事一起处理掉。

## 效果

输入：

```bash
python html2md.py https://ai-bot.cn/personal-agent-china-fails/
```

输出：

```
海外爆火的 Personal Agent 为何在中国走不通？.md
images/
  fig-01-未命名42-73.png
  fig-02-未命名42-74.png
  ...
```

生成的 Markdown：

```markdown
---
title: 海外爆火的 Personal Agent 为何在中国走不通？
source: https://ai-bot.cn/personal-agent-china-fails/
site: AI工具集
published: "2026-10-21T11:32:14+08:00"
converted: 2026-11-02 11:39
---

# 海外爆火的 Personal Agent 为何在中国走不通？

最近，Personal Agent 在大洋彼岸掀起一股热浪。

8月11日，马斯克的 xAI 发布 [Grok Bot](https://ai-bot.cn/grok-bot/)，上线一个月后，周用户达到 41.8 万。

![fig-01](images/fig-01-未命名42-73.png)

## 01. Personal Agent 是什么
...
```

导航、广告、评论区全部不见，图片用相对路径，整个目录能直接搬走。

## 安装

只需要 Python 3.8+ 和 lxml：

```bash
pip install lxml
```

把 `html2md.py` 拷到任何地方就能用，无其他依赖。

### 作为 skill 用

`SKILL.md` 是给 AI Agent 读的说明。放进对应的 skill 目录即可：

| 工具 | 放置位置 |
|---|---|
| Claude Code | `~/.claude/skills/web2md/` |
| DeepSeek Harness | 工作区下的 `web2md/` |
| 其他 | 按该工具的 skill 约定 |

之后对 Agent 说「把这个链接转成 md」就会自动走这套流程。

## 用法

```bash
python html2md.py <URL> [选项]
```

| 选项 | 说明 |
|---|---|
| `-o, --out` | 输出 .md 路径；不填用页面标题当文件名 |
| `--out-dir` | 不指定 `-o` 时的输出目录，默认当前目录 |
| `--assets` | 图片目录名，默认 `images` |
| `--selector` | 手动指定正文容器，如 `div.panel-body`；自动识别失败时用 |
| `--no-images` | 不下载图片，只保留原站外链 |

### 指定输出目录

**方式一：环境变量**（推荐，设一次就够）

```bash
# Windows PowerShell
setx WEB2MD_OUT_DIR "D:\Notes\Inbox"

# macOS / Linux（写进 ~/.bashrc 或 ~/.zshrc）
export WEB2MD_OUT_DIR="$HOME/Notes/Inbox"
```

之后 `python html2md.py <URL>` 就自动落到那个目录，图片进 `<目录>/images/`。

**方式二：命令行**

```bash
python html2md.py <URL> --out-dir "D:\Notes\Inbox"
python html2md.py <URL> -o "D:\Notes\Inbox\自定义标题.md"
```

> 举个例子：如果你的 Obsidian 库放在 `D:\Obsidian\K`，就设 `WEB2MD_OUT_DIR=D:\Obsidian\K`，之后转的文章和配图都直接进库，不用每次敲路径。

## 正文是怎么定位的

两个机制配合：

**1. `pick_content()` 打分**

遍历所有 `div / article / section / main`，对每个容器算：

```
分数 = <p> 文字总量 × (1 − 链接密度)
```

链接密度 = 链接文字量 / 总文字量。导航栏链接多文字少，分数低；正文链接少文字多，分数高。同分时偏向更深的节点（更聚焦）。

**2. `strip_boilerplate()` 清洗**

在选中的容器内部，按 class / id 关键词摘掉噪音块：

```
breadcrumb / comment / related / share / nav / sidebar /
copyright / pagination / newsletter / tag ...
```

安全阈值：只摘「不超过正文 35% 且小于 800 字符」的块，防止把整篇文章误删。

两个机制都失灵时，`--selector` 直接指定，最省事。

## 已验证的站点

| 站点 | 类型 | 结果 |
|---|---|---|
| ai-bot.cn | WordPress / 中文 | 正文 + 13 张图 |
| ruanyifeng.com | Blogger / 中文 | 自动识别，面包屑与评论区已过滤，29 张图 |
| simonwillison.net | 英文博客 | 自动识别，代码块 / 行内代码 / 粗体均正确 |

欢迎提 issue 报告失败站点，附 URL 即可。

## 已知限制

- **需要登录的内容**抓不到 —— 用浏览器手动另存 HTML，再喂给脚本
- **纯 JS 渲染的 SPA** 可能抓不到正文 —— 先跑 `--no-images` 看看有没有内容
- **403 / 429** —— 部分站点拒脚本 UA（如维基百科）。用浏览器另存 HTML 再转
- **中文文件名图片** —— URL 里未编码的中文会让 `urllib` 报 `'ascii' codec can't encode`。脚本里的 `encode_url()` 已处理

## 为什么不用现成工具

[readability](https://github.com/buriy/python-readability) / [trafilatura](https://github.com/adbar/trafilatura) 都很好，但：

- 它们是库，不是开箱即用的命令
- 图片本地化要自己接
- 这个脚本是**单文件、零配置、无框架**，方便直接丢给 AI Agent 当 skill 用

如果你要处理成千上万篇文章，用 trafilatura 更合适；如果只是偶尔存几篇，这个够用且没依赖负担。

## License

MIT
