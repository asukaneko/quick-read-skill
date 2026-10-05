#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_shelf.py — 把多本书的多份速读稿 HTML 汇总成一个**书架式导航页**。

和 build_html.py 的分工：build_html.py 负责把一份速读稿渲染成单书阅读器；
本脚本**不合并任何正文**，只生成一张书架页——书架上有若干本书，一本书
可以有多个速读稿（按段/册，各管一部分章节），每张卡片用**相对路径**指向
已生成的 速读稿.html / 衔接包.html，点击即跳转。

两种收录方式（给 --manifest 用清单；给 --root 扫目录；都不给则自动找
当前目录的 书架.md，找不到就扫当前目录）：

1. 书架.md 显式编排（顺序、册名、简介可控）：

   # 我的速读书架

   ## 《凡人修仙传》
   忘语 · 修仙

   - 第1–320章：凡人修仙传/第1-320章/速读稿.html
   - 第321–640章：凡人修仙传/第321-640章/速读稿.html

   ## 《诡秘之主》
   - [卷一 小丑](诡秘之主/卷一/速读稿.html)

2. 目录扫描：递归找 速读稿.html / 衔接包.html，按一级子目录（或
   阅读器标题里的《书名》）自动归组为一本书。

页面特性：宽窄屏自适应（窄屏单列）、跟随系统深色主题、搜索过滤、
每本书一张 CSS 书脊封面、每份速读稿一张卡片（册号/章号范围/单元统计）。
卡片目标文件是本工具链生成的阅读器时，会读取其进度键——同源部署
（GitHub Pages、http 服务）下显示「读到 N%」丝带与「继续阅读」直达；
本地 file:// 打开时该角标静默不显示，不影响其它功能。

用法
----
python3 build_shelf.py --manifest 阅读工作区/书架.md
python3 build_shelf.py --root 阅读工作区 --out 阅读工作区/书架.html

只依赖 Python 标准库（3.8+）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import html as _html
from pathlib import Path
from urllib.parse import quote

SHELF_NAME = "书架.html"
DIGEST_NAMES = ("速读稿.html", "衔接包.html")
MARKER = 'id="page-data"'          # build_html.py 生成页的识别标记

RANGE_RE = re.compile(r"第\s*(\d{1,6})\s*[–—~～\-至到]\s*(\d{1,6})\s*章?")
RANGE_ONLY_RE = re.compile(r"^第[0-9〇零一二三四五六七八九十百千万两\s、,，和及\-–—~～至到]+章?$")
RANGE_TAIL_RE = re.compile(r"[（(]\s*第\s*\d+[^（）()]*?章\s*[）)]\s*$")
BOOKNAME_RE = re.compile(r"《([^《》]+)》")
EMPH_RE = re.compile(r"[*`]+")
MDLINK_RE = re.compile(r"^\[(.+?)\]\((.+?)\)\s*$")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
PAGE_DATA_RE = re.compile(
    r'<script id="page-data" type="application/json">(.*?)</script>', re.S)
NUM_RE = re.compile(r"\d+")

# 书脊配色：同一套暖纸色系里的几种 muted 颜色，按书名哈希取色
COVER_PALETTE = [
    ("#8a6f4e", "#6e573b"),
    ("#5f7a6a", "#4b6154"),
    ("#5a6b8c", "#475471"),
    ("#8c5a6e", "#704858"),
    ("#a0783f", "#7f5f31"),
    ("#6b7d5a", "#556348"),
    ("#7a5a8c", "#614770"),
    ("#4e8a86", "#3e6e6b"),
]

EMPTY_HTML = """<div id="empty">
书架上还没有书。<br>
写一份 <code>书架.md</code>（每本书一个 <code>## 书名</code> 标题，每行一条
<code>- 册名：路径/速读稿.html</code>），<br>
用 <code>python3 build_shelf.py --manifest 书架.md</code> 生成；<br>
或者把 <code>--root</code> 指向速读稿所在目录，自动扫描收录。
</div>"""


def warn(msg: str, warnings: list):
    warnings.append(msg)


