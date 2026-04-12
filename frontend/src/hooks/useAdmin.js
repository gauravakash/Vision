import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getAdminCosts,
  getAdminDatabaseStats,
  getAdminHealth,
  getAdminLogs,
  getAdminMetrics,
  postAdminClearCaches,
  postAdminCleanupOldData,
  postAdminTestNotification,
} from '../api/client'

export function useHealth() {
  return useQuery({
    queryKey: ['admin', 'health'],
    queryFn: getAdminHealth,
    refetchInterval: 30_000,
    retry: false,
  })
}

export function useMetrics() {
  return useQuery({
    queryKey: ['admin', 'metrics'],
    queryFn: getAdminMetrics,
    refetchInterval: 15_000,
  })
}

export function useCosts() {
  return useQuery({
    queryKey: ['admin', 'costs'],
    queryFn: getAdminCosts,
    refetchInterval: 60_000,
  })
}

export function useDatabaseStats() {
  return useQuery({
    queryKey: ['admin', 'database-stats'],
    queryFn: getAdminDatabaseStats,
    refetchInterval: 60_000,
  })
}

export function useLogs(params) {
  return useQuery({
    queryKey: ['admin', 'logs', params],
    queryFn: () => getAdminLogs(params),
    refetchInterval: 10_000,
  })
}

export function useClearCaches() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: postAdminClearCaches,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin'] }),
  })
}

export function useCleanupData() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: postAdminCleanupOldData,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin'] }),
  })
}

export function useTestNotification() {
  return useMutation({ mutationFn: postAdminTestNotification })
}
