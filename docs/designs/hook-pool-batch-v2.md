# Synframe 批量成片 v2：Hook 库 + 共用主体 + CTA 轮换

* Author: Shane
* Date: 2026-11-09
* 状态：REVISED v2，已过一轮 Goldfish 评审（4 个视角），汇总见 hook-pool-batch-review.md；v1 保留为 hook-pool-batch.md
* 前置：fast-batch-hypothesis.md、one-take-15s-concepts-brief.md
* 流程偏差：EGM 要求逐节确认，本轮按用户"不要停下来"的要求跳过逐节确认，起草后直接评审。

## 1. 目标与非目标

目标：对任意一个已核实的商品，一次准备后批量产出 N 条约 15 秒的带货短视频。每条开头 Hook 不同，中段功能介绍和结尾 CTA 可复用。这是用户 2026-11-09 明确提出的方向。

非目标：不追求每条独立生成整片；不自动发布、不做多账号调度；不定义具体品类卖点，卖点来自商品档案和宣称边界。

## 2. 与 concepts-brief 的差异

| brief 的意见 | 本设计 |
|---|---|
| 主体每条独立生成 | 改为共用 2-5 个主体。理由：用户要求功能介绍复用，主体是成本大头（第 10 节）。代价：同一主体口播在多条里重复，判重风险接受并记录。 |
| 口播 Hook 的接缝和音色需要听审 | 采纳。默认 Hook 为静音 overlay；口播 prepend Hook 只允许与主体同 persona、同模型（代码强制），每个接缝人工听一次。 |
| 付费前先对账、定验收清单、做主体小测 | 采纳，写进第 12 节门槛。 |

## 3. 三段结构

| 段 | 时长 | 批内规则 | 声音 |
|---|---|---|---|
| Hook | overlay 1.5-3 秒；prepend 2-3 秒 | 默认每个 Hook 只用一次（hook_cap=1，可调） | overlay 无口播；prepend 带口播 |
| 主体 | overlay 时 ≤12.5 秒；prepend 时 ≤9.5 秒 | 复用上限 body_cap | 博主口播是主轴，中间插 B-roll |
| CTA | 2-5 秒 | 复用上限 cta_cap | 博主口播 + 大字 |

成片 13.5-15.5 秒，目标 15 秒。口播不足时用 CTA 段声明的 `pad` 源（一段产品画面）补尾卡，不另生成，最长补到 15 秒。超窗的组合不出，不截断口播。sequence 返回 requested、produced、skipped_hooks 和 shortfall_reason，不硬凑。

overlay Hook 只接 `opens` 与其 `fits` 相交的主体，避免"画面说 A、口播说 B"。标签由段验收的人打，词表按商品自定，例如 ceiling、wear、party、family。

## 4. 段库数据格式

```json
{
  "sku": "...", "locale": "es-MX",
  "rules": {"target_sec": 15, "min_sec": 13.5, "max_sec": 15.5, "count": 20,
            "hook_cap": 1, "body_cap": 5, "cta_cap": 7, "grade": {"saturation": 1.2}},
  "hooks":  [{"id": "H1", "type": "spectacle", "family": "call-01", "mode": "overlay", "fits": ["ceiling"],
              "shots": [{"src": "B5.mp4", "src_in": 0.3, "dur": 2.0, "zoom": 1.0}],
              "texts": [{"start": 0, "end": 2.0, "text": "LINE 1\nLINE 2"}]}],
  "bodies": [{"id": "Bd1", "persona": "kayla-mx-01", "model": "minimax-h3", "opens": ["ceiling"],
              "voice": [{"src": "A2.mp4", "in": 0, "out": 5.6, "line": "...", "words": []}],
              "shots": [{"src": "A2.mp4", "src_in": 0, "dur": 1.3, "lipsync": true}]}],
  "ctas":   [{"id": "C1", "persona": "kayla-mx-01", "model": "minimax-h3",
              "pad": {"src": "B1.mp4", "src_in": 2.4}, "voice": [], "shots": [], "texts": []}]
}
```

校验：id 全局唯一；每段至少一个 shot 且 dur>0；口播不长于画面；有口播的段必须有 persona 和 model；Hook 的 mode 只能是 prepend 或 overlay；overlay 不能带口播且必须有 fits；prepend 必须带口播。family 标记同一次生成切出的兄弟 Hook。

## 5. 生成路线（图先行）

1. 每次视频生成前用 tt-image-2.6 抽 4 张关键帧，人工挑 1-2 张；未挑中的不进视频。
2. Hook 走 omni_flash-10s：按次计费、固定 10 秒、参考图最多 7 张、无 mode 参数。一次里排 3 个不同场景的短镜头，切成一个 Hook 家族。切点在段验收时人工标注。
3. 主体和 CTA 走 H3 参考图模式（cankaosheng）：PDP 同 SKU 原图负责产品身份，挑中的关键帧负责姿势、构图和光线。主体加 CTA ≤15 秒时同一次生成，否则 CTA 单独生成。
4. 按次渠道用满最大时长，按秒渠道生成所需秒数（`channels.generation_seconds`）。实际扣费渠道由密钥决定，调用方不能选；估算按白名单里最贵的启用渠道，提交后账本记录实际渠道和单价。

## 6. 接缝与声音

