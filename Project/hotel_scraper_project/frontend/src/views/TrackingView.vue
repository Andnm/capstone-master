<template>
  <div class="tracking-view">
    <!-- Filter Card -->
    <Card class="filter-card mb-4">
      <template #title>
        <div class="flex align-items-center justify-content-between">
          <span><i class="pi pi-chart-line mr-2"></i>Tracking Analytics</span>
          <Button 
            v-if="hasData && !compareMode"
            label="So sánh" 
            icon="pi pi-plus" 
            @click="enableCompareMode"
            :disabled="loading"
            severity="secondary"
            size="small"
            outlined
          />
        </div>
      </template>
      <template #content>
        <!-- Main Filters -->
        <div class="filters">
          <div class="filter-item">
            <label>Phiên cào *</label>
            <Dropdown 
              v-model="filters.historyId" 
              :options="historyOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn phiên cào"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Năm *</label>
            <Dropdown 
              v-model="filters.year" 
              :options="yearOptions" 
              placeholder="Chọn năm"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Tháng *</label>
            <Dropdown 
              v-model="filters.month" 
              :options="monthOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn tháng"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Market *</label>
            <Dropdown 
              v-model="filters.market" 
              :options="marketOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn market"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Cluster *</label>
            <Dropdown 
              v-model="filters.cluster" 
              :options="clusterOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn cluster"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Giá bao gồm bữa sáng *</label>
            <Dropdown 
              v-model="filters.breakfast" 
              :options="breakfastOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Nhóm hạng phòng *</label>
            <Dropdown 
              v-model="filters.roomGroup" 
              :options="roomGroupOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn nhóm"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label>Level *</label>
            <Dropdown 
              v-model="filters.level" 
              :options="levelOptions" 
              optionLabel="label" 
              optionValue="value"
              placeholder="Chọn level"
              class="w-full"
              :disabled="loading"
            />
          </div>
          
          <div class="filter-item">
            <label style="visibility: hidden;">Action</label>
            <Button 
              label="Tải dữ liệu" 
              @click="loadTrackingData"
              :loading="loading"
              :disabled="!canLoadData"
              class="w-full"
              severity="primary"
            />
          </div>
        </div>
        
        <!-- Compare Section -->
        <div v-if="compareMode" class="compare-section mt-4">
          <div class="flex align-items-center justify-content-between mb-3">
            <h4 class="m-0"><i class="pi pi-clone mr-2"></i>So sánh với phiên cào khác</h4>
            <Button 
              icon="pi pi-times" 
              text 
              rounded 
              severity="danger" 
              size="small"
              @click="cancelCompare"
            />
          </div>
          <div class="filters">
            <div class="filter-item">
              <label>Phiên cào so sánh *</label>
              <Dropdown 
                v-model="compareFilters.historyId" 
                :options="historyOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn phiên cào để so sánh"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Năm *</label>
              <Dropdown 
                v-model="compareFilters.year" 
                :options="yearOptions" 
                placeholder="Chọn năm"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Tháng *</label>
              <Dropdown 
                v-model="compareFilters.month" 
                :options="monthOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn tháng"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Market *</label>
              <Dropdown 
                v-model="compareFilters.market" 
                :options="marketOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn market"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Cluster *</label>
              <Dropdown 
                v-model="compareFilters.cluster" 
                :options="clusterOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn cluster"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Giá bao gồm bữa sáng *</label>
              <Dropdown 
                v-model="compareFilters.breakfast" 
                :options="breakfastOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Nhóm hạng phòng *</label>
              <Dropdown 
                v-model="compareFilters.roomGroup" 
                :options="roomGroupOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn nhóm"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label>Level *</label>
              <Dropdown 
                v-model="compareFilters.level" 
                :options="levelOptions" 
                optionLabel="label" 
                optionValue="value"
                placeholder="Chọn level"
                class="w-full"
                :disabled="loading"
              />
            </div>
            
            <div class="filter-item">
              <label style="visibility: hidden;">Action</label>
              <Button 
                label="So sánh ngay" 
                icon="pi pi-check" 
                @click="loadCompareData"
                :loading="loading"
                :disabled="!canLoadCompareData"
                class="w-full"
                severity="success"
              />
            </div>
          </div>
        </div>
      </template>
    </Card>

    <!-- Main Data Table Card -->
    <Card v-if="hasData && !loading" class="data-card mt-4">
      <template #title>
        <div class="flex align-items-center justify-content-between gap-2">
          <span><i class="pi pi-table mr-2"></i>{{ hasCompareData ? 'Phiên chính' : 'Kết quả Tracking' }}</span>
          <div v-if="hasCompareData" class="text-sm text-500">
            Phiên #{{ filters.historyId }} - {{ filters.market }} {{ filters.cluster }} - {{ filters.year }}/{{ filters.month }}
          </div>
        </div>
      </template>
      <template #content>
        <DataTable 
          :value="hasCompareData ? mainTableData : tableData" 
          scrollable 
          scrollHeight="600px"
          :frozenColumns="frozenColumns"
          showGridlines
          stripedRows
          class="tracking-table"
          :rowClass="getRowClass"
        >
          <!-- Frozen Columns -->
          <Column field="Market" header="Market" frozen :style="{ minWidth: '100px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.Market }}
              </template>
            </template>
          </Column>
          <Column field="Cluster" header="Cluster" frozen :style="{ minWidth: '120px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.Cluster }}
              </template>
            </template>
          </Column>
          <Column field="hotel_name" header="Tên khách sạn" frozen :style="{ minWidth: '200px' }">
            <template #body="slotProps">
              {{ slotProps.data.hotel_name }}
            </template>
          </Column>
          <Column field="room_group" header="Nhóm hạng phòng" frozen :style="{ minWidth: '120px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.room_group }}
              </template>
            </template>
          </Column>
          <Column field="breakfast" header="Bữa sáng" frozen :style="{ minWidth: '120px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.breakfast }}
              </template>
            </template>
          </Column>
          <Column field="level" header="Level" frozen :style="{ minWidth: '100px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.level }}
              </template>
            </template>
          </Column>
          
          <!-- Dynamic Date Columns -->
          <Column 
            v-for="dateCol in (hasCompareData ? mainDateColumns : dateColumns)" 
            :key="dateCol.date"
            :field="dateCol.date"
            :style="{ minWidth: '120px', maxWidth: '120px' }"
          >
            <template #header>
              <div v-html="dateCol.display" style="text-align: center; line-height: 1.2;"></div>
            </template>
            <template #body="slotProps">
              <div :style="{
                textAlign: 'right',
                fontWeight: slotProps.data.isSummaryRow ? '600' : '400',
                color: slotProps.data.isSummaryRow ? '#1e40af' : '#1e293b'
              }">
                {{ formatPrice(slotProps.data[dateCol.date]) }}
              </div>
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>

    <!-- Compare Data Table Card -->
    <Card v-if="hasCompareData && !loading" class="data-card mt-4">
      <template #title>
        <div class="flex align-items-center justify-content-between gap-2">
          <span><i class="pi pi-table mr-2"></i>Phiên so sánh</span>
          <div class="text-sm text-500">
            Phiên #{{ compareFilters.historyId }} - {{ compareFilters.market }} {{ compareFilters.cluster }} - {{ compareFilters.year }}/{{ compareFilters.month }}
          </div>
        </div>
      </template>
      <template #content>
        <DataTable 
          :value="compareTableData" 
          scrollable 
          scrollHeight="600px"
          :frozenColumns="frozenColumns"
          showGridlines
          stripedRows
          class="tracking-table"
          :rowClass="getRowClass"
        >
          <!-- Frozen Columns -->
          <Column field="Market" header="Market" frozen :style="{ minWidth: '100px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.Market }}
              </template>
            </template>
          </Column>
          <Column field="Cluster" header="Cluster" frozen :style="{ minWidth: '120px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.Cluster }}
              </template>
            </template>
          </Column>
          <Column field="hotel_name" header="Tên khách sạn" frozen :style="{ minWidth: '200px' }">
            <template #body="slotProps">
              {{ slotProps.data.hotel_name }}
            </template>
          </Column>
          <Column field="room_group" header="Nhóm hạng phòng" frozen :style="{ minWidth: '120px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.room_group }}
              </template>
            </template>
          </Column>
          <Column field="breakfast" header="Bữa sáng" frozen :style="{ minWidth: '120px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.breakfast }}
              </template>
            </template>
          </Column>
          <Column field="level" header="Level" frozen :style="{ minWidth: '100px' }">
            <template #body="slotProps">
              <template v-if="!slotProps.data.isSummaryRow">
                {{ slotProps.data.level }}
              </template>
            </template>
          </Column>
          
          <!-- Dynamic Date Columns -->
          <Column 
            v-for="dateCol in compareDateColumns" 
            :key="dateCol.date"
            :field="dateCol.date"
            :style="{ minWidth: '120px', maxWidth: '120px' }"
          >
            <template #header>
              <div v-html="dateCol.display" style="text-align: center; line-height: 1.2;"></div>
            </template>
            <template #body="slotProps">
              <div :style="{
                textAlign: 'right',
                fontWeight: slotProps.data.isSummaryRow ? '600' : '400',
                color: slotProps.data.isSummaryRow ? '#1e40af' : '#1e293b'
              }">
                {{ formatPrice(slotProps.data[dateCol.date]) }}
              </div>
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>

    <!-- Empty State Card -->
    <Card v-else-if="!loading" class="mt-4">
      <template #content>
        <div class="empty-state">
          <i class="pi pi-chart-line" style="font-size: 3rem; color: #cbd5e1;"></i>
          <h3 style="color: #64748b; margin: 1rem 0;">Chưa có dữ liệu tracking</h3>
          <p style="color: #94a3b8;">Vui lòng chọn các bộ lọc và nhấn "Tải dữ liệu" để xem kết quả</p>
        </div>
      </template>
    </Card>

    <!-- Loading State -->
    <Card v-else class="mt-4">
      <template #content>
        <div class="text-center py-5">
          <i class="pi pi-spin pi-spinner" style="font-size: 2rem; color: #667eea;"></i>
          <p class="mt-3 text-500">Đang tải dữ liệu tracking...</p>
        </div>
      </template>
    </Card>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useToast } from 'primevue/usetoast'
