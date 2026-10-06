# quick-read-skill · 小说速读与跳章衔接

一个给 AI Agent 用的 skill：把长篇小说的原文交给 AI 通读，产出**能替代原著阅读的速读稿**；或者在你想跳过中间若干章时，产出**能无缝接上后续剧情的衔接包**。

不是"把小说喂给大模型要个摘要"，而是一套有工作区、有校验、能断点续跑的阅读流水线。

## 两种模式

| 模式 | 你的处境 | 交付物 |
|---|---|---|
| **A · 全文速读** | 没读过 / 想快速过一遍 / 想重温 | `速读稿.md`：重要剧情详写并附**逐字原文切片**；次要剧情压缩但**讲清因果来龙去脉** |
| **B · 跳章衔接** | 已读到第 N 章，跳过中间，从第 M 章继续读原文 | `衔接包.md`：跳过区的因果桥接 + 进入第 M 章前的状态交接，**不剧透第 M 章之后** |

两者可以叠加：「我从第 80 章就没看了，后面帮我讲完」＝ 入口桥接 + 范围速读。

## 核心思路

摘要之所以常常"读完还是接不上"，是因为它只记了**发生了什么**，没记**现在处于什么状态**。真正让人接得上的信息是：谁在哪、和谁什么关系、**知道什么不知道什么**、身上欠着什么、哪些雷还悬着。

所以这个 skill 的流程是：

```
原文落盘 → 逐章卡片（只记"变化"）→ 聚合状态账本 → 速读稿 / 衔接包 → 机器校验
```

- **状态优先于事件**：每章卡片必须写「谁从什么状态变成了什么状态」。
- **详略由用户偏好决定**，不由"文学价值"决定：你说不想看打斗，三千字的决战就该压成三句。
- **水章也必须有交代**：可以一句话带过，但要写清哪几章、为什么、结果如何、有无后患——这是覆盖率校验兜底的事（`check.py`）。
- **原文切片逐字校验**：切片被润色过就不再是原文，`check.py` 会把它抓出来。
- **长书不靠上下文硬扛**：卡片落盘，逐章提取可并行分派给子代理，几百章也不会把上下文撑爆。
- **断点续跑**：`_进度.md` 记录读到哪、下一步做什么，隔几天回来说一句"继续"就能接上。

## 安装

本仓库根目录就是 skill 本体（`SKILL.md` + `scripts/` + `references/`）。把它放进你所用 Agent 的 skills 目录即可：

```bash
git clone https://github.com/asukaneko/quick-read-skill.git ~/.claude/skills/quick-read
# 或者
git clone https://github.com/asukaneko/quick-read-skill.git ~/.agents/skills/quick-read
```

常见位置：

| 环境 | 目录 |
|---|---|
| Claude Code | `~/.claude/skills/quick-read/` |
| 通用 agent（`.agents` 约定） | `~/.agents/skills/quick-read/` |
| 项目内 | `<project>/.agents/skills/quick-read/` |

> 目录名建议用 `quick-read`（与 `SKILL.md` 里的 `name` 一致）。

## 用法

直接说人话就行，skill 会自己判断模式、问清缺失的关键信息：

```
这本《XXX》太长了帮我快速过一遍，打斗不想看，感情线讲细一点
我看到 120 章了，中间跳过，直接看 200 章，给我个能接上的前情提要
从第 80 章开始我就没看了，后面帮我讲完
这段支线我不想看，告诉我后面接着发生什么
把主角复仇这条线讲全，其他的简单带过
```

长篇会建立这样的工作区，随时可以中断和续跑：

```
阅读工作区/{书名}/
├── _进度.md            # 断点：模式、偏好、已读到第几章、下一步
├── 章节索引.md / chapters.tsv
├── 原文/               # 原文按章落盘（切片与核对的唯一依据）
├── 分章卡片/           # 逐章状态提取
├── 人物档案.md         # 含别名归一表（换马甲/道号/化名）
├── 伏笔与悬念.md
├── 故事状态.md         # 剧情单元级状态快照
├── 速读稿.md / 衔接包.md
├── 速读稿.html         # 由 build_html.py 生成，交付给用户读的就是它
└── 原文切片.md         # 汇总后的关键原文，供"只想细读"
```

