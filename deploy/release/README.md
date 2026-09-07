# deploy/release — 发行部署工件

本目录是 GitHub Release 附带的部署工件源（T-20260902-003-05/06）。用户从 Release
下载 `docker-compose.yml` + `.env`（复制自 `.env.example`）+ `kindo.example.yaml`，
放到同一目录后 `docker compose up -d` 即完成安装——不需要克隆仓库或本地构建镜像。

| 文件 | 用途 |
|---|---|
| docker-compose.yml | 标准三服务栈（hub / asr / tts[profile]），镜像来自 ghcr.io |
| .env.example | 环境模板（版本/媒体目录/端口/LLM/模型包地址覆盖） |
| （Release 内）kindo.example.yaml | Hub 配置模板（复制为 config/kindo.yaml） |

启动顺序与健康检查：hub 依赖 asr 健康后启动；asr/tts 首次启动自动下载模型
（SHA256 校验，持久化到 `data/models/`，升级不重复下载）；模型下载期间容器
显示 starting，带宽慢属预期。

Android TV / Pad APK 亦随 Release 提供（与镜像同版本号）。
