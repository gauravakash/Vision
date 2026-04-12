import { Link } from 'react-router-dom'
import { Zap, RefreshCw, FileText } from 'lucide-react'
import { useCurrentSpikes, useSchedulerStatus } from '../../hooks/useAgent'
import { usePendingDrafts } from '../../hooks/useDrafts'
import { useDesks, useToggleDeskMode } from '../../hooks/useDesks'
import { useState } from 'react'
import toast from 'react-hot-toast'

export default function Topbar() {
  const { data: spikes = [] } = useCurrentSpikes()
  const { data: pending = [] } = usePendingDrafts()
  const { data: desks = [] } = useDesks()
  const { data: scheduler } = useSchedulerStatus()
  const toggleMode = useToggleDeskMode()

  const pendingCount = Array.isArray(pending) ? pending.length : 0
  const spikeCount = Array.isArray(spikes) ? spikes.length : 0

  // Determine global mode from desks (majority wins)
  const autoCount = desks.filter((d) => d.mode === 'auto').length
  const globalMode = autoCount >= desks.length / 2 ? 'auto' : 'manual'

  const [switching, setSwitching] = useState(false)

  async function handleGlobalToggle(targetMode) {
    if (switching || targetMode === globalMode) return
    setSwitching(true)
    try {
      await Promise.all(desks.map((d) => toggleMode.mutateAsync({ id: d.id, mode: targetMode })))
      toast.success(`All desks set to ${targetMode} mode`)
    } catch {
      // individual errors handled in hook
    } finally {
      setSwitching(false)
    }
  }

  return (
    <header
      className="flex items-center justify-between px-5 py-3 border-b bg-card"
      style={{ borderColor: 'rgba(0,0,0,0.07)', height: 56 }}
    >
      {/* Left: Logo */}
      <div className="flex items-center gap-2">
        <div
          className="w-7 h-7 rounded-md flex items-center justify-center font-bold text-white text-xs"
          style={{ background: '#FF5C1A', fontFamily: '"Clash Display"' }}
        >
          X
        </div>
        <span className="font-display font-semibold text-text-primary text-sm">X Agent</span>
      </div>

      {/* Center: Mode toggle */}
      <div
        className="flex items-center rounded-xl p-0.5"
        style={{ background: '#F2EDE4', border: '1px solid rgba(0,0,0,0.07)' }}
      >
        {['auto', 'manual'].map((mode) => (
          <button
            key={mode}
            onClick={() => handleGlobalToggle(mode)}
            disabled={switching}
            className={[
              'px-4 py-1.5 rounded-lg text-sm font-medium transition-all capitalize',
              globalMode === mode
                ? 'bg-white text-orange shadow-sm'
                : 'text-text-muted hover:text-text-secondary',
            ].join(' ')}
          >
            {mode} Mode
          </button>
        ))}
      </div>

      {/* Right: indicators */}
      <div className="flex items-center gap-3">
        {/* Spike alert */}
        {spikeCount > 0 && (
          <div
            className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold animate-pulse"
            style={{ background: 'rgba(192,57,43,0.12)', color: '#C0392B', border: '1px solid rgba(192,57,43,0.25)' }}
          >
            <Zap size={12} />
            {spikeCount} spike{spikeCount !== 1 ? 's' : ''}
          </div>
        )}

        {/* Live indicator */}
        <div
          className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium"
          style={{ background: 'rgba(26,122,74,0.08)', color: '#1A7A4A', border: '1px solid rgba(26,122,74,0.2)' }}
        >
          <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse-slow" />
          Live
        </div>

        {/* Pending drafts */}
        {pendingCount > 0 && (
          <Link
            to="/review"
            className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold hover:opacity-80 transition-opacity"
            style={{ background: 'rgba(198,123,0,0.1)', color: '#C67B00', border: '1px solid rgba(198,123,0,0.2)' }}
          >
            <FileText size={12} />
            {pendingCount} pending
          </Link>
        )}
      </div>
    </header>
  )
}
