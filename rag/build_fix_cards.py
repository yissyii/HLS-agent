"""Build the small, source-anchored UG1399 fix-card corpus.

Fix cards are deliberately hand-authored summaries, not a second extraction of
the complete manual.  Each card carries applicability, exclusions, an action,
and a verification instruction so retrieval cannot present a topic match as a
universally valid repair.
"""
import argparse
import json
from pathlib import Path

from rag.common import file_sha256, load_corpus, record, write_corpus, write_json


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / 'docs' / 'ug1399-vitis-hls-en-us-2026.1.pdf'
GENERAL = ROOT / 'rag' / 'corpora' / 'ug1399-2026.1-en-curated'
DEFAULT_OUTPUT = ROOT / 'rag' / 'corpora' / 'ug1399-2026.1-fix-cards-v1'
UG_URL = 'https://docs.amd.com/r/en-US/ug1399-vitis-hls'


# ``parent_id`` values point at sections already extracted from the immutable
# PDF corpus.  The prose below is intentionally short; it is a routing and
# repair aid, not a replacement for the cited section.
CARDS = [
    dict(
        id='fix-ug1399-2026.1-interface-offset-vitis-kernel',
        title='m_axi offset: let the Vitis kernel flow select the offset',
        error_family='interface_offset',
        signature_terms=['offset', 'slave', 'direct', 'off', 'm_axi', 'Vitis kernel'],
        required_constructs=['m_axi', 'offset'],
        exclusions=['Vivado IP flow', 'reset_identifier', 's_axilite-only interface'],
        action='For the Vitis kernel flow, do not specify the offset option in the per-port INTERFACE pragma. Keep the documented syn.interface.m_axi_offset=direct flow setting unchanged and let the Vitis flow/XRT determine the required offsets.',
        applicability='Applies when the failing top-level pointer is mapped to m_axi in the Vitis kernel flow. This flow has stricter integration requirements than a standalone Vivado IP.',
        verification='Run C simulation, C synthesis, and inspect the generated interface/register map; then run co-simulation or the kernel integration check for the target flow.',
        source_citation='UG1399 v2026.1 pp. 161-162 (Interfaces for Vitis Kernel Flow: Rules for Offset)',
        parent_id='ug1399-2026.1-en-s0110', page_start=161, page_end=162,
    ),
    dict(
        id='fix-ug1399-2026.1-interface-offset-vivado-ip',
        title='m_axi offset: choose off, direct, or slave for Vivado IP',
        error_family='interface_offset',
        signature_terms=['offset', 'slave', 'direct', 'off', 'm_axi', 'Vivado IP'],
        required_constructs=['m_axi', 'offset'],
        exclusions=['Vitis kernel flow', 'reset_identifier', 'non-m_axi port'],
        action='For Vivado IP, choose offset=off for a fixed zero base, offset=direct when a runtime address port is required, or offset=slave when the host/control interface supplies the base address; do not mix modes without checking the generated ports.',
        applicability='Applies to a top-level m_axi interface synthesized for the Vivado IP flow. The correct mode depends on who controls the base address.',
        verification='Run C synthesis and inspect the RTL/IP ports and control register map to confirm that the generated address mechanism matches the integration design.',
        source_citation='UG1399 v2026.1 pp. 172-175 (Offset and Modes of Operation)',
        parent_id='ug1399-2026.1-en-s0114', page_start=172, page_end=175,
    ),
    dict(
        id='fix-ug1399-2026.1-ap-uint-bit-selection',
        title='ap_uint bit selection: use a valid zero-based bit index',
        error_family='ap_uint_bit_selection',
        signature_terms=['ap_uint', 'ap_int', 'operator []', 'bit selection', 'bit index', 'LSB'],
        required_constructs=['ap_uint', 'operator[]'],
        exclusions=['range selection', 'index outside bit width', 'reset_identifier'],
        action='Use value[bit] with an int index from 0 through value.width-1; index 0 is the least-significant bit. Assign through the returned bit reference only when a single-bit lvalue is intended.',
        applicability='Applies to C++ ap_int/ap_uint bit-level access. The index must denote one bit, not a high/low range.',
        verification='Compile and run a small C simulation that exercises index 0 and the highest valid index, then compare the expected bit values in co-simulation when the design is synthesized.',
        source_citation='UG1399 v2026.1 pp. 691-692 (Bit-Level Operations: Bit Selection)',
        parent_id='ug1399-2026.1-en-s0419', page_start=691, page_end=692,
    ),
    dict(
        id='fix-ug1399-2026.1-ap-uint-range-selection',
        title='ap_uint range selection: use Hi/Lo order and materialize the proxy',
        error_family='ap_uint_range_selection',
        signature_terms=['ap_uint', 'range', 'operator ()', 'Hi', 'Lo', 'range selection', 'bit reverse'],
        required_constructs=['ap_uint', 'range(Hi, Lo)'],
        exclusions=['single-bit operator[]', 'Hi/Lo outside width', 'chained method without cast'],
        action='Use value.range(Hi, Lo) or value(Hi, Lo), where Hi is the MSB and Lo is the LSB. If Hi < Lo the result is bit-reversed; construct an ap_uint/ap_int value explicitly before chaining methods because the range result is a proxy, not an ap_[u]int object.',
        applicability='Applies to C++ arbitrary-precision integer range reads and writes. Check the selected bounds and result width before assigning.',
        verification='Compile a boundary test for equal bounds, normal Hi>=Lo, and intentional Hi<Lo cases; compare the bit pattern in C simulation and RTL co-simulation.',
        source_citation='UG1399 v2026.1 pp. 692-693 (Bit-Level Operations: Range Selection)',
        parent_id='ug1399-2026.1-en-s0419', page_start=692, page_end=693,
    ),
    dict(
        id='fix-ug1399-2026.1-unsupported-system-calls',
        title='Unsupported C/C++ system calls in synthesizable code',
        error_family='unsupported_c_construct',
        signature_terms=['system call', 'printf', 'fprintf', 'getc', 'time', 'sleep', '__SYNTHESIS__'],
        required_constructs=['synthesizable function'],
        exclusions=['testbench-only logging', 'behavior-changing preprocessor workaround'],
        action='Remove operating-system calls from the synthesized function. If diagnostic file I/O is needed, guard it with a user-controlled macro for simulation and verify that the macro does not change the algorithmic behavior; never define or undefine __SYNTHESIS__ yourself.',
        applicability='Applies when an HLS source function calls the operating system or performs file/time/sleep operations. printf/fprintf may be ignored for synthesis but must not be relied on as hardware behavior.',
        verification='Run C simulation with the diagnostic path enabled, then compile and synthesize the same algorithm with the diagnostic path disabled; compare functional outputs.',
        source_citation='UG1399 v2026.1 pp. 151-152 (System Calls)',
        parent_id='ug1399-2026.1-en-s0100', page_start=151, page_end=152,
    ),
    dict(
        id='fix-ug1399-2026.1-dynamic-memory',
        title='Replace dynamic memory allocation in synthesized code',
        error_family='unsupported_c_construct',
        signature_terms=['malloc', 'alloc', 'free', 'dynamic memory', 'fixed-size', 'NO_SYNTH'],
        required_constructs=['malloc or free', 'synthesizable function'],
        exclusions=['testbench-only allocation', 'bounded static storage already present'],
        action='Replace runtime allocation with bounded, statically sized storage or pointers to fixed storage. If simulation needs heap allocation, use a separate user macro such as NO_SYNTH, compare both simulation variants, then synthesize with the macro disabled.',
        applicability='Applies to malloc/alloc/free or dynamically created/destroyed C/C++ objects in the design source. HLS hardware must have all required resources known and bounded.',
        verification='Run C simulation for both the heap and fixed-storage paths, require identical outputs, then run synthesis and co-simulation on the fixed-storage path.',
        source_citation='UG1399 v2026.1 pp. 152-154 (Dynamic Memory Usage)',
        parent_id='ug1399-2026.1-en-s0101', page_start=152, page_end=154,
    ),
    dict(
        id='fix-ug1399-2026.1-recursive-functions',
        title='Remove recursion from a synthesizable function',
        error_family='unsupported_c_construct',
        signature_terms=['recursive function', 'tail recursion', 'recursion', 'call graph'],
        required_constructs=['recursive function'],
        exclusions=['compile-only host code', 'template metaprogramming with no runtime recursion'],
        action='Rewrite the recursive algorithm as a bounded iterative loop or an explicit finite state/data structure before synthesis. Do not rely on tail-recursion elimination by the C++ compiler.',
        applicability='Applies when a synthesized function directly or indirectly calls itself, including tail recursion.',
        verification='Compile the rewritten bounded implementation, run the original and rewritten algorithms on the same test vectors, then synthesize the rewritten function.',
        source_citation='UG1399 v2026.1 p. 154 (Recursive Functions)',
        parent_id='ug1399-2026.1-en-s0103', page_start=154, page_end=154,
    ),
    dict(
        id='fix-ug1399-2026.1-standard-template-library',
        title='Avoid unsupported dynamic/recursive C++ standard template libraries',
        error_family='unsupported_c_construct',
        signature_terms=['STL', 'standard template library', 'std::', 'dynamic allocation', 'recursion'],
        required_constructs=['C++ standard template library'],
        exclusions=['std::complex supported forms', 'testbench-only STL'],
        action='Replace the STL operation in the synthesizable path with a local bounded function or fixed-size data structure that has no recursion or dynamic object creation; keep STL use confined to the testbench when appropriate.',
        applicability='Applies to STL containers/algorithms whose implementation uses recursion or dynamic memory. The manual separately notes that standard types such as std::complex have supported forms.',
        verification='Build a minimal C simulation for the replacement and compare it against a reference STL implementation before synthesis and co-simulation.',
        source_citation='UG1399 v2026.1 p. 155 (Standard Template Libraries)',
        parent_id='ug1399-2026.1-en-s0104', page_start=155, page_end=155,
    ),
    dict(
        id='fix-ug1399-2026.1-deprecated-data-pack',
        title='Replace deprecated or unsupported DATA_PACK pragma',
        error_family='deprecated_pragma',
        signature_terms=['DATA_PACK', 'AGGREGATE', 'packed', 'pragma', 'unsupported'],
        required_constructs=['DATA_PACK pragma or directive'],
        exclusions=['non-Vitis legacy tool flow', 'reset_identifier'],
        action='Remove DATA_PACK and use the AGGREGATE pragma/directive, adding a packed attribute only when the interface representation requires it.',
        applicability='Applies when migrating Vivado HLS code that still uses DATA_PACK under Vitis HLS 2026.1.',
        verification='Run C synthesis and inspect the generated interface layout and width; run co-simulation if the packed layout is externally consumed.',
        source_citation='UG1399 v2026.1 p. 834 (Deprecated and Unsupported Features, Table 84)',
        parent_id='ug1399-2026.1-en-s0513', page_start=833, page_end=834,
    ),
    dict(
        id='fix-ug1399-2026.1-interface-ap-bus',
        title='Replace unsupported INTERFACE ap_bus mode',
        error_family='deprecated_pragma',
        signature_terms=['INTERFACE', 'ap_bus', 'm_axi', 'unsupported', 'pragma'],
        required_constructs=['INTERFACE mode=ap_bus'],
        exclusions=['axis interface', 's_axilite-only interface', 'reset_identifier'],
        action='Replace INTERFACE mode=ap_bus with the supported m_axi interface and then review offset, bundle, depth, and control-interface settings for the target flow.',
        applicability='Applies to legacy Vivado HLS sources that specify INTERFACE mode=ap_bus in Vitis HLS.',
        verification='Run C synthesis and inspect the resulting AXI ports and control registers; use co-simulation or integration tests to validate protocol behavior.',
        source_citation='UG1399 v2026.1 p. 834 and p. 838 (Deprecated/Unsupported Pragmas)',
        parent_id='ug1399-2026.1-en-s0513', page_start=834, page_end=838,
    ),
    dict(
        id='fix-ug1399-2026.1-ap-int-header',
        title='Include ap_int.h for ap_int and ap_uint types',
        error_family='hls_header_api',
        signature_terms=['ap_int.h', 'ap_int', 'ap_uint', 'header', 'include'],
        required_constructs=['ap_int or ap_uint'],
        exclusions=['C arbitrary precision types', 'ap_fixed without ap_fixed.h'],
        action='Include ap_int.h in every source file that references ap_int/ap_uint and add the Vitis HLS include directory when compiling an external software model.',
        applicability='Applies to C++ sources using Vitis HLS arbitrary-precision integer classes; the template width is normally 1 to 1024 bits unless AP_INT_MAX_W is deliberately configured.',
        verification='Compile a minimal translation unit containing the include and type declaration, then run C simulation and synthesis with the same include configuration.',
        source_citation='UG1399 v2026.1 pp. 678-680 (C++ Arbitrary Precision Integer Types)',
        parent_id='ug1399-2026.1-en-s0414', page_start=679, page_end=680,
    ),
    dict(
        id='fix-ug1399-2026.1-hls-math-header',
        title='Use hls_math.h only from C++ and match the math type',
        error_family='hls_header_api',
        signature_terms=['hls_math.h', 'cmath', 'hls namespace', 'C++', 'C code', 'float', 'double'],
        required_constructs=['hls_math.h', 'C++ source'],
        exclusions=['C source file', 'unverified float/double conversion', 'reset_identifier'],
        action='Use hls_math.h from C++ and call the hls math APIs with deliberate float, double, half, or fixed-point types; do not include hls_math.h as a C header.',
        applicability='Applies when replacing standard C++ math functions with synthesizable HLS math functions. Type mixing can add conversion hardware and can change numerical results.',
        verification='Compile the C++ source, run C simulation against a reference with a documented tolerance, then run synthesis and co-simulation for the selected precision.',
        source_citation='UG1399 v2026.1 pp. 728-730 and 738 (HLS Math Library and Common Synthesis Errors)',
        parent_id='ug1399-2026.1-en-s0433', page_start=728, page_end=730,
    ),
    dict(
        id='fix-ug1399-2026.1-hls-stream-blocking',
        title='Use blocking hls::stream APIs for deterministic FIFO behavior',
        error_family='hls_header_api',
        signature_terms=['hls_stream.h', 'hls::stream', 'read', 'write', 'blocking', 'FIFO', 'deadlock'],
        required_constructs=['hls::stream', 'C++'],
        exclusions=['non-blocking API', 'axis write restriction', 'unbounded testbench assumption'],
        action='Include hls_stream.h for C++ hls::stream and use blocking read/write when deterministic behavior is required; size the FIFO with STREAM when producer/consumer rates can create stalls or deadlock.',
        applicability='Applies to streaming designs modeled with hls::stream<>. The top-level protocol differs between Vivado IP and Vitis kernel flows.',
        verification='Run C simulation and RTL co-simulation with the intended FIFO depth; confirm that no blocking deadlock or unexpected back-pressure occurs.',
        source_citation='UG1399 v2026.1 pp. 739 and 743 (HLS Stream Library, Blocking API)',
        parent_id='ug1399-2026.1-en-s0445', page_start=739, page_end=743,
    ),
    dict(
        id='fix-ug1399-2026.1-hls-stream-nonblocking',
        title='Treat non-blocking hls::stream behavior as an RTL-only risk',
        error_family='hls_header_api',
        signature_terms=['hls::stream', 'non-blocking', 'read_nb', 'write_nb', 'axis', 'cosimulation', 'nondeterministic'],
        required_constructs=['hls::stream non-blocking API'],
        exclusions=['blocking API', 'ap_fifo write support', 'C-simulation-only claim'],
        action='Use non-blocking stream calls only when the protocol supports them and the design explicitly handles the Boolean status; do not treat passing C simulation as proof because FIFO fullness and timing can differ in RTL.',
        applicability='Applies to non-blocking reads/writes. AXI4-Stream supports non-blocking reads but not non-blocking writes, and ap_ctrl_none plus non-blocking streams may prevent co-simulation completion.',
        verification='Run RTL co-simulation with the actual FIFO depths and an RTL testbench; do not release a non-blocking change based on C simulation alone.',
        source_citation='UG1399 v2026.1 pp. 745-746 (Non-Blocking API)',
        parent_id='ug1399-2026.1-en-s0449', page_start=745, page_end=746,
    ),
    dict(
        id='fix-ug1399-2026.1-dataflow-single-producer-consumer',
        title='Repair dataflow single-producer/single-consumer violations',
        error_family='dataflow_limitation',
        signature_terms=['DATAFLOW', 'single-producer', 'single-consumer', 'fanout', 'conditional task'],
        required_constructs=['dataflow region', 'inter-task variable'],
        exclusions=['independent non-dataflow functions', 'stream feedback exception'],
        action='Restructure the dataflow graph so every inter-task variable has one producer and one consumer; insert an explicit split/duplication task for fanout, and move conditionals inside always-executed tasks when possible.',
        applicability='Applies when dataflow overlap is absent or a diagnostic reports a producer/consumer violation, conditional task, or input/output access in the middle of a region.',
        verification='Inspect the Dataflow Viewer and co-simulation timeline, then compare the achieved overlap/II against the intended graph.',
        source_citation='UG1399 v2026.1 pp. 319-323 (Limitations of Control-Driven Task-Level Parallelism)',
        parent_id='ug1399-2026.1-en-s0198', page_start=319, page_end=323,
    ),
    dict(
        id='fix-ug1399-2026.1-dataflow-multiple-exits',
        title='Remove break/continue multiple exits from a dataflow region',
        error_family='dataflow_limitation',
        signature_terms=['DATAFLOW', 'multiple exit', 'break', 'continue', 'loop', 'unsupported'],
        required_constructs=['dataflow region', 'loop with break or continue'],
        exclusions=['single bounded loop without extra exits', 'non-dataflow loop'],
        action='Rewrite a dataflow loop so its exit is represented by fixed bounds and predicates inside the loop; remove break/continue from the region or move the loop outside the dataflow region.',
        applicability='Applies when a dataflow region contains a loop with multiple exit conditions, including a bound plus break or continue.',
        verification='Run synthesis and confirm the tool recognizes the intended processes; inspect the dataflow report and run co-simulation for functional equivalence.',
        source_citation='UG1399 v2026.1 pp. 323-324 (Loops with Multiple Exit Conditions)',
        parent_id='ug1399-2026.1-en-s0198', page_start=323, page_end=324,
    ),
    dict(
        id='fix-ug1399-2026.1-pipeline-carried-dependency',
        title='Handle carried dependencies before forcing PIPELINE II=1',
        error_family='pipeline_limitation',
        signature_terms=['PIPELINE', 'II=1', 'RAW', 'carried dependency', 'SCHED 204-68', 'Final II'],
        required_constructs=['pipelined loop', 'loop-carried dependency'],
        exclusions=['false dependency proven absent', 'unrelated memory-port warning'],
        action='Treat a true RAW/WAR/WAW loop-carried dependency as a functional ordering constraint; refactor the algorithm or accept a larger II. Use DEPENDENCE false only when the dependency is proven false, because a wrong assertion can produce incorrect hardware.',
        applicability='Applies when scheduling diagnostics show a carried dependency or the final II is greater than the requested II because later iterations need earlier results.',
        verification='Run synthesis, inspect the dependency report and final II, and compare C simulation with RTL co-simulation after any refactor or DEPENDENCE directive.',
        source_citation='UG1399 v2026.1 pp. 67 and 72-77 (Pipelining and Managing Pipeline Dependencies)',
        parent_id='ug1399-2026.1-en-s0039', page_start=72, page_end=77,
    ),
    dict(
        id='fix-ug1399-2026.1-array-partition-memory-ports',
        title='Use ARRAY_PARTITION for array memory-port contention',
        error_family='array_partition_limit',
        signature_terms=['ARRAY_PARTITION', 'array_partition', 'memory ports', 'SCHED 204-69', 'Final II', 'RAM'],
        required_constructs=['array', 'PIPELINE', 'multiple accesses per iteration'],
        exclusions=['interface bundle problem', 'unbounded dynamic array', 'partition without resource budget'],
        action='When a pipelined loop needs more array accesses than the inferred RAM ports allow, choose ARRAY_PARTITION block, cyclic, or complete with an appropriate factor/dim; account for the added RAMs/registers and do not assume II=1 is free.',
        applicability='Applies to local arrays implemented as memories when scheduling reports limited RAM ports or a final II greater than the target due to load contention.',
        verification='Run synthesis and inspect RAM/register utilization, achieved II, and access mapping; run co-simulation to confirm that partitioning preserved behavior.',
        source_citation='UG1399 v2026.1 pp. 86-90 (Array Accesses and Performance; Array Partitioning)',
        parent_id='ug1399-2026.1-en-s0047', page_start=88, page_end=90,
    ),
]