# --------------------------------------------------------------------------
# 从 build_html.py 生成的阅读器里提取元信息
# --------------------------------------------------------------------------
def parse_reader(path: Path) -> dict:
    """读取目标 HTML 的 <title> 与 page-data 统计（key/单元/章节/人物）。"""
    info = {"title": "", "key": None, "units": 0, "chapters": 0, "chars": 0}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    m = TITLE_RE.search(text)
    if m:
        info["title"] = _html.unescape(m.group(1)).strip()
    m = PAGE_DATA_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(1))
            info["key"] = data.get("key") or None
            info["units"] = int(data.get("unitCount") or 0)
            info["chapters"] = int(data.get("chapterCount") or 0)
            info["chars"] = len(data.get("chars") or {})
        except Exception:
            pass
    return info


def find_range(*texts):
    """从若干候选文本里猜章号范围 (起, 止)。"""
    for t in texts:
        if not t:
            continue
        m = RANGE_RE.search(t)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b and b - a < 5000:
                return (a, b)
    return None


def clean_title(t: str) -> str:
    """去掉标题尾部的（第X–Y章）区间，避免和卡片上的范围行重复。"""
    return RANGE_TAIL_RE.sub("", t or "").strip()


def stem_clean(s: str) -> str:
    s = RANGE_TAIL_RE.sub("", s or "").strip()
    for w in ("速读稿", "衔接包"):
        s = s.replace(w, "")
    return s.strip("-_ ··－—")


# --------------------------------------------------------------------------
# 书架.md 解析
# --------------------------------------------------------------------------
def split_item(item: str):
    """清单条目 → (册名, 路径)。认 [册名](路径)、`册名：路径`、裸路径。"""
    s = EMPH_RE.sub("", item).strip()

    def pathish(x: str):
        x = x.strip().strip('<>"\'')
        if re.match(r"^https?://", x, re.I):
            return x
        if x.lower().endswith((".html", ".htm")):
            return x
        return None

    m = MDLINK_RE.match(s)
    if m:
        p = pathish(m.group(2))
        if p:
            return m.group(1).strip(), p
    for sep in ("｜", "：", ":", "|"):
        if sep in s:
            label, rest = s.split(sep, 1)
            p = pathish(rest)
            if p:
                return label.strip(), p
    p = pathish(s)
    if p:
        return "", p
    return None


def parse_manifest(text: str):
    """书架.md → (H1 书架标题, [ {name, desc_lines, digests:[{label,path_str}]} ])"""
    shelf_title = None
    books, cur, desc = [], None, None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## ") and not line.startswith("###"):
            cur = {"name": line[3:].strip().strip("《》"),
                   "desc_lines": [], "digests": []}
            desc = cur["desc_lines"]
            books.append(cur)
            continue
        if line.startswith("# ") and not line.startswith("##"):
            if shelf_title is None:
                shelf_title = line[2:].strip()
            continue
        if cur is None:
            continue
        m = re.match(r"^[-*+]\s+(.*)$", line)
        if m:
            item = split_item(m.group(1).strip())
            if item:
                cur["digests"].append({"label": item[0], "path_str": item[1]})
                continue
        desc.append(line)          # 书名下的说明行，多条用空格连接
    return shelf_title, books


# --------------------------------------------------------------------------
# 路径与链接
# --------------------------------------------------------------------------
def href_for(target: Path, out_dir: Path, warnings: list) -> str:
    """目标文件 → 相对输出 HTML 的链接（POSIX 分隔符，URL 转义）。"""
    try:
        rel = os.path.relpath(target, out_dir)
    except ValueError:
        warn(f"目标与书架页不在同一盘符，改用绝对链接：{target}", warnings)
        return target.as_uri()
    return quote(rel.replace("\\", "/"), safe="/")


def digest_sort_key(d: dict):
    a = d["range"][0] if d["range"] else 10 ** 9
    nums = tuple(int(x) for x in NUM_RE.findall(d["title"] or d["label"] or ""))[:3]
    return (a, nums, d.get("folder") or "", d["title"] or d["label"] or "")


