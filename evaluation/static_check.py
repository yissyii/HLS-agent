"""Read-only C/C++ frontend and AST rules for HLS candidates.

Never writes the candidate or task materials. Official frontend is the Vitis
2025.2 bundled clang-16 on the remote Linux host; missing tools become UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
from ctypes import CFUNCTYPE, POINTER, c_char_p, c_int, c_uint, c_void_p
import os
from pathlib import Path
import time


PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# Clang 16 clang-c/Index.h
FUNCTION_DECL = 8
CXX_METHOD = 21
CONSTRUCTOR = 24
DESTRUCTOR = 25
PARM_DECL = 10
CALL_EXPR = 103
CXX_THROW_EXPR = 107
CXX_NEW_EXPR = 134
CXX_DELETE_EXPR = 135
CXX_TRY_STMT = 223
COMPOUND_STMT = 202

CHILD_BREAK, CHILD_CONTINUE, CHILD_RECURSE = 0, 1, 2
DIAG_ERROR, DIAG_FATAL = 3, 4
KEEP_GOING = 0x200

ALLOC_FNS = {"malloc", "calloc", "realloc", "free", "operator new", "operator new[]",
             "operator delete", "operator delete[]"}

RULES = {
    "R01_TOP_INTERFACE": {
        "title": "显式顶层接口契约",
        "applies_to": "候选必须定义与公开测试台声明一致的顶层函数",
        "contract_source": (
            "仅两处公开材料：task.json 的 top_function 名字；"
            "同一任务 tb.cpp 里该名字的 FunctionDecl（优先声明，其次定义）。"
            "不使用 problem 文本、参考实现或模型输出反推契约。"
        ),
        "evidence": "公开 task.json + 公开测试台 AST",
        "limits": "只比较规范化后的 clang 类型拼写；测试台无法解析时 UNKNOWN",
        "positive": "void TopModule(ap_uint<1>& zero) { zero = 0; }",
        "negative": "缺少 TopModule 定义，或参数个数/类型与测试台声明不一致",
    },
    "R02_DISABLED_CONSTRUCT": {
        "title": "观察到的可疑构造（本版不硬拦截）",
        "applies_to": "候选主文件中的动态存储、虚函数和异常，仅记录",
        "evidence": "本工具链尚未用方案 A 逐条确认这些构造必然失败，因此不得一律判 FAIL",
        "limits": "检出记 UNKNOWN 观察项，不进入 C 的硬拦截；未检出记 PASS。留出集不得用来把本规则升级为硬 FAIL",
        "positive": "静态数组或 ap_int 局部变量",
        "negative": "记录 new/虚函数/try，但不因此拒绝候选",
    },
    "R03_RUNTIME_RECURSION": {
        "title": "可可靠识别的运行时递归",
        "applies_to": "候选主文件函数之间的直接或间接调用环",
        "evidence": "主文件 FunctionDecl 调用图上的环",
        "limits": "函数指针、未解析调用、跨翻译单元递归返回 UNKNOWN，不把不确定情况判 FAIL",
        "positive": "无环的辅助函数调用",
        "negative": "void f() { f(); } 或 f->g->f",
    },
}

HARD_RULES = frozenset({"R01_TOP_INTERFACE", "R03_RUNTIME_RECURSION"})
CONTRACT_ORIGINS = (
    "public_task_json_top_function",
    "public_testbench_declaration",
    "public_testbench_definition",
)


@dataclass
class Finding:
    rule_id: str
    verdict: str
    location: str | None
    diagnostic: str
    elapsed_seconds: float = 0.0


@dataclass
class CheckResult:
    status: str
    scheme: str
    frontend: str
    findings: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    command: list = field(default_factory=list)
    note: str = ""


class CXString(ctypes.Structure):
    _fields_ = [("data", c_void_p), ("private_flags", c_uint)]


class CXCursor(ctypes.Structure):
    _fields_ = [("kind", c_int), ("xdata", c_int), ("data", c_void_p * 3)]


class CXType(ctypes.Structure):
    _fields_ = [("kind", c_int), ("data", c_void_p * 2)]


class CXSourceLocation(ctypes.Structure):
    _fields_ = [("ptr_data", c_void_p * 2), ("int_data", c_uint)]


def _cx(lib, name):
    fn = getattr(lib, "clang_getCString" if name == "cstr" else name)
    return fn


def _ensure_search_path(name, prefixes):
    current = [p for p in os.environ.get(name, "").split(os.pathsep) if p]
    seen = set()
    ordered = []
    for path in list(prefixes) + current:
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    os.environ[name] = os.pathsep.join(ordered)


def _linux_lib_dirs(root):
    return [
        root / "lib/lnx64.o",
        root / "lnx64/tools/clang-16/lib",
    ]


def _preload_linux_deps(directories):
    # LD_LIBRARY_PATH is snapshotted at process start; later os.environ edits
    # do not help ctypes.CDLL. Load known deps by absolute path first.
    names = (
        "libboost_filesystem.so.1.72.0",
        "libboost_system.so.1.72.0",
        "libboost_regex.so.1.72.0",
        "libboost_serialization.so.1.72.0",
    )
    for directory in directories:
        for name in names:
            path = directory / name
            if path.is_file():
                try:
                    ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def load_libclang(vitis_root):
    root = Path(vitis_root)
    candidates = [
        root / "lnx64/tools/clang-16/lib/libclang.so",
        root / "lnx64/tools/clang-16/lib/libclang.so.16",
        root / "win64/tools/clang-16/bin/libclang.dll",
    ]
    extra = _linux_lib_dirs(root) + [
        root / "lib/win64.o",
        root / "tps/win64",
        root / "win64/tools/clang-16/bin",
    ]
    if os.name == "nt":
        for directory in extra:
            if directory.is_dir():
                try:
                    os.add_dll_directory(str(directory))
                except OSError:
                    pass
        _ensure_search_path("PATH", [str(p) for p in extra if p.is_dir()])
    else:
        libs = [p for p in extra if p.is_dir()]
        _ensure_search_path("LD_LIBRARY_PATH", [str(p) for p in libs])
        _preload_linux_deps(libs)
    last = None
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return ctypes.CDLL(str(path)), path
        except OSError as error:
            last = error
    raise FileNotFoundError(last or "libclang not found under Vitis root")


def _bind(lib):
    lib.clang_createIndex.restype = c_void_p
    lib.clang_createIndex.argtypes = [c_int, c_int]
    lib.clang_disposeIndex.argtypes = [c_void_p]
    lib.clang_parseTranslationUnit2.restype = c_int
    lib.clang_parseTranslationUnit2.argtypes = [
        c_void_p, c_char_p, POINTER(c_char_p), c_int, c_void_p, c_uint, c_uint, POINTER(c_void_p)
    ]
    lib.clang_disposeTranslationUnit.argtypes = [c_void_p]
    lib.clang_getTranslationUnitCursor.restype = CXCursor
    lib.clang_getTranslationUnitCursor.argtypes = [c_void_p]
    lib.clang_visitChildren.restype = c_uint
    lib.clang_getCursorKind.argtypes = [CXCursor]
    lib.clang_getCursorKind.restype = c_int
    lib.clang_getCursorSpelling.restype = CXString
    lib.clang_getCursorSpelling.argtypes = [CXCursor]
    lib.clang_getCursorUSR.restype = CXString
    lib.clang_getCursorUSR.argtypes = [CXCursor]
    lib.clang_getCursorType.restype = CXType
    lib.clang_getCursorType.argtypes = [CXCursor]
    lib.clang_getTypeSpelling.restype = CXString
    lib.clang_getTypeSpelling.argtypes = [CXType]
    lib.clang_getResultType.restype = CXType
    lib.clang_getResultType.argtypes = [CXType]
    lib.clang_getNumArgTypes.restype = c_int
    lib.clang_getNumArgTypes.argtypes = [CXType]
    lib.clang_getArgType.restype = CXType
    lib.clang_getArgType.argtypes = [CXType, c_uint]
    lib.clang_Cursor_getNumArguments.restype = c_int
    lib.clang_Cursor_getNumArguments.argtypes = [CXCursor]
    lib.clang_Cursor_getArgument.restype = CXCursor
    lib.clang_Cursor_getArgument.argtypes = [CXCursor, c_uint]
    lib.clang_isCursorDefinition.restype = c_uint
    lib.clang_isCursorDefinition.argtypes = [CXCursor]
    lib.clang_getCursorLocation.restype = CXSourceLocation
    lib.clang_getCursorLocation.argtypes = [CXCursor]
    lib.clang_Location_isFromMainFile.restype = c_int
    lib.clang_Location_isFromMainFile.argtypes = [CXSourceLocation]
    lib.clang_getFileLocation.argtypes = [
        CXSourceLocation, POINTER(c_void_p), POINTER(c_uint), POINTER(c_uint), POINTER(c_uint)
    ]
    lib.clang_getFileName.restype = CXString
    lib.clang_getFileName.argtypes = [c_void_p]
    lib.clang_getCursorReferenced.restype = CXCursor
    lib.clang_getCursorReferenced.argtypes = [CXCursor]
    lib.clang_getCursorSemanticParent.restype = CXCursor
    lib.clang_getCursorSemanticParent.argtypes = [CXCursor]
    lib.clang_CXXMethod_isVirtual.restype = c_uint
    lib.clang_CXXMethod_isVirtual.argtypes = [CXCursor]
    lib.clang_getNumDiagnostics.restype = c_uint
    lib.clang_getNumDiagnostics.argtypes = [c_void_p]
    lib.clang_getDiagnostic.restype = c_void_p
    lib.clang_getDiagnostic.argtypes = [c_void_p, c_uint]
    lib.clang_getDiagnosticSeverity.restype = c_int
    lib.clang_getDiagnosticSeverity.argtypes = [c_void_p]
    lib.clang_formatDiagnostic.restype = CXString
    lib.clang_formatDiagnostic.argtypes = [c_void_p, c_uint]
    lib.clang_disposeDiagnostic.argtypes = [c_void_p]
    lib.clang_getCString.restype = c_char_p
    lib.clang_getCString.argtypes = [CXString]
    lib.clang_disposeString.argtypes = [CXString]
    lib.clang_getNullCursor.restype = CXCursor
    return lib


def _string(lib, cxstr):
    raw = lib.clang_getCString(cxstr)
    text = raw.decode("utf-8", "replace") if raw else ""
    lib.clang_disposeString(cxstr)
    return text


def _loc(lib, cursor):
    location = lib.clang_getCursorLocation(cursor)
    file = c_void_p()
    line = c_uint()
    column = c_uint()
    offset = c_uint()
    lib.clang_getFileLocation(location, ctypes.byref(file), ctypes.byref(line),
                              ctypes.byref(column), ctypes.byref(offset))
    if not file:
        return None
    name = _string(lib, lib.clang_getFileName(file))
    return "%s:%d:%d" % (Path(name).name, line.value, column.value)


def _main(lib, cursor):
    return bool(lib.clang_Location_isFromMainFile(lib.clang_getCursorLocation(cursor)))


def _norm_type(text):
    return " ".join(text.replace(" &", "&").replace("& ", "&").replace(" *", "*").split())


def _resource_dir(vitis_root):
    root = Path(vitis_root)
    for rel in ("lnx64/tools/clang-16/lib/clang/16", "win64/tools/clang-16/lib/clang/16"):
        path = root / rel
        if (path / "include").is_dir():
            return path
    return None


def frontend_args(vitis_root, include_dirs, cxx_standard):
    args = ["-fsyntax-only", "-x", "c++", "-std=" + cxx_standard,
            "-I" + str(Path(vitis_root) / "include")]
    resource = _resource_dir(vitis_root)
    if resource:
        args.extend(["-resource-dir", str(resource)])
    for directory in include_dirs:
        args.append("-I" + str(directory))
    return args


def _diagnostics(lib, tu):
    rows = []
    missing_header = False
    error = False
    for i in range(lib.clang_getNumDiagnostics(tu)):
        diag = lib.clang_getDiagnostic(tu, i)
        severity = lib.clang_getDiagnosticSeverity(diag)
        text = _string(lib, lib.clang_formatDiagnostic(diag, 0))
        lib.clang_disposeDiagnostic(diag)
        rows.append({"severity": severity, "text": text})
        if severity >= DIAG_ERROR:
            error = True
            lowered = text.lower()
            if "file not found" in lowered or "no such file" in lowered:
                missing_header = True
    if missing_header:
        return UNKNOWN, rows
    return (FAIL if error else PASS), rows


def _parent_function(lib, cursor):
    current = cursor
    for _ in range(64):
        kind = lib.clang_getCursorKind(current)
        if kind in {FUNCTION_DECL, CXX_METHOD, CONSTRUCTOR, DESTRUCTOR}:
            usr = _string(lib, lib.clang_getCursorUSR(current))
            name = _string(lib, lib.clang_getCursorSpelling(current))
            return usr or name, name
        parent = lib.clang_getCursorSemanticParent(current)
        if parent.kind == 0 and parent.xdata == 0 and not parent.data[0]:
            return None, None
        current = parent
    return None, None


def extract_ir(lib, tu):
    functions = []
    calls = []
    news = []
    deletes = []
    virtuals = []
    exceptions = []
    unresolved = False

    def function_record(cursor):
        ftype = lib.clang_getCursorType(cursor)
        params = []
        n = lib.clang_Cursor_getNumArguments(cursor)
        for i in range(max(n, 0)):
            arg = lib.clang_Cursor_getArgument(cursor, i)
            params.append({
                "name": _string(lib, lib.clang_getCursorSpelling(arg)),
                "type": _norm_type(_string(lib, lib.clang_getTypeSpelling(lib.clang_getCursorType(arg)))),
            })
        if n < 0:
            ntypes = lib.clang_getNumArgTypes(ftype)
            for i in range(max(ntypes, 0)):
                params.append({
                    "name": "",
                    "type": _norm_type(_string(lib, lib.clang_getTypeSpelling(lib.clang_getArgType(ftype, i)))),
                })
        return {
            "name": _string(lib, lib.clang_getCursorSpelling(cursor)),
            "usr": _string(lib, lib.clang_getCursorUSR(cursor)),
            "is_definition": bool(lib.clang_isCursorDefinition(cursor)),
            "return_type": _norm_type(_string(lib, lib.clang_getTypeSpelling(lib.clang_getResultType(ftype)))),
            "params": params,
            "location": _loc(lib, cursor),
        }

    @CFUNCTYPE(c_int, CXCursor, CXCursor, c_void_p)
    def visitor(cursor, parent, client):
        nonlocal unresolved
        kind = lib.clang_getCursorKind(cursor)
        if kind in {FUNCTION_DECL, CXX_METHOD, CONSTRUCTOR, DESTRUCTOR} and _main(lib, cursor):
            record = function_record(cursor)
            functions.append(record)
            if kind == CXX_METHOD and lib.clang_CXXMethod_isVirtual(cursor):
                virtuals.append({"name": record["name"], "location": record["location"]})
        if not _main(lib, cursor):
            return CHILD_RECURSE
        if kind == CXX_NEW_EXPR:
            news.append({"location": _loc(lib, cursor)})
        elif kind == CXX_DELETE_EXPR:
            deletes.append({"location": _loc(lib, cursor)})
        elif kind in {CXX_TRY_STMT, CXX_THROW_EXPR}:
            exceptions.append({"kind": kind, "location": _loc(lib, cursor)})
        elif kind == CALL_EXPR:
            callee = lib.clang_getCursorReferenced(cursor)
            callee_name = _string(lib, lib.clang_getCursorSpelling(callee)) if callee.kind else ""
            callee_usr = _string(lib, lib.clang_getCursorUSR(callee)) if callee.kind else ""
            caller_usr, caller_name = _parent_function(lib, parent)
            resolved = callee.kind in {FUNCTION_DECL, CXX_METHOD, CONSTRUCTOR, DESTRUCTOR} and bool(callee_usr or callee_name)
            if not resolved and callee_name not in ALLOC_FNS:
                unresolved = True
            calls.append({
                "caller_usr": caller_usr,
                "caller_name": caller_name,
                "callee_usr": callee_usr,
                "callee_name": callee_name,
                "resolved": resolved,
                "location": _loc(lib, cursor),
            })
            if callee_name in ALLOC_FNS:
                news.append({"name": callee_name, "location": _loc(lib, cursor)})
        return CHILD_RECURSE

    lib.clang_visitChildren.argtypes = [CXCursor, type(visitor), c_void_p]
    lib.clang_visitChildren(lib.clang_getTranslationUnitCursor(tu), visitor, None)
    return {
        "functions": functions,
        "calls": calls,
        "new_exprs": news,
        "delete_exprs": deletes,
        "virtual_methods": virtuals,
        "exceptions": exceptions,
        "unresolved_calls": unresolved,
    }


def contract_from_ir(ir, top_function):
    """Build a contract from a public testbench IR. top_function must come from task.json."""
    if not top_function:
        return None
    declared = [f for f in ir["functions"] if f["name"] == top_function and not f["is_definition"]]
    defined = [f for f in ir["functions"] if f["name"] == top_function and f["is_definition"]]
    if declared:
        chosen, origin = declared[0], "public_testbench_declaration"
    elif defined:
        chosen, origin = defined[0], "public_testbench_definition"
    else:
        return None
    return {
        "name": top_function,
        "name_source": "public_task_json_top_function",
        "signature_source": origin,
        "return_type": chosen["return_type"],
        "params": chosen["params"],
        "location": chosen["location"],
    }


def apply_rules(ir, contract, top_function):
    started = time.monotonic()
    findings = []

    defs = [f for f in ir["functions"] if f["name"] == top_function and f["is_definition"]]
    if not defs:
        findings.append(Finding("R01_TOP_INTERFACE", FAIL, None,
                                "Candidate has no definition of top function " + top_function))
    elif contract is None:
        findings.append(Finding("R01_TOP_INTERFACE", UNKNOWN, defs[0]["location"],
                                "Testbench contract could not be recovered from AST"))
    else:
        hit = None
        for item in defs:
            if len(item["params"]) != len(contract["params"]):
                continue
            if item["return_type"] != contract["return_type"]:
                continue
            if all(a["type"] == b["type"] for a, b in zip(item["params"], contract["params"])):
                hit = item
                break
        if hit is None:
            findings.append(Finding("R01_TOP_INTERFACE", FAIL, defs[0]["location"],
                                    "Top function signature does not match the testbench declaration"))
        else:
            findings.append(Finding("R01_TOP_INTERFACE", PASS, hit["location"],
                                    "Top function signature matches the testbench declaration"))

    disabled = []
    for item in ir["new_exprs"]:
        disabled.append(("dynamic allocation", item.get("location")))
    for item in ir["delete_exprs"]:
        disabled.append(("dynamic deallocation", item.get("location")))
    for item in ir["virtual_methods"]:
        disabled.append(("virtual method " + item["name"], item.get("location")))
    for item in ir["exceptions"]:
        disabled.append(("exception construct", item.get("location")))
    if disabled:
        kind, location = disabled[0]
        extra = "; ".join(item[0] for item in disabled[:6])
        findings.append(Finding(
            "R02_DISABLED_CONSTRUCT", UNKNOWN, location,
            "Observed " + extra + "; not a hard reject until confirmed against scheme A on this toolchain",
        ))
    else:
        findings.append(Finding("R02_DISABLED_CONSTRUCT", PASS, None,
                                "No new/delete/virtual/exception construct recorded in the main file"))

    names = {f["usr"] or f["name"] for f in ir["functions"] if f["is_definition"]}
    graph = {node: set() for node in names}
    for call in ir["calls"]:
        src = call["caller_usr"]
        dst = call["callee_usr"] or call["callee_name"]
        if src in graph and dst in graph:
            graph[src].add(dst)
    cycle = None
    visiting, seen = set(), set()

    def dfs(node, stack):
        nonlocal cycle
        if cycle or node in seen:
            return
        if node in visiting:
            cycle = stack[stack.index(node):] + [node]
            return
        visiting.add(node)
        for nxt in graph.get(node, ()):
            dfs(nxt, stack + [node])
        visiting.remove(node)
        seen.add(node)

    for node in graph:
        dfs(node, [])
    if cycle:
        findings.append(Finding("R03_RUNTIME_RECURSION", FAIL, None,
                                "Call-graph cycle among candidate functions: " + " -> ".join(cycle)))
    elif ir["unresolved_calls"]:
        findings.append(Finding("R03_RUNTIME_RECURSION", UNKNOWN, None,
                                "Call graph is incomplete (unresolved callees); recursion not confirmed"))
    else:
        findings.append(Finding("R03_RUNTIME_RECURSION", PASS, None,
                                "No resolved recursion cycle in the candidate call graph"))
    elapsed = round(time.monotonic() - started, 6)
    for finding in findings:
        finding.elapsed_seconds = elapsed
    return findings


def combine(frontend, findings, rules_enabled):
    if frontend == FAIL:
        return FAIL
    if frontend == UNKNOWN:
        return UNKNOWN
    if not rules_enabled:
        return frontend
    verdicts = [f.verdict for f in findings if f.rule_id in HARD_RULES]
    if FAIL in verdicts:
        return FAIL
    if UNKNOWN in verdicts:
        return UNKNOWN
    return PASS


class Translation:
    def __init__(self, lib, index, tu):
        self.lib = lib
        self.index = index
        self.tu = tu

    def close(self):
        if self.tu:
            self.lib.clang_disposeTranslationUnit(self.tu)
            self.tu = None
        if self.index:
            self.lib.clang_disposeIndex(self.index)
            self.index = None


def parse_file(vitis_root, source, include_dirs, cxx_standard):
    lib, path = load_libclang(vitis_root)
    lib = _bind(lib)
    args = frontend_args(vitis_root, include_dirs, cxx_standard)
    argv = (c_char_p * len(args))(*[a.encode("utf-8") for a in args])
    tu = c_void_p()
    index = lib.clang_createIndex(0, 0)
    code = lib.clang_parseTranslationUnit2(
        index, str(source).encode("utf-8"), argv, len(args), None, 0, KEEP_GOING, ctypes.byref(tu)
    )
    if code != 0 or not tu:
        lib.clang_disposeIndex(index)
        raise RuntimeError("clang_parseTranslationUnit2 failed with code %s using %s" % (code, path))
    return Translation(lib, index, tu), args + [str(source)]


def recover_contract(testbench, top_function, vitis_root, include_dirs, cxx_standard="c++14"):
    """Recover the contract from public materials only.

    ``top_function`` must be ``task.json``'s ``top_function``. The signature
    comes from the public testbench AST. Reference designs are never read.
    """
    translation, _ = parse_file(vitis_root, testbench, include_dirs, cxx_standard)
    try:
        return contract_from_ir(extract_ir(translation.lib, translation.tu), top_function)
    finally:
        translation.close()


def check_source(source, testbench, top_function, vitis_root, include_dirs, cxx_standard="c++14",
                 with_rules=False, contract=None):
    """Return a CheckResult. Reads files only; does not modify them.

    Scheme C parses the candidate translation unit once and reuses that TU for
    frontend diagnostics and AST rules. The testbench contract, if not supplied,
    is a separate public-material parse and is not a second parse of the candidate.
    """
    source = Path(source)
    started = time.monotonic()
    timings = {"prepare_seconds": 0.0, "frontend_seconds": 0.0, "ast_rules_seconds": 0.0}
    if with_rules and contract is None and testbench:
        prepare_started = time.monotonic()
        try:
            contract = recover_contract(testbench, top_function, vitis_root, include_dirs, cxx_standard)
        except Exception:
            contract = None
        timings["prepare_seconds"] = round(time.monotonic() - prepare_started, 6)
    try:
        parse_started = time.monotonic()
        translation, command = parse_file(vitis_root, source, include_dirs, cxx_standard)
    except Exception as error:
        return CheckResult(UNKNOWN, "C" if with_rules else "B", UNKNOWN, [],
                           [{"text": str(error)}],
                           {"prepare_seconds": timings["prepare_seconds"],
                            "frontend_seconds": round(time.monotonic() - started, 6),
                            "ast_rules_seconds": 0.0,
                            "total_seconds": round(time.monotonic() - started, 6)},
                           note="tool_or_parse_exception")
    try:
        frontend, diags = _diagnostics(translation.lib, translation.tu)
        timings["frontend_seconds"] = round(time.monotonic() - parse_started, 6)
        findings = []
        if with_rules and frontend == PASS:
            rule_started = time.monotonic()
            findings = apply_rules(extract_ir(translation.lib, translation.tu), contract, top_function)
            timings["ast_rules_seconds"] = round(time.monotonic() - rule_started, 6)
        elif with_rules:
            findings = [Finding(rule, UNKNOWN, None, "Rules skipped because frontend was " + frontend)
                        for rule in RULES]
        status = combine(frontend, findings, with_rules)
        timings["total_seconds"] = round(time.monotonic() - started, 6)
        return CheckResult(status, "C" if with_rules else "B", frontend, findings, diags, timings, command)
    finally:
        translation.close()


def result_dict(result):
    return {
        "status": result.status,
        "scheme": result.scheme,
        "frontend": result.frontend,
        "findings": [finding.__dict__ for finding in result.findings],
        "diagnostics": result.diagnostics[:30],
        "timings": result.timings,
        "command": result.command,
        "note": result.note,
    }
