<template>
  <div class="history-list">
    <Card class="filter-card mb-4">
      <template #title>
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 2rem;">
          <span>Công cụ lọc</span>
          <Button label="Lấy API dữ liệu mới nhất" icon="pi pi-bolt" outlined
            @click="showLatestApiDialog = true" severity="primary" />
        </div>
      </template>
      <template #content>
        <div class="filters">
          <div class="filter-item">
            <label>Nguồn</label>
            <Dropdown v-model="filters.source" :options="sourceOptions" placeholder="Tất cả" class="w-full" />
          </div>

          <div class="filter-item"> <label>Loại cào</label>
            <Dropdown v-model="filters.scrapeType" :options="scrapeTypeOptions" optionLabel="label" optionValue="value"
              placeholder="Chọn loại cào" class="w-full" />
          </div>

          <div class="filter-item">
            <label>Từ ngày</label>
            <Calendar v-model="filters.dateFrom" dateFormat="yy-mm-dd" showIcon class="w-full"
              @keyup.enter="loadHistories" />
          </div>

          <div class="filter-item">
            <label>Đến ngày</label>
            <Calendar v-model="filters.dateTo" dateFormat="yy-mm-dd" showIcon class="w-full"
              @keyup.enter="loadHistories" />
          </div>

          <div class="filter-item">
            <label style="visibility: hidden">Thao tác</label>
            <Button label="Tìm kiếm" @click="loadHistories" :loading="historyStore.loading" class="w-full" severity="primary" />
          </div>
        </div>
      </template>
    </Card>

    <!-- Histories Table -->
    <Card>
      <template #title>
        Danh sách phiên cào (Tổng: {{ historyStore.totalCount }})
      </template>
      <template #content>
        <DataTable :value="historyStore.histories" :loading="historyStore.loading" responsiveLayout="scroll"
          class="p-datatable-sm" showGridlines stripedRows>
          <Column field="id" header="ID" :style="{ width: '70px' }" sortable></Column>
          <Column field="source" header="Nguồn" :style="{ width: '90px' }">
            <template #body="slotProps">
              <Tag :severity="getSourceSeverity(slotProps.data.source)">
                {{ slotProps.data.source.toUpperCase() }}
              </Tag>
            </template>
          </Column>
          <Column field="scrape_type" header="Loại cào" :style="{ width: '100px' }">
            <template #body="slotProps">
              <Tag :severity="slotProps.data.scrape_type === 'info' ? 'info' : 'warning'">
                {{ slotProps.data.scrape_type === 'info' ? 'Thông tin' : 'Cào giá' }}
              </Tag>
            </template>
          </Column>
          <Column field="crawl_date" header="Ngày cào" :style="{ width: '105px' }" sortable>
            <template #body="slotProps">
              {{ formatDate(slotProps.data.crawl_date) }}
            </template>
          </Column>
          <Column field="created_at" header="Lưu lúc" :style="{ width: '145px' }" sortable>
            <template #body="slotProps">
              {{ formatDateTime(slotProps.data.created_at) }}
            </template>
          </Column>
          <Column field="total_records" header="Records" :style="{ width: '90px' }" sortable>
            <template #body="slotProps">
              <Tag :severity="slotProps.data.total_records > 0 ? 'success' : 'danger'">
                {{ slotProps.data.total_records }}
              </Tag>
            </template>
          </Column>
          <Column header="Thao tác" :style="{ width: '80px' }">
            <template #body="slotProps">
              <Button icon="pi pi-ellipsis-h" class="p-button-sm p-button-text" 
                @click="toggleMenu($event, slotProps.data.id)" />
              <Menu :ref="(el) => setMenuRef(el, slotProps.data.id)" :model="getMenuItems(slotProps.data.id)" :popup="true" />
            </template>
          </Column>
        </DataTable>

        <!-- Server-side Pagination -->
        <Paginator v-if="historyStore.totalCount > 0" :rows="historyStore.pageSize"
          :totalRecords="historyStore.totalCount" :first="(historyStore.currentPage - 1) * historyStore.pageSize"
          :rowsPerPageOptions="[10, 20, 50, 100]" @page="onPageChange"
          template="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink JumpToPageInput CurrentPageReport RowsPerPageDropdown"
          currentPageReportTemplate="Trang {currentPage}/{totalPages} | Hiển thị {first}-{last} trên tổng {totalRecords} phiên"
          class="mt-3" />
      </template>
    </Card>

    <!-- Latest API Dialog -->
    <Dialog v-model:visible="showLatestApiDialog" header="Lấy API Dữ liệu mới nhất" :modal="true"
      :style="{ width: '500px' }">
      <div class="flex flex-column gap-4">
        <p class="m-0 line-height-3">
          API này luôn trả về dữ liệu của <strong>phiên cào mới nhất</strong> theo loại bạn chọn.<br>
          Bạn có thể dán link này vào PowerBI (Web Source), Google Sheets, hoặc Excel và dữ liệu sẽ tự động cập nhật khi
          bạn
          Refresh.
        </p>

        <div class="grid formgrid p-fluid">
          <div class="col-6">
            <Button label="API Cào Giá" style="margin-top: 5px" severity="primary"
              @click="copyLatestApiLink('price')" />
          </div>
          <div class="col-6">
            <Button label="API Cào Thông tin" style="margin-top: 5px" severity="primary"
              @click="copyLatestApiLink('info')" />
          </div>
        </div>
      </div>
    </Dialog>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useToast } from 'primevue/usetoast'
