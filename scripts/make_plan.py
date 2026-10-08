#!/usr/bin/env python3
"""批次计划生成：把章节索引切成可并行派单的批次，并为每批写一份自包含的派单文件。

速读流水线的规模靠两件事撑住：卡片落盘 + 子代理并行。
本脚本负责后半件——按固定粒度（默认 12 章）把范围切成批，产出：

    计划/批次清单.tsv        批次 / 起始章 / 结束章 / 章数 / 字数 / 单元名
    计划/批次/批次001.md     该批的自包含派单文件（子代理只读它 + 规范）

派单文件里写的是**绝对路径**：子代理与你不同上下文，读不到你的相对路径。
默认单元号按全局章号网格（每 size 章一个单元）编号，续跑时编号不会错位。
指定 --segment 时从段首切批、段内从 01 编号，计划与产物按段隔离。

用法
----
python3 make_plan.py --index chapters.tsv --base 阅读工作区/归墟行 --start 121 --end 320
python3 make_plan.py --index chapters.tsv --base 阅读工作区/归墟行 --size 8 --json
python3 make_plan.py --index chapters.tsv --base 阅读工作区/归墟行 --start 121 --end 216 --segment 第121-216章

退出码：有章节文件定位不到时 1（计划仍会生成），全部正常 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_SIZE = 12


def load_index(tsv: Path):
    lines = tsv.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise SystemExit(f"索引为空：{tsv}")
    header = [h.strip() for h in lines[0].split("\t")]
    idx_col, title_col, chars_col, file_col = 0, 1, 2, None
    for i, name in enumerate(header):
        low = name.lower()
        if low in ("idx", "index", "序号", "序"):
            idx_col = i
        elif low in ("title", "标题", "章名"):
            title_col = i
        elif low in ("chars", "字数"):
            chars_col = i
        elif "file" in low or "路径" in name or "文件" in name:
            file_col = i
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) <= max(idx_col, title_col):
            continue
        try:
            idx = int(parts[idx_col])
        except ValueError:
            continue
        title = parts[title_col].strip() if len(parts) > title_col else ""
        try:
            chars = int(parts[chars_col]) if len(parts) > chars_col else 0
        except ValueError:
            chars = 0
        raw = parts[file_col].strip() if file_col is not None and len(parts) > file_col else ""
        rows.append({"idx": idx, "title": title, "chars": chars, "file": raw})
    return rows


def select_scope(rows, start: int | None = None, end: int | None = None):
    """检查 idx 范围完整性，避免缺章或重复索引被计划静默略过。"""
    if not rows:
        raise ValueError("索引里没有章节行")
    ordered = sorted(rows, key=lambda r: r["idx"])
    lo = start if start is not None else ordered[0]["idx"]
    hi = end if end is not None else ordered[-1]["idx"]
    if lo < 1 or hi < lo:
        raise ValueError("章节范围必须满足 1 ≤ start ≤ end")
    scope = [r for r in ordered if lo <= r["idx"] <= hi]
    seen = set()
    for row in scope:
        if row["idx"] in seen:
            raise ValueError(f"索引 idx 重复：{row['idx']}，请先核对原文与编号")
        if row["chars"] < 0:
            raise ValueError(f"索引字数不能为负：idx {row['idx']}")
        seen.add(row["idx"])
    expected = lo
    for row in scope:
        if row["idx"] != expected:
            raise ValueError(f"索引缺少 idx {expected}，请先修索引")
        expected += 1
    if expected <= hi:
        raise ValueError(f"索引缺少 idx {expected}，请先修索引")
    return scope, lo, hi


def locate_source(base: Path, src_dir: Path, row: dict):
    """尽量把索引行定位到真实文件，返回绝对路径或 None。"""
    cands = []
    name = row.get("file") or ""
    if name:
        p = Path(name)
        if p.is_absolute():
            cands.append(p)
        else:
            cands += [base / name, src_dir / p.name, src_dir / name]
    for pat in (f"第{row['idx']:04d}章*.txt", f"第{row['idx']}章*.txt", f"{row['idx']:04d}*.txt"):
        cands += sorted(src_dir.glob(pat))
    for c in cands:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def label(row: dict) -> str:
    title = row["title"].strip()
    if not title:
        return f"第{row['idx']}章"
    if title.startswith("第") or title[0].isdigit():
        return title
    return f"第{row['idx']}章 {title}"


def build_batches(rows, size: int):
    """按全局网格切批：边界落在 size 的整数倍上，保证单元号稳定。"""
    batches, cur = [], []
    for row in rows:
        cur.append(row)
        if len(cur) >= size or row["idx"] % size == 0:
            batches.append(cur)
            cur = []
    if cur:
        batches.append(cur)
    return batches


def write_batch_file(path: Path, no: int, total: int, rows, base: Path, spec: Path,
                     src_dir: Path, missing: list, ordinal: int | None = None):
    a, b = rows[0]["idx"], rows[-1]["idx"]
    unit = f"单元{no:02d}_第{a}-{b}章"
    chars = sum(r["chars"] for r in rows)
    out = []
    out.append(f"# 批次{no:03d} · 第{a}–{b}章（本轮第 {ordinal or no} 批 / 共 {total} 批）\n")
    out.append("> 本文件是你这一批的**全部输入说明**。只读本文件、下面的规范文件，"
               "以及本章批列出的原文文件；不要读别的批次，不要读全书。\n")
    out.append(f"- 章数：{len(rows)}（idx {a}–{b}）｜字数：约 {chars:,}")
    out.append(f"- 必读规范：`{spec}`（先读完再动手）")
    out.append(f"- 卡片输出目录：`{base / '分章卡片'}`（每章一份，文件名 `第NNNN章.md`，NNNN 用 idx 补零四位）")
    out.append(f"- 单元摘要输出：`{base / '单元摘要' / (unit + '.md')}`")
    out.append("")
    out.append("## 待读章节（按顺序逐章读完，一章都不能跳）\n")
    for r in rows:
        path_str = r.get("path") or "⚠️ 未定位到文件，请先修索引"
        out.append(f"- [ ] {label(r)}（idx {r['idx']}，{r['chars']:,} 字）— `{path_str}`")
    out.append("")
    out.append("## 回报格式（只回这一行，不要把原文或卡片正文回传）\n")
    out.append(f"`批次{no:03d} 完成｜章数 {len(rows)}｜卡片 {len(rows)}｜摘要 {unit}.md｜异常：无`\n")
    out.append("异常栏写清：缺文件、章号对不上、原文被截断等。发现异常也要把能写的卡片写完。")
    if missing:
        out.append("")
        out.append("## ⚠️ 已标注的原文瑕疵（不要凭空补齐这些章）\n")
        for m in missing:
            out.append(f"- {m}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return unit, chars


def main() -> int:
    ap = argparse.ArgumentParser(description="生成并行派单用的批次计划")
    ap.add_argument("--index", required=True, help="chapters.tsv（split_chapters.py 产出）")
    ap.add_argument("--base", required=True, help="工作区根目录，如 阅读工作区/归墟行")
    ap.add_argument("--start", type=int, help="起始 idx（默认索引首章）")
    ap.add_argument("--end", type=int, help="结束 idx（默认索引末章）")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE,
                    help=f"每批章数，默认 {DEFAULT_SIZE}（3–4 万字一批，便于并行）")
    ap.add_argument("--src-dir", help="原文目录，默认 <base>/原文")
    ap.add_argument("--spec", help="规范路径；--segment 时相对 base，原有模式相对当前目录；默认 <base>/计划/批次规范.md")
    ap.add_argument("--segment", help="段目录名，如 第121-216章；段内编号从 01 起，计划按段隔离")
    ap.add_argument("--dry-run", action="store_true", help="只预览批次与缺文件情况，不写文件")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.size < 1:
        raise SystemExit("--size 必须 ≥ 1")
    base = Path(args.base).expanduser().resolve()
    src_dir = Path(args.src_dir).expanduser().resolve() if args.src_dir else base / "原文"
    spec = Path(args.spec).expanduser() if args.spec else base / "计划" / "批次规范.md"
    if not spec.is_absolute():
        spec = (base if args.segment else Path.cwd()) / spec
    spec = spec.resolve()
    if args.segment is not None and (args.segment in ("", ".", "..")
                                     or any(c in args.segment for c in '/\\:')):
        ap.error("--segment 必须是单层段目录名")
    output_base = base / args.segment if args.segment else base

    rows = load_index(Path(args.index).expanduser().resolve())
    try:
        scope, lo, hi = select_scope(rows, args.start, args.end)
    except ValueError as exc:
        ap.error(str(exc))

    missing_files = []
    for r in scope:
        p = locate_source(base, src_dir, r)
        r["path"] = str(p) if p else ""
        if not p:
            missing_files.append(label(r))

    batches = ([scope[i:i + args.size] for i in range(0, len(scope), args.size)]
               if args.segment else build_batches(scope, args.size))
    suffix = f"_{args.segment}" if args.segment else ""
    batches_dir = base / "计划" / f"批次{suffix}"
    if not args.dry_run:
        batches_dir.mkdir(parents=True, exist_ok=True)
        if args.segment:
            (output_base / "分章卡片").mkdir(parents=True, exist_ok=True)
            (output_base / "单元摘要").mkdir(parents=True, exist_ok=True)

    summary, tsv_lines = [], ["批次\t起始章\t结束章\t章数\t字数\t单元名"]
    for k, rows_k in enumerate(batches, start=1):
        no = k if args.segment else (rows_k[0]["idx"] - 1) // args.size + 1
        a, b = rows_k[0]["idx"], rows_k[-1]["idx"]
        path = batches_dir / f"批次{no:03d}.md"
        batch_missing = [label(r) for r in rows_k if not r.get("path")]
        unit = f"单元{no:02d}_第{a}-{b}章"
        chars = sum(r["chars"] for r in rows_k)
        if not args.dry_run:
            write_batch_file(path, no, len(batches), rows_k, output_base, spec,
                             src_dir, batch_missing, ordinal=k)
        tsv_lines.append(f"批次{no:03d}\t{a}\t{b}\t{len(rows_k)}\t{chars}\t{unit}")
        summary.append({"batch": no, "start": a, "end": b, "chapters": len(rows_k),
                        "chars": chars, "unit": unit, "plan_file": str(path)})

    list_path = base / "计划" / f"批次清单{suffix}.tsv"
    if not args.dry_run:
        list_path.write_text("\n".join(tsv_lines) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps({"base": str(base), "spec": str(spec), "size": args.size,
                          "segment": args.segment, "output_base": str(output_base),
                          "dry_run": args.dry_run,
                          "range": [lo, hi], "batches": summary,
                          "missing_files": missing_files,
                          "manifest": str(list_path)}, ensure_ascii=False, indent=2))
    else:
        print(f"工作区：{base}")
        print(f"规范文件：{spec}（记得先写好它再派单）")
        print(f"范围：idx {lo}–{hi}｜{len(scope)} 章｜{sum(r['chars'] for r in scope):,} 字"
              f"｜{len(batches)} 批（每批 {args.size} 章）\n")
        for s in summary:
            print(f"  批次{s['batch']:03d}  第{s['start']}–{s['end']}章  "
                  f"{s['chapters']} 章 / {s['chars']:,} 字  → {s['plan_file']}")
        print(f"\n批次清单：{list_path}")
    if missing_files:
        print(f"\n⚠️ {len(missing_files)} 章定位不到原文文件（并行派单前先修索引）：", file=sys.stderr)
        for m in missing_files[:20]:
            print(f"  - {m}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