import axios from 'axios'
import Card from 'primevue/card'
import Button from 'primevue/button'
import Dropdown from 'primevue/dropdown'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Toast from 'primevue/toast'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const toast = useToast()

// State
const loading = ref(false)
const compareMode = ref(false)
const hasData = ref(false)

// Filters
const filters = ref({
  historyId: null as number | null,
  year: new Date().getFullYear(),
  month: new Date().getMonth() + 1,
  market: null as string | null,
  cluster: null as string | null,
  breakfast: null as string | null,
  roomGroup: null as string | null,
  level: null as string | null
})

const compareFilters = ref({
  historyId: null as number | null,
  year: new Date().getFullYear(),
  month: new Date().getMonth() + 1,
  market: null as string | null,
  cluster: null as string | null,
  breakfast: null as string | null,
  roomGroup: null as string | null,
  level: null as string | null
})

// Options
const historyOptions = ref<Array<{label: string, value: number}>>([])
const marketOptions = ref<Array<{label: string, value: string}>>([])
const clusterOptions = ref<Array<{label: string, value: string}>>([])
const breakfastOptions = ref<Array<{label: string, value: string}>>([])
const roomGroupOptions = ref<Array<{label: string, value: string}>>([])
const levelOptions = ref<Array<{label: string, value: string}>>([])

