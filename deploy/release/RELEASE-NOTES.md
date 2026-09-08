# 安装 / 升级（随每个 Release 附带）

## 系统要求

- 主机：Docker + Compose v2（NAS / PC / Mac 均可），amd64 或 arm64
- Android TV：Android 7.0 及以上（APK minSdk 24）；Pad：Android 8.0 及以上（minSdk 26）
- 首次启动需联网下载 ASR 模型约 217MB（启用家长克隆 TTS 另需约 157MB）

## 容器部署（推荐，NAS / 主机）

1. 下载本 Release 的 `docker-compose.yml`、`env.example`（重命名为 `.env`）、`kindo.example.yaml`
2. 同目录建 `config/kindo.yaml`（自 kindo.example.yaml 复制）。**保持 `llm_providers`
   段注释不动**：LLM 推荐走后台录入；要改用环境变量方式，需解开该段注释并在 `.env`
   提供 `KINDO_LLM_BASE_URL` / `KINDO_LLM_API_KEY` / `KINDO_LLM_MODEL`（原样复制且
   未设变量会导致 Hub 启动失败）
3. `.env` 设置 `MEDIA_DIR`（家庭媒体目录）。
   Linux NAS 请先给数据/模型目录写权限（容器以 uid 10001 运行）：
   `mkdir -p config data/hub data/models/asr && sudo chown -R 10001 data/hub data/models/asr`
   完成后：`docker compose up -d`
4. 首次启动自动下载 ASR 模型（SHA256 校验后持久化到 `data/models/asr`，升级不重复
   下载）。下载期间 kindo-asr 显示 starting/unhealthy 属预期（慢网可达数十分钟）；
   若 `up -d` 曾报 "dependency failed to start"，模型就绪后再执行一次
   `docker compose up -d` 即可
5. 打开 `http://<主机>:8090/admin` 初始化管理员——首次访问所需的一次性 token 在
   主机 `data/hub/bootstrap/ADMIN_BOOTSTRAP_TOKEN` 文件中（也可在 `.env` 预设
   `KINDO_ADMIN_BOOTSTRAP_TOKEN`）；设置密码后该 token 立即作废
6. 「媒体库→来源与扫描」录入媒体 → TV/Pad 配对

## Android TV / Pad

安装对应 APK（release 签名，各版本间可直接覆盖升级，无需卸载）。

## 升级

1. 建议先备份数据库：`cp data/hub/kindo.db data/hub/kindo.db.bak`
2. `.env` 的 `KINDO_VERSION` 改为新版本 → `docker compose pull && docker compose up -d`
   （数据与模型卷持久；数据库迁移随 Hub 启动自动执行）

## 回滚

`.env` 改回旧版本号再 `docker compose up -d`。注意：若曾运行过更新版本，其数据库
迁移可能已推进，旧版本无法回退迁移——此时先恢复升级前的数据库备份再回滚。

## 镜像

`ghcr.io/aaron-lilei/kindo-hub` · `kindo-asr` · `kindo-tts`（linux/amd64 + linux/arm64，tag 与本 Release 同号，latest 跟随最新）
