import {
  Activity,
  ArrowRight,
  BarChart3,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  ClipboardCheck,
  Database,
  Droplets,
  FileDown,
  FileSpreadsheet,
  FileText,
  FlaskConical,
  Gauge,
  Home,
  MapPin,
  Menu,
  Play,
  Plus,
  Save,
  Settings2,
  SlidersHorizontal,
  Target,
  Upload,
  X
} from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import {
  api,
  apiUrl,
  type CalibrationSample,
  type EffluentLimits,
  type HotStartState,
  type InfluentTimePoint,
  type ProcessCapability,
  type ProcessParameters,
  type ProjectRecord,
  type SimulationResult,
  type WaterQuality
} from "../api";

type PageId = "home" | "project" | "input" | "result" | "report";

const pages: { id: PageId; label: string; icon: typeof Home }[] = [
  { id: "home", label: "工作台", icon: Home },
  { id: "project", label: "项目概览", icon: Activity },
  { id: "input", label: "数据录入", icon: Settings2 },
  { id: "result", label: "仿真结果", icon: BarChart3 },
  { id: "report", label: "报告导出", icon: FileText }
];

const entryWallpapers = [
  { src: `${import.meta.env.BASE_URL}assets/wastewater-plant-entry.webp`, label: "污水处理厂全景" },
  { src: `${import.meta.env.BASE_URL}assets/wastewater-plant-blue-hour.webp`, label: "蓝调时刻处理厂" },
  { src: `${import.meta.env.BASE_URL}assets/wastewater-plant-morning.webp`, label: "晨光曝气池" }
];

const initialIndicators = [
  { name: "流量", key: "flow", value: "5000", unit: "m³/d" },
  { name: "COD", key: "cod", value: "260", unit: "mg/L" },
  { name: "BOD₅", key: "bod", value: "120", unit: "mg/L" },
  { name: "NH₄-N", key: "nh4", value: "32", unit: "mg/L" },
  { name: "TN", key: "tn", value: "48", unit: "mg/L" },
  { name: "TP", key: "tp", value: "4.2", unit: "mg/L" },
  { name: "TSS", key: "tss", value: "180", unit: "mg/L" },
  { name: "pH", key: "ph", value: "7.1", unit: "-" },
  { name: "水温", key: "temperature", value: "22", unit: "°C" }
];

type WorkflowContextValue = {
  project: ProjectRecord | null;
  setProject: (project: ProjectRecord | null) => void;
  influent: WaterQuality | null;
  setInfluent: (influent: WaterQuality) => void;
  parameters: ProcessParameters | null;
  setParameters: (parameters: ProcessParameters) => void;
  simulation: SimulationResult | null;
  setSimulation: (simulation: SimulationResult | null) => void;
  calibrationSamples: CalibrationSample[];
  setCalibrationSamples: (samples: CalibrationSample[]) => void;
};

const WorkflowContext = createContext<WorkflowContextValue | null>(null);

function useWorkflow() {
  const value = useContext(WorkflowContext);
  if (!value) throw new Error("Workflow context is unavailable");
  return value;
}

type ProcessDefinition = {
  id: string;
  label: string;
  shortLabel: string;
  category: string;
  description: string;
  steps: string[];
  features: string[];
};

const fallbackDynamicProcesses = new Set(["CAS", "AO", "AAO"]);

function useProcessCapabilities() {
  const [capabilities, setCapabilities] = useState<ProcessCapability[]>([]);
  useEffect(() => {
    api.listProcessCapabilities().then(setCapabilities).catch(() => undefined);
  }, []);
  const runnable = new Set(
    capabilities.filter((item) => item.runnable).map((item) => item.process_type)
  );
  return {
    capabilities,
    runnable: capabilities.length ? runnable : fallbackDynamicProcesses
  };
}

const processCatalog: ProcessDefinition[] = [
  {
    id: "CAS",
    label: "传统活性污泥法（CAS）",
    shortLabel: "CAS",
    category: "活性污泥法",
    description: "以曝气池和二沉池为核心，主要去除有机物，作为其他活性污泥工艺的基础形式。",
    steps: ["进水", "预处理", "初沉池", "曝气池", "二沉池", "消毒", "出水"],
    features: ["二沉池污泥回流至曝气池", "剩余污泥由二沉池排出"]
  },
  {
    id: "AO",
    label: "缺氧-好氧工艺（A/O）",
    shortLabel: "A/O",
    category: "脱氮除磷工艺",
    description: "前置缺氧段利用进水碳源反硝化，后续好氧段完成有机物降解和硝化。",
    steps: ["进水", "预处理", "缺氧池", "好氧池", "二沉池", "消毒", "出水"],
    features: ["好氧末端混合液回流至缺氧池", "二沉池污泥回流至缺氧池"]
  },
  {
    id: "AAO",
    label: "厌氧-缺氧-好氧工艺（A²/O）",
    shortLabel: "A²/O",
    category: "脱氮除磷工艺",
    description: "依次设置厌氧、缺氧和好氧环境，实现生物除磷、反硝化和硝化。",
    steps: ["进水", "预处理", "厌氧池", "缺氧池", "好氧池", "二沉池", "消毒", "出水"],
    features: ["好氧末端混合液回流至缺氧池", "二沉池污泥回流至厌氧池"]
  },
  {
    id: "oxidation_ditch",
    label: "氧化沟工艺",
    shortLabel: "氧化沟",
    category: "活性污泥法",
    description: "采用封闭环形沟渠连续循环曝气，具有较长泥龄并可通过分区实现硝化反硝化。",
    steps: ["进水", "预处理", "选择池", "氧化沟", "二沉池", "消毒", "出水"],
    features: ["沟内混合液连续循环", "二沉池污泥回流至选择池"]
  },
  {
    id: "SBR",
    label: "序批式活性污泥法（SBR）",
    shortLabel: "SBR",
    category: "序批式工艺",
    description: "在同一反应池内按时间顺序完成进水、反应、沉淀、滗水和闲置。",
    steps: ["进水", "预处理", "进水阶段", "反应阶段", "沉淀阶段", "滗水阶段", "消毒", "出水"],
    features: ["反应与沉淀在同一池体完成", "通常不设二沉池和污泥回流系统"]
  },
  {
    id: "CASS",
    label: "循环式活性污泥法（CASS）",
    shortLabel: "CASS",
    category: "序批式工艺",
    description: "SBR 的改进形式，反应池包含生物选择区和主反应区，并周期完成反应、沉淀和滗水。",
    steps: ["进水", "预处理", "生物选择区", "兼氧区", "主反应区", "沉淀/滗水", "消毒", "出水"],
    features: ["主反应区污泥回流至生物选择区", "主反应区按周期曝气、沉淀和滗水"]
  },
  {
    id: "UCT",
    label: "UCT 生物脱氮除磷工艺",
    shortLabel: "UCT",
    category: "脱氮除磷工艺",
    description: "通过改变污泥回流位置，降低回流污泥中硝酸盐对厌氧释磷的影响。",
    steps: ["进水", "预处理", "厌氧池", "缺氧池", "好氧池", "二沉池", "消毒", "出水"],
    features: ["二沉池污泥回流至缺氧池", "缺氧混合液回流至厌氧池", "好氧混合液回流至缺氧池"]
  },
  {
    id: "MUCT",
    label: "改良 UCT 工艺（MUCT）",
    shortLabel: "MUCT",
    category: "脱氮除磷工艺",
    description: "将缺氧区分为两段，分别处理污泥回流中的硝酸盐和好氧区内回流中的硝酸盐。",
    steps: ["进水", "预处理", "厌氧池", "缺氧池Ⅰ", "缺氧池Ⅱ", "好氧池", "二沉池", "消毒", "出水"],
    features: ["污泥回流至缺氧池Ⅰ", "缺氧池Ⅰ混合液回流至厌氧池", "好氧混合液回流至缺氧池Ⅱ"]
  },
  {
    id: "bardenpho5",
    label: "五段 Bardenpho 工艺",
    shortLabel: "Bardenpho",
    category: "脱氮除磷工艺",
    description: "由厌氧、第一缺氧、第一好氧、第二缺氧和再曝气五段组成，强化总氮和总磷去除。",
    steps: ["进水", "预处理", "厌氧池", "缺氧池Ⅰ", "好氧池Ⅰ", "缺氧池Ⅱ", "再曝气池", "二沉池", "消毒", "出水"],
    features: ["好氧池Ⅰ混合液回流至缺氧池Ⅰ", "二沉池污泥回流至厌氧池"]
  },
  {
    id: "MBR",
    label: "膜生物反应器（MBR）",
    shortLabel: "MBR",
    category: "膜与生物膜工艺",
    description: "以膜分离代替二沉池完成泥水分离，可维持较高污泥浓度并获得低悬浮物出水。",
    steps: ["进水", "精细预处理", "缺氧池", "好氧池", "膜池", "消毒", "出水"],
    features: ["膜组件代替二沉池", "膜池混合液回流至缺氧池", "需设置膜曝气和反洗"]
  },
  {
    id: "MBBR",
    label: "移动床生物膜反应器（MBBR）",
    shortLabel: "MBBR",
    category: "膜与生物膜工艺",
    description: "在反应池中投加悬浮载体形成生物膜，可按缺氧和好氧分区实现脱氮。",
    steps: ["进水", "预处理", "缺氧 MBBR", "好氧 MBBR", "二沉池", "过滤/消毒", "出水"],
    features: ["载体由拦截筛网保留在反应池内", "好氧末端混合液回流至缺氧段"]
  },
  {
    id: "IFAS",
    label: "活性污泥-生物膜复合工艺（IFAS）",
    shortLabel: "IFAS",
    category: "膜与生物膜工艺",
    description: "在活性污泥曝气池内加入生物膜载体，同时保留悬浮污泥和附着生物量。",
    steps: ["进水", "预处理", "缺氧池", "IFAS 好氧池", "二沉池", "过滤/消毒", "出水"],
    features: ["二沉池污泥回流以维持悬浮污泥", "载体保留附着生物量", "好氧混合液回流至缺氧池"]
  },
  {
    id: "BAF",
    label: "曝气生物滤池（BAF）",
    shortLabel: "BAF",
    category: "膜与生物膜工艺",
    description: "利用固定滤料上的生物膜同步完成生化反应和过滤，可分设反硝化与硝化滤池。",
    steps: ["进水", "预处理", "初沉/混凝沉淀", "反硝化滤池", "硝化曝气滤池", "消毒", "出水"],
    features: ["滤池需周期反冲洗", "硝化出水回流至反硝化滤池"]
  },
  {
    id: "contact_oxidation",
    label: "生物接触氧化法",
    shortLabel: "接触氧化",
    category: "膜与生物膜工艺",
    description: "在曝气池内设置固定填料培养生物膜，兼具活性污泥法和生物滤池特征。",
    steps: ["进水", "预处理", "水解酸化池", "接触氧化池", "二沉池", "过滤/消毒", "出水"],
    features: ["生物膜附着于固定填料", "脱氮场景可增设缺氧段和硝化液回流"]
  },
  {
    id: "UASB_AO",
    label: "UASB + A/O 组合工艺",
    shortLabel: "UASB+A/O",
    category: "厌氧组合工艺",
    description: "前段 UASB 去除高浓度有机物并产沼气，后续缺氧-好氧段完成深度有机物去除和脱氮。",
    steps: ["进水", "预处理/调节", "UASB", "缺氧池", "好氧池", "二沉池", "消毒", "出水"],
    features: ["UASB 产生沼气和厌氧污泥", "好氧混合液回流至缺氧池", "二沉池污泥回流至缺氧池"]
  },
  {
    id: "custom",
    label: "自定义组合工艺",
    shortLabel: "自定义",
    category: "其他",
    description: "用于尚未纳入预设目录的工艺路线，后续可通过流程编辑器配置具体处理单元。",
    steps: ["进水", "预处理", "处理单元Ⅰ", "处理单元Ⅱ", "处理单元Ⅲ", "固液分离", "消毒", "出水"],
    features: ["需进一步确认处理单元类型", "需人工配置回流关系和模型参数"]
  }
];

