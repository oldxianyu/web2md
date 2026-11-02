#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""html2md.py —— 把一个网页文章抓下来，转成本地 Markdown（正文提取 + 图片本地化）。

用法：
    python html2md.py <URL> [-o 输出.md] [--assets 目录名] [--selector "div.panel-body"]
                      [--no-images] [--keep-links]

不指定 --selector 时自动猜正文容器（按 <p> 文字量 + 链接密度打分）。
"""

import argparse
import os
import re
import sys
import urllib.request
from datetime import datetime
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit

try:
    import lxml.html
except ImportError:
    sys.exit("需要 lxml：python -m pip install lxml")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DROP_TAGS = {"script", "style", "noscript", "iframe", "svg", "form", "nav",
             "footer", "aside", "header", "button", "input", "select", "textarea"}

# class/id 命中这些词的容器视为页面噪音，整块丢掉
BOILER_PAT = re.compile(
    r"(?:^|[\s_-])(?:breadcrumb|crumb|nav|navbar|menu|sidebar|side-bar|share|social"
    r"|related|recommend|comment|footer|header|advert|ads?|banner|toolbar|pagination"
    r"|pager|subscribe|newsletter|copyright|tags?|author-box|post-nav|prev-next)(?:$|[\s_-])",
    re.I)

# 正文里的标题层级映射：页面标题是 h1，正文里的 h1 降一级
HEADING_MAP = {"h1": "##", "h2": "##", "h3": "###", "h4": "####",
               "h5": "#####", "h6": "######"}

# 默认落盘目录：环境变量 WEB2MD_OUT_DIR，没设就写到当前目录
DEFAULT_OUT_DIR = os.environ.get("WEB2MD_OUT_DIR", "")


# ---------------------------------------------------------------- 抓取

def encode_url(url):
    """把 URL 里的非 ASCII 字符（中文文件名等）转成百分号编码，否则 urllib 会报错。"""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%")
    query = quote(parts.query, safe="=&%")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def fetch(url, timeout=60):
    url = encode_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    m = re.search(r"charset=[\"']?([\w-]+)", ctype, re.I)
    enc = m.group(1) if m else None
    if not enc:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:8192], re.I)
        enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def download_image(url, referer, dest, timeout=60):
    req = urllib.request.Request(encode_url(url), headers={
        "User-Agent": UA,
        "Referer": encode_url(referer),
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    if not data:
        raise ValueError("空文件")
    # 没有扩展名 / 扩展名不靠谱时按 Content-Type 补
    if not os.path.splitext(dest)[1]:
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
               "image/gif": ".gif", "image/svg+xml": ".svg"}.get(ctype.split(";")[0].strip(), ".png")
        dest += ext
    with open(dest, "wb") as f:
        f.write(data)
    return dest, len(data)


# ---------------------------------------------------------------- 正文定位

def parse_simple_selector(sel):
    """支持 tag / .class / #id / tag.class 这种最简写法。"""
    sel = sel.strip()
    tag = None
    classes, ident = [], None
    m = re.match(r"^([a-zA-Z0-9_-]+)", sel)
    if m:
        tag = m.group(1).lower()
    for c in re.findall(r"\.([\w-]+)", sel):
        classes.append(c)
    m = re.search(r"#([\w-]+)", sel)
    if m:
        ident = m.group(1)
    return tag, classes, ident


def matches(node, tag, classes, ident):
    if tag and node.tag.lower() != tag:
        return False
    if ident and (node.get("id") or "") != ident:
        return False
    if classes:
        node_cls = set((node.get("class") or "").split())
        if not all(c in node_cls for c in classes):
            return False
    return True


def text_len(node):
    return len("".join(node.itertext()).strip())


def strip_boilerplate(node):
    """在正文容器内部，把面包屑/评论/相关阅读/分享等噪音块摘掉。"""
    for el in list(node.iter()):
        if not isinstance(el.tag, str):
            continue
        if el is node:
            continue
        marker = f"{el.get('class') or ''} {el.get('id') or ''}"
        if marker.strip() and BOILER_PAT.search(marker):
            # 只摘掉小块，别把整篇正文摘了
            if text_len(el) < max(text_len(node) * 0.35, 800):
                el.drop_tree()
    return node


