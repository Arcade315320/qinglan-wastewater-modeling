export type ProjectRecord = {
  id: string;
  name: string;
  plant_name: string;
  process_type: string;
  owner: string | null;
  description: string | null;
  created_at: string;
};

export type WaterQuality = {
  flow_m3_d: number;
  cod_mg_l: number;
  bod_mg_l: number | null;
  nh4_n_mg_l: number;
  tn_mg_l: number;
  tp_mg_l: number;
  tss_mg_l: number;
  ph: number;
  temperature_c: number;
  soluble_cod_mg_l?: number | null;
  vfa_as_cod_mg_l?: number | null;
  nitrate_n_mg_l?: number | null;
  nitrite_n_mg_l?: number | null;
  orthophosphate_p_mg_l?: number | null;
};

export type ProcessParameters = {
  process_type: string;
  model_type: "ASM1" | "ASM2d";
  hrt_h: number;
  srt_d: number;
  internal_recycle_ratio: number;
  sludge_recycle_ratio: number;
  aeration_power_kw: number;
  aeration_hours_d: number;
  mixing_power_kw: number;
  pumping_power_kw: number;
  aerobic_do_mg_l: number;
  aerobic_kla_d: number | null;
  oxygen_transfer_efficiency_kg_o2_kwh: number;
  alkalinity_mg_l_caco3: number;
  reactor_volume_m3: number | null;
  anaerobic_volume_m3: number | null;
  anoxic_volume_m3: number | null;
  aerobic_volume_m3: number | null;
  clarifier_surface_area_m2: number | null;
  clarifier_depth_m: number | null;
  settler_v_max_m_d: number | null;
  settler_v_max_practical_m_d: number | null;
  settler_tss_threshold_mg_l: number | null;
  waste_sludge_flow_m3_d: number | null;
  mixed_liquor_tss_mg_l: number | null;
  return_sludge_tss_mg_l: number | null;
  waste_sludge_tss_mg_l: number | null;
  step_feed_fractions?: number[] | null;
  simulation_days: number;
  auto_convergence: boolean;
  max_simulation_days: number;
  convergence_tolerance_per_d: number;
  cod_kinetic_factor: number;
  nitrification_kinetic_factor: number;
  denitrification_kinetic_factor: number;
  phosphorus_kinetic_factor: number;
  external_carbon_dose_mg_l: number;
  ferric_chloride_dose_mg_l: number;
  tertiary_filter_solids_capture: number;
  effluent_standard: "grade_a" | "grade_b" | "custom";
  commissioned_before_2006: boolean;
  assessment_date: string;
  operating_data_source: "measured" | "published" | "design" | "assumed";
  advanced_treatment_verified: boolean;
  independent_validation_passed: boolean;
  independent_validation_sample_count: number;
  independent_validation_nrmse: number | null;
  oxidation_ditch_channel_count?: number | null;
  oxidation_ditch_loop_volume_m3?: number | null;
  sbr_reactor_count?: number | null;
  sbr_cycle_h?: number | null;
  sbr_fill_h?: number | null;
  sbr_anoxic_h?: number | null;
  sbr_aerobic_h?: number | null;
  sbr_settle_h?: number | null;
  sbr_decant_h?: number | null;
  sbr_decant_fraction?: number | null;
  mbr_membrane_area_m2?: number | null;
  mbr_design_flux_l_m2_h?: number | null;
  mbr_recovery?: number | null;
  mbr_air_scour_power_kw?: number | null;
};

export type EffluentLimits = {
  cod_mg_l: number;
  nh4_n_mg_l: number;
  tn_mg_l: number;
  tp_mg_l: number;
  tss_mg_l: number;
  basis: string;
  source: string;
};

export type SimulationResult = {
  simulation_id: string;
  created_at: string;
  project_id: string;
  model_id: string;
  engine: string;
  effluent: {
    cod_mg_l: number;
    nh4_n_mg_l: number;
    tn_mg_l: number;
    tp_mg_l: number;
    tss_mg_l: number;
  };
  biological_effluent: {
    cod_mg_l: number;
    nh4_n_mg_l: number;
    tn_mg_l: number;
    tp_mg_l: number;
    tss_mg_l: number;
  } | null;
  advanced_treatment_applied: boolean;
  limits: EffluentLimits;
  reliability: {
    level: string;
    score: number;
    decision: string;
    checks: Record<string, boolean>;
    blockers: string[];
  };
  removal_rates: Record<string, number>;
  energy_kwh_d: number;
  sludge_kg_d: number;
  compliance: Record<string, boolean>;
  applicable_indicators: Record<string, boolean>;
  compliance_valid: boolean;
  model_note: string;
  mass_balance: {
    passed: boolean;
    hydraulic_relative_error: number;
    cod_recovery: number;
    nitrogen_recovery: number;
    phosphorus_recovery: number | null;
    state_drift_per_d: number | null;
    notes: string[];
  };
  component_mapping: {
    method: string;
    concentrations_mg_l: Record<string, number>;
    reconstructed: Record<string, number>;
    relative_residuals: Record<string, number>;
  };
  convergence_reached: boolean;
  simulation_days: number;
  requested_simulation_days: number | null;
  convergence_attempts: number;
  effective_kla_d: number | null;
  oxygen_transfer_capacity_kg_d: number | null;
  estimated_srt_d: number | null;
  clarifier_surface_overflow_m_d: number | null;
  assumptions: string[];
  warnings: string[];
};

