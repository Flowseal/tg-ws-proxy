"""Dependency-free AES-CTR for the explicitly selected Android backend."""


def _mul(a, b):
    out = 0
    for _ in range(8):
        if b & 1:
            out ^= a
        a = ((a << 1) ^ (0x11b if a & 0x80 else 0)) & 0xff
        b >>= 1
    return out


def _sbox(value):
    inverse = 0 if value == 0 else 1
    if value:
        # Multiplicative inverse in GF(2^8), computed once per key byte/block.
        inverse = 1
        for _ in range(254):
            inverse = _mul(inverse, value)
    result = inverse
    for shift in (1, 2, 3, 4):
        result ^= ((inverse << shift) | (inverse >> (8 - shift))) & 0xff
    return result ^ 0x63


_SBOX = tuple(_sbox(i) for i in range(256))


def _round_keys(key):
    if len(key) not in (16, 24, 32):
        raise ValueError('AES key must be 16/24/32 bytes')
    nk = len(key) // 4
    rounds = nk + 6
    words = [list(key[i:i + 4]) for i in range(0, len(key), 4)]
    rcon = 1
    while len(words) < 4 * (rounds + 1):
        i = len(words)
        temp = words[-1][:]
        if i % nk == 0:
            temp = [_SBOX[x] for x in temp[1:] + temp[:1]]
            temp[0] ^= rcon
            rcon = _mul(rcon, 2)
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[x] for x in temp]
        words.append([a ^ b for a, b in zip(words[i - nk], temp)])
    return [sum(words[i:i + 4], []) for i in range(0, len(words), 4)]


def _block(block, keys):
    state = list(block)
    for round_index, key in enumerate(keys):
        if round_index:
            state = [_SBOX[x] for x in state]
            for row in range(1, 4):
                values = [state[row + 4 * col] for col in range(4)]
                values = values[row:] + values[:row]
                for col, value in enumerate(values):
                    state[row + 4 * col] = value
            if round_index < len(keys) - 1:
                for col in range(4):
                    offset = 4 * col
                    a, b, c, d = state[offset:offset + 4]
                    state[offset:offset + 4] = [
                        _mul(a, 2) ^ _mul(b, 3) ^ c ^ d,
                        a ^ _mul(b, 2) ^ _mul(c, 3) ^ d,
                        a ^ b ^ _mul(c, 2) ^ _mul(d, 3),
                        _mul(a, 3) ^ b ^ c ^ _mul(d, 2),
                    ]
        state = [a ^ b for a, b in zip(state, key)]
    return bytes(state)


class AesCtrStream:
    def __init__(self, key, iv):
        if len(iv) != 16:
            raise ValueError('CTR IV must be 16 bytes')
        self._keys = _round_keys(bytes(key))
        self._counter = int.from_bytes(iv, 'big')
        self._keystream = b''

    def update(self, data):
        result = bytearray()
        for value in data:
            if not self._keystream:
                self._keystream = _block(self._counter.to_bytes(16, 'big'), self._keys)
                self._counter = (self._counter + 1) % (1 << 128)
            result.append(value ^ self._keystream[0])
            self._keystream = self._keystream[1:]
        return bytes(result)

    def finalize(self):
        return b''
