# 部署与升级（Docker Compose）

两条路线：**官方镜像**（推荐；自 v0.1.0 Release 起可用）与**源码构建**（开发者）。
约束不变：SQLite 必须本地文件系统；不做实时转码；LLM/模型/数据不出家庭网络。

## 一、官方镜像部署（NAS / 主机，推荐）

前置：Docker + Compose v2；一个存放媒体文件的目录。

```bash
mkdir kindo && cd kindo
# 从 GitHub Release 下载 docker-compose.yml、env.example、kindo.example.yaml
cp env.example .env            # 编辑：MEDIA_DIR 必填；其余有默认
mkdir -p config
cp kindo.example.yaml config/kindo.yaml
docker compose up -d
```

启动顺序：asr（首次含模型下载）→ 健康检查通过 → hub 启动。之后：

1. 打开 `http://<主机>:8090/admin` 初始化管理员（或用 `KINDO_ADMIN_BOOTSTRAP_TOKEN`）。
2. 「媒体库 → 来源与扫描」录入媒体来源：本地目录在容器内路径为 `/media/family`
   （即 .env 的 MEDIA_DIR 只读挂载）；SMB/WebDAV 凭据在页面录入（落盘加密）。
3. 「AI 模型」页录入 LLM Provider（Base URL / API Key / 模型名；Key 落盘加密，
   也可改用 `.env` 的 KINDO_LLM_* 注入）。
4. TV/Pad 安装 Release 附带 APK → 配对码绑定。

### ASR 模型自动准备（T-20260902-003-06）

- 首次启动，kindo-asr 容器 entrypoint 自动下载默认中文模型（Paraformer-large-Chuan
  int8，约 230MB）到 `data/models/asr/`，先校验 SHA256（包体+文件清单）再落盘，写
  完成标记。**升级镜像/重启不会重复下载**（模型在独立卷中持久化）。
- 下载慢/内网环境：把模型包放到内网地址后设 `.env` 的 `ASR_MODEL_URL`；或离线预置——
  解压模型包到 `data/models/asr/` 后重启，脚本校验通过即跳过下载。
- kindo-tts（可选，家长声音克隆）同机制，模型约 180MB；不需要该功能可从 compose
  删除 tts 段（Hub 自动回退 Android 系统 TTS，其余功能不受影响）。
- 模型资产发布约定：`model-assets-v1` Release 承载模型包；维护者更新模型时运行
  `sh deploy/models/package-models.sh --upload` 并同步更新镜像内的默认校验值。

## 二、源码构建部署（开发者）

```bash
git clone <repo> && cd kindo/deploy
docker compose up -d --build
```

开发 Compose 直接挂载仓库内 `apps/kindo-asr/model`、`apps/kindo-tts/model` 作为
模型卷（本地已放置模型时零下载）。

## 三、升级约定

| 项 | 约定 |
|---|---|
| 版本 | 镜像与 Release 同 tag（`vX.Y.Z`）；`latest` 跟随最新发行 |
| 升级 | `.env` 改 `KINDO_VERSION` → `docker compose pull && docker compose up -d` |
| 数据 | `data/hub/`（SQLite/配置/样本）与模型卷均持久；数据库迁移随 Hub 启动自动执行 |
| 模型 | 卷持久 + 清单校验，升级不重复下载；模型版本升级随镜像清单自动增量替换 |
| 回滚 | `.env` 改回旧版本号 → `docker compose up -d`（数据库迁移不可跨版本回滚时以 Release 说明为准） |
| 卸载 | `docker compose down`；需要清数据再 `sudo rm -rf data/`（先备份 data/hub/kindo.db） |

## 四、端口与边界

| 服务 | 暴露 | 说明 |
|---|---|---|
| kindo-hub | 宿主 `${KINDO_PORT:-8090}` | Web Admin / TV / Pad 接入；建议仅家庭局域网 |
| kindo-asr | 仅容器网络 | 儿童原始语音不出 Hub↔ASR（硬性约束 5） |
| kindo-tts | 仅容器网络 | 家长声音样本不出 Hub↔TTS（PRD TTS-007） |

## 五、Android APK

随 Release 提供 TV / Pad APK（release 签名，见 `deploy/signing/README.md`）。
同一签名链内各版本可覆盖安装升级；换签后需卸载重装并重新配对。