def make_digest(label: str, info: dict, href: str, exists, path_str: str,
                folder: str, warnings: list) -> dict:
    """一份速读稿的展示信息：标题行 / 章号范围 / 类型 / 统计 / 搜索串。"""
    title_parsed = clean_title(info.get("title") or "")
    path_name = Path(path_str).name if path_str else ""
    rng = find_range(label, info.get("title") or "", folder, path_name)
    rng_txt = f"第 {rng[0]}–{rng[1]} 章" if rng else ""
    if label and not RANGE_ONLY_RE.match(label):
        title = label                    # 清单里手写的册名优先
    else:
        title = title_parsed or folder or path_name or "速读稿"
    kind = "bridge" if any("衔接" in x for x in (path_name, title_parsed, label)) else "digest"
    stats = []
    if info.get("units"):
        stats.append(f"单元 {info['units']}")
    if info.get("chapters"):
        stats.append(f"可跳转 {info['chapters']} 章")
    if info.get("chars"):
        stats.append(f"{info['chars']} 人")
    search = " ".join(x for x in (label, title, rng_txt,
                                  f"第{rng[0]}章 第{rng[1]}章" if rng else "",
                                  folder, path_name) if x)
    return {"label": label, "title": title, "range": rng, "rng_txt": rng_txt,
            "kind": kind, "href": href, "exists": exists, "key": info.get("key"),
            "units": info.get("units", 0), "chapters": info.get("chapters", 0),
            "folder": folder, "stats_txt": " · ".join(stats), "search": search}


# --------------------------------------------------------------------------
# 目录扫描模式
# --------------------------------------------------------------------------
def scan_root(root: Path, exclude: Path, warnings: list):
    files = []
    for name in DIGEST_NAMES:
        files.extend(root.rglob(name))
    files = sorted({f for f in files if f != exclude})
    if not files:
        # 名字对不上时退一步：凡带 page-data 标记的 html 都算阅读器
        for f in sorted(root.rglob("*.html")):
            if f == exclude:
                continue
            try:
                with f.open("r", encoding="utf-8", errors="replace") as fh:
                    if MARKER in fh.read(8192):
                        files.append(f)
            except OSError:
                continue
    if not files:
        warn(f"在 {root} 里没找到任何速读稿 HTML（速读稿.html / 衔接包.html）——"
             f"先对各段跑 build_html.py，或改用 --manifest 书架.md 显式指定。", warnings)
    return files


def group_scanned(files, root: Path):
    """扫描结果按《书名》或一级子目录归组成书。"""
    groups, loose = {}, []
    for f in files:
        info = parse_reader(f)
        try:
            rel = f.relative_to(root)
        except ValueError:
            rel = Path(f.name)
        top = rel.parts[0] if len(rel.parts) > 1 else ""        # 一级子目录（归组用）
        segdir = rel.parts[-2] if len(rel.parts) > 1 else ""    # 紧邻父目录（段/卷名）
        m = BOOKNAME_RE.search(info["title"] or "")
        if m:
            key, name = m.group(1), m.group(1)
        elif top:
            key, name = top, top
        else:
            loose.append((f, info, segdir))
            continue
        groups.setdefault(key, {"name": name, "digests": []})
        groups[key]["digests"].append({"file": f, "info": info, "folder": segdir,
                                       "range": find_range(info["title"], segdir, f.stem)})
    if loose:
        name = root.name
        for _, info, _ in loose:
            m = BOOKNAME_RE.search(info["title"] or "")
            if m:
                name = m.group(1)
                break
        loose_group = {"name": name, "digests": []}
        for f, info, segdir in loose:
            loose_group["digests"].append(
                {"file": f, "info": info, "folder": segdir,
                 "range": find_range(info["title"], segdir, f.stem)})
        groups["@@loose"] = loose_group
    return [groups[k] for k in groups]


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_card(no: int, d: dict) -> str:
    cls = "digest"
    if d["kind"] == "bridge":
        cls += " kind-bridge"
    if d["exists"] is False:
        cls += " missing"
    attrs = [f'class="{cls}"', f'href="{_html.escape(d["href"], quote=True)}"']
    if d.get("key"):
        attrs.append(f'data-key="{_html.escape(str(d["key"]), quote=True)}"')
    attrs.append(f'data-search="{_html.escape(d["search"])}"')

    top = [f'<span class="digest-no">第 {no} 册</span>']
    if d["rng_txt"]:
        top.append(f'<span class="digest-range">{_html.escape(d["rng_txt"])}</span>')
    if d["kind"] == "bridge":
        top.append('<span class="digest-tag">衔接包</span>')

    body = [f'<span class="digest-title">{_html.escape(d["title"])}</span>']
    if d["stats_txt"]:
        body.append(f'<span class="digest-stats">{_html.escape(d["stats_txt"])}</span>')
    if d["exists"] is False:
        body.append('<span class="digest-missing">⚠ 目标文件缺失</span>')
    body.append('<span class="digest-progress"></span>')
    return f'<a {" ".join(attrs)}>{"".join(top)}{"".join(body)}</a>'


