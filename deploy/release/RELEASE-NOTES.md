# 安装 / 升级（随每个 Release 附带）

## 容器部署（推荐，NAS / 主机）

1. 下载本 Release 的 `docker-compose.yml`、`env.example`（重命名为 `.env`）、`kindo.example.yaml`
2. 同目录建 `config/kindo.yaml`（自 kindo.example.yaml 复制修改；LLM Provider 也可完全经后台录入）
3. `.env` 设置 `MEDIA_DIR`（家庭媒体目录）后：`docker compose up -d`
4. 首次启动自动下载 ASR 模型（SHA256 校验后持久化到 `data/models/asr`，升级不重复下载）
5. 打开 `http://<主机>:8090/admin` 初始化管理员 → 「媒体库→来源与扫描」录入媒体 → TV/Pad 配对

## Android TV / Pad

安装对应 APK（release 签名，各版本间可直接覆盖升级，无需卸载）。

## 升级

`.env` 的 `KINDO_VERSION` 改为新版本 → `docker compose pull && docker compose up -d`
（数据与模型卷持久；数据库迁移随 Hub 启动自动执行；回滚=改回旧版本号再 up -d）

## 镜像

`ghcr.io/aaron-lilei/kindo-hub` · `kindo-asr` · `kindo-tts`（linux/amd64 + linux/arm64，tag 与本 Release 同号，latest 跟随最新）