// Year options: current year - 5 to current year + 2
const yearOptions = computed(() => {
  const currentYear = new Date().getFullYear()
  const years = []
  for (let i = currentYear - 5; i <= currentYear + 2; i++) {
    years.push(i)
  }
  return years
})

const monthOptions = [
  { label: 'Tháng 1', value: 1 },
  { label: 'Tháng 2', value: 2 },
  { label: 'Tháng 3', value: 3 },
  { label: 'Tháng 4', value: 4 },
  { label: 'Tháng 5', value: 5 },
  { label: 'Tháng 6', value: 6 },
  { label: 'Tháng 7', value: 7 },
  { label: 'Tháng 8', value: 8 },
  { label: 'Tháng 9', value: 9 },
  { label: 'Tháng 10', value: 10 },
  { label: 'Tháng 11', value: 11 },
  { label: 'Tháng 12', value: 12 }
]

// Data
const dateColumns = ref<Array<any>>([])
const rawData = ref<Array<any>>([])
const tableData = ref<Array<any>>([])
const frozenColumns = ref<Array<any>>([])

// Compare data (separate from main data)
const mainTableData = ref<Array<any>>([])
const mainDateColumns = ref<Array<any>>([])
const compareTableData = ref<Array<any>>([])
const compareDateColumns = ref<Array<any>>([])
const hasCompareData = ref(false)

