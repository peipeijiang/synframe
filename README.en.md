[简体中文](README.md) · **English**

<p align="center"><img src="assets/banner.svg" alt="Synframe: from one product photo to a batch of 15-second commerce videos" width="100%"></p>

<p align="center">
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/github/license/peipeijiang/synframe?style=for-the-badge&color=22c55e"></a>
  <img alt="Agent Skill" src="https://img.shields.io/badge/Agent%20Skill-Codex%20%7C%20Claude%20Code-0ea5e9?style=for-the-badge">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=for-the-badge&logo=python&logoColor=white">
  <img alt="ffmpeg" src="https://img.shields.io/badge/ffmpeg-required-007808?style=for-the-badge&logo=ffmpeg&logoColor=white">
  <img alt="Languages" src="https://img.shields.io/badge/docs-%E4%B8%AD%E6%96%87%20%7C%20English-f59e0b?style=for-the-badge">
</p>

> Turn one product photo into a batch of 15-second commerce videos, each with its own opening, a faithful product and an auditable spend.

Synframe is a set of Agent Skills plus a local CLI. It draws keyframes on a cheap image model first and spends on video only after a person picks them. Video material is split into hook, body and CTA pools, combined into N finished videos within reuse quotas, then checked technically. No paid call goes out without a price snapshot, proof files and a spend cap. Creative and listening acceptance always stays with a person.

## Highlights

<p align="center"><img src="assets/highlights-1.en.svg" alt="Highlights 01-02: images first, duration follows billing" width="100%"></p>
<p align="center"><img src="assets/highlights-2.en.svg" alt="Highlights 03-04: hook pool batches, ledger before spend" width="100%"></p>
<p align="center"><img src="assets/highlights-3.en.svg" alt="Highlights 05-06: product identity lock, machine QC and human sign-off" width="100%"></p>

## Workflow

```mermaid
flowchart TD
  IN["Product page / same-SKU photos<br/>optional: reference video"]:::input
  CR["Creative and breakdown<br/>synframe-creative<br/>100x methods + Whisper ASR"]:::skill
  PL["Piece and duration plan<br/>synframe-planner<br/>hook · body · CTA"]:::skill
  Q1{"Quote and budget gate<br/>generate --quote"}:::gate
  IMG["Draw keyframes<br/>tt-image-2.6"]:::model
  PICK{"Human pick<br/>synframe select"}:::human
  Q2{"Quote and budget gate<br/>generate --quote"}:::gate
  HK["Hook footage<br/>omni_flash-10s per call · fill 10 s"]:::model
  BD["Body + CTA<br/>MiniMax H3 per second · only what's needed"]:::model
  POOL["Pools pools.json<br/>synframe-assembly"]:::skill
  RN["Batch render<br/>synframe render<br/>video-NN.mp4 + tracks"]:::tool
  QC{"Technical QC<br/>synframe qc"}:::gate
  HR{"Creative and listening review"}:::human
  OUT["Delivery: videos + OTIO tracks + reports"]:::output
  AS["Reuse approved assets<br/>synframe-assets"]:::skill

  IN --> CR --> PL --> Q1 --> IMG --> PICK
  PICK -- not good --> IMG
  PICK --> Q2
  Q2 --> HK & BD
  HK --> POOL
  BD --> POOL
  POOL --> RN --> QC
  QC -- fail --> POOL
  QC --> HR
  HR -- new hook / regenerate --> PL
  HR --> OUT --> AS
  AS -. next batch .-> POOL

  X1["100xSpeed public demo videos"]:::ref
  X2["100x-skill-tiktok"]:::ref
  X3["AdMuse"]:::ref
  X1 -. batches · templates · line check .-> PL
  X2 -. creative methods and validators .-> CR
  X3 -. SKU reference routing · H3 requests .-> BD

  classDef input fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
  classDef skill fill:#ede9fe,stroke:#7c3aed,color:#3b0764
  classDef model fill:#fef3c7,stroke:#d97706,color:#78350f
  classDef tool fill:#dcfce7,stroke:#16a34a,color:#14532d
  classDef gate fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
  classDef human fill:#ffedd5,stroke:#ea580c,color:#7c2d12
  classDef output fill:#cffafe,stroke:#0891b2,color:#164e63
  classDef ref fill:#f8fafc,stroke:#94a3b8,color:#475569,stroke-dasharray:4 3
```

Dashed boxes are design references, not runtime dependencies.

