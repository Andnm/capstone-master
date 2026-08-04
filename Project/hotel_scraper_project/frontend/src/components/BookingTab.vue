<template>
  <div class="booking-tab-container">
    <!-- Stepper -->
    <div class="stepper-wrapper">
      <Steps :model="stepItems" :readonly="false" :activeStep="activeStepIndex" class="custom-steps">
        <template #item="{ item, index }">
          <div 
            class="step-item-container" 
            :class="{ 
              'step-active': index === activeStepIndex,
              'step-completed': index < activeStepIndex,
              'step-clickable': canAccessStep(index),
              'step-disabled': !canAccessStep(index)
            }"
            @click="goToStep(index)"
          >
            <div class="step-number-circle" :class="{ 'active': index === activeStepIndex, 'completed': index < activeStepIndex, 'disabled': !canAccessStep(index) }">
              <i v-if="index < activeStepIndex" class="pi pi-check"></i>
              <span v-else>{{ index + 1 }}</span>
            </div>
            <div class="step-label-container">
              <span class="step-label" :class="{ 'disabled-label': !canAccessStep(index) }">{{ item.label }}</span>
              <i 
                class="pi pi-info-circle info-icon" 
                v-tooltip.top="item.tooltip"
              ></i>
            </div>
          </div>
        </template>
      </Steps>
    </div>

    <!-- Step Content -->
    <div class="step-content-wrapper">
      <!-- Step 1: Upload/Load Data -->
      <div v-show="activeStepIndex === 0" class="step-content">
        <Card>
          <template #content>
            <FileUploader @data-loaded="handleDataLoaded" />
          </template>
        </Card>
      </div>

      <!-- Step 2: Select Market & Scrape Type -->
      <div v-show="activeStepIndex === 1" class="step-content">
        <Card v-if="markets.length > 0">
          <template #content>
            <div class="grid">
              <div class="col-12 md:col-6">
                <MarketSelector 
                  :markets="markets"
                  :linksByMarket="linksByMarket"
                  v-model="selectedMarket"
                />
              </div>
              <div class="col-12 md:col-6">
                <ScrapeTypeSelector v-model="scrapeType" />
              </div>
            </div>
            <div class="mt-4 flex justify-content-end">
              <Button label="Tiếp tục" icon="pi pi-arrow-right" @click="nextStep" :disabled="!scrapeType" severity="primary" />
            </div>
          </template>
        </Card>
      </div>

      <!-- Step 3: Links Display -->
      <div v-show="activeStepIndex === 2" class="step-content">
        <Card>
          <template #title>
            Xem trước links ({{ validLinksCount }} hợp lệ, {{ invalidLinksCount }} không hợp lệ)
            <span v-if="selectedMarket && selectedMarket !== 'all'" class="ml-2">
              <Tag severity="info">Market: {{ selectedMarket }}</Tag>
            </span>
          </template>
          <template #content>
            <div v-if="displayLinks.length === 0" class="text-center py-4">
              <i class="pi pi-inbox" style="font-size: 3rem; color: var(--text-color-secondary)"></i>
              <p class="mt-3" style="color: var(--text-color-secondary)">Không có link nào để hiển thị</p>
            </div>
            
            <template v-else>
            <!-- Invalid Links Table -->
            <div v-if="invalidLinks.length > 0" class="mb-4">
              <h4 class="section-subtitle">❌ Links không hợp lệ ({{ invalidLinksCount }})</h4>
              <DataTable 
                :value="invalidLinks" 
                :paginator="true" 
                :rows="10" 
                :rowsPerPageOptions="[10, 25, 50, 100]"
                paginatorTemplate="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink JumpToPageInput CurrentPageReport RowsPerPageDropdown"
                currentPageReportTemplate="Trang {currentPage}/{totalPages} | Hiển thị {first}-{last} / {totalRecords} links"
                responsiveLayout="scroll"
                severity="danger"
              >
                <Column field="market" header="Market" :style="{ width: '120px' }">
                  <template #body="slotProps">
                    <Tag severity="info">{{ slotProps.data.market }}</Tag>
                  </template>
                </Column>
                <Column field="hotel_name" header="Tên khách sạn" :style="{ width: '200px' }">
                  <template #body="slotProps">
                    <span v-if="slotProps.data.hotel_name">{{ truncate(slotProps.data.hotel_name, 50) }}</span>
                    <span v-else style="color: var(--text-color-secondary); font-style: italic;">Không có</span>
                  </template>
                </Column>
                <Column field="cell_value" header="Tên/Giá trị">
                  <template #body="slotProps">
                    {{ truncate(slotProps.data.cell_value, 60) }}
                  </template>
                </Column>
                <Column field="link" header="Link">
                  <template #body="slotProps">
                    <span class="invalid-link-text">
                      {{ truncate(slotProps.data.link, 80) }}
                    </span>
                  </template>
                </Column>
                <Column field="status" header="Lý do" :style="{ width: '200px' }">
                  <template #body="slotProps">
                    <Tag severity="danger">❌ Không hợp lệ</Tag>
                  </template>
                </Column>
              </DataTable>
              <div class="mt-3">
                <Button 
                  label="Tải xuống Excel (Links không hợp lệ)" 
                  icon="pi pi-download"
                  @click="downloadInvalidLinksExcel"
                  class="p-button-danger"
                  size="small"
                />
              </div>
            </div>

            <!-- Valid Links Table -->
            <div v-if="validLinks.length > 0">
              <h4 class="section-subtitle">✅ Links hợp lệ ({{ validLinksCount }})</h4>
              <DataTable 
                :value="validLinks" 
                :paginator="true" 
                :rows="10" 
                :rowsPerPageOptions="[10, 25, 50, 100]"
                paginatorTemplate="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink JumpToPageInput CurrentPageReport RowsPerPageDropdown"
                currentPageReportTemplate="Trang {currentPage}/{totalPages} | Hiển thị {first}-{last} / {totalRecords} links"
                responsiveLayout="scroll"
              >
                <Column field="market" header="Market" :style="{ width: '120px' }">
                  <template #body="slotProps">
                    <Tag severity="info">{{ slotProps.data.market }}</Tag>
                  </template>
                </Column>
                <Column field="hotel_name" header="Tên khách sạn" :style="{ width: '200px' }">
                  <template #body="slotProps">
                    <span v-if="slotProps.data.hotel_name">{{ truncate(slotProps.data.hotel_name, 50) }}</span>
                    <span v-else style="color: var(--text-color-secondary); font-style: italic;">Không có</span>
                  </template>
                </Column>
                <Column field="cell_value" header="Tên/Giá trị">
                  <template #body="slotProps">
                    {{ truncate(slotProps.data.cell_value, 60) }}
                  </template>
                </Column>
                <Column field="link" header="Link">
                  <template #body="slotProps">
                    <a :href="slotProps.data.link" target="_blank" class="link-text">
                      {{ truncate(slotProps.data.link, 80) }}
                    </a>
                  </template>
                </Column>
                <Column field="status" header="Trạng thái" :style="{ width: '150px' }">
                  <template #body="slotProps">
                    <Tag v-if="slotProps.data.status" :severity="getStatusSeverity(slotProps.data.status)">
                      {{ slotProps.data.status }}
                    </Tag>
                    <Tag v-else severity="info">Chưa cào</Tag>
                  </template>
                </Column>
              </DataTable>
            </div>
            <div class="mt-4 flex justify-content-end gap-2">
              <Button label="Quay lại" icon="pi pi-arrow-left" @click="previousStep" severity="secondary" />
              <Button label="Tiếp tục" icon="pi pi-arrow-right" @click="nextStep" :disabled="validLinksCount === 0" severity="primary" />
            </div>
            </template>
          </template>
        </Card>
      </div>

      <!-- Step 4: Date Range Picker -->
      <div v-show="activeStepIndex === 3" class="step-content">
        <Card>
          <template #content>
            <DateRangePicker @date-ranges-updated="handleDateRangesUpdated" />
            <div class="mt-4 flex justify-content-end gap-2">
              <Button label="Quay lại" icon="pi pi-arrow-left" @click="previousStep" severity="secondary" />
              <Button label="Tiếp tục" icon="pi pi-arrow-right" @click="nextStep" :disabled="scraperStore.dateRanges.length === 0" severity="primary" />
            </div>
          </template>
        </Card>
      </div>

      <!-- Step 5: Start Scraping -->
      <div v-show="activeStepIndex === 4" class="step-content">
        <Card>
          <template #content>
            <div class="text-center mb-4">
              <h3>Sẵn sàng bắt đầu cào dữ liệu</h3>
              <p class="text-muted">
                {{ validLinksCount }} links hợp lệ × {{ scraperStore.dateRanges.length }} cặp ngày = 
                {{ validLinksCount * scraperStore.dateRanges.length }} requests
              </p>
              <p class="text-muted">
                Hình thức: <strong>{{ scrapeType === 'info' ? 'Thông tin' : 'Giá' }}</strong>
              </p>
            </div>
            <div class="flex justify-content-center gap-2">
              <Button label="Quay lại" icon="pi pi-arrow-left" @click="previousStep" severity="secondary" />
              <Button 
                :label="isStarting ? 'Đang kết nối...' : `Bắt đầu ${scrapeType === 'info' ? 'cào thông tin' : 'cào giá'}`"
                :icon="isStarting ? 'pi pi-spinner pi-spin' : 'pi pi-play'"
                class="p-button-lg"
                :disabled="scraperStore.isScraing || isStarting"
                @click="startScrapingAndNext"
                severity="primary"
                size="large"
              />
            </div>
          </template>
        </Card>
      </div>

      <!-- Step 6: Results -->
      <div v-show="activeStepIndex === 5" class="step-content">
        <!-- Progress Display -->
        <ProgressDisplay v-if="scraperStore.isScraing" />

        <!-- Results Display -->
        <Card v-if="scraperStore.results.length > 0 && !scraperStore.isScraing">
          <template #title>
            ✅ Kết quả ({{ scraperStore.successCount }} thành công, {{ scraperStore.errorCount }} lỗi)
          </template>
      <template #content>
        <DataTable 
          :value="scraperStore.results" 
          :paginator="true" 
          :rows="20" 
          :rowsPerPageOptions="[20, 50, 100, 200]"
          paginatorTemplate="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink JumpToPageInput CurrentPageReport RowsPerPageDropdown"
          currentPageReportTemplate="Trang {currentPage}/{totalPages} | Hiển thị {first}-{last} / {totalRecords} kết quả"
          responsiveLayout="scroll" 
          scrollable 
          scrollHeight="600px"
        >
          <!-- Info Mode Columns -->
          <template v-if="scrapeType === 'info'">
            <Column field="Ngày cào" header="Ngày cào" :style="{ width: '110px' }" frozen></Column>
            <Column field="Giờ cào" header="Giờ cào" :style="{ width: '100px' }" frozen></Column>
            <Column field="Check in" header="Check in" :style="{ width: '110px' }"></Column>
            <Column field="Check out" header="Check out" :style="{ width: '110px' }"></Column>
            <Column field="Tên khách sạn" header="Khách sạn" :style="{ minWidth: '200px' }"></Column>
            <Column field="Link khách sạn" header="Link" :style="{ width: '100px' }">
              <template #body="slotProps">
                <a :href="slotProps.data['Link khách sạn']" target="_blank">
                  <Button icon="pi pi-external-link" text size="small" />
                </a>
              </template>
            </Column>
            <Column field="Số lượng review" header="Số review" :style="{ width: '100px' }"></Column>
            <Column field="Điểm review" header="Điểm" :style="{ width: '80px' }"></Column>
            <Column field="Các tiện nghi được ưa chuộng nhất" header="Tiện nghi" :style="{ minWidth: '200px' }">
              <template #body="slotProps">
                <div 
                  class="text-ellipsis" 
                  v-tooltip.top="slotProps.data['Các tiện nghi được ưa chuộng nhất']"
                  style="cursor: help;"
                >
                  {{ slotProps.data['Các tiện nghi được ưa chuộng nhất'] }}
                </div>
              </template>
            </Column>
            <Column field="Tên hạng phòng" header="Loại phòng" :style="{ minWidth: '200px' }"></Column>
            <Column field="Số lượng người" header="Số người" :style="{ width: '90px' }"></Column>
            <Column field="Giường" header="Giường" :style="{ minWidth: '150px' }"></Column>
            <Column field="Diện tích phòng" header="Diện tích" :style="{ width: '100px' }"></Column>
            <Column field="Các lựa chọn" header="Các lựa chọn" :style="{ minWidth: '250px' }">
              <template #body="slotProps">
                <div 
                  class="text-ellipsis" 
                  v-tooltip.top="slotProps.data['Các lựa chọn']"
                  style="cursor: help;"
                >
                  {{ slotProps.data['Các lựa chọn'] }}
                </div>
              </template>
            </Column>
          </template>

          <!-- Price Mode Columns -->
          <template v-else>
            <Column field="Ngày cào" header="Ngày cào" :style="{ width: '110px' }" frozen></Column>
            <Column field="Giờ cào" header="Giờ cào" :style="{ width: '100px' }" frozen></Column>
            <Column field="Check in" header="Check in" :style="{ width: '110px' }"></Column>
            <Column field="Check out" header="Check out" :style="{ width: '110px' }"></Column>
            <Column field="Tên khách sạn" header="Khách sạn" :style="{ minWidth: '200px' }"></Column>
            <Column field="Tên hạng phòng" header="Loại phòng" :style="{ minWidth: '200px' }"></Column>
            <Column field="Số lượng người" header="Số người" :style="{ width: '90px' }"></Column>
            <Column field="Giá sau giảm" header="Giá sau giảm" :style="{ width: '140px' }">
              <template #body="slotProps">
                {{ formatPrice(slotProps.data['Giá sau giảm']) }}
              </template>
            </Column>
            <Column field="Giá gốc" header="Giá gốc" :style="{ width: '140px' }">
              <template #body="slotProps">
                {{ formatPrice(slotProps.data['Giá gốc']) }}
              </template>
            </Column>
            <Column field="Giảm giá" header="Giảm giá" :style="{ width: '100px' }">
              <template #body="slotProps">
                <Tag v-if="slotProps.data['Giảm giá']" severity="success">
                  {{ slotProps.data['Giảm giá'] }}
                </Tag>
              </template>
            </Column>
          </template>
        </DataTable>

        <div class="mt-4 flex gap-2">
          <Button 
            label="Tải xuống Excel" 
            icon="pi pi-download"
            @click="downloadExcel"
            severity="primary"
          />
          <Button 
            label="💾 Lưu lịch sử" 
            icon="pi pi-save"
            @click="saveHistory"
            severity="primary"
            v-if="scraperStore.historyId === null"
          />
          <Button 
            v-else
            label="Đã lưu vào database" 
            icon="pi pi-check"
            disabled
            severity="primary"
          />
        </div>
      </template>
    </Card>

        <!-- Errors Display -->
        <Card v-if="scraperStore.errors.length > 0 && !scraperStore.isScraing" class="mt-4">
          <template #title>❌ Danh sách lỗi ({{ scraperStore.errorCount }})</template>
          <template #content>
            <DataTable 
              :value="scraperStore.errors" 
              :paginator="true" 
              :rows="10" 
              :rowsPerPageOptions="[10, 20, 50, 100]"
              paginatorTemplate="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink JumpToPageInput CurrentPageReport RowsPerPageDropdown"
              currentPageReportTemplate="Trang {currentPage}/{totalPages} | Hiển thị {first}-{last} / {totalRecords} lỗi"
              responsiveLayout="scroll"
            >
              <Column field="Tên" header="Tên"></Column>
              <Column field="Link" header="Link">
                <template #body="slotProps">
                  <a :href="slotProps.data.Link" target="_blank" class="link-text">
                    {{ truncate(slotProps.data.Link, 80) }}
                  </a>
                </template>
              </Column>
              <Column field="Lỗi" header="Lỗi">
                <template #body="slotProps">
                  <div 
                    class="text-ellipsis" 
                    v-tooltip.top="slotProps.data.Lỗi"
                    style="cursor: help; max-width: 400px;"
                  >
                    {{ slotProps.data.Lỗi }}
                  </div>
                </template>
              </Column>
            </DataTable>
            <div class="mt-3">
              <Button 
                label="Tải xuống Excel (Danh sách lỗi)" 
                icon="pi pi-download"
                @click="downloadErrorsExcel"
                class="p-button-danger"
                size="small"
              />
            </div>
          </template>
        </Card>

        <div class="mt-4 flex justify-content-start" v-if="!scraperStore.isScraing">
          <Button label="Cào lại" icon="pi pi-refresh" @click="resetToStep1" severity="secondary" />
        </div>
      </div>
    </div>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useToast } from 'primevue/usetoast'