const canLoadData = computed(() => {
  return filters.value.historyId && 
         filters.value.year && 
         filters.value.month && 
         filters.value.market && 
         filters.value.cluster && 
         filters.value.breakfast && 
         filters.value.roomGroup && 
         filters.value.level
})

const canLoadCompareData = computed(() => {
  return compareFilters.value.historyId && 
         compareFilters.value.year && 
         compareFilters.value.month && 
         compareFilters.value.market && 
         compareFilters.value.cluster && 
         compareFilters.value.breakfast && 
         compareFilters.value.roomGroup && 
         compareFilters.value.level
})

// Load config options
async function loadConfigOptions() {
  try {
    const response = await axios.get(`${API_BASE_URL}/api/config`)
    const configs = response.data
    
    // Config API returns grouped by category: { market: [...], cluster: [...], ... }
    marketOptions.value = (configs.market || [])
      .map((c: any) => ({ label: c.config_value, value: c.config_key }))
    
    clusterOptions.value = (configs.cluster || [])
      .map((c: any) => ({ label: c.config_value, value: c.config_key }))
    
    breakfastOptions.value = (configs.breakfast || [])
      .map((c: any) => ({ label: c.config_value, value: c.config_key }))
    
    roomGroupOptions.value = (configs.room_group || [])
      .map((c: any) => ({ label: c.config_value, value: c.config_key }))
    
    levelOptions.value = (configs.level || [])
      .map((c: any) => ({ label: c.config_value, value: c.config_key }))
  } catch (error) {
    console.error('Error loading config options:', error)
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải cấu hình dropdown',
      life: 3000
    })
  }
}

// Load history list
async function loadHistoryList() {
  try {
    const response = await axios.get(`${API_BASE_URL}/api/tracking/history-list`)
    historyOptions.value = response.data.data.map((h: any) => ({
      label: `#${h.id} - ${h.crawl_date} ${h.crawl_time || ''} (${h.scrape_type === 'price' ? 'Cào giá' : 'Cào thông tin'})`,
      value: h.id
    }))
  } catch (error) {
    console.error('Error loading history list:', error)
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải danh sách phiên cào',
      life: 3000
    })
  }
}

// Load tracking data
async function loadTrackingData() {
  if (!canLoadData.value) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Vui lòng chọn đầy đủ các filters',
      life: 3000
    })
    return
  }
  
  loading.value = true
  try {
    const response = await axios.post(`${API_BASE_URL}/api/tracking/data`, {
      history_id: filters.value.historyId,
      year: filters.value.year,
      month: filters.value.month,
      market: filters.value.market,
      cluster: filters.value.cluster,
      breakfast: filters.value.breakfast,
      room_group: filters.value.roomGroup,
      level: filters.value.level
    })
    
    const data = response.data.data
    dateColumns.value = data.date_columns
    rawData.value = data.main
    
    processTableData()
    
    // Store as main table data
    mainTableData.value = tableData.value
    mainDateColumns.value = dateColumns.value
    
    hasData.value = true
    
    toast.add({
      severity: 'success',
      summary: 'Thành công',
      detail: 'Đã tải dữ liệu tracking',
      life: 3000
    })
  } catch (error: any) {
    console.error('Error loading tracking data:', error)
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.response?.data?.detail || 'Không thể tải dữ liệu',
      life: 3000
    })
  } finally {
    loading.value = false
  }
}