const processCategories = [...new Set(processCatalog.map((item) => item.category))];

function readPage(): PageId {
  const page = window.location.hash.replace("#/", "") as PageId;
  return pages.some((item) => item.id === page) ? page : "home";
}

function AppIcon() {
  return (
    <div className="app-icon" aria-hidden="true">
      <Droplets size={21} strokeWidth={2.4} />
    </div>
  );
}

function PageHeading({
  eyebrow,
  title,
  description,
  action
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </header>
  );
}

function HomePage({ navigate }: { navigate: (page: PageId) => void }) {
  const [activeWallpaper, setActiveWallpaper] = useState(0);

  useEffect(() => {
    entryWallpapers.forEach(({ src }) => {
      const image = new Image();
      image.src = src;
    });
  }, []);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (reducedMotion.matches) return;

    const timer = window.setTimeout(() => {
      setActiveWallpaper((current) => (current + 1) % entryWallpapers.length);
    }, 7000);

    return () => window.clearTimeout(timer);
  }, [activeWallpaper]);

  return (
    <section className="entry-page">
      <div className="entry-wallpapers" aria-hidden="true">
        {entryWallpapers.map((wallpaper, index) => (
          <div
            key={wallpaper.src}
            className={`entry-wallpaper${index === activeWallpaper ? " active" : ""}`}
            style={{ backgroundImage: `url(${wallpaper.src})` }}
          />
        ))}
      </div>
      <div className="entry-shade" />
      <div className="entry-content">
        <h1>清澜智评</h1>
        <p>面向污水处理工艺的建模与评估平台。基于 QSDsan 标准工作流，将实测数据、稳态仿真、模型校准与工艺评价连接为清晰可靠的数字化过程。</p>
      </div>
      <div className="entry-wallpaper-nav" aria-label="入口背景场景">
        {entryWallpapers.map((wallpaper, index) => (
          <button
            key={wallpaper.src}
            className={index === activeWallpaper ? "active" : ""}
            onClick={() => setActiveWallpaper(index)}
            aria-label={`切换至${wallpaper.label}`}
            aria-current={index === activeWallpaper ? "true" : undefined}
          />
        ))}
      </div>
      <button className="entry-button" onClick={() => navigate("project")} aria-label="进入系统" title="进入系统">
        <ArrowRight size={23} />
      </button>
    </section>
  );
}

