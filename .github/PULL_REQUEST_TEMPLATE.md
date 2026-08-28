## 改动说明

（做了什么、为什么；涉及 UI 请附截图）

## 需求编号

（PRD/交互/架构/技术方案编号，如 `MED-016`、`§9.2`、`A-08`；工程治理类写 `工程治理`）

## 验证方式与结果

<!-- 只填写实际运行过的命令与真实结果；未验证项明确列出 -->

- [ ] Hub：`ruff check src tests` / `mypy src` / `pytest tests -q`
- [ ] Admin：`npx tsc --noEmit` / `npm run lint` / `npm run test` / `npm run build`（UI 改动已同步 admin_dist）
- [ ] TV：`gradle assembleDebug` BUILD SUCCESSFUL

命令输出摘要：

## 契约影响

- API/WS/数据模型/错误码是否变化（新增字段需说明与现行契约的关系）：
- 迁移编号（如有）：

## 安全与隐私自查

- [ ] 无 Secret/凭据/原始音频进入日志、响应或测试固件
- [ ] 播放类动作仍经服务端 Family Policy 校验（LLM 不是权限来源）
- [ ] 儿童端文案不含内部术语