import { useConfirm } from 'primevue/useconfirm'
import { useHistoryStore } from '@/stores/history'
import Menu from 'primevue/menu'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const router = useRouter()
const historyStore = useHistoryStore()
const toast = useToast()
const confirm = useConfirm()

const filters = ref({
  source: 'Tất cả',
  dateFrom: null as Date | null,
  dateTo: null as Date | null,
  scrapeType: 'all'
})

const sourceOptions = ['Tất cả', 'Booking', 'Agoda']
const scrapeTypeOptions = [
  { label: 'Tất cả', value: 'all' },
  { label: 'Thông tin', value: 'info' },
  { label: 'Cào giá', value: 'price' }
]

const showLatestApiDialog = ref(false)

// Menu management
const menuRefs = ref<Record<number, any>>({})

function setMenuRef(el: any, historyId: number) {
  if (el) {
    menuRefs.value[historyId] = el
  }
}

function toggleMenu(event: Event, historyId: number) {
  const menu = menuRefs.value[historyId]
  if (menu) {
    menu.toggle(event)
  }
}

function getMenuItems(historyId: number) {
  return [
    {
      label: 'Xem chi tiết',
      icon: 'pi pi-eye',
      command: () => viewDetail(historyId)
    },
    {
      label: 'Tải xuống Excel',
      icon: 'pi pi-download',
      command: () => exportExcel(historyId)
    },
    {
      label: 'Tạo danh sách Com...',
      icon: 'pi pi-list',
      command: () => exportCompetitorsList(historyId)
    },
    {
      label: 'Lấy API Link',
      icon: 'pi pi-link',
      command: () => copyApiLink(historyId)
    },
    {
      separator: true
    },
    {
      label: 'Xóa',
      icon: 'pi pi-trash',
      command: () => confirmDelete(historyId),
      class: 'text-red-500'
    }
  ]
}

onMounted(() => {
  loadHistories()
})

async function loadHistories() {
  try {
    const dateFrom = filters.value.dateFrom
      ? formatDateToString(filters.value.dateFrom)
      : undefined
    const dateTo = filters.value.dateTo
      ? formatDateToString(filters.value.dateTo)
      : undefined

    await historyStore.fetchHistories(
      1,
      filters.value.source === 'Tất cả' ? undefined : filters.value.source,
      dateFrom,
      dateTo,
      filters.value.scrapeType
    )
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.message || 'Không thể tải lịch sử',
      life: 3000
    })
  }
}

function copyApiLink(historyId: number) {
  const url = `${API_BASE_URL}/public/history/${historyId}`
  
  // Try modern clipboard API first (HTTPS only)
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(() => {
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: 'Đã copy link API Public vào clipboard',
        life: 3000
      })
    }).catch(() => {
      fallbackCopyToClipboard(url)
    })
  } else {
    fallbackCopyToClipboard(url)
  }
}

