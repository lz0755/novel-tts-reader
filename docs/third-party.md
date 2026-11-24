# 发行组件与来源

| 组件 | 用途与版本来源 | 项目资料 |
|---|---|---|
| CPython 3.12 | 解释器，Windows 便携包使用 python-build-standalone 的 Windows x64 运行时 | https://docs.python.org/3/license.html |
| PySide6、Shiboken6、Qt 6.9.3 | 界面和多媒体。Qt 在程序外以动态库形式提供，按对应模块及上游分发条款使用 | https://doc.qt.io/qtforpython-6/licenses.html |
| Qt Multimedia / FFmpeg | 运行时解码以及声音输出；平台后端的库以实际包内文件为准 | https://doc.qt.io/qt-6/qtmultimedia-licensing.html |
| libsonic 0.1.0 | 流式播放时调整语速并保持音高 | https://pypi.org/project/libsonic/0.1.0/ |
| httpx、httpcore、certifi、keyring、regex、jsonschema、platformdirs | HTTP、证书、凭据、字形处理、契约与路径；精确版本和包校验值见 desktop-uv.lock | https://pypi.org/ |
| Nuitka | Linux 与 Windows 原生构建工具，仅构建时使用 | https://nuitka.net/ |
| eSpeak NG | 以自制文本生成固定普通话 WAV。仅在素材制作环境使用，主包只携带音频文件和来源清单 | https://github.com/espeak-ng/espeak-ng |
| Qwen3-TTS | 本地完整版附 0.6B CustomVoice 的固定修订、语音分词器、模型卡和 Apache 2.0 许可证 | https://github.com/QwenLM/Qwen3-TTS |
| PyTorch、torchaudio 和 NVIDIA CUDA 12.8 库 | 本地 Qwen 完整版附带对应平台的锁定发行包及其许可证 | https://pytorch.org/get-started/previous-versions/ |
| Faster Qwen3-TTS 0.2.6 | 本地推理环境中的 CUDA Graph 加速与分块语音生成 | https://github.com/andimarafioti/faster-qwen3-tts/tree/0.2.6 |
| edge-tts 7.3.1（独立组件） | LGPLv3（`srt_composer.py` 为 MIT）；随独立 `edge_component/libs/` 目录分发，可按 `edge_component/README-REPLACE.md` 自由替换 | https://github.com/rany2/edge-tts/tree/7.3.1 |
| miniaudio 1.73（独立组件） | MIT（内含 dr_mp3 等公有领域/MIT 解码器）；负责将 24 kHz MP3 解码为 24 kHz 单声道 16 位 PCM WAV | https://pypi.org/project/miniaudio/1.73/ |
| Edge 组件传递依赖（aiohttp、cffi、certifi、tabulate 等） | 独立锁文件见 `edge_component/uv.lock`，各包许可证随包保留于 `edge_component/licenses/` | https://pypi.org/ |

便携包保留随 Python 与 wheels 分发的元数据和许可证。Linux 包同时附构建环境版本清单及上游声明。SQLite 来自平台 Python 的 sqlite3 模块。此仓库没有为用户的项目代码另行选择对外授权方式。

## Edge 在线语音使用条件与核对记录

- 接入说明：界面以“在线、无需用户配置 API Key”描述 Edge 在线语音。默认仅用本地缓存，须用户按章节显式授权后才将缺段正文发送至微软在线朗读服务（参考 [Microsoft Edge 隐私说明 · Read Aloud](https://learn.microsoft.com/en-us/legal/microsoft-edge/privacy#read-aloud)）。
- 核对日期：2026-11-24。
- 使用边界：
  - 第三方桌面调用、批量预生成、本地持久缓存与导出音频再分发均受微软服务条款约束；当前版本仅面向个人本地阅读验证，不承诺官方 SLA 或商业再分发许可。
  - 若需要正式服务条款、配额或商业使用许可，应另行评估接入官方 [Azure Speech TTS](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech)。
- `edge-tts` 运行于独立子进程。发行包的 `edge_component/licenses/` 包含 GPLv3 全文和上游附带的 LGPLv3 全文。替换 `edge_component/libs/edge_tts/` 后，可按 `edge_component/README-REPLACE.md` 校验。
