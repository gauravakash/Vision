const variants = {
  primary:  'bg-orange text-white hover:bg-orange/90 border border-orange',
  outline:  'bg-transparent text-orange border border-orange/50 hover:bg-orange/5',
  ghost:    'bg-transparent text-text-secondary border border-border hover:bg-cream',
  danger:   'bg-error text-white hover:bg-error/90 border border-error',
  success:  'bg-success text-white hover:bg-success/90 border border-success',
  muted:    'bg-cream text-text-secondary border border-border hover:bg-cream/70',
}

const sizes = {
  xs: 'px-2 py-1 text-xs rounded-md gap-1',
  sm: 'px-3 py-1.5 text-sm rounded-lg gap-1.5',
  md: 'px-4 py-2 text-sm rounded-lg gap-2',
  lg: 'px-5 py-2.5 text-base rounded-xl gap-2',
}

export default function Button({
  children,
  variant = 'primary',
  size = 'md',
  loading = false,
  icon,
  className = '',
  ...props
}) {
  return (
    <button
      className={[
        'inline-flex items-center justify-center font-medium transition-all duration-150',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'font-sans',
        variants[variant] || variants.primary,
        sizes[size] || sizes.md,
        className,
      ].join(' ')}
      disabled={loading || props.disabled}
      {...props}
    >
      {loading ? (
        <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
      ) : icon ? (
        <span className="flex items-center">{icon}</span>
      ) : null}
      {children}
    </button>
  )
}
