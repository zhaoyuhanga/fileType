# SignPath Foundation 申请包（SignPath Application Packet）

> 目的：把 <https://signpath.org/apply> 需要的信息一次备齐，照着填即可。
> 条款依据：[signpath.org/terms](https://signpath.org/terms)（**2026-11-21 重新逐条核对**，
> 核对结论见第八节；GitHub 侧依据 [docs.signpath.io/trusted-build-systems/github](https://docs.signpath.io/trusted-build-systems/github)）。
> 相关文件：[`SIGNING.md`](SIGNING.md)（背景与三条路径对比）、
> [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)（用户侧症状）。

---

## 一、提交前的自检（全部 ✅ 才提交）

| 条件 | 本项目 | 核对方式 |
|---|---|---|
| OSI 认证许可证，且**无商业双许可** | MIT | `LICENSE`、`pyproject.toml` |
| 不含专有组件 | 全自研，依赖皆 OSS | `pyproject.toml` 的 dependencies |
| 项目在维护 | 持续迭代 | `CHANGELOG.md` |
| 项目已发布（**以要签名的形态发布**） | GitHub Releases 已有 v2.0.1 / v2.1.0 / v2.1.1 / v2.2.0；**v2.3.0 需先发 Release**（带 onedir zip + Setup + SHA256SUMS） | Releases 页 |
| 功能有文档 | `README.md` + `docs/` | — |
| **公开仓库** | <https://github.com/zhaoyuhanga/fileType> | 已确认公开 |
| **可从源码可验证构建** | `.github/workflows/build.yml`（`windows-latest` 的 GitHub 托管 runner） | Actions 上 `build` 徽章 `passing` |
| 首页有 **Code signing policy** | `README.md` 的 `## Code signing policy`：含 SignPath 要求的原句、团队角色、隐私政策（**已补条款要求的英文原文**） | 2026-11-21 核对 |
| 产物带 ProductName / FileVersion | `packaging/version_info.txt`；v2.3.0 实测 exe 与安装包都是 2.3.0 | `GetVersionInfo` |
| 团队 MFA | 需你自己确认开启 | 见下文「三」 |
| **条款要求的"可验证声誉"** | ⚠️ **本申请最大的不确定项**：条款明说 *"we cannot sign binaries based on source code that nobody knows"*、*"we require a certain verifiable reputation"*。仓库 star/使用者还很少 | 见下文「六」的缓解办法 |

---

## 二、申请表单可直接粘贴的答案

**项目名（Project name）**

```
墨软·工作台 (ModuWorkbench)
```

**项目主页 / 仓库（Repository URL）**

```
https://github.com/zhaoyuhanga/fileType
```

**许可证（License）**

```
MIT
```

**项目简介（Description，建议英文，1~3 句）**

```
ModuWorkbench (墨软·工作台) is a local-first Windows desktop workbench built with
PySide6: e-book library and reader, file/format conversion, music library,
video library with playback and download, photo manager, and document editing.
It runs fully offline by default; online features (book sources, video sources,
music sources, LLM APIs) are strictly opt-in and require user-provided endpoints.
```

**发布形式（Releases / how users obtain binaries）**

```
Windows onedir + NSIS installer built in GitHub Actions from this repository:
https://github.com/zhaoyuhanga/fileType/actions/workflows/build.yml
Local build: `powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer`
```

**Code signing policy 链接**

```
https://github.com/zhaoyuhanga/fileType#code-signing-policy
```

**需要签名的产物**

```
dist/ModuWorkbench-<sha>.zip   （onedir 整个目录；zip **根级**就是 ModuWorkbench.exe）
dist/ModuWorkbench/ModuWorkbench.exe   （zip 里那个被签的主程序）
（可选）墨软工作台-Setup-<version>.exe （NSIS 安装包；单文件 PE，可单独配一条 artifact configuration）
```

> ⚠️ 这两处细节曾经写错过，签名会在**人工批准之后**才报错，白等一轮：
> 1. zip 的条目必须是 `ModuWorkbench.exe`，**不能是** `./ModuWorkbench.exe`
>    （CI 原来用 `tar ... -C dist\ModuWorkbench .`，条目会带 `./` 前缀，已改成在该目录里用 `*` 打包）；
> 2. 传给 SignPath 的是 **GitHub artifact 本身**。要让它就是那个 zip 文件，
>    上传时必须 `archive: false`（否则 GitHub 会再包一层 zip），并给 action 配
>    `skip-decompress: true`。

---

## 三、提交前你必须自己做的两步

### 1. 开启 MFA（SignPath 的硬性要求）

- GitHub：<https://github.com/settings/security> → Two-factor authentication → Enable
- SignPath：注册后在其账号设置里启用双因素

条款原文：*"All team members must use multi-factor authentication for both
SignPath and source code repository access"*。**没开 MFA 会被驳回**。

### 2. 填申请表

<https://signpath.org/apply> —— 用上面的答案填写。
申请后会收到邮件，**可能需要补充说明**（例如为什么需要签名、项目使用场景）。

> 预期周期：几天到两周不等。条款明确写了*"We're under no obligation to accept
> your project"*，所以要做好被拒的准备（备选方案见第六节）。

---

## 四、获批后：接入步骤（代码已就绪）

### 1. 在 SignPath 建 Project 与 Signing Policy

建议命名（与工作流注释保持一致）：

| 项 | 建议值 |
|---|---|
| Project slug | `modu-workbench` |
| Signing policy slug | `release-signing` |
| Artifact configuration slug | `onedir-zip` |

> 这三个 slug 与 `.github/workflows/build.yml` 里注释掉的 `sign` 作业**逐字对应**；
> 改一边就要改另一边，否则请求会在提交后被拒（又是"等一轮才报错"）。

### 2. Artifact configuration（必须限制元数据）

条款要求签名产物**必须用 metadata restrictions 强制** ProductName / ProductVersion
（[*"All signed binaries must have metadata attributes set and enforced"*](https://signpath.org/terms)）。

因为上游用的是 `archive: false` + `skip-decompress: true`（见第二节末的提醒），
SignPath 直接看到的就是我们那个 zip，所以根节点是 `<zip-file>`：

```xml
<artifact-configuration xmlns="http://signpath.io/artifact-configuration">
  <zip-file>
    <pe-file path="ModuWorkbench.exe">
      <metadata>
        <product-name>墨软·工作台</product-name>
        <product-version>${version}</product-version>
        <file-version>${version}</file-version>
        <company-name>ModuWorkbench</company-name>
      </metadata>
    </pe-file>
  </zip-file>
</artifact-configuration>
```

> `${version}` 是**用户自定义参数**，由工作流里的
> `parameters: version: "${{ github.ref_name }}"` 传入（即 tag 名，如 `v2.3.0`）。
> 同一个版本的每个构建必须传同一个值 —— 条款要求"product version 在同一构建内一致"。
> 字段名以其文档为准（artifact-configuration/reference）。

### 3. 在仓库里配置 secrets

```
SIGNPATH_API_TOKEN
SIGNPATH_ORGANIZATION_ID
```

### 4. 启用工作流里预留的签名作业

`.github/workflows/build.yml` 末尾有一段**注释掉的** `sign` 作业（2026-11-21 补齐，
按官方 `signpath/github-action-submit-signing-request@v3` 的输入参数写好）。
获批后取消注释即可，配套的两处**已经就位**：

| 已就位的东西 | 在哪 | 作用 |
|---|---|---|
| 待签名原件上传（`id: upload-unsigned`，`archive: false`） | build 作业，`if: startsWith(github.ref, 'refs/tags/v')` | 只在打 tag 时上传"未签名 zip"，省得每次 push 都多一个 artifact |
| `unsigned-artifact-id` 输出 | build 作业的 `outputs` | sign 作业用 `needs.build.outputs.unsigned-artifact-id` 取它 |
| 签名复核步骤 | sign 作业末尾 | `Get-AuthenticodeSignature` 不是 `Valid` 就让作业失败（否则"签了但没用"没人发现） |

**注意**：工作流只负责上传产物与提交签名请求；**每个发布版本都要在 SignPath 界面
人工批准一次**（条款要求，不可自动化绕过），所以那一步会挂着等（默认 600 秒，
文档里已调到 3600 秒）。

### 5. 第一次签名后的验证

```powershell
# 应为 Valid，且签名者是 SignPath Foundation
Get-AuthenticodeSignature .\ModuWorkbench.exe | Format-List Status, SignerCertificate
```

真机验证：在**开启智能应用控制**的 Windows 11 上双击，应不再被拦。

---

## 五、发版流程（签名到位后）

1. 改版本号（`src/modu_workbench/__init__.py` + `pyproject.toml` +
   `packaging/version_info.txt` + `packaging/installer.nsi` + `README.md`）；
2. `git tag vX.Y.Z && git push origin vX.Y.Z`；
3. 等 CI 的 `build` 作业产出 artifact → `sign` 作业提交签名请求；
4. 在 SignPath 界面**人工批准**；
5. 下载已签名产物，附上 `SHA256SUMS.txt` 一起发布。

> 即使已签名，也建议继续提供校验和：签名证明"谁构建的"，校验和证明"文件没被替换"。

---

## 六、如果被拒

按可能性从高到低：

| 方案 | 成本 | 说明 |
|---|---|---|
| 补强项目可见度后再申请 | 时间 | 条款说得很直白：*"we cannot sign binaries based on source code that nobody knows"* —— 增加 star / 使用者 / 发布记录会提高通过率 |
| **Azure Trusted Signing** | 约 $9.99/月 | 需通过 Microsoft 身份验证；个人开发者资格与地区限制以其文档为准 |
| **Certum 开源代码签名** | 免费档 / 低价 | 规则与有效期以官网为准 |
| 不签名，保留现有降级方案 | 0 | `SHA256SUMS` + 文档说明 + 便携版；用户可选关闭 SAC |

---

## 七、当前状态

2026-11-21 逐条核对后的状态：

- [x] 公开仓库（`git ls-remote` 免凭据可读）
- [x] CI 可验证构建并通过（徽章 `passing`；`windows-latest` GitHub 托管 runner）
- [x] README 有 Code signing policy（含 SignPath 要求的原句、团队角色、隐私政策原文）
- [x] 产物元数据齐备（v2.3.0 实测 exe / 安装包的 FileVersion + ProductVersion 都是 2.3.0）
- [x] 工作流预留签名作业（`signpath/...@v3`，含 `archive: false` 上传、`unsigned-artifact-id` 输出、签名复核）
- [x] 压缩包条目已去掉 `./` 前缀（签名配置要求根级 `ModuWorkbench.exe`）
- [ ] **发 v2.3.0 Release**（带 onedir zip + Setup + SHA256SUMS）——条款要求"以要签名的形态发布"
- [ ] **开启 MFA**（需账号操作：GitHub 与 SignPath 两侧都开）
- [ ] **提交申请表**（需账号操作，<https://signpath.org/apply>）
- [ ] 获批后配置 secrets 与 SignPath 项目（slug 见第四节）

## 八、2026-11-21 的核对记录（这次的结论从哪来）

**依据**：官方条款 <https://signpath.org/terms>（当日抓取）+
GitHub 官方 action 文档 <https://docs.signpath.io/trusted-build-systems/github>。

| 核对项 | 结论 |
|---|---|
| 许可证 / 双许可 | MIT，无商业双许可 ✅ |
| 专有组件 | 无；随包的 ffmpeg 是上游 OSS 二进制，条款明确允许"包含未签名的上游二进制" ✅ |
| 维护中 / 已发布 | 有 CHANGELOG 与 4 个历史 Release ✅；**v2.3.0 尚未发 Release** ⚠️ |
| 可验证构建 | `on: push/pull_request/workflow_dispatch` + `windows-latest` ✅（条款要求 OSS 项目的相关作业必须跑在 GitHub 托管 runner 上） |
| 首页 Code signing policy | 三个必需项（SignPath 原句 / 团队角色 / 隐私政策）齐全 ✅；本次补了隐私政策的**英文原文句** |
| 不可绕过的人工批准 | 条款要求每个版本批准一次；工作流已按"挂着等审批"设计 ✅ |
| 元数据限制 | 需在 SignPath 侧配 artifact configuration（骨架见第四节）⏳ |
| MFA | ❗硬性要求，未开必被拒（需账号操作） |
| 声誉 | ⚠️ 条款原话 *"we cannot sign binaries based on source code that nobody knows"*；这是本项目最主要的被拒风险 |

**这次实际改掉的三个坑**（都会在"人工批准之后"才暴露，值得记下来）：

1. `docs` 原来写"工作流末尾有注释掉的 `sign` 作业"，但工作流里**根本没有**（只有 `test` / `build` 两个作业）—— 已补齐；
2. 文档里写的 action 是 `@v1`，官方当前是 **`@v3`**，且必填参数里有 `github-artifact-id` —— 注释块已按 v3 的输入写好；
3. CI 打的 zip 条目带 `./` 前缀（`./ModuWorkbench.exe`），与 artifact configuration 的
   `<pe-file path="ModuWorkbench.exe">` 不匹配 —— `tar` 用法已改，实测条目为 `ModuWorkbench.exe`。

