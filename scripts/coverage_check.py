#!/usr/bin/env python3
"""覆盖率兜底：检查每一章是否在速读稿/衔接包里被「交代过」。

用户的原话是「不重要的也要讲清来龙去脉」，最怕的是静默漏章。
写稿时很容易写着写着就只覆盖了有戏的章节，把过渡章忘掉。
这个脚本把「有没有漏」变成可以一眼看出来的东西：它从成品里抽出所有章节引用
（第12章、第12–18章、第十二章 都认），和 chapters.tsv 对一遍。

用法
----
python3 coverage_check.py --index chapters.tsv --doc 速读稿.md
python3 coverage_check.py --index chapters.tsv --doc 衔接包.md --range 121-200
python3 coverage_check.py --index chapters.tsv --doc 速读稿.md --out 覆盖率报告.md --json

退出码：覆盖率 >= --fail-under（默认 1.0）时 0，否则 1。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_NUM = r"[0-9〇零一二三四五六七八九十百千万两]{1,12}"


def cn2num(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if not text or any(c not in _CN_DIGITS and c not in _CN_UNITS for c in text):
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


RANGE_RE = re.compile(rf"第?\s*({_NUM})\s*(?:章)?\s*[-–—~～至到]\s*({_NUM})\s*章")
SINGLE_RE = re.compile(rf"第\s*({_NUM})\s*[章回节話话]")
COUNT_RE = re.compile(r"(\d+)\s*章")


def covered_chapters(doc: str):
    """返回 (covered_set, explicit_ranges)。"""
    covered: set[int] = set()
    ranges: list[tuple[int, int]] = []
    for m in RANGE_RE.finditer(doc):
        a, b = cn2num(m.group(1)), cn2num(m.group(2))
        if a and b and a <= b:
            ranges.append((a, b))
            covered.update(range(a, b + 1))
    for m in SINGLE_RE.finditer(doc):
        n = cn2num(m.group(1))
        if n:
            covered.add(n)
    return covered, ranges


def load_index(tsv: Path):
    rows = []
    for line in tsv.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                rows.append({"idx": int(parts[0]), "title": parts[1],
                             "chars": int(parts[2])})
            except ValueError:
                continue
    return rows


def compress(nums: list[int]) -> list[str]:
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
            continue
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
        start = prev = n
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="章节覆盖率检查")
    ap.add_argument("--index", required=True, help="chapters.tsv")
    ap.add_argument("--doc", required=True, help="速读稿.md / 衔接包.md")
    ap.add_argument("--range", help="只检查某段章号，如 121-200（衔接包模式）")
    ap.add_argument("--out", help="把报告写入该 markdown")
    ap.add_argument("--fail-under", type=float, default=1.0,
                    help="覆盖率低于该值则退出码 1（默认 1.0）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    index_rows = load_index(Path(args.index).expanduser().resolve())
    if not index_rows:
        raise SystemExit("chapters.tsv 为空或格式不对")
    doc = Path(args.doc).expanduser().resolve().read_text(encoding="utf-8")
    covered, ranges = covered_chapters(doc)

    scope = [r["idx"] for r in index_rows]
    if args.range:
        a, _, b = args.range.partition("-")
        lo, hi = int(a), int(b or a)
        scope = [i for i in scope if lo <= i <= hi]
    missing = [i for i in scope if i not in covered]
    covered_in_scope = [i for i in scope if i in covered]
    rate = len(covered_in_scope) / max(len(scope), 1)

    by_idx = {r["idx"]: r for r in index_rows}
    report = []
    report.append(f"# 覆盖率报告\n")
    report.append(f"- 检查文件：`{args.doc}`")
    report.append(f"- 检查范围：{scope[0]}–{scope[-1]} 章（共 {len(scope)} 章）"
                  if scope else "- 检查范围：无")
    report.append(f"- 覆盖率：**{rate:.1%}**（{len(covered_in_scope)}/{len(scope)}）")
    if ranges:
        report.append(f"- 识别到的章节区间引用："
                      + "、".join(f"第{a}–{b}章" for a, b in ranges[:12]))
    if missing:
        report.append(f"\n## ⚠️ 未被交代的章节（{len(missing)} 章）\n")
        report.append("这些章要么真的漏了，要么被一句话概括时没写清覆盖范围。"
                      "逐条确认：不漏则并入摘要句并补上章号范围；漏了则补写。\n")
        for group in compress(missing):
            first = int(group.split("–")[0])
            sample = ""
            if first in by_idx:
                t = by_idx[first]["title"]
                label = t if t.startswith("第") else f"第{first}章 {t}"
                sample = f"　（如 {label}，{by_idx[first]['chars']:,} 字）"
            report.append(f"- 第 {group} 章{sample}")
    else:
        report.append("\n✅ 范围内每一章都在文档中被交代过。")

    text = "\n".join(report)
    if args.json:
        print(json.dumps({"doc": args.doc, "scope": [scope[0], scope[-1]] if scope else [],
                          "total": len(scope), "covered": len(covered_in_scope),
                          "missing": missing, "rate": round(rate, 4)},
                         ensure_ascii=False, indent=2))
    else:
        print(text)
    if args.out:
        Path(args.out).expanduser().resolve().write_text(text + "\n", encoding="utf-8")
        print(f"\n报告已写入：{args.out}")

    return 0 if rate >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
