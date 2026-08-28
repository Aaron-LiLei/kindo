"""家长 AI 助手（PRD 8.14 AIA-001~008 / 架构 A-18~A-20 / 技术方案 §19）。

AI Runtime 为 kindo-hub 内部逻辑模块（非独立服务）：Profile Router、Context
Builder、Tool Permission、Suggestion/Proposal、AI Job Runner。家长侧 AI 只获得
读取与分析类 Tool；修改以结构化 Proposal 输出，经家长确认后由既有 Domain
Service 重新校验执行（Agent 不直接写库，A-20）。child_companion 的代码实体即
既有 Conversation 链路（agent/ + conversation/），不进入本包——两个 Tool 注册表
物理隔离（AC-19）。
"""