def render_books(books: list) -> str:
    parts = []
    for i, bk in enumerate(books, 1):
        plain = bk["cover_name"] or "书"
        c1, c2 = COVER_PALETTE[sum(ord(c) for c in plain) % len(COVER_PALETTE)]
        lv = "lv1" if len(plain) <= 7 else ("lv2" if len(plain) <= 10 else "lv3")
        name = bk["name"]
        name_disp = name if "《" in name else f"《{name}》"
        desc = bk.get("desc") or ""

        meta_bits = []
        n = len(bk["digests"])
        if n:
            meta_bits.append(f"{n} 册")
        spans = [d["range"] for d in bk["digests"] if d.get("range")]
        if spans and len(spans) == n:
            meta_bits.append(f"约 {sum(b - a + 1 for a, b in spans)} 章")
        else:
            ch_total = sum(d["chapters"] for d in bk["digests"])
            if ch_total:
                meta_bits.append(f"约 {ch_total} 章")
        stats_txt = " · ".join(meta_bits)

        parts.append(f'<section class="book" id="book-{i}" '
                     f'style="--c1:{c1};--c2:{c2};--book-accent:{c1}" '
                     f'data-search="{_html.escape(f"{name_disp} {plain} {desc}")}">')
        parts.append('<div class="book-head">')
        parts.append(f'<div class="cover {lv}">'
                     f'<span class="cover-title">{_html.escape(plain)}</span></div>')
        parts.append(f'<div class="book-meta"><h2>{_html.escape(name_disp)}</h2>')
        if desc:
            parts.append(f'<p class="book-desc">{_html.escape(desc)}</p>')
        if stats_txt:
            parts.append(f'<p class="book-stats">{_html.escape(stats_txt)}</p>')
        parts.append('</div><span class="fold" title="折叠 / 展开">▾</span></div>')
        parts.append('<div class="cards">')
        for ci, d in enumerate(bk["digests"], 1):
            parts.append(render_card(ci, d))
        parts.append("</div>")
        if not n:
            parts.append('<div class="book-empty">这一本还没有速读稿——'
                         '先对各段跑 build_html.py，再把生成的 HTML 路径写进清单。</div>')
        parts.append("</section>")
    return "\n".join(parts)


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>@@TITLE@@</title>
<style>
:root{
  --bg:#f6f4f0; --surface:#ffffff; --ink:#2c2a27; --ink-2:#6b6660; --line:#e3ded6;
  --accent:#8a6f4e; --accent-soft:#f0e9df; --bridge:#8a8f6a; --key:#b0714a;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 8px 24px rgba(0,0,0,.05);
  --wood-a:#b3895c; --wood-b:#8a6f4e; --radius:14px;
}
html[data-theme="dark"]{
  --bg:#17171a; --surface:#1f1f23; --ink:#e8e5e0; --ink-2:#a09a92; --line:#33323a;
  --accent:#c9a878; --accent-soft:#2a2620; --bridge:#9aa07c; --key:#d08a5c;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
  --wood-a:#4a3d30; --wood-b:#352c24;
}
@media (prefers-color-scheme: dark){
  html[data-theme="auto"]{
    --bg:#17171a; --surface:#1f1f23; --ink:#e8e5e0; --ink-2:#a09a92; --line:#33323a;
    --accent:#c9a878; --accent-soft:#2a2620; --bridge:#9aa07c; --key:#d08a5c;
    --wood-a:#4a3d30; --wood-b:#352c24;
  }
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Songti SC","Noto Serif SC",Georgia,"PingFang SC","Microsoft YaHei",serif;
  font-size:16px; line-height:1.7; -webkit-text-size-adjust:100%;
}
a{color:var(--accent); text-decoration:none}
/* ---------- 顶栏 ---------- */
#topbar{
  position:sticky; top:0; z-index:40;
  background:color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter:blur(10px); border-bottom:1px solid var(--line);
  padding:10px 18px 12px;
}
.tb-row{display:flex; align-items:center; gap:10px; max-width:1080px; margin:0 auto}
#topbar h1{font-size:17px; margin:0; font-weight:700; flex:1; min-width:0;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; letter-spacing:.02em}
.stats{font-size:12px; color:var(--ink-2); font-family:system-ui,sans-serif; white-space:nowrap}
#continue{font-family:system-ui,sans-serif; font-size:12.5px; color:var(--accent);
  border:1px solid var(--accent); border-radius:999px; padding:3px 11px;
  background:var(--surface); white-space:nowrap}
