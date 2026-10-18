"""用标准库生成简洁的 N 字母应用图标；像素规则可复现，不依赖外部图像或第三方商标。"""
import struct
import zlib
from pathlib import Path


def build(root):
    """生成64像素RGBA PNG及内嵌PNG的Windows ICO，供Tauri资源编译和窗口任务栏使用。"""
    root.mkdir(parents=True, exist_ok=True)
    rows = bytearray()
    for y in range(64):
        rows.append(0)  # 每行使用 PNG 无过滤模式，保持构建结果确定。
        for x in range(64):
            letter = (16 <= x <= 23 or 40 <= x <= 47 or abs(x-(16+(y-16))) <= 4) and 16 <= y <= 47
            rows.extend((52,211,153,255) if letter else (2,6,23,255))

    def chunk(kind, data):
        """为PNG数据块写入网络字节序长度与CRC，损坏图标会在构建时被Tauri拒绝。"""
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I',zlib.crc32(kind+data))

    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,6,0,0,0))
    png += chunk(b'IDAT',zlib.compress(bytes(rows),9)) + chunk(b'IEND',b'')
    (root/'icon.png').write_bytes(png)
    header = struct.pack('<HHH',0,1,1) + struct.pack('<BBBBHHII',64,64,0,0,1,32,len(png),22)
    (root/'icon.ico').write_bytes(header+png)


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[1]/'launcher/icons')
