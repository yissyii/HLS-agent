# 项目结构思维导图

核对日期：2026-09-18。以 `F:/Projects/ADMCmpt` 为工作区根目录，`zcomp/` 为 Git 仓库及框架根目录。图中按职责组织真实路径；省略包标记、缓存、编辑器配置和临时目录。

Obsidian 可编辑版本：[项目结构 Canvas](project-structure.canvas)。画布文件链接以工作区 `ADMCmpt/` 为 Obsidian vault 根目录。

```mermaid
mindmap
  root((ADMCmpt))
    docs 资料与阅读
      ReadReference 论文共读
      SAGE-HLS-References 文献与下载清单
      AMD讲座_整理笔记.md
      比赛指南与 UG1399 手册
    zcomp HLS 框架
      运行入口
        run.sh Agent
        run_baseline.sh 裸基线
        run_paired.sh 同配置配对
        run_*.ps1 Windows 入口
      agent 决策与修复
        interface 输入适配
        core 控制器与停止策略
        context 上下文与规则选择
        feedback 错误分类
        candidates 去重与候选选择
        artifacts 证据写入
        config 策略参数
      serve 模型调用
        inference.py HTTP 推理
        agent_model.py 限时调用
        code.py 源码提取
        baseline_entry.py 与 paired_entry.py
      evaluation 验证执行
        task_io.py 公开任务材料
        validator.py 与 hls.py 仿真及综合
        diagnostics.py 结构化诊断提取
        single_task.py 与 batch.py 兼容及批量入口
        lifecycle.py 研发评测接入
      local_eval 研发会话
        guard.py 配置冻结与请求观察
        retry.py 整轮作废及重跑
      知识与模型
        skill 可复用规则内容
        rag 手册建库与混合检索
          README.md 与 TASKS.md
          reports 原始 JSON 证据
          默认尚未接入 Agent
        model 模型声明与本地权重位置
      report 归档报告
        README.md 总索引
        design 设计与图解
        reproducibility 复现说明
        model_selection 检索方案依据
        evaluations 四轮对照报告
      工程支撑
        tools 评测与合同检查脚本
        data 数据清单与本地题集
        src 源码布局说明
        sim 仿真约定
        build 与 board 构建及板级约定
        Dockerfile 容器契约占位
      output 本地运行产物
        会话与有效轮索引
        请求与候选代码
        日志与验证证据
```

`src/` 是源码布局说明，当前 Python 实现分布在 `agent/`、`serve/`、`evaluation/` 等目录。`agent/artifacts/` 和 `agent/candidates/` 保存管理代码，实际运行数据写入输出目录。

`skill/` 的规则内容由 `agent/context/skills.py` 读取，默认关闭。`rag/` 提供独立的手册检索能力，当前并未接入 Agent 默认修复链路。`build/`、`board/`、`sim/` 主要说明约定，不能据此认为已有完整构建、板级部署或离线容器。

本地题集、权重、语料、向量与运行产物按 Git 忽略规则保存；图中出现目录不代表其内容应提交。显式输出路径也可以由用户指定。原 `record/` 和 `zcomp/record/` 中的报告已集中迁入 `report/`，不再作为报告归档入口。

继续阅读：[Agent 入口与模块流程](agent_flow.md) · [报告总索引](../README.md) · [项目说明](../../README.md)。
