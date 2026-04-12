import { memo } from 'react'
import { Edit2, LogIn, Trash2, CheckCircle, XCircle, Clock } from 'lucide-react'

function ConnectionBadge({ account }) {
  if (account.is_connected && account.is_session_valid) {
    return (
      <span className="flex items-center gap-1 text-xs font-medium" style={{ color: '#1A7A4A' }}>
        <CheckCircle size={12} /> Connected
      </span>
    )
  }
  if (account.is_connected && !account.is_session_valid) {
    return (
      <span className="flex items-center gap-1 text-xs font-medium" style={{ color: '#C0392B' }}>
        <XCircle size={12} /> Expired
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-xs font-medium" style={{ color: '#C67B00' }}>
      <Clock size={12} /> Not connected
    </span>
  )
}

const AccountCard = memo(function AccountCard({ account, onEdit, onLogin, onDelete }) {
  return (
    <div className="bg-card rounded-2xl border p-4" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <div className="flex items-start gap-3">
        {/* Avatar */}
        <div
          className="w-11 h-11 rounded-full flex items-center justify-center text-white font-bold text-base flex-shrink-0"
          style={{ background: account.color, fontFamily: '"Clash Display"' }}
        >
          {account.initials}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <div>
              <p className="font-semibold text-sm text-text-primary truncate">{account.name}</p>
              <p className="font-mono text-xs text-text-muted">{account.handle}</p>
            </div>
            <ConnectionBadge account={account} />
          </div>

          {/* Desk badges */}
          {account.desk_names?.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {account.desk_names.map((name) => (
                <span key={name} className="px-2 py-0.5 rounded-full text-xs bg-cream text-text-secondary border border-border">
                  {name}
                </span>
              ))}
            </div>
          )}

          {/* Personality chips */}
          <div className="flex gap-1.5 mt-2">
            {[account.tone, account.style, account.stance].map((v) => v && (
              <span key={v} className="px-2 py-0.5 rounded-full text-xs font-medium"
                style={{ background: 'rgba(255,92,26,0.08)', color: '#FF5C1A', border: '1px solid rgba(255,92,26,0.15)' }}>
                {v}
              </span>
            ))}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between mt-3 pt-2.5 border-t" style={{ borderColor: 'rgba(0,0,0,0.06)' }}>
            <span className="text-xs text-text-muted">{account.daily_limit} posts/day</span>
            <div className="flex items-center gap-1">
              <button onClick={() => onEdit?.(account)} className="p-1.5 rounded-lg text-text-muted hover:bg-cream hover:text-text-primary transition-colors">
                <Edit2 size={14} />
              </button>
              <button onClick={() => onLogin?.(account)} className="p-1.5 rounded-lg text-text-muted hover:bg-orange/10 hover:text-orange transition-colors">
                <LogIn size={14} />
              </button>
              <button onClick={() => onDelete?.(account)} className="p-1.5 rounded-lg text-text-muted hover:bg-red-50 hover:text-error transition-colors">
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
})

export default AccountCard
