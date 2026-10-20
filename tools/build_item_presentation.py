"""从冻结目录生成独立的界面展示索引与缺图清单；从不修改游戏目录、编号或规则摘要。"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXACT = {
    '100216': ('wanfei.jpg', 'https://nz.17173.com/info/role_all.shtml'),
    '100217': ('wanyang.jpg', 'https://nz.17173.com/info/role_all.shtml'),
    '100688': ('amy-classic.jpg', 'https://nz.17173.com/news/09182013/094133489.shtml'),
    '100689': ('barbie-classic.jpg', 'https://nz.17173.com/news/09182013/094133489.shtml'),
}


def main():
    """名称来自现有4323项目录；明确区分图鉴配图与AI分类示意，输出可复核覆盖数量。"""
    catalog = json.loads((ROOT / 'config/cn2020-feature-catalog-v1.json').read_text())
    items = {}
    missing = []
    for key, item in catalog['items'].items():
        exact = EXACT.get(key)
        # 槽位来自已冻结目录；主武器只配主武器分类示意，不能当成某把枪的准确模型。
        category = 'primary-category-v3.webp' if item.get('location') == 0 else 'equipment-category-v3.webp'
        items[key] = dict(name=item['name'], image_url='/assets/items/' + (exact[0] if exact else category),
                          image_status='exact' if exact else 'illustration', source=exact[1] if exact else 'ImageGen: ' + category)
        if not exact:
            missing.append([key, item['name'], '分类示意', '原道具准确图片待补充；不得把AI示意标为原图'])
    result = dict(version='nz-presentation-v2', catalog='cn2020-feature-catalog-v1', exact_count=len(EXACT),
                  illustration_count=len(missing), items=items)
    (ROOT / 'admin/src/lib/item-presentation.json').write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    target = ROOT / 'docs/assets/item-image-missing.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['道具编号', '中文名称', '当前图片类型', '待补内容'])
        writer.writerows(missing)
    print(json.dumps({'items': len(items), 'exact': len(EXACT), 'illustration': len(missing)}))


if __name__ == '__main__':
    main()
