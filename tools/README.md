# 工作流脚本（三个"技能"）

把每次发版都要手工重复的三件事固化成脚本：**更新代码 / 提交推送 / 打包**。
都只依赖 Windows 自带 PowerShell 5.1 + git + curl，不需要额外安装。

| 脚本 | 作用 | 典型用法 |
|---|---|---|
| `update-code.ps1` | 自动挑代理 → `git fetch` → 快进合并 | `powershell -ExecutionPolicy Bypass -File tools\update-code.ps1` |
| `push-code.ps1` | `git add -u` → 提交 →（必要时变基）→ 带代理 push → 复核远端 SHA | `powershell -ExecutionPolicy Bypass -File tools\push-code.ps1 -Message "feat: xxx" -Include 新文件` |
| `pack.ps1` | 结束占用进程 → 重试清理 dist → 构建 → **以产物校验完整性** | `powershell -ExecutionPolicy Bypass -File tools\pack.ps1` |

`common.ps1` 是三者共用的公共函数（代理探测、带代理跑 git），不需要单独调用。

## 为什么需要它们

- **代理会挂、端口会变**：本机直连 GitHub 是 `Connection reset`，必须走代理；
  脚本按「环境变量 → Windows 系统代理 → 7890/7891/7892」探活，只挑**当前真的通**的那条，
  并用 `HTTP/1.1` 避免代理下的 TLS 握手失败；
- **push 会半路失败**：脚本最多重试 3 轮，每轮先探活、必要时 rebase，
  最后用 `ls-remote` 核对远端 SHA 与本地 HEAD 一致，避免"以为推上去了"；
- **dist 会被占用**：应用还开着 / 杀毒在扫时，PyInstaller 删不掉目录，
  会留下"exe 在、`_internal` 缺一半"的残缺包（上一次"所有数据源不可用"
  就是缺 `certifi\cacert.pem`）。`pack.ps1` 先结束进程、带重试清理，
  并在最后**强制校验 certifi 与安装包存在**；
- **构建脚本退出码不可靠**：`build_app.ps1` 在 PowerShell 5.1 下末尾固定返回 1
  （闸门其实都过了），所以 `pack.ps1` 以产物为准判断成功。

## 常用参数

```powershell
# 只看看远端有没有新提交，先不合并
tools\update-code.ps1 -NoPull

# 指定代理（默认自动探测）
tools\update-code.ps1 -Proxy http://127.0.0.1:7890

# 提交时把新文件一起带上（未跟踪文件不会自动加入）
tools\push-code.ps1 -Message "feat(video): ..." -Include tests\test_new.py, docs\NEW.md

# 只出 onedir、不出安装包；保留正在运行的应用
tools\pack.ps1 -NoInstaller -KeepRunning
```

## 约定

- `push-code.ps1` **不替你决定**提交信息：有改动但没给 `-Message` 会直接报错；
- `pack.ps1` 默认会结束正在运行的墨软工作台（否则 dist 删不掉），不想这样用 `-KeepRunning`；
- 三个脚本都不改 `packaging\build_app.ps1` 的参数语义，只是在其外面加保护与校验。
