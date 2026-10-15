import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tools.client_catalog import Catalogue, MAGIC, RESOURCE_KEY, decode_resource, fields, parse_ini, parse_value, scan, check_unchanged
from tools.client_catalog_export import add_external, export_catalogue, verify_output, load_capture


class ClientCatalogTests(unittest.TestCase):
    def encrypted(self, raw, size=None):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        payload = struct.pack('<I', len(raw) if size is None else size) + zlib.compress(raw)
        payload += b'\0' * (-len(payload) % 16)
        encoder = Cipher(algorithms.AES(RESOURCE_KEY), modes.ECB()).encryptor()
        return MAGIC + encoder.update(payload) + encoder.finalize()

    def test_decoder_preserves_chinese_and_rejects_corruption(self):
        for encoding in ['utf-8-sig', 'utf-16', 'gb18030']:
            text = '[物品]\nName=雷明顿\\路径\n'
            decoded, meta = decode_resource(self.encrypted(text.encode(encoding)))
            self.assertEqual(decoded, text)
            self.assertTrue(meta['encrypted'])
        for blob in [b'\0' * 64, MAGIC + b'abc', self.encrypted(b'Name=x', 999), self.encrypted(b'Name=x', 100000000)]:
            with self.assertRaises(ValueError): decode_resource(blob)
        decoded,meta=decode_resource(b'123 ..\\TGame\\map.udk\r\n'+b'\0'*100)
        self.assertEqual(decoded,'123 ..\\TGame\\map.udk\r\n')
        self.assertEqual(meta['terminal_nul_bytes'],100)
        decoded,meta=decode_resource(self.encrypted('[Subtitles]\nIntro=It\u2019s a test\n'.encode('cp1252')))
        self.assertIn('\u2019',decoded);self.assertTrue(meta['encoding_fallback'])

    def test_nested_arrays_repeated_fields_and_strings(self):
        value = parse_value('(Name="武器, (限时)",IDs=(1,2),Curve=(Points=((X=1,Y=2),(X=3,Y=4))),Name="重复",Path="A\\B")')
        self.assertEqual([x['key'] for x in value['fields']], ['Name', 'IDs', 'Curve', 'Name', 'Path'])
        self.assertEqual(fields(value)['Path'], 'A\\B')
        self.assertEqual(fields(fields(value)['Curve'])['Points'][1]['fields'][0]['value'], 3)

    def test_inline_comments_keep_values_and_semicolons_in_strings(self):
        rows=parse_ini('[A]\nX=(A=1,B=2) ; comment\nName="a;b"\nCount=12 ; 中文\n','test.ini')
        self.assertEqual(fields(rows[1]['value']),{'A':1,'B':2})
        self.assertEqual(rows[1]['inline_comment'],'; comment')
        self.assertEqual(rows[2]['value'],'a;b')
        self.assertEqual(rows[3]['value'],12)

    def test_map_reference_is_not_a_new_map_definition(self):
        records=parse_ini('[A]\nGameMapSettings=(MapId=7,NickName="地图",MapName="M_Main")\nOther=(MapID=7,PropId=99)\n','TGame/Config/DefaultGame.ini')
        c=Catalogue([],records);c.build()
        self.assertEqual(len(c.maps),1)
        self.assertEqual(c.entities['map:7']['name'],'地图')

    def test_optional_empty_suite_array_is_not_price_period_error(self):
        records=parse_ini('[Shop]\nRawCommodityDatas=(nCommodityID=1,sItemName="X",nPriceArray=(10),nAvailPreiodArray=(72),PeriodArrayForSuite=())\n','TGame/Config/DefaultCommodityLibrary.ini')
        c=Catalogue([],records);c.build()
        self.assertFalse(any(i['code'].startswith('SHOP_') for i in c.issues))
        self.assertEqual(c.options[0]['price_raw'],10)

    def test_ini_keeps_operators_comments_multiline_and_unknown_lines(self):
        text = ';ignored=1\n[A]\n+Values=(1,\n2)\n.Values=3\n-Values=4\n!Values=ClearArray\n[A]\nBasedOn=..\\Base.ini;\nunknown line\n'
        rows = parse_ini(text, 'TGame/Config/fixture.ini')
        assigns = [r for r in rows if r['kind'] == 'assignment']
        self.assertEqual([r['operator'] for r in assigns], ['+', '.', '-', '!', ''])
        self.assertEqual(assigns[0]['value'], [1, 2]); self.assertEqual(assigns[0]['line_end'], 4)
        self.assertEqual(assigns[4]['value'], '..\\Base.ini')
        self.assertEqual(assigns[4]['section_occurrence'], 2)
        self.assertTrue(any(r['kind'] == 'unparsed_line' for r in rows))

    def fixture(self, root):
        config = root/'TGame/Config'; config.mkdir(parents=True)
        (config/'DefaultItemLibrary.ini').write_bytes(self.encrypted('[Items]\nItemDatas=(nItemID=7,ItemName="M4A1",ItemChineseName="测试武器",Description="路径\\x 引号\\\"",Location=0,bHidden=false)\n'.encode('gb18030')))
        (config/'DefaultCommodityLibrary.ini').write_bytes(self.encrypted('[Shop]\nRawCommodityDatas=(nCommodityID=8,sItemName="礼包",sCommodityType="GP",nPriceArray=(100,200),nAvailPreiodArray=(72),PeriodArrayForSuite=(-2,-2),nItemIDArray=(7,999),bIsPackage=true)\n'.encode('gb18030')))
        (config/'DefaultWeapon.ini').write_text('[UTGame.TGWeap_M4A1]\nDamage=1.5f\n', encoding='utf-8')
        (config/'DefaultGame.ini').write_text('[Engine.GameInfo]\n+DefaultMapPrefixes=(Prefix="SV",GameType="PVEGame.TGSVGame")\n\n\n', encoding='utf-8')
        (config/'broken.ini').write_bytes(b'\0'*32)

    def test_export_fidelity_and_unresolved_references(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'client';root.mkdir();self.fixture(root)
            files,records,stats=scan(root,False);c=Catalogue(files,records);c.build()
            self.assertEqual(c.items[0]['classification'], 'WEAPON_CLASS_MATCH')
            self.assertTrue(any(r['target_id'] is None and r['raw_target']=='999' for r in c.relations.values()))
            self.assertTrue(any(i['code']=='SHOP_OPTION_ARRAY_LENGTH' for i in c.issues))
            add_external(c, {'sources':[{'id':'H','url':'https://example.test/history','title':'历史','published_date':None,'accessed_date':'2026-10-14'}],
                             'annotations':[{'id':'S','source_id':'H','categories':['mode'],'client_match':'TGSVGame$','summary':'历史生存玩法','display_name':'生存'}]})
            self.assertEqual(len(c.external_annotations),1)
            check_unchanged(root,files,stats)
            output=Path(temp)/'delivery';output.mkdir()
            export_catalogue(c,'a'*64,output)
            self.assertTrue(verify_output(output)['passed'])
            captured_files,captured_records,metadata=load_capture(output)
            self.assertEqual(len(captured_files),len(files))
            self.assertEqual([r['value'] for r in captured_records],[r['value'] for r in records])
            self.assertEqual(metadata['source_snapshot_id'],'a'*64)
            schema=(output/'sql/001_schema.sql').read_text()
            self.assertIn('ENGINE=InnoDB',schema)
            self.assertNotIn('DROP TABLE',schema)
            seed=(output/'sql/002_seed.sql').read_text(encoding='utf-8')
            self.assertIn('ON DUPLICATE KEY UPDATE',seed)
            self.assertIn('CONVERT(X\'',seed)
            path=output/'json/items.json'; rows=json.loads(path.read_text(encoding='utf-8'));rows[0]['hidden']=True
            path.write_text(json.dumps(rows),encoding='utf-8')
            self.assertFalse(verify_output(output)['passed'])

    def test_rebuild_refuses_tampered_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'client';root.mkdir();self.fixture(root)
            files,records,_=scan(root,False);c=Catalogue(files,records);c.build()
            output=Path(temp)/'delivery';output.mkdir();export_catalogue(c,'b'*64,output)
            (output/'json/raw_records.json').write_text('[]',encoding='utf-8')
            with self.assertRaises(ValueError):load_capture(output)

    def test_added_and_modified_client_files_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'client';root.mkdir();self.fixture(root)
            files,records,stats=scan(root,False)
            (root/'extra').write_text('added')
            with self.assertRaises(ValueError):check_unchanged(root,files,stats)
            (root/'extra').unlink(); (root/'TGame/Config/broken.ini').write_bytes(b'\1'*32)
            with self.assertRaises(ValueError):check_unchanged(root,files,stats)


if __name__ == '__main__': unittest.main()
