#!/usr/bin/env python3
"""把一整本小说切分成章节，并生成「章节索引」。

用法示例
--------
# 1) 切分整本 txt，同时把每章单独落盘到 <outdir>/原文/
python3 split_chapters.py --input 全书.txt --outdir 阅读工作区/书名 --split

# 2) 用户是分多次粘贴、已经手动存成 原文/第0001章.txt ...
python3 split_chapters.py --from-dir 阅读工作区/书名/原文 --outdir 阅读工作区/书名

# 3) 只看看能不能识别出章节（不写任何文件）
python3 split_chapters.py --input 全书.txt --dry-run

产物
----
<outdir>/章节索引.md     给人看的索引（含卷/章标题、字数、偏移）
<outdir>/chapters.tsv    给脚本看的索引：idx、title、chars、start、end、file
<outdir>/原文/第0001章_标题.txt   （仅 --split 时）

只有标准库依赖。识别不到章节时会退化为固定字数分块，并在 stderr 明确告警。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------- 编码处理

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16", "big5")


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    sample = text[:200_000]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(len(sample), 1)


def read_text_best(path: Path, forced: str | None = None) -> tuple[str, str]:
    """按候选编码逐个尝试，返回 (文本, 实际编码)。以「中文占比 + 乱码率」挑最像的那个。"""
    raw = path.read_bytes()
    candidates = [forced] if forced else list(ENCODINGS)
    best: tuple[float, str, str] | None = None
    for enc in candidates:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        score = _cjk_ratio(text) - text.count("\ufffd") / max(len(text), 1) * 10
        if best is None or score > best[0]:
            best = (score, text, enc)
        if enc in ("utf-8-sig", "utf-8") and score > 0.2:
            break
    if best is None:
        raise SystemExit(f"无法解码文件（试过 {candidates}）：{path}")
    return best[1], best[2]


# ---------------------------------------------------------------- 清洗

_AD_LINE = re.compile(
    r"(请收藏本站|更新最快|最快更新|手机版阅读|手机用户请|笔趣阁|www\.|https?://|"
    r"天才一秒记住|一秒记住|本章未完|内容严重缺失|章节错误|加入书签|投推荐票|"
    r"求收藏|求订阅|求月票|读者群|QQ群|微信公众号|app下载|APP下载|"
    r"^\s*[（(]?本章完[）)]?\s*$|^\s*[（(]?未完待续[）)]?\s*$)"
)

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def cn2num(text: str) -> int | None:
    """中文数字转阿拉伯数字（支持 十二 / 一百零三 / 两千 / 1）；失败返回 None。"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    if not text or any(ch not in _CN_DIGITS and ch not in _CN_UNITS for ch in text):
        return None
    total = section = number = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        else:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
    return total + section + number


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if _AD_LINE.search(stripped):
            continue
        lines.append(line.rstrip())
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# ---------------------------------------------------------------- 章节识别

SPECIAL_HEADINGS = r"序章|序言|序|楔子|引子|前言|尾声|终章|结局|后记|番外|外传|附录|完本感言"

_CHAPTER_RES: list[re.Pattern[str]] = [
    # 第12章 / 第十二章 / 【第12章】 / 第12回 标题
    re.compile(
        r"^(?:[【\[]\s*)?第\s*([0-9〇零一二三四五六七八九十百千万两]{1,12})\s*"
        r"([章回节話话])\s*(?:[】\]])?\s*(.{0,60})$"
    ),
    # Chapter 12 / CHAPTER 12 / Ch.12
    re.compile(r"^(?:Chapter|CHAPTER|Ch\.|ch\.)\s*([0-9]{1,5})\b\s*(.{0,60})$"),
    # 001. 标题  /  12、标题  /  12 标题
    re.compile(r"^([0-9]{1,4})\s*[.、,，:：]?\s+(.{0,60})$"),
    # 序章 / 楔子 / 尾声 ...
    re.compile(rf"^({SPECIAL_HEADINGS})\s*[·:：]?\s*(.{{0,40}})$"),
]

_VOLUME_RE = re.compile(
    r"^(?:[【\[]\s*)?第\s*([0-9〇零一二三四五六七八九十百千万两]{1,12})\s*"
    r"([卷部篇])\s*(?:[】\]])?\s*(.{0,60})$"
)

_MAX_TITLE_LEN = 50


