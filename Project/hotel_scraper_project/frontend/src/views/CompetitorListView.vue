<template>
  <div class="competitor-list-view">
    <Card>
      <template #title>
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 2rem;">
          <span><i class="pi pi-users mr-2"></i>Competitor List</span>
          <div style="display: flex; gap: 1rem; align-items: center;">
            <Button label="Import Excel" icon="pi pi-upload" @click="triggerFileInput" severity="primary" />
            <input ref="fileInput" type="file" accept=".xlsx,.xls" @change="handleFileUpload" style="display: none" />
            <Button label="Thêm mới" icon="pi pi-plus" @click="openCreateDialog" severity="primary" />
          </div>
        </div>
      </template>
      <template #content>
        <DataTable 
          :value="competitors" 
          :paginator="true" 
          :rows="20"
          :loading="loading"
          scrollable
          scrollHeight="600px"
          paginatorTemplate="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
          :rowsPerPageOptions="[10,20,50]"
          filterDisplay="row"
          v-model:filters="filters"
        >
          <!-- 1. Tên khách sạn -->
          <Column field="hotel_name" header="Tên khách sạn" :frozen="true" style="min-width: 200px" :sortable="true">
            <template #filter="{ filterModel, filterCallback }">
              <InputText v-model="filterModel.value" @input="filterCallback()" placeholder="Tìm tên KS" class="p-column-filter" />
            </template>
          </Column>
          
          <!-- 2. Link khách sạn -->
          <Column field="hotel_link" header="Link khách sạn" style="min-width: 150px" :sortable="true">
            <template #body="slotProps">
              <a v-if="slotProps.data.hotel_link" :href="slotProps.data.hotel_link" target="_blank" class="text-primary" style="text-decoration: underline;">
                <i class="pi pi-external-link"></i> Link
              </a>
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <InputText v-model="filterModel.value" @input="filterCallback()" placeholder="Tìm link" class="p-column-filter" />
            </template>
          </Column>
          
          <!-- 3. Tên hạng phòng -->
          <Column field="room_type" header="Tên hạng phòng" style="min-width: 180px" :sortable="true">
            <template #filter="{ filterModel, filterCallback }">
              <InputText v-model="filterModel.value" @input="filterCallback()" placeholder="Tìm hạng phòng" class="p-column-filter" />
            </template>
          </Column>
          
          <!-- 4. Số lượng người -->
          <Column field="num_people" header="Số người" style="width: 100px" :sortable="true"></Column>
          
          <!-- 5. Giường -->
          <Column field="bed_info" header="Giường" style="min-width: 150px" :sortable="true"></Column>
          
          <!-- 6. Diện tích phòng -->
          <Column field="room_area" header="Diện tích" style="width: 120px" :sortable="true"></Column>
          
          <!-- 7. Các lựa chọn -->
          <Column field="room_choices" header="Các lựa chọn" style="min-width: 200px" :sortable="true">
            <template #body="slotProps">
              <div style="max-height: 60px; overflow-y: auto;">{{ slotProps.data.room_choices }}</div>
            </template>
          </Column>
          
          <!-- 8. Các tiện nghi được ưa chuộng nhất -->
          <Column field="popular_facilities" header="Tiện nghi" style="min-width: 200px" :sortable="true">
            <template #body="slotProps">
              <div style="max-height: 60px; overflow-y: auto;">{{ slotProps.data.popular_facilities }}</div>
            </template>
          </Column>
          
          <!-- 9. Market -->
          <Column field="market" header="Market" style="width: 150px" :sortable="true">
            <template #body="slotProps">
              {{ getConfigLabel(marketOptions, slotProps.data.market) }}
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <MultiSelect v-model="filterModel.value" @change="filterCallback()" :options="marketOptions" optionLabel="label" optionValue="value" placeholder="Chọn market" :maxSelectedLabels="1" class="p-column-filter" display="chip" />
            </template>
          </Column>
          
          <!-- 10. Cluster -->
          <Column field="cluster" header="Cluster" style="width: 150px" :sortable="true">
            <template #body="slotProps">
              {{ getConfigLabel(clusterOptions, slotProps.data.cluster) }}
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <MultiSelect v-model="filterModel.value" @change="filterCallback()" :options="clusterOptions" optionLabel="label" optionValue="value" placeholder="Chọn cluster" :maxSelectedLabels="1" class="p-column-filter" display="chip" />
            </template>
          </Column>
          
          <!-- 11. Level đối thủ -->
          <Column field="competitor_level" header="Level ĐT" style="width: 150px" :sortable="true">
            <template #body="slotProps">
              {{ getConfigLabel(competitorLevelOptions, slotProps.data.competitor_level) }}
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <MultiSelect v-model="filterModel.value" @change="filterCallback()" :options="competitorLevelOptions" optionLabel="label" optionValue="value" placeholder="Chọn level" :maxSelectedLabels="1" class="p-column-filter" display="chip" />
            </template>
          </Column>
          
          <!-- 12. Giá bao gồm bữa sáng -->
          <Column field="breakfast_included" header="Giá bao gồm bữa sáng" style="width: 180px" :sortable="true">
            <template #body="slotProps">
              {{ getConfigLabel(breakfastOptions, slotProps.data.breakfast_included) }}
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <MultiSelect v-model="filterModel.value" @change="filterCallback()" :options="breakfastOptions" optionLabel="label" optionValue="value" placeholder="Chọn" :maxSelectedLabels="1" class="p-column-filter" display="chip" />
            </template>
          </Column>
          
          <!-- 13. Nhóm hạng phòng -->
          <Column field="room_group" header="Nhóm hạng phòng" style="width: 170px" :sortable="true">
            <template #body="slotProps">
              {{ getConfigLabel(roomGroupOptions, slotProps.data.room_group) }}
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <MultiSelect v-model="filterModel.value" @change="filterCallback()" :options="roomGroupOptions" optionLabel="label" optionValue="value" placeholder="Chọn nhóm" :maxSelectedLabels="1" class="p-column-filter" display="chip" />
            </template>
          </Column>
          
          <!-- 14. Level -->
          <Column field="level" header="Level" style="width: 120px" :sortable="true">
            <template #body="slotProps">
              {{ getConfigLabel(levelOptions, slotProps.data.level) }}
            </template>
            <template #filter="{ filterModel, filterCallback }">
              <MultiSelect v-model="filterModel.value" @change="filterCallback()" :options="levelOptions" optionLabel="label" optionValue="value" placeholder="Chọn level" :maxSelectedLabels="1" class="p-column-filter" display="chip" />
            </template>
          </Column>
          
          <!-- Actions -->
          <Column header="Thao tác" style="width: 100px" :frozen="true" alignFrozen="right">
            <template #body="slotProps">
              <Button icon="pi pi-ellipsis-h" class="p-button-sm p-button-text" @click="toggleMenu($event, slotProps.data)" />
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>

    <!-- Actions Menu -->
    <Menu ref="menu" :model="menuItems" :popup="true" />

    <!-- Create/Edit/View Dialog -->
    <Dialog 
      v-model:visible="showDialog" 
      :header="dialogMode === 'create' ? 'Thêm Competitor' : dialogMode === 'edit' ? 'Sửa Competitor' : 'Chi tiết Competitor'" 
      :style="{ width: '950px', maxHeight: '95vh' }" 
      modal
      :dismissableMask="true"
      class="competitor-dialog"
    >
      <div class="dialog-content">
        <!-- Thông tin khách sạn -->
        <div class="form-section">
          <div class="section-header">
            <i class="pi pi-building"></i>
            <h3>Thông tin khách sạn</h3>
          </div>
          <div class="section-body">
            <div class="form-row single">
              <div class="form-group full">
                <label><i class="pi pi-home"></i> Tên khách sạn</label>
                <InputText v-model="formData.hotel_name" :disabled="dialogMode === 'view'" placeholder="Nhập tên khách sạn" />
              </div>
            </div>
            <div class="form-row single">
              <div class="form-group full">
                <label><i class="pi pi-external-link"></i> Link khách sạn</label>
                <InputText v-model="formData.hotel_link" :disabled="dialogMode === 'view'" placeholder="https://..." />
              </div>
            </div>
          </div>
        </div>

        <!-- Thông tin phòng -->
        <div class="form-section">
          <div class="section-header">
            <i class="pi pi-box"></i>
            <h3>Thông tin phòng</h3>
          </div>
          <div class="section-body">
            <div class="form-row">
              <div class="form-group">
                <label><i class="pi pi-tag"></i> Tên hạng phòng</label>
                <InputText v-model="formData.room_type" :disabled="dialogMode === 'view'" placeholder="VD: Deluxe" />
              </div>
              <div class="form-group">
                <label><i class="pi pi-users"></i> Số lượng người</label>
                <InputNumber v-model="formData.num_people" :disabled="dialogMode === 'view'" placeholder="2" />
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label><i class="pi pi-inbox"></i> Giường</label>
                <InputText v-model="formData.bed_info" :disabled="dialogMode === 'view'" placeholder="VD: 2 giường đơn" />
              </div>
              <div class="form-group">
                <label><i class="pi pi-clone"></i> Diện tích phòng</label>
                <InputText v-model="formData.room_area" :disabled="dialogMode === 'view'" placeholder="VD: 35 m²" />
              </div>
            </div>
            <div class="form-row single">
              <div class="form-group full">
                <label><i class="pi pi-list"></i> Các lựa chọn</label>
                <Textarea v-model="formData.room_choices" rows="2" :disabled="dialogMode === 'view'" placeholder="Nhập các lựa chọn..." :style="{ width: '100%', minWidth: '100%', flex: '1' }" :autoResize="false" />
              </div>
            </div>
            <div class="form-row single">
              <div class="form-group full">
                <label><i class="pi pi-star"></i> Các tiện nghi được ưa chuộng nhất</label>
                <Textarea v-model="formData.popular_facilities" rows="2" :disabled="dialogMode === 'view'" placeholder="Nhập tiện nghi..." :style="{ width: '100%', minWidth: '100%', flex: '1' }" :autoResize="false" />
              </div>
            </div>
          </div>
        </div>

        <!-- Phân loại -->
        <div class="form-section">
          <div class="section-header">
            <i class="pi pi-chart-bar"></i>
            <h3>Phân loại</h3>
          </div>
          <div class="section-body">
            <div class="form-row">
              <div class="form-group">
                <label><i class="pi pi-globe"></i> Market</label>
                <Dropdown v-model="formData.market" :options="marketOptions" optionLabel="label" optionValue="value" placeholder="Chọn market" :disabled="dialogMode === 'view'" />
              </div>
              <div class="form-group">
                <label><i class="pi pi-map-marker"></i> Cluster</label>
                <Dropdown v-model="formData.cluster" :options="clusterOptions" optionLabel="label" optionValue="value" placeholder="Chọn cluster" :disabled="dialogMode === 'view'" />
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label><i class="pi pi-shield"></i> Level đối thủ</label>
                <Dropdown v-model="formData.competitor_level" :options="competitorLevelOptions" optionLabel="label" optionValue="value" placeholder="Chọn level" :disabled="dialogMode === 'view'" />
              </div>
              <div class="form-group">
                <label><i class="pi pi-sun"></i> Giá bao gồm bữa sáng</label>
                <Dropdown v-model="formData.breakfast_included" :options="breakfastOptions" optionLabel="label" optionValue="value" placeholder="Chọn" :disabled="dialogMode === 'view'" />
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label><i class="pi pi-ticket"></i> Nhóm hạng phòng</label>
                <Dropdown v-model="formData.room_group" :options="roomGroupOptions" optionLabel="label" optionValue="value" placeholder="Chọn nhóm" :disabled="dialogMode === 'view'" />
              </div>
              <div class="form-group">
                <label><i class="pi pi-bookmark"></i> Level</label>
                <Dropdown v-model="formData.level" :options="levelOptions" optionLabel="label" optionValue="value" placeholder="Chọn level" :disabled="dialogMode === 'view'" />
              </div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <Button label="Đóng" icon="pi pi-times" @click="showDialog = false" severity="secondary" text class="btn-cancel" />
          <Button v-if="dialogMode !== 'view'" label="Lưu" icon="pi pi-check" @click="saveCompetitor" :loading="saving" severity="primary" class="btn-save" />
        </div>
      </template>
    </Dialog>

    <!-- Import Errors Dialog -->
    <Dialog v-model:visible="showErrorDialog" header="Lỗi Import" :style="{ width: '900px' }" modal>
      <DataTable :value="importErrors" scrollable scrollHeight="400px" responsiveLayout="scroll">
        <Column field="row" header="Hàng" style="width: 80px"></Column>
        <Column field="error" header="Lỗi" style="min-width: 300px"></Column>
      </DataTable>
      <template #footer>
        <Button label="Đóng" icon="pi pi-times" @click="showErrorDialog = false" severity="secondary" />
      </template>
    </Dialog>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'primevue/usetoast'
