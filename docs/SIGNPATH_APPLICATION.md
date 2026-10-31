# SignPath Foundation 申请包（SignPath Application Packet）

> 目的：把 <https://signpath.org/apply> 需要的信息一次备齐，照着填即可。
> 条款依据：[signpath.org/terms](https://signpath.org/terms)（2026-10 核对）。
> 相关文件：[`SIGNING.md`](SIGNING.md)（背景与三条路径对比）、
> [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)（用户侧症状）。

---

## 一、提交前的自检（全部 ✅ 才提交）

| 条件 | 本项目 | 核对方式 |
|---|---|---|
| OSI 认证许可证，且**无商业双许可** | MIT | `LICENSE`、`pyproject.toml` |
| 不含专有组件 | 全自研，依赖皆 OSS | `pyproject.toml` 的 dependencies |
| 项目在维护 | 持续迭代 | `CHANGELOG.md` |
| 项目已发布 | v2.0.0 → v2.2.0 | Releases / `dist/` |
| 功能有文档 | `README.md` + `docs/` | — |
| **公开仓库** | <https://github.com/zhaoyuhanga/fileType> | 已确认公开 |
| **可从源码可验证构建** | `.github/workflows/build.yml` | Actions 上 `build` 已绿 |
| 首页有 **Code signing policy** | `README.md` 的 `## Code signing policy` | 已添加 |
| 产物带 ProductName / FileVersion | `packaging/version_info.txt` | 已核对 |
| 团队 MFA | 需你自己确认开启 | 见下文「三」 |

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
dist/ModuWorkbench/ModuWorkbench.exe   （主程序）
dist/ModuWorkbench-<sha>.zip           （整个 onedir 目录，供免安装使用）
（可选）墨软工作台-Setup-<version>.exe （NSIS 安装包）
```

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
| Artifact configuration slug | `zip` |

### 2. Artifact configuration（必须限制元数据）

条款要求签名产物**必须用 metadata restrictions 强制** ProductName / ProductVersion。
可参考的配置骨架（按 SignPath 的 artifact configuration 语法填写 `<zip>` 下的
`<pe-file>` 节点）：

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

> `${version}` 由 SignPath 的 build metadata / 参数传入；
> 具体字段名以其文档为准（artifact-configuration/reference）。

### 3. 在仓库里配置 secrets

```
SIGNPATH_API_TOKEN
SIGNPATH_ORGANIZATION_ID
```

### 4. 启用工作流里预留的签名作业

`.github/workflows/build.yml` 末尾有一段**注释掉的** `sign` 作业，取消注释即可。
它已引用 `steps.upload.outputs.artifact-id`（上传步骤的 `id: upload` 已就位）。

**注意**：工作流里只负责上传产物与调用 SignPath；**每个发布版本都要在
SignPath 界面人工批准一次签名**（条款要求，不可自动化绕过）。

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

- [x] 公开仓库
- [x] CI 可验证构建并通过
- [x] README 有 Code signing policy
- [x] 产物元数据齐备
- [x] 工作流预留签名作业（含 `id: upload`）
- [ ] **开启 MFA**（需账号操作）
- [ ] **提交申请表**（需账号操作）
- [ ] 获批后配置 secrets 与 SignPath 项目
