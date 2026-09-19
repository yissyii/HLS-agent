"""Offline retrieval worker; errors are explicit and no generation API is called."""
import argparse
from pathlib import Path

from rag.common import read_json, write_json
from rag.release import reference_records, verify_release
from rag.retrieve import Retriever


def retrieve(job):
    release, options = job['release'], job['options']
    verify_release(release)
    retriever = Retriever(release['corpus'], release['index'] if options['rag_mode'] == 'hybrid' else None,
                          job['model'])
    reference_records(retriever.records)
    evidence_filter = None
    if options.get('rag_strategy') == 'diagnostic_v1':
        from rag.query import assess
        evidence_filter = lambda record: assess(record, job['query_plan'])
    result = retriever.search(job['query'], mode=options['rag_mode'], top_k=options['rag_top_k'],
                              recall_k=options['rag_recall_k'], max_bytes=options['rag_max_bytes'],
                              evidence_filter=evidence_filter)
    return dict(result, status='completed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('output')
    args = parser.parse_args()
    if Path(args.output).exists():
        raise FileExistsError('Do not overwrite retrieval evidence')
    try:
        result = retrieve(read_json(args.input))
    except Exception as error:
        write_json(args.output, {'status': 'failed', 'error': type(error).__name__ + ': ' + str(error)})
        return 1
    write_json(args.output, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