def build(output=DEFAULT_OUTPUT):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite immutable corpus: {output}')
    if not PDF.is_file():
        raise FileNotFoundError(PDF)
    # Load the general corpus only as a source-of-truth for bookmark paths and
    # to fail early if a card cites a section that is not in the PDF extraction.
    general_records, _ = load_corpus(GENERAL)
    by_parent = {}
    for item in general_records:
        by_parent.setdefault(item['source']['parent_id'], item)
    digest = file_sha256(PDF)
    records = []
    for card in CARDS:
        parent = by_parent.get(card['parent_id'])
        if parent is None:
            raise ValueError(f'Unknown UG1399 section: {card["parent_id"]}')
        source = dict(
            document='UG1399', version='2026.1', language='en-US',
            file=PDF.name, file_sha256=digest,
            page_start=card['page_start'], page_end=card['page_end'],
            section_path=parent['source']['section_path'],
            parent_id=card['parent_id'], license='AMD documentation terms; card prose is original',
            citation=card['source_citation'], url=UG_URL,
        )
        text = (
            f'Error family: {card["error_family"]}\n'
            f'Signature terms: {", ".join(card["signature_terms"])}\n'
            f'Required constructs: {", ".join(card["required_constructs"]) or "none"}\n'
            f'Exclusions: {", ".join(card["exclusions"]) or "none"}\n\n'
            f'Action: {card["action"]}\n'
            f'Applicability: {card["applicability"]}\n'
            f'Verification: {card["verification"]}'
        )
        item = record(
            card['id'], card['title'], text, source, kind='fix_card', role='design',
            aliases=card['signature_terms'],
            tool=dict(target_version='2026.1', upstream_version='2026.1', measured_version=None),
            release=dict(status='reference', note='Human-reviewed fix card; source-anchored to UG1399, not a blanket rule.'),
            validation=dict(status='human_reviewed', config_sha256=None,
                             evidence='Manual review against the cited UG1399 v2026.1 pages; no Vitis run claimed.'),
            quality=['human_authored', 'source_anchored', 'applicability_required'],
            extra=dict(
                corpus_class='fix', corpus_priority=110,
                corpus_reason='small high-precision repair card with explicit applicability/exclusions',
                error_family=card['error_family'], signature_terms=card['signature_terms'],
                required_constructs=card['required_constructs'], exclusions=card['exclusions'],
                action=card['action'], applicability=card['applicability'],
                verification=card['verification'], source_citation=card['source_citation'],
            ),
        )
        records.append(item)
    metadata = dict(
        source_type='human_authored_fix_cards', source_file=PDF.name,
        source_sha256=digest, document='UG1399', document_version='2026.1',
        language='en-US', profile='fix-cards-v1', corpus_class='fix',
        validation_note='Cards are human-reviewed summaries anchored to UG1399 pages; no HLS execution is claimed.',
        card_count=len(records), card_ids=[r['id'] for r in records],
        general_corpus='rag/corpora/ug1399-2026.1-en-curated',
    )
    manifest = write_corpus(output, records, metadata)
    write_json(output / 'cards.json', {'schema_version': 1, 'cards': records})
    write_json(output / 'README.json', {
        'purpose': 'Small high-precision fix-card corpus, separate from the UG1399 general corpus.',
        'release_policy': 'Reference only until each card is validated against a reproducible 2026.1 HLS case.',
        'source_pdf': PDF.name, 'source_sha256': digest,
        'manual_review': 'Cards were reviewed against their cited UG1399 sections; human_reviewed is not synthesis_passed.',
    })
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
