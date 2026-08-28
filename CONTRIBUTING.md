# 贡献指南（CONTRIBUTING）

感谢关注童映 Kindo。本文说明开发环境、质量门禁与协作约定；开始前请先阅读 [README.md](README.md) 了解项目定位。

## 开发环境

| 组件 | 技术栈 | 本地验证命令 |
|---|---|---|
| kindo-hub | Python 3.11 + FastAPI + SQLAlchemy + SQLite | `pytest tests -q` · `ruff check src tests` · `mypy src` |
| kindo-admin | React + TypeScript + Vite + Ant Design | `npx tsc --noEmit` · `npm run lint` · `npm run test` · `npm run build` |
| kindo-tv | Kotlin + Compose for TV + Media3（JDK 17 + Gradle 8.9 wrapper + compileSdk 35） | `./gradlew assembleDebug` |
| kindo-asr | Python + sherpa-onnx（Paraformer 中文） | `/health` 就绪检查 |

前置依赖：Python 3.11+、Node 20+、FFmpeg（ffprobe）、JDK 17 + Android SDK 35（仅 TV）。

```bash
# Hub（Windows Git Bash 示例；Linux/macOS 把 .venv/Scripts 换成 .venv/bin）
cd apps/kindo-hub
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests -q -m "not docker and not slow"   # 快速子集

# Admin 前端（构建产物并入 hub 的 admin_dist/；清空重建，勿增量拷贝）
cd ../kindo-admin && npm install
npm run build && rm -rf ../kindo-hub/admin_dist/* && cp -r dist/. ../kindo-hub/admin_dist/

# TV（Gradle 由 wrapper 自动获取，无需预装）
cd ../kindo-tv && ./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

测试标记：`docker`（SMB 真容器联调）、`slow`（端到端/真实媒体素材）。

## 质量门禁（CI 强制，本地须全绿后再提 PR）

1. Hub：`ruff check src tests` + `mypy src` 零告警；全量 pytest 通过（环境项允许 skip/标注）。
2. Admin：`tsc --noEmit` 0 错误 + lint + vitest + build 全绿；改动 UI 后以**清空重建**方式同步 `admin_dist` 产物。
3. TV：`assembleDebug` BUILD SUCCESSFUL。
4. 每个功能改动附带自动化测试（单元 + 契约级接口测试）；测试不过不算完成。
5. 功能与契约以 [docs/design/](docs/design/) 现行设计文档为准；发现文档矛盾时在 Issue/PR 中提出，不擅自发明协议。

## 代码与文案约定

- 代码标识符与 API 字段用英文（snake_case）；注释、提交说明、面向用户文案用中文。
- 儿童端文案简短友好，不出现「模型/Provider/函数/token」等内部术语。
- 日志为结构化 JSON；**永不记录** Authorization、Cookie、Grant、API Key、NAS 凭据、原始音频。
- 依赖版本用 lockfile 固定（hub 的 `requirements.lock`），不写 latest；升级依赖需重跑核心场景。
- 数据库迁移：`alembic revision -m "描述"` 手写 DDL（参照既有迁移），应用启动时自动执行。

## 隐私与安全红线（违反即拒绝）

- 儿童原始语音只在家庭网络内、仅内存缓冲、转写完成即释放、默认不落盘不写日志。
- TV 端永不持有 NAS 凭据 / LLM API Key；LLM 不是权限来源——一切播放动作经服务端 Family Policy 校验。
- Secret（LLM Key、NAS 密码、TMDB Key）密文落盘（secretbox），API 只返回 configured/masked_hint。
- 详见 [SECURITY.md](SECURITY.md)。

## 提交与 PR 流程

1. Fork → 分支（`feat-xxx` / `fix-xxx`）。
2. 本地跑齐三端门禁；提交说明说明动机与影响。
3. PR 按模板填写：改动范围、验证方式（命令+结果）、已知风险。
4. 维护者复验：契约一致 + 测试全绿 + 无 Secret/隐私泄漏后方可合入。

> 许可证：MIT（见 [LICENSE](LICENSE)）。提交即表示同意以 MIT 许可发布您的贡献。
