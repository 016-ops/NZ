"""版本化恢复旧目录中的续费商品；价格、期限、币种及销售时间仍取冻结原规则。"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def restored_offers():
    """只允许已被物品续费字段引用的192项；不把全部隐藏商品一并上架。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-renewal-offer-restore-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='4cc8cb0ae2a05e2aded1dc5e4fc248451b895053b2edb85f99927a708946984c':
        raise ValueError('RENEWAL_OFFER_RULE_MISMATCH')
    return json.loads(raw)
