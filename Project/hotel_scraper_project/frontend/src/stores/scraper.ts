import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ScrapeProgress, LinkInfo, DateRange } from '@/types'

export const useScraperStore = defineStore('scraper', () => {
  const isScraing = ref(false)
  const currentProgress = ref(0)
  const totalProgress = ref(0)
  const currentMessage = ref('')
  const currentHotelName = ref('')
  const currentRow = ref(0)
  
  const results = ref<any[]>([])
  const errors = ref<any[]>([])
  const historyId = ref<number | null>(null)
  
  const links = ref<LinkInfo[]>([])
  const dateRanges = ref<DateRange[]>([])
  const selectedSource = ref('booking')

  const progressPercentage = computed(() => {
    if (totalProgress.value === 0) return 0
    return Math.round((currentProgress.value / totalProgress.value) * 100)
  })

  const successCount = computed(() => results.value.length)
  const errorCount = computed(() => errors.value.length)

  function setLinks(newLinks: LinkInfo[]) {
    links.value = newLinks
  }

  function setDateRanges(ranges: DateRange[]) {
    dateRanges.value = ranges
  }

  function setSource(source: string) {
    selectedSource.value = source
  }

  function updateProgress(data: ScrapeProgress) {
    currentMessage.value = data.message

    switch (data.type) {
      case 'started':
        isScraing.value = true
        currentProgress.value = 0
        totalProgress.value = data.total || 0
        historyId.value = data.history_id || null
        results.value = []
        errors.value = []
        break

      case 'progress':
        currentProgress.value = data.current || 0
        totalProgress.value = data.total || 0
        currentHotelName.value = data.hotel_name || ''
        currentRow.value = data.row || 0
        break

      case 'success':
        if (data.row) {
          const linkIndex = links.value.findIndex((l) => l.row === data.row)
          if (linkIndex !== -1) {
            links.value[linkIndex].status = '✅ Thành công'
          }
        }
        break

      case 'error':
        if (data.row) {
          const linkIndex = links.value.findIndex((l) => l.row === data.row)
          if (linkIndex !== -1) {
            links.value[linkIndex].status = '❌ Lỗi'
          }
          errors.value.push({
            row: data.row,
            message: data.message
          })
        }
        break

      case 'completed':
        isScraing.value = false
        currentProgress.value = totalProgress.value
        results.value = data.results || []
        errors.value = data.errors || []
        historyId.value = data.history_id || null
        break
    }
  }

  function reset() {
    isScraing.value = false
    currentProgress.value = 0
    totalProgress.value = 0
    currentMessage.value = ''
    currentHotelName.value = ''
    currentRow.value = 0
    results.value = []
    errors.value = []
    historyId.value = null
  }

  function resetLinks() {
    links.value = []
    dateRanges.value = []
  }

  return {
    isScraing,
    currentProgress,
    totalProgress,
    progressPercentage,
    currentMessage,
    currentHotelName,
    currentRow,
    results,
    errors,
    successCount,
    errorCount,
    historyId,
    links,
    dateRanges,
    selectedSource,
    setLinks,
    setDateRanges,
    setSource,
    updateProgress,
    reset,
    resetLinks
  }
}, {
  persist: {
    key: 'scraper-state',
    paths: ['results', 'links', 'dateRanges', 'selectedSource', 'historyId']
  }
})
