# ZComp HLS Harness — 环境适配与最小闭环验证记录

> 这是 2026-09-13 的环境与实验记录。下文配置自动覆盖和入口行为适用于当时的旧评测入口；新 Agent、严格基线及配对入口应显式传入本机配置。当前用法与输出路径见 [项目状态说明](../design/instruction.md)，历史路径和测量结果保留用于追溯。

- 日期：2026-09-13
- 仓库：`F:\Projects\ADMCmpt\zcomp`
- 结论：**本机环境可完整运行**（模型生成 → C 仿真 → HLS 综合）

---

## 1. 环境排查结果

| 项目 | 位置 / 状态 |
|---|---|
| Vitis 2025.2（完整安装） | `D:\FPGA\AMDDesignTools\2025.2\` ✅ |
| `vitis-run.bat` | `D:\FPGA\AMDDesignTools\2025.2\Vitis\bin\vitis-run.bat` ✅ |
| `vivado.bat` | `D:\FPGA\AMDDesignTools\2025.2\Vivado\bin\vivado.bat` ✅ |
| 空壳目录（不可用） | `D:\FPGA\Xilinx\2025.2\Vitis\bin`（无任何可执行文件）⚠️ |
| 旧 Vivado 2018.3（Vivado HLS，非 Vitis HLS） | `D:\FPGA\Xilinx\Vivado\2018.3` ⚠️ |
| 许可证（trial） | `C:\Users\24229\AppData\Roaming\XilinxLicense\trial.lic`，覆盖 `Synthesis` + `ap_cc`，**到期 2026-10-13** |
| Python | 3.14.7（框架仅依赖标准库，兼容） |
| 模型服务 | `https://unpaired-valid-crouch.ngrok-free.dev/v1`，模型 `qwen38`（自建 ngrok + vLLM） |

---

## 2. 配置管理：每台机器一份本地覆盖文件

`serve/runtime.json` 里的 Vitis 路径与许可证路径是机器相关的，但该文件被 git 跟踪。若每个人都直接改它，必然产生路径互相覆盖和合并冲突。

采用**本地覆盖文件**方案（不改任何框架代码）：

| 文件 | 是否提交 | 作用 |
|---|---|---|
| `serve/runtime.json` | ✅ 已提交 | 仓库原始默认值，**任何人都不再修改** |
| `serve/runtime.local.json.example` | ✅ 新增提交 | 模板，供队友复制填写 |
| `serve/runtime.local.json` | ❌ 已加入 `.gitignore` | 各人自己的真实路径，不外泄、不冲突 |

`.gitignore` 新增一行（精确路径，因此 `.example` 模板不受影响）：

```gitignore
serve/runtime.local.json
```

本机 `serve/runtime.local.json` 中的实际取值：

| 字段 | 取值 |
|---|---|
| `vitis_root` | `D:/FPGA/AMDDesignTools/2025.2/Vitis` |
| `vivado_root` | `D:/FPGA/AMDDesignTools/2025.2/Vivado` |
| `license_file` | `C:/Users/24229/AppData/Roaming/XilinxLicense/trial.lic` |

模型段（`model`）无需任何改动：`qwen38` 与 `/v1/models` 返回一致，`enable_thinking: false` 时干净输出纯代码，无需 API key。

队友接入方式：复制 `runtime.local.json.example` 为 `runtime.local.json`，填入自己的路径即可，**命令无需任何额外参数**（见下）。

### 自动优先机制

为避免每次都要手敲 `--config`，对框架做了最小改动，让**本地覆盖文件存在时自动优先使用**：

| 文件 | 改动 |
|---|---|
| `serve/inference.py` | `load_config()` 在未显式传参时，优先读取 `serve/runtime.local.json`，不存在才回退到 `serve/runtime.json` |
| `evaluation/batch.py` | `--config` 默认值由硬编码 `serve/runtime.json` 改为不传（否则会绕过上面的逻辑），仅在显式指定时才向下传递 |
| `tools/check_endpoint.py` | 直接读文件的地方同样改为优先本地覆盖文件 |

解析优先级：**显式 `--config` > `serve/runtime.local.json` > `serve/runtime.json`**

因此日常命令保持原样即可：

```powershell
.\run_eval.ps1 <task>\task.json --cpu-only
.\run_batch.ps1 <task-root> --cpu-only
```

---