function copyLatestApiLink(type: 'price' | 'info') {
  const url = `${API_BASE_URL}/public/latest?scrape_type=${type}`
  
  // Try modern clipboard API first (HTTPS only)
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(() => {
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: `Đã copy link API (${type}) mới nhất`,
        life: 3000
      })
      showLatestApiDialog.value = false
    }).catch(() => {
      fallbackCopyToClipboard(url, type)
    })
  } else {
    fallbackCopyToClipboard(url, type)
  }
}

// Fallback copy method for HTTP (legacy browsers or non-HTTPS)
function fallbackCopyToClipboard(url: string, type?: 'price' | 'info') {
  try {
    const textArea = document.createElement('textarea')
    textArea.value = url
    textArea.style.position = 'fixed'
    textArea.style.left = '-999999px'
    textArea.style.top = '-999999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    
    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)
    
    if (successful) {
      const detail = type ? `Đã copy link API (${type}) mới nhất` : 'Đã copy link API Public vào clipboard'
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: detail,
        life: 3000
      })
      if (type) showLatestApiDialog.value = false
    } else {
      throw new Error('Copy command failed')
    }
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể copy link. Vui lòng copy thủ công từ URL: ' + url,
      life: 5000
    })
    console.error('Fallback copy failed: ', err)
  }
}

function viewDetail(historyId: number) {
  router.push({ name: 'history-detail', params: { id: historyId } })
}

async function exportExcel(historyId: number) {
  try {
    toast.add({
      severity: 'info',
      summary: 'Đang xử lý',
      detail: 'Đang tải dữ liệu và tạo file Excel...',
      life: 3000
    })

    const data = await historyStore.exportHistory(historyId)

    if (!data || data.length === 0) {
      toast.add({
        severity: 'warn',
        summary: 'Thông báo',
        detail: 'Không có dữ liệu để xuất',
        life: 3000
      })
      return
    }

    const scrapeType = data.length > 0 ? data[0].scrape_type : 'info';

    // Define columns to match Main UI exactly
    const infoColumns = [
      'Ngày cào', 'Giờ cào', 'Check in', 'Check out',
      'Tên khách sạn', 'Link khách sạn',
      'Số lượng review', 'Điểm review', 'Các tiện nghi được ưa chuộng nhất',
      'Tên hạng phòng', 'Số lượng người', 'Giường', 'Diện tích phòng', 'Các lựa chọn', 'Market'
    ];

    const priceColumns = [
      'Ngày cào', 'Giờ cào', 'Check in', 'Check out',
      'Tên khách sạn', 'Tên hạng phòng', 'Số lượng người',
      'Giá sau giảm', 'Giá gốc', 'Giảm giá', 'Market'
    ];

    const targetColumns = scrapeType === 'price' ? priceColumns : infoColumns;

    // Filter and order data
    const filteredData = data.map((item: any) => {
      const newItem: any = {};
      targetColumns.forEach(col => {
        // Use existing value or empty string if N/A to keep Excel clean, or keep N/A if preferred. 
        // User UI often shows text, so let's keep original values.
        newItem[col] = item[col];
      });
      return newItem;
    });

    import('xlsx').then((XLSX) => {
      const ws = XLSX.utils.json_to_sheet(filteredData)
      const wb = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(wb, ws, "Data")
      const filename = `history_export_${historyId}_${new Date().toISOString().slice(0, 10)}.xlsx`
      XLSX.writeFile(wb, filename)

      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: 'Đã xuất file Excel',
        life: 3000
      })
    })

  } catch (error: any) {
    console.error(error)
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.message || 'Không thể xuất Excel',
      life: 3000
    })
  }
}

