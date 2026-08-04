import apiClient from './api'
import type { HistoryListResponse, HistoryDetailResponse } from '@/types'

export const historyService = {
  async getHistories(
    page: number = 1,
    pageSize: number = 10,
    source?: string,
    dateFrom?: string,
    dateTo?: string,
    scrapeType?: string
  ): Promise<HistoryListResponse> {
    const params: any = { page, page_size: pageSize }
    if (source && source !== 'Tất cả') params.source = source.toLowerCase()
    if (dateFrom) params.date_from = dateFrom
    if (dateTo) params.date_to = dateTo
    if (scrapeType && scrapeType !== 'all') params.scrape_type = scrapeType

    const response = await apiClient.get('/histories', { params })
    return response.data
  },

  async getHistoryDetail(
    historyId: number,
    page: number = 1,
    pageSize: number = 100
  ): Promise<HistoryDetailResponse> {
    const response = await apiClient.get(`/histories/${historyId}`, {
      params: { page, page_size: pageSize }
    })
    return response.data
  },

  async deleteHistory(historyId: number): Promise<void> {
    await apiClient.delete(`/histories/${historyId}`)
  },

  async exportHistory(historyId: number): Promise<any[]> {
    const response = await apiClient.get(`/export/${historyId}`)
    return response.data
  },

  async getApiData(params: {
    mode: 'latest' | 'all' | 'filter'
    source?: string
    history_id?: number
    date_from?: string
    date_to?: string
  }): Promise<any[]> {
    const response = await apiClient.get('/api', { params })
    return response.data
  }
}
