# 速读段体例清单（模板）

## 目录结构
```
第X-Y章/
  _头部.md          # 起读提示：你现在的位置、全书概览
  _尾部.md          # 读完索引：人物现状表、十条悬念、接着读
  速读稿.md         # 主交付物 ＝ _头部 + 段A/B/C + _尾部
  速读稿.html       # 由 scripts/build_html.py 生成，交付给用户读的就是它
  段/段A_单元01-03.md 段/段B_… 段/段C_…
  单元摘要/单元01.md …
  分章卡片/第NNNN章.md
  人物档案.md  故事状态.md  伏笔与悬念.md  原文切片.md  覆盖率报告.md
  章节索引.tsv（或 chapters.tsv）  原文/
```

## 单元正文体例
1. `## 单元NN｜<标题>（第 X–Y 章）`
2. `**【过渡】**`：无状态跃变的铺垫章，写明这几章在铺什么
3. `**【原文·第NNNN章 逐字章名】**`：切片，后续为解读
4. 段末 `**这一段结束时**`：位置、境界、目标、底牌、未了账目、悬而未决

> HTML 版依赖 1 与 3 这两条：单元标题决定侧栏导航，切片决定「章节速查」能跳到具体章。
> `## 读完索引` 之类非单元二级标题会进「总览 / 附录」组，不受影响。

## 人物档案体例
- `## 别名归一表`：表头必须含「本名」，别名列（马甲/尊称/别名/别称）用 `、` `,` `/` 分隔。
- `## 人物` 下每人一个 `### 人名`（人名需命中别名表），条目内容原样进 HTML 人物抽屉。
- 这两条不满足时 HTML 只是没有人名链接，正文与文本版不受影响。

## 伏笔四栏
- 一、还开着的（首次埋设章号＋逐字章名＋现状与影响）
- 二、已回收的（段内 / 【跨段】子栏，写明埋与揭晓的章）
- 三、未兑现的承诺与威胁（谁对谁、内容、章号）
- 四、信息差（谁已知／谁尚不知）

## 收尾：一条命令
```bash
python3 scripts/finalize.py --dir 第X-Y章 --range X-Y --source 原文/
# 等价于依次跑 coverage_check.py → verify_slices.py → build_html.py，
# 只在末尾回一小段摘要（覆盖率 / 切片通过数 / HTML 体量），失败才展开明细。
python3 scripts/finalize.py --dir 第X-Y章 --range X-Y --source 原文/ --json   # 机器可读
```

## 校验命令备忘（需要单项细查时才手敲）
```bash
wc -m 段/*.md; grep -c '^\*\*【原文·' 段/*.md
grep -n '^## ' 速读稿.md
grep -c '跨段' 速读稿.md          # 应为 0
python3 scripts/coverage_check.py --index chapters.tsv --doc 第X-Y章/速读稿.md --range X-Y --out 第X-Y章/覆盖率报告.md
python3 scripts/verify_slices.py --digest 第X-Y章/速读稿.md --source 原文/ --index chapters.tsv --collect 第X-Y章/原文切片.md
python3 scripts/build_html.py --digest 第X-Y章/速读稿.md --json
```

## 断点与台账
- 台账：`README.md`、`_进度.md` — 记本段范围、覆盖率、切片 PASS 数、资料线状态、下一段起读章（断点章＋1）。
- 续做口令：用户说「继续速读《书名》，从第 X 章开始」即按本技能从第 1 步起新一轮。
- HTML 进度键：同一本书各段建议统一 `--progress-key <书名拼音>-<起章>`，跨段阅读进度不打架。