#continue:hover{background:var(--accent-soft)}
.iconbtn{border:1px solid var(--line); background:var(--surface); color:var(--ink);
  border-radius:10px; padding:6px 10px; font-size:14px; cursor:pointer; font-family:system-ui,sans-serif}
.iconbtn:hover{border-color:var(--accent)}
.tb-search{margin-top:9px}
#search{width:100%; padding:9px 14px; border:1px solid var(--line); border-radius:10px;
  background:var(--surface); color:var(--ink); font-size:14px; font-family:system-ui,sans-serif}
#search:focus{outline:2px solid var(--accent-soft); border-color:var(--accent)}
/* ---------- 书架 ---------- */
#main{max-width:1080px; margin:0 auto; padding:30px 18px 60px}
.book{position:relative; margin-bottom:46px; padding-bottom:26px}
.book::after{
  content:""; position:absolute; left:-14px; right:-14px; bottom:0; height:12px; border-radius:6px;
  background:linear-gradient(180deg,var(--wood-a),var(--wood-b));
  box-shadow:0 6px 14px rgba(0,0,0,.13), inset 0 1px 0 rgba(255,255,255,.25);
}
.book-head{display:flex; gap:18px; align-items:flex-end; margin-bottom:16px; cursor:pointer}
.cover{
  flex:0 0 auto; width:66px; height:180px; border-radius:4px 10px 10px 4px; position:relative;
  background:linear-gradient(163deg,var(--c1,#8a6f4e),var(--c2,#6e573b));
  box-shadow:inset 3px 0 4px rgba(255,255,255,.16), inset -5px 0 8px rgba(0,0,0,.22),
    0 8px 16px rgba(0,0,0,.15);
  display:flex; align-items:center; justify-content:center;
}
.cover::before{content:""; position:absolute; left:8px; top:6px; bottom:6px; width:1px;
  background:rgba(255,255,255,.25)}
.cover-title{writing-mode:vertical-rl; color:#f7f1e6; font-weight:600; letter-spacing:.3em;
  text-shadow:0 1px 3px rgba(0,0,0,.35)}
.cover.lv1 .cover-title{font-size:19px}
.cover.lv2 .cover-title{font-size:15px}
.cover.lv3 .cover-title{font-size:12px; letter-spacing:.18em}
.book-meta{flex:1; min-width:0; padding-bottom:2px}
.book-meta h2{margin:0 0 6px; font-size:24px; letter-spacing:.02em; line-height:1.35}
.book-desc{margin:0 0 8px; color:var(--ink-2); font-size:13.5px;
  font-family:system-ui,sans-serif; line-height:1.6}
.book-stats{margin:0; color:var(--accent); font-size:12.5px;
  font-family:system-ui,sans-serif; letter-spacing:.06em}
.fold{flex:0 0 auto; align-self:flex-start; margin-top:8px; color:var(--ink-2);
  font-size:13px; font-family:system-ui,sans-serif; transition:transform .18s ease}
.book.collapsed .fold{transform:rotate(-90deg)}
.book.collapsed .cards{display:none}
/* ---------- 册卡片 ---------- */
.cards{display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px}
.digest{
  position:relative; display:flex; flex-direction:column; gap:5px;
  padding:15px 16px 12px; background:var(--surface); border:1px solid var(--line);
  border-radius:var(--radius); box-shadow:var(--shadow); color:var(--ink); overflow:hidden;
  transition:transform .15s ease, border-color .15s ease, box-shadow .15s ease;
}
.digest:hover{transform:translateY(-2px); border-color:var(--accent);
  box-shadow:0 2px 4px rgba(0,0,0,.06), 0 12px 26px rgba(0,0,0,.09)}
.digest:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.digest::before{content:""; position:absolute; top:0; left:0; right:0; height:3px;
  background:var(--book-accent,var(--accent)); opacity:.8}
.digest-top{display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-family:system-ui,sans-serif}
.digest-no{font-size:11px; font-weight:600; color:#f7f1e6; background:var(--book-accent,var(--accent));
  border-radius:6px; padding:1px 7px; letter-spacing:.08em; white-space:nowrap}
.digest-range{font-size:12px; color:var(--accent); letter-spacing:.04em; font-variant-numeric:tabular-nums}
.digest-tag{font-size:11px; color:var(--bridge); border:1px solid var(--bridge);
  border-radius:6px; padding:0 6px; white-space:nowrap}
.digest-title{font-size:15.5px; font-weight:600; line-height:1.55;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden}
.digest-stats{font-size:12px; color:var(--ink-2); font-family:system-ui,sans-serif}
.digest-missing{font-size:12px; color:var(--key); font-family:system-ui,sans-serif}
.digest.missing{opacity:.62}
.digest-progress{display:none; font-size:12px; color:var(--key);
  font-family:system-ui,sans-serif; margin-top:2px}
.digest.has-progress .digest-progress{display:block}
.digest.done .digest-progress{color:var(--bridge)}
.digest.has-progress::after{
  content:""; position:absolute; top:0; right:16px; width:13px; height:19px;
  background:var(--book-accent,var(--accent)); clip-path:polygon(0 0,100% 0,100% 100%,50% 72%,0 100%);
  filter:drop-shadow(0 1px 1px rgba(0,0,0,.2));
}
.digest.done::after{background:var(--bridge)}
.book-empty{border:1px dashed var(--line); border-radius:var(--radius); padding:16px;
  color:var(--ink-2); font-size:13px; font-family:system-ui,sans-serif}
#noresult{display:none; color:var(--ink-2); text-align:center; padding:36px 0;
  font-family:system-ui,sans-serif}
#empty{border:1px dashed var(--line); border-radius:var(--radius); padding:40px 22px;
  text-align:center; color:var(--ink-2); font-family:system-ui,sans-serif; line-height:2}