import { useScraperStore } from '@/stores/scraper'
import { scraperWebSocket } from '@/services/websocket'
import type { LinkInfo, DateRange } from '@/types'
import Steps from 'primevue/steps'
import FileUploader from './FileUploader.vue'
import DateRangePicker from './DateRangePicker.vue'
import ProgressDisplay from './ProgressDisplay.vue'
import SavedSourcesManager from './SavedSourcesManager.vue'
import ScrapeTypeSelector from './ScrapeTypeSelector.vue'
import MarketSelector from './MarketSelector.vue'

const scraperStore = useScraperStore()
const toast = useToast()

// Loading state for immediate UI feedback
const isStarting = ref(false)

// Stepper state
const activeStepIndex = ref(0)

// Step items with tooltips
const stepItems = ref([
  { 
    label: 'Chọn nguồn dữ liệu',
    tooltip: 'Upload file Excel chứa danh sách link khách sạn hoặc chọn từ nguồn đã lưu'
  },
  { 
    label: 'Hình thức cào',
    tooltip: 'Chọn market và hình thức cào (thông tin khách sạn hoặc giá phòng)'
  },
  { 
    label: 'Xem trước links',
    tooltip: 'Kiểm tra danh sách links hợp lệ và không hợp lệ trước khi cào'
  },
  { 
    label: 'Chọn ngày',
    tooltip: 'Chọn các cặp ngày nhận phòng và trả phòng để cào dữ liệu'
  },
  { 
    label: 'Bắt đầu cào',
    tooltip: 'Xác nhận thông tin và bắt đầu quá trình cào dữ liệu'
  },
  { 
    label: 'Kết quả',
    tooltip: 'Xem kết quả cào, tải xuống Excel hoặc lưu vào database'
  }
])