## 脚本

只用 Python 标准库（Python 3.8+），`python3` 直接跑，无需安装依赖。

| 脚本 | 作用 |
|---|---|
| `scripts/split_chapters.py` | 章节识别与切分、生成索引；自动猜编码（UTF-8/GB18030…）、清理盗版站广告行、识别卷/篇结构；认不出会告警并退化为定长分块 |
| `scripts/verify_slices.py` | 校验原文切片是否**逐字**来自原文，定位到章，并可汇总成切片合集 |
| `scripts/coverage_check.py` | 覆盖率兜底：列出一整段里"没被交代过"的章节 |
| `scripts/check.py` | **一步收尾校验**：覆盖率 + 切片逐字 + 生成 HTML，单进程一次跑完；原文归一化缓存于 `原文/.normcache`，几百万字的书重跑也是亚秒级 |
| `scripts/finalize.py` | 旧版收尾（三个子进程串跑、无缓存），日常用 `check.py` 即可 |
| `scripts/build_html.py` | 把速读稿渲染成**单文件 HTML 阅读器**：单元导航、章节速查、人物链接、阅读进度缓存、响应式 |
| `scripts/build_shelf.py` | 把**多本书的多份**速读稿 HTML 汇总成**书架式导航页**：一本书多册、卡片经相对路径跳转（不合并正文）、搜索、深色、进度角标 |
| `scripts/make_plan.py` | 把章节索引切成可并行派单的批次，并为每批写一份自包含的派单文件 |

```bash
SKILL_DIR=~/.claude/skills/quick-read

# 切分整本小说并建索引
python3 "$SKILL_DIR/scripts/split_chapters.py" --input 全书.txt --outdir 阅读工作区/书名 --split

# 先试探能不能识别章节（不写文件）
python3 "$SKILL_DIR/scripts/split_chapters.py" --input 全书.txt --dry-run

# 一条命令收尾：覆盖率 + 切片逐字 + 生成 HTML（原文归一化有缓存，改完随手重跑）
python3 "$SKILL_DIR/scripts/check.py" \
  --dir 阅读工作区/书名 --range 1-320 --source 阅读工作区/书名/原文

# 单项细查才手敲下面两条（check.py 内部复用同一套规则，平时用不到）
python3 "$SKILL_DIR/scripts/verify_slices.py" \
  --digest 阅读工作区/书名/速读稿.md \
  --source 阅读工作区/书名/原文 \
  --index 阅读工作区/书名/chapters.tsv \
  --collect 阅读工作区/书名/原文切片.md

# 检查有没有漏讲的章节
python3 "$SKILL_DIR/scripts/coverage_check.py" \
  --index 阅读工作区/书名/chapters.tsv \
  --doc 阅读工作区/书名/速读稿.md

# 把多本书的速读稿汇总成一个书架页（扫描目录自动收录）
python3 "$SKILL_DIR/scripts/build_shelf.py" \
  --root 阅读工作区 --out 阅读工作区/书架.html

# 或者用 书架.md 显式编排书的顺序、册名与简介
python3 "$SKILL_DIR/scripts/build_shelf.py" --manifest 阅读工作区/书架.md
```

## HTML 速读页

`build_html.py` 把速读稿渲染成一份**单文件 HTML 阅读器**，直接发给用户就能当书读：

```bash
python3 "$SKILL_DIR/scripts/build_html.py" \
  --digest 阅读工作区/书名/速读稿.md \
  --characters 阅读工作区/书名/人物档案.md \
  --index 阅读工作区/书名/chapters.tsv \
  --progress-key 书名-1-320
```

- **单元导航**：侧栏列出各单元与「总览 / 附录」，搜索框可同时筛单元、章节、人物。
- **章节速查**：有 `chapters.tsv` 时列出全段每一章，点击直达该章所在单元；有切片的章直达切片。
- **人物链接**：正文人名可点，弹出《人物档案》里该人物条目（窄屏为底部抽屉）。
- **字号调节**：顶栏 `A-` / `A+` 缩放正文字号（0.8–1.6 倍），`localStorage` 记住选择。
- **阅读进度**：`localStorage` 记住所在单元与滚动位置，刷新或重开自动回到原处，可一键清除。
- **响应式**：宽屏左栏＋限宽正文，窄屏折叠为抽屉；跟随系统深色主题；零外链、可离线打开。

