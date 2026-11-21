# 安装、升级与恢复

## 核心包

Windows x64 将 ZIP 解压到独立目录，双击 NovelTTSReader.exe。Linux x86_64 将 tar.gz 解压到用户目录，在图形桌面运行 ./NovelTTSReader。解压时保留全部库和插件的相对路径。核对随包 SHA-256。Linux 使用系统字体、图形及音频服务，Ubuntu 桌面所需系统库包括 libegl1、libopengl0、libxkbcommon-x11-0、libxcb-cursor0、libpulse0、libasound2t64、libgssapi-krb5-2、libatomic1、libcups2t64、libgtk-3-0t64、libwayland-cursor0、libwayland-egl1，推荐中文字体 fonts-noto-cjk。

首次打开可以选择「打开样书」或「导入 TXT」。样书固定语音会显示 Mock 固定语音，未收录的文本和音色组合使用 Mock 测试音。先「预生成本章」再「导出章节」，为新文件夹命名，可得到 chapter.wav 和带顺序、来源、摘要的 manifest.json。导出拒绝覆盖已有目录。

## 数据与备份

框架按操作系统定位用户数据和缓存。可以用 --data-dir "/路径/中文 书库" 指定独立书库。程序将原文件字节、书架、原文、修订、绑定、进度存入 library.sqlite3。正常退出后备份整个用户数据目录；运行中使用 SQLite 在线备份接口。恢复时关闭应用，在新的数据目录恢复副本，再用 --data-dir 打开。迁移前自动保留 .before-vN.sqlite3。音频缓存可由「清理缓存」释放，书籍原文和校对记录保留。凭据使用系统凭据服务或仅本次会话。

升级时先退出并备份书库，将新版本解压到独立安装目录。卸载只需移除安装目录；用户可另行删除不再需要的数据和缓存。核心包使用普通账户，支持安装到只读目录。

## 验证

程序 --self-test 在临时书库执行样书、定位、预生成和整章导出，并输出帧数及片段来源。实体声卡的人工试听、平台信息、目标 GPU 和云端凭据的验收状态见验收记录。

## 源码与构建

安装 Python 3.12 环境及 uv 后，运行 uv sync --locked 和 uv run python -m novel_tts。使用 uv sync --locked --group build 安装独立的构建组。在各自平台运行 uv run --no-sync python scripts/build_desktop.py --platform linux 或 --platform windows。Windows 构建需要 MSVC Build Tools，Ubuntu 构建需要 C 编译器及上述系统依赖。构建使用 PySide6 官方部署路线的 Nuitka standalone 后端。目标机解压包运行无需编译器或开发环境。

「播放 → 连续播放同角色的已缓存短句」会把至多四个兼容、相邻的已缓存短句按 PCM 帧顺序组装，按实际帧边界高亮。新句、旁白、待确认、换人、换声或风格变化时保留独立段。单句缓存继续用于按原文定位、纠音和导出。

从「书库」可备份数据库、删除当前书和设置缓存容量。从「任务记录」可查看本次桌面任务的结果、取消运行中的任务或重试失败；供应商的持久化尝试和费用位于「供应商 → 请求与费用记录」。真实模式需选择对应服务并明确授权发送文本，重启后云端缓存可用，新的上传按章重新授权。

批量标注前可导入十章真实样本的费用报告。在源码环境用已设置价格与日期的服务运行 `scripts/evaluate_llm.py --send-corpus --profile openai --output runs.jsonl`，再运行 `scripts/cost_summary.py runs.jsonl --total-characters 100000 --save-calibration costs.json`。两个脚本均以 `uv run python` 启动。十章测量会发送自制样本并消耗所选服务的请求预算，请按需要设置上限。通过「供应商 → 导入十章费用样本」打开 costs.json，整书标注确认框会按本书原文长度和当前价格显示范围。缺少记录、定价变化或有未知费用时，界面显示「费用待估」。手工重发发生过响应丢失的同一请求时，可在请求记录中查看前次尝试和潜在重复费用标记。