- 有口播的段按 (persona, model) 比较，不同则拒绝组合。persona 由段验收的人听过后填写。
- 同模型两次生成的音色仍可能不同，所以 sequence 输出 `listen_checklist`：每个 (Hook, 主体) 和 (主体, CTA) 接缝只列一次，人工听一次即覆盖所有用到它的成片。
- 口播切点在段验收时写进 pools.json：优先 silencedetect -35 dB、d≥0.12 秒的静音中点，否则用 ASR 词间隙，并记录方法。
- 渲染约定沿用 cut15，参数在 fastcut 里。
- CTA 若与主体 X 同次生成，接到主体 Y 时属于换镜头，按接缝清单检查。

## 7. 批内差异与查重

- Hook 默认批内唯一；hook_cap>1 时同一 Hook 不会接同一主体两次。
- 段级上限 body_cap、cta_cap 默认 ceil(N/段数)。这与 recombine 的"单个 B-roll 素材 20 条内最多 4 次"是不同层级：这里限制整段口播的复用次数。口播重复是接受的风险，plan.json 的 segment_usage 记录每段实际次数。
- 查重两层：同家族 Hook 在段验收时比前 3 秒 dHash，过近的只留一个；成片层两两比较只作报告。初始阈值 12/64；run-v1 样片最低开头距离 19.67，需要更多批次校准。
- 音频指纹查重未实现。

## 8. 验收

| 层 | 粒度 | 谁做 | 通过条件 |
|---|---|---|---|
| 段验收 | 每个 Hook / 主体 / CTA 一次 | 人工 | 产品身份、动作因果、口播听审、宣称、persona |
| 接缝 | listen_checklist 每项一次 | 人工 | 声音和画面衔接自然 |
| 成片技术检查 | 每条 | 程序 | 关键词全在；ASR 差异 ≤2 处；-14±1 LUFS；峰值 ≤-1 dBFS；blackdetect d=0.1 pix_th=0.10 无黑帧；时长在窗口内 |
| 抽样创意 | 每批至少 3 条且覆盖每个主体 | 人工 | 节奏、Hook 是否成立 |

`speech.compare` 只给出 needs_human_review，通过条件由批量 QC 脚本判定。技术通过不等于创意通过。

## 9. 回流

每条清单记录 hook、hook_type、hook_family、body、cta、persona、voice_model 和 expected_text。发布后数据回填到 hook 上。

## 10. 成本（N=20，价格快照 2026-11-09，按失败照扣）

| 项 | 计算 | 账面 |
|---|---|---|
| Hook，hook_cap=1 | 20 个，按每次 2 个可用 → 10 次成功；成功率 52-62% → 约 17 次 × 0.9547 | 约 16.2 |
| Hook，hook_cap=2 | 10 个 → 5 次成功 → 约 9 次 | 约 8.6 |
| 主体 | 4 个 × 13 秒 × 0.1498（MC-H3 特价，实际扣费渠道），成功率 86.79% | 约 9.0 |
| CTA | 与主体同次生成 | 0 |
| 关键帧 | 约 26 次生成 × 4 张 × 0.0288 | 约 3.0 |
| 合计 | hook_cap=1 / hook_cap=2 | 约 28.2 / 20.6 |

余额实际下降比账本多 50-67%，套用后约 31-47。当前余额约 17.75，不够跑完整 N=20。

## 11. 实现

- `synframe/sequence.py`（新增）：段库校验、组合挑选、剪辑规格、listen_checklist、segment_usage；`to_tracks` 转成 timeline/preview 的轨道格式。未复用 `recombine._greedy`：后者的约束是"同一口播下 B-roll 槽位差异数"，这里是三段组合加时长窗口和标签匹配，共用挑选器需要给两边各加分支。
- `synframe/fastcut.py`（新增）：cut15 的数据驱动版，渲染缩放、大字、ASR 字幕、多段口播和响度归一；复用 `output.new_output` 和 `preview.probe_media`。OTIO 走 `to_tracks`，字幕、缩放和调色不进 OTIO。
- `synframe/diversity.py`（新增）：dHash 查重。
- `synframe/__main__.py`（修改）：`sequence`、`fastcut` 命令；sequence --output 写 plan.json、video-NN.json、video-NN-tracks.json。
- `skills/synframe-assembly/SKILL.md`（修改）：加 Hook 池规则，不另建子 skill。
- 现已并入 `python3 -m synframe render` 与 `qc`；早期私有示例未随仓库发布。

## 12. 验证与门槛

已完成（免费）：单元测试；用已付费素材拼 5 条样片并跑 QC。样片只能验证 sequencer、渲染、接缝、响度和查重。可用口播只有 A1/A2/A3，三个"主体"其实是同两句台词的不同排列，验证不了主体多样性。

付费门槛，按顺序：
1. 对清余额与账本差额，或用户接受按 1.67 倍做预算。
2. 用户看完样片，确认结构可用。
3. 付费小测：2 次 omni_flash-10s（测一次能切出几个可用 Hook）+ 1 次 H3 13 秒主体含 CTA。账面约 3.9，含差额约 6.5。预设规则：每次可用 Hook ≥2 才按第 10 节放量，否则重算成本。
4. 小测通过后按 N 和 hook_cap 放量。

## 13. 未决问题

1. 余额与账本差额。
2. omni_flash-10s 失败是否退款。
3. 成片层查重阈值，以及是否需要音频指纹。
4. 背景音乐：没有授权曲库，只用模型原声。
