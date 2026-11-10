# 15 秒成片：一次生成 vs A/B-roll 素材池

* Author: Shane
* Date: 2026-11-09
* 状态：BRAINSTORM 输入，未实现，未付费验证

## 1. 背景与目标

Synframe 是一个带货短视频生产工具（项目说明见 README 与 skills/synframe/SKILL.md）。当前商品：TikTok 墨西哥站手指投影灯，万圣节 12 颗装，es-MX 口播。产品身份只能来自商品页同 SKU 原图。

用户的新判断：一条好的带货视频 15 秒就够，要有吸引人的 Hook、充满激情、少讲解、直接展示满屋彩色投影、多切镜头。用户的问题：既然只要 15 秒，而且模型能在一次生成里自己切多个镜头，是否直接用 MiniMax H3 一次生成 15 秒就够了？A-roll / B-roll 还有没有价值？

目标规模：每个商品约 20 条可带货的 15 秒成片，成本低、速度快，产品身份和投影因果不出错。

## 2. 现在的做法（已跑通）

1. 每个镜头先用 tt-image-2.6 抽关键帧（0.029/张），选好的图作 H3 参考生（cankaosheng）模式的参考图。
2. A-roll：同一个达人对镜口播，H3 按台词时长生成（按秒计费，只生成需要的秒数）。
3. B-roll：4 秒静音镜头（天花板投影、派对、家庭、产品袋），H3 生成。
4. 本地 ffmpeg 按词级时间码组装：A-roll 是音轨主轴，B-roll 盖画面。

最新成片 cut15-v2：15.0 秒，13 个镜头，来自 7 个已生成片段（A1 15 秒、A2 8 秒、A3 10 秒、B1/B5/B6/B7 各 4 秒），共生成 49 秒素材，另有 B4 4 秒因因果错误废弃。剪辑脚本：examples/cut15-20261109/cut15.py，剪辑表 cut15-v2.json。

## 3. 证据（全部来自本项目实测）

| 证据 | 数据 | 位置 |
|---|---|---|
| H3 一次生成 15 秒会自己切镜 | A1 在 3.583 秒、10.333 秒自动切镜，共 3 个镜头 | examples/wave1-video-20261109/assembly/qc-report.md |
| 一次生成 15 秒的口播风险 | A1 前 3.58 秒口播是乱码（"para Han You Shop Pro Free Charchon"），后面正常 | 同上，qc/A1-asr.json |
| 按台词时长生成可消除乱码填充 | A2 8 秒、A3 10 秒，ASR 与台词逐词一致 | examples/wave2-video-20261109/qc/*-asr.json，synframe/planning.py aroll_duration |
| 生成耗时随时长上升 | A1 15 秒生成 895 秒；4 秒 B-roll 约 145 秒；DMC 排队约 1200 秒 | wave1 qc-report.md |
| 自拍近景和天花板投影难同框 | 任务 212435508（H3 8 秒）把投影打在沙发和毛衣上，没有天花板 | docs/designs/fast-batch-hypothesis.md 第 5 节 |
| B-roll 失败率 | 第一波 3 条 B-roll 有 1 条（B4）光束方向与投影图案不符，废弃；第二波 2 条全过 | wave1/wave2 qc |
| 人物一致 | 天花板镜头那条（paid-finger-light-ceiling）是另一个女生，成片里没用 | examples/paid-finger-light-ceiling-20261109 |
| 参考爆款节奏 | 15 秒，约 9 刀，连续口播配字幕 | examples/reference-competitor-20261109 |
| 价格 | H3 DMC 0.1123/秒（15 秒约 1.68）；账本里第二波实际按 0.1498/秒；余额实际下降比账本多约 40% | docs/designs/pricing-snapshot-20261109.json，wave2 balance-drift.md |
| 其他模型 | omni_flash-10s 按次 0.9547 固定 10 秒；seedance-2.0-anci 按次 7.2 起，4-15 秒；omni-flash XT 按秒 0.1031，4/6/8/10 秒 | pricing-snapshot-20261109.json |

## 4. 待评审的三个方案

**方案 A：一次生成。** 每条成片 = 1 次 H3 15 秒生成。提示词里写好分秒镜头表（Hook、佩戴、墙面、天花板、派对、CTA），口播一口气说完，原声保留。本地只加字幕、大字、音乐。每条约 1.68（按 DMC 价）。

**方案 B：素材池重组（现状）。** 一次生成一批 A-roll 和 B-roll，本地重组成多条成片。

**方案 C：一次生成为主，B-roll 小库兜底。** 每条成片的底片是一次生成的 15 秒（连续人声和多镜头）。另外为每个商品建一个很小的已验收 B-roll 库（如广角满天花板投影、产品袋特写）。底片里哪几秒出错（乱码、产品变形、因果错误、人物漂移），就用库里镜头盖住那几秒画面，保留原声，不整条重做。

## 5. 请评审回答的问题

1. 按"每条可带货成片"的实际成本算（含失败重做），20 条规模下 A、B、C 谁更便宜？一次生成里只要有 1 秒坏了，A 方案是不是只能整条重做？
2. A-roll / B-roll 分工在 15 秒格式里还剩什么价值？是"生成方式"还是只剩"剪辑时的角色"（音轨主轴 vs 画面覆盖）？
3. 一次生成能否稳定做到：Hook 前 1.5 秒就是满屋投影、人物和产品前后一致、口播完整无乱码、9 个左右镜头？目前只有 A1 一条样本。
4. 20 条之间的差异从哪来？A 方案每条都是独立生成，差异大但不可控；B 方案重组，可控但可能雷同、被平台判重。
5. 速度：A 方案每条排队约 20 分钟 + 生成约 15 分钟，20 条并行时是否撞并发或渠道限流？
6. 最小验证实验应该怎么设计，花多少钱，用什么指标决定选哪个方案？

## 6. 固定约束

- 每次付费提交前核对余额和价格，不自动重试，每个候选只提交一次。
- 先用 tt-image-2.6 出图再做视频（用户要求，为了降成本）。
- es-MX 口播，同一条视频里口播必须是同一个人。
- 商品宣称只用已核实的：12 颗装、彩色随机图案、万圣节主题。不提价格、续航、耐用、覆盖面积。灯不能对着眼睛或镜头。
- 新产物写新目录，不覆盖。技术 QC、创意验收、听审分开报告。

## 7. 阅读清单

- README.md
- skills/synframe/SKILL.md
- docs/designs/fast-batch-hypothesis.md
- docs/designs/pricing-snapshot-20261109.json
- synframe/planning.py
- synframe/channels.py
- synframe/recombine.py
- examples/wave1-video-20261109/assembly/qc-report.md（私有素材，未随仓库发布）
- examples/wave2-video-20261109/run_video2.py（私有素材，未随仓库发布）
- examples/cut15-20261109/cut15.py（私有素材，未随仓库发布）
- examples/cut15-20261109/cut15-v2.json（私有素材，未随仓库发布）
- examples/cut15-20261109/cut15-v1-contact.jpg（私有素材，未随仓库发布）
