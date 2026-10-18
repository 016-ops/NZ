"""发布离线依赖的边界测试：摘要、requirements绑定和不夹带缓存，不安装软件或访问真实服务器。"""
import hashlib,json,tempfile,unittest
from pathlib import Path
from tools.deploy_dev import wheelhouse_files


class DevWheelhouseTests(unittest.TestCase):
    """用隔离临时目录验证发布前拒绝条件，不把自造轮子当作实际Linux安装验收。"""

    def setUp(self):
        """准备三个固定测试依赖记录；真实官方轮子另有372远端离线安装证据。"""
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'config').mkdir();self.folder=self.root/'cache';self.folder.mkdir()
        self.rows=[]
        for name in ('alpha','beta','gamma'):
            raw=(name+' binary').encode();filename=name+'-1.0-py3-none-any.whl'
            (self.folder/filename).write_bytes(raw)
            self.rows.append(dict(package=name,version='1.0',name=filename,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        (self.root/'config/dev-linux-wheels-v1.json').write_text(json.dumps(dict(version='dev-linux-crypto-wheels-v1',files=self.rows)))
        (self.root/'requirements.txt').write_text('alpha==1.0\nbeta==1.0\ngamma==1.0\n')

    def test_only_manifest_files_are_selected(self):
        """上传目录即使混有凭据或其他包，也只返回明确列出的三个经过摘要核验的文件。"""
        (self.folder/'credentials.env').write_text('not-for-release')
        (self.folder/'other-9.0.whl').write_bytes(b'not-pinned')
        selected=wheelhouse_files(self.root,self.folder)
        self.assertEqual({p.name for p in selected},{r['name'] for r in self.rows})

    def test_same_length_corruption_is_rejected(self):
        """字节数相同仍必须核对完整SHA256，损坏包不得进入发布归档。"""
        target=self.folder/self.rows[0]['name'];target.write_bytes(b'x'*target.stat().st_size)
        with self.assertRaisesRegex(ValueError,'DEV_WHEEL_HASH'):wheelhouse_files(self.root,self.folder)

    def test_requirement_drift_is_rejected(self):
        """业务依赖改版却携带旧轮子时，上传前拒绝而不是在线降级或使用错误缓存。"""
        (self.root/'requirements.txt').write_text('alpha==2.0\nbeta==1.0\ngamma==1.0\n')
        with self.assertRaisesRegex(ValueError,'DEV_WHEEL_RULE'):wheelhouse_files(self.root,self.folder)

    def test_manifest_path_traversal_is_rejected(self):
        """发布白名单不能指向缓存之外；即使外部文件摘要正确也不允许打包。"""
        self.rows[0]['name']='../outside.whl'
        (self.root/'config/dev-linux-wheels-v1.json').write_text(json.dumps(dict(version='dev-linux-crypto-wheels-v1',files=self.rows)))
        with self.assertRaisesRegex(ValueError,'DEV_WHEEL_RULE'):wheelhouse_files(self.root,self.folder)


if __name__=='__main__':unittest.main()
