# Edge 在线语音组件说明与替换指引

`edge_component` 是「小说朗读室」独立运行的轻量语音组件，负责通过标准输入输出（stdio）与主程序通信、连接微软在线朗读服务以及使用 `miniaudio` 解码 MP3。

主程序未将 `edge-tts` 或其传递依赖静态编译进主可执行文件。所有 Python 库均以独立文件保存在本目录的 `libs/` 中，用户可按 LGPLv3 条款自由查看或替换。

## 目录结构

- `component.json`：组件协议版本清单（当前 `protocol_version: 1`）。
- `novel_tts_edge/`：组件进程入口、stdin/stdout 通信与 MP3 解码源码。
- `libs/`：独立 Python 依赖目录（包含 `edge_tts`、`miniaudio`、`aiohttp` 等）。
- 发行包的 `licenses/` 含 GPLv3 全文，以及从各依赖复制的许可证与版权声明。`edge-tts` 的 `LICENSE` 包含 LGPLv3 全文。
- `python/`（发行包中）：随包提供的独立 CPython 3.12 运行时。
- `pyproject.toml` / `uv.lock`：本组件的独立依赖定义与锁文件。

## 替换 `edge-tts` 或其他依赖库

1. 关闭正在运行的「小说朗读室」。
2. 备份当前的 `edge_component/libs/` 目录。
3. 使用兼容版本（Python 3.12、保持 `edge_tts.Communicate` / `edge_tts.list_voices` 接口兼容）替换 `edge_component/libs/` 中的对应包，例如：
   ```bash
   uv pip install --target edge_component/libs --upgrade edge-tts==7.3.1
   ```
   或直接将自行修改后的 `edge_tts/` 源码目录覆盖到 `edge_component/libs/edge_tts/`。
4. 验证组件可正常握手（Windows PowerShell 示例）：
   ```powershell
   $env:PYTHONPATH = "edge_component\libs;edge_component"
   echo '{"cmd":"shutdown"}' | .\edge_component\python\python.exe -m novel_tts_edge
   ```
   正常时首行应输出包含 `"type":"hello"` 与 `"protocol_version":1` 的 JSON。
5. 重新启动「小说朗读室」，在「朗读来源」中测试「管理 Edge 音色与目录 → 手动刷新音色目录」或试听新句子。

## 卸载或组件缺失时的降级行为

如果移除或损坏 `edge_component/`，主程序仍可正常启动、打开书库、离线重听已有缓存音频、导出已完成缓存的章节以及使用离线 Mock / 本地 Qwen 音源；仅在需要新合成未缓存 Edge 句子或刷新 Edge 目录时提示修复组件。
