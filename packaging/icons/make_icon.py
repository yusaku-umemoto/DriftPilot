"""1024 の正方形の PNG（RGB）から、角を丸めた RGBA の PNG を作る。部品は標準ライブラリだけ。

使い方: python make_icon.py 入力.png 出力.png 内側の大きさ 角の割合
  例 Mac:     ... 824 0.225 （1024 の中に 824 の四角。Apple の格子）
  例 Windows: ... 984 0.18
入力は先に sips で「内側の大きさ」に縮めておく。
"""
import struct
import sys
import zlib


def read_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        typ, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and inter == 0 and ctype in (2, 6), (depth, ctype, inter)
            bpp = 3 if ctype == 2 else 4
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[i]; line = bytearray(raw[i + 1:i + 1 + stride]); i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line); prev = line
    return w, h, bpp, rows


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


def coverage(x, y, s, r):
    """角丸の四角（0..s）の中にある割合。4×4 の点で数える。"""
    inside = 0
    for i in range(4):
        for j in range(4):
            px, py = x + (i + 0.5) / 4, y + (j + 0.5) / 4
            cx = min(max(px, r), s - r); cy = min(max(py, r), s - r)
            if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                inside += 1
    return inside / 16


def main():
    src, out, inner, ratio = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    w, h, bpp, rows = read_png(src)
    assert w == h == inner, (w, h, inner)
    full, off, r = 1024, (1024 - inner) // 2, inner * ratio
    outrows = [bytearray(full * 4) for _ in range(full)]
    for y in range(inner):
        line = rows[y]
        near_y = y < r + 1 or y > inner - r - 2
        for x in range(inner):
            near = near_y and (x < r + 1 or x > inner - r - 2)
            a = coverage(x, y, inner, r) if near else 1.0
            if a <= 0:
                continue
            o = (x + off) * 4
            outrows[y + off][o:o + 3] = line[x * bpp:x * bpp + 3]
            outrows[y + off][o + 3] = round(255 * a)
    write_png(out, full, full, outrows)


main()