// Process table data
function processTableData() {
  // Group by hotel + room
  const grouped = new Map()
  
  rawData.value.forEach(row => {
    // Only process rows that have ALL required fields from competitor_list
    if (!row.Market || !row.Cluster || !row.hotel_name || 
        !row['Nhóm hạng phòng'] || !row['Giá bao gồm bữa sáng'] || !row.Level) {
      // Skip rows without complete competitor info
      return
    }
    
    const key = `${row.hotel_name}|${row.room_type}`
    if (!grouped.has(key)) {
      grouped.set(key, {
        Market: row.Market,
        Cluster: row.Cluster,
        hotel_name: row.hotel_name,
        room_group: row['Nhóm hạng phòng'],
        breakfast: row['Giá bao gồm bữa sáng'],
        level: row.Level,
        competitor_level: row['Level đối thủ'],
        prices: {}
      })
    }
    
    // Add price for check_in date
    if (row.check_in) {
      const checkInDate = row.check_in.split('T')[0] // Extract date part
      grouped.get(key).prices[checkInDate] = row.price_after_discount
    }
  })
  
  // Convert to array and add date columns
  const result = []
  grouped.forEach(item => {
    const rowData: any = {
      Market: item.Market,
      Cluster: item.Cluster,
      hotel_name: item.hotel_name,
      room_group: item.room_group,
      breakfast: item.breakfast,
      level: item.level,
      competitor_level: item.competitor_level
    }
    
    // Add price for each date column
    dateColumns.value.forEach(dateCol => {
      rowData[dateCol.date] = item.prices[dateCol.date] || null
    })
    
    result.push(rowData)
  })
  
  // Add summary rows back to main data
  const summaryRows = calculateSummaryRows(result)
  result.push(...summaryRows)
  
  tableData.value = result
}

// Calculate summary rows (Trung bình, Min, Mid, Max)
function calculateSummaryRows(data: any[], dateColumnsData: any[] = dateColumns.value) {
  const summaryRows = []
  
  // For each date column, calculate averages
  // Get common values from first data row (if exists)
  const firstRow = data.length > 0 ? data[0] : {}
  
  const avgRow: any = {
    Market: firstRow.Market || '',
    Cluster: firstRow.Cluster || '',
    hotel_name: 'Trung bình thị trường',
    room_group: firstRow.room_group || '',
    breakfast: firstRow.breakfast || '',
    level: firstRow.level || '',
    isSummaryRow: true,
    isFirstSummary: true
  }
  
  const minRow: any = {
    hotel_name: 'Min',
    isSummaryRow: true
  }
  
  const midRow: any = {
    hotel_name: 'Mid',
    isSummaryRow: true
  }
  
  const maxRow: any = {
    hotel_name: 'Max',
    isSummaryRow: true
  }
  
  dateColumnsData.forEach(dateCol => {
    // Get all valid prices for this date (for average)
    const allPrices = data
      .map(d => d[dateCol.date])
      .filter(p => p != null && p !== undefined && p > 0)
    
    // Get prices by competitor_level (1=Min, 2=Mid, 3=Max) - stored as strings
    const minLevelItems = data.filter(d => d.competitor_level === '1')
    const minLevelPrices = minLevelItems
      .map(d => d[dateCol.date])
      .filter(p => p != null && p !== undefined && p > 0)
    
    const midLevelItems = data.filter(d => d.competitor_level === '2')
    const midLevelPrices = midLevelItems
      .map(d => d[dateCol.date])
      .filter(p => p != null && p !== undefined && p > 0)
    
    const maxLevelItems = data.filter(d => d.competitor_level === '3')
    const maxLevelPrices = maxLevelItems
      .map(d => d[dateCol.date])
      .filter(p => p != null && p !== undefined && p > 0)
    
    // Trung bình thị trường = average of ALL prices (regardless of level)
    if (allPrices.length > 0) {
      const sum = allPrices.reduce((a, b) => a + b, 0)
      avgRow[dateCol.date] = sum / allPrices.length
    } else {
      avgRow[dateCol.date] = null
    }
    
    // Min = average of prices from hotels with competitor_level = 'Min'
    if (minLevelPrices.length > 0) {
      const sum = minLevelPrices.reduce((a, b) => a + b, 0)
      minRow[dateCol.date] = sum / minLevelPrices.length
    } else {
      minRow[dateCol.date] = null
    }
    
    // Mid = average of prices from hotels with competitor_level = 'Mid'
    if (midLevelPrices.length > 0) {
      const sum = midLevelPrices.reduce((a, b) => a + b, 0)
      midRow[dateCol.date] = sum / midLevelPrices.length
    } else {
      midRow[dateCol.date] = null
    }
    
    // Max = average of prices from hotels with competitor_level = 'Max'
    if (maxLevelPrices.length > 0) {
      const sum = maxLevelPrices.reduce((a, b) => a + b, 0)
      maxRow[dateCol.date] = sum / maxLevelPrices.length
    } else {
      maxRow[dateCol.date] = null
    }
  })
  
  summaryRows.push(avgRow, minRow, midRow, maxRow)
  
  return summaryRows
}

