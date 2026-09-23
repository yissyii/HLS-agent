# 自建测试员：开发／留出评测协议

本协议用于独立模块 `agent/selftest` 的小规模真实模型评测，不改主流程。

## 题集与信息边界

`tools/prepare_selftest_study.py` 生成 5 道既有开发题和 5 道新功能留出题。
开发题为截断加法、有符号比较、选择器、常量异或、饱和加法。
留出题为置位计数、前导零计数、位反转、BCD 解码、Gray 解码，不把开发题换位宽充当新题。
这只是原创标量题的功能族划分；不能证明模型训练时没见过类似算法，也不构成官方比赛评测。

生成程序只读取 public_manifest.json、公开题面和接口，不读取 private 实现或 gold_suites。
原作者仍知道两组题，需要其他队员独立复核题意和对照实现，才能进一步提高基准可信度。
一旦看过留出结果并用于改进，就不再把这批题作为下一版的新鲜留出集。

## 冻结与顺序

1. 先核验原创样例：人工计划下正确实现通过、每个 mutant 确有可观测不匹配。
2. `freeze` 保存数据 ID、私有样例清单哈希、代码与提示词哈希、模型配置、验证配置和预算。
3. 对开发集每题最多两次请求：一次规则、一次计划。每题 120 秒、4096 个用例、无自动重试、仅一次采样。
4. 用冻结测试台执行正确／错误实现。没有测试包的生成失败题仍保留在总题数中。
5. 只有存在同一协议的完整开发集评测报告，才能解锁 holdout 生成。
6. 留出阶段使用完全相同的代码、提示词和预算。检测到代码或数据变更则中断，而不是混合不同版本的结果。
7. 保存所有结果，不现场修正失败响应、不选择性补跑。环境问题单列，不计为功能检出。

协议哈希用于防止意外变更，不是对恶意本地篡改的安全防护。CLI 也不提供对数据作者的保密隔离。

## 命令

```powershell
python -B tools/prepare_selftest_study.py output/study_data
python -B tools/run_selftest_study.py freeze output/study_data serve/runtime.json output/study_protocol --backend vitis --evaluation-config agent/selftest/config/runtime.remote-study.example.json
python -B tools/run_selftest_study.py generate output/study_data output/study_protocol/protocol.json output/study_dev --split development
```

将相同版本代码、study_data、study_dev 拷贝到独立工具机目录。不要覆盖正式实验目录。
验证配置中的工具、许可证、器件路径必须在工具机有效。源文件字节必须一致，以便跨平台校验冻结哈希。

```bash
python3 -B tools/run_selftest_study.py evaluate output/study_data output/study_dev output/study_dev_eval --allow-execution
```

取回 `study_result.json` 后再解锁：

```powershell
python -B tools/run_selftest_study.py generate output/study_data output/study_protocol/protocol.json output/study_holdout --split holdout --development-report output/study_dev_eval/study_result.json
```

将 holdout 生成结果传到同一工具机，以相同 evaluate 命令评测。每次输出目录必须是新目录。
默认 native 后端只作本地程序检查；选择 Vitis 后端则使用冻结的验证配置，不因运行机器切换而偷换工具条件。

### 环境修正与并行开发

若只是许可证路径选错，使用 `tools/amend_selftest_license.py <dataset> <generation> <new-output> --license-file <absolute-path>`。
此命令只修改验证配置中的 license_file，保留旧协议和生成摘要，产生有父协议 ID 的新协议；逐个校验复制后的 suite_id，不增加模型调用。
旧许可下的环境失败不得删除，也不能将更正后的通过结果伪装成首次环境就正常。

若本地代码在实验中被其他工作修改，不要回滚用户改动或放宽哈希检查。使用事先归档的相同版本副本继续，记录实际执行主机。
2026-09-23 试验中开发集生成在 Windows，留出生成转移到远程冻结副本；模型服务与提示词不变，但主机切换应在报告中披露。

## 指标口径

- 生成成功率：有效测试包数／全部题数。格式、表达式、预算失败均保留。
- 正确对照通过率（全题分母）：正确对照通过题数／全部题数。不能将没有生成测试的题剔除后声称整体可靠。
- 误报率：错误拒绝正确实现的题数／完成了功能测试的正确对照数。同时列出没有生成测试、构建失败、环境错误等无法判定数。
- 检出率：仅在正确对照通过的题中计算；功能不匹配的 mutant 数／这些题的全部 mutant 数。
- 编译失败、环境异常、超时不算功能检出；单独记录无法判定数。
- 报告真实模型请求数、生成时间、配置、协议 ID、后端，并列出每题原始状态。

该协议不测综合、RTL 协同仿真或性能。即使小题正确对照通过，也不表示生成规则获得了形式证明。

## 已实现的评测程序检查

`python -B -m unittest tools.test_selftest_study` 检查分组、冻结变更检测、留出解锁、生成阶段不读取私有实现、全失败题保留和分母计算。
测试使用模拟模型是为了校验评测程序；实际 study 的 generate 使用真实模型，两者证据不能混写。