| Stage | Owner | Output | Pass condition |
|---|---|---|---|
| Creative and breakdown | `synframe-creative` | Claims, hook directions, original-language transcript | Claims verifiable on the product page; target language kept verbatim |
| Duration plan | `synframe-planner` | Clip plan | No clip exceeds the chosen model's single-call maximum; unknown maximum means no submit |
| Keyframes | `generate` + tt-image-2.6 | `out/images/`, report | Budget gate passed, images downloaded |
| Pick | person + `select` | Selection records | Every slot has a reviewer and reason |
| Video | `generate` + omni_flash-10s / H3 | `out/videos/`, `report-*.json` | Each job submitted once, task ID saved |
| Render | `render` | `video-NN.mp4`, `-tracks.json` | 13.5 to 15.5 s, no repeated hook |
| Technical QC | `qc` | `qc/technical.json`, `qc/contact.jpg` | Critical terms present, ASR changes ≤ 2, -14±1 LUFS, peak ≤ -1 dBFS, no black frames |
| Acceptance | person | Review notes | Product identity, causal action, speech and captions checked one by one |

## Install

```bash
git clone https://github.com/peipeijiang/synframe.git
cd synframe
python3 install.py --check    # show what would happen
python3 install.py            # install into ~/.agents/skills, then run the doctor
```

The installer only manages the `synframe*` skill directories it installed and never overwrites anything else:

- **Existing directory it did not install**: skipped with a notice.
- **`--upgrade`**: replaces only unchanged managed copies; old versions move to `skills-backups/synframe-<timestamp>`.
- **`--uninstall`**: removes only unchanged managed copies.
- **`--doctor`**: dependency check only. It also runs after every install or upgrade and says how to install what is missing.

### Dependencies and companions

| Dependency | Role | Source |
|---|---|---|
| Python ≥ 3.10, ffmpeg / ffprobe | Render and QC; needs the drawtext, loudnorm, ebur128 and blackdetect filters | Required, `brew install ffmpeg` |
| Caption font | Big text and captions; override with `SYNFRAME_FONT` | Required, any system font |
| openai-whisper | Original-language ASR for `qc`; reads cached weights only | Optional, `pip install openai-whisper` |
| opentimelineio | OTIO export | Optional, `pip install -r requirements-optional.txt` |
| Node.js + AJV | Runs the upstream validators in vendor/100x | Optional |
| `LK888_API_KEY` | Paid generation (tt-image-2.6, omni_flash-10s, MiniMax H3) | Required for paid runs; the doctor checks presence only |
| [ad-muse](https://github.com/peipeijiang/ad-muse) | Single-shot product fidelity and reference routing | Companion skill; some scripts vendored in vendor/admuse |
| [100x-skill-tiktok](https://github.com/kezd088/100x-skill-tiktok) | Script segmentation, localization, persona, visual fission | Companion skill; vendored in vendor/100x |
| [egm](https://github.com/google/elephant-goldfish-model) | Fresh-eyes design review before a large batch | Optional plugin |

## Quick start

Run local render and QC on synthetic media first. No spend, no network:

```bash
cd examples/quickstart
export PYTHONPATH="$PWD/../.."                          # lets python3 -m synframe find the code
python3 make_media.py                                   # 3 hook clips, 1 voice take, a product image
python3 -m synframe render pools.json --output runs/r1 --count 3
python3 -m synframe qc runs/r1 --no-transcribe          # partial when ASR is skipped
```

Output lands in `runs/r1/`: three 15-second videos, track files and `qc/contact.jpg`. The paid path runs from the same directory in this order, and you can stop after any step:

```bash
python3 -m synframe generate image-spec.json            # offline check, sends nothing
python3 -m synframe generate image-spec.json --quote    # live prices, no spend
python3 -m synframe generate image-spec.json --submit   # submit within spend_cap and download
python3 -m synframe select select.json                  # record the picked keyframes
python3 -m synframe generate video-spec.json --submit   # keyframes + product photos to video
```

`generate` exits with code 3 when keyframes are unpicked, the cap is exceeded or the balance is short; nothing is charged in that case. Rerunning the same spec only polls and finishes downloads; it never resubmits.

## Cost model

| Model | Billing | Use | Reference price (2026-11-10 snapshot) |
|---|---|---|---|
| tt-image-2.6 | per image | Keyframes; draw several, then pick | about 0.029 / image |
| omni_flash-10s | per call | Fill 10 s with 3 scenes, cut into hooks | about 0.9547 / call |
| MiniMax H3 | per second | Body and CTA, only the integer seconds needed | about 0.1123 to 0.1647 / s, by channel |

One measured run: 12 keyframes + 3 videos cost 3.61 (LK888 credits) and produced 11 finished 15-second videos, about 0.33 each. Prices change; trust the live `--quote` snapshot.

## Limits

- LK888 is the only paid adapter so far; other providers need their own `providers/` module and tests.
- A technical QC pass is not a release. Product details, causal action, speech and captions need human review.
- Two separately generated voice takes may not sound alike; listen before cross-combining them.
- Generator watermarks are not removed, and nothing is published automatically.
- OTIO export has not been import-tested in a specific editor.

## License

MIT. The vendored `vendor/100x` (kezd088/100x-skill-tiktok) and `vendor/admuse` are MIT as well; sources and commit are in [NOTICE.md](NOTICE.md).
