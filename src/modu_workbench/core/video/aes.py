"""AES-128-CBC 解密（纯 Python 标准库实现，零依赖）。

为什么自己实现：HLS 的 AES-128 分片加密非常常见（第三方采集站大量使用），
而本项目**不希望为了解密而强依赖 ffmpeg 或 cryptography** ——
否则用户没装 ffmpeg 就会看到「该视频流已加密，需要 ffmpeg」这种无谓的失败。

正确性由 FIPS-197 官方测试向量与 NIST SP 800-38A CBC 向量保证
（见 tests/test_video_aes.py）。只实现「解密」，用于播放用户本就有权访问的流。
"""
from __future__ import annotations

_SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)

_INV_SBOX_LIST = [0] * 256
for _i, _value in enumerate(_SBOX):
    _INV_SBOX_LIST[_value] = _i
_INV_SBOX = tuple(_INV_SBOX_LIST)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(value: int) -> int:
    value <<= 1
    if value & 0x100:
        value = (value ^ 0x1B) & 0xFF
    return value


def _mul(a: int, b: int) -> int:
    """GF(2^8) 乘法。"""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result & 0xFF


def _expand_key(key: bytes) -> list[list[int]]:
    """AES-128 密钥扩展 → 11 组轮密钥（每组 16 字节）。"""
    if len(key) != 16:
        raise ValueError("AES-128 需要 16 字节密钥")
    words = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for index in range(4, 44):
        temp = list(words[index - 1])
        if index % 4 == 0:
            temp = temp[1:] + temp[:1]                        # RotWord
            temp = [_SBOX[byte] for byte in temp]             # SubWord
            temp[0] ^= _RCON[index // 4 - 1]
        words.append([words[index - 4][i] ^ temp[i] for i in range(4)])
    return [sum(words[i * 4:i * 4 + 4], []) for i in range(11)]


def _inv_shift_rows(state: list[int]) -> list[int]:
    """InvShiftRows：第 1/2/3 行分别右移 1/2/3 字节。"""
    return [
        state[0], state[13], state[10], state[7],
        state[4], state[1], state[14], state[11],
        state[8], state[5], state[2], state[15],
        state[12], state[9], state[6], state[3],
    ]


def _inv_mix_columns(state: list[int]) -> list[int]:
    mixed = [0] * 16
    for column in range(4):
        offset = column * 4
        a0, a1, a2, a3 = state[offset:offset + 4]
        mixed[offset + 0] = _mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9)
        mixed[offset + 1] = _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13)
        mixed[offset + 2] = _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11)
        mixed[offset + 3] = _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14)
    return mixed


def _decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    """解密单个 16 字节分组（等价逆密码）。"""
    state = [block[i] ^ round_keys[10][i] for i in range(16)]
    for round_index in range(9, 0, -1):
        state = _inv_shift_rows(state)
        state = [_INV_SBOX[byte] for byte in state]
        state = [state[i] ^ round_keys[round_index][i] for i in range(16)]
        state = _inv_mix_columns(state)
    state = _inv_shift_rows(state)
    state = [_INV_SBOX[byte] for byte in state]
    return bytes(state[i] ^ round_keys[0][i] for i in range(16))


def aes128_cbc_decrypt(data: bytes, key: bytes, iv: bytes, *, unpad: bool = True) -> bytes:
    """AES-128-CBC 解密。

    参数：
    - data ：密文（长度须为 16 的倍数）
    - key  ：16 字节密钥
    - iv   ：16 字节初始向量
    - unpad：是否去掉 PKCS#7 填充（HLS AES-128 分片需要去填充）
    """
    if len(key) != 16:
        raise ValueError(f"AES-128 密钥应为 16 字节，实际 {len(key)}")
    if len(iv) != 16:
        raise ValueError(f"IV 应为 16 字节，实际 {len(iv)}")
    if not data:
        return b""
    if len(data) % 16 != 0:
        raise ValueError(f"密文长度应为 16 的倍数，实际 {len(data)}")

    round_keys = _expand_key(key)
    out = bytearray()
    previous = iv
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        plain = _decrypt_block(block, round_keys)
        out.extend(bytes(plain[i] ^ previous[i] for i in range(16)))
        previous = block

    result = bytes(out)
    if unpad:
        pad = result[-1]
        if 1 <= pad <= 16 and result.endswith(bytes([pad]) * pad):
            result = result[:-pad]
    return result


__all__ = ["aes128_cbc_decrypt"]