def _is_heading(line: str):
    """返回 (type, number, title) 或 None。type ∈ {chapter, special, volume}。"""
    s = line.strip()
    if not s or len(s) > _MAX_TITLE_LEN:
        return None
    if "。" in s:  # 正文句子常以句号结尾，章节标题基本不会
        return None

    m = _VOLUME_RE.match(s)
    if m:
        num = cn2num(m.group(1))
        title = f"第{m.group(1)}{m.group(2)}卷 {m.group(3).strip()}".strip()
        if num is not None and not m.group(3).strip():
            title = f"第{m.group(1)}{m.group(2)}"
        return ("volume", num, title)

    for i, rx in enumerate(_CHAPTER_RES):
        m = rx.match(s)
        if not m:
            continue
        if i == 0:
            num = cn2num(m.group(1))
            title = f"第{m.group(1)}{m.group(2)} {m.group(3).strip()}".strip()
            return ("chapter", num, title)
        if i == 1:
            num = int(m.group(1))
            title = f"Chapter {num} {m.group(2).strip()}".strip()
            return ("chapter", num, title)
        if i == 2:
            # 纯数字行极易误判：要求这行确实是「编号 + 标题」的样子
            tail = m.group(2).strip()
            if not tail or len(tail) < 1:
                continue
            return ("chapter", int(m.group(1)), f"{m.group(1)}. {tail}")
        num = None
        return ("special", num, s)
    return None


def find_chapters(text: str, pattern: str | None = None):
    """扫描全文，返回 (entries, volumes)。entries = [dict(idx,title,start,end)]"""
    custom = re.compile(pattern) if pattern else None
    lines = text.split("\n")
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1  # +1 for '\n'

    entries: list[dict] = []
    volumes: list[tuple[int, str]] = []  # (offset, title)
    current_volume = ""
    counter = 0
    for i, line in enumerate(lines):
        if custom:
            m = custom.match(line.strip())
            if m:
                counter += 1
                title = line.strip()
                if m.groups():
                    title = next((g for g in m.groups() if g), title).strip() or title
                entries.append({"idx": counter, "title": title,
                                "start": offsets[i], "end": len(text),
                                "volume": current_volume})
            continue
        hit = _is_heading(line)
        if not hit:
            continue
        kind, num, title = hit
        if kind == "volume":
            current_volume = title
            volumes.append((offsets[i], title))
            continue
        counter += 1
        entries.append({"idx": counter, "title": title,
                        "start": offsets[i], "end": len(text),
                        "volume": current_volume})

    for j, e in enumerate(entries):
        if j + 1 < len(entries):
            e["end"] = entries[j + 1]["start"]
        e["chars"] = len(text[e["start"]:e["end"]].strip())
    return entries, volumes


def fallback_chunks(text: str, size: int = 8000):
    entries = []
    for i, start in enumerate(range(0, len(text), size), 1):
        end = min(start + size, len(text))
        entries.append({"idx": i, "title": f"分块{i}（自动，非真实章节）",
                        "start": start, "end": end, "chars": end - start,
                        "volume": ""})
    return entries


# ---------------------------------------------------------------- 输出

def safe_name(title: str, limit: int = 40) -> str:
    # 去掉标题里自带的「第12章」前缀，避免文件名出现「第0001章_第1章 楔子.txt」
    title = re.sub(r"^第\s*[0-9〇零一二三四五六七八九十百千万两]{1,12}\s*[章回节話话卷部篇]\s*", "", title)
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", title).strip(" ._")
    return (name or "未命名")[:limit]


def write_index(outdir: Path, book: str, entries: list[dict],
                source_desc: str, volumes: list[tuple[int, str]] | None = None):
    outdir.mkdir(parents=True, exist_ok=True)
    tsv = outdir / "chapters.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("idx\ttitle\tchars\tstart\tend\tfile\tvolume\n")
        for e in entries:
            f.write(f"{e['idx']}\t{e['title']}\t{e['chars']}\t{e['start']}\t"
                    f"{e['end']}\t{e.get('file', '')}\t{e.get('volume', '')}\n")

    total = sum(e["chars"] for e in entries)
    md = outdir / "章节索引.md"
    with md.open("w", encoding="utf-8") as f:
        f.write(f"# 《{book}》章节索引\n\n")
        f.write(f"- 原文来源：{source_desc}\n")
        f.write(f"- 章节数：{len(entries)}\n")
        f.write(f"- 总字数（含标题）：约 {total:,}\n")
        if volumes:
            f.write(f"- 卷/部：{'、'.join(t for _, t in volumes)}\n")
        f.write("\n> 本文件是**切片定位的唯一坐标源**。引用、摘录原文时一律以这里的章号为准，"
                "不要凭记忆编号。\n\n")
        f.write("| 章号 | 标题 | 字数 |\n|---:|---|---:|\n")
        for e in entries:
            vol = f"{e.get('volume')} " if e.get("volume") else ""
            f.write(f"| {e['idx']} | {vol}{e['title']} | {e['chars']:,} |\n")
    return md, tsv