import { useConfirm } from 'primevue/useconfirm'
import axios from 'axios'
import Card from 'primevue/card'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import MultiSelect from 'primevue/multiselect'
import InputNumber from 'primevue/inputnumber'
import Textarea from 'primevue/textarea'
import Toast from 'primevue/toast'
import Menu from 'primevue/menu'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const toast = useToast()
const confirm = useConfirm()

interface CompetitorData {
  id?: number
  hotel_name?: string
  hotel_link?: string
  room_type?: string
  num_people?: number
  bed_info?: string
  room_area?: string
  room_choices?: string
  popular_facilities?: string
  market?: string
  cluster?: string
  competitor_level?: string
  breakfast_included?: string
  room_group?: string
  level?: string
}

const competitors = ref<CompetitorData[]>([])
const loading = ref(false)
const showDialog = ref(false)
const dialogMode = ref<'create' | 'edit' | 'view'>('create')
const saving = ref(false)
const fileInput = ref<HTMLInputElement>()
const showErrorDialog = ref(false)
const importErrors = ref<Array<{ row: number, error: string }>>([])
const menu = ref()
const selectedCompetitor = ref<CompetitorData | null>(null)

const menuItems = ref([
  {
    label: 'Xem chi tiết',
    icon: 'pi pi-eye',
    command: () => viewCompetitor()
  },
  {
    label: 'Chỉnh sửa',
    icon: 'pi pi-pencil',
    command: () => editCompetitor()
  },
  {
    separator: true
  },
  {
    label: 'Xóa',
    icon: 'pi pi-trash',
    class: 'text-danger',
    command: () => deleteCompetitor()
  }
])

