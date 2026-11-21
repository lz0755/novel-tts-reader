# 本地 Qwen 运行时

Windows x64 / Linux x86_64 的独立 Python 3.12 环境通过回环控制协议与桌面通信。核心包无需安装 PyTorch。此版本的两档模型接入代码及无 GPU 控制协议已测试；两档真实模型、8 GB 显存与实时能力均待目标机验证。

在有足够磁盘、兼容 NVIDIA 驱动和网络的目标设备，从源码的 sidecar 目录执行：

```sh
uv sync --locked --extra qwen --python 3.12
uv run --no-sync python -m novel_tts_sidecar.prepare --model 0.6B --root "/path/to/models"
uv run --no-sync python -m novel_tts_sidecar.prepare --model 1.7B --root "/path/to/models"
```

Windows 使用自己的模型目录和 sidecar/.venv/Scripts/python.exe，Linux 使用 sidecar/.venv/bin/python。Qwen 0.1.1 的环境可能需要系统 SoX。依赖锁固定 CUDA 12.8 版 PyTorch / torchaudio 2.8.0。准备步骤使用 Hugging Face 的恢复下载能力，逐文件核对指定提交的 LFS SHA-256 或 Git blob 摘要。runtime.json 记录模型路径与提交。加载前再次校验文件，推理过程使用本地资源。

在「朗读来源 → 选择本地推理环境」中选择该 Python 和 runtime.json。依次选择 0.6B、1.7B、0.6B 可检验模型切换；一次一个 spawn 工作进程。0.6B 省略指令，1.7B 按有效指令合成。等待期间可停止播放，显式硬取消将回收进程。若本地运行时退出，可选择上次模型的缓存或手工切换其它来源。

runtime.json 中 tf32、benchmark 可分别开关，dtype 和 attention 记录实际选项。首次加载含 2 或 3 次预热；长句按字形安全边界拆分，动态 token 预算及结束 token 均受检查。建议同机 A/B 每次只改变一项，在独立输出路径保存原始结果。

从核心环境运行以下入口，将路径替换为实际运行时：

```sh
uv run python scripts/probe_local_tts.py --profile qwen_0_6b --runtime /path/to/python --settings /path/to/runtime.json --output short.wav
uv run python scripts/benchmark_local_tts.py --profile qwen_1_7b_customvoice --runtime /path/to/python --settings /path/to/runtime.json --output measurements.jsonl
```

脚本采集冷启动、未缓存生成、RTF 及显存信息。真实设备首音、50 次缓存命中、30 分钟从空缓存连续播放、预生成回放、50 项纠音听测及 1.7B 风格 A/B 请按验收手册记录。Windows 与 Linux 分开保存，WSL2 单列。
