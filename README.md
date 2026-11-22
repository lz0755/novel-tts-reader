# 小说朗读室

将中文 TXT 小说导入桌面书架，按角色给对白配音，在原文中跳句、改音和续听，也可以把整章导出为 WAV。支持 Python 3.12 和 PySide6 6.9.3，核心包可以离线启动。

## 第一次使用

Windows x64 和 Ubuntu 24.04 x86_64 均有桌面核心版和本地 Qwen 完整版。核心版下载一个 ZIP 或 tar.gz。本地版包含阅读器、0.6B CustomVoice 权重、语音分词器、独立 Python 和 CUDA 12.8 推理库。下载所选平台全部 7z 分卷，放在同一目录，用 7-Zip 打开第一卷 `.7z.001` 并解压。每个版本均提供 SHA-256 校验。 [下载桌面发行版](https://github.com/lz0755/novel-tts-reader/releases/latest)

1. 解压完整的发行包。Ubuntu x86_64 运行 `./NovelTTSReader`，Windows x64 双击 `NovelTTSReader.exe`。本地完整版可从「朗读来源 → 加载 0.6B-CustomVoice」开始，首次加载会校验权重并预热。
2. 点击「打开样书」，选第一章后点击「播放」。右侧显示当前说话人、朗读文本及音频来源。样书有两章、三种固定普通话音色，未收录的文本和音色组合使用标为 Mock 的测试音。
3. 点击「预生成本章」，完成后点击「导出章节」，输入新文件夹名。输出包含 `chapter.wav` 和逐段 `manifest.json`。样章可直接试听。 [第一章音频](docs/evidence/demo-chapter/chapter.wav) · [导出清单](docs/evidence/demo-chapter/manifest.json)
4. 导入自己的 TXT 时可以预览编码和章节。在正文选择范围后，通过校对菜单编辑角色、声音、风格或纠音。重开书架会恢复到上次的原句。 [安装、书库备份与恢复](docs/installation.md)

![两章样书在桌面中的阅读界面](docs/evidence/reader.png)

## 朗读来源

| 来源 | 使用方式与本次验证范围 |
|---|---|
| 离线样书 | 从输入、固定语音、播放器、高亮到导出走完整流程，已在断网 Ubuntu 包上运行。音频及 SHA-256 随包提供。 |
| 云端标注 | 在「供应商」选择服务、模型和协议后，可以分章或整书标注。OpenAI 的 `gpt-6-luna` 已通过多段样章实测。校验失败时，在「请求与费用记录」查看具体段落和原因。DeepSeek 与自定义云端账号仍待实测。 [样章验证记录](docs/evidence/M4.md) |
| Qwen 云端语音 | 选择与账号地域一致的工作区端点、模型和实际 voice ID，允许本章上传后合成。下次仅使用缓存也可重听。已有音频协议和归一化测试，真实服务与费用待验。 |
| Qwen 本地双模型 | 在「朗读来源」选择推理用的 Python 和 `runtime.json`，再加载 0.6B 或 1.7B。Faster Qwen3-TTS 支持边生成边听、跳句和保调倍速，「本地权重量化」可切换 INT4。已在 RTX 4060 Laptop GPU（8 GB）上验证。 [本地 Qwen3-TTS 使用指南](docs/local-runtime.md) |

云端标注和语音分别授权。程序按模型实际支持的能力发送指令。费用未知会保留此状态。真实本地语音需要独立模型、驱动与运行时。 [供应商阶段记录](docs/evidence/M4.md) · [云端配音记录](docs/evidence/M5.md)

## 从源码启动

```sh
uv sync --locked --python 3.12
uv run --no-sync python -m novel_tts --mode mock
uv run --no-sync python -m novel_tts --self-test
uv run --no-sync pytest -q
```

自检在临时目录导入样书并导出整章。可用 `--data-dir` 指定独立书库，无显示设备的 CI 设 `QT_QPA_PLATFORM=offscreen`。Windows 和 Ubuntu 的原生构建命令、系统库及运行目录要求见安装说明。 [安装与构建](docs/installation.md)

## 版本与验收

2026-11-22 的逐项验收共 52 项，35 项通过、1 项未通过、4 项待测、12 项阻塞。其中，未缓存合成速度使用原整句引擎测量。 [逐项验收](docs/ACCEPTANCE.md) · [本地 Qwen 实测](docs/evidence/M7.md) · [原引擎 INT4 对照](docs/evidence/qwen-int4-20261122.json)

当前源码版在 Windows 11 通过 71 项自动化测试。Faster 流式引擎在 RTX 4060 Laptop 8 GB 上已验证两档模型都能边生成边播放。从点击播放到 Realtek 首次测得音频信号，0.6B 为 1.92 秒，1.7B 为 0.87 秒，各测一次，均早于整句 WAV 写入缓存。30 分钟连续实体播放另测。 [Faster 流式实测](docs/evidence/windows-faster-streaming-20261122.json)

M0 至 M7 各有独立提交、标签和 GitHub Milestone。预览标签的 Ubuntu 24.04 和 Windows Server 2022 原生包已完成样书自检，主分支历史 CI 在两平台各通过 47 项测试。 [GitHub 双平台测试](https://github.com/lz0755/novel-tts-reader/actions/runs/44915411279) · [交付状态与阶段标签](docs/STATUS.md) · [样本清单](fixtures/manifest.json)
