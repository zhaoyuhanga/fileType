"""文档板块 · 隐私脱敏（`core/document/security.py`）测试。

## 为什么这个模块必须有测试

脱敏是**隐私相关**功能：用户拿它处理身份证、手机号、银行卡、邮箱。
它此前**零测试覆盖**，而这类代码的失败方式特别危险：

- **漏脱**（正则没匹配上）→ 用户以为脱敏了，实际把原件发了出去；
- **过度脱敏**（把不该动的数字也打了码）→ 文档不可用；
- **保留位数不对**（该留 4 位却留了 6 位）→ 等于没脱干净。

所以这里不只是"函数能跑"，而是把**每类规则保留哪些位**逐条钉死 ——
这些数字是脱敏强度的直接体现，改动必须是有意识的。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.document import security as S


def test_default_rules_cover_the_common_pii() -> None:
    """默认启用的规则必须包含最常泄露的五类个人信息。"""
    assert set(S.DEFAULT_MASK_RULES) == {"phone", "id_card", "bank_card", "email", "amount"}


def test_every_default_rule_is_selectable_and_labelled() -> None:
    """每个默认规则都要有中文标签（设置页直接展示），且能出现在选项列表里。"""
    keys = {key for key, _label in S.mask_rule_choices()}
    for key in S.DEFAULT_MASK_RULES:
        assert key in keys, f"{key} 不在可选项里"
        label = S.mask_label(key)
        assert label and label != key, f"{key} 没有中文标签"


# --------------------------------------------------------------------------- 逐类规则


def test_phone_keeps_first_three_and_last_four() -> None:
    """手机号：保留 138 + 末 4 位。

    保留前 3 位是为了仍能看出号段（便于人工核对归属地），但中间 4 位必须打掉。
    """
    masked, counts = S.mask_text("联系电话 13812345678", ["phone"])
    assert masked == "联系电话 138****5678"
    assert counts == {"phone": 1}


def test_id_card_keeps_six_and_four() -> None:
    """身份证：保留前 6 位（地区码）+ 末 4 位；中间 8 位（含出生日期）必须打掉。"""
    masked, _counts = S.mask_text("身份证 110101199003071234", ["id_card"])
    assert masked == "身份证 110101********1234"
    assert "19900307" not in masked, "出生日期必须被打掉"


def test_bank_card_keeps_grouping() -> None:
    """银行卡：保留分组空格（可读性）与末 4 位。"""
    masked, _counts = S.mask_text("卡号 6222 0212 3456 7890", ["bank_card"])
    assert masked == "卡号 **** **** **** 7890"


def test_email_keeps_first_char_and_domain() -> None:
    """邮箱：保留首字母与域名（能判断是哪家邮箱），本地部分其余打掉。"""
    masked, _counts = S.mask_text("邮箱 zhangsan@example.com", ["email"])
    assert masked == "邮箱 z*******@example.com"
    assert "@example.com" in masked


def test_amount_is_masked_entirely() -> None:
    """金额：整数部分全部打掉（只留小数点位置提示量级）。"""
    masked, _counts = S.mask_text("金额 12345.67 元", ["amount"])
    assert "12345" not in masked
    assert masked.endswith("元")


# --------------------------------------------------------------------------- 边界


def test_only_selected_rules_apply() -> None:
    """**只处理勾选的规则**：不勾邮箱就不该动邮箱。

    这条很重要 —— 用户可能只想脱手机号，其它内容要原样保留。
    """
    text = "电话 13812345678 邮箱 zhangsan@example.com"
    masked, counts = S.mask_text(text, ["phone"])
    assert "138****5678" in masked
    assert "zhangsan@example.com" in masked, "未勾选的邮箱规则不应生效"
    assert set(counts) == {"phone"}


def test_multiple_occurrences_all_masked() -> None:
    """同一类出现多次都要打掉（不能只替换第一个）。"""
    text = "甲 13812345678，乙 13987654321"
    masked, counts = S.mask_text(text, ["phone"])
    assert counts["phone"] == 2
    assert "13812345678" not in masked and "13987654321" not in masked


def test_text_without_pii_is_unchanged() -> None:
    """没有敏感内容时**原文返回**，不能改动任何字符。

    这条用例曾经**抓到过一个真 bug**：金额规则原来是裸的三位以上数字匹配，
    把「2024 年」这种普通数字也打了码（`**** 年`），
    属于过度脱敏 —— 文档里的年份/编号会被毁掉。已改成"必须带金额特征"。
    """
    text = "这是一段没有任何敏感信息的普通文字，包含数字 2024 年。"
    masked, counts = S.mask_text(text, S.DEFAULT_MASK_RULES)
    assert masked == text
    assert counts == {}


@pytest.mark.parametrize(
    "plain",
    [
        "2024 年",
        "2024年1月",
        "第 3 章",
        "文件编号 10086",
        "共 5 人",
        "版本 2.2.0",
    ],
)
def test_plain_numbers_are_not_masked_as_amount(plain: str) -> None:
    """普通数字（年份/章节号/编号/版本号）不能被金额规则误伤。

    这是**过度脱敏**的回归防线：金额必须带货币符号、千分位、小数或单位之一。
    """
    masked, counts = S.mask_text(plain, ["amount"])
    assert masked == plain, f"{plain!r} 被误当成金额打了码：{masked!r}"
    assert counts == {}


@pytest.mark.parametrize(
    "money",
    ["金额 12345.67 元", "价格 998 元", "1,234,567.00", "成本 5.5 万", "1234.56"],
)
def test_real_amounts_are_still_masked(money: str) -> None:
    """真正的金额仍必须被打码（收紧规则不能把功能也关掉）。"""
    _masked, counts = S.mask_text(money, ["amount"])
    assert counts.get("amount") == 1, f"{money!r} 应该被识别为金额"


def test_empty_and_blank_text_are_safe() -> None:
    """空文本不抛异常（界面可能传入空串）。"""
    for text in ("", "   ", "\n"):
        masked, counts = S.mask_text(text, S.DEFAULT_MASK_RULES)
        assert masked == text
        assert counts == {}


def test_unknown_rule_key_is_ignored() -> None:
    """传了不存在的规则 key 不应崩溃、也不应神秘改动文本。"""
    text = "电话 13812345678"
    masked, counts = S.mask_text(text, ["phone", "不存在的规则"])
    assert masked == "电话 138****5678"
    assert counts == {"phone": 1}


def test_custom_mask_char() -> None:
    """可以换打码字符（例如用 # 代替 *）。"""
    masked, _counts = S.mask_text("13812345678", ["phone"], char="#")
    assert masked == "138####5678"


