# 代码签名：怎么让 Windows 不再拦我们的 exe

> 用途：解决「智能应用控制已阻止可能不安全的应用」与 SmartScreen 警告。
> 现象与诊断见 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) 同名小节。

> **现状**：CI 已就绪（`.github/workflows/build.yml`），仓库已确认公开，
> 资质类条件全部满足，Code signing policy 已写进 README。
> **下一步只剩「开 MFA + 提交申请」**，材料见
> [`SIGNPATH_APPLICATION.md`](SIGNPATH_APPLICATION.md)。

## 为什么必须签名

墨软工作台**没有代码签名**，所以：

- **智能应用控制（Smart App Control）** 会直接拦下 exe（Windows 11 默认对
  全新安装的系统开启；判定按文件信誉走，换个路径都可能从能跑变不能跑）；
- **SmartScreen** 会弹「Windows 已保护你的电脑」（需要用户点"更多信息 → 仍要运行"）。

这两件事都**不是代码缺陷**，靠改程序绕不过去。唯一正解是让二进制带上一个
受信任 CA 背书（并对该二进制）的签名。

## 一个绕不开的硬约束：私钥不能再随便放

自 2023-06-01 起，CA/Browser Forum 的代码签名基线要求：**代码签名私钥必须
存放在 FIPS 140-2 Level 2 以上的硬件或云 HSM 中**。

现实影响：

- 你**拿不到**一个可以随便拷来拷去的 `.pfx` 了（新证书）；
- 主流形态变成 **U 盘令牌（EV）** 或 **云签名服务（HSM 托管）**；
- 我们的构建脚本对两种都支持（见下文"本项目的接入方式"）。

这也解释了为什么"免费签名"基本只有云服务这一条路 —— 自己搭 HSM 不现实。

---

## 三条可选路径