// Stepper navigation functions
function canAccessStep(index: number): boolean {
  if (index === 0) return true
  if (index === 1) return dataLoaded.value
  if (index === 2) return scrapeType.value !== null
  if (index === 3) return displayLinks.value.length > 0 && scrapeType.value !== null
  if (index === 4) return scraperStore.dateRanges.length > 0
  if (index === 5) return scraperStore.results.length > 0 || scraperStore.isScraing
  return false
}

function goToStep(index: number) {
  if (canAccessStep(index)) {
    activeStepIndex.value = index
  }
}

function nextStep() {
  if (activeStepIndex.value < stepItems.value.length - 1) {
    activeStepIndex.value++
  }
}

function previousStep() {
  if (activeStepIndex.value > 0) {
    activeStepIndex.value--
  }
}

function resetToStep1() {
  activeStepIndex.value = 0
  // Reset data
  dataLoaded.value = false
  allLinks.value = []
  markets.value = []
  selectedMarket.value = null
  scrapeType.value = null
  scraperStore.reset()
}

// State for markets and scrape type
const dataLoaded = ref(false)
const allLinks = ref<LinkInfo[]>([])
const markets = ref<string[]>([])
const selectedMarket = ref<string | null>(null)
const scrapeType = ref<'info' | 'price' | null>(null)
const linksByMarket = ref<Record<string, number>>({})
const currentSourceId = ref<number | null>(null)

