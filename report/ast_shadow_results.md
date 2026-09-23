# AST 旁路实验结果（2026-09-19）

状态：远程串行 A/B/C 已完成 319/319。不修改 baseline 协议，不开展闭环。

## 一页结论

| 问题 | 答案 | 口径 |
|---|---|---|
| 有没有误杀？ | **没有** | C FAIL ∩ A PASS = 0；B FAIL ∩ A PASS = 0（含留出集） |
| 提前编译（B）值得进主流程吗？ | **旁路可留，闭环不准入** | 留出集 26 条真拦截，全是 A 也会挂的编译失败；B 全量成本 206s，跳过 A 只省 171s |
| AST 硬规则（C 相对 B）有额外收益吗？ | **有 6 条，全是 R01** | 留出集 Prob078/110/166 的 agent+baseline；4 条 A 为 compile_error，2 条为 functional_or_runtime_error |
| 是否接入主流程？ | **否** | 无误杀，但检查成本高于可确认节省；UNKNOWN 不触发跳过 |

## 数据

- 冻结清单：`output/static_shadow/20260919T105419719926Z/`
- 候选：319（agent 170 + baseline 149）；开发 47，留出 272
- 远程原始结果：`/home/dingjy/sxt/zcomp-ast-shadow/pack/raw_results.json`（已同步到冻结目录）
- 中途一次在 243 条因进程内 `LD_LIBRARY_PATH` 膨胀触发 `Argument list too long`，去重后续跑至 319

## 全量对照

| 方案 | PASS | FAIL | UNKNOWN |
|---|---:|---:|---:|
| A | 182 | 137 | 0 |
| B | 281 | 30 | 8 |
| C | 198 | 36 | 85 |

C 的 36 条 FAIL：30 条与 B 相同（前端编译失败），6 条仅 C 命中（R01 接口不一致）。A 后来这 36 条全部 FAIL。

C 的 85 条 UNKNOWN：R03 调用图不完整 85；另有 R02 观察 11、R01 契约不清 8（可并存）。其中 49 条 A 为 PASS，按设计不能当拦截。

A FAIL 且 C PASS：65 条，主因是功能/运行时错误（60），AST 本来就看不见。

## 留出集（准入用）

| 项 | 值 |
|---|---|
| C FAIL ∩ A PASS | 0 |
| C FAIL ∩ A FAIL | 32 |
| 其中仅 C、非 B | 6（R01） |
| B FAIL ∩ A PASS | 0 |
| C UNKNOWN | 70（39 条 A PASS） |
| A 墙钟合计 | 4011.9 s |
| 若 C FAIL 跳过 A | 省 184.0 s |
| C 全量成本 | 249.8 s |
| 若 B FAIL 跳过 A | 省 146.2 s |
| B 全量成本 | 172.1 s |

净节省（只计已确认真拦截，UNKNOWN 不计）：C 为 184 − 250 < 0；B 为 146 − 172 < 0。

## 闭环

不满足「可覆盖检查成本的已确认节省」。停止扩展，不按留出结果改规则，不开展闭环。
