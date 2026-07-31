# 核心接口需求梳理

## 1. 系统边界

本 MVP 采用前后端分层：

- 前端负责项目管理、水质数据录入、工艺参数配置、结果展示、误差对比和报告入口。
- 后端负责数据校验、单位统一、模型计算、误差计算和报告生成。
- 模型层已提供 ASM1/ASM2d 快速筛选，并预留 QSDsan 官方动态系统适配器。

## 2. 核心数据对象

### WaterQuality

| 字段 | 含义 | 单位 |
|---|---|---|
| `flow_m3_d` | 日处理水量 | m3/d |
| `cod_mg_l` | 化学需氧量 | mg/L |
| `bod_mg_l` | 生化需氧量 | mg/L |
| `nh4_n_mg_l` | 氨氮 | mg/L |
| `tn_mg_l` | 总氮 | mg/L |
| `tp_mg_l` | 总磷 | mg/L |
| `tss_mg_l` | 悬浮物 | mg/L |
| `ph` | pH | - |
| `do_mg_l` | 溶解氧 | mg/L |
| `temperature_c` | 水温 | C |
| `conductivity_us_cm` | 电导率 | uS/cm |
| `orp_mv` | 氧化还原电位 | mV |

### ProcessParameters

| 字段 | 含义 |
|---|---|
| `process_type` | 工艺类型，默认 AAO |
| `model_type` | 模型类型，默认 ASM2d |
| `hrt_h` | 水力停留时间 |
| `srt_d` | 污泥龄 |
| `internal_recycle_ratio` | 内回流比 |
| `sludge_recycle_ratio` | 污泥回流比 |
| `aeration_power_kw` | 曝气功率 |
| `aerobic_do_mg_l` | 好氧段 DO 设定值 |
| `alkalinity_mg_l_caco3` | 碱度，以 CaCO3 计 |
| `clarifier_solids_capture` | 二沉池固体捕集率，0-1 |
| `external_carbon_dose_mg_l` | 后置反硝化外加碳源，按化学需氧量当量计，毫克/升 |
| `ferric_chloride_dose_mg_l` | 化学除磷三氯化铁投加量，毫克/升 |
| `tertiary_filter_solids_capture` | 三级过滤固体截留率，0-0.99 |

## 3. API 清单

### `GET /health`

用途：检查后端服务是否可用。

返回示例：

```json
{
  "status": "ok",
  "service": "Wastewater Process Modeling API"
}
```

### `POST /api/projects`

用途：新建项目。

请求示例：

```json
{
  "name": "AAO稳态仿真测试",
  "plant_name": "深水海纳测试污水厂",
  "process_type": "AAO",
  "owner": "C同学",
  "description": "第1阶段MVP项目"
}
```

### `GET /api/projects`

用途：获取项目列表。

### `POST /api/measurements`

用途：上传进水、出水或过程点位的实测数据。

请求示例：

```json
{
  "project_id": "项目ID",
  "location": "influent",
  "water_quality": {
    "flow_m3_d": 5000,
    "cod_mg_l": 260,
    "bod_mg_l": 120,
    "nh4_n_mg_l": 32,
    "tn_mg_l": 48,
    "tp_mg_l": 4.2,
    "tss_mg_l": 180,
    "ph": 7.1,
    "do_mg_l": 1.2,
    "temperature_c": 22
  }
}
```

### `GET /api/models`

用途：获取 ASM1、ASM2d、mASM2d、ADM1 的适用范围、输入要求和就绪状态。

### `GET /api/models/engine`

用途：检查 FastAPI 当前环境是否能导入官方 QSDsan 包。

### `POST /api/simulate`

用途：执行参数化稳态筛选。通过 `parameters.model_type` 选择 ASM1 或 ASM2d；
mASM2d、ADM1 在扩展输入未提供前返回 422，避免用虚构默认值计算。

主要输出：

- 预测出水指标
- 各指标去除率
- 日能耗
- 污泥量
- 达标判断
- 模型、假设及数据不足警告

### `POST /api/calibrate`

用途：输入预测出水和实测出水，计算误差。

主要输出：

- MAE
- MAPE
- RMSE
- 各指标绝对误差
- 参数修正建议

### `POST /api/reports`

用途：预留 PDF/Excel 报告生成接口。

第 7 工作日重点实现：

- 项目信息
- 输入水质
- 仿真结果
- 达标判断
- 误差分析
- 运行建议

## 4. 第 2 天衔接任务

C 同学明天建议继续完成：

- 完善 `/api/simulate` 请求和响应 JSON 模板
- 与 A 同学确认指标字段、单位、上下限
- 与 B 同学确认 QSDsan 模型输入输出字段
- 前端增加项目新建表单和数据录入提交逻辑
- 后端增加基础单元换算和数据清洗函数
