export default function Badge({ children, color, bg, border, className = '' }) {
  const style = color
    ? { color, background: bg || color + '18', border: `1px solid ${border || color + '40'}` }
    : {}

  return (
    <span
      className={[
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium font-sans',
        !color && 'bg-cream text-text-secondary border border-border',
        className,
      ].join(' ')}
      style={style}
    >
      {children}
    </span>
  )
}