// Auto-advance logic
watch([markets, selectedMarket], () => {
  if (activeStepIndex.value === 1 && markets.value.length > 0 && selectedMarket.value) {
    // Auto advance after selection (optional)
  }
})

// Computed links based on selected market
const displayLinks = computed(() => {
  if (!selectedMarket.value || selectedMarket.value === 'all') {
    return allLinks.value
  }
  return allLinks.value.filter(link => link.market === selectedMarket.value)
})

// Separate valid and invalid links
const validLinks = computed(() => 
  displayLinks.value.filter(l => l.is_valid)
)

const invalidLinks = computed(() => 
  displayLinks.value.filter(l => !l.is_valid)
)

const validLinksCount = computed(() => validLinks.value.length)

const invalidLinksCount = computed(() => invalidLinks.value.length)

function handleDataLoaded(data: { markets: string[], links: LinkInfo[], total_links: number, source_id?: number }) {
  // Mark that data has been loaded (even if empty)
  dataLoaded.value = true
  
  // Store all links and markets
  allLinks.value = data.links
  markets.value = data.markets
  currentSourceId.value = data.source_id || null
  
  // Calculate links by market
  linksByMarket.value = {}
  markets.value.forEach(market => {
    linksByMarket.value[market] = data.links.filter(l => l.market === market).length
  })
  
  // Set selectedMarket to 'all' for consistency with MarketSelector
  selectedMarket.value = 'all'
  
  // Update store links (for backward compatibility)
  scraperStore.setLinks(data.links)
  
  // Auto advance to step 2
  activeStepIndex.value = 1
  
  toast.add({
    severity: 'success',
    summary: 'Thành công',
    detail: `Tìm thấy ${data.total_links} link từ ${markets.value.length} market(s)`,
    life: 3000
  })
}