def test_scan_reports_counts_without_modifying() -> None:
    """`scan_sensitive` 只统计不修改（预览用：先告诉用户会脱掉几处）。"""
    text = "电话 13812345678，身份证 110101199003071234，邮箱 a@b.com"
    counts = S.scan_sensitive(text, S.DEFAULT_MASK_RULES)
    assert counts["phone"] == 1
    assert counts["id_card"] == 1
    assert counts["email"] == 1
    # 原文不受影响
    assert "13812345678" in text


def test_mask_report_records_changes() -> None:
    """整篇脱敏返回 (新 IR, 报告)：报告含统计与逐条变更（审计日志要用）。

    `mask_document` 接的是 `DocumentIR`（不是纯文本）——
    走文本层的是 `mask_text`。
    """
    from modu_workbench.core.document.models import BLOCK_PARAGRAPH, Block, DocumentIR

    ir = DocumentIR(title="测试文档")
    ir.blocks = [Block(kind=BLOCK_PARAGRAPH, text="电话 13812345678")]

    masked_ir, report = S.mask_document(ir, ["phone"])
    assert isinstance(report, S.MaskReport)
    assert report.counts.get("phone") == 1
    assert report.changes, "应当记录每一处替换，供审计追溯"
    assert "13812345678" not in masked_ir.blocks[0].text
    # 不改动原对象（脱敏产出新 IR，便于"预览 / 撤销"）
    assert ir.blocks[0].text == "电话 13812345678"


def test_idempotent_on_already_masked_text() -> None:
    """对已经打过码的文本再跑一次，不应二次破坏（避免"***"被继续打码）。"""
    text = "电话 138****5678"
    masked, _counts = S.mask_text(text, ["phone"])
    assert "138****5678" in masked
    assert "****" in masked


# --------------------------------------------------------------------------- 权限


def test_permissions_defaults_are_conservative() -> None:
    """权限默认值必须**保守**：读不到设置时，不允许云端、且上传前必须先脱敏。"""
    # getter 返回默认值（模拟"用户什么都没配"）
    permissions = S.permissions_from_settings(lambda _key, default: default)
    assert permissions.allow_cloud is False, "默认不允许把文档发到云端"
    assert permissions.mask_before_upload is True, "默认上传前必须脱敏"
    assert permissions.max_chars > 0, "必须有内容长度上限"
    assert "phone" in permissions.mask_rules, "默认脱敏规则应包含手机号"


def test_permissions_read_from_settings() -> None:
    """设置里显式开了云端时，权限对象要如实反映（不能一律强制 False）。"""
    values = {"document/allow_cloud": True, "document/max_chars": 500}
    permissions = S.permissions_from_settings(lambda key, default: values.get(key, default))
    assert permissions.allow_cloud is True
    assert permissions.max_chars == 500
