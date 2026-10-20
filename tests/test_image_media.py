"""图片解码边界纯内存测试；不使用数据库、用户上传文件或项目秘密。"""
from io import BytesIO
from unittest.mock import patch
import unittest

from revival import media
from revival.service import Fault


class ImageMediaTests(unittest.TestCase):
    """检查真实格式、完整解码、元数据清理、像素/字节上限及动画拒绝。"""
    def image(self, fmt='PNG', **kwargs):
        """在内存生成格式明确的小图，附加参数仅供单例元数据/动画验证。"""
        from PIL import Image
        output = BytesIO()
        Image.new('RGB', (24, 16), 'red').save(output, format=fmt, **kwargs)
        return output.getvalue()

    def test_three_allowed_formats_are_reencoded_without_metadata(self):
        """允许格式统一输出WebP，不保留输入的EXIF、文本注释或原始编码文件。"""
        from PIL import Image, PngImagePlugin
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('private-note', 'never persist source metadata')
        exif = Image.Exif()
        exif[270] = 'private original description'
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            options = {'pnginfo': metadata} if fmt == 'PNG' else {'exif': exif} if fmt == 'JPEG' else {}
            raw = self.image(fmt, **options)
            result = media.normalize_image(raw)
            with Image.open(BytesIO(result['payload'])) as image:
                self.assertEqual((image.format, image.size), ('WEBP', (24, 16)))
                self.assertNotIn('private-note', image.info)
                self.assertNotIn('exif', image.info)
            self.assertEqual(result['mime'], 'image/webp')

    def test_fake_corrupt_animated_and_oversized_sources_are_rejected(self):
        """SVG/伪后缀、截断PNG、GIF和超限输入都不能通过图片扩展名绕过真解码。"""
        for raw in (b'<svg><script>alert(1)</script></svg>', self.image()[:30], self.image('GIF')):
            with self.subTest(length=len(raw)), self.assertRaises(Fault):
                media.normalize_image(raw)
        with self.assertRaises(Fault) as error:
            media.normalize_image(b'x' * (media.MAX_UPLOAD_BYTES + 1))
        self.assertEqual(error.exception.code, 'IMAGE_SIZE')
        from PIL import Image
        animated = BytesIO()
        Image.new('RGB', (24, 16), 'red').save(animated, format='WEBP', save_all=True,
            append_images=[Image.new('RGB', (24, 16), 'blue')], duration=100, loop=0)
        with self.assertRaises(Fault):
            media.normalize_image(animated.getvalue())

    def test_pixel_limit_precedes_full_decode(self):
        """声明像素超限时在完整解码前拒绝，避免小压缩文件占用巨大解码内存。"""
        raw = self.image()
        with patch.object(media, 'MAX_PIXELS', 100), self.assertRaises(Fault) as error:
            media.normalize_image(raw)
        self.assertEqual(error.exception.code, 'IMAGE_DIMENSIONS')