def pick_content(doc, selector=None):
    if selector:
        tag, classes, ident = parse_simple_selector(selector)
        hits = [n for n in doc.iter() if isinstance(n.tag, str) and matches(n, tag, classes, ident)]
        if not hits:
            sys.exit(f"selector 没匹配到节点：{selector}")
        return max(hits, key=text_len)

    best, best_score = None, 0.0
    for node in doc.iter():
        if not isinstance(node.tag, str) or node.tag.lower() not in ("div", "article", "section", "main"):
            continue
        ps = node.findall(".//p")
        if len(ps) < 3:
            continue
        text = sum(text_len(p) for p in ps)
        if text < 200:
            continue
        links = sum(text_len(a) for a in node.findall(".//a"))
        density = min(links / max(text, 1), 1.0)
        score = text * (1.0 - density)
        # 同分时偏向更深的（更聚焦的）容器
        score += len(node.xpath("ancestor::*")) * 0.01
        if score > best_score:
            best, best_score = node, score
    if best is None:
        sys.exit("没找到正文容器，请用 --selector 指定")
    return best


def page_meta(doc):
    def meta(prop):
        for xp in (f'//meta[@property="{prop}"]/@content', f'//meta[@name="{prop}"]/@content'):
            v = doc.xpath(xp)
            if v and v[0].strip():
                return v[0].strip()
        return ""

    title = meta("og:title")
    if not title:
        hs = doc.xpath("//h1")
        title = " ".join(hs[0].itertext()).strip() if hs else ""
    if not title and doc.findtext(".//title"):
        title = doc.findtext(".//title").strip()
    author = meta("article:author") or meta("author")
    published = meta("article:published_time") or meta("og:update_time")
    site = meta("og:site_name") or (urlparse(meta("og:url")).netloc if meta("og:url") else "")
    desc = meta("og:description") or meta("description")
    site = site.strip()
    # 去掉标题里的站点后缀，如 "标题 | AI工具集" / "标题 - 某某网"
    if site and title:
        for sep in (" | ", " - ", " — ", " _ ", "｜"):
            suffix = sep + site
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
                break
    return {"title": title, "author": author, "published": published,
            "site": site, "description": desc}


# ---------------------------------------------------------------- 转换

class Ctx:
    def __init__(self, base_url, assets_dir, assets_rel, images_on=True, page_title="", slug=""):
        self.base_url = base_url
        self.assets_dir = assets_dir
        self.assets_rel = assets_rel
        self.images_on = images_on
        self.page_title = re.sub(r"\s+", "", page_title or "")
        self.slug = slug
        self.title_seen = False
        self.img_no = 0
        self.images = []
        self.skipped = []

    def is_dup_title(self, text):
        """正文里的第一个标题如果和页面标题一样，跳过（避免 H1/H2 重复）。"""
        if self.title_seen:
            return False
        flat = re.sub(r"\s+", "", text)
        if self.page_title and flat == self.page_title:
            self.title_seen = True
            return True
        return False

    def image(self, el):
        src = (el.get("data-src") or el.get("data-original") or el.get("data-lazy-src")
               or el.get("src") or "").strip()
        if not src or src.startswith("data:"):
            return ""
        url = urljoin(self.base_url, src)
        alt = (el.get("alt") or "").strip()
        if not self.images_on:
            return f"![{alt}]({url})"
        self.img_no += 1
        stem = os.path.splitext(os.path.basename(urlparse(url).path))[0] or "image"
        stem = re.sub(r"[\\/:*?\"<>|\s]+", "-", stem)[:40]
        # 多篇文章共用一个 images/，用文章 slug 前缀避免 fig-01 撞名互相覆盖
        prefix = f"{self.slug}-" if self.slug else ""
        name = f"{prefix}fig-{self.img_no:02d}-{stem}"
        dest = os.path.join(self.assets_dir, name)
        try:
            dest, size = download_image(url, self.base_url, dest)
            rel = f"{self.assets_rel}/{os.path.basename(dest)}"
            self.images.append((rel, size, url))
            return f"![{alt or os.path.basename(dest)}]({rel})"
        except Exception as exc:  # 图片挂了不影响正文
            self.skipped.append((url, str(exc)))
            return f"![{alt}]({url})"


