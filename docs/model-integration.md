# 后端工艺模型接入说明

## 已纳入的模型目录

| 模型 | 用途 | 当前状态 |
| --- | --- | --- |
| ASM1 | 有机物去除、硝化、反硝化 | QSDsan 动态系统 |
| ASM2d | 有机物、氮、强化生物除磷及化学除磷 | QSDsan 动态系统，默认模型 |
| mASM2d | ASM2d + pH/离子平衡/矿物沉淀 | 已登记，等待 Ca、Mg、K、Na、Cl 等输入 |
| ADM1 | 厌氧消化、VFA、产甲烷和污泥稳定化 | 已登记，等待厌氧组分及气相输入 |

模型元数据可由 `GET /api/models` 获取。`POST /api/simulate` 通过
`parameters.model_type` 选择模型。

`GET /api/models/engine` 会返回 FastAPI 当前 Python 进程能否导入 QSDsan、
包版本及失败原因。只有 `available=true` 才能启用官方动态系统适配器。

## ASM2d 对照结果

领导提供的 1999 年 ASM2d 论文为扫描版，共 18 页。模型采用 Gujer 矩阵，
描述 19 个组分及 21 个过程，覆盖水解、异养菌好氧/缺氧生长、发酵、PAO
的 PHA/聚磷过程、自养硝化、衰亡及磷沉淀/再溶解。

当前计算层使用 QSDsan 动态反应器、内回流、污泥回流和十层二沉池，并执行
Gujer 矩阵动态积分。总量水质会先映射为 ASM 组分，响应中的
`component_mapping` 和 `mass_balance` 用于检查重构误差与质量守恒。

## 完整 QSDsan 运行环境

建议用 Python 3.12 创建独立环境：

```bash
cd backend
python3.12 -m venv .model-venv
source .venv-model/bin/activate
pip install -r requirements-models.txt
python -c "import qsdsan; print(qsdsan.__version__)"
```

本机曾存在 QSDsan 1.5.3，但 Anaconda 基础环境的 NumPy 2.4.6 与已有
pandas/pyarrow 二进制包不兼容。不要直接复用该基础环境；应按上述版本隔离。

## 上线前必须完成

1. 用实测溶解/颗粒组分替代自动组分化比例。
2. 用工艺单元表中的实际池容和连接关系替代模板化反应器分区。
3. 用现场排泥量、MLSS 和 SVI 校准二沉池与目标污泥龄。
4. 每厂至少提供五组完整配对数据；程序按工厂分组并保留最新日期作验证集。
5. 质量守恒、BSM1 回归和独立时段误差均通过后方可形成正式结论。

“模型已运行”不等于“结果准确无误”。未经校准的 ASM 模型只能用于筛选，
不能直接作为达标承诺或工程设计依据。

## 主要来源

- QSDsan `process_models._asm2d`、`_asm1`、`_adm1`
- Henze et al., *Activated Sludge Models ASM1, ASM2, ASM2d and ASM3*, IWA, 2000
- Batstone et al., *Anaerobic Digestion Model No. 1*, IWA, 2002
- Rieger et al., *Guidelines for Using Activated Sludge Models*, IWA, 2012