function ProjectPage() {
  const { capabilities, runnable } = useProcessCapabilities();
  const {
    project: createdProject,
    setProject,
    influent,
    parameters,
    simulation,
    calibrationSamples
  } = useWorkflow();
  const [processType, setProcessType] = useState("AAO");
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [saveMessage, setSaveMessage] = useState("");
  const [projectForm, setProjectForm] = useState({
    name: "深水海纳示范污水厂",
    code: "DSHN-2026-001",
    owner: "C 同学",
    location: "广东省深圳市",
    period: "2026-07-27 至 2026-08-06",
    description: "基于实测进出水数据，对 AAO 工艺进行动态仿真、参数校准与达标评估。",
    designScale: "5000"
  });
  useEffect(() => {
    if (!createdProject) return;
    setProcessType(createdProject.process_type);
    setProjectForm((current) => ({
      ...current,
      name: createdProject.name,
      code: createdProject.project_code ?? current.code,
      owner: createdProject.owner ?? "",
      location: createdProject.location ?? "",
      period: createdProject.modeling_period ?? "",
      description: createdProject.description ?? "",
      designScale: createdProject.design_flow_m3_d?.toString() ?? current.designScale
    }));
  }, [createdProject?.id]);
  const selectedProcess = processCatalog.find((item) => item.id === processType) ?? processCatalog[0];
  const selectedCapability = capabilities.find((item) => item.process_type === processType);
  const readinessItems: [string, string, boolean][] = [
    ["项目及工艺信息", createdProject ? "已保存" : "待保存", Boolean(createdProject)],
    ["专用动态拓扑", runnable.has(processType) ? "已建立" : "尚未建立", runnable.has(processType)],
    ["进水水质数据", influent ? "已录入" : "待录入", Boolean(influent)],
    ["运行参数", parameters ? "已录入" : "待录入", Boolean(parameters)],
    ["动态计算与守恒", simulation?.mass_balance.passed ? "已通过" : "待通过", Boolean(simulation?.mass_balance.passed)],
    ["出水实测校准数据", calibrationSamples.length >= 5 ? "已达到最低数量" : `${calibrationSamples.length}/5 条`, calibrationSamples.length >= 5],
    ["独立时段验证", parameters?.independent_validation_passed ? "已完成" : "待完成", Boolean(parameters?.independent_validation_passed)]
  ];
  const readyCount = readinessItems.filter(([, , ready]) => ready).length;
  const readinessPercent = Math.round(readyCount / readinessItems.length * 100);
  const markDirty = () => {
    setDirty(true);
    setSaveState("idle");
    setSaveMessage("");
  };
  const updateProjectField = (field: keyof typeof projectForm, value: string) => {
    setProjectForm((current) => ({ ...current, [field]: value }));
    markDirty();
  };
  const saveProject = async () => {
    const name = projectForm.name.trim();
    if (!name || !projectForm.owner.trim()) {
      setSaveState("error");
      setSaveMessage("请先填写项目名称和项目负责人。");
      return;
    }

    setSaveState("saving");
    setSaveMessage("");
    const projectPayload = {
      name,
      plant_name: name,
      process_type: processType,
      owner: projectForm.owner.trim(),
      description: projectForm.description.trim() || null,
      project_code: projectForm.code.trim() || null,
      location: projectForm.location.trim() || null,
      modeling_period: projectForm.period.trim() || null,
      design_flow_m3_d: Number(projectForm.designScale)
    };

    try {
      const record = createdProject
        ? await api.updateProject(createdProject.id, projectPayload)
        : await api.createProject(projectPayload);
      setProject(record);
      setDirty(false);
      setSaveState("success");
      setSaveMessage(
        createdProject
          ? `项目已更新至第 ${record.revision} 版。`
          : `项目已创建，ID：${record.id}`
      );
      window.setTimeout(() => setSaveState("idle"), 4000);
    } catch (error) {
      setSaveState("error");
      setSaveMessage(error instanceof Error ? `保存失败：${error.message}` : "保存失败，请检查后端服务。");
    }
  };

  return (
    <div className="page">
      <PageHeading eyebrow="01 / 项目建档" title="项目概览" description="定义评估对象、工艺路线与模型边界，形成后续数据录入和仿真计算的统一项目上下文。"
        action={<button className="button primary" onClick={saveProject} disabled={saveState === "saving"}>
          <Save size={17} /> {saveState === "saving" ? "正在保存..." : dirty || !createdProject ? "保存项目" : "已保存"}
        </button>} />

      {saveState === "success" && <div className="save-toast" role="status"><CheckCircle2 size={17} /> {saveMessage}</div>}
      {saveState === "error" && <div className="save-toast error" role="alert"><span>!</span> {saveMessage}</div>}

      <section className="project-summary" aria-label="项目状态摘要">
        <div className="project-identity">
          <span className="project-mark"><Building2 size={22} /></span>
        <div><small>当前项目</small><strong>{projectForm.name || "未命名项目"}</strong><span><i /> {createdProject ? "已在后端创建" : "建模准备中"}</span></div>
        </div>
        <div><small>处理工艺</small><strong>{selectedProcess.shortLabel}</strong><span>{selectedProcess.category}</span></div>
        <div><small>设计规模</small><strong>{Number(projectForm.designScale || 0).toLocaleString()}</strong><span>m³/d</span></div>
        <div><small>评价标准</small><strong>{parameters?.effluent_standard === "grade_b" ? "一级 B" : parameters?.effluent_standard === "custom" ? "项目限值" : "一级 A"}</strong><span>以计算时选择为准</span></div>
        <div><small>资料完整度</small><strong>{readinessPercent}%</strong><span className="mini-progress"><i style={{ width: `${readinessPercent}%` }} /></span></div>
      </section>

      <section className="form-section">
        <div className="section-title"><div><h2>基本信息</h2><p>用于项目检索、成果归档和报告封面</p></div><span className="required-note">* 必填项</span></div>
        <div className="form-grid">
          <label className="field wide"><span>项目名称 *</span><input value={projectForm.name} onChange={(event) => updateProjectField("name", event.target.value)} /></label>
          <label className="field"><span>项目编号 *</span><input value={projectForm.code} onChange={(event) => updateProjectField("code", event.target.value)} /></label>
          <label className="field"><span>项目负责人 *</span><input value={projectForm.owner} onChange={(event) => updateProjectField("owner", event.target.value)} /></label>
          <label className="field"><span>项目地点</span><div className="field-with-icon"><MapPin size={15} /><input value={projectForm.location} onChange={(event) => updateProjectField("location", event.target.value)} /></div></label>
          <label className="field"><span>建模周期</span><div className="field-with-icon"><CalendarDays size={15} /><input value={projectForm.period} onChange={(event) => updateProjectField("period", event.target.value)} /></div></label>
          <label className="field wide"><span>项目描述</span><textarea value={projectForm.description} onChange={(event) => updateProjectField("description", event.target.value)} /></label>
        </div>
      </section>

      <section className="form-section">
        <div className="section-title"><div><h2>工艺路线</h2><p>选择主体工艺并确认模型中的处理单元顺序</p></div><span className="process-tag">{selectedCapability?.status ?? (runnable.has(processType) ? "专用动态拓扑可运行" : "能力状态加载中")}</span></div>
        <div className="form-grid three">
          <label className="field process-select-field"><span>主体工艺 *</span>
            <select value={processType} onChange={(event) => { setProcessType(event.target.value); markDirty(); }}>
              {processCategories.map((category) => (
                <optgroup label={category} key={category}>
                  {processCatalog.filter((item) => item.category === category).map((item) => (
                    <option value={item.id} key={item.id}>{item.label}{runnable.has(item.id) ? " · 可运行" : " · 仅建档"}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <ChevronDown size={16} />
          </label>
          <label className="field"><span>设计规模 *</span><div className="input-unit"><input value={projectForm.designScale} onChange={(event) => updateProjectField("designScale", event.target.value)} /><b>m³/d</b></div></label>
          <label className="field"><span>污水类型</span><select defaultValue="municipal" onChange={markDirty}><option value="municipal">市政生活污水</option><option value="industrial">工业废水</option><option value="mixed">混合污水</option></select><ChevronDown size={16} /></label>
        </div>
        <div className="process-definition">
          <strong>{selectedProcess.label}</strong>
          <p>{selectedProcess.description}</p>
        </div>
        <div className="process-flow" aria-label={`${selectedProcess.label}工艺流程`}>
          {selectedProcess.steps.map((item, index) => (
            <div key={`${item}-${index}`}><span>{index + 1}</span><strong>{item}</strong>{index < selectedProcess.steps.length - 1 && <ArrowRight size={18} />}</div>
          ))}
        </div>
        <div className="process-features">
          <span>关键结构</span>
          <div>{selectedProcess.features.map((feature) => <small key={feature}><CheckCircle2 size={14} />{feature}</small>)}</div>
        </div>
        <div className="process-note"><CircleHelp size={16} /><span>{selectedCapability?.limitation ?? selectedCapability?.topology ?? "正在读取后端模型能力。"}</span></div>
        {createdProject && (
          <div className="backend-record">
            <Database size={16} />
            <span><strong>后端项目记录</strong>ID：{createdProject.id}</span>
            <small>创建于 {new Date(createdProject.created_at).toLocaleString("zh-CN")}</small>
          </div>
        )}
      </section>

      <div className="project-detail-grid">
        <section className="form-section model-scope">
          <div className="section-title"><div><h2>模型与评价范围</h2><p>明确本次计算包含的系统边界和成果指标</p></div><Target size={19} /></div>
          <div className="form-grid">
            <label className="field"><span>仿真模式 *</span><select defaultValue="dynamic" onChange={markDirty}><option value="dynamic">动态仿真</option></select><ChevronDown size={16} /></label>
            <label className="field"><span>排放判定配置</span><input value="在数据录入页随工况设置" readOnly /></label>
          </div>
          <fieldset className="scope-options">
            <legend>系统边界</legend>
            {["水线处理单元", "污泥产量核算", "曝气能耗核算", "药剂消耗核算"].map((item, index) => (
              <label key={item}><input type="checkbox" defaultChecked={index < 3} onChange={markDirty} /><span><CheckCircle2 size={15} />{item}</span></label>
            ))}
          </fieldset>
          <fieldset className="scope-options">
            <legend>评价指标</legend>
            {["COD / BOD₅ 去除", "脱氮效果", "除磷效果", "TSS 达标", "单位水量能耗", "污泥产量"].map((item) => (
              <label key={item}><input type="checkbox" defaultChecked onChange={markDirty} /><span><CheckCircle2 size={15} />{item}</span></label>
            ))}
          </fieldset>
        </section>

        <aside className="readiness-panel">
          <div className="section-title"><div><h2>数据准备度</h2><p>运行模型前的必要条件</p></div><Database size={19} /></div>
          <div className="readiness-score"><strong>{readinessPercent}%</strong><span><i style={{ width: `${readinessPercent}%` }} /></span><small>{readyCount} 项已就绪，{readinessItems.length - readyCount} 项待完善</small></div>
          <div className="readiness-list">
            {readinessItems.map(([name, state, ready]) => (
              <div key={String(name)}><span className={ready ? "ready" : "pending"}>{ready ? <CheckCircle2 size={15} /> : "!"}</span><strong>{name}</strong><small>{state}</small></div>
            ))}
          </div>
          <button className="button secondary full-button"><Database size={16} /> 查看数据清单</button>
        </aside>
      </div>
    </div>
  );
}

function InputPage({ navigate }: { navigate: (page: PageId) => void }) {
  const { runnable } = useProcessCapabilities();
  const {
    project,
    setInfluent,
    setParameters,
    setSimulation,
    setCalibrationSamples
  } = useWorkflow();
  const [indicators, setIndicators] = useState(initialIndicators);
  const [parameterValues, setParameterValues] = useState({
    hrt: "12",
    srt: "15",
    internalRecycle: "200",
    sludgeRecycle: "80",
    aerationPower: "15",
    aerationHours: "24",
    mixingPower: "0",
    pumpingPower: "0",
    dissolvedOxygen: "2",
    aerobicKla: "",
    oxygenTransferEfficiency: "1.5",
    oxygenAlphaFactor: "0.80",
    oxygenBetaFactor: "0.95",
    diffuserFoulingFactor: "0.90",
    siteAltitude: "0",
    diffuserSubmergence: "4",
    reactorPh: "",
    alkalinity: "250",
    reactorVolume: "",
    anaerobicVolume: "",
    anoxicVolume: "",
    aerobicVolume: "",
    clarifierArea: "",
    clarifierDepth: "",
    settlerVMax: "",
    settlerVMaxPractical: "",
    settlerTssThreshold: "",
    wasteSludgeFlow: "",
    mixedLiquorTss: "",
    returnSludgeTss: "",
    wasteSludgeTss: "",
    measuredTotalEnergy: "",
    measuredDrySludge: "",
    simulationDays: "30",
    maxSimulationDays: "100"
  });
  const [fractionationValues, setFractionationValues] = useState({
    solubleCod: "",
    vfa: "",
    nitrate: "",
    nitrite: "",
    orthophosphate: ""
  });
  const [stepFeedEnabled, setStepFeedEnabled] = useState(false);
  const [firstStepFeedPercent, setFirstStepFeedPercent] = useState("60");
  const [advancedTreatmentEnabled, setAdvancedTreatmentEnabled] = useState(false);
  const [advancedTreatmentValues, setAdvancedTreatmentValues] = useState({
    externalCarbon: "8",
    ferricChloride: "26",
    filterCapture: "85"
  });
  const [standard, setStandard] = useState<"grade_a" | "grade_b" | "custom">("grade_a");
  const [assessmentBasis, setAssessmentBasis] = useState<"daily_average" | "instantaneous">("daily_average");
  const [commissionedBefore2006, setCommissionedBefore2006] = useState(false);
  const [operatingDataSource, setOperatingDataSource] = useState<"measured" | "published" | "design" | "assumed">("assumed");
  const [advancedTreatmentVerified, setAdvancedTreatmentVerified] = useState(false);
  const [customLimitValues, setCustomLimitValues] = useState({
    cod: "50",
    nh4: "5",
    tn: "15",
    tp: "0.5",
    tss: "10"
  });
  const [influentSeries, setInfluentSeries] = useState<InfluentTimePoint[] | undefined>();
  const [hotStart, setHotStart] = useState<HotStartState | undefined>();
  const [runState, setRunState] = useState<"idle" | "saving" | "running" | "error">("idle");
  const [message, setMessage] = useState("");
  const updateValue = (key: string, value: string) =>
    setIndicators((items) => items.map((item) => item.key === key ? { ...item, value } : item));
  const numericValue = (key: string) => Number(indicators.find((item) => item.key === key)?.value ?? 0);
  const buildPayload = () => {
    const selectedProcessType = project?.process_type ?? "AAO";
    if (!runnable.has(selectedProcessType)) {
      throw new Error(
        `当前尚未建立 ${selectedProcessType} 专用动态拓扑，请先选择 CAS、AO 或 AAO。`
      );
    }
    if (Number(parameterValues.simulationDays) > 100) {
      throw new Error("单次动态积分最多支持 100 天，更长时段请分段计算。");
    }
    const optionalNumber = (value: string) => value.trim() === "" ? null : Number(value);
    if (
      stepFeedEnabled
      && selectedProcessType === "AO"
      && (Number(firstStepFeedPercent) <= 0 || Number(firstStepFeedPercent) >= 100)
    ) {
      throw new Error("第一缺氧段进水比例必须大于0%且小于100%。");
    }
    const zoneValues = [
      parameterValues.anaerobicVolume,
      parameterValues.anoxicVolume,
      parameterValues.aerobicVolume
    ];
    if (zoneValues.some((value) => value.trim() !== "") && zoneValues.some((value) => value.trim() === "")) {
      throw new Error("厌氧、缺氧和好氧池有效容积必须同时填写，或全部留空采用工艺默认比例。");
    }
    const waterQuality: WaterQuality = {
      flow_m3_d: numericValue("flow"),
      cod_mg_l: numericValue("cod"),
      bod_mg_l: numericValue("bod"),
      nh4_n_mg_l: numericValue("nh4"),
      tn_mg_l: numericValue("tn"),
      tp_mg_l: numericValue("tp"),
      tss_mg_l: numericValue("tss"),
      ph: numericValue("ph"),
      temperature_c: numericValue("temperature"),
      soluble_cod_mg_l: optionalNumber(fractionationValues.solubleCod),
      vfa_as_cod_mg_l: optionalNumber(fractionationValues.vfa),
      nitrate_n_mg_l: optionalNumber(fractionationValues.nitrate),
      nitrite_n_mg_l: optionalNumber(fractionationValues.nitrite),
      orthophosphate_p_mg_l: optionalNumber(fractionationValues.orthophosphate)
    };
    if (waterQuality.nh4_n_mg_l > waterQuality.tn_mg_l) {
      throw new Error("氨氮不能高于总氮，请检查录入值。");
    }
    if (
      waterQuality.bod_mg_l !== null
      && waterQuality.bod_mg_l > waterQuality.cod_mg_l * 1.1
    ) {
      throw new Error("五日生化需氧量不能明显高于化学需氧量，请检查录入值。");
    }
    if (
      waterQuality.cod_mg_l > 0
      && waterQuality.tss_mg_l / waterQuality.cod_mg_l < 0.08
    ) {
      throw new Error(
        "进水悬浮物与化学需氧量的比值异常低，请确认是否误将出水悬浮物填入进水栏。"
      );
    }
    const phosphorusProcesses = new Set(["AAO", "SBR", "CASS", "UCT", "MUCT", "bardenpho5", "MBR", "IFAS"]);
    const processParameters: ProcessParameters = {
      process_type: selectedProcessType,
      model_type: phosphorusProcesses.has(selectedProcessType) ? "ASM2d" : "ASM1",
      hrt_h: Number(parameterValues.hrt),
      srt_d: Number(parameterValues.srt),
      internal_recycle_ratio: Number(parameterValues.internalRecycle) / 100,
      sludge_recycle_ratio: Number(parameterValues.sludgeRecycle) / 100,
      aeration_power_kw: Number(parameterValues.aerationPower),
      aeration_hours_d: Number(parameterValues.aerationHours),
      mixing_power_kw: Number(parameterValues.mixingPower),
      pumping_power_kw: Number(parameterValues.pumpingPower),
      aerobic_do_mg_l: Number(parameterValues.dissolvedOxygen),
      aerobic_kla_d: optionalNumber(parameterValues.aerobicKla),
      oxygen_transfer_efficiency_kg_o2_kwh: Number(parameterValues.oxygenTransferEfficiency),
      oxygen_alpha_factor: Number(parameterValues.oxygenAlphaFactor),
      oxygen_beta_factor: Number(parameterValues.oxygenBetaFactor),
      diffuser_fouling_factor: Number(parameterValues.diffuserFoulingFactor),
      site_altitude_m: Number(parameterValues.siteAltitude),
      diffuser_submergence_m: Number(parameterValues.diffuserSubmergence),
      reactor_ph: optionalNumber(parameterValues.reactorPh),
      alkalinity_mg_l_caco3: Number(parameterValues.alkalinity),
      reactor_volume_m3: optionalNumber(parameterValues.reactorVolume),
      anaerobic_volume_m3: optionalNumber(parameterValues.anaerobicVolume),
      anoxic_volume_m3: optionalNumber(parameterValues.anoxicVolume),
      aerobic_volume_m3: optionalNumber(parameterValues.aerobicVolume),
      clarifier_surface_area_m2: optionalNumber(parameterValues.clarifierArea),
      clarifier_depth_m: optionalNumber(parameterValues.clarifierDepth),
      settler_v_max_m_d: optionalNumber(parameterValues.settlerVMax),
      settler_v_max_practical_m_d: optionalNumber(parameterValues.settlerVMaxPractical),
      settler_tss_threshold_mg_l: optionalNumber(parameterValues.settlerTssThreshold),
      waste_sludge_flow_m3_d: optionalNumber(parameterValues.wasteSludgeFlow),
      mixed_liquor_tss_mg_l: optionalNumber(parameterValues.mixedLiquorTss),
      return_sludge_tss_mg_l: optionalNumber(parameterValues.returnSludgeTss),
      waste_sludge_tss_mg_l: optionalNumber(parameterValues.wasteSludgeTss),
      measured_total_energy_kwh_d: optionalNumber(parameterValues.measuredTotalEnergy),
      measured_dry_sludge_kg_d: optionalNumber(parameterValues.measuredDrySludge),
      step_feed_fractions: stepFeedEnabled && selectedProcessType === "AO"
        ? [Number(firstStepFeedPercent) / 100, 1 - Number(firstStepFeedPercent) / 100]
        : null,
      simulation_days: Number(parameterValues.simulationDays),
      auto_convergence: true,
      max_simulation_days: Number(parameterValues.maxSimulationDays),
      convergence_tolerance_per_d: 0.01,
      cod_kinetic_factor: 1,
      nitrification_kinetic_factor: 1,
      denitrification_kinetic_factor: 1,
      phosphorus_kinetic_factor: 1,
      external_carbon_dose_mg_l: advancedTreatmentEnabled
        ? Number(advancedTreatmentValues.externalCarbon)
        : 0,
      ferric_chloride_dose_mg_l: advancedTreatmentEnabled
        ? Number(advancedTreatmentValues.ferricChloride)
        : 0,
      tertiary_filter_solids_capture: advancedTreatmentEnabled
        ? Number(advancedTreatmentValues.filterCapture) / 100
        : 0,
      effluent_standard: standard,
      assessment_basis: assessmentBasis,
      commissioned_before_2006: commissionedBefore2006,
      assessment_date: new Date().toISOString().slice(0, 10),
      operating_data_source: operatingDataSource,
      advanced_treatment_verified: advancedTreatmentEnabled && advancedTreatmentVerified,
      independent_validation_passed: false,
      independent_validation_sample_count: 0,
      independent_validation_nrmse: null,
      validation_record_id: null
    };
    const customLimits: EffluentLimits | undefined = standard === "custom"
      ? {
          cod_mg_l: Number(customLimitValues.cod),
          nh4_n_mg_l: Number(customLimitValues.nh4),
          tn_mg_l: Number(customLimitValues.tn),
          tp_mg_l: Number(customLimitValues.tp),
          tss_mg_l: Number(customLimitValues.tss),
          basis: assessmentBasis === "instantaneous" ? "项目实际执行的瞬时值" : "项目实际执行的日均值",
          source: "项目实际执行限值（地方标准、排污许可证与国家标准从严）"
        }
      : undefined;
    return { waterQuality, processParameters, customLimits };
  };
  const saveMeasurement = async () => {
    if (!project) throw new Error("请先在项目概览中保存项目。");
    const { waterQuality, processParameters } = buildPayload();
    await api.createMeasurement(project.id, waterQuality);
    setInfluent(waterQuality);
    setParameters(processParameters);
  };
  const saveData = async () => {
    setRunState("saving");
    setMessage("");
    try {
      await saveMeasurement();
      setRunState("idle");
      setMessage("进水数据已保存到后端。");
    } catch (error) {
      setRunState("error");
      setMessage(error instanceof Error ? error.message : "数据保存失败。");
    }
  };
  const runSimulation = async () => {
    setRunState("running");
    setMessage("正在执行动态积分，复杂工况通常需要一至五分钟，请保持页面打开。");
    try {
      if (!project) throw new Error("请先在项目概览中保存项目。");
      const { waterQuality, processParameters, customLimits } = buildPayload();
      await api.createMeasurement(project.id, waterQuality);
      setInfluent(waterQuality);
      setParameters(processParameters);
      const result = await api.simulate(
        project.id,
        waterQuality,
        processParameters,
        customLimits,
        influentSeries,
        hotStart
      );
      setSimulation(result);
      setRunState("idle");
      setMessage("");
      navigate("result");
    } catch (error) {
      setRunState("error");
      setMessage(error instanceof Error ? error.message : "仿真失败，请检查后端服务。");
    }
  };
  const importJson = async (
    file: File | undefined,
    kind: "influent" | "hotStart"
  ) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (kind === "influent") {
        if (!Array.isArray(parsed) || parsed.length < 3) {
          throw new Error("动态进水文件至少需要三个时间点。");
        }
        setInfluentSeries(parsed as InfluentTimePoint[]);
        setMessage(`已载入 ${parsed.length} 个动态进水时间点。`);
      } else {
        setHotStart(parsed as HotStartState);
        setMessage("已载入热启动状态。");
      }
      setRunState("idle");
    } catch (error) {
      setRunState("error");
      setMessage(error instanceof Error ? error.message : "文件解析失败。");
    }
  };
  const importWorkbook = async (file: File | undefined) => {
    if (!file) return;
    setRunState("saving");
    setMessage("正在检查表格中的日期配对和必填指标...");
    try {
      if (!project) throw new Error("请先在项目概览中保存项目。");
      const result = await api.importCalibration(project.id, file);
      setCalibrationSamples(result.samples);
      setRunState("idle");
      setMessage(
        `数据质量 ${result.quality_score} 分，${result.readiness}；`
        + `导入 ${result.imported_count} 条，跳过 ${result.skipped_count} 条。`
        + `${result.recommendations[0] ?? result.warnings[0] ?? ""}`
      );
    } catch (error) {
      setRunState("error");
      setMessage(error instanceof Error ? error.message : "表格导入失败。");
    }
  };

  return (
    <div className="page">
      <PageHeading eyebrow="02 / 数据准备" title="数据录入" description="录入进水水质和工艺运行参数。所有数值均采用每日平均值。"
        action={<div className="heading-actions"><label className="button secondary file-button"><Upload size={17} /> 导入校准表格<input type="file" accept=".xlsx" onChange={(event) => importWorkbook(event.target.files?.[0])} /></label><button className="button primary" onClick={saveData} disabled={runState === "saving" || runState === "running"}><Save size={17} /> {runState === "saving" ? "处理中..." : "保存数据"}</button></div>} />
      <div className="info-banner"><CircleHelp size={18} /><span>当前数据集：2026 年 7 月日均监测数据</span><button>切换数据集</button></div>
      <section className="form-section">
        <div className="section-title"><div><h2>进水水质</h2><p>用于构建 QSDsan 进水流对象的组分浓度</p></div><span className="validation-ok"><CheckCircle2 size={16} /> 基本关系已检查</span></div>
        <div className="indicator-grid">
          {indicators.map((item) => (
            <label className="indicator-field" key={item.key}>
              <span>{item.name}</span>
              <div><input value={item.value} onChange={(event) => updateValue(item.key, event.target.value)} /><b>{item.unit}</b></div>
            </label>
          ))}
        </div>
      </section>
      <section className="form-section">
        <div className="section-title"><div><h2>动态边界条件</h2><p>周期进水与已验证状态快照</p></div></div>
        <div className="heading-actions">
          <label className="button secondary file-button"><Upload size={17} /> 导入动态进水<input type="file" accept=".json" onChange={(event) => importJson(event.target.files?.[0], "influent")} /></label>
          <label className="button secondary file-button"><Upload size={17} /> 导入热启动状态<input type="file" accept=".json" onChange={(event) => importJson(event.target.files?.[0], "hotStart")} /></label>
          {influentSeries && <button className="button secondary" onClick={() => setInfluentSeries(undefined)}><X size={17} /> 清除动态进水</button>}
          {hotStart && <button className="button secondary" onClick={() => setHotStart(undefined)}><X size={17} /> 清除热启动</button>}
        </div>
      </section>
      <section className="form-section">
        <div className="section-title"><div><h2>进水实测分项组分</h2><p>用于约束总氮、总磷和可生化碳源分配；留空时仅能进行自动组分化筛选</p></div></div>
        <div className="indicator-grid">
          {[
            ["溶解性化学需氧量", "solubleCod", "mg/L"],
            ["挥发性脂肪酸（化学需氧量当量）", "vfa", "mg/L"],
            ["硝态氮", "nitrate", "mg/L"],
            ["亚硝态氮", "nitrite", "mg/L"],
            ["正磷酸盐磷", "orthophosphate", "mg/L"]
          ].map(([name, key, unit]) => (
            <label className="indicator-field" key={key}><span>{name}</span><div><input value={fractionationValues[key as keyof typeof fractionationValues]} onChange={(event) => setFractionationValues((current) => ({ ...current, [key]: event.target.value }))} /><b>{unit}</b></div></label>
          ))}
        </div>
      </section>
      <section className="form-section">
        <div className="section-title"><div><h2>判定依据与数据来源</h2><p>排放限值和证据来源直接决定结果能否用于工程复核</p></div></div>
        <div className="form-grid three">
          <label className="field"><span>排放标准层级</span><select value={standard} onChange={(event) => setStandard(event.target.value as typeof standard)}><option value="grade_a">国家标准一级 A</option><option value="grade_b">国家标准一级 B</option><option value="custom">项目实际执行限值</option></select><ChevronDown size={16} /></label>
          <label className="field"><span>判定值类型</span><select value={assessmentBasis} onChange={(event) => setAssessmentBasis(event.target.value as typeof assessmentBasis)}><option value="daily_average">日均值</option><option value="instantaneous">瞬时值</option></select><ChevronDown size={16} /></label>
          <label className="field"><span>运行参数来源</span><select value={operatingDataSource} onChange={(event) => setOperatingDataSource(event.target.value as typeof operatingDataSource)}><option value="assumed">程序默认假设</option><option value="published">公开文献数据</option><option value="design">设计或竣工资料</option><option value="measured">同期现场实测（参数完整）</option></select><ChevronDown size={16} /></label>
          <label className="field"><span>评估日期</span><input value={new Date().toLocaleDateString("zh-CN")} readOnly /></label>
        </div>
        <div className="evidence-options">
          <label><input type="checkbox" checked={commissionedBefore2006} onChange={(event) => setCommissionedBefore2006(event.target.checked)} /><span>污水厂在 2006 年前建成，按修改单过渡期处理总磷限值</span></label>
        </div>
        <p className="form-note">独立验证状态由服务器根据校准样本和留出数据自动生成，不能人工勾选或填写。</p>
        {standard === "custom" && (
          <div className="indicator-grid custom-limits">
            {[
              ["化学需氧量限值", "cod"],
              ["氨氮限值", "nh4"],
              ["总氮限值", "tn"],
              ["总磷限值", "tp"],
              ["悬浮物限值", "tss"]
            ].map(([name, key]) => (
              <label className="indicator-field" key={key}><span>{name}</span><div><input value={customLimitValues[key as keyof typeof customLimitValues]} onChange={(event) => setCustomLimitValues((current) => ({ ...current, [key]: event.target.value }))} /><b>mg/L</b></div></label>
            ))}
          </div>
        )}
        <p className="treatment-note">国家标准判定已区分日均值与瞬时值，并考虑低温氨氮限值及总磷过渡期；瞬时值自 2026 年 3 月 1 日起适用，悬浮物不作为该表的瞬时判定项目。地方标准或排污许可证更严格时应选择项目实际执行限值。</p>
      </section>
      <section className="form-section">
        <div className="section-title treatment-heading">
          <div>
            <h2>出水强化处理</h2>
            <p>用于测算后置反硝化、化学除磷和三级过滤，必须与现场实际设施一致</p>
          </div>
          <label className="treatment-toggle">
            <input
              type="checkbox"
              checked={advancedTreatmentEnabled}
              onChange={(event) => setAdvancedTreatmentEnabled(event.target.checked)}
            />
            <span>{advancedTreatmentEnabled ? "已启用方案测算" : "未启用"}</span>
          </label>
        </div>
        <div className="indicator-grid">
          {[
            ["外加碳源（化学需氧量当量）", "externalCarbon", "mg/L"],
            ["三氯化铁投加量", "ferricChloride", "mg/L"],
            ["三级过滤固体截留率", "filterCapture", "%"]
          ].map(([name, key, unit]) => (
            <label className="indicator-field" key={name}>
              <span>{name}</span>
              <div>
                <input
                  value={advancedTreatmentValues[key as keyof typeof advancedTreatmentValues]}
                  disabled={!advancedTreatmentEnabled}
                  onChange={(event) => setAdvancedTreatmentValues((current) => ({
                    ...current,
                    [key]: event.target.value
                  }))}
                />
                <b>{unit}</b>
              </div>
            </label>
          ))}
        </div>
        <p className="treatment-note">
          推荐初始情景为碳源 8、三氯化铁 26 毫克/升、过滤截留率 85%；实际投加量必须通过现场试验校准。
        </p>
        {advancedTreatmentEnabled && (
          <div className="evidence-options treatment-evidence">
            <label><input type="checkbox" checked={advancedTreatmentVerified} onChange={(event) => setAdvancedTreatmentVerified(event.target.checked)} /><span>现场确有对应设施，且投加量和过滤截留率来自同期运行记录</span></label>
          </div>
        )}
      </section>
      <section className="form-section">
        <div className="section-title"><div><h2>运行与设备参数</h2><p>优先填写同期实测池容、排泥、沉淀和供氧参数；留空项将采用基准估算</p></div></div>
        <div className="indicator-grid parameters">
          {[
            ["水力停留时间", "hrt", "h"],
            ["污泥龄", "srt", "d"],
            ["内回流比", "internalRecycle", "%"],
            ["污泥回流比", "sludgeRecycle", "%"],
            ["曝气功率", "aerationPower", "kW"],
            ["每日曝气时长", "aerationHours", "h/d"],
            ["搅拌总功率", "mixingPower", "kW"],
            ["泵送总功率", "pumpingPower", "kW"],
            ["好氧池溶解氧", "dissolvedOxygen", "mg/L"],
            ["现场传氧系数", "aerobicKla", "1/d"],
            ["氧转移效率", "oxygenTransferEfficiency", "kgO₂/kWh"],
            ["污水传氧修正系数", "oxygenAlphaFactor", "-"],
            ["饱和溶氧修正系数", "oxygenBetaFactor", "-"],
            ["曝气器污堵系数", "diffuserFoulingFactor", "-"],
            ["厂区海拔", "siteAltitude", "m"],
            ["曝气器淹没水深", "diffuserSubmergence", "m"],
            ["反应池实测酸碱度", "reactorPh", "-"],
            ["碱度", "alkalinity", "mg/L"],
            ["生化池总有效容积", "reactorVolume", "m³"],
            ["厌氧池有效容积", "anaerobicVolume", "m³"],
            ["缺氧池有效容积", "anoxicVolume", "m³"],
            ["好氧池有效容积", "aerobicVolume", "m³"],
            ["二沉池总表面积", "clarifierArea", "m²"],
            ["二沉池有效水深", "clarifierDepth", "m"],
            ["理论最大沉降速度", "settlerVMax", "m/d"],
            ["实用最大沉降速度", "settlerVMaxPractical", "m/d"],
            ["沉降受阻临界污泥浓度", "settlerTssThreshold", "mg/L"],
            ["实际排泥流量", "wasteSludgeFlow", "m³/d"],
            ["池内污泥浓度", "mixedLiquorTss", "mg/L"],
            ["回流污泥浓度", "returnSludgeTss", "mg/L"],
            ["排泥污泥浓度", "wasteSludgeTss", "mg/L"],
            ["同期全厂总能耗", "measuredTotalEnergy", "kWh/d"],
            ["同期全厂干污泥产量", "measuredDrySludge", "kg/d"],
            ["初始动态积分时长", "simulationDays", "d"],
            ["自动收敛最大时长", "maxSimulationDays", "d"]
          ].map(([name, key, unit]) => (
            <label className="indicator-field" key={name}><span>{name}</span><div><input value={parameterValues[key as keyof typeof parameterValues]} onChange={(event) => setParameterValues((current) => ({ ...current, [key]: event.target.value }))} /><b>{unit}</b></div></label>
          ))}
        </div>
        {project?.process_type === "AO" && (
          <div className="evidence-options treatment-evidence">
            <label>
              <input
                type="checkbox"
                checked={stepFeedEnabled}
                onChange={(event) => setStepFeedEnabled(event.target.checked)}
              />
              <span>启用两点分段进水专用拓扑</span>
            </label>
            {stepFeedEnabled && (
              <label className="indicator-field">
                <span>第一缺氧段进水比例</span>
                <div>
                  <input
                    type="number"
                    min="1"
                    max="99"
                    value={firstStepFeedPercent}
                    onChange={(event) => setFirstStepFeedPercent(event.target.value)}
                  />
                  <b>%</b>
                </div>
              </label>
            )}
          </div>
        )}
      </section>
      {message && <div className={`save-toast inline ${runState === "error" ? "error" : ""}`} role="status">{runState === "error" ? <span>!</span> : <CheckCircle2 size={17} />}{message}</div>}
      <div className="page-footer-actions">
        <span>计算将执行动态积分、排放判定、质量守恒和工程可信度检查</span>
        <button className="button primary" onClick={runSimulation} disabled={runState === "running" || !runnable.has(project?.process_type ?? "")}><Play size={17} fill="currentColor" /> {runState === "running" ? "正在计算..." : runnable.has(project?.process_type ?? "") ? "运行动态仿真" : "专用模型尚不可运行"}</button>
      </div>
    </div>
  );
}

function ResultPage({ navigate }: { navigate: (page: PageId) => void }) {
  const {
    project,
    influent,
    parameters,
    setParameters,
    simulation,
    setSimulation,
    calibrationSamples,
    setCalibrationSamples
  } = useWorkflow();
  const [calibrationOpen, setCalibrationOpen] = useState(false);
  const [calibrationState, setCalibrationState] = useState<"idle" | "running" | "error" | "success">("idle");
  const [calibrationMessage, setCalibrationMessage] = useState("");
  const [groupId, setGroupId] = useState(project?.plant_name ?? "当前污水厂");
  const [measured, setMeasured] = useState({
    cod_mg_l: "",
    nh4_n_mg_l: "",
    tn_mg_l: "",
    tp_mg_l: "",
    tss_mg_l: ""
  });
  if (!simulation || !influent) {
    return (
      <div className="page empty-state">
        <FlaskConical size={32} />
        <h1>尚无真实仿真结果</h1>
        <p>请先保存项目并在数据录入页运行动态仿真。</p>
        <button className="button primary" onClick={() => navigate("input")}>前往数据录入</button>
      </div>
    );
  }
  const resultRows = [
    { name: "COD", inlet: influent.cod_mg_l, outlet: simulation.effluent.cod_mg_l, limit: simulation.limits.cod_mg_l, key: "cod" },
    { name: "NH₄-N", inlet: influent.nh4_n_mg_l, outlet: simulation.effluent.nh4_n_mg_l, limit: simulation.limits.nh4_n_mg_l, key: "nh4_n" },
    { name: "TN", inlet: influent.tn_mg_l, outlet: simulation.effluent.tn_mg_l, limit: simulation.limits.tn_mg_l, key: "tn" },
    { name: "TP", inlet: influent.tp_mg_l, outlet: simulation.effluent.tp_mg_l, limit: simulation.limits.tp_mg_l, key: "tp" },
    { name: "TSS", inlet: influent.tss_mg_l, outlet: simulation.effluent.tss_mg_l, limit: simulation.limits.tss_mg_l, key: "tss" }
  ]
    .filter((row) => simulation.applicable_indicators?.[row.key] !== false)
    .map((row) => ({ ...row, unit: "mg/L", pass: simulation.compliance[row.key] }));
  const maxValue = Math.max(...resultRows.map((row) => row.inlet));
  const passCount = resultRows.filter((row) => row.pass).length;
  const indicatorCount = resultRows.length;
  const phosphorusApplicable = simulation.applicable_indicators?.tp !== false;
  const complianceReady = simulation.compliance_valid;
  const relevantMappingResiduals = Object.entries(
    simulation.component_mapping.relative_residuals
  ).filter(([key]) => !(simulation.model_id === "ASM1" && key === "tp_mg_l"));
  const maximumMappingResidual = Math.max(
    0,
    ...relevantMappingResiduals.map(([, value]) => Math.abs(value))
  );
  const mappingPassed = maximumMappingResidual <= simulation.quality_thresholds.component_mapping_relative_error;
  const elementBalancePassed = simulation.mass_balance.element_balance_passed;
  const advancedTreatmentActive = Boolean(
    simulation.advanced_treatment_applied
    || (
      parameters
      && (
        (parameters.external_carbon_dose_mg_l ?? 0) > 0
        || (parameters.ferric_chloride_dose_mg_l ?? 0) > 0
        || (parameters.tertiary_filter_solids_capture ?? 0) > 0
      )
    )
  );
  const advancedTreatmentVerified = simulation.reliability.checks["强化处理现场核实"] ?? true;
  const addCalibrationSample = () => {
    if (!parameters) return;
    const values = Object.fromEntries(
      Object.entries(measured)
        .filter(([, value]) => value !== "")
        .map(([key, value]) => [key, Number(value)])
    );
    if (!Object.keys(values).length) {
      setCalibrationState("error");
      setCalibrationMessage("请至少填写一项实测出水指标。");
      return;
    }
    const sample: CalibrationSample = {
      group_id: groupId.trim() || "当前污水厂",
      sample_time: new Date().toISOString(),
      influent,
      measured: values,
      parameters
    };
    const next = [...calibrationSamples, sample];
    setCalibrationSamples(next);
    setCalibrationState("success");
    setCalibrationMessage(`已加入第 ${next.length} 条校准样本；每组五条以上会保留最新日期，全部分组合计至少两条才可通过独立验证。`);
  };
  const runCalibration = async () => {
    if (!project || calibrationSamples.length < 2) {
      setCalibrationState("error");
      setCalibrationMessage("至少需要两条校准样本。");
      return;
    }
    setCalibrationState("running");
    setCalibrationMessage("正在按污水厂分组并保留最新日期作为验证集...");
    try {
      const result = await api.calibrate(project.id, calibrationSamples);
      if (!parameters) throw new Error("当前仿真参数不存在。");
      const calibratedParameters: ProcessParameters = {
        ...parameters,
        cod_kinetic_factor: result.factors.cod,
        nitrification_kinetic_factor: result.factors.nitrification,
        denitrification_kinetic_factor: result.factors.denitrification,
        phosphorus_kinetic_factor: result.factors.phosphorus,
        independent_validation_passed: result.engineering_qualified,
        independent_validation_sample_count: result.engineering_qualified ? result.validation_sample_count : 0,
        independent_validation_nrmse: result.engineering_qualified ? result.validation_objective : null,
        validation_record_id: result.engineering_qualified ? result.validation_record_id : null
      };
      const recalculated = await api.simulate(project.id, influent, calibratedParameters);
      setParameters(calibratedParameters);
      setSimulation(recalculated);
      setCalibrationState("success");
      const failedIndicators = Object.entries(result.validation_indicator_nrmse)
        .filter(([, value]) => value > 0.2)
        .map(([key, value]) => {
          const labels: Record<string, string> = {
            cod_mg_l: "化学需氧量",
            nh4_n_mg_l: "氨氮",
            tn_mg_l: "总氮",
            tp_mg_l: "总磷",
            tss_mg_l: "悬浮物"
          };
          return `${labels[key] ?? key} ${(value * 100).toFixed(1)}%`;
        })
        .join("、");
      setCalibrationMessage(
        result.engineering_qualified
          ? `${result.method}完成：训练、独立验证和现场证据均已通过，验证 ${result.validation_sample_count} 条；当前工况已用完整动态模型复算。`
          : result.validation_passed
          ? `${result.method}的数值验证已通过，但尚未取得工程复核资格：${result.qualification_blockers.join("、")}。当前工况按筛选级复算。`
          : result.improvement_percent > 0
          ? `${result.method}完成，但尚未通过20%逐指标验收。训练 ${result.training_sample_count} 条，验证 ${result.validation_sample_count} 条。${failedIndicators ? `超限：${failedIndicators}。` : "请补充每厂至少两条独立日期数据。"}`
          : `预校准候选参数未降低误差，已保留原参数并完成动态复算。训练 ${result.training_sample_count} 条，验证 ${result.validation_sample_count} 条。`
      );
    } catch (error) {
      setCalibrationState("error");
      setCalibrationMessage(error instanceof Error ? error.message : "校准失败。");
    }
  };
  return (
    <div className="page">
      <PageHeading eyebrow="03 / 模型计算" title="仿真结果" description="查看动态模型预测、污染物去除效果及出水达标情况。"
        action={<div className="heading-actions"><button className="button secondary" onClick={() => setCalibrationOpen((open) => !open)}><SlidersHorizontal size={17} /> 校准模型</button><button className="button primary" onClick={() => navigate("input")}><Play size={17} fill="currentColor" /> 重新计算</button></div>} />
      <div className="run-summary">
        <div><span className="success-pulse" /><p><strong>{!complianceReady ? "计算完成，达标结论待复核" : advancedTreatmentActive && !advancedTreatmentVerified ? "强化处理方案测算完成" : advancedTreatmentActive ? "生化与强化处理计算完成" : "基础生化计算完成"}</strong><small>{new Date(simulation.created_at).toLocaleString("zh-CN")} · 仿真编号 {simulation.simulation_id.slice(0, 8)}</small></p></div>
        <span>计算引擎 <b>{simulation.engine}</b></span>
      </div>
      <div className="result-metrics">
        <div><Gauge size={20} /><span>综合达标率</span><strong>{complianceReady ? `${Math.round(passCount / indicatorCount * 100)}%` : "待判定"}</strong><small>{complianceReady ? `${passCount} / ${indicatorCount} 项达标` : "模型尚未满足正式判定条件"}</small></div>
        <div><Droplets size={20} /><span>预测处理水量</span><strong>{influent.flow_m3_d.toLocaleString()}</strong><small>m³/d</small></div>
        <div><Activity size={20} /><span>估算运行能耗</span><strong>{simulation.energy_kwh_d.toLocaleString()}</strong><small>kWh/d · {simulation.operational_estimate_evidence.energy_calibrated ? "实测校准通过" : "未通过实测校准"}</small></div>
        <div><ClipboardCheck size={20} /><span>估算干污泥产量</span><strong>{simulation.sludge_kg_d.toLocaleString()}</strong><small>kg/d · {simulation.operational_estimate_evidence.sludge_calibrated ? "实测校准通过" : "未通过实测校准"}</small></div>
      </div>
      <section className={`reliability-panel ${simulation.reliability.score >= 60 ? "conditional" : "screening"}`}>
        <div>
          <span>工程可信度</span>
          <strong>{simulation.reliability.score} 分 · {simulation.reliability.level}</strong>
        </div>
        <p>{simulation.reliability.decision}</p>
        <small>尚缺：{simulation.reliability.blockers.join("、") || "无"}</small>
      </section>
      {!phosphorusApplicable && (
        <div className="info-banner treatment-result-banner">
          <CircleHelp size={18} />
          <span>当前采用活性污泥模型一，该模型不包含磷过程；总磷不展示、不参与综合达标率和模型校准。评价总磷请改用活性污泥模型二d或独立除磷模型。</span>
        </div>
      )}
      {advancedTreatmentActive && parameters && (
        <div className="info-banner treatment-result-banner">
          <CheckCircle2 size={18} />
          <span>
            {advancedTreatmentVerified ? "已叠加现场核实的强化处理：" : "已叠加强化处理方案情景："}碳源 {parameters.external_carbon_dose_mg_l} 毫克/升、
            三氯化铁 {parameters.ferric_chloride_dose_mg_l} 毫克/升、
            过滤截留率 {Math.round(parameters.tertiary_filter_solids_capture * 100)}%
          </span>
        </div>
      )}
      {!advancedTreatmentActive && passCount < indicatorCount && (
        <div className="info-banner treatment-result-banner untreated-warning">
          <CircleHelp size={18} />
          <span>
            当前为基础生化段与二沉池预测，未启用外加碳源、化学除磷和三级过滤；
            {phosphorusApplicable ? "总氮、总磷或悬浮物" : "总氮或悬浮物"}超标不代表计算错误，而是当前工艺配置未满足排放限值。
          </span>
          <button onClick={() => navigate("input")}>配置强化处理</button>
        </div>
      )}
      {advancedTreatmentActive && simulation.biological_effluent && (
        <div className="treatment-stage-summary">
          <strong>处理阶段可追溯</strong>
          <span>
            生化段出水：总氮 {simulation.biological_effluent.tn_mg_l}、{phosphorusApplicable ? `总磷 ${simulation.biological_effluent.tp_mg_l}、` : ""}
            悬浮物 {simulation.biological_effluent.tss_mg_l} 毫克/升
          </span>
          <span>
            最终出水：总氮 {simulation.effluent.tn_mg_l}、{phosphorusApplicable ? `总磷 ${simulation.effluent.tp_mg_l}、` : ""}
            悬浮物 {simulation.effluent.tss_mg_l} 毫克/升
          </span>
        </div>
      )}
      <section className="result-section">
        <div className="section-title"><div><h2>进出水对比</h2><p>{simulation.limits.source} · {simulation.limits.basis}</p></div><div className="legend"><span className="inlet-key" />进水 <span className="outlet-key" />预测出水</div></div>
        <div className="result-bars">
          {resultRows.map((row) => (
            <div className="bar-row" key={row.name}>
              <strong>{row.name}</strong>
              <div className="bars">
                <span className="inlet-bar" style={{ width: `${Math.max(8, row.inlet / maxValue * 100)}%` }} />
                <span className="outlet-bar" style={{ width: `${Math.max(3, row.outlet / maxValue * 100)}%` }} />
              </div>
              <span>{row.inlet} → <b>{row.outlet}</b> {row.unit}（限值 {row.limit}）</span>
              <em className={!complianceReady ? "pending" : row.pass ? "pass" : "fail"}>{!complianceReady ? "待判定" : row.pass ? advancedTreatmentActive && !advancedTreatmentVerified ? "情景达标" : "达标" : "超标"}</em>
            </div>
          ))}
        </div>
      </section>
      <section className={`balance-panel ${simulation.mass_balance.passed ? "passed" : "failed"}`}>
        <div>
          <strong>模型可信度检查</strong>
          <span>
            {simulation.mass_balance.passed
              ? simulation.convergence_reached ? "通过" : "基础检查通过"
              : "需要复核"}
          </span>
        </div>
        <p>
          水力闭合{simulation.mass_balance.hydraulic_relative_error <= simulation.quality_thresholds.hydraulic_relative_error ? "通过" : "未通过"}
          {" · "}组分重构{mappingPassed ? "通过" : `偏差 ${Math.round(maximumMappingResidual * 100)}%`}
          {" · "}表观回收{
            !simulation.convergence_reached
              ? "暂不判定"
              : elementBalancePassed ? "通过" : "未通过"
          }
          {" · "}{simulation.convergence_reached ? "达到末端准稳态判定" : "尚未达到末端准稳态判定"}
          {simulation.mass_balance.state_drift_per_d === null
            ? ""
            : `（状态漂移 ${(simulation.mass_balance.state_drift_per_d * 100).toFixed(3)}%/天，阈值 ${(simulation.quality_thresholds.state_drift_per_d * 100).toFixed(3)}%/天）`}
        </p>
        <p className="recovery-detail">
          化学需氧量 {Math.round(simulation.mass_balance.cod_recovery * 100)}%
          {" · "}总氮 {Math.round(simulation.mass_balance.nitrogen_recovery * 100)}%
          {simulation.mass_balance.phosphorus_recovery === null
            ? ""
            : ` · 总磷 ${Math.round(simulation.mass_balance.phosphorus_recovery * 100)}%`}
        </p>
        <p className="recovery-detail">
          自动积分 {simulation.requested_simulation_days ?? simulation.simulation_days} → {simulation.simulation_days} 天
          {" · "}收敛检查 {simulation.convergence_attempts} 轮
          {simulation.effective_kla_d === null ? "" : ` · 有效传氧系数 ${simulation.effective_kla_d.toFixed(2)}/天`}
          {simulation.oxygen_transfer_capacity_kg_d === null ? "" : ` · 供氧能力 ${simulation.oxygen_transfer_capacity_kg_d.toFixed(1)}千克氧/天`}
          {simulation.reactor_ph_used === null ? "" : ` · 反应池酸碱度 ${simulation.reactor_ph_used.toFixed(2)}`}
          {simulation.alkalinity_margin_mg_l_caco3 === null ? "" : ` · 剩余碱度 ${simulation.alkalinity_margin_mg_l_caco3.toFixed(1)}毫克/升`}
          {simulation.estimated_srt_d === null ? "" : ` · 估算污泥龄 ${simulation.estimated_srt_d.toFixed(1)}天`}
          {simulation.clarifier_surface_overflow_m_d === null ? "" : ` · 二沉池表面负荷 ${simulation.clarifier_surface_overflow_m_d.toFixed(1)}米/天`}
        </p>
        <p className="recovery-detail">
          计算追溯 {simulation.manifest.request_sha256.slice(0, 12)}
          {` · 程序 ${simulation.manifest.application_version}`}
          {` · QSDsan ${simulation.manifest.qsdsan_version}`}
          {simulation.manifest.validation_record_id ? ` · 验证记录 ${simulation.manifest.validation_record_id}` : ""}
        </p>
      </section>
      {calibrationOpen && (
      <section className="form-section calibration-panel">
          <div className="section-title"><div><h2>实测出水动态校准</h2><p>降阶模型仅生成初值，训练、候选筛选和独立验证均使用完整动态模型</p></div><span>{calibrationSamples.length} 条样本</span></div>
          <label className="field"><span>污水厂分组</span><input value={groupId} onChange={(event) => setGroupId(event.target.value)} /></label>
          <div className="indicator-grid">
            {[
              ["化学需氧量", "cod_mg_l"],
              ["氨氮", "nh4_n_mg_l"],
              ["总氮", "tn_mg_l"],
              ["总磷", "tp_mg_l"],
              ["悬浮物", "tss_mg_l"]
            ].filter(([, key]) => key !== "tp_mg_l" || phosphorusApplicable).map(([label, key]) => (
              <label className="indicator-field" key={key}><span>实测{label}</span><div><input value={measured[key as keyof typeof measured]} onChange={(event) => setMeasured((current) => ({ ...current, [key]: event.target.value }))} /><b>mg/L</b></div></label>
            ))}
          </div>
          <div className="calibration-actions"><button className="button secondary" onClick={addCalibrationSample}><Plus size={16} /> 加入当前样本</button><button className="button primary" onClick={runCalibration} disabled={calibrationState === "running"}><SlidersHorizontal size={16} /> {calibrationState === "running" ? "动态校准中..." : "执行分组动态校准"}</button></div>
          {calibrationMessage && <p className={calibrationState === "error" ? "form-message error" : "form-message"}>{calibrationMessage}</p>}
        </section>
      )}
      <div className="result-note"><span>!</span><p><strong>模型提示</strong>{simulation.warnings[0] ?? "计算已完成，建议使用独立时段实测数据复核。"}</p><button className="text-button" onClick={() => navigate("report")}>查看评估报告 <ArrowRight size={15} /></button></div>
    </div>
  );
}

function ReportPage() {
  const { project, simulation } = useWorkflow();
  const [format, setFormat] = useState<"pdf" | "excel">("pdf");
  const [reportName, setReportName] = useState("深水海纳示范污水厂工艺建模评估报告");
  const [reportState, setReportState] = useState<"idle" | "running" | "error" | "success">("idle");
  const [reportMessage, setReportMessage] = useState("");
  const generateReport = async () => {
    if (!project || !simulation) {
      setReportState("error");
      setReportMessage("请先保存项目并完成一次真实仿真。");
      return;
    }
    setReportState("running");
    setReportMessage("正在生成报告...");
    try {
      const result = await api.createReport(project.id, simulation.simulation_id, format, reportName);
      const anchor = document.createElement("a");
      anchor.href = apiUrl(result.download_url);
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setReportState("success");
      setReportMessage(`报告已生成：${result.filename}`);
    } catch (error) {
      setReportState("error");
      setReportMessage(error instanceof Error ? error.message : "报告生成失败。");
    }
  };
  return (
    <div className="page">
      <PageHeading eyebrow="04 / 成果输出" title="报告导出" description="整理项目参数、仿真结果和评估结论，生成标准化成果文件。" />
      <div className="report-layout">
        <section className="form-section">
          <div className="section-title"><div><h2>报告配置</h2><p>选择报告内容与输出格式</p></div></div>
          <label className="field"><span>报告名称</span><input value={reportName} onChange={(event) => setReportName(event.target.value)} /></label>
          <fieldset className="check-list">
            <legend>包含章节</legend>
            {["项目与工艺概况", "进水水质与运行参数", "稳态仿真结果", "污染物去除与达标分析", "模型校准指标", "结论与优化建议"].map((item) => (
              <label key={item}><input type="checkbox" defaultChecked /><span><CheckCircle2 size={15} />{item}</span></label>
            ))}
          </fieldset>
          <fieldset className="format-picker">
            <legend>输出格式</legend>
            <label className={format === "pdf" ? "selected" : ""}><input type="radio" name="format" value="pdf" checked={format === "pdf"} onChange={() => setFormat("pdf")} /><FileText size={22} /><span><strong>PDF 报告</strong><small>适合汇报与归档</small></span></label>
            <label className={format === "excel" ? "selected" : ""}><input type="radio" name="format" value="excel" checked={format === "excel"} onChange={() => setFormat("excel")} /><FileSpreadsheet size={22} /><span><strong>Excel 数据</strong><small>适合复核与二次分析</small></span></label>
          </fieldset>
          <button className="button primary full-button" onClick={generateReport} disabled={reportState === "running"}><FileDown size={18} /> {reportState === "running" ? "正在生成..." : `生成并下载${format === "pdf" ? " PDF 报告" : " Excel 数据"}`}</button>
          {reportMessage && <p className={reportState === "error" ? "form-message error" : "form-message"}>{reportMessage}</p>}
        </section>
        <aside className="report-preview">
          <div className="preview-toolbar"><span>报告预览</span><small>A4 · 共 12 页</small></div>
          <div className="paper">
            <AppIcon />
            <span>QSDsan 标准工作流</span>
            <h2>污水工艺流程<br />建模评估报告</h2>
            <div className="paper-line" />
            <strong>{project?.plant_name ?? "尚未关联项目"}</strong>
            <p>{project?.process_type ?? "待选择"} 工艺 · {simulation?.model_id ?? "待计算"} 动态模型</p>
            <footer><span>编制日期</span><b>{new Date().toLocaleDateString("zh-CN")}</b></footer>
          </div>
        </aside>
      </div>
    </div>
  );
}

export function App() {
  const [page, setPage] = useState<PageId>(readPage);
  const [menuOpen, setMenuOpen] = useState(false);
  const [project, setProject] = useState<ProjectRecord | null>(() => {
    const saved = localStorage.getItem("qinglan-project");
    return saved ? JSON.parse(saved) as ProjectRecord : null;
  });
  const [influent, setInfluent] = useState<WaterQuality | null>(null);
  const [parameters, setParameters] = useState<ProcessParameters | null>(null);
  const [simulation, setSimulation] = useState<SimulationResult | null>(null);
  const [calibrationSamples, setCalibrationSamples] = useState<CalibrationSample[]>([]);

  useEffect(() => {
    const onHashChange = () => setPage(readPage());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    if (project) localStorage.setItem("qinglan-project", JSON.stringify(project));
  }, [project]);
  useEffect(() => {
    if (!project?.id) return;
    let cancelled = false;
    Promise.all([
      api.getProject(project.id),
      api.listMeasurements(project.id),
      api.listSimulations(project.id),
      api.listSimulationJobs(project.id)
    ]).then(([savedProject, measurements, simulations, jobs]) => {
      if (cancelled) return;
      setProject(savedProject);
      if (measurements[0]) setInfluent(measurements[0].water_quality);
      if (simulations[0]) setSimulation(simulations[0]);
      const latestRequest = jobs.find((job) => job.request_payload)?.request_payload;
      if (latestRequest) {
        setParameters(latestRequest.parameters);
        setInfluent(latestRequest.influent);
      }
    }).catch(() => {
      localStorage.removeItem("qinglan-project");
      if (!cancelled) setProject(null);
    });
    return () => {
      cancelled = true;
    };
  }, [project?.id]);

  const currentLabel = useMemo(() => pages.find((item) => item.id === page)?.label, [page]);
  const navigate = (next: PageId) => {
    window.location.hash = `/${next}`;
    setPage(next);
    setMenuOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (page === "home") {
    return <HomePage navigate={navigate} />;
  }

  return (
    <WorkflowContext.Provider value={{
      project,
      setProject,
      influent,
      setInfluent,
      parameters,
      setParameters,
      simulation,
      setSimulation,
      calibrationSamples,
      setCalibrationSamples
    }}>
    <main className="app-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`} aria-label="功能导航">
        <div className="brand"><AppIcon /><div><strong>清澜智评</strong><small>WATER PROCESS STUDIO</small></div></div>
        <button className="mobile-close" onClick={() => setMenuOpen(false)} aria-label="关闭菜单"><X size={20} /></button>
        <nav>
          <span className="nav-label">主要功能</span>
          {pages.map((item) => (
            <button className={page === item.id ? "active" : ""} key={item.id} onClick={() => navigate(item.id)}>
              <item.icon size={18} /><span>{item.label}</span>{page === item.id && <i />}
            </button>
          ))}
        </nav>
        <div className="sidebar-project">
          <span>当前项目</span>
          <strong>{project?.plant_name ?? "尚未创建项目"}</strong>
          <small><span /> {project?.process_type ?? "待选择"} · 动态模型</small>
        </div>
        <footer><button><CircleHelp size={17} /> 使用帮助</button><span>v0.3.0 · 工程复核版</span></footer>
      </aside>
      {menuOpen && <button className="scrim" aria-label="关闭菜单" onClick={() => setMenuOpen(false)} />}

      <section className="workspace">
        <div className="mobile-bar">
          <button onClick={() => setMenuOpen(true)} aria-label="打开菜单"><Menu size={21} /></button>
          <span>{currentLabel}</span>
          <AppIcon />
        </div>
        {page === "project" && <ProjectPage />}
        {page === "input" && <InputPage navigate={navigate} />}
        {page === "result" && <ResultPage navigate={navigate} />}
        {page === "report" && <ReportPage />}
      </section>
    </main>
    </WorkflowContext.Provider>
  );
}