def clean(text):
    if not text:
        return ""
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def render_inline(node, ctx):
    """把节点内部的文字渲染成一行 markdown（不处理块级标签）。"""
    out = []
    if node.text:
        out.append(clean(node.text))
    for ch in node:
        if not isinstance(ch.tag, str):
            continue
        tag = ch.tag.lower()
        if tag in DROP_TAGS:
            if ch.tail:
                out.append(clean(ch.tail))
            continue
        inner = render_inline(ch, ctx).strip()
        if tag in ("strong", "b"):
            out.append(f"**{inner}**" if inner else "")
        elif tag in ("em", "i"):
            out.append(f"*{inner}*" if inner else "")
        elif tag == "code":
            out.append(f"`{inner}`" if inner else "")
        elif tag == "a":
            href = (ch.get("href") or "").strip()
            if not href or href.startswith("javascript:"):
                out.append(inner)
            else:
                out.append(f"[{inner or urljoin(ctx.base_url, href)}]({urljoin(ctx.base_url, href)})")
        elif tag == "img":
            out.append(ctx.image(ch))
        elif tag == "br":
            out.append("  \n")
        else:
            out.append(inner)
        if ch.tail:
            out.append(clean(ch.tail))
    return "".join(out)


def squash(s):
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def render_list(el, ctx, ordered=False):
    lines = []
    items = [c for c in el if isinstance(c.tag, str) and c.tag.lower() == "li"]
    for i, li in enumerate(items, 1):
        body = squash(render_inline(li, ctx))
        body = body.replace("\n", " ")
        lines.append(f"{i}. {body}" if ordered else f"- {body}")
    return "\n".join(lines)


def render_table(el, ctx):
    rows = []
    for tr in el.findall(".//tr"):
        cells = [squash(render_inline(c, ctx)).replace("\n", " ").replace("|", "\\|")
                 for c in tr if isinstance(c.tag, str) and c.tag.lower() in ("td", "th")]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def render_blocks(node, ctx, depth=0):
    """把容器节点的子节点按顺序渲染成 markdown 块。"""
    blocks = []
    if node.text and clean(node.text).strip():
        blocks.append(clean(node.text).strip())

    for ch in node:
        if not isinstance(ch.tag, str):
            continue
        tag = ch.tag.lower()
        if tag in DROP_TAGS:
            continue
        if tag in HEADING_MAP:
            txt = squash(render_inline(ch, ctx)).replace("\n", " ")
            # 标题本身已经是强调，去掉内嵌的 **粗体** / *斜体* 标记
            txt = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", txt)
            txt = txt.strip().rstrip("。.")
            if txt and not ctx.is_dup_title(txt):
                blocks.append(f"{HEADING_MAP[tag]} {txt}")
        elif tag == "p":
            txt = squash(render_inline(ch, ctx))
            if txt:
                blocks.append(txt)
        elif tag == "blockquote":
            inner = render_blocks(ch, ctx, depth + 1) or squash(render_inline(ch, ctx))
            inner = inner.strip()
            if inner:
                blocks.append("\n".join("> " + ln if ln.strip() else ">"
                                        for ln in inner.splitlines()))
        elif tag in ("ul", "ol"):
            t = render_list(ch, ctx, ordered=(tag == "ol"))
            if t:
                blocks.append(t)
        elif tag == "pre":
            code = ch.text_content().rstrip()
            lang = ""
            for c in ch:
                if isinstance(c.tag, str) and c.tag.lower() == "code":
                    cls = c.get("class") or ""
                    m = re.search(r"language-([\w+-]+)", cls)
                    if m:
                        lang = m.group(1)
                    code = c.text_content().rstrip()
            blocks.append(f"```{lang}\n{code}\n```")
        elif tag == "table":
            t = render_table(ch, ctx)
            if t:
                blocks.append(t)
        elif tag == "img":
            t = ctx.image(ch)
            if t:
                blocks.append(t)
        elif tag == "hr":
            blocks.append("---")
        elif tag in ("div", "section", "article", "main", "figure", "figcaption", "span"):
            inner = render_blocks(ch, ctx, depth + 1)
            if inner.strip():
                blocks.append(inner.strip())
        else:
            txt = squash(render_inline(ch, ctx))
            if txt:
                blocks.append(txt)
    return "\n\n".join(b for b in blocks if b.strip())


