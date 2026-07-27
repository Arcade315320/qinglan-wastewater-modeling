from app.models.schemas import ReportRequest, ReportResult


def create_report_stub(payload: ReportRequest) -> ReportResult:
    return ReportResult(
        project_id=payload.project_id,
        status="planned",
        report_format=payload.report_format,
        message="Report export endpoint reserved. PDF/Excel generation will be implemented after simulation and calibration data stabilize.",
    )
