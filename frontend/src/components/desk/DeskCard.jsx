import { memo, useCallback } from 'react'
import { Play, Zap, MoreHorizontal } from 'lucide-react'
import { LineChart, Line, ResponsiveContainer, Tooltip } from 'recharts'
import { useDeskTrends, useToggleDeskMode } from '../../hooks/useDesks'
import { useRunDesk } from '../../hooks/useAgent'
import { timeAgo, formatVolume, spikeColor } from '../../utils/formatters'

function ModeChip({ mode, deskId }) {
  const toggle = useToggleDeskMode()
  const isAuto = mode === 'auto'
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        toggle.mutate({ id: deskId, mode: isAuto ? 'manual' : 'auto' })
      }}
      className="px-2 py-0.5 rounded-full text-xs font-medium transition-colors"
      style={
        isAuto
          ? { background: 'rgba(26,122,74,0.12)', color: '#1A7A4A', border: '1px solid rgba(26,122,74,0.2)' }
          : { background: 'rgba(90,90,90,0.08)', color: '#5C4D42', border: '1px solid rgba(0,0,0,0.1)' }
      }
    >
      {isAuto ? '⚡ Auto' : '⏸ Manual'}
    </button>
  )
}

const DeskCard = memo(function DeskCard({ desk, isSpiking = false, onEdit }) {
  const { data: trends = [] } = useDeskTrends(desk.id)
  const runDesk = useRunDesk()

  // Build sparkline data from trends
  const sparkData = trends.slice(0, 12).reverse().map((t, i) => ({
    i,
    v: t.volume_numeric || 0,
    status: t.status,
  }))

  const topTrend = trends[0]
  const lineColor = isSpiking ? '#C0392B' : (topTrend?.status === 'rising' ? '#FF5C1A' : '#1A7A4A')

  const handleRun = useCallback((e) => {
    e.stopPropagation()
    runDesk.mutate({ deskId: desk.id, data: {} })
  }, [desk.id, runDesk])

  return (
    <div
      className="bg-card rounded-2xl border overflow-hidden hover:shadow-md transition-shadow"
      style={{ borderColor: isSpiking ? 'rgba(192,57,43,0.4)' : 'rgba(0,0,0,0.07)' }}
    >
      {/* Header */}
      <div className="px-4 pt-4 pb-3 flex items-start justify-between">
        <div className="flex items-center gap-2.5">
          <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: desk.color }} />
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-display font-semibold text-sm text-text-primary">{desk.name}</h3>
              {isSpiking && (
                <span
                  className="text-xs font-bold px-1.5 py-0.5 rounded animate-pulse"
                  style={{ background: 'rgba(192,57,43,0.12)', color: '#C0392B', fontSize: 9 }}
                >
                  SPIKE
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ModeChip mode={desk.mode} deskId={desk.id} />
          {onEdit && (
            <button onClick={(e) => { e.stopPropagation(); onEdit(desk) }} className="p-1 text-text-muted hover:text-text-secondary">
              <MoreHorizontal size={15} />
            </button>
          )}
        </div>
      </div>

      {/* Sparkline */}
      {sparkData.length > 0 ? (
        <div style={{ height: 56, padding: '0 8px' }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparkData}>
              <Line
                type="monotone"
                dataKey="v"
                stroke={lineColor}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
              <Tooltip
                contentStyle={{ background: '#1A1208', border: 'none', borderRadius: 8, color: '#fff', fontSize: 11 }}
                formatter={(v) => [formatVolume(v), 'Volume']}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="h-14 flex items-center justify-center">
          <span className="text-xs text-text-muted">No trend data yet</span>
        </div>
      )}

      {/* Topics */}
      <div className="px-4 py-3 border-t" style={{ borderColor: 'rgba(0,0,0,0.06)' }}>
        {trends.slice(0, 3).map((t, i) => (
          <div
            key={t.id || i}
            className="flex items-center gap-2 py-1 rounded-lg px-1.5 -mx-1.5"
            style={{ background: t.status === 'spiking' ? 'rgba(192,57,43,0.05)' : 'transparent' }}
          >
            <span className="font-mono text-xs text-text-muted w-4">{i + 1}</span>
            <span className="flex-1 text-xs text-text-primary font-medium truncate">{t.topic_tag}</span>
            {t.volume_display && (
              <span className="font-mono text-xs text-text-muted">{t.volume_display}</span>
            )}
            {t.spike_percent > 0 && (
              <span className="font-mono text-xs font-bold" style={{ color: lineColor }}>
                +{Math.round(t.spike_percent)}%
              </span>
            )}
          </div>
        ))}
        {trends.length === 0 && (
          <div className="text-xs text-text-muted py-1">
            {desk.topics?.slice(0, 4).join(' · ')}
          </div>
        )}
      </div>

      {/* Footer */}
      <div
        className="flex items-center justify-between px-4 py-2.5 border-t"
        style={{ borderColor: 'rgba(0,0,0,0.06)', background: '#FDFAF6' }}
      >
        <span className="text-xs text-text-muted">
          {trends[0] ? timeAgo(trends[0].snapshot_time) : 'No data'}
        </span>
        <button
          onClick={handleRun}
          disabled={runDesk.isPending}
          className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-semibold transition-colors"
          style={
            isSpiking
              ? { background: 'rgba(192,57,43,0.1)', color: '#C0392B', border: '1px solid rgba(192,57,43,0.2)' }
              : { background: 'rgba(255,92,26,0.1)', color: '#FF5C1A', border: '1px solid rgba(255,92,26,0.2)' }
          }
        >
          {isSpiking ? <Zap size={12} /> : <Play size={11} />}
          {isSpiking ? 'Draft Now' : 'Run'}
        </button>
      </div>
    </div>
  )
})

export default DeskCard