# ---------------------------------------------------------------- 主流程

def yaml_escape(s):
    if not s:
        return '""'
    s = re.sub(r"\s*\n\s*", " ", s).strip()
    if re.search(r"[:#\"'\n\[\]{}&*!|>%@`]", s) or s != s.strip():
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def main():
    ap = argparse.ArgumentParser(description="网页文章转本地 Markdown")
    ap.add_argument("url")
    ap.add_argument("-o", "--out", help="输出 .md 路径")
    ap.add_argument("--assets", default="images", help="图片目录名（相对 md 所在目录）")
    ap.add_argument("--selector", help='正文容器选择器，如 "div.panel-body"')
    ap.add_argument("--no-images", action="store_true", help="只保留图片外链，不下载")
    ap.add_argument("--keep-links", action="store_true", help="保留原文链接（默认也保留）")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                    help="不指定 -o 时的输出目录，默认当前目录（可用环境变量 WEB2MD_OUT_DIR 设定）")
    args = ap.parse_args()

    html_text = fetch(args.url)
    doc = lxml.html.fromstring(html_text)
    for bad in doc.iter():
        if isinstance(bad.tag, str) and bad.tag.lower() in DROP_TAGS:
            bad.drop_tree()

    meta = page_meta(doc)
    content = strip_boilerplate(pick_content(doc, args.selector))

    out_md = args.out
    if not out_md:
        name = re.sub(r"[\\/:*?\"<>|]+", "-", meta["title"] or "article").strip() or "article"
        out_md = os.path.join(args.out_dir, name[:80] + ".md")
    elif not os.path.isabs(out_md) and not os.path.dirname(out_md):
        # 只给了文件名 -> 落到输出目录
        out_md = os.path.join(args.out_dir, out_md)
    out_dir = os.path.dirname(os.path.abspath(out_md)) or "."
    os.makedirs(out_dir, exist_ok=True)
    # 文章 slug：用于给图片加前缀，避免多篇文章撞名
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", os.path.splitext(os.path.basename(out_md))[0])[:20]

    assets_dir = os.path.join(out_dir, args.assets)
    if not args.no_images:
        os.makedirs(assets_dir, exist_ok=True)
    ctx = Ctx(args.url, assets_dir, args.assets, images_on=not args.no_images,
              page_title=meta["title"], slug=slug)

    body = render_blocks(content, ctx)

    fm = ["---",
          f"title: {yaml_escape(meta['title'])}",
          f"source: {args.url}"]
    if meta["author"]:
        fm.append(f"author: {yaml_escape(meta['author'])}")
    if meta["site"]:
        fm.append(f"site: {yaml_escape(meta['site'])}")
    if meta["published"]:
        fm.append(f"published: {yaml_escape(meta['published'])}")
    fm.append(f"converted: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if meta["description"]:
        fm.append(f"description: {yaml_escape(meta['description'][:200])}")
    fm.append("---")

    md = "\n".join(fm) + f"\n\n# {meta['title']}\n\n{body}\n"
    with open(out_md, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)

    print(f"[ok] {out_md}")
    print(f"     正文 {len(body)} 字符，图片 {len(ctx.images)} 张下载成功，{len(ctx.skipped)} 张失败")
    for url, err in ctx.skipped:
        print(f"     [图片失败] {url} -> {err}")


if __name__ == "__main__":
    main()
