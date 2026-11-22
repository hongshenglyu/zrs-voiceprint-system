# ZRS 噪声环境下声纹身份认证系统

面向 Windows 的本地声纹身份认证系统。本仓库保留当前系统代码、可校验运行包、配置示例和必要的合成回归。系统功能包括账号密码登录、五段录音注册、开启或关闭降噪验证、记录、持久化和撤回。

## Windows 启动

准备 Windows x64 和 Python 3.11.6（含 py 启动器）。在 PowerShell 中运行：

```powershell
.\Install-ZRS.ps1
Copy-Item config/launch.synthetic.example.json launch.local.json
.\Start-ZRS.ps1 -Config .\launch.local.json -Action Init
.\Start-ZRS.ps1 -Config .\launch.local.json -Action SetPassword
.\Start-ZRS.ps1 -Config .\launch.local.json -Action Start
```

将示例数据目录改成自己有写权限的本地绝对路径。首次设置管理员密码后，使用账号 admin 和刚设置的密码登录。启动终端会显示本机 URL 和合成 WAV 目录。用前五段 WAV 注册、第六段验证。数据目录与源码分开。

## 真实 E14

从负责人处获得获准的冻结 E14 权重及训练配置后，使用 config/launch.e14.example.json。将模型文件放在研究目录内的 .local/self-trained/e14-assets，按 docs/zrs-auth-08-11-04-p3/model.example.json 设置摘要。FFmpeg 在 PATH 上时可读 MP3、MP4、M4A、FLAC 等，WAV 可直接上传。没有获批并绑定阈值时，系统只显示诊断相似度。

## 结构

- tools/zrs_study_ui 包含当前用户界面。
- tools/zrs_study.py、zrs_study_local.py、zrs_study_storage.py 提供运行、账号、导入与持久化入口。
- runtime-source 是随包两种运行模式的可阅读源码。实际启动核验 tools/zrs-runtime.zip 的 SHA256。
- tests 包含会话、音频时长和音频质量回归。测试用合成数据。

本地演示仅适用于说明系统功能。模型权重、原始音视频、账户及数据保存在访问受限的独立目录。第三方软件和数据的权利归其所有。
