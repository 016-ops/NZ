"""用真实AES/zlib夹具验证UI诊断只修改目标字段，保留其他原始语义并拒绝未知/重复配置。"""
import hashlib
import struct
import zlib
from unittest import TestCase
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from tools.client_catalog import MAGIC, RESOURCE_KEY, decode_resource
from tools.native_ui_config import disable_current_screen_texture


def encrypted(clear):
    """产生本测试独有资源替身；不读取或写入真实客户端。"""
    packed=struct.pack('<I',len(clear))+zlib.compress(clear)
    packed+=bytes((-len(packed))%16)
    encoder=Cipher(algorithms.AES(RESOURCE_KEY),modes.ECB()).encryptor()
    return MAGIC+encoder.update(packed)+encoder.finalize()


class NativeUiConfigTests(TestCase):
    """固定指纹与目标节双重限制，避免诊断选项触及其他类或静默接受不同构建。"""
    def test_only_scene_texture_field_changes_and_chinese_bom_survive(self):
        """中文/BOM/CRLF和其他节同名属性原样保留，仅场景客户端的一处true改false。"""
        clear='\ufeff[Other]\r\nbUseScreenTexture=true\r\n; 中文注释\r\n[UTGame.TGUISceneClient]\r\nbUseScreenTexture=true\r\nOtherFlag=1\r\n'.encode('utf-8')
        before=encrypted(clear)
        with patch('tools.native_ui_config.GAME_CONFIG_SHA256',hashlib.sha256(before).hexdigest()):
            after=disable_current_screen_texture(before)
        expected=clear.replace(b'[UTGame.TGUISceneClient]\r\nbUseScreenTexture=true',b'[UTGame.TGUISceneClient]\r\nbUseScreenTexture=false')
        self.assertEqual(decode_resource(after)[0],expected.decode('utf-8-sig'))
        self.assertEqual(decode_resource(after)[1]['decoded_bytes'],len(expected))

    def test_unknown_fingerprint_missing_and_duplicate_fields_fail_closed(self):
        """指纹错误先拒绝；固定版本校验通过后仍检查目标字段完整且唯一。"""
        with self.assertRaisesRegex(ValueError,'CURRENT_UI_CONFIG_FINGERPRINT_MISMATCH'):
            disable_current_screen_texture(b'unknown')
        for clear in (b'[UTGame.TGUISceneClient]\nOther=true\n',
                      b'[UTGame.TGUISceneClient]\nbUseScreenTexture=true\nbUseScreenTexture=true\n'):
            before=encrypted(clear)
            with patch('tools.native_ui_config.GAME_CONFIG_SHA256',hashlib.sha256(before).hexdigest()), self.assertRaises(ValueError):
                disable_current_screen_texture(before)


class NativeShopConfigTests(TestCase):
    """续费兼容只恢复白名单字段；不放宽未知版本、缺字段、重复行或未关联商品。"""
    def test_renewal_flags_only_and_non_target_bytes_unchanged(self):
        """固定商品两开关改变，价格、中文/BOM、日期和未选中的商品逐字节保持。"""
        from tools.native_shop_config import restore_renewal_offers
        clear=('\ufeff[UTGame.TGFPSCommodityLibrary]\r\n'
            'RawCommodityDatas=(nCommodityID=200020, sItemName="中文SCAR", bShowCommodity=false, bGoodsAdded=false, nPriceArray=(500), ItemRecallDate=21120101)\r\n'
            'RawCommodityDatas=(nCommodityID=999999, bShowCommodity=false, bGoodsAdded=false, Other=1)\r\n').encode()
        original=encrypted(clear)
        rules=dict(source_sha256=hashlib.sha256(original).hexdigest(),offer_ids=[200020])
        with patch('tools.native_shop_config.restored_offers',return_value=rules):
            result=restore_renewal_offers(original)
        expected=clear.replace(b'bShowCommodity=false, bGoodsAdded=false',b'bShowCommodity=true, bGoodsAdded=true',1)
        self.assertEqual(decode_resource(result)[1]['decoded_sha256'],hashlib.sha256(expected).hexdigest())

    def test_reject_unknown_missing_or_duplicate_config(self):
        """入口安装必须完整匹配白名单，错误时不返回部分配置。"""
        from tools.native_shop_config import restore_renewal_offers
        with self.assertRaisesRegex(ValueError,'FINGERPRINT'):
            restore_renewal_offers(b'unknown')
        row=b'RawCommodityDatas=(nCommodityID=200020, bShowCommodity=false, bGoodsAdded=false, Other=1)\n'
        for body in (b'',row+row,row.replace(b'bGoodsAdded',b'OtherAdded')):
            original=encrypted(b'[UTGame.TGFPSCommodityLibrary]\n'+body)
            rules=dict(source_sha256=hashlib.sha256(original).hexdigest(),offer_ids=[200020])
            with patch('tools.native_shop_config.restored_offers',return_value=rules),self.assertRaises(ValueError):
                restore_renewal_offers(original)
