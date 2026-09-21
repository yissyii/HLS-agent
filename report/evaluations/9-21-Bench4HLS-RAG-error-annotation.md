# Bench4HLS RAG 错误标注表（2026-09-21 · 基于 9-20 重排测评）

## 用途与口径

本表是对 `9-20-Bench4HLS-RAG-rerank.md` 中「编译错误抽查」的完整化：把那一次测评里**所有实际触发了检索并返回候选章节的诊断**逐条标注，为后续「错误签名精确匹配」改进提供 ground truth。

**数据来源**：`output/bench4hls_rag/run01/hybrid_rerank/*/result.json`（`rag_history` + `checks`）+ `.../candidates/*/retrieval_output.json`（`hits` 的 title/section/text）+ `rag/corpora/ug1399-2026.1-en-curated/records.jsonl`（核对正确章节页号）。

**为何是 22 条而非建议的 30～50**：全量 170 题中，真正产生「检索命中」的诊断只有 22 条（按去重后的诊断计）：
- `injected` 18 条（16 题，Prob079/Prob129 各 2 条；Prob129 两条同诊断去重后算 1）
- `retrieved`（检索到但未注入）2 条
- `no_reference_injected`（检索到候选但判定无可用引用）3 条

其余 71 条 rag_history 为 `skipped`（低信息门跳过，query 里没有 `error:` 行，无可标注的 diagnostic），另有 3 题（Prob078/110/112）compile 失败但反馈只给了 `compile_error` 标签、无具体错误文本，同样不可标注。因此本表是**全量覆盖**而非抽样，22 行已是该 run 的完整标注集。

## 列语义

| 列 | 含义 |
|---|---|
| diagnostic | 反馈里的首条 `error:` 行（+ 触发该错误的关键代码） |
| 真正错误类别 | 代码真实缺陷的类型（人工判定） |
| 正确章节 | UG1399 v2026.1 中真正能解决该错误的小节（无则为「无」） |
| 检索/注入章节 | RAG 实际返回/注入的小节（即「错误章节」候选） |
| 足够官方证据 | UG1399 是否存在足够证据支撑修复（是 / 边缘 / 否） |
| 应 abstain | RAG 是否应放弃注入（是=当前候选无用或误导；否=内容命中） |

> 「应 abstain = 否」的分支说明：命中正确章节但题目仍失败的（如 Prob142）不记为检索之过；「命中正确章节却弃注入」的（Prob083）记为漏检（false-negative）。

## 标注表

| # | Prob | diagnostic | 真正错误类别 | 正确章节 | 检索/注入章节（错误章节） | 足够证据 | 应 abstain |
|---|---|---|---|---|---|---|---|
| 1 | Prob016 | `no matching function for call to object of type 'ap_uint<4>'`（`x(0)`） | ap_uint 位选写成函数调用 | Ch.21 §Bit Selection `operator[](int)`（p692） | Bit-Width Propagation（p149）+ Dealing with Unsupported Functions（p134，ap_float） | 是 | 是 |
| 2 | Prob032 | `no matching function ... 'ap_int<8>'`（`a(7)`） | ap_int 位选写成函数调用 | Ch.21 §Bit Selection `operator[]`（p692） | Other Class Methods（p696，set/clear/invert）+ Bit-Width Propagation（p149） | 是 | 是 |
| 3 | Prob041 | `no matching function ... 'ap_uint<25>'`（`result(idx)`） | ap_uint 位选/范围选语法 | p692 `operator[]` / `(hi,lo)`（p692-693） | Other Class Methods（p693，含 `Val1(3,0)=Val2(3,0)`） | 是 | 否 |
| 4 | Prob045 | `unexpected interface offset value '0x0', expects '[slave, direct, off]'` | `#pragma HLS INTERFACE offset=` 用了非法十六进制值 | Ch.8 Interfaces §Offset and Modes of Operation（p175） | Offset and Modes of Operation（p175） | 是 | 否 |
| 5 | Prob065 | `conditional expression is ambiguous; 'int' ... ap_int<33>`（`(ones==9)?0:(ones+1)`） | 三元运算符两端类型不匹配，需显式 cast | Ch.6 §Expressions Involving ap_[u]<> types（p683） | C++ Arbitrary Precision Integer Types: Reference Information（p679，总览） | 边缘 | 是 |
| 6 | Prob071 | `use of undeclared identifier 'reset'`（另有 `clk`） | 变量/端口未声明（纯 C++ 作用域） | 无 | Reset pragma（p594） | 否 | 是 |
| 7 | Prob072 | `invalid digit 'b' in decimal constant`（`2'b01`） | Verilog 字面量误入 C++（应为 `0b01` 或字符串构造） | 边缘（p680-681 literal/radix，未直接讲 `2'b` 非法） | Initialization and Assignment from Constants（p681） | 边缘 | 是 |
| 8 | Prob079-a | `no matching function ... 'ap_uint<32>'`（`feedback = q(0)`） | ap_uint 位选写成函数调用 | Ch.21 §Bit Selection `operator[]`（p692） | Other Class Methods（p693，范围选） | 是 | 是 |
| 9 | Prob079-b | `invalid suffix 'h1' on integer constant`（`q = 32'h1`） | Verilog 十六进制字面量 `32'h1` 非法 | 边缘（p680-681 literal） | C++ Arbitrary Precision Integer Types: Reference Info（p679）+ ap_[u]fixed Representation（p703） | 边缘 | 是 |
| 10 | Prob081 | `excess elements in array initializer`（`bool Q[8]={9 个 false}`） | 数组初始化元素数超数组大小（纯 C++） | 无 | Implementing ROMs（p097） | 否 | 是 |
| 11 | Prob090 | `no matching function ... 'ap_uint<4>'`（`mux_in(0)`） | ap_uint 位选写成函数调用 | p692 `operator[]` | Other Class Methods（p693，范围选） | 是 | 否 |
| 12 | Prob101 | `invalid digit 'b' in decimal constant`（`2'b01`） | Verilog 字面量误入 C++ | 边缘（p680-681） | Initialization and Assignment from Constants（p681） | 边缘 | 是 |
| 13 | Prob104 | `'hls.h' file not found`（`#include <hls.h>`） | 错误头文件名（应 `ap_int.h`/`hls_stream.h`） | Ch.6/Ch.21 头文件列表（p680 `#include "ap_int.h"`） | Refactoring C++ Source Code for HLS（p021） | 是 | 是 |
| 14 | Prob120 | `no matching function ... 'ap_uint<512>'`（`q(i)`） | ap_uint 位选写成函数调用 | p692 `operator[]` | Class Methods and Operators（p689，移位运算符） | 是 | 是 |
| 15 | Prob129 | `no matching function ... 'ap_uint<3>'`（`y(0)`） | ap_uint 位选写成函数调用 | p692 `operator[]` | Fixed-Point Math Functions（p732） | 是 | 是 |
| 16 | Prob130 | `no matching function ... 'ap_uint<3>'`（`y(0)`） | ap_uint 位选写成函数调用 | p692 `operator[]` | Fixed-Point Math Functions（p732） | 是 | 是 |
| 17 | Prob142 | `no matching function ... 'ap_uint<3>'`（`r(0)`） | ap_uint 位选写成函数调用 | p692 `operator[]` | Other Class Methods（p693，范围选） | 是 | 否 |
| 18 | Prob147 | `redefinition of 'pht'` / `redefinition of 'TopModule'` | 重复定义/作用域冲突（Bench4HLS 拼接所致） | 无 | FIR Static Parameters（p810，rerank −4.9）+ syn.directive.reset（p525）〔未注入〕 | 否 | 是 |
| 19 | Prob166 | `redefinition of 'W' ... WORD[80] vs WORD[16]` | 重复定义且类型不同 | 无 | Unions（p123）〔未注入〕 | 否 | 是 |
| 20 | Prob083 | `no matching function ... 'ap_uint<5>'`（`q(0)`） | ap_uint 位选写成函数调用 | p692 `operator[]` | Other Class Methods（p693，范围选）〔检索到但弃注入〕 | 是 | 否 |
| 21 | Prob146 | `use of undeclared identifier 'next_state'` | 变量未声明 | 无 | （检索空，无候选） | 否 | 是 |
| 22 | Prob152 | `expected ';' after expression`（+ undeclared `The`/`new_d`） | 语法错误（缺分号 / 说明文字未加注释） | 无 | FIR Filter IP Library（p807）〔检索到但弃注入〕 | 否 | 是 |

