# 提交说明（Submission README）

**挑战**：C2 — AI for Math 论文（`ch-20260717031343-8ot0ji`）
**论文标题**：*Anchored Semantic Reduction: Locating and Policing the Bottleneck of Reliable AI for Mathematics*

> 注：本目录下的 `README.md` 是挑战资料包自带的清单文件，未作改动。本文件是提交说明。

---

## 1. 交付物对照

| 挑战要求（`required_deliverables`） | 本提交对应文件 | 说明 |
|---|---|---|
| `paper.tex` | **`paper.tex`** | 论文主文件，可独立编译 |
| `references.bib` | **`references.bib`** | 13 条 BibTeX 条目，全部经一手来源核验 |
| `*AI日志*` | **`AI日志.md`** | AI 协作日志，含提示迭代与 6 个 AI 误导被拦截案例 |
| `*AAR*` | **`AAR.md`** | 七维复盘 |

**附加产物（超出要求）**：

| 文件 | 说明 |
|---|---|
| `paper.pdf` | 编译产物（由 `references.bib` 经 BibTeX 真实解析，非手工硬编码文献表） |
| `experiments/` | 可复现实验代码（8 个 Python 模块 + 1 个确定性驱动脚本） |
| `experiments/results/` | 机器可读（`results.json`）与人可读（`summary.md`）结果留档 |
| `paper/tables/`、`paper/figures/` | 由脚本生成的 LaTeX 表格与图，被 `paper.tex` 以 `\input` 引入 |

---

## 2. 论文一句话概要

AI4Math 的可靠性通常靠**加强最后一层**（验证）来追求。本文论证并实验证明：可靠性实际由**中间层（自然语言 → 验证器所消费对象的"语义归约"）**决定，而这一层在多数系统中无人监控。两个命题：验证器只对**它拿到的东西**可靠，因此不忠实的归约会产生"带证书的错误答案"；且只看 IR 的验证器是 IR 的函数，故对被归约**坍缩**的句子对召回率恒为 0（结构性，不是经验巧合）。实验在精确可判定的受控语言上对照两种锚点：词法往返比对（A-lex）与类型化谓词锚点（A-type）。

---

## 3. 核心实验结论（供快速核对）

| 结论 | 数字 | 出处 |
|---|---|---|
| 归约+验证的静默错误率（受控普遍率语料，n=238） | **55.9%**，同时证书率 **100%** | `paper/tables/pipeline_mixed.tex` |
| 加 A-type 锚点后静默错误率 | **0.0%** | 同上 |
| 结构性盲区的经验验证 | 192 个临界对中，**100%** 产生完全相同的 IR 与相同的判定 | `experiments/results/summary.md` |
| IR-only 验证作为归约错误检测器 | 召回 **0.0%**（244 个不忠实归约） | `paper/tables/detector.tex` |
| A-type 安全性（不忠实归约被扣下） | **100%** | 同上 |
| A-lex 在 p=1.00 时对类型漂移的召回 | **0.0%**（其含混的总体召回部分来自同义词误判） | `paper/tables/detector_canonical.tex` |
| 空真命题（32 条）上 `verify` 的表现 | 全部答对，**一次都不警告** | `paper/tables/vacuity.tex` |

---

## 4. 如何复现

### 4.1 跑实验（重新生成论文中所有数字、表格、图）

```bash
cd experiments
python run_experiment.py            # 默认 seed=20260928, n_per_class=40
```

依赖见 `requirements.txt`（仅标准库 + 无第三方运行时依赖；PDF 抽取脚本另需 `pypdf`）。
脚本是确定性的：同一种子进出，结果逐位一致。

输出：

```
experiments/results/results.json     # 机器可读全量留档
experiments/results/summary.md       # 人可读汇总
paper/tables/*.tex                   # 论文表格（\input 引入）
paper/figures/*.tex                  # 论文图（pgfplots）
```

### 4.2 编译论文

```bash
tectonic -X compile paper.tex --outdir .
```

或使用标准 LaTeX 发行版：

```bash
pdflatex paper && bibtex paper && pdflatex paper && pdflatex paper
```