| 路径 | 成本 | 是否解决 SAC | 主要门槛 | 核实状态 |
|---|---|---|---|---|
| **SignPath Foundation** | **免费**（OSS 项目） | ✅（证书由基金会签发） | 公开仓库 + 可验证的 CI 构建 + 合规条款 | 已核实（[官方条款](https://signpath.org/terms)） |
| **Certum 开源代码签名** | 免费档 / 低价 | ✅ | 需提交开源项目证明，证书有效期与续期规则以其站点为准 | **需自行核实**（[Certum 代码签名](https://certum.store/code-signing.html)） |
| **Azure Trusted Signing** | 约 $9.99/月起 | ✅ | 需通过 Microsoft 身份验证，个人开发者资格与地区限制以其文档为准 | **需自行核实**（[官方文档](https://learn.microsoft.com/azure/trusted-signing/)） |

> 上面三行里只有 SignPath 是**我逐条读过官方条款**的；Certum 与 Azure 的
> 个人开发者资格、当期价格经常变，请以官方页面为准，别照抄本文档。

---

## 首选：SignPath Foundation（已核实条款）

### 它是什么

SignPath 不是 CA，也不会给你个人发证书。做法是：**证书签发给 SignPath Foundation**，
然后授权 OSS 项目在**受控的构建流水线**里用自己的名字签名。因此：

- 免费；
- 私钥在 SignPath 的 HSM 里，不落到你机器上；
- 签名意味着"这个二进制是从那个公开仓库构建出来的" —— 由技术手段强制，不是口头承诺；
- 代价：**签名者显示为 SignPath Foundation**，不是"墨软工作台"。

### 逐条对照：本项目现状 vs 要求

| 要求（[官方条款](https://signpath.org/terms)） | 本项目现状 | 差距 |
|---|---|---|
| OSI 认证许可证、且**没有商业双许可** | MIT（`LICENSE`、`pyproject.toml`） | ✅ 满足 |
| 不包含专有组件 | 全部代码自研；依赖均为 OSS（PySide6 为 LGPL） | ✅ 满足 |
| 项目**在维护** | 持续迭代（见 `CHANGELOG.md`） | ✅ 满足 |
| 项目**已发布** | v2.0.0 → v2.2.0 多个发布版本 | ✅ 满足 |
| 功能有文档 | `README.md` + `docs/` | ✅ 满足 |
| **能从源码可验证地构建**（CI） | `.github/workflows/build.yml`：测试 → 打包 → 无界面自检 → 产物 | ✅ 满足 |
| **公开仓库** | `github.com/zhaoyuhanga/fileType`（已确认公开） | ✅ 满足 |
| 首页要有 **Code signing policy** 段 | 无 | ❌ 缺（模板见下） |
| 签名产物必须带 **product name / version 元数据** | `packaging/version_info.txt` 已有 `ProductName`/`FileVersion` | ✅ 满足 |
| 每个 release **人工审批**签名 | — | 流程上要做 |
| 团队成员 **MFA**（GitHub + SignPath） | — | 需要开启 |
| 定义 Authors / Reviewers / Approvers 角色 | 单人项目 | 需在政策段里写明 |

### 落地步骤（按依赖顺序）

1. **确认仓库公开**（私有仓库不能申请）；
2. ~~**补 CI**~~ ✅ **已完成**：`.github/workflows/build.yml` 会在 `windows-latest` 上
   跑「架构闸门 + 全量测试 → 下载 ffmpeg → PyInstaller 打包 → 无界面自检（17 项）
   → 压缩 → SHA256」并上传产物。这就是 SignPath 认可的可验证构建来源；
3. ~~**在 README 加 Code signing policy 段**~~ ✅ **已完成**：
   `README.md` 的 `## Code signing policy` 已包含 SignPath 要求的措辞、
   团队角色与隐私政策；
4. **开启 GitHub 与 SignPath 账号的 MFA**（需你本人操作，步骤见申请包「三」）；
5. **提交申请**：<https://signpath.org/apply> —— 表单答案已备好，
   见 [`SIGNPATH_APPLICATION.md`](SIGNPATH_APPLICATION.md)（可直接粘贴）；
6. 通过后按 SignPath 文档建 Project、配置 artifact configuration
   （要求限制 `ProductName` / `ProductVersion`），并把签名步骤接进 CI；
7. 每个发布版本在 SignPath 里**人工批准**一次。

### Code signing policy 模板（申请时用，获批后再贴到 README）

```markdown
## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

- Committers and reviewers: [repository owner](https://github.com/zhaoyuhanga)
- Approvers: [repository owner](https://github.com/zhaoyuhanga)

Privacy policy: this program will not transfer any information to other
networked systems unless specifically requested by the user or the person
installing or operating it. （在线书源/影视源/大模型等联网能力均需用户显式
启用并自行配置，详见 README。）
```

> ⚠️ 这段里的"Free code signing provided by…"是**事实陈述**。
> 只有申请通过、签名真的生效后才应放进 README；申请阶段可先随申请材料提交。

---

## 本项目的接入方式（签名能力已就绪）

构建脚本已支持签名，**拿到证书/服务后用一条命令即可**：

```powershell
# ① 证书在证书存储里（EV U 盘令牌、或导入到存储的证书）
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer -Sign `
  -CertificateThumbprint <证书指纹>

# ② 用 .pfx（仅旧证书或自建 CA 场景可用）
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer -Sign `
  -PfxPath D:\cert.pfx -PfxPassword "密码"
```

实现细节：

- 有 `signtool`（Windows SDK）就用它 —— 支持 EV 令牌与 RFC3161 时间戳；
- 没装 SDK 也能签：自动回退到 PowerShell 原生 `Set-AuthenticodeSignature`；
- 签完会复核 `AuthenticodeSignature.Status`，若仍是 `UnknownError`
  说明根证书不受信（自签名常见），脚本会明确提示"智能应用控制不会接受"。

**走 SignPath 的情况**：签名发生在他们的服务端（CI 里调 SignPath 的
GitHub Action / `Submit-SigningRequest`），本地脚本的 `-Sign` 就不需要用了 ——
本地构建只负责产出未签名产物供 CI 参考。

### 自签名证书为什么不行

可以签，但**治不了病**：

```
签名结果: UnknownError
  A certificate chain processed, but terminated in a root certificate
  which is not trusted by the trust provider
```

Smart App Control 只认"受信任公共 CA + 可信发布者"。自签名最多让
`Get-AuthenticodeSignature` 显示个签名者，对 SAC/SmartScreen 没有任何帮助。
（本仓库曾用临时自签名证书验证签名**管道**可用，验证后已删除证书。）

---

## 不签名时的替代方案

1. **随附 SHA256 校验和**（构建已自动生成 `dist/SHA256SUMS.txt`）：
   让用户能确认文件没被篡改，这是未签名发布物的最低要求；
2. **文档写清**：`TROUBLESHOOTING.md` 里给了 SAC 的诊断命令与三条出路；
3. **便携版**：`dist\ModuWorkbench\` 免安装，实测在 SAC 强制模式下仍可运行
   —— 但可能过一段时间也被标记，只适合临时使用。

## 参考

- [SignPath Foundation 条款](https://signpath.org/terms)（OSS 免费签名的条件）
- [SignPath 项目列表](https://signpath.org/projects)（stellarium、Flameshot 等在用）
- [Certum 代码签名](https://certum.store/code-signing.html)
- [Azure Trusted Signing](https://learn.microsoft.com/azure/trusted-signing/)
- [CA/Browser Forum 代码签名基线（私钥保护要求）](https://cabforum.org/working-groups/code-signing/)