def _rel(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def split_files(outdir: Path, text: str, entries: list[dict]) -> None:
    src_dir = outdir / "原文"
    src_dir.mkdir(parents=True, exist_ok=True)
    width = max(4, len(str(len(entries))))
    for e in entries:
        body = text[e["start"]:e["end"]].strip()
        fn = f"第{str(e['idx']).zfill(width)}章_{safe_name(e['title'])}.txt"
        p = src_dir / fn
        p.write_text(body, encoding="utf-8")
        e["file"] = _rel(p, outdir)


def index_existing_dir(src_dir: Path, outdir: Path):
    files = sorted(
        [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".md")],
        key=lambda p: (int(re.match(r"(\d+)", p.name).group(1))
                       if re.match(r"(\d+)", p.name) else 10**9, p.name),
    )
    entries = []
    for i, p in enumerate(files, 1):
        try:
            t = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            t, _ = read_text_best(p)
        first_line = next((ln.strip() for ln in t.split("\n") if ln.strip()), p.stem)
        entries.append({"idx": i, "title": first_line[:60], "start": 0,
                        "end": len(t), "chars": len(re.sub(r"\s", "", t)),
                        "file": _rel(p, outdir), "volume": ""})
    return entries


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="小说章节切分与索引")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="整本小说文件（txt/md）")
    src.add_argument("--from-dir", help="已经按章分好文件的目录")
    ap.add_argument("--outdir", help="工作区目录（默认：输入文件所在目录）")
    ap.add_argument("--book", default="", help="书名（默认取文件名）")
    ap.add_argument("--split", action="store_true", help="把每章写入 <outdir>/原文/")
    ap.add_argument("--index-only", action="store_true", help="只写索引，不写 原文/")
    ap.add_argument("--pattern", help="自定义章节标题正则（作用于 strip 后的整行）")
    ap.add_argument("--encoding", help="强制编码，如 gb18030")
    ap.add_argument("--chunk-size", type=int, default=8000,
                    help="识别不到章节时每块字数（默认 8000）")
    ap.add_argument("--force-chunks", action="store_true",
                    help="跳过识别，直接按 chunk-size 分块")
    ap.add_argument("--dry-run", action="store_true", help="只打印识别结果，不写文件")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    args = ap.parse_args()

    if args.from_dir:
        src_dir = Path(args.from_dir).expanduser().resolve()
        if not src_dir.is_dir():
            raise SystemExit(f"目录不存在：{src_dir}")
        outdir = Path(args.outdir).expanduser().resolve() if args.outdir else src_dir.parent
        book = args.book or src_dir.parent.name
        entries = index_existing_dir(src_dir, outdir)
        md, tsv = write_index(outdir, book, entries, f"目录 {src_dir}")
        summary = {"book": book, "chapters": len(entries), "total_chars":
                   sum(e["chars"] for e in entries), "fallback": False,
                   "index": str(md), "tsv": str(tsv), "files": [e["file"] for e in entries]}
        print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json
              else f"已索引 {len(entries)} 章 → {md}")
        return 0

    path = Path(args.input).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")
    text, enc = read_text_best(path, args.encoding)
    text = clean_text(text)
    if args.book:
        book = args.book
    else:
        book = re.sub(r"\.(txt|md)$", "", path.name, flags=re.I)

    fallback = False
    if args.force_chunks:
        entries, volumes = fallback_chunks(text, args.chunk_size), []
        fallback = True
    else:
        entries, volumes = find_chapters(text, args.pattern)
        if len(entries) < 2:
            print("[warn] 没有识别出章节标题，退化为固定字数分块。"
                  "如果原文标题格式特殊，请用 --pattern 指定正则。", file=sys.stderr)
            entries, volumes = fallback_chunks(text, args.chunk_size), []
            fallback = True

    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else path.parent

    if args.dry_run:
        print(f"编码={enc} 字符数={len(text):,} 识别到 {len(entries)} 章"
              f"{'（分块退化）' if fallback else ''}")
        for e in entries[:20]:
            print(f"  #{e['idx']:>4}  {e['chars']:>7,} 字  {e['title']}")
        if len(entries) > 20:
            print(f"  ... 其余 {len(entries) - 20} 章略")
        odd = [e for e in entries if e["chars"] < 200]
        if odd:
            print(f"[warn] 有 {len(odd)} 章字数 < 200（可能是误判的标题行）："
                  f"{[e['title'] for e in odd[:5]]}", file=sys.stderr)
        return 0

    if args.split and not args.index_only:
        split_files(outdir, text, entries)
    md, tsv = write_index(outdir, book, entries,
                          f"{path}（编码 {enc}{'，分块退化' if fallback else ''}）", volumes)

    summary = {
        "book": book, "chapters": len(entries),
        "total_chars": sum(e["chars"] for e in entries),
        "encoding": enc, "fallback": fallback,
        "index": str(md), "tsv": str(tsv), "outdir": str(outdir),
        "split": bool(args.split and not args.index_only),
        "first": entries[0]["title"] if entries else None,
        "last": entries[-1]["title"] if entries else None,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"《{book}》共 {len(entries)} 章，约 {summary['total_chars']:,} 字 → {md}")
        if summary["split"]:
            print(f"原文已按章落盘：{outdir / '原文'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
