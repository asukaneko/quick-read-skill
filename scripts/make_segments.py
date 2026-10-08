#!/usr/bin/env python3
"""生成整书或剩余范围的分段草案；剧情断点由读者/Agent 核对后补充。

python3 make_segments.py --index chapters.tsv --base 阅读工作区/书名 --start 121 --chapters 100
python3 make_segments.py --index chapters.tsv --base 阅读工作区/书名 --start 121 --end 320 --ends 216,320

只读章节索引。输出计划/分段规划_第X-Y章.md 与同名 TSV，不读取原文、不回写进度。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

from make_plan import load_index, select_scope


def split_segments(rows, ends=None, chapters=100, target_chars=None):
    if ends is not None:
        if not ends or ends[-1] != rows[-1]["idx"]:
            raise ValueError("--ends 最后一个断点必须等于范围末章")
        if ends[0] < rows[0]["idx"] or any(a >= b for a, b in zip(ends, ends[1:])):
            raise ValueError("--ends 必须在范围内严格递增")
        endpoints = set(ends)
    else:
        endpoints = None
    segments, current, chars = [], [], 0
    for row in rows:
        current.append(row)
        chars += row["chars"]
        if endpoints is not None:
            boundary = row["idx"] in endpoints
        elif target_chars is not None:
            boundary = chars >= target_chars
        else:
            boundary = len(current) >= chapters
        if boundary:
            segments.append(current)
            current, chars = [], 0
    if current:
        segments.append(current)
    return segments


def main():
    ap = argparse.ArgumentParser(description="按索引生成分段规划草案，剧情断点需另行核对")
    ap.add_argument("--index", required=True, help="split_chapters.py 生成的 chapters.tsv")
    ap.add_argument("--base", required=True, help="书籍工作区根目录")
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    sizing = ap.add_mutually_exclusive_group()
    sizing.add_argument("--chapters", type=int, help="每段章数草案，默认 100")
    sizing.add_argument("--target-chars", type=int, help="按索引字数累积至此预算后切段")
    sizing.add_argument("--ends", help="已选各段末章 idx，逗号分隔，最后一个必须是范围末章")
    ap.add_argument("--batch-size", type=int, default=12, help="估算段内读卡批数，默认 12")
    ap.add_argument("--first-number", type=int, default=1, help="首段编号，续规划时可延续段号")
    ap.add_argument("--out", help="Markdown 输出路径（绝对或相对 base），TSV 与其同名")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖同名规划文件；手填依据会被覆盖")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for name in ("chapters", "target_chars", "batch_size", "first_number"):
        value = getattr(args, name)
        if value is not None and value < 1:
            ap.error(f"--{name.replace('_', '-')} 必须 ≥ 1")
    base = Path(args.base).expanduser().resolve()
    try:
        rows, lo, hi = select_scope(load_index(Path(args.index).expanduser().resolve()),
                                    args.start, args.end)
        if args.target_chars and any(r["chars"] <= 0 for r in rows):
            raise ValueError("按字数规划需要每章都有正数字数，请先补齐索引")
        ends = [int(s.strip()) for s in args.ends.split(",")] if args.ends is not None else None
        groups = split_segments(rows, ends, args.chapters or 100, args.target_chars)
    except ValueError as exc:
        ap.error(str(exc))
    method = ("指定末章" if ends is not None else
              f"约 {args.target_chars:,} 字/段" if args.target_chars else
              f"约 {args.chapters or 100} 章/段")
    segments = []
    for number, group in enumerate(groups, args.first_number):
        a, b = group[0]["idx"], group[-1]["idx"]
        segments.append({"segment": f"段{number:02d}", "start": a, "end": b,
                         "chapters": len(group), "chars": sum(r["chars"] for r in group),
                         "batches": (len(group) + args.batch_size - 1) // args.batch_size,
                         "first_title": group[0]["title"], "last_title": group[-1]["title"],
                         "directory": f"第{a}-{b}章", "next_start": b + 1 if b < hi else None})
    md_path = Path(args.out).expanduser() if args.out else Path("计划") / f"分段规划_第{lo}-{hi}章.md"
    if not md_path.is_absolute():
        md_path = base / md_path
    if md_path.suffix.lower() != ".md":
        ap.error("--out 必须以 .md 结尾")
    tsv_path = md_path.with_suffix(".tsv")
    if not args.dry_run:
        if not args.force and (md_path.exists() or tsv_path.exists()):
            ap.error("规划文件已存在；续规划请换范围/路径，确认重建草案可加 --force")
        md = [f"# 分段规划 · 第{lo}–{hi}章", "",
              f"- 编号口径：索引 idx；范围 {len(rows)} 章 / {sum(r['chars'] for r in rows):,} 字（沿用索引口径）。",
              f"- 划分方式：{method}；预计批次按段首每 {args.batch_size} 章切分。",
              "- 以下是范围与预算草案。只读取了索引；断点依据待补，尚未核对剧情。",
              "- 执行前核对 _进度.md 的已验收范围；实际断点调整后，同步更新相邻未执行段。", "",
              "| 段 | 范围 (idx) | 章数 | 字数 | 预计批次 | 首章（索引原名） | 末章（索引原名） | 断点依据 | 状态 |",
              "|---|---|---:|---:|---:|---|---|---|---|"]
        table = io.StringIO(newline="")
        writer = csv.writer(table, delimiter="\t", lineterminator="\n")
        writer.writerow(["段", "起始idx", "结束idx", "章数", "字数", "预计批次", "首章", "末章", "目录", "下一段起idx"])
        for s in segments:
            first = s["first_title"].replace("|", "\\|")
            last = s["last_title"].replace("|", "\\|")
            md.append(f"| {s['segment']} | 第{s['start']}–{s['end']}章 | {s['chapters']} | {s['chars']:,} | {s['batches']} | {first} | {last} | 待补 | 待核对断点 |")
            writer.writerow([s[k] for k in ("segment", "start", "end", "chapters", "chars", "batches", "first_title", "last_title", "directory", "next_start")])
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
        tsv_path.write_text(table.getvalue(), encoding="utf-8")
    result = {"range": [lo, hi], "method": method, "segments": segments,
              "markdown": str(md_path), "manifest": str(tsv_path), "dry_run": args.dry_run}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{'预览' if args.dry_run else '规划草案'} · 第{lo}–{hi}章｜{len(rows)} 章｜{len(segments)} 段｜剧情断点待核对")
        for s in segments:
            print(f"  {s['segment']} 第{s['start']}–{s['end']}章 · {s['chars']:,} 字 · 预计 {s['batches']} 批")
        if not args.dry_run:
            print(f"规划：{md_path}｜清单：{tsv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
