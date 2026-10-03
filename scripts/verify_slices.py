#!/usr/bin/env python3
"""校验「原文切片」是否真的逐字来自原文，并可汇总成 原文切片合集。

为什么需要它：模型很擅长把原文「复述」得很像原文，但它不是原文。
一旦切片被润色或凭记忆重写，用户按切片去引用、去对照就会踩坑。
这个脚本把"是不是逐字"变成可验证的事实，而不是靠自觉。

切片在 markdown 里的写法（速读稿/衔接包通用）：

    【原文·第12章 密室对峙】
    > 「……原文第一段……」
    > 「……原文第二段……」

    （可只写 【原文·第12章】；一个标记下可以有多行 > 引用）

用法
----
python3 verify_slices.py --digest 速读稿.md --source 原文/            # 目录或单个整本文件
python3 verify_slices.py --digest 速读稿.md --source 全书.txt --index chapters.tsv
python3 verify_slices.py --digest 速读稿.md --source 原文/ --collect 原文切片.md
python3 verify_slices.py --digest 速读稿.md --source 原文/ --json

退出码：全部通过 0；存在 FAIL 1；没找到任何切片 2。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

MARKER_RE = re.compile(r"^\s*(?:\*\*)?\s*【原文\s*[·・:：]?\s*([^】]*)】\s*(?:\*\*)?\s*(.*)$")
QUOTE_STRIP = "「」『』“”\"'‘’《》〈〉 \t"
ELLIPSIS_SPLIT = re.compile(r"(?:\.{2,}|…+|﹍+|——+|\*{2,})")


def normalize(text: str) -> str:
    """用于比对：全角半角归一、去空白、去引号、去省略号，只留下实打实的字。"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", "", text)
    text = text.translate(str.maketrans("", "", "「」『』“”\"'‘’《》〈〉"))
    return text


def parse_slices(digest_text: str):
    """返回 [{chapter, label, raw, text, line}]"""
    out = []
    lines = digest_text.split("\n")
    i = 0
    while i < len(lines):
        m = MARKER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        chapter, rest = m.group(1).strip(), m.group(2).strip()
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        body = []
        while j < len(lines) and lines[j].lstrip().startswith(">"):
            body.append(re.sub(r"^\s*>\s?", "", lines[j]).rstrip())
            j += 1
        raw = "\n".join(body).strip()
        if raw:
            out.append({
                "chapter": chapter,
                "label": rest,
                "raw": raw,
                "text": raw.strip(QUOTE_STRIP),
                "line": i + 1,
            })
        i = j if j > i else i + 1
    return out


def load_source(source: Path):
    """返回 [(name, text, chapter_hint)]。chapter_hint 从「第0002章_xxx.txt」这类文件名里取。"""
    if source.is_dir():
        files = sorted(
            [p for p in source.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".md")],
            key=lambda p: (int(re.match(r"(\d+)", p.name).group(1))
                           if re.match(r"(\d+)", p.name) else 10**9, p.name),
        )
        if not files:
            raise SystemExit(f"目录里没有 txt/md：{source}")
        chunks = []
        for p in files:
            try:
                t = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                t = p.read_bytes().decode("gb18030", errors="replace")
            m = re.match(r"\D*?(\d{1,6})", p.name)
            hint = int(m.group(1)) if m else None
            chunks.append((p.name, t, hint))
        return chunks
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = source.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise SystemExit(f"无法解码：{source}")
    return [(source.name, text, None)]


def load_index(tsv: Path):
    rows = []
    for line in tsv.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 5:
            try:
                rows.append({"idx": int(parts[0]), "title": parts[1],
                             "start": int(parts[3]), "end": int(parts[4])})
            except ValueError:
                continue
    return rows


def chapter_of(offset: int, rows) -> str:
    for r in rows:
        if r["start"] <= offset < r["end"]:
            return f"第{r['idx']}章 {r['title']}"
    return "?"


