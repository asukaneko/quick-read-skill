#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py — 一步收尾：覆盖率 + 切片逐字 + HTML，单进程一次跑完。

为什么比 finalize.py 快：
- finalize.py 要拉三个子进程、各读一遍速读稿，切片有 FAIL 时还得把
  verify_slices.py 整个重跑一遍取明细；这里单进程一遍过，明细第一次就拿到。
- 原文归一化（全角半角、去空白引号）结果缓存在 <原文>/.normcache，几百万字
  的长篇第二次校验降到亚秒级；首次只多花几秒。删掉缓存文件只是下次重新归一化。
- 输出固定一两行；明细只在校验失败时写入 校验报告.md。

用法
----
python3 check.py --dir 第1583-1678章 --range 1583-1678 --source 原文/
python3 check.py --digest 第1583-1678章/速读稿.md --no-html
python3 check.py --dir 第X-Y章 --json          # 给上层脚本吃

退出码：全部通过 0；覆盖率 <100% 或切片有 FAIL → 1；脚本级错误 2。
只依赖 Python 标准库（3.8+）。校验规则与 coverage_check.py / verify_slices.py 一致
（直接复用其函数），发现差异时以那两个脚本为准。
"""

from __future__ import annotations

import argparse
import bisect
import json
import marshal
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from coverage_check import compress, covered_chapters, load_index as load_cov_index
from finalize import load_json, pick, pick_dir, run, short_missing
from verify_slices import (ELLIPSIS_SPLIT, chapter_of, load_index as load_vs_index,
                           normalize, parse_slices)

CACHE_NAME = ".normcache"
CACHE_VER = 1          # 归一化口径变了就 +1，旧缓存自动失效


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-16", errors="replace")


def load_chunks(source: Path):
    """返回 [(文件名, 归一化文本, 章号hint)]。目录模式读写 .normcache 缓存。"""
    if source.is_file():
        return [(source.name, normalize(_decode(source)), None)]
    files = sorted(
        [p for p in source.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".md")],
        key=lambda p: (int(re.match(r"(\d+)", p.name).group(1))
                       if re.match(r"(\d+)", p.name) else 10 ** 9, p.name),
    )
    if not files:
        raise SystemExit(f"目录里没有 txt/md：{source}")
    cache_path = source / CACHE_NAME
    cache: dict = {}
    if cache_path.is_file():
        try:
            blob = marshal.loads(cache_path.read_bytes())
            if isinstance(blob, dict) and blob.get("v") == CACHE_VER:
                cache = blob.get("files") or {}
        except Exception:
            cache = {}
    out, used = [], set()
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        key = f"{p.name}|{st.st_size}|{st.st_mtime_ns}"
        norm = cache.get(key)
        if norm is None:
            norm = normalize(_decode(p))
            cache[key] = norm
        used.add(key)
        m = re.match(r"\D*?(\d{1,6})", p.name)
        out.append((p.name, norm, int(m.group(1)) if m else None))
    try:
        tmp = cache_path.with_name(CACHE_NAME + ".tmp")
        with tmp.open("wb") as f:
            marshal.dump({"v": CACHE_VER, "files": {k: cache[k] for k in used}}, f)
        os.replace(tmp, cache_path)
    except OSError:
        pass    # 只读目录等场景：校验照跑，只是没有缓存
    return out


def verify_fast(slices, chunks, index_rows):
    """verify_slices.verify 的单遍版本：chunks 已归一化，不再重复归一化。"""
    flat = "".join(t for _, t, _ in chunks)
    starts, names, hints = [], [], []
    acc = 0
    for name, t, hint in chunks:
        starts.append(acc)
        names.append(name)
        hints.append(hint)
        acc += len(t)

    def locate(pos: int):
        i = bisect.bisect_right(starts, pos) - 1
        if i < 0:
            return {"file": "?", "offset": pos, "hint": None}
        return {"file": names[i], "offset": pos - starts[i], "hint": hints[i]}

    results = []
    for s in slices:
        body = normalize(s["text"])
        segments = [seg for seg in ELLIPSIS_SPLIT.split(body) if len(seg) >= 4]
        if not segments:
            results.append({**s, "status": "FAIL", "reason": "切片内容为空或过短",
                            "chapters": []})
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
            loc = locate(pos)
            found.append({**loc, "preview": seg[:60]})
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

        results.append({**s, "status": status, "reason": reason, "chapters": chapters})
    return results


def write_slice_collection(out: Path, results) -> int:
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
    return len(kept)


def write_check_report(out: Path, missing_groups, results, ratio):
    lines = ["# 校验报告\n"]
    if missing_groups:
        lines += ["## ⚠️ 覆盖率漏章\n"]
        lines += [f"- 第 {g} 章" for g in missing_groups] + [""]
    bad = [r for r in results if r["status"] in ("FAIL", "WARN")]
    if bad:
        lines += ["## 切片问题\n"]
        for r in bad:
            m = "❌" if r["status"] == "FAIL" else "⚠️"
            where = f"｜定位={'、'.join(r['chapters'][:2])}" if r.get("chapters") else ""
            lines.append(f"- {m} L{r['line']} 【{r['chapter']}】{r.get('label') or ''}"
                         f"{where} — {r['reason']}")
        lines.append("")
    if ratio > 0.25:
        lines += ["## ⚠️ 切片占比过高",
                  f"切片占原文 {ratio:.1%}（阈值 25%）。速读稿应当是「摘要 + 少量原文」的形态，建议压缩。\n"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="一步收尾校验：覆盖率 + 切片逐字 + HTML")
    ap.add_argument("--dir", help="段目录（含 速读稿.md），如 第1583-1678章")
    ap.add_argument("--digest", help="直接指定速读稿.md，替代 --dir")
    ap.add_argument("--index", help="chapters.tsv，默认在段目录/上级自动找")
    ap.add_argument("--source", help="原文目录或整本 txt，默认自动找 原文/")
    ap.add_argument("--range", help="本段章号范围，如 1583-1678（覆盖率只查这段）")
    ap.add_argument("--characters", help="人物档案.md，默认自动找")
    ap.add_argument("--title", help="HTML 标题，默认取速读稿 H1")
    ap.add_argument("--out", help="HTML 输出路径，默认与速读稿同名 .html")
    ap.add_argument("--no-html", action="store_true", help="只校验，不生成 HTML")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    args = ap.parse_args()

    raw = None
    if args.digest:
        raw = Path(args.digest)
    elif args.dir:
        d = Path(args.dir).expanduser()
        raw = pick(d, "速读稿.md") or pick(d, "衔接包.md")
    digest = raw.expanduser().resolve() if raw else None
    if not digest or not digest.is_file():
        print("[error] 找不到速读稿：用 --dir <段目录> 或 --digest <文件> 指定", file=sys.stderr)
        return 2
    base = digest.parent

    index = None
    if args.index:
        index = Path(args.index).expanduser().resolve()
    else:
        p = pick(base, "chapters.tsv", "章节索引.tsv")
        index = p.expanduser().resolve() if p else None

    source = None
    if args.source:
        source = Path(args.source).expanduser().resolve()
    else:
        p = pick_dir(base, "原文") or pick(base, "全书.txt") or pick_dir(base.parent, "原文")
        source = p.expanduser().resolve() if p else None

    doc = digest.read_text(encoding="utf-8")
    summary: dict = {"digest": str(digest), "checks": {}, "errors": []}
    slice_results: list = []

    # --- 1) 覆盖率（速读稿已在内存，直接正则抽取章号） ---
    cov: dict = {"skipped": "没找到 chapters.tsv"}
    cov_text = None
    if index:
        try:
            rows = load_cov_index(index)
            if not rows:
                raise SystemExit("chapters.tsv 为空或格式不对")
            covered, ranges = covered_chapters(doc)
            scope = [r["idx"] for r in rows]
            if args.range:
                parts = re.split(r"[-–—~～至到]", args.range.strip())
                lo = int(parts[0])
                hi = int(parts[1]) if len(parts) > 1 and parts[1] else lo
                scope = [i for i in scope if lo <= i <= hi]
            by_idx = {r["idx"]: r for r in rows}
            covered_in_scope = [i for i in scope if i in covered]
            missing = [i for i in scope if i not in covered]
            rate = len(covered_in_scope) / max(len(scope), 1)
            cov = {"total": len(scope), "covered": len(covered_in_scope),
                   "missing": missing, "rate": round(rate, 4)}
            report = ["# 覆盖率报告\n", f"- 检查文件：`{digest.name}`",
                      f"- 检查范围：{scope[0]}–{scope[-1]} 章（共 {len(scope)} 章）"
                      if scope else "- 检查范围：无",
                      f"- 覆盖率：**{rate:.1%}**（{len(covered_in_scope)}/{len(scope)}）"]
            if ranges:
                report.append("- 识别到的章节区间引用："
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
            cov_text = "\n".join(report)
            (base / "覆盖率报告.md").write_text(cov_text + "\n", encoding="utf-8")
        except SystemExit as e:
            cov = {"error": str(e)}
        except Exception as e:
            cov = {"error": str(e)[:300]}
    summary["checks"]["coverage"] = cov

    # --- 2) 切片逐字（同一份速读稿顺带解析，原文走缓存） ---
    sl: dict = {"skipped": "没找到原文目录（--source 原文/）"}
    ratio = 0.0
    if source:
        try:
            slices = parse_slices(doc)
            if not slices:
                sl = {"error": "没找到任何切片。应为【原文·第N章 章名】标记 + > 引用行"}
            else:
                chunks = load_chunks(source)
                index_rows = load_vs_index(index) if index else []
                slice_results = verify_fast(slices, chunks, index_rows)
                passed = sum(1 for r in slice_results if r["status"] == "PASS")
                warned = sum(1 for r in slice_results if r["status"] == "WARN")
                failed = sum(1 for r in slice_results if r["status"] == "FAIL")
                total_slice = sum(len(normalize(r["text"])) for r in slice_results)
                total_src = sum(len(t) for _, t, _ in chunks)
                ratio = total_slice / max(total_src, 1)
                sl = {"slices": len(slice_results), "pass": passed, "warn": warned,
                      "fail": failed, "ratio": round(ratio, 4)}
                write_slice_collection(base / "原文切片.md", slice_results)
        except SystemExit as e:
            sl = {"error": str(e)}
        except Exception as e:
            sl = {"error": str(e)[:300]}
    summary["checks"]["slices"] = sl

    # --- 3) HTML（调用兄弟脚本，反正只花两三秒） ---
    if not args.no_html:
        cmd = ["--digest", str(digest), "--json"]
        if args.out:
            cmd += ["--out", args.out]
        if args.characters:
            cmd += ["--characters", args.characters]
        if index:
            cmd += ["--index", str(index)]
        if args.title:
            cmd += ["--title", args.title]
        code, so, se = run("build_html.py", cmd)
        html = load_json(so) or {"error": (se or so).strip()[:300]}
        html["exit"] = code
        summary["checks"]["html"] = html

    # --- 判定 ---
    fail = 0
    if cov.get("total") is not None and cov.get("covered") != cov.get("total"):
        fail = 1
    if sl.get("fail"):
        fail = 1
    for k, v in summary["checks"].items():
        if isinstance(v, dict) and v.get("error"):
            summary["errors"].append(f"{k}: {v['error']}")
    if summary["errors"]:
        fail = max(fail, 1)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return fail

    # --- 人读输出：一两行结论，失败才展开 ---
    mark = "✅" if fail == 0 else "❌"
    bits = []
    if cov.get("skipped"):
        bits.append(f"覆盖率：跳过 — {cov['skipped']}")
    elif cov.get("error"):
        bits.append(f"覆盖率：报错 {str(cov['error'])[:160]}")
    elif "total" in cov:
        bits.append(f"覆盖率 {'✅' if not cov.get('missing') else '❌'} "
                    f"{cov['covered']}/{cov['total']}（{cov.get('rate', 0):.0%}）")
    if sl.get("skipped"):
        bits.append(f"切片：跳过 — {sl['skipped']}")
    elif sl.get("error"):
        bits.append(f"切片：报错 {str(sl['error'])[:160]}")
    elif "slices" in sl:
        bits.append(f"切片 {'✅' if not sl.get('fail') else '❌'} {sl['slices']} → "
                    f"过 {sl['pass']} · 告警 {sl['warn']} · 败 {sl['fail']}"
                    f"｜占原文 {ratio:.1%}")
    h = summary["checks"].get("html")
    if h and not h.get("error"):
        bits.append(f"HTML ✅ {Path(h.get('html', '')).name}"
                    f"（{h.get('bytes', 0) / 1024:.0f} KB · 单元 {h.get('units')}"
                    f" · 章 {h.get('chapters')} · 人物 {h.get('chars')}）")
    elif h:
        bits.append("HTML：生成失败")

    lines = [f"{mark} 校验 · {base.name}｜" + "｜".join(bits)]

    detail = []
    miss = cov.get("missing") or []
    if miss:
        detail.append(f"· 覆盖率漏章：{short_missing(miss)}")
    for r in slice_results:
        if r["status"] == "PASS":
            continue
        m = "❌" if r["status"] == "FAIL" else "⚠️"
        detail.append(f"    {m} L{r['line']}【{r['chapter']}】{r['reason']}")
    if ratio > 0.25:
        detail.append(f"    ⚠️ 切片占比 {ratio:.1%} 超过 25%，建议压缩摘要外的原文引用")
    if detail:
        lines += detail[:10]
        write_check_report(base / "校验报告.md", compress(miss), slice_results, ratio)
        lines.append(f"    明细 → 校验报告.md（共 {len(detail)} 条；修完重跑本命令）")

    made = []
    if h and not h.get("error") and h.get("html"):
        made.append(Path(h["html"]).name)
    if cov_text is not None:
        made.append("覆盖率报告.md")
    if "slices" in sl:
        made.append("原文切片.md")
    if made:
        lines.append("产出：" + " · ".join(made))
    print("\n".join(lines))
    return fail


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        raise SystemExit(2)