async function exportCompetitorsList(historyId: number) {
  try {
    toast.add({
      severity: 'info',
      summary: 'Đang xử lý',
      detail: 'Đang tạo danh sách đối thủ...',
      life: 3000
    })

    const data = await historyStore.exportHistory(historyId)

    if (!data || data.length === 0) {
      toast.add({
        severity: 'warn',
        summary: 'Thông báo',
        detail: 'Không có dữ liệu để xuất',
        life: 3000
      })
      return
    }

    // Define columns for Competitors List
    const competitorsColumns = [
      'Tên khách sạn',
      'Link khách sạn',
      'Tên hạng phòng',
      'Số lượng người',
      'Giường',
      'Diện tích phòng',
      'Các lựa chọn',
      'Các tiện nghi được ưa chuộng nhất',
      'Level đối thủ',
      'Giá bao gồm bữa sáng',
      'Nhóm hạng phòng',
      'Level'
    ];

    // Map data with empty columns for manual fields
    const competitorsData = data.map((item: any) => {
      return {
        'Tên khách sạn': item['Tên khách sạn'] || '',
        'Link khách sạn': item['Link khách sạn'] || '',
        'Tên hạng phòng': item['Tên hạng phòng'] || '',
        'Số lượng người': item['Số lượng người'] || '',
        'Giường': item['Giường'] || '',
        'Diện tích phòng': item['Diện tích phòng'] || '',
        'Các lựa chọn': item['Các lựa chọn'] || '',
        'Các tiện nghi được ưa chuộng nhất': item['Các tiện nghi được ưa chuộng nhất'] || '',
        'Level đối thủ': '',
        'Giá bao gồm bữa sáng': '',
        'Nhóm hạng phòng': '',
        'Level': ''
      };
    });

    import('xlsx').then((XLSX) => {
      const ws = XLSX.utils.json_to_sheet(competitorsData)
      const wb = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(wb, ws, "Competitors")
      const filename = `competitors_list_${historyId}_${new Date().toISOString().slice(0, 10)}.xlsx`
      XLSX.writeFile(wb, filename)

      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: 'Đã tạo Competitors List',
        life: 3000
      })
    })

  } catch (error: any) {
    console.error(error)
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.message || 'Không thể tạo Competitors List',
      life: 3000
    })
  }
}

function confirmDelete(historyId: number) {
  confirm.require({
    message: 'Bạn có chắc muốn xóa lịch sử này?',
    header: 'Xác nhận xóa',
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: 'Xóa',
    rejectLabel: 'Hủy',
    accept: () => deleteHistory(historyId)
  })
}

async function deleteHistory(historyId: number) {
  try {
    await historyStore.deleteHistory(historyId)
    toast.add({
      severity: 'success',
      summary: 'Thành công',
      detail: 'Đã xóa lịch sử',
      life: 3000
    })
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể xóa lịch sử',
      life: 3000
    })
  }
}

function onPageChange(event: any) {
  const page = event.page + 1

  // Update page size if changed
  if (event.rows && event.rows !== historyStore.pageSize) {
    historyStore.pageSize = event.rows
  }

  // Load data for the new page
  const dateFrom = filters.value.dateFrom
    ? formatDateToString(filters.value.dateFrom)
    : undefined
  const dateTo = filters.value.dateTo
    ? formatDateToString(filters.value.dateTo)
    : undefined

  historyStore.fetchHistories(
    page,
    filters.value.source === 'Tất cả' ? undefined : filters.value.source,
    dateFrom,
    dateTo
  )
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleDateString('vi-VN')
}

function formatDateTime(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleString('vi-VN')
}

function formatDateToString(date: Date): string {
  if (!date) return ''
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function formatPrice(price: any): string {
  if (!price) return 'N/A'
  return parseFloat(price).toLocaleString('vi-VN') + ' VND'
}

function getSourceSeverity(source: string): string {
  return source === 'booking' ? 'info' : 'warning'
}
</script>

<style scoped>
.history-list {
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
}

.align-end {
  align-items: flex-end;
}

.w-full {
  width: 100%;
}

.action-buttons {
  display: flex;
  gap: 0.25rem;
}

.detail-info {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  padding: 1rem;
  background: #f8f9fa;
  border-radius: 4px;
}

.info-item {
  font-size: 0.875rem;
}

.mb-4 {
  margin-bottom: 1.5rem;
}

.mt-3 {
  margin-top: 1rem;
}

@media (max-width: 768px) {
  .filters {
    grid-template-columns: 1fr;
  }
}
</style>