function handleDateRangesUpdated(ranges: DateRange[]) {
  scraperStore.setDateRanges(ranges)
}

async function startScraping() {
  try {
    const validLinksArray = validLinks.value
    
    if (validLinksArray.length === 0) {
      toast.add({
        severity: 'warn',
        summary: 'Cảnh báo',
        detail: 'Không có link hợp lệ để cào',
        life: 3000
      })
      return
    }

    if (scraperStore.dateRanges.length === 0) {
      toast.add({
        severity: 'warn',
        summary: 'Cảnh báo',
        detail: 'Vui lòng chọn ít nhất một cặp ngày',
        life: 3000
      })
      return
    }

    // Set loading state immediately for better UX
    isStarting.value = true
    scraperStore.reset()

    if (!scraperWebSocket.isConnected()) {
      await scraperWebSocket.connect()
    }

    scraperWebSocket.onMessage((data) => {
      scraperStore.updateProgress(data)
      
      // Clear starting state when scraping actually begins or completes
      if (data.type === 'started' || data.type === 'completed') {
        isStarting.value = false
      }

      if (data.type === 'started') {
        // Auto advance to step 6 when scraping starts
        activeStepIndex.value = 5
      }

      if (data.type === 'completed') {
        toast.add({
          severity: 'success',
          summary: 'Hoàn thành',
          detail: `Đã cào xong ${data.total_success} hotels (Mode: ${scrapeType.value === 'info' ? 'Thông tin' : 'Giá'})`,
          life: 5000
        })
      }
    })

    const request = {
      links: validLinksArray,
      date_ranges: scraperStore.dateRanges,
      source: scraperStore.selectedSource,
      scrape_type: scrapeType.value,
      market: selectedMarket.value === 'all' ? null : selectedMarket.value
    }

    scraperWebSocket.send(request)

    const modeText = scrapeType.value === 'info' ? 'thông tin' : 'giá'
    const marketText = selectedMarket.value && selectedMarket.value !== 'all' 
      ? ` (Market: ${selectedMarket.value})` 
      : ''
    
    toast.add({
      severity: 'info',
      summary: 'Bắt đầu',
      detail: `Đang cào ${modeText}${marketText}...`,
      life: 3000
    })
  } catch (error: any) {
    // Clear starting state on error
    isStarting.value = false
    
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.message || 'Không thể kết nối WebSocket',
      life: 5000
    })
  }
}

