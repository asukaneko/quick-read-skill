#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_html.py — 把速读稿 markdown 渲染成**单文件 HTML 阅读器**。

设计目标：AI 只要按既有体例产出 markdown（速读稿.md / 人物档案.md），
其它一律交给本脚本，不需要模型再手写任何 HTML。

生成物特性
----------
1. 单元导航：侧边栏列出全部单元，点击直达；章节速查可跳到具体章（有切片的章跳到切片）。
2. 人物链接：正文里的人名自动变成可点击链接，点开看《人物档案》里的该人物详情。
3. 阅读进度本地缓存：localStorage 记录滚动位置与当前单元，刷新/关掉重开会回到原处。
4. 响应式：宽屏左侧固定导航 + 正文双栏；窄屏导航收成抽屉，人物详情用底部抽屉。
5. 单文件、零依赖、零外链（可直接放进 App WebView、微信、或任意离线环境）。

用法
----
python3 build_html.py --digest 第1583-1678章/速读稿.md
python3 build_html.py --digest 速读稿.md --characters 人物档案.md --index chapters.tsv \\
        --title "《凡人修仙传》速读稿" --out 速读稿.html

只依赖 Python 标准库（3.8+）。
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 中文数字
# --------------------------------------------------------------------------
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_NUM = r"[0-9〇零一二三四五六七八九十百千万两]{1,12}"


def cn2num(text: str):
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = section = number = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


# --------------------------------------------------------------------------
# 块级解析
# --------------------------------------------------------------------------
SLICE_RE = re.compile(r"^\s*(?:\*\*)?\s*【原文\s*[·・:：]?\s*([^】]*)】\s*(?:\*\*)?\s*(.*)$")
UNIT_RE = re.compile(r"^##\s*单元\s*([0-9〇零一二三四五六七八九十百]*)\s*[｜|：:、,.\-—]?\s*(.*)$")
UNIT_RANGE_RE = re.compile(r"[（(]\s*第?\s*(\d{1,6})\s*[–—~～\-至到]\s*(\d{1,6})\s*章?\s*[）)]")
UL_RE = re.compile(r"^[-*+]\s+")
OL_RE = re.compile(r"^\d+[.)]\s+")
HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
TABLE_SEP_RE = re.compile(r"^:?-{2,}:?$")
BRIDGE_RE = re.compile(r"^\*\*【(过渡|重要|原文[^】]*)】\*\*")
UNIT_END_RE = re.compile(r"^\*\*这一段结束时\*\*")


def parse_blocks(text: str):
    """markdown → 块列表。只认速读稿体例里实际会出现的结构。"""
    lines = text.split("\n")
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue

        m = SLICE_RE.match(raw)
        if m:
            chapter, label = m.group(1).strip(), m.group(2).strip()
            j = i + 1
            body = []
            while j < n:
                t = lines[j].lstrip()
                if t.startswith(">"):
                    body.append(re.sub(r"^\s*>\s?", "", lines[j]).rstrip())
                    j += 1
                elif not lines[j].strip():
                    # 允许切片内部有空行，空行后仍跟引用则继续
                    k = j
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n and lines[k].lstrip().startswith(">"):
                        body.append("")
                        j = k
                    else:
                        break
                else:
                    break
            blocks.append({"t": "slice", "chapter": chapter, "label": label, "lines": body})
            i = j
            continue

        if s.startswith("#"):
            lvl = len(s) - len(s.lstrip("#"))
            blocks.append({"t": "h", "level": lvl, "text": s[lvl:].strip()})
            i += 1
            continue

        if HR_RE.match(s):
            blocks.append({"t": "hr"})
            i += 1
            continue

        if s.startswith(">"):
            body = []
            while i < n and lines[i].lstrip().startswith(">"):
                body.append(re.sub(r"^\s*>\s?", "", lines[i]).rstrip())
                i += 1
            blocks.append({"t": "quote", "lines": body})
            continue

        if s.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                cells = lines[i].strip().strip("|").split("|")
                rows.append([c.strip() for c in cells])
                i += 1
            blocks.append({"t": "table", "rows": rows})
            continue

        if UL_RE.match(s):
            items = []
            while i < n and UL_RE.match(lines[i].strip()):
                items.append(UL_RE.sub("", lines[i].strip()))
                i += 1
            blocks.append({"t": "ul", "items": items})
            continue

        if OL_RE.match(s):
            items = []
            while i < n and OL_RE.match(lines[i].strip()):
                items.append(OL_RE.sub("", lines[i].strip()))
                i += 1
            blocks.append({"t": "ol", "items": items})
            continue

        para = []
        while i < n:
            cur = lines[i]
            cs = cur.strip()
            if not cs:
                break
            if cs.startswith(("#", "|", ">")) or UL_RE.match(cs) or OL_RE.match(cs):
                break
            if HR_RE.match(cs) or SLICE_RE.match(cur):
                break
            para.append(cs)
            i += 1
        blocks.append({"t": "p", "text": "\n".join(para)})
    return blocks


# --------------------------------------------------------------------------
# 人物档案解析
# --------------------------------------------------------------------------
ALIAS_HEAD_KEYS = ("本名", "姓名", "人物", "角色")
ALIAS_COL_KEYS = ("马甲", "尊称", "别名", "化名", "道号", "代号", "称呼")
SPLIT_RE = re.compile(r"[、,，/／|;；\s]+")
EMPH_RE = re.compile(r"[*`]+")
PAREN_RE = re.compile(r"（[^）]*）|\([^()]*\)")