## 3. 验证一：已有代码验证（validate_existing，不碰模型）

- 运行：`run_eval.ps1 output/smoke/task.json --source output/smoke/kernel.cpp --cpu-only`
- 结果目录：`output/runs/20260913T063846820443Z-ff21943d/`
- 状态：**passed**，耗时 95.3 s

| 检查 | 结果 |
|---|---|
| parse / compile / run（C 仿真） | ✅ passed |
| synthesize（HLS 综合） | ✅ passed |

综合资源估计：`clock 1.603 ns`、latency 6 周期、LUT 97、FF 8、DSP 0、BRAM 0。

---

## 4. 验证二：最小闭环（模型生成 → C 仿真 → 综合）

- 运行：`run_eval.ps1 output/smoke/task.json --cpu-only`（不带 `--source`，模型生成代码）
- 结果目录：`output/runs/20260913T064434369830Z-b3420a36/`
- 状态：**passed**，模式 `baseline_and_validate`，耗时 44.4 s

| 环节 | 结果 |
|---|---|
| 模型生成（`qwen38`） | ✅ passed，1 次请求，`finish_reason=stop` |
| C 仿真 | ✅ passed |
| HLS 综合 | ✅ passed |

- 代码提取：`single_outer_fence_removed`（模型输出 ```cpp 围栏，框架正确剥离）
- 模型生成的代码：

```cpp
void vecadd(const int a[4], const int b[4], int c[4]) {
    for (int i = 0; i < 4; i++) {
        c[i] = a[i] + b[i];
    }
}
```

- 综合资源估计：`clock 1.603 ns`、latency 6 周期、LUT 97、FF 8、DSP 0、BRAM 0。

---

## 5. 验证三：配置覆盖路径可用性

改用本地覆盖文件后，需确认 `--config` 能真正驱动工具链。三组对照：

| 场景 | 命令 | 结果 |
|---|---|---|
| A 不带 `--config` | `run_eval.ps1 output/smoke/task.json --cpu-only` | ❌ `environment_error`（仓库默认的 `E:/2025.2` 本机不存在；**在调用模型前即失败**，不浪费请求） |
| B 带 `--config`，但模型服务已关闭 | 加 `--config serve/runtime.local.json` | ❌ `api_http_error` HTTP 404（ngrok `ERR_NGROK_3200: endpoint is offline`）；**环境问题，与配置无关** |
| B' 带 `--config` + `--source`（不调用模型） | 加 `--config` 与 `--source` | ✅ **passed**，csim + synthesis 全过 |

B' 结果目录：`output/runs/20260913T070342161102Z-318cf723/`

| 检查 | 结果 |
|---|---|
| parse / compile / run（C 仿真） | ✅ passed |
| synthesize（HLS 综合） | ✅ passed |

综合资源估计：`clock 1.603 ns`、latency 6 周期、LUT 97、FF 8、DSP 0、BRAM 0。

该运行存档的 `config.json` 中 `vitis_root` 为 `D:/FPGA/AMDDesignTools/2025.2/Vitis`、`license_file` 指向 `trial.lic`，**证明生效的确实是 `runtime.local.json`**，而非仓库默认值。

结论：`--config` 重定向路径完全可用；模型生成段已在第 4 节验证通过（当时服务在线），整链路无阻塞。

### 自动优先机制验证

加入自动优先逻辑后复测，**命令均不带 `--config`**：

| 入口 | 命令 | 结果 |
|---|---|---|
| `run_eval` | `run_eval.ps1 output/smoke/task.json --source output/smoke/kernel.cpp --cpu-only` | ✅ **passed**（csim + synthesis 全过） |
| `run_batch` | `run_batch.ps1 output/smoke --cpu-only` | ⚠️ `api_http_error`（模型服务已关闭），**但报的是模型错误而非 `environment_error`，恰好证明环境检查已通过、配置解析正确** |

解析优先级亦单独验证：无参数时取到 `D:/FPGA/AMDDesignTools/2025.2/Vitis`（本地文件），显式传 `serve/runtime.json` 时取到 `E:/2025.2/Vitis`（仓库默认），显式指定仍然优先。

---

**结论**：框架在本机环境完整可用——Vitis 2025.2 通过本地覆盖文件 `serve/runtime.local.json` 适配（仓库 `runtime.json` 保持原样不动），模型生成 → C 仿真 → HLS 综合全链路跑通，许可证正常。
