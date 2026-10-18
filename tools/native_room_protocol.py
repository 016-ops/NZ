"""从当前固定TDR向前扩展原生频道/房间协议；保留v11全部结构，静态生成不代表实机通过。"""
import argparse
import hashlib
import json
from pathlib import Path
from . import native_feature_protocol

PREVIOUS_SHA256='5a48a0f03fec4f134288f1f1cb7763ac3e367fa5f8b64c7e93d2b493b9fdde41'
# A11B包含统计联合体，需在实际胜负格式核实后单独接入；不把未知联合体编码成空成功。
COMMANDS=(set(range(0xa100,0xa123))-{0xa101,0xa11b})|{0xa12e,0xa12f,0xa13d,0xa13e,
    0xa14b,0xa14c,0xa210,0xa211,0xa212,0xa330,0xa601,0xa602,0xa603,0xa604}


def extract(source,previous):
    """固定旧目录摘要，递归拷贝准确选择的依赖；旧字段或命令发生冲突直接拒绝。"""
    raw=Path(previous).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PREVIOUS_SHA256:raise ValueError('ROOM_PREVIOUS_SCHEMA_CHANGED')
    result=json.loads(raw)
    # 解析器只读固定摘要TDR；临时扩大选择，不改变其模块默认功能目录。
    old_tokens=native_feature_protocol.TOKENS
    try:
        native_feature_protocol.TOKENS=('',)
        current=native_feature_protocol.extract(Path(source))
    finally:
        native_feature_protocol.TOKENS=old_tokens
    pending=[]
    for command in sorted(COMMANDS):
        key=str(command)
        if key not in current['commands']:raise ValueError('ROOM_COMMAND_MISSING')
        name=current['commands'][key]
        if key in result['commands'] and result['commands'][key]!=name:raise ValueError('ROOM_COMMAND_CONFLICT')
        result['commands'][key]=name;pending.append(name)
    seen=set()
    while pending:
        name=pending.pop()
        if name in seen:continue
        seen.add(name);schema=current['schemas'][name]
        if name in result['schemas'] and result['schemas'][name]!=schema:raise ValueError('ROOM_SCHEMA_CONFLICT')
        if any(f['type'] not in (1,2,3,5,6,7,8,11,12,15,19,21) for f in schema['fields']):
            raise ValueError('ROOM_UNVERIFIED_TYPE')
        result['schemas'][name]=schema
        pending.extend(f['nested'] for f in schema['fields'] if f['nested'])
    return result


def main():
    """只生成新文件和摘要，不覆盖任何已发布协议目录或修改客户端。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--previous',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    value=extract(args.source,args.previous)
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    with args.output.open('xb') as output:output.write(raw)
    print(json.dumps(dict(commands=len(value['commands']),schemas=len(value['schemas']),sha256=hashlib.sha256(raw).hexdigest(),native_live_verified=False)))


if __name__=='__main__':main()
