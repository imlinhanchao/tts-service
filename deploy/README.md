# 开机自启（macOS launchd）

前提：项目已放在固定路径（下称 `<repo>`，示例假定 `~/tts-service`），
且 `bash scripts/setup.sh` 已跑通、`bash service/start.sh` 能正常启动。

> 提示：不要把项目放在 `~/Documents`、`~/Downloads`、`~/Desktop` 下，
> macOS 的 TCC 隐私保护会阻止 launchd 后台任务读取这些目录。

## 安装

1. 把两个包装脚本放进服务目录（launchd 通过它们启动）：

   ```bash
   cp deploy/launchd-run.sh deploy/rotate-logs.sh <repo>/service/
   chmod +x <repo>/service/launchd-run.sh <repo>/service/rotate-logs.sh
   ```

2. 复制 plist 到 LaunchAgents，并把其中所有 `/Users/yourname/tts-service`
   改成你的实际路径（launchd 不会展开 `~` 和环境变量）：

   ```bash
   cp deploy/com.tts-service.plist deploy/com.tts-service-logrotate.plist \
      ~/Library/LaunchAgents/
   ```

3. 加载：

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tts-service.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tts-service-logrotate.plist
   ```

## 常用命令

```bash
# 查看状态（state = running 即正常）
launchctl print gui/$(id -u)/com.tts-service | grep -E "state =|pid ="

# 重启（批量合成期间不要执行，会中断当前请求）
launchctl kickstart -k gui/$(id -u)/com.tts-service

# 停止并卸载
launchctl bootout gui/$(id -u)/com.tts-service

# 手动触发一次日志轮转（MAX_MB=0 强制轮转）
MAX_MB=0 bash <repo>/service/rotate-logs.sh
```

## 行为说明

- `com.tts-service`：`RunAtLoad` 登录后自动启动；`KeepAlive.SuccessfulExit=false`
  表示崩溃自动拉起、手动停止不重启；崩溃重启间隔 10s（`ThrottleInterval`）。
- `com.tts-service-logrotate`：每天 04:10（休眠错过会在唤醒后补跑），
  单日志 >20MB 时 copytruncate 轮转，保留 `.1` / `.2` 两代，运行中的服务无需重启。
- LaunchAgent 属于登录会话：机器重启后需该用户登录一次，服务才会启动。
