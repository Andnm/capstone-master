import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { CrawlHistory } from '@/types'
import { historyService } from '@/services/history'

export const useHistoryStore = defineStore('history', () => {
  const histories = ref<CrawlHistory[]>([])
  const currentHistory = ref<CrawlHistory | null>(null)
  const currentHistoryData = ref<any[]>([])
  
  const totalCount = ref(0)
  const currentPage = ref(1)
  const pageSize = ref(10)
  const totalPages = ref(0)
  
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchHistories(
    page: number = 1,
    source?: string,
    dateFrom?: string,
    dateTo?: string,
    scrapeType?: string
  ) {
    loading.value = true
    error.value = null
    
    try {
      const response = await historyService.getHistories(page, pageSize.value, source, dateFrom, dateTo, scrapeType)
      histories.value = response.items
      totalCount.value = response.total
      currentPage.value = response.page
      totalPages.value = response.total_pages
    } catch (err: any) {
      error.value = err.message || 'Failed to fetch histories'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchHistoryDetail(historyId: number, page: number = 1) {
    loading.value = true
    error.value = null
    
    try {
      const response = await historyService.getHistoryDetail(historyId, page, 100)
      currentHistory.value = response.history
      currentHistoryData.value = response.data
      return response
    } catch (err: any) {
      error.value = err.message || 'Failed to fetch history detail'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function deleteHistory(historyId: number) {
    loading.value = true
    error.value = null
    
    try {
      await historyService.deleteHistory(historyId)
      histories.value = histories.value.filter((h) => h.id !== historyId)
      totalCount.value--
    } catch (err: any) {
      error.value = err.message || 'Failed to delete history'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function exportHistory(historyId: number) {
    try {
      const data = await historyService.exportHistory(historyId)
      return data
    } catch (err: any) {
      error.value = err.message || 'Failed to export history'
      throw err
    }
  }

  function clearCurrentHistory() {
    currentHistory.value = null
    currentHistoryData.value = []
  }

  return {
    histories,
    currentHistory,
    currentHistoryData,
    totalCount,
    currentPage,
    pageSize,
    totalPages,
    loading,
    error,
    fetchHistories,
    fetchHistoryDetail,
    deleteHistory,
    exportHistory,
    clearCurrentHistory
  }
})
