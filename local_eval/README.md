# 永久规则：请求级基础设施重试（2026-09-23）

所有通过 evaluation.lifecycle 进入的研发评测默认采用本规则，替代整轮作废重跑。

1. 成功任务、已生成候选和已完成验证保留。
2. 网络断开、外部 HTTP 故障（含鉴权、限流、离线端点）、TLS 校验失败、服务无效响应，重试当前逻辑请求。TLS 校验始终保持，重试不会绕过证书校验。
3. 重试使用原提示词、原配置，不额外消耗一次代码修复机会，不根据答案质量挑选响应。
4. 完整生成后的格式错误、编译、功能、综合失败，以及生成截断，不以网络名义重试。
5. 生成工作进程或工具超时不能仅凭名称判定为外部故障，保留证据并等待归因；不计入能力失败率，亦不能宣称评测已完整结束。
6. 请求重试仍耗用原请求和任务时间预算。预算或重试上限耗尽后保留未完成记录，其余任务继续；不会整批重跑。

## 配置和审计

retry.json 的旧键 max_restarts 为兼容历史会话保留，现表示每个请求最多额外重试次数（默认 2），不再表示整批重跑次数。退避默认 5、10 秒，上限 30 秒。
retry_http_statuses 为历史兼容字段；外部 HTTP 服务错误按类别统一处理。400 等请求/上下文错误不自动重新采样。
retry_generation_timeout 为历史兼容字段；工作进程超时不会重启整批。

response.txt.transport/attempt_NNN.txt.meta.json 保存各次传输证据；response.txt.meta.json 保存累计实际请求数、transport_retries 和引用。断线后的服务端执行结果可能未知，会保留 request_outcome_unknown。
scope 内 requests/ 存逻辑请求的累计回执；unresolved_network/ 存重试仍未恢复的外部故障。
session.json 保留同一 attempt。completed_with_network_failures 表示仍有外部故障，已成功结果仍可用，但不是完整实验结论。

## 统计

评测通过率必须同时报告有效样本数、未完成/待归因数量。外部故障不进入能力失败分母；不能因排除它们而声称全量通过率。
tools/summarize_transport_eval.py 可只读计算历史或当前 summary.json 的新口径，并报告双方均有有效结果的同题对照。
原始回执保持原样；不同重试规则的实验需说明口径变化。

## 边界与验证

移除 local_eval/ 后 evaluation.lifecycle 回到单次调用；直接调用无 scope 的底层生成函数仍为单请求。
python -B -m unittest local_eval.test_lifecycle 验证回退、失败请求重试、成功分支保留、非网络失败不重试、外部故障单列。
本规则不改反馈策略、修复模板、RAG、模型配置或 Vitis 配置。