export type CalibrationSample = {
  group_id: string;
  sample_time: string;
  influent: WaterQuality;
  measured: Partial<SimulationResult["effluent"]>;
  parameters: ProcessParameters;
};

const baseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export function apiUrl(path: string): string {
  return `${baseUrl}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = formatApiError(body?.detail, response.status);
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function formatApiError(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const error = item as { loc?: unknown; msg?: unknown; type?: unknown; ctx?: Record<string, unknown> };
      const location = Array.isArray(error.loc)
        ? error.loc
          .filter((part) => part !== "body")
          .map((part) => API_FIELD_NAMES[String(part)] ?? String(part))
          .join(" → ")
        : "";
      const message = translateValidationMessage(error);
      return [`${location ? `${location}：` : ""}${message}`];
    });
    if (messages.length) return messages.join("；");
  }
  return `接口返回 ${status}`;
}

const API_FIELD_NAMES: Record<string, string> = {
  influent: "进水水质",
  parameters: "运行参数",
  max_simulation_days: "自动收敛最大时长",
  simulation_days: "初始动态积分时长",
  operating_data_source: "运行参数来源",
  custom_limits: "自定义排放限值"
};

function translateValidationMessage(error: {
  msg?: unknown;
  type?: unknown;
  ctx?: Record<string, unknown>;
}): string {
  const type = typeof error.type === "string" ? error.type : "";
  if (type === "missing") return "此项为必填项";
  if (type === "less_than_equal") return `不能大于${error.ctx?.le ?? "规定上限"}`;
  if (type === "greater_than_equal") return `不能小于${error.ctx?.ge ?? "规定下限"}`;
  if (type === "greater_than") return `必须大于${error.ctx?.gt ?? "零"}`;
  const message = typeof error.msg === "string" ? error.msg : "输入值不符合要求";
  return message.replace(/^Value error,\s*/i, "");
}

export const api = {
  createProject(payload: Omit<ProjectRecord, "id" | "created_at">) {
    return request<ProjectRecord>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  createMeasurement(projectId: string, waterQuality: WaterQuality) {
    return request("/api/measurements", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        location: "influent",
        water_quality: waterQuality
      })
    });
  },
  simulate(
    projectId: string,
    influent: WaterQuality,
    parameters: ProcessParameters,
    customLimits?: EffluentLimits
  ) {
    type SimulationJob = {
      id: string;
      status: "queued" | "running" | "completed" | "failed";
      result: SimulationResult | null;
      error: string | null;
    };
    return request<SimulationJob>("/api/simulate/jobs", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        influent,
        parameters,
        custom_limits: customLimits
      })
    }).then(async (job) => {
      for (let attempt = 0; attempt < 450; attempt += 1) {
        const current = attempt === 0
          ? job
          : await request<SimulationJob>(`/api/simulate/jobs/${job.id}`);
        if (current.status === "completed" && current.result) return current.result;
        if (current.status === "failed") {
          throw new Error(current.error ?? "动态仿真失败。");
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
      throw new Error("动态仿真超过十五分钟，请检查组分和初始状态后重新运行。");
    });
  },
  calibrate(projectId: string, samples: CalibrationSample[]) {
    return request<{
      factors: Record<string, number>;
      training_sample_count: number;
      validation_sample_count: number;
      validation_objective: number | null;
      validation_passed: boolean;
      calibration_passed: boolean;
      validation_indicator_nrmse: Record<string, number>;
      improvement_percent: number;
      warnings: string[];
      method: string;
    }>("/api/calibrate/model", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        samples,
        max_iterations: 8,
        validation_fraction: 0.2
      })
    });
  },
  async importCalibration(projectId: string, file: File) {
    const form = new FormData();
    form.append("project_id", projectId);
    form.append("file", file);
    const response = await fetch(apiUrl("/api/calibrate/import"), {
      method: "POST",
      body: form
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : `接口返回 ${response.status}`);
    }
    return response.json() as Promise<{
      imported_count: number;
      skipped_count: number;
      groups: string[];
      samples: CalibrationSample[];
      warnings: string[];
      quality_score: number;
      readiness: string;
      field_coverage: Record<string, number>;
      duplicate_key_count: number;
      recommendations: string[];
    }>;
  },
  createReport(
    projectId: string,
    simulationId: string,
    format: "pdf" | "excel",
    reportName: string
  ) {
    return request<{
      status: string;
      filename: string;
      download_url: string;
      message: string;
    }>("/api/reports", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        simulation_id: simulationId,
        report_format: format,
        report_name: reportName
      })
    });
  }
};
