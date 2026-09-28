# C2 — AI for Math 论文：AI4Math 可靠性框架

> 挑战 `ch-20260717031343-8ot0ji`
> 论文：*Anchored Semantic Reduction: Locating and Policing the Bottleneck of Reliable AI for Mathematics*

AI4Math 的可靠性通常靠**加强最后一层（验证）**来追求。本文论证并实验证明：可靠性实际由**中间层**决定 —— 即"自然语言 → 验证器所消费对象"的**语义归约**（semantic reduction），而这一层在多数系统中无人监控。

两个命题：

1. **不忠实的归约会产出"带证书的错误答案"**。验证器只对*它拿到的东西*可靠；归约一旦丢失语义，验证越完美，错误越可信。
2. **只看中间表示的验证器是中间表示的函数**，因此对被归约**坍缩**的句子对，其召回率**恒为 0** —— 这是结构性结论，不是经验巧合。推论：换更强的同类验证器无法修复它。

实验在**精确可判定**的受控语言（有限域仿射算术 + 量词，全程 `fractions.Fraction`，无浮点容差）上，对照两种锚点：词法往返比对（**A-lex**）与类型化谓词锚点（**A-type**）。

---

## 交付物

| 挑战要求 | 文件 |
|---|---|
| `paper.tex` | [paper.tex](paper.tex)（可独立编译，15 页） |
| `references.bib` | [references.bib](references.bib)（13 条，全部一手来源核验） |
| `*AI日志*` | [AI日志.md](AI日志.md)（含 6 个 AI 误导被拦截案例） |
| `*AAR*` | [AAR.md](AAR.md)（七维复盘） |

附加产物：`paper.pdf`（编译产物）、`experiments/`（可复现实验代码）、`paper/tables/` 与 `paper/figures/`（由脚本生成的 LaTeX 片段，被 `paper.tex` 以 `\input` 引入）。

---

## 核心结果

| 结论 | 数字 |
|---|---|
| 归约 + 验证的静默错误率（受控语料，n=238） | **55.9%**，同时证书率 **100%** ← 最危险组合 |
| 加 A-type 锚点后静默错误率 | **0.0%** |
| 结构性盲区的经验验证 | 192 个临界对中 **100%** 产生完全相同的 IR 与相同判定 |
| IR-only 验证作为归约错误检测器 | 召回 **0.0%**（244 个不忠实归约） |
| A-type 安全性（不忠实归约被扣下） | **100%** |
| A-lex 在 p=1.00 时对类型漂移的召回 | **0.0%**（其含混的总体召回部分来自同义词误判） |
| 空真命题（32 条）上 `reduce+verify` 的表现 | 全部答对，**一次都不警告** |

---

## 复现

**跑实验**（重新生成论文中所有数字、表格、图）：

```bash
cd experiments
python run_experiment.py          # 默认 seed=20260928, n_per_class=40
```

脚本是**确定性**的：同一种子进出，结果逐位一致，无第三方运行时依赖。

**编译论文**：

```bash
tectonic -X compile paper.tex --outdir .
# 或标准发行版： pdflatex paper && bibtex paper && pdflatex paper && pdflatex paper
```

用到的宏包：`amsmath` `amssymb` `amsthm` `booktabs` `graphicx` `pgfplots` `geometry` `hyperref` `natbib`。

---

## 代码结构

```
experiments/
├── run_experiment.py              # 确定性驱动：跑实验 + 生成全部 LaTeX 片段
└── ai4math_reliability/
    ├── ir.py                      # 类型化 IR + 精确可判定过程（有理数枚举）
    ├── surface.py                 # 受控表层语言 + 同义词词表 + 句框抽取器
    ├── reductor.py                # 语义归约层（含 blind 特征集）+ 部分逆
    ├── anchors.py                 # A-lex（词法往返）与 A-type（类型化谓词）锚点
    ├── data.py                    # P1–P5 语料生成（生成时校验真值确实翻转）
    └── pipelines.py               # 7 条流水线阶梯
```

设计要点见 [experiments/README.md](experiments/README.md)：
分层与论文三层一一对应；全程有理数精确判定；语料自校验（只接受真值确实翻转的扰动对）；
**检测（recall）与弃答（safety）与误弃（over-abstention）三者分开度量**。

---

## 原创贡献

1. **三层错误模型 + 瓶颈定位**（generation / semantic reduction / verification）。
2. **归约层失败分类学 P1–P5**：量词漂移、定义域类型漂移、严格性漂移、假设遗漏、空真 —— 全部实例化并测量。
3. **两个命题 + 一个推论 + 一个注记**（注记解释 A-lex 在 P2 上为何召回归零：类型擦除对往返不可见）。
4. **对照实验**：7 条流水线阶梯 × 5 类失败 + 良性对照 + 普遍率受控语料 + p 敏感性扫描 + p=1.00 消融。
5. **8 项可靠性检查清单**（可直接用于工程评审）。
6. **明确指出框架失效的条件**（锚点抽取器必须独立于归约器）与外推边界。

---

## 已知局限

1. 受控语言片段（有限域仿射算术），**不是**真实数学文本；机制声称与语言无关，但**量级不可外推**。
2. 未与真实 autoformalizer 做基线对比 —— 本工作最大的实质缺口（见 `AAR.md` 维度 1、行动 A4）。
3. 语料为确定性构造而非抽样，报告的是对所构造语料的**确定性测量**，不构成对总体的统计推断。
4. A-lex 的"非单调召回"依赖 `canonical_prior` 建模参数，是本项目的建模选择而非自然常数。
5. `paper.tex` 作者块为占位符，投稿前需替换。

---

## 引用真实性声明

`references.bib` 中 **13 条**条目全部对照一手来源核验（出版方页面 / arXiv 摘要页 / 官方仓库），核验过程记录在 `AI日志.md`。
**无任何条目直接来自模型记忆而未核验。** 期间拦截到 1 处错误（模型将 AlphaProof 的 Nature 卷期年份记为 2026，实际为 2025）。

详见 [SUBMISSION.md](SUBMISSION.md)。
