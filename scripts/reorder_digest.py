#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把旧体例速读稿重排成「连贯体例」。

旧体例（两种写法都见过）：
  A. 单元内先 `**【重要】**` 再 `**【过渡】**`，条目形如 `- **第1292章 标题**：正文`
     或 `- **标题（第 1285 章）**：正文`
  B. 行内前缀 `【重要】标题（第1679-1682章）：正文` / `【过渡】第1686章是……：正文`
读者读完重要章要倒回去读前面的过渡章，章号来回跳。

新体例：单元内是**按章号升序的单一列表**，条目开头带级别标签：

    - **第1285章（详） 金刚诀第四层瓶颈被「打」破**：……
    - **第1280–1281章（略）**：……
    - **第1340、1341、1342章（详） 拍卖场：三枚墨麒麟鳞片**：……

原文切片（`**【原文·第N章 章名】**` + 引用行）插到所属章条目之后，跟着章走。
条目可以是一个多段块（首行 + 其后的子列表/续段），整块一起参与排序。

用法：
    python3 reorder_digest.py --in 速读稿.md --dry-run      # 只报告
    python3 reorder_digest.py --in 速读稿.md --out 新.md
    python3 reorder_digest.py --in 速读稿.md --in-place      # 备份为 *.旧体例.md
"""
import argparse
import re
import sys
from pathlib import Path

H2_RE = re.compile(r'^##\s')
UNIT_RE = re.compile(r'^##\s*单元')
SECT_RE = re.compile(r'^\*\*【(重要|过渡)】\*\*\s*$')
SLICE_RE = re.compile(r'^\*\*【原文[·・:：]?\s*([^】]*)】\*\*\s*$')
END_RE = re.compile(r'^\*\*这一段结束时\*\*')
ITEM_RE = re.compile(r'^[-*+]\s+')
QUOTE_RE = re.compile(r'^\s*>')
SEC_INLINE_RE = re.compile(r'^【(重要|过渡)】\s*(.*)$', re.S)

HEAD_RE = re.compile(r'^\*\*(.+?)\*\*\s*([（(][^）)]*[）)])?\s*[：:]\s*(.*)$', re.S)
NUMHEAD_RE = re.compile(r'^第\s*([0-9][0-9、,，和及\s–—~～\-至到]*?)\s*章\s*(.*)$', re.S)
TAILNUM_RE = re.compile(r'^(.*?)\s*[（(]第\s*([0-9][0-9、,，和及\s–—~～\-至到]*?)\s*章[^）)]*[）)](.*)$', re.S)
DONE_RE = re.compile(r'^\*\*第[^*]*[（(](详|略)[）)]')
RANGE_RE = re.compile(r'(\d{1,6})\s*[–—~～\-至到]\s*(\d{1,6})')
NUM_RE = re.compile(r'\d{1,6}')

LVL = {'重要': '详', '过渡': '略'}
BIG = 10 ** 7


def chapter_numbers(s: str):
    """从章号片段里抽出全部章号（范围展开）。"""
    nums = set()

    def repl(m):
        a, b = int(m.group(1)), int(m.group(2))
        if b > a and b - a <= 300:
            nums.update(range(a, b + 1))
        else:
            nums.update((a, b))
        return ' '

    rest = RANGE_RE.sub(repl, s)
    nums.update(int(x) for x in NUM_RE.findall(rest))
    return nums


def _clean_chpart(s: str) -> str:
    return re.sub(r'\s+', '', s or '')


def parse_item_entry(text: str):
    """A 类条目行（`- **…**：…`）→ (nums, chpart, title, after)。"""
    m = HEAD_RE.match(text)
    if not m:
        return None
    head = m.group(1).strip()
    paren = (m.group(2) or '').strip()
    body = m.group(3).strip()
    if paren and len(paren) <= 16:      # `（S）`/`（打斗压缩）` 这类短注并进标题
        head += paren
        paren = ''
    after = (paren + '：' + body) if paren else '：' + body

    m2 = TAILNUM_RE.match(head)
    if m2:
        title, chpart = m2.group(1).strip(), m2.group(2)
    else:
        m1 = NUMHEAD_RE.match(head)
        if not m1:
            return None
        chpart, title = m1.group(1), m1.group(2).strip()
        if title.startswith('的'):
            title = title[1:].strip()
    chpart = _clean_chpart(chpart)
    nums = chapter_numbers(chpart)
    if not nums:
        return None
    return nums, chpart, title, after


def parse_sec_entry(text: str):
    """B 类条目行（`【重要】…`）→ (lvl, nums, chpart, title, after)。"""
    m = SEC_INLINE_RE.match(text)
    if not m:
        return None
    lvl = LVL[m.group(1)]
    rest = m.group(2).strip()
    m2 = TAILNUM_RE.match(rest)
    if m2:
        title, chpart, after = m2.group(1).strip(), m2.group(2), m2.group(3).strip()
    else:
        m3 = NUMHEAD_RE.match(rest)
        if not m3:
            return None
        chpart, tailtxt = m3.group(1), m3.group(2).strip()
        if '：' in tailtxt:
            title, aft = tailtxt.split('：', 1)
            title, after = title.strip(), '：' + aft.strip()
        else:
            title, after = tailtxt, ''
        if title.startswith('是'):
            title = title[1:].strip()
    chpart = _clean_chpart(chpart)
    nums = chapter_numbers(chpart)
    if not nums:
        return None
    return lvl, nums, chpart, title, after


def _render_entry_block(b):
    """条目块 → 新体例行列表。"""
    lines = list(b['lines'])
    if not b.get('ok'):
        return _trim(lines)
    head = f"- **第{b['chpart']}章（{b['lvl']}）"
    if b['title']:
        head += f" {b['title']}"
    head += '**' + b.get('after', '')
    out = [head] + lines[1:]
    return _trim(out)


def split_units(lines):
    out, cur_head, cur_body = [], None, []
    for ln in lines:
        if H2_RE.match(ln) and UNIT_RE.match(ln):
            out.append((cur_head, cur_body))
            cur_head, cur_body = ln, []
        else:
            cur_body.append(ln)
    out.append((cur_head, cur_body))
    return out


def reorder_unit(body_lines):
    lead, tail, blocks = [], [], []
    cur = None
    sec = None
    in_tail = False
    st = {'n_item': 0, 'n_slice': 0, 'bad': []}
    n = len(body_lines)
    i = 0
    while i < n:
        raw = body_lines[i]
        s = raw.strip()
        if in_tail:
            tail.append(raw)
            i += 1
            continue
        if END_RE.match(s):
            in_tail, cur = True, None
            tail.append(raw)
            i += 1
            continue
        if SLICE_RE.match(s):
            blk = [raw]
            i += 1
            while i < n and (QUOTE_RE.match(body_lines[i]) or not body_lines[i].strip()):
                if not body_lines[i].strip():
                    k = i
                    while k < n and not body_lines[k].strip():
                        k += 1
                    if k < n and QUOTE_RE.match(body_lines[k]):
                        blk.append('')
                        i = k
                        continue
                    break
                blk.append(body_lines[i])
                i += 1
            nums = chapter_numbers(SLICE_RE.match(s).group(1))
            blocks.append({'kind': 'slice', 'lines': blk, 'nums': nums,
                           'ch': min(nums) if nums else None, 'order': len(blocks)})
            st['n_slice'] += 1
            cur = None
            continue
        if SECT_RE.match(s):
            sec = LVL[SECT_RE.match(s).group(1)]
            cur = None
            i += 1
            continue
        if s.startswith('【重要】') or s.startswith('【过渡】'):
            p = parse_sec_entry(s)
            b = {'kind': 'entry', 'lines': [raw], 'order': len(blocks), 'mode': 'sec',
                 'lvl': p[0] if p else None, 'chpart': p[2] if p else '',
                 'title': p[3] if p else '', 'after': p[4] if p else '',
                 'nums': p[1] if p else set(), 'ok': bool(p)}
            if not p:
                st['bad'].append(('B类条目无法解析', s[:50]))
            st['n_item'] += 1
            blocks.append(b)
            cur = b
            i += 1
            continue
        if sec and ITEM_RE.match(s):
            item = ITEM_RE.sub('', s, count=1)
            if DONE_RE.match(item):
                p = parse_item_entry(item)
                b = {'kind': 'entry', 'lines': [raw], 'order': len(blocks), 'mode': 'item',
                     'lvl': sec, 'nums': p[0] if p else set(),
                     'chpart': p[1] if p else '', 'title': p[2] if p else '',
                     'after': p[3] if p else '', 'ok': bool(p), 'done': True}
            else:
                p = parse_item_entry(item)
                if p:
                    b = {'kind': 'entry', 'lines': [raw], 'order': len(blocks), 'mode': 'item',
                         'lvl': sec, 'nums': p[0], 'chpart': p[1], 'title': p[2],
                         'after': p[3], 'ok': True}
                else:
                    b = {'kind': 'entry', 'lines': [raw], 'order': len(blocks), 'mode': 'item',
                         'lvl': sec, 'nums': set(), 'ok': False}
                    st['bad'].append(('A类条目无法解析', s[:50]))
            st['n_item'] += 1
            blocks.append(b)
            cur = b
            i += 1
            continue
        if cur is not None and cur['kind'] == 'entry':
            cur['lines'].append(raw)
        else:
            lead.append(raw)
        i += 1

    entries = [b for b in blocks if b['kind'] == 'entry']
    slices = [b for b in blocks if b['kind'] == 'slice']

    def key(b):
        return (min(b['nums']) if b['nums'] else BIG,
                0 if b['lvl'] == '详' else 1, b['order'])

    entries.sort(key=key)

    attach, used = {}, [False] * len(slices)
    for si, sl in enumerate(slices):
        if sl['ch'] is None:
            continue
        for b in entries:
            if sl['ch'] in b['nums']:
                attach.setdefault(id(b), []).append(si)
                used[si] = True
                break

    out = _trim(lead)
    for b in entries:
        blk = _render_entry_block(b)
        if not blk:
            continue
        if out and not _joinable(out[-1], blk[0]):
            out.append('')
        out.extend(blk)
        for si in attach.get(id(b), []):
            out.append('')
            out.extend(slices[si]['lines'])
            out.append('')
    for si, ok in enumerate(used):
        if ok:
            continue
        out.append('')
        out.extend(slices[si]['lines'])
        out.append('')
    if tail:
        if out and out[-1].strip():
            out.append('')
        out.extend(_trim(tail))
    return _trim(out), st


def _joinable(prev_last, next_first):
    """两个块之间是否可以不加空行（都是列表项时紧凑排列）。"""
    return prev_last.strip().startswith('- ') and next_first.strip().startswith('- ')


def _trim(rows):
    a = list(rows)
    while a and not a[0].strip():
        a.pop(0)
    while a and not a[-1].strip():
        a.pop()
    return a


def run(path: Path, dry: bool, in_place: bool, out_path):
    lines = path.read_text(encoding='utf-8').splitlines()
    res, stats = [], []
    for head, body in split_units(lines):
        if head is None:
            res.extend(body)
            continue
        new_body, st = reorder_unit(body)
        stats.append((head, st))
        res.append(head)
        res.extend(new_body)
    text = '\n'.join(res) + '\n'

    print(f'== {path}')
    for head, st in stats:
        flag = '  ⚠ ' + '; '.join(f'{k}:{v}' for k, v in st['bad'][:2]) if st['bad'] else ''
        print(f'   {head[:46]:48s} 条目 {st["n_item"]:3d}  切片 {st["n_slice"]:2d}{flag}')
    if dry:
        return text
    if in_place:
        bak = path.with_name(path.stem + '.旧体例.md')
        if not bak.exists():
            bak.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        path.write_text(text, encoding='utf-8')
        print(f'   写入 {path}（备份 {bak.name}）')
    else:
        out_path = out_path or path.with_name(path.stem + '.新.md')
        Path(out_path).write_text(text, encoding='utf-8')
        print(f'   写入 {out_path}')
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='src', required=True)
    ap.add_argument('--out', dest='out')
    ap.add_argument('--in-place', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    run(Path(a.src), a.dry_run, a.in_place, a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
