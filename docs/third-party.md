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
| Qwen3-TTS | 可选运行时，模型与 GPU 库由用户独立准备 | https://github.com/QwenLM/Qwen3-TTS |
| Faster Qwen3-TTS 0.2.6 | 本地推理环境中的 CUDA Graph 加速与分块语音生成 | https://github.com/andimarafioti/faster-qwen3-tts/tree/0.2.6 |

便携包保留随 Python 与 wheels 分发的元数据和许可证。Linux 包同时附构建环境版本清单及上游声明。SQLite 来自平台 Python 的 sqlite3 模块。此仓库没有为用户的项目代码另行选择对外授权方式。
