# 污水厂工艺流程建模评估程序

第 1 工作日基础框架，面向 C 同学负责的前后端程序开发任务：

- Python 后端基础环境：FastAPI + Pydantic 分层接口
- 前端基础框架：React + Vite + TypeScript
- 数据交互主线：项目、测量、仿真、校准、报告
- 模型计算层：ASM1/ASM2d 快速筛选，mASM2d/ADM1 输入条件登记

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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
`frontend`。Pages 环境没有 FastAPI 时，项目记录保存在浏览器本地；配置
`VITE_API_BASE_URL` 后可连接独立部署的后端服务。

## 第 1 天完成状态

- 已搭建后端 FastAPI 基础工程
- 已完成项目、测量、仿真、校准、报告 5 类核心接口骨架
- 已搭建前端 React/Vite 基础工程
- 已完成水质录入与接口清单的页面骨架
- 已整理核心接口需求文档

## 模型接口

当前数据流：

```text
前端 JSON 输入
→ FastAPI 参数校验
→ 模型选择与输入完备性检查
→ ASM1/ASM2d 快速筛选计算
→ 出水指标/去除率/能耗等结果
→ JSON 返回前端
```

模型目录见 `GET /api/models`，完整 QSDsan 动态系统的环境与校准要求见
`docs/model-integration.md`。
