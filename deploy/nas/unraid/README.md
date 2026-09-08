# Unraid Community Applications 适配（T-20260902-003-07）

本目录是 Unraid CA 提交仓库（Aaron-LiLei/kindo-unraid）的单一事实来源。

| 文件 | 用途 |
|---|---|
| ca_profile.xml | CA 仓库档案（仓库简介/图标/入口） |
| templates/kindo-hub.xml | 核心服务模板（WebUI 8090 / 数据 / 媒体目录 / ASR-TTS 环境变量） |
| templates/kindo-asr.xml | 本地语音识别模板（模型自动下载 217MB，发布 18081） |
| templates/kindo-tts.xml | 可选家长声音克隆模板（发布 18092） |

## 设计要点

- **bridge 网络默认形态**：Unraid 默认 bridge 下容器名解析不可用，模板默认用
  「宿主网关 + 发布端口」互联（hub 的 `KINDO_ASR_ENDPOINT=http://172.17.0.1:18081`）；
  用户若使用自定义网络（br0）可改为 `http://kindo-asr:8081`（模板说明已写明）。
- **免配置文件**：hub 镜像不再强依赖 /config/kindo.yaml（缺省走内置默认 + 环境变量），
  CA 一键安装无需预先放置任何文件。
- **权限**：容器以 uid 10001 运行，appdata 目录 Linux 下需可写（模板 Description 已提示）。

## 提交流程

1. 从 unraid/unraid-community-apps-starter 创建公开仓库 `Aaron-LiLei/kindo-unraid`
2. 用本目录内容替换 ca_profile.xml 与 templates/（删除示例），补 icon.svg
3. 在 CA 提交流程（/submit）跑 Validate and Scan；按审核意见修订
4. 建论坛支持帖后把 Support 链接补进 ca_profile.xml / 模板（当前以 GitHub Issues 代替）
