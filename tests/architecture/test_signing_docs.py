"""代码签名材料的完整性闸门。

## 为什么需要它

[SignPath Foundation](https://signpath.org) 给开源项目免费签名有硬性条件，
其中两条是**写在仓库里**的：

1. 项目首页必须有 **Code signing policy** 段，且包含指定措辞
   "Free code signing provided by SignPath.io, certificate by SignPath Foundation"；
2. 该段要写明团队角色（Authors / Reviewers / Approvers）与隐私政策。

这些内容一旦被后续的重构/文档整理顺手删掉，申请会被驳回，而**删除的那一刻
不会有任何报错**。所以用测试把它们钉住。

本文件只检查"材料是否还在、关键措辞是否还在"，不判断内容质量。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]   # tests/architecture/xxx.py -> 仓库根
README = ROOT / "README.md"
APPLICATION = ROOT / "docs" / "SIGNPATH_APPLICATION.md"
SIGNING = ROOT / "docs" / "SIGNING.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README.is_file(), "缺少 README.md"
    return README.read_text(encoding="utf-8")


def test_readme_has_code_signing_policy_section(readme_text: str) -> None:
    """README 必须有 `## Code signing policy` 段（SignPath 的硬性要求）。"""
    assert "## Code signing policy" in readme_text, (
        "README 缺少 `## Code signing policy` 段 —— SignPath 申请会被驳回"
    )


def test_policy_contains_required_wording(readme_text: str) -> None:
    """该段必须包含 SignPath 指定的那句措辞（原文照抄）。"""
    assert "Free code signing provided by" in readme_text
    assert "SignPath.io" in readme_text
    assert "certificate by" in readme_text
    assert "SignPath Foundation" in readme_text


def test_policy_lists_the_three_roles(readme_text: str) -> None:
    """必须写明 Authors / Reviewers / Approvers 三个角色及其成员。"""
    for role in ("Authors", "Reviewers", "Approvers"):
        assert role in readme_text, f"Code signing policy 缺少角色：{role}"


def test_policy_states_privacy(readme_text: str) -> None:
    """必须给出隐私声明（本项目：默认离线、联网功能需用户显式配置、无遥测）。"""
    assert "隐私政策" in readme_text
    assert "遥测" in readme_text, "隐私声明应明确说明没有遥测/埋点"
    assert "不会向其它联网系统传输任何信息" in readme_text


def test_policy_does_not_overclaim_signature_status(readme_text: str) -> None:
    """在签名尚未生效时，不能声称"已签名"。

    这是一条**诚实性检查**：审核方与用户都会读这段。
    """
    section_start = readme_text.find("## Code signing policy")
    assert section_start >= 0
    section = readme_text[section_start: section_start + 3000]
    # 只查"断言当前状态"的说法；允许出现
    # 「获批后此处会改为『已签名』」这种描述未来的句子
    overclaims = (
        "发布物已签名",
        "发布物已完成签名",
        "已获得 SignPath 签名",
        "签名已生效",
        "已通过 SignPath 审核",
        "已获批准",
    )
    for overclaim in overclaims:
        assert overclaim not in section, (
            f"Code signing policy 里断言了「{overclaim}」，但签名尚未生效 —— "
            "获批前不能这样写"
        )


@pytest.mark.parametrize(
    "path,keywords",
    [
        (SIGNING, ["SignPath Foundation", "Certum", "Azure Trusted Signing", "HSM"]),
        (APPLICATION, ["apply", "MFA", "artifact configuration", "驳回"]),
    ],
)
def test_signing_docs_exist_with_key_content(path: Path, keywords: list[str]) -> None:
    """签名相关文档要存在，且覆盖关键内容（避免被"精简"成空壳）。"""
    assert path.is_file(), f"缺少文档：{path.name}"
    text = path.read_text(encoding="utf-8")
    for keyword in keywords:
        assert keyword in text, f"{path.name} 缺少关键内容：{keyword}"