async function startScrapingAndNext() {
  await startScraping()
}

function downloadExcel() {
  try {
    import('xlsx').then((XLSX) => {
      let filteredResults = [];

      if (scrapeType.value === 'info') {
        const columns = [
          'Ngày cào', 'Giờ cào', 'Check in', 'Check out', 
          'Tên khách sạn', 'Link khách sạn', 
          'Số lượng review', 'Điểm review', 'Các tiện nghi được ưa chuộng nhất',
          'Tên hạng phòng', 'Số lượng người', 'Giường', 'Diện tích phòng', 'Các lựa chọn'
        ];
        
        filteredResults = scraperStore.results.map(item => {
            const newItem: any = {};
            columns.forEach(col => {
              newItem[col] = item[col];
            });
            return newItem;
        });
      } else {
        // Price mode
        const columns = [
          'Ngày cào', 'Giờ cào', 'Check in', 'Check out',
          'Tên khách sạn', 'Tên hạng phòng', 'Số lượng người',
          'Giá sau giảm', 'Giá gốc', 'Giảm giá'
        ];

        filteredResults = scraperStore.results.map(item => {
            const newItem: any = {};
            columns.forEach(col => {
              newItem[col] = item[col];
            });
            return newItem;
        });
      }

      // Tạo worksheet từ results
      const ws = XLSX.utils.json_to_sheet(filteredResults)
      
      // Tạo workbook
      const wb = XLSX.utils.book_new()
      const sheetName = scrapeType.value === 'info' ? 'Thông tin KS' : 'Giá phòng'
      XLSX.utils.book_append_sheet(wb, ws, sheetName)
      
      // Tạo filename với timestamp và mode
      const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-')
      const mode = scrapeType.value === 'info' ? 'info' : 'price'
      const market = selectedMarket.value && selectedMarket.value !== 'all' ? `_${selectedMarket.value}` : ''
      const filename = `booking_${mode}${market}_${timestamp}.xlsx`
      
      // Download file
      XLSX.writeFile(wb, filename)
      
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: 'Đã tải xuống file Excel',
        life: 3000
      })
    })
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải xuống Excel: ' + error.message,
      life: 5000
    })
  }
}

