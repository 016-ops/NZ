import tempfile
import unittest
from pathlib import Path
from tools.client_inventory import compare,inventory,pe_architecture
from tools.inspect_metalib import inspect


class InventoryTests(unittest.TestCase):
    def test_repeated_manifest_and_precise_change_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'client';root.mkdir()
            (root/'version.ini').write_bytes(b'version=sample')
            (root/'map.udk').write_bytes(b'not-a-real-map')
            (root/'fixture.exe').write_bytes(b'not-executable')
            first=base/'first.csv';second=base/'second.csv'
            result=inventory(root,first);inventory(root,second)
            self.assertEqual(result['files'],3);self.assertEqual(compare(first,second),{'added':[],'removed':[],'changed':[]})
            (root/'version.ini').write_bytes(b'changed');(root/'fixture.exe').unlink();(root/'extra.bin').write_bytes(b'new')
            inventory(root,second)
            self.assertEqual(compare(first,second),{'added':['extra.bin'],'removed':['fixture.exe'],'changed':['version.ini']})
            self.assertEqual(pe_architecture(root/'map.udk'),'NOT_PE')

    def test_unsafe_output_empty_and_duplicate_manifests_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'client';root.mkdir()
            with self.assertRaises(ValueError):inventory(root,root/'manifest.csv')
            with self.assertRaises(ValueError):inventory(root,base/'manifest.csv')
            self.assertFalse((base/'manifest.csv').exists())
            bad=base/'bad.csv';bad.write_text('path,bytes,sha256\na,1,x\na,1,x\n')
            with self.assertRaises(ValueError):compare(bad,bad)

    def test_symbol_index_never_claims_wire_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'fixture.tdr'
            path.write_bytes(b'\x00C2ZNReqExample\x00ordinary-private-data\x00')
            result=inspect(path)
            self.assertEqual(result['candidate_symbols'][0]['name'],'C2ZNReqExample')
            self.assertEqual(result['wire_schema'],'UNKNOWN')
            self.assertNotIn('ordinary-private-data',str(result))
