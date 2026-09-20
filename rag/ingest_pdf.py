"""Extract PDF bookmark sections with page provenance; never claim executable QA."""
import argparse
from collections import defaultdict
from pathlib import Path
import re
import textwrap

from rag.common import record, sha256, write_corpus, write_json


def bookmarks(reader):
    result = []

    def walk(items, parents=()):
        last = parents
        for item in items:
            if isinstance(item, list):
                walk(item, last)
            else:
                page = reader.get_destination_page_number(item)
                if page is None or page < 0:
                    continue
                title = str(item['/Title']).strip()
                y = item.get('/Top')
                top = float(reader.pages[page].mediabox.height) - float(y) if isinstance(y, (float, int)) else 0.0
                last = parents + (title,)
                result.append(dict(title=title, path=list(last), page=page + 1, top=max(0, top)))
    walk(reader.outline)
    result.sort(key=lambda e: (e['page'], e['top'], len(e['path'])))
    # Same-position container headings belong to the deepest named subsection.
    unique = {}
    for e in result:
        unique[(e['page'], round(e['top'], 1))] = e
    return list(unique.values())


def clean(text):
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if (re.match(r'^UG1399\s*\(v', stripped) or stripped == 'Send Feedback'
                or re.match(r'^Vitis HLS 用户指南\s+\d+$', stripped)
                or re.match(r'^第\s*\d+\s*章[：:]', stripped)
                or re.match(r'^第[一二三四五六七八九十]+部分[：:]', stripped)):
            continue
        lines.append(line.rstrip())
    return textwrap.dedent(re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))).strip()


def split_text(text, max_chars=1700):
    """Prefer blank lines; oversized paragraphs split by line, with explicit flags.

    PDF layout is not a C++ AST. Never present fragments as compilable examples.
    """
    chunks, current = [], ''
    for paragraph in re.split(r'\n\s*\n', text):
        paragraph = paragraph.strip('\n')
        if not paragraph.strip():
            continue
        if len(paragraph) > max_chars:
            if current:
                chunks.append((current, []))
                current = ''
            part = ''
            for line in paragraph.splitlines():
                if part and len(part) + len(line) + 1 > max_chars:
                    chunks.append((part, ['oversized_block_split', 'expand_parent_for_context']))
                    part = ''
                # A single oversized line stays intact rather than corrupting syntax.
                part += ('\n' if part else '') + line
            if part:
                chunks.append((part, ['oversized_block_split', 'expand_parent_for_context']))
        elif current and len(current) + len(paragraph) + 2 > max_chars:
            chunks.append((current, []))
            current = paragraph
        else:
            current += ('\n\n' if current else '') + paragraph
    if current:
        chunks.append((current, []))
    return chunks


def ingest(pdf_path, output, max_chars=1700, language='en-US'):
    from pypdf import PdfReader
    import pypdf
    import pdfplumber
    import pdfminer

    path = Path(pdf_path).resolve()
    digest = sha256(path.read_bytes())
    reader = PdfReader(path)
    if not re.search(r'UG1399\s*\(v2025\.2\)', reader.pages[0].extract_text()):
        raise ValueError('Expected UG1399 v2025.2 on the cover; filename is not sufficient')
    if Path(output).exists():
        raise FileExistsError('Use a new corpus directory to preserve previous evidence')
    headings = bookmarks(reader)
    by_page = defaultdict(list)
    for i, h in enumerate(headings):
        h['section_id'] = f'ug1399-2025.2-{language[:2]}-s{i:04d}'
        by_page[h['page']].append(h)
    segments = defaultdict(list)
    current = None
    raw_pages, coverage = [], []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            # Cache characters once per page; cropped views reuse this parsed data.
            _ = page.chars
            raw_pages.append({'page': page_no, 'text': page.extract_text(layout=False) or ''})
            page_heads = by_page.get(page_no, [])
            # Running headers occupy y<60; footer begins at y~755 on this edition.
            body_top, body_bottom = 60.0, page.height - 47.0
            cuts = [(body_top, current)]
            for h in page_heads:
                # Bookmark points typically align with the heading's top edge.
                pos = min(max(h['top'] - 2, body_top), body_bottom)
                cuts.append((pos, h))
            cuts.sort(key=lambda x: x[0])
            extracted = 0
            for j, (start, active) in enumerate(cuts):
                end = cuts[j + 1][0] if j + 1 < len(cuts) else body_bottom
                if active is None or end - start < 1:
                    continue
                if active['title'] in ('目录', 'Vitis 高层次综合用户指南',
                                       'Table of Contents', 'Vitis High-Level Synthesis User Guide'):
                    continue
                region = page.crop((0, start, page.width, end))
                text = clean(region.extract_text(layout=True, x_density=7.25, y_density=10) or '')
                if text:
                    segments[active['section_id']].append((page_no, text))
                    extracted += len(text)
            if page_heads:
                current = page_heads[-1]
            coverage.append({'page': page_no, 'raw_chars': len(raw_pages[-1]['text']), 'section_chars': extracted})
            page.close()
            if page_no % 50 == 0:
                print(f'PDF extraction {page_no}/{len(reader.pages)}', flush=True)
    records = []
    for h in headings:
        parts = segments.get(h['section_id'], [])
        if not parts:
            continue
        for page_no, text in parts:
            for n, (chunk, flags) in enumerate(split_text(text, max_chars)):
                identifier = f'{h["section_id"]}-p{page_no:04d}-{n:02d}'
                source = dict(document='UG1399', version='2025.2', language=language,
                              file=path.name, file_sha256=digest, page_start=page_no, page_end=page_no,
                              section_path=h['path'], parent_id=h['section_id'],
                              url='https://docs.amd.com/r/2025.2-' + ('English' if language == 'en-US' else 'Chinese') + '/ug1399-vitis-hls',
                              license='AMD documentation terms; not assumed Apache-2.0')
                records.append(record(identifier, h['title'], chunk, source, kind='document_section', role='doc',
                                      tool={'target_version': '2025.2', 'upstream_version': '2025.2', 'measured_version': None},
                                      release={'status': 'reference',
                                               'note': 'UG1399 v2025.2 official manual; source-reviewed, not locally HLS-validated'},
                                      validation={'status': 'unvalidated'},
                                      quality=['pdf_extracted_not_executable', *flags]))
    metadata = dict(source_type='pdf', source_file=path.name, source_sha256=digest,
                    source_pages=len(reader.pages), language=language, extraction='pdfplumber-layout-bookmarks-v2',
                    extraction_versions=dict(pdfplumber=pdfplumber.__version__, pypdf=pypdf.__version__, pdfminer=pdfminer.__version__),
                    max_chars=max_chars, skipped_front_matter=True,
                    validation_note='Document excerpts only; no HLS repair effectiveness claim.')
    manifest = write_corpus(output, records, metadata)
    write_json(Path(output) / 'outline.json', headings)
    write_json(Path(output) / 'coverage.json', coverage)
    write_json(Path(output) / 'pages.json', raw_pages)
    write_json(Path(output) / 'sections.json', {h['section_id']: {'heading': h, 'pages': segments.get(h['section_id'], [])} for h in headings})
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf')
    parser.add_argument('output')
    parser.add_argument('--max-chars', type=int, default=1700)
    parser.add_argument('--language', choices=['en-US', 'zh-CN'], default='en-US')
    args = parser.parse_args()
    if args.max_chars < 200:
        parser.error('--max-chars must be >= 200')
    print(ingest(args.pdf, args.output, args.max_chars, args.language))


if __name__ == '__main__':
    main()