def clean_cell(s) -> str:
    """去掉表格单元格里的 markdown 强调符号（**加粗** / `代码`）。

    别名归一表的「本名」「马甲」列常写成 **韩立** 这类加粗体例，
    不剥离的话别名键会变成 '**韩立**'，正文人名就再也匹配不上。
    """
    return EMPH_RE.sub("", s or "").strip()


def clean_alias_cell(s) -> str:
    """别名单元格：再去掉「（第1292章 某人所称）」这类括号说明。"""
    return clean_cell(PAREN_RE.sub("", s or ""))


def parse_characters(path: Path):
    """返回 (aliases: {别名: 本名}, details: {本名: html片段})。"""
    aliases: dict[str, str] = {}
    details: dict[str, str] = {}
    if not path or not path.is_file():
        return aliases, details

    blocks = parse_blocks(path.read_text(encoding="utf-8"))

    # 1) 别名归一表
    for b in blocks:
        if b["t"] != "table" or len(b["rows"]) < 2:
            continue
        header = [clean_cell(c) for c in b["rows"][0]]
        if not any(any(k in c for k in ALIAS_HEAD_KEYS) for c in header):
            continue
        name_col, alias_cols = None, []
        for ci, c in enumerate(header):
            if any(k in c for k in ALIAS_HEAD_KEYS):
                if name_col is None:
                    name_col = ci
            elif any(k in c for k in ALIAS_COL_KEYS):
                alias_cols.append(ci)
        if name_col is None:
            continue
        for row in b["rows"][1:]:
            if len(row) <= name_col or TABLE_SEP_RE.match(row[name_col] or "--"):
                continue
            main = clean_cell(row[name_col])
            if not main:
                continue
            aliases.setdefault(main, main)
            for ci in alias_cols:
                if ci < len(row):
                    for a in SPLIT_RE.split(clean_alias_cell(row[ci])):
                        a = a.strip()
                        if a and len(a) <= 8:
                            aliases.setdefault(a, main)

    # 2) 人物条目（h2/h3/h4 标题名命中别名表，或位于「人物」章节下的 h3）
    cur_name, buf = None, []
    known = set(aliases.values())

    def flush():
        if cur_name and buf:
            body = "\n".join(buf).strip()
            if body:
                details[cur_name] = render_char_detail(body, aliases)

    for b in blocks:
        if b["t"] == "h":
            title = b["text"].strip().strip("*").strip()
            hit = title if title in aliases else None
            if hit is None and b["level"] >= 3 and known:
                for k in aliases:
                    if title.startswith(k) and len(k) >= 2:
                        hit = aliases[k]
                        break
            if hit:
                flush()
                cur_name = aliases.get(hit, hit)
                buf = []
                continue
            if b["level"] <= 2 and cur_name:
                flush()
                cur_name, buf = None, []
                continue
            if cur_name:
                buf.append(f"### {title}" if b["level"] >= 3 else f"## {title}")
                continue
            continue
        if cur_name:
            buf.append(block_to_md(b))
    flush()
    return aliases, details


def block_to_md(b) -> str:
    t = b["t"]
    if t == "p":
        return b["text"]
    if t == "h":
        return "#" * b["level"] + " " + b["text"]
    if t == "ul":
        return "\n".join("- " + x for x in b["items"])
    if t == "ol":
        return "\n".join(f"{i+1}. {x}" for i, x in enumerate(b["items"]))
    if t == "quote":
        return "\n".join("> " + x for x in b["lines"])
    if t == "slice":
        return f"【原文·{b['chapter']}】\n" + "\n".join("> " + x for x in b["lines"])
    if t == "table":
        return "\n".join("| " + " | ".join(r) + " |" for r in b["rows"])
    if t == "hr":
        return "---"
    return ""


# --------------------------------------------------------------------------
# 行内渲染
# --------------------------------------------------------------------------
CHAP_RANGE_RE = re.compile(r"第?\s*(" + _NUM + r")\s*(?:章)?\s*[–—~～\-至到]\s*(" + _NUM + r")\s*[章回]")
CHAP_ONE_RE = re.compile(r"第\s*(" + _NUM + r")\s*[章回]")


class RenderCtx:
    def __init__(self, aliases, cmap, link_all=False):
        self.alias_keys = sorted([a for a in aliases if len(a) >= 2],
                                 key=len, reverse=True)
        self.aliases = aliases
        self.cmap = cmap                # 章号 -> 锚点
        self.link_all = link_all
        self.linked: set[str] = set()

    def reset_unit(self):
        self.linked = set()


def _inline(raw: str, ctx: RenderCtx, allow_char=True, allow_chap=True) -> str:
    s = raw
    if allow_char and ctx.alias_keys:
        pattern = re.compile("|".join(re.escape(a) for a in ctx.alias_keys))
        out, pos = [], 0
        for m in pattern.finditer(s):
            name = ctx.aliases.get(m.group(0), m.group(0))
            out.append(s[pos:m.start()])
            if ctx.link_all or name not in ctx.linked:
                ctx.linked.add(name)
                out.append(f"⟦P:{name}⟧")
            else:
                out.append(m.group(0))
            pos = m.end()
        out.append(s[pos:])
        s = "".join(out)

    if allow_chap and ctx.cmap:
        def repl_range(m):
            a, b = cn2num(m.group(1)), cn2num(m.group(2))
            if not a or not b:
                return m.group(0)
            anchor = ctx.cmap.get(a) or ctx.cmap.get(b)
            if not anchor:
                return m.group(0)
            return f"⟦R:{a}-{b}:{anchor}⟧"

        def repl_one(m):
            v = cn2num(m.group(1))
            if not v:
                return m.group(0)
            anchor = ctx.cmap.get(v)
            if not anchor:
                return m.group(0)
            return f"⟦C:{v}:{anchor}⟧"

        s = CHAP_RANGE_RE.sub(repl_range, s)
        s = CHAP_ONE_RE.sub(repl_one, s)

    s = _html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", s)
    s = re.sub(r"⟦P:([^⟧]+)⟧",
               lambda m: f'<a class="char" data-c="{m.group(1)}" href="#char-panel">{m.group(1)}</a>',
               s)
    s = re.sub(r"⟦C:(\d+):([^⟧]+)⟧",
               lambda m: f'<a class="chip" href="#{m.group(2)}">第{m.group(1)}章</a>',
               s)
    s = re.sub(r"⟦R:(\d+)-(\d+):([^⟧]+)⟧",
               lambda m: f'<a class="chip" href="#{m.group(3)}">第{m.group(1)}–{m.group(2)}章</a>',
               s)
    return s


