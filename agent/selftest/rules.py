"""Small agent-authored rule registry. Mathematical checks, NOT human-certified specs."""
from .spec import integer, require, type_info


RULES = {
    'gray_decode': 'Reflected Gray to unsigned binary: high-to-low cumulative XOR of input bits.',
    'reverse_bits': 'Reverse exactly width bits, preserving leading zero positions.',
    'popcount': 'Count one bits in an unsigned width-bit word.',
}
RULE_VERSION = 1


def check_applicability(rule_id, width, contract):
    require(rule_id in RULES, 'Unknown rule')
    integer(width, 1, 16, 'rule width (pilot supports 1..16)')
    require(len(contract['inputs']) == 1, 'Rule requires one scalar input')
    param = contract['inputs'][0]
    require(param['type'] != 'bool' and type_info(param['type'])[0] == 0, 'Rule requires unsigned input')
    require(param['domain'] == [0, (1 << width)-1], 'Rule requires the full declared width-bit domain')
    low, high = type_info(contract['output_type'])
    maximum = width if rule_id == 'popcount' else (1 << width)-1
    require(low == 0 and high >= maximum, 'Output cannot represent this unsigned rule')


def reference(rule_id, width, x):
    """Deliberately different structure from the expression oracle; no eval/code generation."""
    integer(x, 0, (1 << width)-1, 'rule input')
    if rule_id == 'gray_decode':
        y = bit = 0
        for i in range(width-1, -1, -1):
            bit ^= (x >> i) & 1
            y |= bit << i
        return y
    if rule_id == 'reverse_bits':
        return int(format(x, f'0{width}b')[::-1], 2)
    if rule_id == 'popcount':
        return bin(x).count('1')
    raise ValueError('Unknown rule')


def anchors(rule_id, width):
    mask = (1 << width)-1
    high = 1 << (width-1)
    if rule_id == 'gray_decode':
        return [(0, 0), (1, 1), (high, mask)]
    if rule_id == 'reverse_bits':
        return [(0, 0), (1, high), (high, 1), (mask, mask)]
    return [(0, 0), (1, 1), (high, 1), (mask, width)]


def property_check(rule_id, width, x, value, evaluate):
    """Return a witness on violation; relations are supplementary, never a full oracle."""
    mask = (1 << width)-1
    if rule_id == 'gray_decode':
        # Inverse relation with standard reflected-Gray encoding, not another generated oracle.
        lhs, rhs = value ^ (value >> 1), x
        name = 'decode_then_encode'
    elif rule_id == 'reverse_bits':
        lhs, rhs = evaluate(value), x
        name = 'reverse_twice'
    else:
        lhs, rhs = value + evaluate(x ^ mask), width
        name = 'complement_counts'
    return None if lhs == rhs else dict(property=name, input=x, left=lhs, right=rhs)
