import { NavLink, useLocation } from 'react-router-dom'
import { Home, FileText, Users, LayoutGrid, Clock, Settings, Zap, MessageSquare } from 'lucide-react'
import { useDesks } from '../../hooks/useDesks'
import { usePendingDrafts } from '../../hooks/useDrafts'
import { useCurrentSpikes } from '../../hooks/useAgent'
import { useQuery } from '@tanstack/react-query'
import { getOpportunities } from '../../api/client'

const NAV = [
  { path: '/',            icon: Home,          label: 'Home' },
  { path: '/review',      icon: FileText,      label: 'Review' },
  { path: '/accounts',    icon: Users,         label: 'Accounts' },
  { path: '/desks',       icon: LayoutGrid,    label: 'Desks' },
  { path: '/history',     icon: Clock,         label: 'History' },
  { path: '/engagement',  icon: MessageSquare, label: 'Engagement' },
]

export default function Sidebar({ collapsed, onToggle }) {
  const { data: desks = [] } = useDesks()
  const { data: pending = [] } = usePendingDrafts()
  const { data: spikes = [] } = useCurrentSpikes()
  const location = useLocation()

  const { data: pendingOpps = [] } = useQuery({
    queryKey: ['opportunities', '', 'pending'],
    queryFn: () => getOpportunities({ status: 'pending' }),
    staleTime: 30000,
    refetchInterval: 60000,
  })

  const spikeTagSet = new Set(spikes.map((s) => s.desk_id))
  const pendingCount = Array.isArray(pending) ? pending.length : 0
  const oppCount = Array.isArray(pendingOpps) ? pendingOpps.length : 0

  return (
    <aside
      className="flex flex-col h-full border-r"
      style={{
        width: collapsed ? 56 : 240,
        background: '#FAF7F2',
        borderColor: 'rgba(0,0,0,0.07)',
        transition: 'width 0.2s ease',
        minWidth: collapsed ? 56 : 240,
      }}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-5 border-b" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
        <div
          className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center font-bold text-white text-sm"
          style={{ background: '#FF5C1A', fontFamily: '"Clash Display"' }}
        >
          X
        </div>
        {!collapsed && (
          <span className="font-display font-semibold text-text-primary text-base tracking-tight">
            Agent
          </span>
        )}
      </div>

      {/* Main nav */}
      <nav className="flex flex-col gap-1 px-2 pt-3 flex-1">
        {NAV.map(({ path, icon: Icon, label }) => {
          const isActive = path === '/' ? location.pathname === '/' : location.pathname.startsWith(path)
          return (
            <NavLink
              key={path}
              to={path}
              className={[
                'flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium transition-all duration-150',
                isActive
                  ? 'nav-active'
                  : 'text-text-secondary hover:bg-white hover:text-text-primary',
              ].join(' ')}
            >
              <Icon size={17} className="flex-shrink-0" />
              {!collapsed && (
                <>
                  <span className="flex-1">{label}</span>
                  {label === 'Review' && pendingCount > 0 && (
                    <span
                      className="text-xs font-mono px-1.5 py-0.5 rounded-full text-white"
                      style={{ background: '#C0392B', fontSize: 10, minWidth: 18, textAlign: 'center' }}
                    >
                      {pendingCount}
                    </span>
                  )}
                  {label === 'Engagement' && oppCount > 0 && (
                    <span
                      className="text-xs font-mono px-1.5 py-0.5 rounded-full text-white"
                      style={{ background: '#FF5C1A', fontSize: 10, minWidth: 18, textAlign: 'center' }}
                    >
                      {oppCount}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          )
        })}

        {/* Desks section */}
        {!collapsed && (
          <div className="mt-4">
            <p className="px-3 pb-1.5 text-xs font-semibold tracking-widest text-text-muted uppercase font-sans">
              Desks
            </p>
            <div className="flex flex-col gap-0.5">
              {desks.map((desk) => (
                <div
                  key={desk.id}
                  className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg hover:bg-white transition-colors"
                >
                  <span
                    className="flex-shrink-0 w-2 h-2 rounded-full"
                    style={{ background: desk.color }}
                  />
                  <span className="text-sm text-text-secondary flex-1 truncate">{desk.name}</span>
                  {spikeTagSet.has(desk.id) && (
                    <span
                      className="text-xs font-bold px-1.5 rounded"
                      style={{ background: 'rgba(192,57,43,0.12)', color: '#C0392B', fontSize: 9 }}
                    >
                      SPIKE
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </nav>

      {/* Settings */}
      <div className="px-2 pb-3 border-t pt-3" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
        <NavLink
          to="/settings"
          className={({ isActive }) => [
            'flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium transition-all',
            isActive ? 'nav-active' : 'text-text-secondary hover:bg-white',
          ].join(' ')}
        >
          <Settings size={17} className="flex-shrink-0" />
          {!collapsed && <span>Settings</span>}
        </NavLink>
      </div>
    </aside>
  )
}
