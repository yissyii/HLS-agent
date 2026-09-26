"""Small, closed typed-dataflow primitives used by the Bench4HLS contract path."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Type:
    width: int
    signed: bool

    def __post_init__(self):
        if type(self.width) is not int or not 1 <= self.width <= 1024:
            raise ValueError('Type width must be an integer from 1 through 1024')
        if type(self.signed) is not bool:
            raise ValueError('Type signed must be a bool')


_REF = re.compile(r'(?:[isv][0-9]+|true|false)\Z')
_B1 = Type(1, False)
_BINARY = frozenset({'add', 'sub', 'and', 'or', 'xor', 'eq', 'lt'})


def _integer(value, name):
    if type(value) is not int:
        raise ValueError(name + ' must be an integer (JSON bool is not an integer)')
    return value


def _ref(value, name):
    if type(value) is not str or not _REF.fullmatch(value):
        raise ValueError(name + ' must be a builtin, input, state, or graph reference')
    return value


def bounds(type_):
    if not isinstance(type_, Type):
        raise ValueError('Expected Type')
    if type_.signed:
        limit = 1 << (type_.width - 1)
        return -limit, limit - 1
    return 0, (1 << type_.width) - 1


def coerce(value, type_):
    """Wrap a mathematical integer into *type_*; preserve unknown values."""
    if value is None:
        return None
    _integer(value, 'value')
    if not isinstance(type_, Type):
        raise ValueError('Expected Type')
    bits = value & ((1 << type_.width) - 1)
    if type_.signed and bits & (1 << (type_.width - 1)):
        return bits - (1 << type_.width)
    return bits


def _fields(node):
    if type(node) is not dict:
        raise ValueError('node must be an object')
    if not {'id', 'op'} <= set(node):
        raise ValueError('node requires id and op')
    if type(node['id']) is not str or not re.fullmatch(r'v[0-9]+', node['id']):
        raise ValueError('node id must be a graph reference')
    if type(node['op']) is not str:
        raise ValueError('node op must be a string')
    required = {
        'const': {'width', 'signed', 'value'}, 'slice': {'arg', 'lsb', 'width'},
        'concat': {'args'}, 'not': {'arg'}, 'select': {'cond', 'yes', 'no'},
        'shl': {'arg', 'amount'}, 'lshr': {'arg', 'amount'}, 'ashr': {'arg', 'amount'},
        'cast': {'arg', 'width', 'signed'}, 'rotate': {'arg', 'direction', 'amount'},
        'reverse': {'arg'}, 'masked_write': {'old', 'data', 'mask', 'lane_width'},
        'table': {'arg', 'ones', 'zeros', 'dont_care'},
    }
    required.update({op: {'a', 'b'} for op in _BINARY})
    if node['op'] not in required or set(node) != {'id', 'op'} | required[node['op']]:
        raise ValueError('unknown operation or extra/missing node fields')
    return node['op']


def dependencies(node):
    """Return ordered direct references after validating the closed node shape."""
    op = _fields(node)
    names = {
        'const': (), 'slice': ('arg',), 'concat': ('args',), 'not': ('arg',),
        'select': ('cond', 'yes', 'no'), 'shl': ('arg',), 'lshr': ('arg',),
        'ashr': ('arg',), 'cast': ('arg',), 'rotate': ('arg',), 'reverse': ('arg',),
        'masked_write': ('old', 'data', 'mask'), 'table': ('arg',),
    }
    names.update({name: ('a', 'b') for name in _BINARY})
    result = []
    for name in names[op]:
        refs = node[name] if name == 'args' else (node[name],)
        if name == 'args' and type(refs) is not list:
            raise ValueError('concat args must be an array')
        for ref in refs:
            result.append(_ref(ref, name))
    return tuple(result)


def _type_for(ref, types):
    _ref(ref, 'reference')
    if ref == 'true' or ref == 'false':
        return _B1
    try:
        result = types[ref]
    except (KeyError, TypeError):
        raise ValueError('unknown reference: ' + ref) from None
    if not isinstance(result, Type):
        raise ValueError('reference type must be Type')
    return result


def _same(a, b, description):
    if a != b:
        raise ValueError(description + ' requires equal operand types')


def _rows(node, width):
    universe = set(range(1 << width))
    rows = []
    for name in ('ones', 'zeros', 'dont_care'):
        value = node[name]
        if type(value) is not list:
            raise ValueError('table ' + name + ' must be an array')
        row = set()
        for entry in value:
            entry = _integer(entry, 'table entry')
            if entry not in universe:
                raise ValueError('table entry outside argument range')
            if entry in row:
                raise ValueError('duplicate table entry')
            row.add(entry)
        rows.append(row)
    if set.union(*rows) != universe or any(rows[i] & rows[j] for i in range(3) for j in range(i)):
        raise ValueError('table rows must be an exact disjoint partition')
    return rows


def infer_type(node, types):
    op = _fields(node)
    deps = dependencies(node)
    if op == 'const':
        result = Type(node['width'], node['signed'])
        value = _integer(node['value'], 'const value')
        if not bounds(result)[0] <= value <= bounds(result)[1]:
            raise ValueError('const value is not representable')
        return result
    if op == 'concat':
        if not 2 <= len(deps) <= 16:
            raise ValueError('concat requires 2 through 16 arguments')
        parts = [_type_for(ref, types) for ref in deps]
        width = sum(part.width for part in parts)
        if width > 1024:
            raise ValueError('concat result width exceeds 1024')
        return Type(width, False)
    args = [_type_for(ref, types) for ref in deps]
    if op == 'slice':
        lsb = _integer(node['lsb'], 'slice lsb'); width = _integer(node['width'], 'slice width')
        if width < 1 or lsb < 0 or lsb + width > args[0].width:
            raise ValueError('invalid slice range')
        return Type(width, False)
    if op in {'add', 'sub', 'and', 'or', 'xor'}:
        _same(*args, op); return args[0]
    if op in {'eq', 'lt'}:
        _same(*args, op); return _B1
    if op in {'not', 'reverse'}:
        return args[0]
    if op == 'select':
        if args[0] != _B1:
            raise ValueError('select condition must be unsigned one-bit')
        _same(args[1], args[2], 'select'); return args[1]
    if op in {'shl', 'lshr', 'ashr'}:
        amount = _integer(node['amount'], 'shift amount')
        if not 0 <= amount <= args[0].width:
            raise ValueError('shift amount outside width')
        if (op == 'lshr' and args[0].signed) or (op == 'ashr' and not args[0].signed):
            raise ValueError('shift signedness does not match operation')
        return args[0]
    if op == 'cast':
        return Type(node['width'], node['signed'])
    if op == 'rotate':
        if node['direction'] not in {'left', 'right'}:
            raise ValueError('rotate direction must be left or right')
        amount = _integer(node['amount'], 'rotate amount')
        if not 0 <= amount <= args[0].width:
            raise ValueError('rotate amount outside width')
        return args[0]
    if op == 'masked_write':
        old, data, mask = args; _same(old, data, 'masked_write')
        lane = _integer(node['lane_width'], 'lane_width')
        if not 1 <= lane <= old.width or old.width % lane:
            raise ValueError('lane_width must divide old width')
        if mask != Type(old.width // lane, False):
            raise ValueError('masked_write mask has wrong type')
        return old
    if op == 'table':
        if args[0].signed or args[0].width > 8:
            raise ValueError('table argument must be unsigned and at most 8 bits')
        _rows(node, args[0].width); return _B1
    raise AssertionError('unreachable')


def _value(ref, values):
    if ref == 'true': return 1
    if ref == 'false': return 0
    try: value = values[ref]
    except (KeyError, TypeError): raise ValueError('missing value: ' + ref) from None
    if value is not None: _integer(value, 'value')
    return value


def _bits(value, type_):
    return value & ((1 << type_.width) - 1)


def evaluate(node, values, types):
    """Evaluate one inferred node without executing supplied expressions or code."""
    result = infer_type(node, types)
    op = node['op']; refs = dependencies(node); vals = [_value(ref, values) for ref in refs]
    if op == 'const': return node['value']
    if op == 'select':
        cond, yes, no = vals
        if cond is None: return yes if yes is not None and yes == no else None
        return yes if cond else no
    if op == 'masked_write':
        old, data, mask = vals; lane = node['lane_width']
        if mask == 0: return old
        full = (1 << (result.width // lane)) - 1
        if mask is None or data is None or (old is None and mask != full): return None
        old_bits = 0 if old is None else _bits(old, result)
        data_bits = _bits(data, result); out = old_bits
        for index in range(result.width // lane):
            if mask & (1 << index):
                field = ((1 << lane) - 1) << (index * lane)
                out = (out & ~field) | (data_bits & field)
        return coerce(out, result)
    if any(value is None for value in vals): return None
    if op == 'slice': return coerce(_bits(vals[0], _type_for(refs[0], types)) >> node['lsb'], result)
    if op == 'concat':
        out = 0
        for ref, value in zip(refs, vals): out = (out << _type_for(ref, types).width) | _bits(value, _type_for(ref, types))
        return coerce(out, result)
    if op == 'add': return coerce(vals[0] + vals[1], result)
    if op == 'sub': return coerce(vals[0] - vals[1], result)
    if op == 'and': return coerce(_bits(vals[0], result) & _bits(vals[1], result), result)
    if op == 'or': return coerce(_bits(vals[0], result) | _bits(vals[1], result), result)
    if op == 'xor': return coerce(_bits(vals[0], result) ^ _bits(vals[1], result), result)
    if op == 'not': return coerce(~_bits(vals[0], result), result)
    if op == 'eq': return int(vals[0] == vals[1])
    if op == 'lt': return int(vals[0] < vals[1])
    if op == 'shl': return coerce(_bits(vals[0], result) << node['amount'], result)
    if op == 'lshr': return coerce(_bits(vals[0], result) >> node['amount'], result)
    if op == 'ashr': return coerce(vals[0] >> node['amount'], result)
    if op == 'cast': return coerce(vals[0], result)
    if op == 'rotate':
        amount = node['amount'] % result.width; bits = _bits(vals[0], result)
        if node['direction'] == 'left': out = (bits << amount) | (bits >> (result.width - amount))
        else: out = (bits >> amount) | (bits << (result.width - amount))
        return coerce(out, result)
    if op == 'reverse':
        bits = _bits(vals[0], result); out = 0
        for index in range(result.width): out |= ((bits >> index) & 1) << (result.width - 1 - index)
        return coerce(out, result)
    if op == 'table':
        ones, zeros, dc = _rows(node, _type_for(refs[0], types).width)
        return 1 if vals[0] in ones else 0 if vals[0] in zeros else None
    raise AssertionError('unreachable')
