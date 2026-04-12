export default function Spinner({ size = 20, color = '#FF5C1A' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin"
      style={{ color }}
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

export function SkeletonBlock({ className = '' }) {
  return <div className={`skeleton ${className}`} style={{ minHeight: 16 }} />
}

export function SkeletonCard() {
  return (
    <div className="bg-card rounded-2xl border p-5 space-y-3" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <div className="flex items-center gap-3">
        <SkeletonBlock className="w-8 h-8 rounded-full" />
        <div className="flex-1 space-y-2">
          <SkeletonBlock className="h-4 w-1/2" />
          <SkeletonBlock className="h-3 w-1/3" />
        </div>
      </div>
      <SkeletonBlock className="h-3 w-full" />
      <SkeletonBlock className="h-3 w-4/5" />
      <SkeletonBlock className="h-3 w-3/5" />
    </div>
  )
}
