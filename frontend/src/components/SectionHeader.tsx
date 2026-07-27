type SectionHeaderProps = {
  eyebrow: string;
  title: string;
  description: string;
};

export function SectionHeader({ eyebrow, title, description }: SectionHeaderProps) {
  return (
    <header className="section-header">
      <span>{eyebrow}</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}