def render_char_detail(md_text: str, aliases) -> str:
    blocks = parse_blocks(md_text)
    ctx = RenderCtx(aliases, {}, link_all=True)
    parts = []
    for b in blocks:
        parts.append(render_block(b, ctx))
    return "\n".join(x for x in parts if x)


# --------------------------------------------------------------------------
# 块级渲染
# --------------------------------------------------------------------------
def render_table(b, ctx) -> str:
    rows = b["rows"]
    if len(rows) >= 2 and all(TABLE_SEP_RE.match(c or "--") for c in rows[1]) and len(rows[1]) == len(rows[0]):
        head, body = rows[0], rows[2:]
    else:
        head, body = rows[0], rows[1:]
    out = ['<div class="table-wrap"><table>', "<thead><tr>"]
    for c in head:
        out.append(f"<th>{_inline(c, ctx)}</th>")
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>")
        for c in r:
            out.append(f"<td>{_inline(c, ctx)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render_block(b, ctx: RenderCtx) -> str:
    t = b["t"]
    if t == "h":
        lvl = min(max(b["level"], 2), 4)
        return f'<h{lvl} class="h{lvl}">{_inline(b["text"], ctx, allow_char=False)}</h{lvl}>'
    if t == "p":
        text = b["text"]
        cls = ""
        m = BRIDGE_RE.match(text)
        if m:
            cls = ' class="callout callout-bridge"'
        elif UNIT_END_RE.match(text):
            cls = ' class="callout callout-end"'
        elif text.startswith("**【重要】**"):
            cls = ' class="callout callout-key"'
        body = _inline(text, ctx).replace("\n", "<br>")
        return f"<p{cls}>{body}</p>"
    if t == "slice":
        anchor = b.get("id", "")
        head = f"【原文·{b['chapter']}】" if not b.get("label") else f"【原文·{b['chapter']} {b['label']}】"
        body = "<br>".join(_inline(x, ctx, allow_char=False, allow_chap=False) for x in b["lines"])
        return (f'<figure class="slice" id="{anchor}">'
                f'<figcaption>{_html.escape(head)}</figcaption>'
                f'<blockquote>{body}</blockquote></figure>')
    if t == "quote":
        body = "<br>".join(_inline(x, ctx, allow_char=False) for x in b["lines"])
        return f'<blockquote class="quote">{body}</blockquote>'
    if t == "ul":
        return "<ul>" + "".join(f"<li>{_inline(x, ctx)}</li>" for x in b["items"]) + "</ul>"
    if t == "ol":
        return "<ol>" + "".join(f"<li>{_inline(x, ctx)}</li>" for x in b["items"]) + "</ol>"
    if t == "table":
        return render_table(b, ctx)
    if t == "hr":
        return '<hr class="rule">'
    return ""


# --------------------------------------------------------------------------
# 章节索引
# --------------------------------------------------------------------------
def load_index(path: Path):
    if not path or not path.is_file():
        return {}
    out = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                out[int(parts[0])] = parts[1].strip()
            except ValueError:
                continue
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build(digest_path: Path, characters_path, index_path, title, out_path,
          link_all=False, progress_key=None):
    text = digest_path.read_text(encoding="utf-8")
    blocks = parse_blocks(text)
    aliases, details = parse_characters(characters_path)
    index_rows = load_index(index_path)

    # --- 第一遍：切单元 / 总览附录章节，给切片编号 ---
    head = {"kind": "head", "num": 0, "title": "", "range": (None, None),
            "id": "sec-0", "blocks": [], "slices": []}
    buckets = [head]
    cur = head
    sec_n = 0
    for b in blocks:
        if b["t"] == "h" and b["level"] == 2:
            m = UNIT_RE.match("#" * b["level"] + " " + b["text"])
            if m:
                num_raw = (m.group(1) or "").strip()
                rest = (m.group(2) or "").strip()
                num = cn2num(num_raw) if num_raw else None
                if not num:
                    num = sum(1 for x in buckets if x["kind"] == "unit") + 1
                rng = UNIT_RANGE_RE.search(rest)
                a, z = (int(rng.group(1)), int(rng.group(2))) if rng else (None, None)
                cur = {"kind": "unit", "num": num, "title": rest, "range": (a, z),
                       "id": f"u-{num}", "blocks": [], "slices": []}
            else:
                sec_n += 1
                cur = {"kind": "section", "num": sec_n, "title": b["text"],
                       "range": (None, None), "id": f"sec-{sec_n}",
                       "blocks": [], "slices": []}
            buckets.append(cur)
            continue
        cur["blocks"].append(b)

    units = [b for b in buckets if b["kind"] == "unit"]
    sections = [b for b in buckets if b["kind"] == "section"]
    if not units:
        # 没有单元标题：整份文档当一段处理，仍然给锚点与导航
        head["kind"], head["num"], head["title"] = "unit", 1, title
        units, sections = [head], []

    cmap: dict[int, str] = {}
    for u in buckets:
        for b in u["blocks"]:
            if b["t"] != "slice":
                continue
            m = re.search(r"(\d{1,6})", b["chapter"])
            if not m:
                continue
            ch = int(m.group(1))
            sid = f"s{ch}"
            b["id"] = sid
            b["n"] = ch
            u["slices"].append({"id": sid, "n": ch, "label": b["label"]})
            cmap[ch] = sid

    for u in units:
        a, z = u["range"]
        if a and z:
            for n in range(a, z + 1):
                cmap.setdefault(n, u["id"])

    # --- 第二遍：渲染 ---
    ctx = RenderCtx(aliases, cmap, link_all=link_all)
    main_parts = []
    for bk in buckets:
        if bk["kind"] == "head" and not bk["blocks"]:
            continue
        ctx.reset_unit()
        title_txt = re.sub(r"[（(].*?[）)]\s*$", "", bk["title"]).strip() or bk["title"]
        rng = bk["range"]
        if bk["kind"] == "unit":
            rng_txt = f"（第 {rng[0]}–{rng[1]} 章）" if rng[0] else ""
            head_html = (f'<h2 class="unit-title">单元{int(bk["num"]):02d}'
                         f'<span class="unit-sep">｜</span>'
                         f'{_inline(title_txt, ctx, allow_char=False)}'
                         f'<span class="unit-range">{rng_txt}</span></h2>')
        elif bk["kind"] == "section":
            head_html = (f'<h2 class="unit-title section-title">'
                         f'{_inline(title_txt, ctx, allow_char=False)}</h2>')
        else:
            head_html = ""
        main_parts.append(f'<section class="unit {bk["kind"]}" id="{bk["id"]}" '
                          f'data-unit="{bk["id"]}" '
                          f'data-search="{_html.escape(title_txt)}">')
        main_parts.append(head_html)
        for b in bk["blocks"]:
            if b["t"] == "h" and b["level"] <= 1:
                continue
            if b["t"] == "h" and b["level"] == 2:
                main_parts.append(render_block(b, ctx))
                continue
            main_parts.append(render_block(b, ctx))
        main_parts.append("</section>")
    body_html = "\n".join(x for x in main_parts if x)

    # --- 导航 ---
    nav_units = []
    for u in units:
        rng = u["range"]
        title_txt = re.sub(r"[（(].*?[）)]\s*$", "", u["title"]).strip() or u["title"]
        nav_units.append({
            "id": u["id"],
            "num": int(u["num"]),
            "title": title_txt or u["title"],
            "range": list(rng),
            "search": _html.escape(f"单元{u['num']} {title_txt} {rng[0]} {rng[1]}"),
        })

    nav_sections = [{"id": s["id"], "title": s["title"],
                     "search": _html.escape(s["title"])} for s in sections]

    sliced = {}
    for u in units:
        for s in u["slices"]:
            sliced.setdefault(s["n"], s)
    nav_chapters = []
    if index_rows:
        for n in sorted(index_rows):
            anchor = cmap.get(n)
            if not anchor:
                continue
            nm = index_rows[n]
            nav_chapters.append({"n": n, "label": nm, "anchor": anchor,
                                 "search": _html.escape(f"第{n}章 {nm}")})
    else:
        for n in sorted(sliced):
            s = sliced[n]
            nav_chapters.append({"n": n, "label": s["label"], "anchor": s["id"],
                                 "search": _html.escape(f"第{n}章 {s['label']}")})

    nav_chars = [{"name": k, "search": _html.escape(f"{k} {' '.join(a for a, v in aliases.items() if v == k)}")}
                 for k in sorted(details)]

    key = progress_key or out_path.stem or digest_path.stem
    data = {"key": key, "chars": details,
            "unitCount": len(units), "chapterCount": len(nav_chapters)}

    page = PAGE_TEMPLATE
    page = page.replace("@@TITLE@@", _html.escape(title))
    page = page.replace("@@BODY@@", body_html)
    page = page.replace("@@NAV_UNITS@@", render_nav_units(nav_units))
    page = page.replace("@@NAV_EXTRA@@", render_nav_sections(nav_sections))
    page = page.replace("@@NAV_CHAPTERS@@", render_nav_chapters(nav_chapters))
    page = page.replace("@@NAV_CHARS@@", render_nav_chars(nav_chars))
    page = page.replace("@@DATA@@", json.dumps(data, ensure_ascii=False)
                        .replace("<", "\\u003c").replace("&", "\\u0026"))
    page = page.replace("@@STATS@@", _html.escape(
        f"{len(units)} 个单元 · {len(nav_chapters)} 章可跳转 · {len(nav_chars)} 位人物"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return {"units": len(units), "chapters": len(nav_chapters),
            "chars": len(nav_chars), "bytes": out_path.stat().st_size,
            "warnings": char_warnings(aliases, details, units, characters_path)}


def char_warnings(aliases, details, units, characters_path):
    warns = []
    if not characters_path or not Path(characters_path).is_file():
        warns.append("未找到《人物档案.md》：正文里不会生成人物链接。"
                     "用 --characters 指定，或先产出该文件。")
    elif not aliases:
        warns.append("《人物档案.md》里没解析到「别名归一表」：正文里不会生成人物链接。")
    elif not details:
        warns.append("《人物档案.md》有别名表但没有人物条目（### 人名）：点了人名没有详情可看。")
    if not any(u["slices"] for u in units):
        warns.append("速读稿里没找到「【原文·第N章 章名】」切片：章节速查只能跳到单元级。")
    return warns


def render_nav_units(nav_units):
    out = []
    for u in nav_units:
        rng = f'<span class="nav-range">第{u["range"][0]}–{u["range"][1]}章</span>' if u["range"][0] else ""
        out.append(f'<li class="nav-item" data-search="{u["search"]}">'
                   f'<a href="#{u["id"]}"><span class="nav-num">{u["num"]:02d}</span>'
                   f'<span class="nav-text">{_html.escape(u["title"])}</span>{rng}</a></li>')
    return "\n".join(out)


def render_nav_sections(nav_sections):
    if not nav_sections:
        return ""
    out = ['<div class="nav-group">总览 / 附录</div>', '<ul class="nav-list" id="sec-list">']
    for s in nav_sections:
        out.append(f'<li class="nav-item" data-search="{s["search"]}">'
                   f'<a href="#{s["id"]}"><span class="nav-num">·</span>'
                   f'<span class="nav-text">{_html.escape(s["title"])}</span></a></li>')
    out.append("</ul>")
    return "\n".join(out)


def render_nav_chapters(nav_chapters):
    out = []
    for c in nav_chapters:
        label = f'<span class="nav-text">{_html.escape(c["label"])}</span>' if c["label"] else ""
        out.append(f'<li class="nav-item" data-search="{c["search"]}">'
                   f'<a href="#{c["anchor"]}"><span class="nav-num">{c["n"]}</span>{label}</a></li>')
    return "\n".join(out)


def render_nav_chars(nav_chars):
    out = []
    for c in nav_chars:
        out.append(f'<li class="nav-item" data-search="{c["search"]}">'
                   f'<a class="nav-char" data-c="{_html.escape(c["name"])}" '
                   f'href="#char-panel">{_html.escape(c["name"])}</a></li>')
    return "\n".join(out)


# --------------------------------------------------------------------------
# 页面模板
# --------------------------------------------------------------------------
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
  --sidebar:308px; --radius:14px;
}
html[data-theme="dark"]{
  --bg:#17171a; --surface:#1f1f23; --ink:#e8e5e0; --ink-2:#a09a92; --line:#33323a;
  --accent:#c9a878; --accent-soft:#2a2620; --bridge:#9aa07c; --key:#d08a5c;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
}
@media (prefers-color-scheme: dark){
  html[data-theme="auto"]{
    --bg:#17171a; --surface:#1f1f23; --ink:#e8e5e0; --ink-2:#a09a92; --line:#33323a;
    --accent:#c9a878; --accent-soft:#2a2620; --bridge:#9aa07c; --key:#d08a5c;
  }
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion: reduce){html{scroll-behavior:auto}}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Songti SC","Noto Serif SC",Georgia,"PingFang SC","Microsoft YaHei",serif;
  font-size:17px; line-height:1.85; -webkit-text-size-adjust:100%;
}
a{color:var(--accent); text-decoration:none}
/* ---------- 顶栏 ---------- */
#topbar{
  position:sticky; top:0; z-index:40; display:flex; align-items:center; gap:10px;
  padding:10px 14px; background:color-mix(in srgb, var(--bg) 86%, transparent);
  backdrop-filter:blur(10px); border-bottom:1px solid var(--line);
}
#topbar h1{font-size:15px; margin:0; font-weight:600; flex:1; min-width:0;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
#topbar .stats{font-size:12px; color:var(--ink-2); font-family:system-ui,sans-serif;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.iconbtn{
  border:1px solid var(--line); background:var(--surface); color:var(--ink);
  border-radius:10px; padding:6px 10px; font-size:14px; cursor:pointer; font-family:system-ui,sans-serif;
}
.iconbtn:hover{border-color:var(--accent)}
#menubtn{display:none}
#progress-rail{position:fixed; top:0; left:0; right:0; height:3px; z-index:60; background:transparent}
#progress-bar{height:100%; width:0; background:var(--accent); transition:width .12s linear}
/* ---------- 布局 ---------- */
#layout{display:flex; align-items:flex-start}
#sidebar{
  position:sticky; top:52px; align-self:flex-start; width:var(--sidebar); flex:0 0 var(--sidebar);
  height:calc(100vh - 52px); overflow-y:auto; overscroll-behavior:contain;
  padding:14px 12px 40px; border-right:1px solid var(--line);
  background:var(--surface);
}
#main{flex:1; min-width:0; padding:26px 28px 120px; max-width:900px; margin:0 auto}
/* ---------- 侧栏 ---------- */
.nav-group{
  font-family:system-ui,sans-serif; font-size:11px; letter-spacing:.12em; color:var(--ink-2);
  margin:16px 6px 6px; text-transform:uppercase;
}
.nav-list{list-style:none; margin:0; padding:0}
.nav-item a{
  display:flex; gap:8px; align-items:baseline; padding:7px 10px; border-radius:9px;
  color:var(--ink); font-size:14px; line-height:1.5; font-family:system-ui,"PingFang SC",sans-serif;
}
.nav-item a:hover{background:var(--accent-soft)}
.nav-item.active a{background:var(--accent-soft); color:var(--accent); font-weight:600}
.nav-num{color:var(--ink-2); font-size:11px; min-width:2.2em; font-variant-numeric:tabular-nums}
.nav-text{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.nav-range{font-size:11px; color:var(--ink-2)}
#chap-list{max-height:340px; overflow-y:auto}
#search{width:100%; padding:9px 12px; border:1px solid var(--line); border-radius:10px;
  background:var(--bg); color:var(--ink); font-size:14px; font-family:system-ui,sans-serif}