**本仓库使用的工具链**：Tectonic 0.17.0（单文件 LaTeX 引擎，免安装、免管理员权限），
引擎位于 `D:\tools\tectonic\tectonic.exe`，包缓存位于 `D:\tools\tectonic-cache`。
选择 Tectonic 的原因见 `AAR.md` 维度 4 与 `AI日志.md` §4.3（MiKTeX 安装器在受限环境下反复失败）。

论文使用的宏包：`amsmath` `amssymb` `amsthm` `booktabs` `graphicx` `pgfplots` `geometry`
`hyperref` `natbib`（书目样式 `plainnat`）。

---

## 5. 代码结构

```
experiments/
├── run_experiment.py              # 确定性驱动：跑实验 + 生成全部 LaTeX 片段
├── ai4math_reliability/
│   ├── ir.py                      # 类型化 IR + 精确可判定过程（有理数枚举）
│   ├── surface.py                 # 受控表层语言 + 同义词词表 + 句框抽取器
│   ├── reductor.py                # 语义归约层（含 blind 特征集）+ 部分逆
│   ├── anchors.py                 # A-lex（词法往返）与 A-type（类型化谓词）锚点
│   ├── data.py                    # P1–P5 语料生成（生成时校验真值确实翻转）
│   └── pipelines.py               # 7 条流水线阶梯
└── results/                       # 结果留档
```

设计要点：

- **分层与论文一致**：`ir.py` 不 import 自然语言；`surface.py` 不 import 求解器。
  论文中的「生成 / 语义归约 / 验证」三层与代码模块一一对应。
- **精确性**：全程 `fractions.Fraction`，判定结果是精确整数/有理数，无浮点容差可掩盖错误。
- **语料自校验**：`data.py::build_dataset` 只接受「真值确实翻转」的扰动对，其余候选在生成时丢弃。
- **检测与弃答分离**：度量三个量 —— `recall`（锚点与 IR 矛盾=真检测）、`safety`（答案被扣下）、
  `over_abstention`（忠实归约被拒绝认证=代价）。混同这三者是本项目曾犯过的错误，见 `AI日志.md` §3.2。

---

## 6. 论文的原创贡献（对应评分维度）

1. **三层错误模型 + 瓶颈定位**（generation / semantic reduction / verification）。
2. **归约层失败分类学 P1–P5**：量词漂移、定义域类型漂移、严格性漂移、假设遗漏、空真 —— 且全部实例化并测量。
3. **两个命题 + 一个推论 + 一个注记**：
   - 命题 1：验证器可靠且完备时，不忠实归约仍产出**带证书的错误答案**；
   - 命题 2（结构性）：IR-only 验证器是 IR 的函数 → 对坍缩对召回恒为 0；
   - 推论：换更强的 IR-only 验证器无法提高召回；
   - 注记（类型擦除对往返不可见）：解释 A-lex 为何在 P2 上召回归零。
4. **对照实验**：7 条流水线阶梯 × 5 类失败 + 良性对照 + 普遍率受控语料 + p 敏感性扫描 + p=1.00 消融。
5. **8 项可靠性检查清单**（可直接用于工程评审）。
6. **明确指出框架失效的条件**（锚点抽取器必须独立于归约器），以及外推边界。

---

## 7. 已知局限（论文 §Limitations 同样声明）

1. 受控语言片段（有限域仿射算术），**不是**真实数学文本；机制声称与语言无关，但**量级不可外推**。
2. 未与真实 autoformalizer 做基线对比 —— 本论文最大的实质缺口（见 `AAR.md` 维度 1、行动 A4）。
3. 语料为确定性构造，非抽样，故报告的是对所构造语料的**确定性测量**，不构成对总体的统计推断。
4. A-lex 的「非单调召回」依赖 `canonical_prior` 这一建模参数，是本项目的建模选择而非自然常数。
5. `paper.tex` 的作者块为占位符，投稿前需替换。

---

## 8. 引用真实性声明

`references.bib` 中 **13 条**条目全部对照一手来源核验（出版方页面 / arXiv 摘要页 / 官方仓库），
核验动作在 `AI日志.md` 中有记录。**无任何条目直接来自模型记忆而未核验。**
期间拦截到 1 处错误（AI 将 AlphaProof 的 Nature 卷期年份记为 2026，实际为 2025）。
