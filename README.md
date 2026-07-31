# 污水厂工艺流程建模评估程序

第 1 工作日基础框架，面向 C 同学负责的前后端程序开发任务：

- Python 后端基础环境：FastAPI + Pydantic 分层接口
- 前端基础框架：React + Vite + TypeScript
- 数据交互主线：项目、测量、仿真、校准、报告
- 模型计算层：QSDsan/EXPOsan ASM1、ASM2d 动态系统

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
    models/
      schemas.py
    services/
      project_service.py
      simulation_service.py
      calibration_service.py
      report_service.py

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
```

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

## 第 1 天完成状态

- 已搭建后端 FastAPI 基础工程
- 已完成项目、测量、动态仿真、分组校准、表格导入和报告下载接口
- 已搭建前端 React/Vite 基础工程
- 已完成水质录入与接口清单的页面骨架
- 已整理核心接口需求文档

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
`docs/model-integration.md`。