#search:focus{outline:2px solid var(--accent-soft); border-color:var(--accent)}
#noresult{display:none; color:var(--ink-2); font-size:13px; padding:10px; font-family:system-ui,sans-serif}
.sidebar-foot{margin-top:18px; padding:0 6px; font-size:12px; color:var(--ink-2); font-family:system-ui,sans-serif}
.sidebar-foot button{background:none; border:none; color:var(--ink-2); text-decoration:underline;
  cursor:pointer; font-size:12px; padding:0}
/* ---------- 正文 ---------- */
.unit{padding-top:8px; margin-bottom:44px; scroll-margin-top:70px}
.unit-title{
  font-size:26px; margin:34px 0 16px; padding-bottom:10px; border-bottom:1px solid var(--line);
  scroll-margin-top:70px; letter-spacing:.02em;
}
.unit-sep{color:var(--accent); margin:0 2px}
.unit-range{display:block; font-size:12px; color:var(--ink-2); margin-top:4px;
  font-family:system-ui,sans-serif; letter-spacing:.08em}
.h3{font-size:19px; margin:26px 0 10px}
.h4{font-size:16px; margin:20px 0 8px; color:var(--ink-2)}
p{margin:12px 0}
ul,ol{margin:12px 0; padding-left:1.4em}
li{margin:6px 0}
strong{color:var(--ink); font-weight:700}
code{background:var(--accent-soft); padding:1px 5px; border-radius:5px; font-size:.9em;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
hr.rule{border:none; border-top:1px dashed var(--line); margin:34px 0}
.callout{border-left:3px solid var(--line); padding:2px 0 2px 14px; margin:16px 0}
.callout-bridge{border-color:var(--bridge)}
.callout-key{border-color:var(--key)}
.callout-end{border-color:var(--accent); background:var(--accent-soft); border-radius:0 10px 10px 0;
  padding:10px 14px}
.slice{
  margin:20px 0; padding:14px 16px; background:var(--surface); border:1px solid var(--line);
  border-radius:var(--radius); box-shadow:var(--shadow); scroll-margin-top:70px;
}
.slice figcaption{font-size:12px; color:var(--accent); letter-spacing:.06em; margin-bottom:8px;
  font-family:system-ui,sans-serif}
.slice blockquote{margin:0; padding:0; color:var(--ink); font-size:16.5px; line-height:1.95}
.slice blockquote::before{content:"「"; color:var(--ink-2)}
.slice blockquote::after{content:"」"; color:var(--ink-2)}
.quote{margin:16px 0; padding:10px 16px; border-left:3px solid var(--line); color:var(--ink-2)}
.table-wrap{overflow-x:auto; margin:16px 0}
table{border-collapse:collapse; width:100%; font-size:14.5px; font-family:system-ui,sans-serif;
  background:var(--surface)}
th,td{border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top}
th{background:var(--accent-soft); font-weight:600; white-space:nowrap}
.chip{
  display:inline-block; font-size:12px; font-family:system-ui,sans-serif; padding:1px 7px;
  border:1px solid var(--line); border-radius:8px; color:var(--accent); background:var(--surface);
  white-space:nowrap;
}
.chip:hover{background:var(--accent-soft)}
.char{
  color:var(--accent); border-bottom:1px dotted var(--accent); cursor:pointer;
}
.char:hover{background:var(--accent-soft)}
.flash{animation:flash 1.4s ease}
@keyframes flash{0%{background:var(--accent-soft)}100%{background:transparent}}
/* ---------- 人物抽屉 ---------- */
#char-panel{
  position:fixed; top:0; right:0; height:100%; width:min(460px,92vw); z-index:70;
  background:var(--surface); border-left:1px solid var(--line); box-shadow:var(--shadow);
  transform:translateX(102%); transition:transform .22s ease; display:flex; flex-direction:column;
}
#char-panel.open{transform:none}
#char-panel header{display:flex; align-items:center; gap:10px; padding:14px 16px;
  border-bottom:1px solid var(--line)}