const formData = ref<CompetitorData>({
  hotel_name: '',
  hotel_link: '',
  room_type: '',
  num_people: undefined,
  bed_info: '',
  room_area: '',
  room_choices: '',
  popular_facilities: '',
  market: '',
  cluster: '',
  competitor_level: '',
  breakfast_included: '',
  room_group: '',
  level: ''
})

const filters = ref({
  hotel_name: { value: null, matchMode: 'contains' },
  hotel_link: { value: null, matchMode: 'contains' },
  room_type: { value: null, matchMode: 'contains' },
  market: { value: null, matchMode: 'in' },
  cluster: { value: null, matchMode: 'in' },
  competitor_level: { value: null, matchMode: 'in' },
  breakfast_included: { value: null, matchMode: 'in' },
  room_group: { value: null, matchMode: 'in' },
  level: { value: null, matchMode: 'in' }
})

// Config options - will be loaded from API
const marketOptions = ref<Array<{ label: string, value: string }>>([])
const clusterOptions = ref<Array<{ label: string, value: string }>>([])
const competitorLevelOptions = ref<Array<{ label: string, value: string }>>([])
const breakfastOptions = ref<Array<{ label: string, value: string }>>([])
const roomGroupOptions = ref<Array<{ label: string, value: string }>>([])
const levelOptions = ref<Array<{ label: string, value: string }>>([])