def verify(slices, chunks, index_rows):
    """把 source 归一化后做 in-order 搜索。"""
    norm_chunks = []
    for name, text, hint in chunks:
        norm_chunks.append((name, normalize(text), hint))
    flat = "".join(t for _, t, _ in norm_chunks)

    def locate(seg: str):
        pos = flat.find(seg)
        if pos < 0:
            return None
        acc = 0
        for name, nt, hint in norm_chunks:
            if acc <= pos < acc + len(nt):
                return {"file": name, "offset": pos - acc, "hint": hint}
            acc += len(nt)
        return {"file": "?", "offset": pos, "hint": None}

    results = []
    for s in slices:
        body = normalize(s["text"])
        segments = [seg for seg in ELLIPSIS_SPLIT.split(body) if len(seg) >= 4]
        if not segments:
            results.append({**s, "status": "FAIL", "reason": "切片内容为空或过短",
                            "found": [], "chapters": []})
            continue
        cursor = 0
        found, missing, order_issue = [], [], False
        for seg in segments:
            pos = flat.find(seg, cursor)
            if pos < 0:
                pos0 = flat.find(seg)
                if pos0 < 0:
                    missing.append(seg[:40])
                    continue
                order_issue = True
                pos = pos0
            cursor = pos + len(seg)
            found.append({**locate(seg), "preview": seg[:60]})
        if missing:
            status, reason = "FAIL", f"{len(missing)} 段在原文中找不到：{missing[0]}…"
        elif order_issue:
            status, reason = "WARN", "片段顺序与原文不一致（可能跨段拼接时顺序写反）"
        else:
            status, reason = "PASS", ""
        chapters = []
        for f in found:
            if f["hint"]:
                ch = f"第{f['hint']}章"
            elif index_rows:
                ch = chapter_of(f["offset"], index_rows)
            else:
                ch = "?"
            if ch != "?" and ch not in chapters:
                chapters.append(ch)

        # 标注章号 vs 实际位置：不一致时降级为 WARN，提示可能是抄错章号或跨章拼接
        expected = None
        em = re.search(r"第\s*(\d+)", s["chapter"] or "")
        if em:
            expected = f"第{int(em.group(1))}章"
        elif (s["chapter"] or "").strip().isdigit():
            expected = f"第{int(s['chapter'].strip())}章"
        if status == "PASS" and expected and chapters and expected not in chapters:
            status = "WARN"
            reason = (f"标注为 {expected}，但这段文字出现在 {'、'.join(chapters)}"
                      f"（可能抄错章号，或该段落原文本身跨章）")

        results.append({**s, "status": status, "reason": reason,
                        "found": found, "chapters": chapters})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="校验原文切片是否逐字来自原文")
    ap.add_argument("--digest", required=True, help="含切片的 markdown（速读稿/衔接包）")
    ap.add_argument("--source", required=True, help="原文：单个整本文件，或 原文/ 目录")
    ap.add_argument("--index", help="chapters.tsv，用于把切片定位到具体章节")
    ap.add_argument("--collect", help="把通过校验的切片按阅读顺序汇总到该文件")
    ap.add_argument("--min-ratio", type=float, default=0.0,
                    help="切片总字数 / 原文总字数 上限，超过只告警（默认 0=不检查）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    digest_path = Path(args.digest).expanduser().resolve()
    if not digest_path.is_file():
        raise SystemExit(f"找不到：{digest_path}")
    digest_text = digest_path.read_text(encoding="utf-8")
    slices = parse_slices(digest_text)
    if not slices:
        print("[error] 没找到任何切片。切片标记格式应为：`【原文·第12章 标签】` "
              "后跟一行或多行以 `>` 开头的原文引用。", file=sys.stderr)
        return 2

    source = Path(args.source).expanduser().resolve()
    chunks = load_source(source)
    index_rows = load_index(Path(args.index).expanduser().resolve()) if args.index else []

    results = verify(slices, chunks, index_rows)
    passed = [r for r in results if r["status"] == "PASS"]
    warned = [r for r in results if r["status"] == "WARN"]
    failed = [r for r in results if r["status"] == "FAIL"]

    total_slice = sum(len(normalize(r["text"])) for r in results)
    total_src = sum(len(normalize(t)) for _, t, _ in chunks)
    ratio = total_slice / max(total_src, 1)

    if args.json:
        print(json.dumps({
            "digest": str(digest_path), "source": str(source),
            "slices": len(results), "pass": len(passed),
            "warn": len(warned), "fail": len(failed),
            "slice_chars": total_slice, "source_chars": total_src, "ratio": round(ratio, 4),
            "results": [{k: r[k] for k in ("chapter", "label", "line", "status",
                                           "reason", "chapters")} for r in results],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"切片校验：共 {len(results)} 条 → 通过 {len(passed)}，"
              f"告警 {len(warned)}，失败 {len(failed)}")
        print(f"切片占原文比例：{ratio:.1%}（{total_slice:,}/{total_src:,} 字）")
        for r in results:
            mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["status"]]
            where = f" | 定位={'、'.join(r['chapters'][:2])}" if r.get("chapters") else ""
            print(f"  {mark} L{r['line']} 【{r['chapter']}】{r.get('label') or ''}{where}")
            if r["reason"]:
                print(f"       {r['reason']}")
        if ratio > 0.25:
            print("[warn] 切片占比超过 25%。速读稿应当是「摘要 + 少量原文」的形态；"
                  "大量复制原文既没起到速读作用，也有版权风险，建议压缩。", file=sys.stderr)

    if args.collect:
        out = Path(args.collect).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        kept = [r for r in results if r["status"] in ("PASS", "WARN")]
        with out.open("w", encoding="utf-8") as f:
            f.write("# 原文切片合集\n\n")
            f.write("> 从速读稿/衔接包中汇总，按出现顺序排列，均已通过逐字校验。\n")
            f.write("> 用于「只想细读关键原文」的场景。\n\n---\n\n")
            for r in kept:
                tag = f"{r['chapter']}" + (f" {r['label']}" if r.get("label") else "")
                if r["status"] == "WARN":
                    tag += f"  <!-- WARN: {r['reason']} -->"
                f.write(f"## {tag}\n\n")
                for line in r["raw"].split("\n"):
                    f.write(f"> {line}\n")
                f.write("\n")
        skipped = len(results) - len(kept)
        msg = f"切片合集已写入：{out}（{len(kept)} 条）"
        if skipped:
            msg += f"，{skipped} 条未通过校验、已排除"
        print(msg)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