#char-panel h3{margin:0; font-size:18px; flex:1}
#char-body{padding:16px; overflow-y:auto; font-size:16px; line-height:1.9}
#backdrop{position:fixed; inset:0; background:rgba(0,0,0,.32); z-index:65; opacity:0;
  pointer-events:none; transition:opacity .2s}
#backdrop.show{opacity:1; pointer-events:auto}
/* ---------- 进度浮标 ---------- */
#resume{
  position:fixed; left:16px; bottom:16px; z-index:50; max-width:min(420px,92vw);
  background:var(--surface); border:1px solid var(--line); border-radius:12px;
  box-shadow:var(--shadow); padding:10px 12px; font-size:13px; display:none;
  font-family:system-ui,sans-serif; align-items:center; gap:10px; flex-wrap:wrap;
}
#resume.show{display:flex}
#resume .pct{color:var(--accent); font-variant-numeric:tabular-nums}
#resume button{border:1px solid var(--line); background:var(--bg); color:var(--ink);
  border-radius:8px; padding:4px 9px; cursor:pointer; font-size:12px}
#resume button:hover{border-color:var(--accent)}
#totop{position:fixed; right:16px; bottom:16px; z-index:45; display:none}
#totop.show{display:block}
/* ---------- 窄屏 ---------- */
@media (max-width:980px){
  body{font-size:16.5px}
  #menubtn{display:inline-block}
  #topbar .stats{display:none}
  #sidebar{
    position:fixed; top:0; left:0; height:100%; z-index:66; transform:translateX(-102%);
    transition:transform .22s ease; box-shadow:var(--shadow); border-right:1px solid var(--line);
    padding-top:16px;
  }
  #sidebar.open{transform:none}
  #main{padding:18px 16px 130px; max-width:100%}
  .unit-title{font-size:21px}
  #char-panel{width:100%; height:78vh; top:auto; bottom:0; right:0; border-left:none;
    border-top:1px solid var(--line); border-radius:16px 16px 0 0; transform:translateY(102%)}
  #char-panel.open{transform:none}
  .slice{padding:12px}
  #resume{left:12px; right:12px; bottom:12px; max-width:none}
}
@media print{
  #sidebar,#topbar,#resume,#totop,#char-panel,#backdrop{display:none!important}
  #main{max-width:100%; padding:0}
}
</style>
</head>
<body>
<div id="progress-rail"><div id="progress-bar"></div></div>

