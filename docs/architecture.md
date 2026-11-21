# 运行边界

核心契约版本为 1。原文以码点半开区间定位。AnnotationRequest 的只读前文不拥有输出区间，AnnotationResult 的所有引用均须在本书解析。SynthesisRequest 是有效的波形输入，Ticket 记录作业、尝试和内容修订。AudioArtifact 只指向完整 PCM WAV，origin 取 mock_fixture、mock_tone、cloud、local。

Qt 主线程持有控件、媒体播放器和播放会话。有界 QThreadPool 执行阻塞工作。RepositoryWorker 通过排队信号接单，在线程内打开、串行操作并关闭 SQLite。核心包独立于本地推理环境。

控制服务监听系统分配的回环端口，以标准输出交接经过 schema 检查的握手。临时令牌从父进程传入。所有 HTTP 路由请求使用 Bearer 认证。停止等待、软取消和终止子进程为不同操作。

Python 3.12，Qt Widgets / Multimedia 使用 PySide6 6.9.3。regex 用于 Unicode 字形簇，jsonschema 验证外部数据，httpx 处理协议传输，keyring 使用系统凭据，platformdirs 管理各平台用户目录。uv.lock 锁定桌面跨平台依赖。构建依赖仅在 build 组启用。
