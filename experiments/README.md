# experiments — 可复现实验代码

配套论文：*Anchored Semantic Reduction: Locating and Policing the Bottleneck of Reliable AI for Mathematics*

---

## 快速开始

```bash
cd experiments
python run_experiment.py                 # 默认 seed=20260928, n_per_class=40
```

**运行时依赖：无第三方库。** 仅用 Python 标准库（`fractions`、`itertools`、`dataclasses`、`random`、`json`、`argparse`、`pathlib`）。
论文所需的表格与图会被写入 `../paper/tables/` 与 `../paper/figures/`，结果留档写入 `results/`。

脚本**确定性**：同一种子进出，结果逐位一致。

```bash
python run_experiment.py --seed 12345 --n-per-class 60   # 换种子/规模复跑
```

---

## 模块职责

| 模块 | 职责 | 边界 |
|---|---|---|
| `ai4math_reliability/ir.py` | 类型化 IR（变量声明 / 假设 / 目标 / 量词）+ 精确可判定过程 | **不 import 自然语言**。判定用 `fractions.Fraction` 精确枚举，可靠且（在片段内）完备 |
| `ai4math_reliability/surface.py` | 受控表层语言、同义词词表（`SURFACE`）、锚点文法子集（`RECOGNISED`）、句框抽取器 | **不 import 求解器**。抽取策略：先匹配固定句框，再对每个 slot 做最长短语查表 |
| `ai4math_reliability/reductor.py` | 语义归约层：特征记录 → IR（`features_to_spec`），含 `blind` 特征集；IR → 特征的**部分逆**（`spec_to_features`） | `blind` 建模"模型把自己没建模的区分悄悄归一化掉"这一失败模式 |
| `ai4math_reliability/anchors.py` | 两类锚点：`lexical_anchor_check`（A-lex，词法往返）与 `typed_anchor_check`（A-type，类型化谓词） | 锚点判定三态：`violated` / `unconfirmed` / `certifiable` |
| `ai4math_reliability/data.py` | P1–P5 临界语料 + 良性对照语料 + 普遍率受控语料 | **生成时校验**：只接受真值确实翻转的扰动对 |
| `ai4math_reliability/pipelines.py` | 7 条流水线阶梯 | 见下 |
| `run_experiment.py` | 确定性驱动：跑全部度量 + 生成全部 LaTeX 片段 | 单一入口，论文中每个数字都由它产生 |

---

## 流水线阶梯

| 名称 | 归约器 | 锚点 | 策略 |
|---|---|---|---|
| `gen_only` | blind（全盲） | 无 | 只答不证 |
| `verify` | blind | 无 | 归约后精确判定，全部出证书 |
| `verify+lexical_anchors` | blind | A-lex | 保守：锚点不可读或不成立则弃答 |
| `verify+typed_anchors` | blind | A-type | 保守：同上 |
| `typed_anchored` | **忠实** | A-type | 保守：不可认证则弃答 |
| `typed_best_effort` | **忠实** | A-type | 尽力：总作答，仅在锚点确认时出证书 |
| `oracle` | **忠实** | 完美 | 上限 |

**两处关键建模决定**（混同它们会得出误导性结论）：

1. **归约器与锚点抽取器的缺陷不同**。归约器有**完整词法能力**（读得懂所有同义词）但对四个特征族全盲 —— 缺陷是**语义**的。锚点抽取器是符号组件，只有**声明的封闭文法** —— 缺陷是**覆盖**的。分开建模，才能让"词表缺口"与"语义盲区"在表里不混淆。
2. **`typed_*` / `oracle` 行的忠实归约是"目标"，不是"真实系统已达成的状态"**。它的作用是做分解：前三行回答"归约不忠实时锚点能买到什么"，后三行回答"归约可信之后还剩什么"。

---

## 度量定义（容易混淆，务必区分）

**流水线层面**：

- `coverage` = 作答数 / 总数
- `accuracy_answered` = 答对数 / 作答数
- `silent_error_rate` = （作答数 − 答对数）/ 总数 —— **以"已认证"姿态发出的错误答案占比**
- `certificate_rate` = 带证书数 / 总数

**锚点层面**（"检测到"与"拒绝作答"不是一回事）：

- 实例 **unfaithful** ⟺ blind 归约产出的 IR ≠ 由句子真实特征构造的参考 IR。
  注意这是**归约**的性质，不是**答案**的性质 —— 归约可以不忠实却碰巧答对。
- `recall` = 锚点**违反**（矛盾）数 / unfaithful —— 真检测
- `safety` = unfaithful 中答案被扣下的比例（含因不可读而弃答）
- `over_abstention` = faithful 中被拒绝认证的比例 —— **代价**

> **警告**：曾把"弃答"错算成"检测"，使召回虚高到 100%。三个量必须分开报告。详见 `../AI日志.md` §3.2。

---

## 输出与留档

| 路径 | 内容 |
|---|---|
| `results/results.json` | 全量机器可读留档（种子、语料计数、每条流水线/每个检测器的度量、盲区不变性、空真报告、敏感性、p=1.00 消融） |
| `results/summary.md` | 人可读汇总（Markdown 表） |
| `../paper/tables/*.tex` | 论文表格（`booktabs`，被 `paper.tex` 以 `\input` 引入） |
| `../paper/figures/*.tex` | 论文图（`pgfplots`） |

论文与证据的绑定方式：论文**不手写数字**，全部 `\input` 脚本产物。因此论文无法与证据悄悄脱钩。

---

## 复现验证要点

1. **盲区不变性**：192 个临界对中，100% 产生完全相同的 IR 与相同的判定（命题 2 的经验验证）。
2. **零基线**：IR-only 验证作为归约错误检测器召回 0.0%（推论）。
3. **空真**：32 条空真命题上，`gen_only`/`verify` 全部答对却一次都不警告。
4. **p=1.00 消融**：A-lex 对类型漂移（P2）的召回归零 —— 其含混的总体召回部分来自同义词误判（注记：类型擦除对往返不可见）。

---

## 已知缺陷（复现时请留意）

- **未加守恒断言**：度量函数没有断言"处理数 == 输入数"。此前一个 `if naive is None: continue` 曾使基线只统计 40/244 个实例。补断言是下一步改动（见 `../AAR.md` 行动 A3）。
- **无第三方库**：这是刻意的（零依赖便于复核），代价是没有测试框架；验证靠显式断言与手工算术核对。