模型只负责按体例产出 markdown（单元标题、`**【原文·第N章 章名】**` 切片、`### 人名` 人物条目），HTML 全部交给脚本；输入契约与告警处理见 `references/html-output.md`。

切片在 Markdown 里必须写成这个格式，脚本才认（加粗写法同样识别）：

```markdown
【原文·第12章 密室对峙】
> 「逐字原文……」
```

## 书架页

追多本书、或一本书分段交付之后，`build_shelf.py` 把所有已生成的
`速读稿.html` / `衔接包.html` 汇总成一张**书架式导航页**：

```bash
python3 "$SKILL_DIR/scripts/build_shelf.py" --root 阅读工作区
# → 阅读工作区/书架.html
```

- **结构即需求**：一个书架有多本书，一本书多个速读稿（各负责一部分章节）；点击书下的卡片，经**相对路径**跳到对应速读稿——正文不合并在书架页里，各册 HTML 仍是独立文件。
- **两种收录**：给 `--manifest 书架.md` 按清单编排（顺序、册名、简介可控）；或给 `--root` 递归扫描 `速读稿.html` / `衔接包.html`，按《书名》或一级子目录自动归组成书。
- **卡片信息**：册号、章号范围、单元/可跳转章统计，都从目标阅读器里自动读取；目标文件缺失时卡片降级并告警，不会静默丢链接。
- **阅读进度角标**：同源部署（GitHub Pages、本地 http 服务）下，卡片会显示「读到 N%」的丝带书签，顶栏出现「继续阅读」直达最近在读的一册；`file://` 双击打开时浏览器限制跨文件读进度，角标自动隐藏，其余功能不受影响。
- **宽窄屏**：宽屏每行多卡片、窄屏自动单列；深色主题与单书阅读器共用同一份偏好；支持搜索过滤与书本折叠。

书架清单 `书架.md` 长这样（完整契约见 `references/shelf-output.md`）：

```markdown
# 我的速读书架

## 《凡人修仙传》
忘语 · 修仙

- 第1–320章：凡人修仙传/第1-320章/速读稿.html
- 第321–640章：凡人修仙传/第321-640章/速读稿.html

## 《诡秘之主》
- [卷一 小丑](诡秘之主/卷一/速读稿.html)
```

## 目录

```
SKILL.md                       # 主流程：定段 → 并发读卡 → 聚合 → 校验 → 交付 HTML → 回写台账
reference.md                   # 速读段体例清单速查：单元、切片、人物档案、伏笔四栏、收尾命令
references/card-template.md    # 分章卡片模板 + 正反例
references/digest-template.md  # 速读稿模板 + 详略分级示例
references/bridge-template.md  # 衔接包模板 + 「断片变数清单」
references/splitting.md        # 章节切分、编码、自定义正则与异常处理
references/html-output.md      # HTML 速读页的输入契约、参数与告警
references/shelf-output.md     # 书架页的清单格式、扫描规则、参数与告警
references/parallel-dispatch.md# 并行派单手册：批次粒度与子代理调度
scripts/                       # 切分、校验、收尾、批次计划、HTML 与书架页生成（8 个脚本）
```

## 设计来源

在做法上参考了若干社区 skill 的思路：长文档分章笔记与来源锚定、并行逐章 digest 与缓存、阅读伴侣的剧透边界、分阶段流水线与断点续跑。本仓库把它们整合成"为读者服务"的单一流程，并补上了切片逐字校验与覆盖率校验。

## 使用须知

- 请只对你**有权使用**的文本运行本 skill（自有文档、公版作品、已获授权的内容等）。
- 速读稿的定位是「摘要 + 少量原文引用」，不是复制整本书。请遵守你所在地区的版权规定。
- 模型的输出仍可能有误。切片可用 `check.py` 校验，但摘要性陈述需要你自己判断。

## License

[MIT](LICENSE)