#empty code{background:var(--accent-soft); padding:1px 6px; border-radius:5px}
footer{max-width:1080px; margin:0 auto; padding:0 18px 44px; color:var(--ink-2);
  font-size:12px; font-family:system-ui,sans-serif; text-align:center}
/* ---------- 窄屏 ---------- */
@media (max-width:720px){
  #topbar{padding:10px 14px 12px}
  .stats{display:none}
  #main{padding:22px 14px 46px}
  .book{margin-bottom:38px; padding-bottom:20px}
  .book::after{left:-14px; right:-14px; height:10px}
  .book-head{gap:12px; margin-bottom:13px}
  .cover{width:52px; height:148px}
  .cover.lv1 .cover-title{font-size:16px}
  .cover.lv2 .cover-title{font-size:13px}
  .cover.lv3 .cover-title{font-size:10.5px}
  .book-meta h2{font-size:19px}
  .cards{grid-template-columns:1fr; gap:10px}
  .digest{padding:13px 14px 11px}
}
@media print{
  #topbar,footer{display:none!important}
  .book{break-inside:avoid}
}
</style>
</head>
<body>
<header id="topbar">
  <div class="tb-row">
    <h1>@@TITLE@@</h1>
    <span class="stats">@@STATS@@</span>
    <a id="continue" hidden>继续阅读 →</a>
    <button class="iconbtn" id="themebtn" title="切换深浅色">◐</button>
  </div>
  <div class="tb-row tb-search">
    <input id="search" type="search" placeholder="筛选书名 / 册名 / 章号（按 / 聚焦）" autocomplete="off">
  </div>
</header>

<main id="main">
@@BOOKS@@
<div id="noresult">没有匹配的书或速读稿</div>
@@EMPTY@@
</main>

