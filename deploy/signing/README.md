# 自发布签名密钥库

`kindo-release.keystore`（alias=kindo，store/key 密码=kindo-release-key）为本项目
Android APK（TV / Pad）的自发布签名密钥，**随仓库分发以保证各 Release 之间 APK
可覆盖安装升级**（签名不一致会导致升级时必须卸载重装、丢失设备绑定）。

- 该签名只保证升级链连续性，不构成对抗仓库持有者的安全边界；企业/渠道分发请更换
  为自有密钥（替换本文件并同步修改两端 build.gradle.kts 引用，或经 CI Secret 注入）。
- 生成命令（复现/更换）：
  keytool -genkeypair -v -keystore kindo-release.keystore -alias kindo \
    -keyalg RSA -keysize 2048 -validity 10950 \
    -storepass <密码> -keypass <密码> -dname "CN=Kindo Self Release, O=Kindo, C=CN"