async function loadConfigOptions() {
  try {
    const response = await axios.get(`${API_BASE_URL}/api/config`)
    const configs = response.data

    marketOptions.value = (configs.market || []).map((c: any) => ({ label: c.config_value, value: c.config_key }))
    clusterOptions.value = (configs.cluster || []).map((c: any) => ({ label: c.config_value, value: c.config_key }))
    competitorLevelOptions.value = (configs.competitor_level || []).map((c: any) => ({ label: c.config_value, value: c.config_key }))
    breakfastOptions.value = (configs.breakfast || []).map((c: any) => ({ label: c.config_value, value: c.config_key }))
    roomGroupOptions.value = (configs.room_group || []).map((c: any) => ({ label: c.config_value, value: c.config_key }))
    levelOptions.value = (configs.level || []).map((c: any) => ({ label: c.config_value, value: c.config_key }))
  } catch (error) {
    console.error('Failed to load config options:', error)
  }
}

function getConfigLabel(options: Array<{ label: string, value: string }>, value: string | undefined): string {
  if (!value) return ''
  const option = options.find(o => o.value === value)
  return option ? option.label : value
}

function triggerFileInput() {
  fileInput.value?.click()
}

async function loadCompetitors() {
  loading.value = true
  try {
    const response = await axios.get(`${API_BASE_URL}/api/competitors`, {
      params: { limit: 1000 }
    })
    competitors.value = response.data.data
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải danh sách competitor',
      life: 3000
    })
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  dialogMode.value = 'create'
  formData.value = {
    hotel_name: '',
    hotel_link: '',
    room_type: '',
    num_people: undefined,
    bed_info: '',
    room_area: '',
    room_choices: '',
    popular_facilities: '',
    market: '',
    cluster: '',
    competitor_level: '',
    breakfast_included: '',
    room_group: '',
    level: ''
  }
  showDialog.value = true
}