## 统计

| 判定 | 数量 | 条目 |
|---|---|---:|---|
| 命中正确章节（真命中） | 4 | Prob041、Prob045、Prob090、Prob142 |
| 词面/主题假匹配（检索到错误章节） | 16 | Prob016、032、065、071、072、079-a/b、081、101、104、120、129、130、147、166、152 |
| 命中正确章节却弃注入（漏检，false-negative） | 1 | Prob083 |
| 正确弃注入（无可注入内容） | 1 | Prob146 |

| 官方证据充分度 | 数量 |
|---|---:|
| 是（UG1399 有直接证据） | 12 |
| 边缘（相关但不直接） | 4 |
| 否（非 UG1399 范围，纯 C++ 语法） | 6 |

| 应 abstain | 数量 |
|---|---:|
| 是（应放弃注入） | 17 |
| 否（应注入） | 5 |

## 结论

1. **命中率 4/22 ≈ 18%**：只有 interface offset（Prob045）、ap_uint 位选（Prob041/090/142）这类 `error:` 里带手册原文/强类型签名的诊断能被精确命中；其余 18 条是关键词假匹配或正确弃注入。
2. **ap_uint 位选是最大的高频坑**：`no matching function for call to object of type 'ap_uint<N>'` 出现 9 次，正确章节统一是 **Ch.21 §Bit Selection `operator[](int)`（p692）**；但检索把其中 6 次带到了 p134/p149/p689/p696/p732 等无关小节，仅有 3 次落到相邻的 p693（范围选 `(hi,lo)` 示例，基本正确）。这说明**用「ap_uint 位选语法」做签名精确匹配即可稳定命中 p692**，而不是靠语义 embedding 泛匹配。
3. **Verilog 字面量（`2'b`/`32'h`）是第二高频坑**（Prob072/079/101）：UG1399 没有「Verilog 字面量在 C++ 非法」的章节，p680-681 只讲字符串构造 radix，属「边缘相关」，此类应 abstain 或改用独立校验规则。
4. **纯 C++ 语法错误（undeclared / redefinition / excess initializer / 缺分号）共 6 条**：UG1399 作为 API 参考手册根本不覆盖，检索必然误导，**这些应一律 abstain**。
5. **漏检 1 条（Prob083）**：检索其实命中了接近正确的 p693，却被 `no_reference_injected` 放弃，属 false-negative——说明「是否注入」的门槛除了词面匹配，还应参考「命中章节是否落在 ap_int 参考小节」。

**改进建议（与 9-20 报告一致）**：从「整段手册泛匹配 + 重排」转向**错误签名精确匹配**——对 `HLS 207-XXXX` 错误码、`error:` 后的模板名（`ap_uint<N>`/`ap_int<N>` 位选、`offset=`、`#include <...>`）做查表式映射到精确页码，命中不了就 abstain。
