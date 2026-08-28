# 安全说明（SECURITY）

童映 Kindo 是**单家庭、局域网部署**的自托管系统。本项目不支持、也不承诺任何公网直接暴露场景。

## 信任边界（架构 §13 / PRD §13）

| 边界 | 保障 |
|---|---|
| 家庭网络 ↔ 互联网 | 只有 Hub → 远程 LLM 的 HTTPS 文本调用出网（最小上下文）；媒体字节、原始语音、NAS 路径、完整历史、任何 Secret 一律不出家庭网络 |
| TV ↔ Hub | Device Token（配对批准后签发）+ Playback Grant（逐请求校验，Header 传输、只存 hash、无 TTL） |
| 浏览器 ↔ Hub | Admin 会话 Cookie（HttpOnly + SameSite=Strict）+ CSRF Token（写操作强制） |
| 字幕/sidecar/Provider 元数据 | 一律按"非可信内容数据"处理：其中出现的指令性文本不具指令优先级，不能触发 Tool、改规则或绕过 Policy |

## Secret 保护（闭环）

- **落盘加密**：LLM API Key、NAS（SMB/WebDAV）密码、TMDB Key 在 SQLite 中以 Fernet 密文存储（前缀 `k1:`）；主密钥优先取环境变量 `KINDO_SECRET_KEY`，否则首次启动自动生成 `<data_dir>/secret.key`（权限 0600）。
- **写-only 语义**：任何 API 只返回 `configured: true` 与掩码（如 `sk-****abcd`），永不回显明文；录入需管理员会话 + CSRF。
- **日志过滤**：结构化日志对 authorization / cookie / api_key / secret / token / password 等字段脱敏为 `[REDACTED]`。
- **密钥丢失**：删除 `secret.key` 或更换 `KINDO_SECRET_KEY` 后，已存 Secret 解密失败按"未配置"处理（CRITICAL 日志），家长重新录入即可，其余数据不受影响。
- **备份注意**：`<data_dir>/backups/` 下的删除前自动备份为密文库——**请与 `secret.key`（或环境变量值）一并备份**，否则备份中的 Secret 无法恢复。

## 儿童隐私

- 儿童原始语音只在家庭网络内（TV → Hub → 本地 ASR）、仅内存缓冲、转写完成即释放、默认不落盘不写日志。
- 远程 LLM 只接收完成任务所需的最小文本上下文。
- 兴趣信号只存客观引用（profile/entity/topic + 行为类型 + 时间），不存儿童语音原文、不写推断性结论。

## 部署建议

- 仅在可信家庭局域网内使用；如需远程管理，经 VPN 或反向代理 + HTTPS（如 Caddy/Nginx + 自有证书）进入，不要直接端口映射到公网。
- Docker 部署：SQLite（`/data`）必须留在本地文件系统，勿放网络挂载。
- Admin bootstrap token 首次设置密码后即作废删除；请使用强口令。

## 报告漏洞

请勿在公开 Issue 中提交安全漏洞细节。使用 GitHub 私有安全通告（Security Advisories → Report a vulnerability），或联系维护者。我们会在确认后按"修复 → 发布 → 公告"流程处理，并致谢报告者。
