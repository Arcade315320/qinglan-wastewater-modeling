# 清澜智评：污水厂工艺流程建模评估平台

面向污水处理工艺建模、动态仿真、校准和评估的前后端应用：

- Python 后端基础环境：FastAPI + Pydantic 分层接口
- 前端基础框架：React + Vite + TypeScript
- 数据交互主线：项目、测量、仿真、校准、报告
- 模型计算层：QSDsan/EXPOsan ASM1、ASM2d 动态系统

## 在线环境

- 前端网站：https://arcade315320.github.io/qinglan-wastewater-modeling/
- 后端接口文档：https://qinglan-wastewater-api.onrender.com/docs
- GitHub 仓库：https://github.com/Arcade315320/qinglan-wastewater-modeling

线上架构为 GitHub Pages 前端、Render 接口与持久化数据库、Modal 动态模型计算端。

## 当前模型能力

- 普通活性污泥：活性污泥模型一动态拓扑
- 缺氧-好氧：活性污泥模型一动态拓扑
- 厌氧-缺氧-好氧：活性污泥模型二维动态拓扑
- 氧化沟、序批式、周期循环活性污泥和膜生物反应器已建立输入契约，但专用动态拓扑尚未开放
- 其他工艺在专用模型完成前会被服务端明确阻止，避免错误套用模型

模型结果只有在稳态、质量守恒、工程数据资格和独立验证同时通过后，才能形成正式达标判定。

## 目录结构

```text
backend/
  requirements.txt
  app/
    main.py
    api/
      routes.py
    core/
      config.py
      time.py
      warning_policy.py
    models/
      schemas.py
    services/
      project_service.py
      simulation_service.py
      calibration_service.py
      report_service.py
      qsdsan_adapter.py
      simulation_job_service.py
      traceability_service.py

frontend/
  package.json
  index.html
  vite.config.ts
  tsconfig.json
  src/
    main.tsx
    pages/
      App.tsx
    components/
      MetricCard.tsx
      SectionHeader.tsx
    styles/
      global.css

docs/
  api-requirements.md
  model-integration.md
  model-development-roadmap.md

render.yaml                 # Render 服务与数据库配置
.github/workflows/          # GitHub Pages 自动发布
```

主要修改入口：

- 页面和交互：`frontend/src/pages/App.tsx`
- 前端接口类型：`frontend/src/api.ts`
- 后端路由：`backend/app/api/routes.py`
- 数据结构与校验：`backend/app/models/schemas.py`
- 仿真流程：`backend/app/services/simulation_service.py`
- QSDsan 动态系统：`backend/app/services/qsdsan_adapter.py`
- 校准流程：`backend/app/services/calibration_service.py`
- 报告导出：`backend/app/services/report_service.py`

## 本地启动

后端：

```bash
cd backend
python3.12 -m venv .venv-model
source .venv-model/bin/activate
pip install -r requirements-models.txt
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

默认地址：

- 后端健康检查：`http://127.0.0.1:8000/health`
- 后端接口文档：`http://127.0.0.1:8000/docs`
- 前端页面：`http://127.0.0.1:5173`

## GitHub Pages

推送到 `main` 后，`.github/workflows/deploy-pages.yml` 会自动构建并发布
`frontend`。先用根目录 `render.yaml` 部署后端，再在 GitHub 仓库变量中将
`API_BASE_URL` 设置为后端公开地址。前端不再生成本地假结果。

当前仓库变量应指向：

```text
https://qinglan-wastewater-api.onrender.com
```

公司接手后只需把代码推送到 `main`，GitHub Actions 会自动重新发布前端。

## Render 后端

根目录 `render.yaml` 定义后端服务和 PostgreSQL 数据库。Render 需要配置：

- `MODAL_SIMULATION_URL`：Modal 动态计算接口地址
- `MODAL_AUTH_TOKEN`：Render 与 Modal 共用的鉴权密钥
- `DATABASE_URL`：由 `render.yaml` 中的数据库自动注入

Render 会跟踪 `main` 分支并自动部署。生产密钥不得写入源码、压缩包或 GitHub 提交。

## 免费线上计算

线上采用两层部署：Render 免费实例处理项目、校准和报告接口，Modal 免费
账户提供 2 GB 内存运行完整 QSDsan 动态模型。部署计算端点：

```bash
cd backend
python -m modal secret create qinglan-simulation-auth \
  SIMULATION_AUTH_TOKEN=自行生成的高强度随机值
python -m modal deploy modal_app.py
```

随后在 Render 中配置：

- `MODAL_SIMULATION_URL`：Modal 部署命令返回的函数地址
- `MODAL_AUTH_TOKEN`：与 Modal 密钥中的随机值一致

访问密钥不得提交到 Git 仓库。

## 验证

后端完整测试：

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
```

前端生产构建：

```bash
cd frontend
npm ci
npm run build
```

当前交付版本已通过 67 项后端测试、前端生产构建，以及普通活性污泥、缺氧-好氧、厌氧-缺氧-好氧三种动态拓扑的稳态收敛测试。

## 交接注意事项

- 不提交 `.env`、密钥、数据库文件、生成报告、虚拟环境和 `node_modules`。
- 修改模型后必须补充固定回归测试和独立工厂数据验证。
- 活性污泥模型一不含磷过程，总磷不得参与其达标判定。
- 能耗和污泥默认属于估算结果，只有同期实测偏差不超过 20% 才标记为校准通过。
- GitHub Pages 只能托管前端静态文件，完整动态模型仍需 Render 与 Modal 服务。

## 模型接口

当前数据流：

```text
前端 JSON 输入
→ FastAPI 参数校验
→ 模型选择与输入完备性检查
→ 总量到ASM组分映射及重构检查
→ QSDsan动态反应器、回流和二沉池积分
→ 出水指标、质量守恒、能耗和污泥结果
→ JSON 返回前端
```

模型目录见 `GET /api/models`，QSDsan 动态系统的环境与校准要求见
`docs/model-integration.md`，公开数据复核结果见
`docs/public-validation.md`。