function enableCompareMode() {
  compareMode.value = true
}

function cancelCompare() {
  compareMode.value = false
  hasCompareData.value = false
  compareTableData.value = []
  compareDateColumns.value = []
  compareFilters.value = {
    historyId: null,
    year: new Date().getFullYear(),
    month: new Date().getMonth() + 1,
    market: null,
    cluster: null,
    breakfast: null,
    roomGroup: null,
    level: null
  }
}

async function loadCompareData() {
  if (!canLoadCompareData.value) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Vui lòng chọn đầy đủ các filters cho phiên so sánh',
      life: 3000
    })
    return
  }
  
  // Must have main data first
  if (!hasData.value) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Vui lòng tải dữ liệu phiên chính trước',
      life: 3000
    })
    return
  }
  
  loading.value = true
  try {
    const response = await axios.post(`${API_BASE_URL}/api/tracking/data`, {
      // Compare filters only (we load compare data separately)
      history_id: compareFilters.value.historyId,
      year: compareFilters.value.year,
      month: compareFilters.value.month,
      market: compareFilters.value.market,
      cluster: compareFilters.value.cluster,
      breakfast: compareFilters.value.breakfast,
      room_group: compareFilters.value.roomGroup,
      level: compareFilters.value.level
    })
    
    const data = response.data.data
    
    // Process compare data separately without touching main data
    compareDateColumns.value = data.date_columns
    const compareRawData = data.main
    
    // Process compare table data
    const grouped = new Map()
    
    compareRawData.forEach(row => {
      if (!row.Market || !row.Cluster || !row.hotel_name || 
          !row['Nhóm hạng phòng'] || !row['Giá bao gồm bữa sáng'] || !row.Level) {
        return
      }
      
      const key = `${row.Market}|${row.Cluster}|${row.hotel_name}|${row['Nhóm hạng phòng']}|${row['Giá bao gồm bữa sáng']}|${row.Level}`
      
      if (!grouped.has(key)) {
        grouped.set(key, {
          Market: row.Market,
          Cluster: row.Cluster,
          hotel_name: row.hotel_name,
          room_group: row['Nhóm hạng phòng'],
          breakfast: row['Giá bao gồm bữa sáng'],
          level: row.Level,
          competitor_level: row['Level đối thủ'],
          prices: {}
        })
      }
      
      const hotel = grouped.get(key)
      const dateKey = row.check_in
      
      if (dateKey && row.price_after_discount) {
        if (!hotel.prices[dateKey]) {
          hotel.prices[dateKey] = []
        }
        hotel.prices[dateKey].push(row.price_after_discount)
      }
    })
    
    // Calculate average prices
    const processed = Array.from(grouped.values()).map(hotel => {
      const result: any = {
        Market: hotel.Market,
        Cluster: hotel.Cluster,
        hotel_name: hotel.hotel_name,
        room_group: hotel.room_group,
        breakfast: hotel.breakfast,
        level: hotel.level,
        competitor_level: hotel.competitor_level
      }
      
      for (const date in hotel.prices) {
        const prices = hotel.prices[date]
        const sum = prices.reduce((a: number, b: number) => a + b, 0)
        result[date] = sum / prices.length
      }
      
      return result
    })
    
    // Calculate summary rows for compare data
    const summary = calculateSummaryRows(processed, compareDateColumns.value)
    compareTableData.value = [...processed, ...summary]
    
    hasCompareData.value = true
    
    toast.add({
      severity: 'success',
      summary: 'Thành công',
      detail: `Đã tải dữ liệu so sánh: ${processed.length} hotels`,
      life: 5000
    })
    
  } catch (error: any) {
    console.error('Load compare data error:', error)
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.response?.data?.detail || 'Không thể tải dữ liệu so sánh',
      life: 3000
    })
  } finally {
    loading.value = false
  }
}