function toggleMenu(event: Event, competitor: CompetitorData) {
  selectedCompetitor.value = competitor
  menu.value.toggle(event)
}

async function loadCompetitorById(id: number) {
  loading.value = true
  try {
    const response = await axios.get(`${API_BASE_URL}/api/competitors/${id}`)
    return response.data
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải thông tin competitor',
      life: 3000
    })
    return null
  } finally {
    loading.value = false
  }
}

async function viewCompetitor() {
  if (!selectedCompetitor.value?.id) return
  
  const data = await loadCompetitorById(selectedCompetitor.value.id)
  if (data) {
    formData.value = { ...data }
    dialogMode.value = 'view'
    showDialog.value = true
  }
}

async function editCompetitor() {
  if (!selectedCompetitor.value?.id) return
  
  const data = await loadCompetitorById(selectedCompetitor.value.id)
  if (data) {
    formData.value = { ...data }
    dialogMode.value = 'edit'
    showDialog.value = true
  }
}

async function deleteCompetitor() {
  if (!selectedCompetitor.value) return

  confirm.require({
    message: `Bạn có chắc chắn muốn xóa competitor "${selectedCompetitor.value.hotel_name}"?`,
    header: 'Xác nhận xóa',
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: 'Xóa',
    rejectLabel: 'Hủy',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await axios.delete(`${API_BASE_URL}/api/competitors/${selectedCompetitor.value?.id}`)
        toast.add({
          severity: 'success',
          summary: 'Thành công',
          detail: 'Đã xóa competitor',
          life: 3000
        })
        await loadCompetitors()
      } catch (error: any) {
        toast.add({
          severity: 'error',
          summary: 'Lỗi',
          detail: 'Không thể xóa competitor',
          life: 3000
        })
      }
    }
  })
}

async function saveCompetitor() {
  if (!formData.value.hotel_name || !formData.value.room_type) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Vui lòng nhập tên khách sạn và tên hạng phòng',
      life: 3000
    })
    return
  }

  saving.value = true
  try {
    if (dialogMode.value === 'create') {
      await axios.post(`${API_BASE_URL}/api/competitors`, formData.value)
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: 'Đã thêm competitor',
        life: 3000
      })
    } else {
      await axios.put(`${API_BASE_URL}/api/competitors/${formData.value.id}`, formData.value)
      toast.add({
        severity: 'success',
        summary: 'Thành công',
        detail: 'Đã cập nhật competitor',
        life: 3000
      })
    }
    showDialog.value = false
    await loadCompetitors()
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.response?.data?.detail || 'Không thể lưu competitor',
      life: 3000
    })
  } finally {
    saving.value = false
  }
}

async function handleFileUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  const formData = new FormData()
  formData.append('file', file)

  loading.value = true
  try {
    const response = await axios.post(`${API_BASE_URL}/api/competitors/import`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })

    const message = response.data.skipped > 0 
      ? `Đã tạo: ${response.data.created}, Cập nhật: ${response.data.updated}, Bỏ qua: ${response.data.skipped}`
      : `Đã tạo: ${response.data.created}, Cập nhật: ${response.data.updated}`

    toast.add({
      severity: response.data.errors && response.data.errors.length > 0 ? 'warn' : 'success',
      summary: 'Import hoàn tất',
      detail: message,
      life: 5000
    })

    if (response.data.errors && response.data.errors.length > 0) {
      // Errors now come as objects with row and error fields
      importErrors.value = response.data.errors.map((err: any) => {
        if (typeof err === 'object' && err.row && err.error) {
          return err
        } else if (typeof err === 'string') {
          // Fallback for old format
          const match = err.match(/Row (\d+): (.+)/)
          if (match) {
            return { row: parseInt(match[1]), error: match[2] }
          }
          return { row: 0, error: err }
        }
        return { row: 0, error: String(err) }
      })
      showErrorDialog.value = true
    }

    await loadCompetitors()
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: error.response?.data?.detail || 'Không thể import file',
      life: 3000
    })
  } finally {
    loading.value = false
    // Reset file input
    target.value = ''
  }
}