function downloadInvalidLinksExcel() {
  try {
    import('xlsx').then((XLSX) => {
      // Chuẩn bị data cho Excel
      const excelData = invalidLinks.value.map(link => ({
        'Market': link.market,
        'Tên khách sạn': link.hotel_name || '',
        'Tên/Giá trị': link.cell_value,
        'Link': link.link,
        'Trạng thái': 'Không hợp lệ',
        'Lý do': 'Link không đúng định dạng hoặc không hợp lệ'
      }))

      // Tạo worksheet từ data
      const ws = XLSX.utils.json_to_sheet(excelData)
      
      // Tự động điều chỉnh độ rộng cột
      const colWidths = [
        { wch: 15 },  // Market
        { wch: 30 },  // Tên khách sạn
        { wch: 40 },  // Tên/Giá trị
        { wch: 60 },  // Link
        { wch: 15 },  // Trạng thái
        { wch: 50 }   // Lý do
      ]
      ws['!cols'] = colWidths
      
      // Tạo workbook
      const wb = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(wb, ws, 'Links không hợp lệ')
      
      // Tạo filename với timestamp
      const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-')
      const market = selectedMarket.value && selectedMarket.value !== 'all' ? `_${selectedMarket.value}` : ''
      const filename = `invalid_links${market}_${timestamp}.xlsx`
      
      // Download file
      XLSX.writeFile(wb, filename)
      
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: `Đã tải xuống ${invalidLinks.value.length} links không hợp lệ`,
        life: 3000
      })
    })
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải xuống Excel: ' + error.message,
      life: 5000
    })
  }
}

