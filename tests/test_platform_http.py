"""统一 HTTP 客户端的两个"环境坑"：系统代理与 CA 证书。

## 为什么单独测

这两件事都**不在代码里出错，而在环境里出错**，而且症状一样：所有源一起
"不可用"，用户很难自己定位。

1. `requests` 只认 `HTTP_PROXY/HTTPS_PROXY` 环境变量，**不读 Windows 系统代理**；
   国内用户几乎都用 Clash/v2ray 的「系统代理」，于是浏览器正常、应用全挂；
2. 打包漏了 `certifi/cacert.pem` 时，所有 https 请求直接抛
   `Could not find a suitable TLS CA certificate bundle`。

这里的契约：没设环境变量时回退系统代理；本机地址绝不走代理；CA 缺失时
先找系统证书，并把最终错误翻译成"安装包不完整"。
"""
from __future__ import annotations

import ssl

import certifi
import pytest

from modu_workbench.core.platform import http as http_module


def test_parse_proxy_server_single_port() -> None:
    parsed = http_module.parse_proxy_server("127.0.0.1:7890")
    assert parsed == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_parse_proxy_server_per_scheme_and_socks() -> None:
    parsed = http_module.parse_proxy_server(
        "http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7891"
    )
    assert parsed["http"] == "http://127.0.0.1:7890"
    assert parsed["https"] == "http://127.0.0.1:7890"
    assert parsed["socks"] == "socks5://127.0.0.1:7891", "socks 代理被当成了 http"


def test_parse_proxy_server_empty() -> None:
    assert http_module.parse_proxy_server("") is None
    assert http_module.parse_proxy_server("   ") is None


def test_client_uses_system_proxy_when_env_absent(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setattr(
        http_module, "system_proxy", lambda: {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    )
    client = http_module.HttpClient()
    assert client.proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_client_prefers_env_proxy_over_system(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(http_module, "system_proxy", lambda: {"http": "http://127.0.0.1:7890"})
    client = http_module.HttpClient()
    assert client.proxies is None, "设了环境变量代理时不应再叠加系统代理"


def test_localhost_never_uses_proxy() -> None:
    client = http_module.HttpClient()
    client.proxies = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    assert client._proxies_for("http://127.0.0.1:8080/x") is None  # noqa: SLF001
    assert client._proxies_for("http://localhost:8080/x") is None  # noqa: SLF001
    assert client._proxies_for("https://api.example.com/x") is not None  # noqa: SLF001


def test_ca_bundle_falls_back_when_certifi_missing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(certifi, "where", lambda: "D:/definitely-missing/cacert.pem")
    monkeypatch.setattr(
        ssl, "get_default_verify_paths", lambda: ssl.DefaultVerifyPaths(None, None, None, None)
    )
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "")
    assert http_module.ca_bundle() is True


def test_ca_bundle_error_message_is_actionable() -> None:
    text = http_module.describe_network_error(
        OSError("Could not find a suitable TLS CA certificate bundle, invalid path: X")
    )
    assert "安装包" in text and "certifi" in text


def test_ca_bundle_uses_certifi_when_present() -> None:
    if not certifi.where():
        pytest.skip("本机没有 certifi")
    assert http_module.ca_bundle() == certifi.where()
