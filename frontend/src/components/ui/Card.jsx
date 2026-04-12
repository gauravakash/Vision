export default function Card({ children, className = '', style, onClick }) {
  return (
    <div
      className={[
        'bg-card rounded-2xl border shadow-sm',
        onClick && 'cursor-pointer hover:shadow-md transition-shadow',
        className,
      ].join(' ')}
      style={{ borderColor: 'rgba(0,0,0,0.07)', ...style }}
      onClick={onClick}
    >
      {children}
    </div>
  )
}