<footer>由 build_shelf.py 生成 · 卡片经相对路径指向各速读稿，正文不合并在本页</footer>

<script>
(function(){
  var root = document.documentElement;

  /* ---------- 深浅色（与速读稿阅读器共用同一个键） ---------- */
  var THEME_KEY = 'nfr:theme';
  try { var st = localStorage.getItem(THEME_KEY); if (st) root.setAttribute('data-theme', st); } catch(e){}
  document.getElementById('themebtn').onclick = function(){
    var cur = root.getAttribute('data-theme');
    if (cur === 'auto') cur = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    var next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem(THEME_KEY, next); } catch(e){}
  };

  /* ---------- 书本折叠 / 展开 ---------- */
  document.querySelectorAll('.book-head').forEach(function(h){
    h.addEventListener('click', function(){
      h.closest('.book').classList.toggle('collapsed');
    });
  });

  /* ---------- 搜索过滤 ---------- */
  var search = document.getElementById('search');
  var noresult = document.getElementById('noresult');
  search.addEventListener('input', function(){
    var q = search.value.trim().toLowerCase();
    var anyBook = false;
    document.querySelectorAll('.book').forEach(function(bk){
      var bHit = !q || (bk.getAttribute('data-search') || '').toLowerCase().indexOf(q) >= 0;
      var vis = 0;
      bk.querySelectorAll('.digest').forEach(function(c){
        var ok = bHit || (c.getAttribute('data-search') || '').toLowerCase().indexOf(q) >= 0;
        c.style.display = ok ? '' : 'none';
        if (ok) vis++;
      });
      bk.style.display = vis ? '' : 'none';
      if (vis){ anyBook = true; if (q) bk.classList.remove('collapsed'); }
    });
    noresult.style.display = (!anyBook && q) ? 'block' : 'none';
  });
  document.addEventListener('keydown', function(e){
    if (e.key === '/' && document.activeElement !== search){ e.preventDefault(); search.focus(); }
    if (e.key === 'Escape' && document.activeElement === search){
      search.value = ''; search.dispatchEvent(new Event('input'));
    }
  });

  /* ---------- 阅读进度角标（同源可读到速读稿的进度键时显示） ---------- */
  var latest = null, latestT = 0;
  document.querySelectorAll('.digest[data-key]').forEach(function(c){
    var s = null;
    try { s = JSON.parse(localStorage.getItem('nfr:progress:' + c.getAttribute('data-key')) || 'null'); } catch(e){}
    if (!s || typeof s.pct !== 'number') return;
    c.classList.add('has-progress');
    if (s.pct >= 100) c.classList.add('done');
    var p = c.querySelector('.digest-progress');
    if (p) p.textContent = s.pct >= 100 ? '已读完 ✓' : ('读到 ' + s.pct + '%');
    var t = s.t || 0;
    if (t > latestT){ latestT = t; latest = c; }
  });
  if (latest){
    var go = document.getElementById('continue');
    go.href = latest.getAttribute('href');
    go.hidden = false;
    latest.classList.add('latest');
  }
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build(books: list, title: str, out_path: Path, warnings: list) -> dict:
    out_dir = out_path.parent
    # 相对路径一律相对最终 HTML 所在目录换算，清单挪了位置链接也不会断
    for bk in books:
        for d in bk["digests"]:
            if d.get("href") is None and d.get("file") is not None:
                d["href"] = href_for(d["file"], out_dir, warnings)

    total_digests = sum(len(bk["digests"]) for bk in books)
    # 章数估算：有章号范围用跨度，否则退回可跳转章数
    total_chapters = sum(
        (d["range"][1] - d["range"][0] + 1) if d.get("range") else d["chapters"]
        for bk in books for d in bk["digests"])
    stats = f"{len(books)} 本书 · {total_digests} 份速读稿"
    if total_chapters:
        stats += f" · 约 {total_chapters} 章"

    page = (PAGE_TEMPLATE
            .replace("@@TITLE@@", _html.escape(title))
            .replace("@@STATS@@", _html.escape(stats))
            .replace("@@BOOKS@@", render_books(books))
            .replace("@@EMPTY@@", "" if books else EMPTY_HTML))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return {"books": len(books), "digests": total_digests,
            "chapters": total_chapters or None, "bytes": out_path.stat().st_size}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="把多本书的多份速读稿 HTML 汇总成书架式导航页（相对路径跳转，不合并正文）")
    ap.add_argument("--manifest", help="书架.md（书 → 册清单；不给则自动找当前目录的 书架.md）")
    ap.add_argument("--root", help="扫描根目录（递归找 速读稿.html / 衔接包.html 自动归组）")
    ap.add_argument("--out", help="输出 html，默认 书架.html（写在清单/根目录旁）")
    ap.add_argument("--title", help="书架标题，默认取 书架.md 的 H1 或「速读书架」")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    args = ap.parse_args()

    warnings: list = []
    manifest = Path(args.manifest).expanduser() if args.manifest else None
    root = Path(args.root).expanduser().resolve() if args.root else None
    if args.manifest is None and args.root is None:
        cand = Path("书架.md")
        if cand.is_file():
            manifest = cand
        else:
            root = Path.cwd()
    if manifest is not None and not manifest.is_file():
        raise SystemExit(f"找不到书架清单：{manifest}")

    if manifest is not None:
        # ---- 清单模式 ----
        base = manifest.parent
        out_path = (Path(args.out).expanduser().resolve() if args.out
                    else base / SHELF_NAME)
        shelf_title, mbooks = parse_manifest(
            manifest.read_text(encoding="utf-8-sig"))
        books = []
        for mb in mbooks:
            entry = {"name": mb["name"], "desc": " ".join(mb["desc_lines"]).strip(),
                     "cover_name": mb["name"], "digests": []}
            for d in mb["digests"]:
                path_str = d["path_str"]
                if re.match(r"^https?://", path_str, re.I):
                    # 外链：能跳转，但读不到统计与进度
                    dg = make_digest(d["label"], {}, path_str, None, path_str,
                                     "", warnings)
                    dg["href"] = path_str
                else:
                    target = Path(os.path.normpath(
                        path_str if Path(path_str).is_absolute() else base / path_str))
                    exists = target.is_file()
                    info = parse_reader(target) if exists else {}
                    if not exists:
                        warn(f"「{mb['name']}」指向的文件不存在：{path_str}"
                             f"（先跑 build_html.py 生成，或检查相对路径）", warnings)
                    dg = make_digest(d["label"], info, None, exists, path_str,
                                     Path(path_str).parent.name, warnings)
                    dg["file"] = target      # href 统一在 build() 里按输出位置换算
                entry["digests"].append(dg)
            if not entry["digests"]:
                warn(f"「{mb['name']}」下面没有可用的速读稿条目"
                     f"（每行写：- 册名：相对路径/速读稿.html）", warnings)
            books.append(entry)
        if not mbooks:
            warn(f"{manifest.name} 里没解析到任何书（每本书一个「## 书名」标题）", warnings)
    else:
        # ---- 扫描模式 ----
        out_path = (Path(args.out).expanduser().resolve() if args.out
                    else root / SHELF_NAME)
        shelf_title = None
        books = []
        for g in group_scanned(scan_root(root, out_path, warnings), root):
            digests = []
            for item in g["digests"]:
                f, info, folder = item["file"], item["info"], item["folder"]
                dg = make_digest("", info, None, True, str(f), folder, warnings)
                dg["file"] = f
                dg["range"] = dg["range"] or item.get("range")
                digests.append(dg)
            digests.sort(key=digest_sort_key)
            books.append({"name": g["name"], "desc": "", "cover_name": g["name"],
                          "digests": digests})

    title = args.title or shelf_title or "速读书架"
    info = build(books, title, out_path, warnings)

    if args.json:
        print(json.dumps({"html": str(out_path), "title": title, **info,
                          "warnings": warnings}, ensure_ascii=False, indent=2))
    else:
        kb = info["bytes"] / 1024
        print(f"✅ 书架已生成：{out_path}（{kb:.0f} KB）")
        stats = f"{info['books']} 本书 · {info['digests']} 份速读稿"
        if info.get("chapters"):
            stats += f" · 约 {info['chapters']} 章"
        print(f"   {stats}")
        for w in warnings:
            print(f"   ⚠️ {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
