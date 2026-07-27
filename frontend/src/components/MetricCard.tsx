type MetricCardProps = {
  label: string;
  value: string;
  unit: string;
};

export function MetricCard({ label, value, unit }: MetricCardProps) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{unit}</small>
    </article>
  );
}
