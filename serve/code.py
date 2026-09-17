"""Deterministic C/C++ extraction, without compilation, repair or extra sampling."""
import re
from serve.inference import Failure

_FENCE = re.compile(r'^[ \t]*(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)$')
_LANGUAGES = {'', 'c', 'cpp', 'c++', 'cc', 'cxx', 'h', 'hpp', 'hxx'}
_NON_FUNCTIONS = {'if', 'for', 'while', 'switch', 'catch', 'sizeof', 'alignof',
                  'decltype', 'noexcept', 'static_assert', '__attribute__'}


def _blocks(text):
    """Read line-delimited Markdown fences, preserving each block's source bytes."""
    blocks, current, offset = [], None, 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n')
        if current is None:
            match = _FENCE.fullmatch(content)
            if match:
                info = match['info'].strip()
                current = dict(index=len(blocks), language=info.split()[0].lower() if info else '',
                               marker=match['marker'], start=offset, body_start=offset + len(line))
        else:
            closing = content.strip(' \t')
            if (len(closing) >= len(current['marker']) and
                    set(closing) == {current['marker'][0]}):
                blocks.append(dict(current, source=text[current['body_start']:offset],
                                   end=offset + len(line), closed=True))
                current = None
        offset += len(line)
    if current is not None:
        blocks.append(dict(current, source=text[current['body_start']:], end=len(text), closed=False))
    return blocks


def _mask_literals(source):
    """Mask comments and literals before scoring; this is not a C++ parser."""
    masked = list(source)
    index, complete = 0, True
    while index < len(source):
        end = None
        if source.startswith('//', index):
            end = source.find('\n', index + 2)
            end = len(source) if end < 0 else end
        elif source.startswith('/*', index):
            closing = source.find('*/', index + 2)
            complete &= closing >= 0
            end = len(source) if closing < 0 else closing + 2
        elif source.startswith('R"', index):
            opening = re.match(r'R"([^ ()\\\t\r\n]{0,16})\(', source[index:])
            if opening:
                closing_marker = ')' + opening[1] + '"'
                closing = source.find(closing_marker, index + opening.end())
                complete &= closing >= 0
                end = len(source) if closing < 0 else closing + len(closing_marker)
        if end is None and source[index] in {'"', "'"}:
            # A digit separator in 1'000 / 0xA'B is not a character literal.
            separator = (source[index] == "'" and index > 0 and index + 1 < len(source)
                         and source[index - 1].isalnum() and source[index + 1].isalnum())
            if not separator:
                quote, end = source[index], index + 1
                while end < len(source) and source[end] != quote:
                    end += 2 if source[end] == '\\' else 1
                complete &= end < len(source)
                end = min(end + 1, len(source))
        if end is None:
            index += 1
            continue
        for position in range(index, end):
            if masked[position] not in '\r\n':
                masked[position] = ' '
        index = end
    return ''.join(masked), complete


def _features(source, top_function):
    code, complete = _mask_literals(source)
    stack, closing_parens = [], {}
    pairs = {')': '(', ']': '[', '}': '{'}
    for position, character in enumerate(code):
        if character in '([{':
            stack.append((character, position))
        elif character in pairs:
            if not stack or stack[-1][0] != pairs[character]:
                complete = False
            else:
                opening, start = stack.pop()
                if opening == '(':
                    closing_parens[start] = position
    complete = bool(complete and not stack)
    definitions = []
    for match in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', code):
        name = match[1]
        closing = closing_parens.get(match.end() - 1)
        if name in _NON_FUNCTIONS or closing is None:
            continue
        # Common HLS C/C++ signatures; intentionally does not claim full parsing.
        suffix = code[closing + 1:]
        if re.match(r'\s*(?:(?:const|volatile|override|final|noexcept)\s*)*(?:->[^;{}=]+)?\s*\{', suffix):
            definitions.append(name)
    non_main = [name for name in definitions if name != 'main']
    features = dict(structurally_complete=complete, functions=definitions,
                    target_defined=bool(top_function and top_function in definitions),
                    contains_main='main' in definitions, non_main_functions=len(non_main),
                    includes=bool(re.search(r'^\s*#\s*include\b', code, re.M)),
                    hls_pragmas=bool(re.search(r'^\s*#\s*pragma\s+HLS\b', code, re.M)),
                    code_tokens=len(re.findall(r'\w+|[^\s\w]', code)))
    # Lexicographic priorities: plausible complete implementation, target match,
    # design rather than testbench, then self-contained supporting structure.
    score = (int(complete and bool(definitions)), int(complete), int(features['target_defined']),
             int(not features['contains_main']), int(bool(non_main)), len(non_main),
             int(features['includes']), int(features['hls_pragmas']), features['code_tokens'])
    return features, score


def extract_code(text, *, top_function=None, details=None):
    """Select one source block unchanged; ties keep the first block in the response.

    Optional details records the fixed scoring rule, all candidates and the
    selection. Scoring estimates completeness; only the validator proves it.
    """
    audit = details if details is not None else {}
    audit.clear()
    audit.update(rule_version='ranked_fences_v1', top_function=top_function, candidates=[])
    if not isinstance(text, str) or not text.strip():
        raise Failure('response_format_error', 'Empty source')
    blocks = _blocks(text)
    if not blocks:
        audit.update(method='verbatim', selected_block=None)
        return text, 'verbatim'
    eligible = []
    for block in blocks:
        record = dict(index=block['index'], language=block['language'], closed=block['closed'])
        if not block['closed']:
            record['excluded'] = 'unclosed_fence'
        elif block['language'] not in _LANGUAGES:
            record['excluded'] = 'non_c_cpp_language'
        elif not block['source'].strip():
            record['excluded'] = 'empty_block'
        else:
            features, score = _features(block['source'], top_function)
            # An unlabeled fence may contain command output/prose. Require a
            # C/C++ structure signal before treating it as a source candidate.
            if block['language'] == '' and not (features['functions'] or features['includes'] or
                    re.search(r'\b(?:int|void|char|float|double|bool|struct|class|typedef|template|namespace)\b', block['source'])):
                record['excluded'] = 'unlabeled_non_source'
            else:
                record.update(features, score=list(score))
                eligible.append((score, block))
        audit['candidates'].append(record)
    if not eligible:
        raise Failure('response_format_error', 'No nonempty, closed C/C++ source block')
    score, selected = max(eligible, key=lambda candidate: candidate[0])
    ties = [block['index'] for rank, block in eligible if rank == score]
    outer = (len(blocks) == 1 and not text[:selected['start']].strip() and not text[selected['end']:].strip())
    method = ('ranked_code_fence_selected' if len(eligible) > 1 else
              'single_outer_fence_removed' if outer else 'single_code_fence_extracted')
    audit.update(method=method, selected_block=selected['index'], tied_blocks=ties,
                 tie_break='first_in_response' if len(ties) > 1 else None)
    return selected['source'], method
