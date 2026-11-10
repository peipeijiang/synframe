# hook-pool-batch.md Goldfish 评审汇总

* 日期：2026-11-09
* 评审：EGM 0.1.1 四个视角（zoom_out、design_critic、implementation_readiness、codebase_details），通过 Claude Code 只读执行，每个评审为全新上下文。
* 结论：四份都判 v1 "not ready"。处理后的设计见 hook-pool-batch-v2.md。
* 偏差：没用原生子 agent（上一轮遇到线程上限），改用 Claude Code 委派；EGM 逐节确认被跳过。

## 采纳并已修改

| 问题 | 来源 | 处理 |
|---|---|---|
| 未说明与 concepts-brief 的分歧，丢了对账、验收清单、小测三道门 | 四份都提 | v2 第 2 节逐条说明；第 12 节恢复门槛 |
| 口播 prepend Hook 与主体可能是两个模型、两种音色，persona 只是标签 | design_critic、readiness、zoom_out | 代码强制 (persona, model) 一致；有口播段缺 model 直接报错；默认 overlay |
| 接缝只靠抽样看 | design_critic | sequence 输出 listen_checklist，每个接缝听一次 |
| hook 唯一是最贵的规则但没证据 | zoom_out | 新增 hook_cap，默认 1；>1 时同一 Hook 不接同一主体两次 |
| 时长预算加起来超窗 | design_critic、readiness | v2 按模式给出主体上限；凑不满时返回 shortfall_reason |
| 成本偏乐观：成功率引错、DMC 不能选、关键帧数量错、差额未计入、CTA 秒数没算 | 四份都提 | v2 第 10 节重算：账面 20.6-28.2，含差额 31-47 |
| 同次生成切出的兄弟 Hook 会互相判重 | design_critic、codebase | 引入 family；家族内查重放到段验收 |
| 成片自动检查没有通过条件 | readiness、codebase | qc.py 实现：关键词、ASR 差异 ≤2、-14±1 LUFS、峰值 ≤-1、blackdetect、时长 |
| fastcut 绕过 timeline/OTIO | zoom_out | 新增 to_tracks，sequence --output 同时写 tracks.json |
| 段库格式未定义 | readiness | v2 第 4 节给出格式和校验规则 |
| 尾卡来源未定义 | readiness | 尾卡是 CTA 段声明的 pad 源，最长补到 15 秒 |
| 免费样片验证不了主体和 CTA 轮换 | readiness、codebase | v2 第 12 节写明样片能验证什么、不能验证什么 |
| Hook 路线写成 H3 cankaosheng，与 omni_flash-10s 参数不符 | codebase | v2 第 5 节分开写两条路线 |
| 新建 synframe-sequence 子 skill 与 assembly 重叠 | zoom_out | 并入 synframe-assembly |

## 部分采纳或不采纳

| 意见 | 处理与理由 |
|---|---|
| 把 sequence 合并进 recombine，只留一个挑选器（zoom_out P1） | 不合并。两者约束不同：recombine 管同一口播下的 B-roll 槽位差异，sequence 管三段组合、时长窗口和标签匹配。复用了 output.new_output 和 preview.probe_media，成片格式可转回 track spec。 |
| 把字幕、缩放、混音加进 preview（zoom_out P1） | 不加。preview 保留为基础预览；fastcut 负责成片。OTIO 中不含字幕和缩放，已在 to_tracks 输出里标明。 |
| body_cap 默认值与 recombine 的 4/20 冲突（codebase P0） | 说明为两个层级：段级口播复用与单个 B-roll 素材复用。口播重复作为已接受风险，segment_usage 记录次数。 |
| 音频指纹查重（design_critic P1） | 未实现，列入未决问题。 |
| Hook 类型库收缩到 1-2 类（zoom_out P2） | 采纳为"type 是自由标签"，不建类型库文件。 |

## 评审发现的实现缺陷

run-v1 的 video-05 开头两行大字重叠：较短的第二行单独缩小字号后行距变小。已修为同一文字块统一字号，重渲染为 run-v2 并复查 0.5 秒帧。
