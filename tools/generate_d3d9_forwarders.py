"""从本机Windows SDK的公开COM声明生成逐方法转发类，避免假定对象内存或修改系统虚表。"""
from pathlib import Path
import re


def generate(header, output):
    """严格核对三个公开接口的方法数；特殊生命周期和呈现方法由手写实现，其余原样转交。"""
    definitions = [
        ('IDirect3D9', 'FactoryProxy', 17, {'CreateDevice'},
         'explicit FactoryProxy(IDirect3D9* value) : real(value) {}\n'
         '    ~FactoryProxy() { real->Release(); }'),
        ('IDirect3DDevice9', 'DeviceProxy', 119,
         {'Present', 'Reset', 'GetDirect3D', 'GetSwapChain', 'CreateAdditionalSwapChain'},
         'FactoryProxy* factory; DeviceState display;\n'
         '    DeviceProxy(IDirect3DDevice9* value, FactoryProxy* parent) : real(value), factory(parent) { factory->AddRef(); }\n'
         '    ~DeviceProxy() { display.ClearDisplay(); real->Release(); factory->Release(); }'),
        ('IDirect3DSwapChain9', 'SwapProxy', 10, {'Present', 'GetDevice'},
         'DeviceProxy* owner; DeviceState display;\n'
         '    SwapProxy(IDirect3DSwapChain9* value, DeviceProxy* parent) : real(value), owner(parent) { owner->AddRef(); }\n'
         '    ~SwapProxy() { display.ClearDisplay(); real->Release(); owner->Release(); }'),
    ]
    text = Path(header).read_text()
    result = ['// 本文件由generate_d3d9_forwarders.py生成；方法签名来自Windows SDK公开接口。',
              '// 普通方法不改变参数/返回值。资源接口保持系统对象；本实验的覆盖边界另记于交接文档。']
    for interface, proxy, expected, custom, constructor in definitions:
        body = text.split(f'DECLARE_INTERFACE_({interface}, IUnknown)')[1].split('};')[0]
        methods = re.findall(r'STDMETHOD(?:_\(([^,]+),\s*(\w+)\)|\((\w+)\))\(THIS_?\s*(.*?)\)\s*PURE;', body, re.S)
        if len(methods) != expected:
            raise ValueError(f'Unexpected SDK method count: {interface}')
        result += [f'// 显式{interface}代理；自身引用计数与所持系统对象引用分离，释放时只归还拥有的引用。',
                   f'class {proxy} final : public {interface} {{', 'public:',
                   f'    {interface}* real; std::atomic<ULONG> references{{1}};',
                   '    // 构造接管一个系统引用；析构归还本层拥有的对象和父接口。', '    ' + constructor]
        for returned, alternate, name, parameters in methods:
            name = name or alternate
            returned = returned.strip() or 'HRESULT'
            parameters = ' '.join(parameters.split())
            arguments, named_parameters = [], []
            for number, parameter in enumerate(parameters.split(',') if parameters else []):
                # SDK允许声明省略指针参数名；生成可调用转发体时补本地名字，不改类型。
                if parameter.rstrip().endswith('*') or re.fullmatch(r'\s*\w+\s*', parameter):
                    parameter += f' argument{number}'
                match = re.search(r'(\w+)\s*(?:\[[^\]]*\])?$', parameter)
                if not match:
                    raise ValueError(f'Unrecognized parameter: {parameter}')
                arguments.append(match.group(1))
                named_parameters.append(parameter)
            parameters = ','.join(named_parameters)
            signature = f'    {returned} WINAPI {name}({parameters}) override'
            if name == 'QueryInterface':
                result += ['    // 保持本层IUnknown身份；其他扩展接口沿用系统查询结果，不宣称已覆盖其呈现路径。',
                           signature + ' { if (!ppvObj) return E_POINTER; '
                           f'if (riid == __uuidof(IUnknown) || riid == __uuidof({interface})) '
                           '{ *ppvObj = this; AddRef(); return S_OK; } return real->QueryInterface(riid, ppvObj); }']
            elif name == 'AddRef':
                result += ['    // 线程安全地增加本代理引用，不干预系统对象内部资源引用。', signature + ' { return ++references; }']
            elif name == 'Release':
                result += ['    // 最后一个本层引用释放时销毁代理，返回剩余引用数。',
                           signature + ' { ULONG left = --references; if (!left) delete this; return left; }']
            elif name in custom:
                result += ['    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。', signature + ';']
            else:
                result += ['    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。',
                           signature + ' { return real->' + name + '(' + ', '.join(arguments) + '); }']
        result.append('};')
    Path(output).write_text('\n'.join(result) + '\n', encoding='utf-8')


if __name__ == '__main__':
    import sys
    generate(sys.argv[1], sys.argv[2])
