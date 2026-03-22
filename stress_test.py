"""
Stress-test: сравнение OLD vs NEW реализаций горячих функций прокси.

Тестируются:
  1. _build_frame  — сборка WS-фрейма (masked binary)
  2. _build_frame  — сборка WS-фрейма (unmasked)
  3. _socks5_reply — генерация SOCKS5-ответа
  4. _dc_from_init XOR-часть (bytes(a^b for …) vs int.from_bytes)
  5. mask key generation (os.urandom vs PRNG)
"""

import gc
import os
import random
import struct
import time

# ── Размеры данных, типичные для Telegram ──────────────────────────
SMALL = 64          # init-пакет / ack
MEDIUM = 1024       # текстовое сообщение
LARGE = 65536       # фото / голосовое


# ═══════════════════════════════════════════════════════════════════
#  XOR mask (не менялся — для полноты)
# ═══════════════════════════════════════════════════════════════════

def xor_mask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    mask_rep = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'big') ^ int.from_bytes(mask_rep, 'big')).to_bytes(n, 'big')


# ═══════════════════════════════════════════════════════════════════
#  _build_frame
# ═══════════════════════════════════════════════════════════════════

def build_frame_old(opcode: int, data: bytes, mask: bool = False) -> bytes:
    """Старая: bytearray + append/extend + os.urandom."""
    header = bytearray()
    header.append(0x80 | opcode)
    length = len(data)
    mask_bit = 0x80 if mask else 0x00

    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header.extend(struct.pack('>H', length))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack('>Q', length))

    if mask:
        mask_key = os.urandom(4)
        header.extend(mask_key)
        return bytes(header) + xor_mask(data, mask_key)
    return bytes(header) + data


# ── Новая: pre-compiled struct + PRNG ──────────────────────────────
_st_BB = struct.Struct('>BB')
_st_BBH = struct.Struct('>BBH')
_st_BBQ = struct.Struct('>BBQ')
_st_BB4s = struct.Struct('>BB4s')
_st_BBH4s = struct.Struct('>BBH4s')
_st_BBQ4s = struct.Struct('>BBQ4s')

_mask_rng = random.Random(int.from_bytes(os.urandom(16), 'big'))
_mask_pack = struct.Struct('>I').pack

def _random_mask_key() -> bytes:
    return _mask_pack(_mask_rng.getrandbits(32))

def build_frame_new(opcode: int, data: bytes, mask: bool = False) -> bytes:
    """Новая: struct.pack + PRNG mask."""
    length = len(data)
    fb = 0x80 | opcode

    if not mask:
        if length < 126:
            return _st_BB.pack(fb, length) + data
        if length < 65536:
            return _st_BBH.pack(fb, 126, length) + data
        return _st_BBQ.pack(fb, 127, length) + data

    mask_key = _random_mask_key()
    masked = xor_mask(data, mask_key)
    if length < 126:
        return _st_BB4s.pack(fb, 0x80 | length, mask_key) + masked
    if length < 65536:
        return _st_BBH4s.pack(fb, 0x80 | 126, length, mask_key) + masked
    return _st_BBQ4s.pack(fb, 0x80 | 127, length, mask_key) + masked


# ═══════════════════════════════════════════════════════════════════
#  _socks5_reply
# ═══════════════════════════════════════════════════════════════════

def socks5_reply_old(status):
    return bytes([0x05, status, 0x00, 0x01]) + b'\x00' * 6

_SOCKS5_REPLIES = {s: bytes([0x05, s, 0x00, 0x01, 0, 0, 0, 0, 0, 0])
                   for s in (0x00, 0x05, 0x07, 0x08)}

def socks5_reply_new(status):
    return _SOCKS5_REPLIES[status]


# ═══════════════════════════════════════════════════════════════════
#  dc_from_init XOR (8 байт keystream ^ data)
# ═══════════════════════════════════════════════════════════════════

def dc_xor_old(data8: bytes, ks8: bytes) -> bytes:
    """Старая: генераторное выражение."""
    return bytes(a ^ b for a, b in zip(data8, ks8))

def dc_xor_new(data8: bytes, ks8: bytes) -> bytes:
    """Новая: int.from_bytes."""
    return (int.from_bytes(data8, 'big') ^ int.from_bytes(ks8, 'big')).to_bytes(8, 'big')


# ═══════════════════════════════════════════════════════════════════
#  mask key: os.urandom(4) vs PRNG
# ═══════════════════════════════════════════════════════════════════

def mask_key_old() -> bytes:
    return os.urandom(4)

def mask_key_new() -> bytes:
    return _random_mask_key()


# ═══════════════════════════════════════════════════════════════════
#  Бенчмарк
# ═══════════════════════════════════════════════════════════════════

def bench(func, args_list: list, iters: int) -> float:
    gc.collect()
    for i in range(min(100, iters)):
        func(*args_list[i % len(args_list)])
    start = time.perf_counter()
    for i in range(iters):
        func(*args_list[i % len(args_list)])
    elapsed = time.perf_counter() - start
    return elapsed / iters * 1_000_000  # мкс