function downloadErrorsExcel() {
  try {
    import('xlsx').then((XLSX) => {
      // Chuẩn bị data cho Excel
      const excelData = scraperStore.errors.map(error => ({
        'Tên': error.Tên,
        'Link': error.Link,
        'Lỗi': error.Lỗi
      }))

      // Tạo worksheet từ data
      const ws = XLSX.utils.json_to_sheet(excelData)
      
      // Tự động điều chỉnh độ rộng cột
      const colWidths = [
        { wch: 40 },  // Tên
        { wch: 60 },  // Link
        { wch: 50 }   // Lỗi
      ]
      ws['!cols'] = colWidths
      
      // Tạo workbook
      const wb = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(wb, ws, 'Danh sách lỗi')
      
      // Tạo filename với timestamp
      const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-')
      const market = selectedMarket.value && selectedMarket.value !== 'all' ? `_${selectedMarket.value}` : ''
      const filename = `errors${market}_${timestamp}.xlsx`
      
      // Download file
      XLSX.writeFile(wb, filename)
      
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: `Đã tải xuống ${scraperStore.errors.length} lỗi`,
        life: 3000
      })
    })
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải xuống Excel: ' + error.message,
      life: 5000
    })
  }
}

function saveHistory() {
  // TODO: Implement save to database
  toast.add({
    severity: 'info',
    summary: 'Tính năng',
    detail: 'Tính năng lưu lịch sử đang được phát triển',
    life: 3000
  })
}

function truncate(text: string, length: number): string {
  if (!text) return ''
  return text.length > length ? text.substring(0, length) + '...' : text
}

function formatPrice(price: any): string {
  if (!price || price === 'N/A') return ''
  const num = parseInt(price.toString().replace(/\D/g, ''))
  return num ? num.toLocaleString('vi-VN') + ' VND' : ''
}

function getStatusSeverity(status: string): string {
  if (status.includes('✅')) return 'success'
  if (status.includes('❌')) return 'danger'
  if (status.includes('🔄')) return 'info'
  return 'info'
}
</script>

<style scoped>
.mb-4 {
  margin-bottom: 1.5rem;
}

.mt-3 {
  margin-top: 1rem;
}

.mt-4 {
  margin-top: 1.5rem;
}

.w-full {
  width: 100%;
}

.flex {
  display: flex;
}

.gap-2 {
  gap: 0.5rem;
}

.link-text {
  color: #3b82f6;
  text-decoration: none;
}

.link-text:hover {
  text-decoration: underline;
}

.invalid-link-text {
  color: #dc2626;
  font-family: monospace;
  font-size: 0.9em;
}

.section-subtitle {
  margin: 0 0 1rem 0;
  padding: 0.5rem 0;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-color);
  border-bottom: 2px solid var(--surface-border);
}

.text-ellipsis {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: normal;
  word-break: break-word;
}

/* Stepper Layout */
.booking-tab-container {
  padding: 2rem 0;
}

.stepper-wrapper {
  margin-bottom: 3rem;
  background: white;
  padding: 2rem;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.custom-steps {
  padding: 0;
}

/* Override PrimeVue Steps connector line z-index */
.custom-steps :deep(.p-steps-item::before) {
  z-index: 1 !important;
}

.custom-steps :deep(.p-steps-item::after) {
  z-index: 1 !important;
}

.step-item-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  transition: all 0.3s;
  padding: 1rem;
  border-radius: 8px;
  position: relative;
  z-index: 2;
}

.step-item-container.step-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.step-item-container.step-clickable:hover {
  background: rgba(30, 136, 229, 0.05);
}

.step-item-container.step-disabled:hover {
  background: transparent;
}

.step-item-container.step-active {
  background: rgba(30, 136, 229, 0.1);
}

.step-number-circle {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e0e0e0;
  color: #666;
  font-weight: 600;
  font-size: 1.1rem;
  transition: all 0.3s;
  position: relative;
  z-index: 3;
}

.step-number-circle.active {
  background: #1e88e5;
  color: white;
  box-shadow: 0 0 0 4px rgba(30, 136, 229, 0.2);
}

.step-number-circle.completed {
  background: #4caf50;
  color: white;
}

.step-number-circle.disabled {
  background: #e0e0e0;
  color: #999;
}

.step-label-container {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.step-label {
  font-size: 0.9rem;
  font-weight: 500;
  text-align: center;
  color: #333;
  transition: color 0.3s;
}

.step-label.disabled-label {
  color: #999;
}

.info-icon {
  color: #1e88e5;
  cursor: help;
  font-size: 1rem;
}

.step-content-wrapper {
  min-height: 400px;
}

.step-content {
  animation: fadeIn 0.3s ease-in;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
