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
- **水章也必须有交代**：可以一句话带过，但要写清哪几章、为什么、结果如何、有无后患——这是 `coverage_check.py` 兜底的事。
- **原文切片逐字校验**：切片被润色过就不再是原文，`verify_slices.py` 会把它抓出来。
- **长书不靠上下文硬扛**：卡片落盘，逐章提取可并行分派给子代理，几百章也不会把上下文撑爆。
- **断点续跑**：`_进度.md` 记录读到哪、下一步做什么，隔几天回来说一句"继续"就能接上。

## 安装

本仓库根目录就是 skill 本体（`SKILL.md` + `scripts/` + `references/`）。把它放进你所用 Agent 的 skills 目录即可：

```bash
git clone https://github.com/asukaneko/quick-read-skill.git ~/.claude/skills/novel-fast-read
# 或者
git clone https://github.com/asukaneko/quick-read-skill.git ~/.agents/skills/novel-fast-read
```

常见位置：

| 环境 | 目录 |
|---|---|
| Claude Code | `~/.claude/skills/novel-fast-read/` |
| 通用 agent（`.agents` 约定） | `~/.agents/skills/novel-fast-read/` |
| 项目内 | `<project>/.agents/skills/novel-fast-read/` |

> 目录名建议用 `novel-fast-read`（与 `SKILL.md` 里的 `name` 一致）。

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
└── 原文切片.md         # 汇总后的关键原文，供"只想细读"
```

## 脚本

只用 Python 标准库（Python 3.8+），`python3` 直接跑，无需安装依赖。

| 脚本 | 作用 |
|---|---|
| `scripts/split_chapters.py` | 章节识别与切分、生成索引；自动猜编码（UTF-8/GB18030…）、清理盗版站广告行、识别卷/篇结构；认不出会告警并退化为定长分块 |
| `scripts/verify_slices.py` | 校验原文切片是否**逐字**来自原文，定位到章，并可汇总成切片合集 |
| `scripts/coverage_check.py` | 覆盖率兜底：列出一整段里"没被交代过"的章节 |

```bash
SKILL_DIR=~/.claude/skills/novel-fast-read

# 切分整本小说并建索引
python3 "$SKILL_DIR/scripts/split_chapters.py" --input 全书.txt --outdir 阅读工作区/书名 --split

# 先试探能不能识别章节（不写文件）
python3 "$SKILL_DIR/scripts/split_chapters.py" --input 全书.txt --dry-run

# 校验速读稿里的切片是否逐字来自原文
python3 "$SKILL_DIR/scripts/verify_slices.py" \
  --digest 阅读工作区/书名/速读稿.md \
  --source 阅读工作区/书名/原文 \
  --index 阅读工作区/书名/chapters.tsv \
  --collect 阅读工作区/书名/原文切片.md

# 检查有没有漏讲的章节
python3 "$SKILL_DIR/scripts/coverage_check.py" \
  --index 阅读工作区/书名/chapters.tsv \
  --doc 阅读工作区/书名/速读稿.md
```

切片在 Markdown 里必须写成这个格式，脚本才认：

```markdown
【原文·第12章 密室对峙】
> 「逐字原文……」
```

## 目录

```
SKILL.md                       # 主流程：两种模式、铁律、六步执行、详略分级
references/card-template.md    # 分章卡片模板 + 正反例
references/digest-template.md  # 速读稿模板 + 详略分级示例
references/bridge-template.md  # 衔接包模板 + 「断片变数清单」
references/splitting.md        # 章节切分、编码、自定义正则与异常处理
scripts/                       # 三个校验/切分脚本
```

## 设计来源

在做法上参考了若干社区 skill 的思路：长文档分章笔记与来源锚定、并行逐章 digest 与缓存、阅读伴侣的剧透边界、分阶段流水线与断点续跑。本仓库把它们整合成"为读者服务"的单一流程，并补上了切片逐字校验与覆盖率校验。

## 使用须知

- 请只对你**有权使用**的文本运行本 skill（自有文档、公版作品、已获授权的内容等）。
- 速读稿的定位是「摘要 + 少量原文引用」，不是复制整本书。请遵守你所在地区的版权规定。
- 模型的输出仍可能有误。切片可用 `verify_slices.py` 校验，但摘要性陈述需要你自己判断。

## License

[MIT](LICENSE)