def compare(name: str, old_fn, new_fn, args_list: list, iters: int):
    t_old = bench(old_fn, args_list, iters)
    t_new = bench(new_fn, args_list, iters)
    speedup = t_old / t_new if t_new > 0 else float('inf')
    marker = '✅' if speedup >= 1.0 else '⚠️'
    print(f"  {name:.<42s} OLD {t_old:8.3f} мкс | NEW {t_new:8.3f} мкс | {speedup:5.2f}x {marker}")


# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 74)
    print("  Stress Test: OLD vs NEW (горячие функции tg_ws_proxy)")
    print("=" * 74)

    N = 500_000

    # # ── 1. _build_frame masked ────────────────────────────────────
    # print(f"\n── _build_frame masked ({N:,} итераций) ──")
    # for size, label in [(SMALL, "64B"), (MEDIUM, "1KB"), (LARGE, "64KB")]:
    #     data_list = [(0x2, os.urandom(size), True) for _ in range(1000)]
    #     compare(f"build_frame masked {label}",
    #             build_frame_old, build_frame_new, data_list, N)

    # # ── 2. _build_frame unmasked ──────────────────────────────────
    # print(f"\n── _build_frame unmasked ({N:,} итераций) ──")
    # for size, label in [(SMALL, "64B"), (MEDIUM, "1KB"), (LARGE, "64KB")]:
    #     data_list = [(0x2, os.urandom(size), False) for _ in range(1000)]
    #     compare(f"build_frame unmasked {label}",
    #             build_frame_old, build_frame_new, data_list, N)

    # # ── 3. mask key generation ────────────────────────────────────
    # print(f"\n── mask key: os.urandom(4) vs PRNG ({N:,} итераций) ──")
    # compare("mask_key", mask_key_old, mask_key_new, [()] * 100, N)

    # # ── 4. _socks5_reply ─────────────────────────────────────────
    N2 = 2_000_000
    # print(f"\n── _socks5_reply ({N2:,} итераций) ──")
    # compare("socks5_reply", socks5_reply_old, socks5_reply_new,
    #         [(s,) for s in (0x00, 0x05, 0x07, 0x08)], N2)

    # # ── 5. dc_from_init XOR (8 bytes) ────────────────────────────
    # print(f"\n── dc_xor 8B: generator vs int.from_bytes ({N2:,} итераций) ──")
    # compare("dc_xor_8B", dc_xor_old, dc_xor_new,
    #         [(os.urandom(8), os.urandom(8)) for _ in range(1000)], N2)

    # ── 6. _read_frame struct.unpack vs pre-compiled ─────────────
    print(f"\n── struct unpack read-path ({N2:,} итераций) ──")
    _st_H_pre = struct.Struct('>H')
    _st_Q_pre = struct.Struct('>Q')
    h_bufs = [(os.urandom(2),) for _ in range(1000)]
    q_bufs = [(os.urandom(8),) for _ in range(1000)]
    compare("unpack >H",
            lambda b: struct.unpack('>H', b),
            lambda b: _st_H_pre.unpack(b),
            h_bufs, N2)
    compare("unpack >Q",
            lambda b: struct.unpack('>Q', b),
            lambda b: _st_Q_pre.unpack(b),
            q_bufs, N2)

    # ── 7. dc_from_init: 2x unpack vs 1x merged ─────────────────
    print(f"\n── dc_from_init unpack: 2 calls vs 1 merged ({N2:,} итераций) ──")
    _st_Ih = struct.Struct('<Ih')
    plains = [(os.urandom(8),) for _ in range(1000)]
    def dc_unpack_old(p):
        return struct.unpack('<I', p[0:4])[0], struct.unpack('<h', p[4:6])[0]
    def dc_unpack_new(p):
        return _st_Ih.unpack(p[:6])
    compare("dc_unpack", dc_unpack_old, dc_unpack_new, plains, N2)

    # ── 8. bytes() copy vs direct slice ──────────────────────────
    print(f"\n── bytes(slice) vs direct slice ({N2:,} итераций) ──")
    raw_data = [(os.urandom(64),) for _ in range(1000)]
    def slice_copy(d):
        return bytes(d[8:40]), bytes(d[40:56])
    def slice_direct(d):
        return d[8:40], d[40:56]
    compare("bytes(slice) vs slice", slice_copy, slice_direct, raw_data, N2)

    # ── 9. MsgSplitter unpack_from: struct vs pre-compiled ───────
    print(f"\n── unpack_from <I: struct vs pre-compiled ({N2:,} итераций) ──")
    _st_I_le = struct.Struct('<I')
    splitter_bufs = [(os.urandom(64), 1) for _ in range(1000)]
    compare("unpack_from <I",
            lambda b, p: struct.unpack_from('<I', b, p),
            lambda b, p: _st_I_le.unpack_from(b, p),
            splitter_bufs, N2)

    print("\n" + "=" * 74)
    print("  Готово!")
    print("=" * 74)


if __name__ == "__main__":
    main()