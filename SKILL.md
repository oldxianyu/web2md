---
name: web2md
description: 把一个网页文章抓下来转成本地 Markdown 文件（正文提取 + 图片本地化 + frontmatter）。用户给一个 URL 说「存成 md」「转成本地笔记」「存档这篇文章」「离线保存」时使用。
---

# web2md —— 网页文章转本地 Markdown

把在线文章变成一份干净的本地 `.md` + `images/` 目录，正文只留文章本身（导航、广告、侧栏、评论区全部丢掉），图片下载到本地相对路径，离线也能看。

## 什么时候用

- 「把这个链接转成 md / 存成笔记 / 离线保存一下」
- 「这篇文章我要存档，图也要」
- 需要把网页正文喂给后续处理（总结、改写、做 PPT、做知识库）

不适用：需要登录才能看的内容（脚本抓不到，得用浏览器 lane）、纯 JS 渲染的 SPA（先确认 `--no-images` 抓下来有没有正文）。

## 怎么做

脚本 `html2md.py` 与本文件同级。需要 Python 3.8+ 和 lxml。

```bash
python html2md.py "<URL>"
```

不传 `-o` 时，md 落到 `--out-dir`（默认当前目录），图片进 `<输出目录>/images/`。

参数：

| 参数 | 作用 |
|---|---|
| `-o, --out` | 输出 .md 路径；不填用页面标题当文件名 |
| `--out-dir` | 输出目录，默认当前目录，也可用环境变量 `WEB2MD_OUT_DIR` 设定 |
| `--assets` | 图片目录名，默认 `images` |
| `--selector` | 手动指定正文容器，如 `div.panel-body`；自动识别失败时用 |
| `--no-images` | 不下载图片，只保留原站外链 |

### 固定输出目录

如果用户有固定的笔记库，用环境变量设一次，之后不用每次写路径：

```powershell
# Windows
setx WEB2MD_OUT_DIR "D:\Notes\Inbox"
```

```bash
# macOS / Linux
export WEB2MD_OUT_DIR="$HOME/Notes/Inbox"
```

例如 Obsidian 库在 `D:\Obsidian\K`，就设 `WEB2MD_OUT_DIR=D:\Obsidian\K`，转出来的文章和配图直接进库。

## 工作流程

1. **先直接跑**，不带 `--selector`，看自动识别对不对。
2. **看输出统计**：正文少于 500 字符、或图片全部失败，说明正文容器猜错了。
3. **猜错就找选择器**：抓原始 HTML，定位文章正文的 class：
   ```bash
   curl -sL "<URL>" | grep -o 'class="[^"]*\(content\|article\|post\)[^"]*"'
   ```
   把最内层那个传 `--selector`。
4. **验收**：读生成的 md，检查
   - 标题层级正常（`##` 章、`###` 小节），且标题里没有残留的 `**` 粗体标记
   - 图片链接指向本地 `images/xxx.png` 且文件真实存在
   - 没有把「相关文章」「最新收录」这类列表混进正文
   - frontmatter 的 title/source/published 对得上

## 已验证的站点

| 站点 | 结果 |
|---|---|
| ai-bot.cn（WordPress / 中文） | 正文 + 13 张图，用 `--selector div.panel-body` 更稳 |
| ruanyifeng.com（Blogger / 中文） | 自动识别，面包屑与评论区已过滤，29 张图 |
| simonwillison.net（英文博客） | 自动识别，代码块 / 行内代码 / 粗体均正确 |

## 正文定位的两个机制

1. **`pick_content()` 打分**：遍历 `div/article/section/main`，取「`<p>` 文字总量 × (1 − 链接密度)」最高的容器，同分偏向更深的节点。
2. **`strip_boilerplate()` 清洗**：在选中的容器内部，按 class/id 关键词（breadcrumb / comment / related / share / nav / sidebar / copyright / pagination …）摘掉噪音块。阈值是「不超过正文的 35% 且小于 800 字符」，防止把整篇文章误删。

如果站点结构特殊，两个机制都失灵，就用 `--selector` 直接指定，最省事。

## 已知坑

- **中文文件名图片**：URL 里有未编码的中文会触发 `'ascii' codec can't encode`。脚本里的 `encode_url()` 已经处理，别删。
- **懒加载图**：站点常用 `data-src` / `data-original` / `data-lazy-src` 占位，`src` 是 1px 占位图。脚本按 `data-src` → `data-original` → `data-lazy-src` → `src` 顺序取，别改成只读 `src`。
- **标题层级**：`HEADING_MAP` 的 key 必须是字符串 `"h1"/"h2"`，不是整数。
- **frontmatter 换行**：`description` 里常有换行，会把 YAML 撑坏；`yaml_escape()` 已经压成单行。
- **标题站点后缀**：`og:title` 常带 ` | 站点名`，脚本会按 `site` 自动剥掉。
- **标题重复**：很多站点的 `<h1>` 和正文首个标题一样，`Ctx.is_dup_title()` 会跳过正文里重复的那个。
- **图片撞名**：多篇文章共用一个 `images/` 目录时，图片名带文章 slug 前缀（`<slug>-fig-01-xxx.png`），否则各自从 `fig-01` 开始会互相覆盖。
- **不要用 `Invoke-WebRequest` 直接读正文做转换**：它返回的 `.Content` 在部分站点会被当成 UTF-8 解错，中文变乱码。脚本自己按 `Content-Type` / `<meta charset>` 解码。
- **403 / 429**：部分站点（如维基百科对脚本 UA）会拒；这类用浏览器抓 HTML 存到本地，再喂脚本。

## 产出约定

- 图片用相对路径 `images/xxx.png` 引用，**整个输出目录自包含**，能直接搬走或分享。
- 文件名用文章标题，去掉 Windows 非法字符 `\ / : * ? " < > |`。
- 生成后逐条验证 `![](images/...)` 引用的文件真实存在，别只看脚本报的「N 张成功」。
