Write a private development testbench from the public problem and the supplied validation plan. Candidate source, official tests, hidden results and reference implementations are unavailable. The plan is an unreviewed hypothesis: follow only claims justified by the public problem.

Implement every sound planned check using bounded deterministic C++14. Preserve the exact public function prototype, including bool, ap_int widths, references and arrays. Do not define the function or include its implementation. Use an independent expected-value calculation. Include comments identifying the implemented check IDs and excluded behavior. A partial plan remains partial even if every implemented check passes. Do not assert excluded/unknown outputs.

C++ functions can keep static state across calls. Use repeated calls and an independent state model where specified. No clock argument is required merely to call a stateful function. Never infer zero initialization, one-call/one-cycle ordering or an extra cycle of latency unless supported by the problem/plan. Use a specified reset/synchronizing sequence when needed. Preserve uncertainty about observation timing instead of guessing it.

Do not abandon well-defined checks because excluded behavior is unspecified. If even the planned checks have no sound interpretation or cannot be called safely, return abstention with the specific invalid check and missing information. Never generate a vacuous always-pass test.

Target Vitis/Vivado 2026.1. Use x[i] for a single ap_int/ap_uint bit, x.range(hi, lo) for a slice; neither x(i) nor x[hi:lo] is valid for that purpose. Widen operands before shifts used to assemble wider values. Do not use .to_ullong(). Prefer direct typed comparisons; to_ulong() is only for fitting widths, and to_string(10) can be printed as a quoted JSON string. Include ap_int.h/ap_fixed.h only when needed. bool is valid C++ and must not be silently replaced by ap_uint<1>.

Define int main(), actually call the top function, and compare its output against the planned expectations. Return nonzero on a mismatch and 0 only after executing all implemented checks. For a mismatch print a single line beginning ZCOMP_FUNCTIONAL followed by valid JSON with kind="output_mismatch" and fields such as index, signal, inputs, expected and actual. JSON numbers must be finite. Print no more than 20 mismatch records. Use iostream for diagnostic strings. No file access, environment access, subprocesses, networking, absolute paths, dynamic loading, random_device or time-dependent behavior.

Return exactly ZCOMP_SELFTEST_READY followed by one cpp fenced code block containing the complete source.
Or, when no meaningful planned check is sound, return ZCOMP_SELFTEST_ABSTAIN followed by a specific reason and check ID on the next line.
Do not return other prose. This test is unreviewed development evidence, not an official verdict.
