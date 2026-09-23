# AST 规则卡片（第一版）

规则在合成 IR 上验证。不读取参考实现。留出集结果不得用来改规则或把观察项升级为硬拦截。

## 契约来源（R01 唯一输入）

1. **名字：** 公开 `task.json` 的 `top_function`（Bench4HLS 为 `TopModule`）。
2. **签名：** 同一任务公开 `tb.cpp` 里该名字的 FunctionDecl，优先声明，否则定义。
3. **不用：** `problem.txt` 正则、`reference_design`、模型输出、历史日志里的报错文本。

测试台解析失败 → R01 为 UNKNOWN，不猜签名。

## R01_TOP_INTERFACE — 硬规则

- **做：** 候选主文件必须有同名定义，参数个数、类型拼写、返回类型与上述契约一致。
- **正例：** `void TopModule(ap_uint<1>& zero) { zero = 0; }`
- **反例：** 无 `TopModule`，或 `void TopModule(int x)` 对 `ap_uint<4>` 声明。
- **本版：** 保留硬拦截。

## R02_DISABLED_CONSTRUCT — 观察项，不硬拦截

- **做：** 记录主文件中的 new/delete/malloc 族、虚函数、try/throw。
- **不做：** 一律判 FAIL。本工具链尚未用方案 A 确认这些构造必然失败。
- **检出：** UNKNOWN 观察记录；**不进入** C 的总体 FAIL/UNKNOWN。
- **未检出：** PASS。
- **本版：** 不启用为硬规则。不得为过门槛在留出集上改成硬 FAIL。

## R03_RUNTIME_RECURSION — 硬规则

- **做：** 主文件已解析函数的调用图环 → FAIL。
- **看不清：** 函数指针、未解析 callee、跨 TU → UNKNOWN，不判 FAIL。
- **本版：** 保留硬拦截。

## C 的总体判定

硬规则只有 R01、R03。前端 FAIL 则 C 为 FAIL；前端 UNKNOWN 则 C 为 UNKNOWN。  
R02 不影响「拒绝 / 不拒绝」：UNKNOWN 观察不拦截，后续仍走 A。