<header id="topbar">
  <button class="iconbtn" id="menubtn" aria-label="目录">☰</button>
  <h1>@@TITLE@@</h1>
  <span class="stats">@@STATS@@</span>
  <button class="iconbtn" id="themebtn" title="切换深浅色">◐</button>
</header>

<div id="layout">
  <aside id="sidebar" aria-label="目录">
    <input id="search" type="search" placeholder="筛选单元 / 章节 / 人物" autocomplete="off">
    <div id="noresult">没有匹配项</div>
    <div class="nav-group">单元</div>
    <ul class="nav-list" id="unit-list">@@NAV_UNITS@@</ul>
@@NAV_EXTRA@@
    <div class="nav-group">章节速查</div>
    <ul class="nav-list" id="chap-list">@@NAV_CHAPTERS@@</ul>
    <div class="nav-group">人物</div>
    <ul class="nav-list" id="char-list">@@NAV_CHARS@@</ul>
    <div class="sidebar-foot">
      <div>进度自动保存在本机浏览器，刷新后回到原处。</div>
      <button id="clear-progress">清除阅读进度</button>
    </div>
  </aside>

  <main id="main">
@@BODY@@
  </main>
</div>

<div id="resume" role="status">
  <span>已回到上次位置 <span class="pct" id="resume-pct">0%</span></span>
  <button id="resume-last">跳到上次位置</button>
  <button id="resume-top">回到顶部</button>
  <button id="resume-close" title="关闭">✕</button>
