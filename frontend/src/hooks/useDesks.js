import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getDesks, getDeskById, createDesk, updateDesk, deleteDesk, toggleDeskMode, getDeskTrends, getDeskTrendsLive } from '../api/client'
import toast from 'react-hot-toast'

export function useDesks(params) {
  return useQuery({
    queryKey: ['desks', params],
    queryFn: () => getDesks(params),
    staleTime: 30000,
    select: (data) => {
      // API returns { items: [...], total: N } — normalize to flat array
      if (Array.isArray(data)) return data
      if (data?.items && Array.isArray(data.items)) return data.items
      return []
    },
  })
}

export function useDeskById(id) {
  return useQuery({
    queryKey: ['desks', id],
    queryFn: () => getDeskById(id),
    enabled: !!id,
  })
}

export function useCreateDesk() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createDesk,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['desks'] })
      toast.success('Desk created')
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useUpdateDesk() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }) => updateDesk(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['desks'] })
      toast.success('Desk updated')
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useDeleteDesk() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: deleteDesk,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['desks'] })
      toast.success('Desk removed')
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useToggleDeskMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, mode }) => toggleDeskMode(id, mode),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['desks'] })
      qc.invalidateQueries({ queryKey: ['scheduler'] })
      toast.success(`Desk set to ${vars.mode} mode`)
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useDeskTrends(deskId) {
  return useQuery({
    queryKey: ['trends', deskId],
    queryFn: () => getDeskTrends(deskId),
    enabled: !!deskId,
    refetchInterval: 60000,
    staleTime: 30000,
  })
}

export function useRefreshDeskTrends() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (deskId) => getDeskTrendsLive(deskId, true, 10),
    onSuccess: (_, deskId) => {
      qc.invalidateQueries({ queryKey: ['trends', deskId] })
      qc.invalidateQueries({ queryKey: ['spikes'] })
    },
    onError: (err) => toast.error(err.message),
  })
}
