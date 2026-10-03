#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize.py — 一段速读收尾：覆盖率 + 切片逐字 + HTML，一条命令跑完。

为什么要它：收尾原本要跑三四条命令、每条都往对话里回一大段文本，
既费 token 又要人肉比对。这里把三件事串起来，**只在结尾回一小段摘要**，
通过的项目不逐条刷屏，只有失败/漏章才列明细。

做三件事（任一步失败不影响其它步，最后统一汇报）：
1. coverage_check.py  → 每章是否被交代；顺手写 覆盖率报告.md
2. verify_slices.py   → 切片是否逐字来自原文；顺手汇总 原文切片.md
3. build_html.py      → 生成单文件 HTML 阅读器（导航 + 人物链接 + 进度缓存）

用法
----
python3 finalize.py --dir 第1583-1678章 --range 1583-1678 --source 原文/
python3 finalize.py --digest 第1583-1678章/速读稿.md --index chapters.tsv
python3 finalize.py --dir 第X-Y章 --json          # 给上层脚本吃

退出码：全部通过 0；覆盖率 <100% 或切片有 FAIL → 1；脚本级错误 2。
只依赖 Python 标准库（3.8+）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(script: str, args: list[str]):
    """跑同目录下的兄弟脚本，返回 (exit_code, stdout, stderr)。"""
    cmd = [sys.executable, str(HERE / script), *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def load_json(text: str):
    """从脚本输出里挖出第一个 JSON 对象。

    兄弟脚本在 --out/--collect 之后往往还会多打印一行「报告已写入：…」，
    直接 json.loads 会炸，所以只认开头那个对象。
    """
    s = text.lstrip()
    i = s.find("{")
    if i < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[i:])
        return obj
    except Exception:
        return None


def pick(base: Path, *names):
    for name in names:
        for p in (Path(name), base / name, base.parent / name):
            if p.is_file():
                return p
    return None


def pick_dir(base: Path, *names):
    for name in names:
        for p in (Path(name), base / name, base.parent / name):
            if p.is_dir():
                return p
    return None


def short_missing(missing, limit=12):
    if not missing:
        return ""
    if len(missing) <= limit:
        return "、".join(str(x) for x in missing)
    head = "、".join(str(x) for x in missing[:limit])
    return f"{head} …(共{len(missing)}章)"


def main() -> int:
    ap = argparse.ArgumentParser(description="一段速读的收尾校验 + HTML 生成")
    ap.add_argument("--dir", help="段目录（含 速读稿.md），如 第1583-1678章")
    ap.add_argument("--digest", help="直接指定速读稿.md，替代 --dir")
    ap.add_argument("--index", help="chapters.tsv，默认在段目录/上级自动找")
    ap.add_argument("--source", help="原文目录或整本 txt，默认自动找 原文/")
    ap.add_argument("--range", help="本段章号范围，如 1583-1678（覆盖率只查这段）")
    ap.add_argument("--characters", help="人物档案.md，默认自动找")
    ap.add_argument("--title", help="HTML 标题，默认取速读稿 H1")
    ap.add_argument("--out", help="HTML 输出路径，默认与速读稿同名 .html")
    ap.add_argument("--no-coverage", action="store_true", help="跳过覆盖率检查")
    ap.add_argument("--no-slices", action="store_true", help="跳过切片逐字校验")
    ap.add_argument("--no-html", action="store_true", help="不生成 HTML")
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

    summary: dict = {"digest": str(digest), "checks": {}, "errors": []}

    # --- 1) 覆盖率 ---
    if not args.no_coverage:
        if not index:
            summary["checks"]["coverage"] = {"skipped": "没找到 chapters.tsv"}
        else:
            out_md = base / "覆盖率报告.md"
            cmd = ["--index", str(index), "--doc", str(digest), "--out", str(out_md), "--json"]
            if args.range:
                cmd += ["--range", args.range]
            code, so, se = run("coverage_check.py", cmd)
            cov = load_json(so) or {"error": (se or so).strip()[:300]}
            cov["exit"] = code
            summary["checks"]["coverage"] = cov

    # --- 2) 切片逐字 ---
    if not args.no_slices:
        if not source:
            summary["checks"]["slices"] = {"skipped": "没找到原文目录（--source 原文/）"}
        else:
            cmd = ["--digest", str(digest), "--source", str(source),
                   "--collect", str(base / "原文切片.md"), "--json"]
            if index:
                cmd += ["--index", str(index)]
            code, so, se = run("verify_slices.py", cmd)
            sl = load_json(so) or {"error": (se or so).strip()[:300]}
            sl["exit"] = code
            sl.pop("results", None)          # 明细太长，只留计数
            summary["checks"]["slices"] = sl

    # --- 3) HTML ---
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

    fail = 0
    cov = summary["checks"].get("coverage") or {}
    if cov and "total" in cov and cov.get("covered") != cov.get("total"):
        fail = 1
    sl = summary["checks"].get("slices") or {}
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

    # --- 人读摘要：默认只回一小段 ---
    lines = [f"{'✅' if fail == 0 else '⚠️'} 收尾检查 · {base.name}"]

    if cov.get("skipped"):
        lines.append(f"· 覆盖率：跳过 — {cov['skipped']}")
    elif cov.get("error"):
        lines.append(f"· 覆盖率：脚本报错 {cov['error']}")
    elif "total" in cov:
        miss = cov.get("missing") or []
        mark = "✅" if not miss else "❌"
        lines.append(f"· 覆盖率 {mark} {cov.get('covered')}/{cov.get('total')}"
                     f"（{cov.get('rate', 0):.0%}）"
                     + (f" 漏章：{short_missing(miss)}" if miss else ""))

    if sl.get("skipped"):
        lines.append(f"· 切片校验：跳过 — {sl['skipped']}")
    elif sl.get("error"):
        lines.append(f"· 切片校验：脚本报错 {sl['error']}")
    elif "slices" in sl:
        mark = "✅" if not sl.get("fail") else "❌"
        lines.append(f"· 切片校验 {mark} 共 {sl['slices']} 条 → 通过 {sl.get('pass')}"
                     f"，告警 {sl.get('warn')}，失败 {sl.get('fail')}"
                     f"｜占原文 {sl.get('ratio', 0):.1%}")
        if sl.get("fail"):
            code, so, _ = run("verify_slices.py",
                              ["--digest", str(digest), "--source", str(source)])
            bad = [l.strip() for l in so.split("\n") if "❌" in l or "⚠️" in l]
            lines += [f"    {b}" for b in bad[:8]]

    if "html" in summary["checks"]:
        h = summary["checks"]["html"]
        if h.get("error"):
            lines.append(f"· HTML：生成失败 {h['error']}")
        else:
            kb = h.get("bytes", 0) / 1024
            lines.append(f"· HTML ✅ {Path(h.get('html', '')).name}（{kb:.0f} KB）"
                         f"｜单元 {h.get('units')} · 可跳转章 {h.get('chapters')}"
                         f" · 人物 {h.get('chars')}")
            for w in h.get("warnings") or []:
                lines.append(f"    ⚠️ {w}")

    made = []
    if isinstance(summary["checks"].get("html"), dict) and summary["checks"]["html"].get("html"):
        made.append(Path(summary["checks"]["html"]["html"]).name)
    if cov and "total" in cov:
        made.append("覆盖率报告.md")
    if sl and "slices" in sl:
        made.append("原文切片.md")
    if made:
        lines.append("产出：" + " · ".join(made))
    print("\n".join(lines))
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