</div>
<button class="iconbtn" id="totop" title="回到顶部">↑</button>

<div id="backdrop"></div>
<aside id="char-panel" aria-label="人物详情">
  <header><h3 id="char-name">人物</h3><button class="iconbtn" id="char-close">✕</button></header>
  <div id="char-body"></div>
</aside>

<script id="page-data" type="application/json">@@DATA@@</script>
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('page-data').textContent);
  var KEY = 'nfr:progress:' + DATA.key;
  var THEME_KEY = 'nfr:theme';
  var bar = document.getElementById('progress-bar');
  var sidebar = document.getElementById('sidebar');
  var backdrop = document.getElementById('backdrop');
  var panel = document.getElementById('char-panel');
  var chars = DATA.chars || {};

  /* ---------- 主题 ---------- */
  var savedTheme = null;
  try { savedTheme = localStorage.getItem(THEME_KEY); } catch(e){}
  if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);
  document.getElementById('themebtn').onclick = function(){
    var cur = document.documentElement.getAttribute('data-theme');
    if (cur === 'auto') cur = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem(THEME_KEY, next); } catch(e){}
  };

  /* ---------- 抽屉 ---------- */
  function closeSidebar(){ sidebar.classList.remove('open'); backdrop.classList.remove('show'); }
  function closePanel(){ panel.classList.remove('open'); backdrop.classList.remove('show'); }
  document.getElementById('menubtn').onclick = function(){
    sidebar.classList.toggle('open');
    backdrop.classList.toggle('show', sidebar.classList.contains('open'));
    panel.classList.remove('open');
  };
  backdrop.onclick = function(){ closeSidebar(); closePanel(); };
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape'){ closeSidebar(); closePanel(); }
  });

  /* ---------- 人物详情 ---------- */
  function openChar(name){
    if (!chars[name]) return;
    document.getElementById('char-name').textContent = name;
    document.getElementById('char-body').innerHTML = chars[name];
    panel.classList.add('open');
    if (matchMedia('(max-width:980px)').matches) backdrop.classList.add('show');
    var m = history.state;
    history.replaceState({c: name}, '');
    panel.querySelector('#char-body').scrollTop = 0;
  }
  document.getElementById('char-close').onclick = closePanel;
  document.addEventListener('click', function(e){
    var t = e.target.closest('[data-c]');
    if (!t) return;
    e.preventDefault();
    openChar(t.getAttribute('data-c'));
    if (matchMedia('(max-width:980px)').matches) closeSidebar();
  });

  /* ---------- 侧栏筛选 ---------- */
  var searchEl = document.getElementById('search');
  var noresult = document.getElementById('noresult');
  searchEl.addEventListener('input', function(){
    var q = searchEl.value.trim().toLowerCase();
    var items = document.querySelectorAll('.nav-item');
    var hit = 0;
    items.forEach(function(li){
      var hay = (li.getAttribute('data-search') || '').toLowerCase() + li.textContent.toLowerCase();
      var ok = !q || hay.indexOf(q) >= 0;
      li.style.display = ok ? '' : 'none';
      if (ok) hit++;
    });
    noresult.style.display = (q && hit === 0) ? 'block' : 'none';
  });

  /* ---------- 章节跳转后高亮 ---------- */
  document.addEventListener('click', function(e){
    var a = e.target.closest('a[href^="#"]');
    if (!a) return;
    var el = document.querySelector(a.getAttribute('href'));
    if (!el) return;
    el.classList.add('flash');
    setTimeout(function(){ el.classList.remove('flash'); }, 1400);
    if (matchMedia('(max-width:980px)').matches) closeSidebar();
  });

  /* ---------- 进度：保存 / 恢复 ---------- */
  var units = Array.prototype.slice.call(document.querySelectorAll('.unit'));
  var currentUnit = units.length ? units[0].id : '';
  var resumeBox = document.getElementById('resume');
  var resumePct = document.getElementById('resume-pct');
  var lastSaved = 0;

  function pct(){
    var h = document.documentElement.scrollHeight - window.innerHeight;
    return h > 0 ? Math.min(100, Math.max(0, Math.round(window.scrollY / h * 100))) : 100;
  }
  function read(){
    try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch(e){ return null; }
  }
  function save(){
    try {
      localStorage.setItem(KEY, JSON.stringify({
        y: Math.round(window.scrollY), id: currentUnit, pct: pct(), t: Date.now()
      }));
    } catch(e){}
  }
  function applyProgress(){
    bar.style.width = pct() + '%';
    var top = window.scrollY + 90;
    for (var i = 0; i < units.length; i++){
      var el = units[i];
      if (el.offsetTop <= top) currentUnit = el.id; else break;
    }
    document.querySelectorAll('#unit-list .nav-item').forEach(function(li){
      li.classList.toggle('active', li.querySelector('a').getAttribute('href') === '#' + currentUnit);
    });
    document.getElementById('totop').classList.toggle('show', window.scrollY > 900);
  }

  var tick = null;
  window.addEventListener('scroll', function(){
    if (tick) return;
    tick = requestAnimationFrame(function(){ tick = null; applyProgress(); });
    if (lastSaved && Date.now() - lastSaved < 700) return;
    lastSaved = Date.now();
    save();
  }, {passive: true});
  window.addEventListener('beforeunload', save);
  document.addEventListener('visibilitychange', function(){ if (document.hidden) save(); });

  function showResume(saved){
    resumePct.textContent = saved.pct + '%';
    resumeBox.classList.add('show');
  }
  document.getElementById('resume-last').onclick = function(){
    var s = read();
    if (s) window.scrollTo({top: s.y, behavior: 'smooth'});
  };
  document.getElementById('resume-top').onclick = function(){
    window.scrollTo({top: 0, behavior: 'smooth'});
    resumeBox.classList.remove('show');
  };
  document.getElementById('resume-close').onclick = function(){
    resumeBox.classList.remove('show');
  };
  document.getElementById('totop').onclick = function(){ window.scrollTo({top: 0, behavior: 'smooth'}); };
  document.getElementById('clear-progress').onclick = function(){
    try { localStorage.removeItem(KEY); } catch(e){}
    resumeBox.classList.remove('show');
    this.textContent = '已清除';
  };

  var saved = read();
  if (saved && saved.y > 40 && saved.pct < 100){
    requestAnimationFrame(function(){
      window.scrollTo(0, saved.y);
      setTimeout(function(){ window.scrollTo(0, saved.y); showResume(saved); applyProgress(); }, 60);
    });
  }
  window.addEventListener('load', function(){
    var s = read();
    if (s && s.y > 40 && Math.abs(window.scrollY - s.y) > 200) window.scrollTo(0, s.y);
    applyProgress();
  });
  applyProgress();
})();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="把速读稿 markdown 渲染成单文件 HTML 阅读器")
    ap.add_argument("--digest", required=True, help="速读稿.md（或衔接包.md）")
    ap.add_argument("--out", help="输出 html，默认与 digest 同名的 .html")
    ap.add_argument("--characters", help="人物档案.md（默认在 digest 同目录/上级目录自动查找）")
    ap.add_argument("--index", help="chapters.tsv（提供后可跳转到全部分章）")
    ap.add_argument("--title", help="页面标题，默认取速读稿 H1 或文件名")
    ap.add_argument("--progress-key", help="localStorage 进度键，默认用输出文件名")
    ap.add_argument("--link-all", action="store_true", help="每个人名出现都变成链接（默认只链每单元首次）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    digest_path = Path(args.digest).expanduser().resolve()
    if not digest_path.is_file():
        raise SystemExit(f"找不到速读稿：{digest_path}")
    base = digest_path.parent

    def find(name):
        for p in (Path(name), base / name, base.parent / name):
            if p.is_file():
                return p
        return None

    characters_path = Path(args.characters).expanduser().resolve() if args.characters else (
        find("人物档案.md"))
    index_path = Path(args.index).expanduser().resolve() if args.index else (
        find("chapters.tsv") or find("章节索引.tsv"))

    title = args.title
    if not title:
        for line in digest_path.read_text(encoding="utf-8").split("\n")[:20]:
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break
    title = title or digest_path.stem

    out_path = Path(args.out).expanduser().resolve() if args.out else digest_path.with_suffix(".html")

    info = build(digest_path, characters_path, index_path, title, out_path,
                 link_all=args.link_all, progress_key=args.progress_key)

    if args.json:
        print(json.dumps({"html": str(out_path), "title": title,
                          "characters": str(characters_path) if characters_path else None,
                          "index": str(index_path) if index_path else None, **info},
                         ensure_ascii=False, indent=2))
    else:
        print(f"✅ HTML 已生成：{out_path}（{info['bytes'] / 1024:.0f} KB）")
        print(f"   单元 {info['units']} · 可跳转章节 {info['chapters']} · 人物 {info['chars']}")
        for w in info["warnings"]:
            print(f"   ⚠️ {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