onMounted(async () => {
  await loadConfigOptions()
  await loadCompetitors()
})
</script>

<style scoped>
.competitor-list-view {
  padding: 2rem 0;
}

:deep(.p-column-filter) {
  width: 100%;
}

:deep(.p-datatable .p-datatable-tbody > tr > td) {
  white-space: normal;
  word-wrap: break-word;
}

:deep(.text-primary) {
  color: #3b82f6;
  cursor: pointer;
}

:deep(.text-primary:hover) {
  color: #2563eb;
}

/* ==================== CUSTOM DIALOG STYLING ==================== */

/* Dialog Header */
:deep(.competitor-dialog .p-dialog-header) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 1.5rem;
  border-radius: 12px 12px 0 0;
}

:deep(.competitor-dialog .p-dialog-title) {
  font-size: 1.5rem;
  font-weight: 700;
  color: white;
}

:deep(.competitor-dialog .p-dialog-header-icon) {
  color: white;
  width: 2.5rem;
  height: 2.5rem;
  transition: all 0.2s;
}

:deep(.competitor-dialog .p-dialog-header-icon:hover) {
  background: rgba(255, 255, 255, 0.2);
  transform: scale(1.1);
}

:deep(.competitor-dialog .p-dialog-content) {
  padding: 0;
  background: #f8fafc;
  max-height: calc(95vh - 180px);
  overflow-y: auto;
}

/* Dialog Content */
.dialog-content {
  padding: 1.5rem;
  width: 100%;
  box-sizing: border-box;
}

/* Form Sections */
.form-section {
  background: white;
  border-radius: 12px;
  margin-bottom: 1.5rem;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  transition: all 0.3s ease;
  width: 100%;
}

.form-section:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  transform: translateY(-2px);
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
  border-bottom: 2px solid #e2e8f0;
}

.section-header i {
  font-size: 1.25rem;
  color: #667eea;
}

.section-header h3 {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: 0.3px;
}

.section-body {
  padding: 1.5rem;
  width: 100%;
  box-sizing: border-box;
}

/* Form Rows and Groups */
.form-row {
  display: flex;
  gap: 1.25rem;
  margin-bottom: 1.25rem;
}

.form-row:last-child {
  margin-bottom: 0;
}

/* Single field rows should display at full width */
.form-row.single {
  display: block !important;
  width: 100% !important;
}

.form-row.single .form-group {
  width: 100% !important;
  max-width: 100% !important;
  flex: 1 1 100% !important;
}

/* Ensure all inputs in single rows are full width */
:deep(.form-row.single .p-inputtext),
:deep(.form-row.single .p-inputtextarea),
:deep(.form-row.single .p-dropdown),
:deep(.form-row.single .p-inputnumber),
:deep(.form-row.single textarea) {
  width: 100% !important;
  max-width: 100% !important;
}

/* Textarea wrapper */
:deep(.form-row.single .form-group.full) {
  width: 100% !important;
  max-width: 100% !important;
}

:deep(.form-row.single .form-group.full textarea),
:deep(.form-row.single .form-group.full .p-inputtextarea) {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}

.form-group {
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
}

.form-group.full {
  flex: 1 1 100%;
  width: 100% !important;
}

/* Ensure all form-group inputs fill their container */
:deep(.form-group .p-inputtext),
:deep(.form-group .p-inputtextarea),
:deep(.form-group .p-dropdown),
:deep(.form-group .p-inputnumber) {
  width: 100%;
}

.form-group label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  font-size: 0.875rem;
  color: #475569;
  margin-bottom: 0.5rem;
  letter-spacing: 0.3px;
}

/* CRITICAL: Force Textarea to full width */
:deep(.form-group.full .p-inputtextarea) {
  width: 100% !important;
  min-width: 100% !important;
  max-width: 100% !important;
  display: block !important;
  flex: 1 !important;
}

:deep(.form-row.single .form-group.full) {
  width: 100% !important;
  display: block !important;
}

