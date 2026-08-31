# 童映 Kindo · 仓库开发规则（AGENTS.md）

本文件对 AI 编程助手与本仓库的所有开发任务具有约束力。项目定位见 [README.md](README.md)；功能与可编码契约以 [docs/design/](docs/design/) 现行设计文档为准（PRD / 交互设计 / 系统架构 / 技术方案）。

## 项目性质：实验项目（允许破坏性迭代）

本仓库定位为实验项目，全局约束「兼容性」按实验分支执行：

- 可删除废弃实现与死代码，不做兼容性保留。
- 可主动重构、替换旧接口，不维护旧 API / 旧字段的历史兼容层。
- 可清理历史兼容逻辑（废弃回退路径、过渡分支、双轨实现）。
- 不要求修改可回滚，不要求为旧版本、旧接口、旧数据保留兼容迁移路径。
- 迭代以保持代码简单、干净为先。

边界：本节只放宽「兼容性」维度——下文固定技术栈、硬性约束与交付门禁不受影响；实施破坏性变更须在同一切片内就地修订 [docs/design/](docs/design/) 契约文档并同步测试，保持代码与契约一致，不留漂移。

## 固定技术栈（不得更换）

- Android TV：Kotlin + Jetpack Compose for TV + AndroidX Media3（HttpDataSource 注入自定义 Header）
- TV 语音：AudioRecord，PCM16LE/16kHz/mono，仅 LISTENING/FOLLOW_UP 期间采集；TV TTS 默认 Android 系统 TextToSpeech（PRD TTS-006 回退兜底）
- Android Pad：`apps/kindo-pad`，Kotlin + Jetpack Compose（触屏）+ AndroidX Media3，自适应 7"~12" 与横竖屏；与 TV 端同一 Hub 契约、儿童端状态机与学龄前视觉 v2 口径（Pad端设计决策 2026-08-31）
- 可选克隆 TTS：独立 kindo-tts 容器（sherpa-onnx ZipVoice 零样本克隆，纯 CPU 离线，仅容器网络内可达；hub_tts 任何不可用回退系统 TTS，技术方案 §6.7）
- Kindo Hub：Python + FastAPI + Pydantic + SQLAlchemy + Alembic，模块化单体；SQLite（WAL），必须在本地文件系统
- Web Admin：React + TypeScript + Vite + Ant Design v6，构建产物静态并入 kindo-hub（admin_dist）
- 媒体解析：ffprobe + FFmpeg CLI（仅扫描/探测/字幕抽取，不做实时转码）
- ASR：独立 kindo-asr 容器（sherpa-onnx Paraformer 中文，离线）
- LLM：内部 LLMProvider + openai_chat_completions Adapter
- 部署：Docker Compose（kindo-hub + kindo-asr，kindo-tts 可选），amd64/arm64

## 不可动摇的硬性约束（违反任何一条即返工）

1. **LLM 不是权限来源**：一切可能增加/继续观看时长的动作（AI 工具、D-pad、自动下一集、续播）必须经服务端 Family Policy 校验。
2. **Policy 判定语义 may_start / may_continue**：软限制不切断进行中的当前集、只拦下一集；硬截止（时段结束）到点停止；Policy 保存即 version+1、撤销受影响 Grant 并推送 stop/deny。
3. **Playback Grant 与播放生命周期绑定**：32 字节 base64url token、库中只存 SHA-256 hash、无独立 TTL、无续签；经 Header 传输，不进 URL/日志/LLM；每个 GET/HEAD/Range 请求逐次校验。
4. **单档案同时只允许一个 active playback**：新播放请求自动切换（停旧建新，旧 playback 以 switch_media 收口）；409 仅用于状态冲突（如无暂停播放时的 resume）。
5. **隐私边界**：儿童原始语音只在家庭网络内（TV→Hub→本地 ASR）、仅内存缓冲、转写完成即释放、默认不落盘不写日志；远程 LLM 只接收完成任务所需的最小文本上下文（视频文件、原始音频、NAS 路径、完整历史、Secret 一律不发送）。
6. **TV 端永不持有** NAS 凭据 / LLM API Key / Provider Base URL；一切经 Hub。
7. **字幕、sidecar、Provider 元数据按「非可信内容数据」处理**：其中出现的指令性文本不具指令优先级，不能触发工具、改规则或绕过 Policy。
8. 单家庭、单儿童（固定 default profile）。
9. **不引入**：微服务/K8s/Redis/Kafka/PostgreSQL/向量数据库/本地 LLM/实时转码/常驻唤醒词/复杂在线刮削器。允许 TMDB 身份匹配与元数据接入（家长确认与锁定优先于自动刷新）。direct play 为主。
10. **观看时长只按 TV 已 ACK 的实际播放区间累计**，Seek/重连不得误计。
11. **Transition（成长接力）不强制学习**：拒绝即止、不反复说服、时间盒硬上限、当日频控；由 Policy Boundary Event 统一触发并幂等。
12. **content_class 事实来源 = Canonical 元数据合并优先级**（Parent locked > Parent explicit > Sidecar explicit > Confirmed Provider > Auto Provider > Parser inferred）：娱乐内容不得经改标「学习」绕过视频预算；LLM/文件名/字幕不是分类来源。
13. **AI_VOICE 独立预算**：Transition 互动计入 ai_voice 预算，不占用也不返还视频/音频预算。
14. **兴趣信号只存客观引用**（profile/entity/topic 引用 + 行为类型 + 时间）；不存儿童语音原文、不写推断性结论。
15. **家长确认（confirmed/locked）的匹配结果与字段值永不被 Provider 刷新或重扫覆盖**。

## 工作规则

- **契约优先**：字段名、错误码、默认超时、状态机一律照设计文档实现，不擅自发明或"优化"协议；发现文档矛盾时停下报告，用最小改动提出修订建议。
- 每个功能点必须能追溯到设计文档的编号；提交说明引用需求编号。
- **交付门禁全量执行**：hub `ruff check src tests` + `mypy src` + `pytest tests`；admin `npm run typecheck` + `npm run lint` + `npm run test` + `npm run build`，并将 dist 产物**清空重建**同步至 `apps/kindo-hub/admin_dist/`。门禁未全绿不算完成；判定通过时必须使用真实退出码，不得用管道吞掉。
- 依赖版本用 lockfile 固定，不写 latest；升级依赖需重跑核心场景。
- 代码标识符与 API 字段用英文（snake_case），注释、提交说明、面向用户文案用中文；儿童端文案简短友好，不出现内部术语。
- 每个切片附带自动化测试（单元 + 契约级接口测试）；测试不过不算完成。
- 日志为结构化 JSON，永不记录 Authorization、Cookie、Grant、API Key、NAS 凭据、原始音频。