function formatPrice(price: number | null) {
  if (!price && price !== 0) return '-'
  return new Intl.NumberFormat('vi-VN').format(Math.round(price))
}

function getRowClass(data: any) {
  return data.isSummaryRow ? 'summary-row' : ''
}

onMounted(async () => {
  await loadConfigOptions()
  await loadHistoryList()
})
</script>

<style scoped>
.tracking-view {
  min-height: 400px;
}

.filter-card {
  background: #f8f9fa;
}

.filters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  align-items: end;
}

.filter-item {
  display: flex;
  flex-direction: column;
}

.filter-item label {
  margin-bottom: 0.5rem;
  font-weight: 500;
  font-size: 0.875rem;
  color: #475569;
}

.w-full {
  width: 100%;
}

.mb-4 {
  margin-bottom: 1.5rem;
}

.mt-4 {
  margin-top: 1.5rem;
}

.compare-section {
  background: #f8fafc;
  border: 2px dashed #cbd5e1;
  border-radius: 8px;
  padding: 1rem;
}

.data-card {
  background: white;
}

.tracking-table-wrapper {
  overflow-x: auto;
}

.empty-state {
  text-align: center;
  padding: 3rem 1rem;
}

.py-5 {
  padding-top: 3rem;
  padding-bottom: 3rem;
}

:deep(.p-card) {
  border-radius: 12px;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

:deep(.p-card-title) {
  display: flex;
  align-items: center;
  font-size: 1.25rem;
  font-weight: 600;
  color: #1e293b;
}

:deep(.tracking-table .p-datatable-thead > tr > th) {
  background: #f1f5f9;
  color: #334155 !important;
  font-weight: 600;
  font-size: 0.875rem;
  padding: 0.75rem;
  border: 1px solid #cbd5e1;
}

:deep(.tracking-table .p-datatable-tbody > tr > td) {
  padding: 0.75rem;
  border: 1px solid #e2e8f0;
  font-size: 0.875rem;
  background: white;
}

:deep(.tracking-table .p-datatable-tbody > tr:hover) {
  background: #f8fafc !important;
}

:deep(.tracking-table .p-datatable-frozen-column) {
  background: #ffffff;
  font-weight: 500;
}

:deep(.p-datatable-frozen-tbody) {
  font-weight: 500;
}

:deep(.p-dropdown),
:deep(.p-inputtext) {
  font-size: 0.875rem;
}

/* Responsive */
@media (max-width: 768px) {
  .filters {
    grid-template-columns: 1fr;
  }
}

/* Summary Row Styles */
:deep(.tracking-table .summary-row) {
  background: #93c5fd !important;
  font-weight: 600;
}

:deep(.tracking-table .summary-row:hover) {
  background: #60a5fa !important;
}

:deep(.tracking-table .summary-row td) {
  background: #93c5fd !important;
  border-color: #60a5fa !important;
  color: #1e40af !important;
}

/* Merge 6 frozen columns in summary rows - remove internal borders */
:deep(.tracking-table .summary-row td:nth-child(1)),
:deep(.tracking-table .summary-row td:nth-child(2)),
:deep(.tracking-table .summary-row td:nth-child(3)),
:deep(.tracking-table .summary-row td:nth-child(4)),
:deep(.tracking-table .summary-row td:nth-child(5)),
:deep(.tracking-table .summary-row td:nth-child(6)) {
  border-right: none !important;
}

:deep(.tracking-table .summary-row td:nth-child(2)),
:deep(.tracking-table .summary-row td:nth-child(3)),
:deep(.tracking-table .summary-row td:nth-child(4)),
:deep(.tracking-table .summary-row td:nth-child(5)),
:deep(.tracking-table .summary-row td:nth-child(6)) {
  border-left: none !important;
}
</style>
