"""内置视频源清单的结构自检（离线：不联网、不依赖任何采集站存活）。

维护约定见 `core/video/sources/providers/__init__.py` 里 `CMS_SITES` 上方注释：
- 清单里每一项都必须是「人工实测能用」的地址，失效的源直接替换而不是留着；
- 同一上游换域名的站点只保留一个（避免聚合搜索重复请求同名结果）。

因此这里只锁定「清单本身不会写错」的结构性约束：
必填字段、key 唯一且命名规范、接口地址不重复、清单与默认顺序表一致、
构造内置源不产生任何网络请求。**接口是否依然可用属于人工实测，不写成联网测试。**
"""
from __future__ import annotations

import re

import pytest
import requests

from modu_workbench.core.video.sources.providers import (
    CMS_SITE_BY_KEY,
    CMS_SITES,
    DEFAULT_PROVIDER_ORDER,
    build_cms_providers,
    build_default_providers,
)

# 非采集类内置源（自由授权 / 直链 / 自定义），必须始终留在默认顺序表里
NON_CMS_KEYS = ("archive", "wikimedia", "url", "custom")

CMS_KEY_RE = re.compile(r"^cms_[a-z0-9]+$")


def test_cms_sites_have_required_fields_and_unique_keys() -> None:
    """每个采集站都要有 key/label/base_url/note，且 key、地址不重复。"""
    assert CMS_SITES, "内置采集站清单不能为空"
    keys: list[str] = []
    urls: list[str] = []
    for site in CMS_SITES:
        assert set(site) == {"key", "label", "base_url", "note"}, f"字段不合法：{site}"
        key, label = site["key"], site["label"]
        assert CMS_KEY_RE.match(key), f"key 命名不规范：{key}"
        assert label.strip(), f"{key} 缺少中文名称"
        assert site["base_url"].startswith(("http://", "https://")), f"{key} 接口地址不合法"
        # note 沿用「站名 · 说明」的既有风格，源设置里直接展示给用户
        assert site["note"].startswith(label), f"{key} 的说明应以站名开头"
        keys.append(key)
        urls.append(site["base_url"].rstrip("/"))
    assert len(keys) == len(set(keys)), f"key 重复：{keys}"
    assert len(urls) == len(set(urls)), f"接口地址重复：{urls}"


def test_cms_sites_match_default_provider_order() -> None:
    """清单与默认顺序表必须一一对应，且顺序一致（顺序即聚合优先级）。"""
    cms_keys = [site["key"] for site in CMS_SITES]
    order = list(DEFAULT_PROVIDER_ORDER)

    assert len(order) == len(set(order)), f"默认顺序表有重复项：{order}"
    assert set(cms_keys) <= set(order), "有采集站没写进 DEFAULT_PROVIDER_ORDER"
    assert set(order) - set(cms_keys) == set(NON_CMS_KEYS), "默认顺序表多了未知的源"
    # 顺序一致：采集站在顺序表里的先后关系要和清单完全相同
    assert [key for key in order if key in set(cms_keys)] == cms_keys
    assert set(CMS_SITE_BY_KEY) == set(cms_keys)


def test_cms_sites_api_url_is_built_correctly() -> None:
    """适配器要能把这些 base_url 拼成 `…/api.php/provide/vod` 接口地址。"""
    for provider in build_cms_providers():
        api = provider._api()  # noqa: SLF001  这里就是要锁拼接规则
        assert api.endswith("provide/vod"), f"{provider.key} 接口地址拼接异常：{api}"
        assert api.startswith("http"), f"{provider.key} 接口地址不合法：{api}"


def test_build_default_providers_needs_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造内置源清单不能联网（离线可用）；每个采集站都要带默认接口地址。"""

    def _offline(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("构造视频源时不应该发起网络请求")

    monkeypatch.setattr(requests, "request", _offline)
    monkeypatch.setattr(requests, "get", _offline)

    providers = build_default_providers()
    by_key = {provider.key: provider for provider in providers}
    assert list(by_key) == list(DEFAULT_PROVIDER_ORDER), "构造顺序应与默认顺序表一致"
    assert len(providers) == len(DEFAULT_PROVIDER_ORDER)

    for site in CMS_SITES:
        provider = by_key[site["key"]]
        assert provider.available(), f"{site['key']} 缺少接口地址"
        assert provider.credential == site["base_url"].rstrip("/")
        assert provider.info.editable, f"{site['key']} 应允许在源设置里改地址"

    for key in NON_CMS_KEYS:
        assert by_key[key].key == key
