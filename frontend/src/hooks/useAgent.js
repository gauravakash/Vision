import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  runDesk, runAll, getActivity, getCurrentSpikes,
  getNextRuns, getSchedulerStatus, runSpikeCheck,
  getDeskTrendsLive, getDesks,
} from '../api/client'
import toast from 'react-hot-toast'

export function useRunDesk() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ deskId, data }) => runDesk(deskId, data),
    onMutate: () => toast.loading('Running desk…', { id: 'run-desk' }),
    onSuccess: (result) => {
      toast.dismiss('run-desk')
      const count = result?.drafts_created ?? 0
      toast.success(count > 0 ? `${count} draft${count !== 1 ? 's' : ''} ready` : 'Run complete — no drafts generated')
      qc.invalidateQueries({ queryKey: ['drafts', 'pending'] })
      qc.invalidateQueries({ queryKey: ['drafts', 'stats'] })
      qc.invalidateQueries({ queryKey: ['activity'] })
      qc.invalidateQueries({ queryKey: ['trends'] })
    },
    onError: (err) => {
      toast.dismiss('run-desk')
      toast.error(err.message)
    },
  })
}

export function useRunAll() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mode) => runAll(mode),
    onSuccess: () => {
      toast.success('All desks queued — drafts arriving shortly')
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['drafts', 'pending'] })
        qc.invalidateQueries({ queryKey: ['activity'] })
        qc.invalidateQueries({ queryKey: ['trends'] })
      }, 5000)
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useActivity(params) {
  return useQuery({
    queryKey: ['activity', params],
    queryFn: () => getActivity(params),
    refetchInterval: 5000,
    staleTime: 3000,
  })
}

export function useCurrentSpikes() {
  return useQuery({
    queryKey: ['spikes'],
    queryFn: getCurrentSpikes,
    refetchInterval: 15000,
    staleTime: 10000,
  })
}

export function useNextRuns() {
  return useQuery({
    queryKey: ['scheduler', 'next-runs'],
    queryFn: getNextRuns,
    refetchInterval: 30000,
    staleTime: 15000,
  })
}

export function useSchedulerStatus() {
  return useQuery({
    queryKey: ['scheduler', 'status'],
    queryFn: getSchedulerStatus,
    refetchInterval: 10000,
    staleTime: 8000,
  })
}

export function useRunSpikeCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: runSpikeCheck,
    onSuccess: (result) => {
      toast.success(`Spike check: ${result?.spikes_found ?? 0} spike(s) found`)
      qc.invalidateQueries({ queryKey: ['spikes'] })
      qc.invalidateQueries({ queryKey: ['activity'] })
      qc.invalidateQueries({ queryKey: ['trends'] })
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useRefreshAllTrends() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      // Fetch all desks first, then refresh trends for each active one
      let desks = await getDesks()
      if (desks?.items) desks = desks.items
      if (!Array.isArray(desks)) desks = []
      const activeDesks = desks.filter((d) => d.is_active)
      let refreshed = 0
      for (const desk of activeDesks) {
        try {
          await getDeskTrendsLive(desk.id, true, 10)
          refreshed++
        } catch {
          // Continue with remaining desks
        }
      }
      return { refreshed, total: activeDesks.length }
    },
    onMutate: () => toast.loading('Fetching fresh trends…', { id: 'refresh-trends' }),
    onSuccess: (result) => {
      toast.dismiss('refresh-trends')
      toast.success(`Trends refreshed for ${result.refreshed}/${result.total} desk(s)`)
      qc.invalidateQueries({ queryKey: ['trends'] })
      qc.invalidateQueries({ queryKey: ['spikes'] })
      qc.invalidateQueries({ queryKey: ['activity'] })
    },
    onError: (err) => {
      toast.dismiss('refresh-trends')
      toast.error(err.message)
    },
  })
}
