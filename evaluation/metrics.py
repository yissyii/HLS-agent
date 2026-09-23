'''Ability metrics exclude unresolved infrastructure failures and unknown causes.'''
ABILITY_FAILURES = {
    'response_format_error', 'generation_incomplete', 'compile_error',
    'functional_or_runtime_error', 'synthesis_error', 'compile_or_csim_error',
    'repeated_candidate', 'stagnation', 'repair_budget_exhausted',
    'context_budget_exceeded',
}


def eligible(row):
    if row.get('overall'):
        return True
    category = row.get('category') or row.get('generation_category') or row.get('reason') or row.get('stop_reason')
    return category in ABILITY_FAILURES


def summarize(results):
    report = {}
    for method in ('baseline', 'agent'):
        rows = [r for r in results if r.get('method') == method]
        scored = [r for r in rows if eligible(r)]
        passed = sum(bool(r.get('overall')) for r in scored)
        report[method] = dict(completed=len(rows), scored=len(scored),
                              passed=passed, pending=len(rows)-len(scored),
                              pass_rate=passed/len(scored) if scored else None,
                              pending_tasks=[r['task'] for r in rows if not eligible(r)])
    by_method = {m: {r['task']: r for r in results if r.get('method') == m} for m in report}
    common = sorted(set(by_method['baseline']) & set(by_method['agent']))
    common = [t for t in common if all(eligible(by_method[m][t]) for m in report)]
    report['paired'] = dict(tasks=len(common), **{
        m: sum(bool(by_method[m][t].get('overall')) for t in common) for m in by_method})
    return report