.form-group label i {
  font-size: 0.875rem;
  color: #94a3b8;
}

/* Custom Input Styling */
:deep(.competitor-dialog .p-inputtext),
:deep(.competitor-dialog .p-dropdown),
:deep(.competitor-dialog .p-inputnumber-input),
:deep(.competitor-dialog .p-inputtextarea),
:deep(.competitor-dialog textarea),
:deep(.competitor-dialog input[type="text"]) {
  border: 2px solid #e2e8f0;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  font-size: 0.9375rem;
  transition: all 0.2s ease;
  background: white;
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}

:deep(.competitor-dialog .p-inputtext:hover),
:deep(.competitor-dialog .p-dropdown:hover),
:deep(.competitor-dialog .p-inputnumber-input:hover),
:deep(.competitor-dialog .p-inputtextarea:hover),
:deep(.competitor-dialog textarea:hover) {
  border-color: #cbd5e1;
  width: 100% !important;
  max-width: 100% !important;
}

:deep(.competitor-dialog .p-inputtext:focus),
:deep(.competitor-dialog .p-dropdown:focus),
:deep(.competitor-dialog .p-inputnumber-input:focus),
:deep(.competitor-dialog .p-inputtextarea:focus),
:deep(.competitor-dialog textarea:focus) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
  outline: none;
  width: 100% !important;
  max-width: 100% !important;
}

/* Disabled State */
:deep(.competitor-dialog .p-inputtext:disabled),
:deep(.competitor-dialog .p-dropdown:disabled),
:deep(.competitor-dialog .p-inputnumber-input:disabled),
:deep(.competitor-dialog .p-inputtextarea:disabled) {
  background: #f1f5f9 !important;
  border-color: #e2e8f0 !important;
  color: #64748b !important;
  opacity: 1 !important;
  cursor: not-allowed;
}

:deep(.competitor-dialog .p-dropdown:disabled .p-dropdown-label) {
  color: #64748b !important;
}

/* Dropdown specific */
/* Dropdown specific */
:deep(.competitor-dialog .p-dropdown) {
  width: 100%;
  display: flex;
}

:deep(.competitor-dialog .p-dropdown-label) {
  padding: 0;
  flex: 1;
}

:deep(.competitor-dialog .p-dropdown-trigger) {
  width: 2.5rem;
  color: #94a3b8;
  flex-shrink: 0;
}

/* InputNumber specific */
:deep(.competitor-dialog .p-inputnumber) {
  width: 100%;
}

:deep(.competitor-dialog .p-inputnumber-input) {
  width: 100%;
}

/* Textarea */
:deep(.competitor-dialog .p-inputtextarea),
:deep(.competitor-dialog textarea) {
  resize: vertical;
  min-height: 70px;
  line-height: 1.5;
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}

/* Force textarea in single rows to be full width */
:deep(.competitor-dialog .form-row.single textarea),
:deep(.competitor-dialog .form-row.single .p-inputtextarea) {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}

/* Dialog Footer */
:deep(.competitor-dialog .p-dialog-footer) {
  padding: 1.5rem;
  background: white;
  border-top: 2px solid #e2e8f0;
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
}

/* Custom Buttons */
:deep(.competitor-dialog .btn-cancel) {
  padding: 0.75rem 1.5rem;
  font-weight: 600;
  border-radius: 8px;
  transition: all 0.2s;
  color: #64748b;
}

:deep(.competitor-dialog .btn-cancel:hover) {
  background: #f1f5f9;
  color: #334155;
}

:deep(.competitor-dialog .btn-save) {
  padding: 0.75rem 2rem;
  font-weight: 600;
  border-radius: 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  transition: all 0.3s;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

:deep(.competitor-dialog .btn-save:hover) {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(102, 126, 234, 0.4);
}

:deep(.competitor-dialog .btn-save .pi) {
  font-size: 1rem;
}

/* Scrollbar Styling */
.dialog-content::-webkit-scrollbar {
  width: 8px;
}

.dialog-content::-webkit-scrollbar-track {
  background: #f1f5f9;
  border-radius: 4px;
}

.dialog-content::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.dialog-content::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* Responsive */
@media (max-width: 768px) {
  .form-row {
    flex-direction: column;
    gap: 1rem;
  }
  
  :deep(.competitor-dialog) {
    width: 95vw !important;
  }
}
</style>
