<template>
  <div class="saved-sources-manager">
    <div class="mb-3" style="display: flex; flex-direction: row; align-items: center; gap: 1rem">
      <h4 class="m-0" style="line-height: 1; flex-shrink: 0">Nguồn dữ liệu đã lưu</h4>
      <Button 
        label="Làm mới" 
        icon="pi pi-refresh" 
        size="small"
        text
        @click="loadSources"
        style="flex-shrink: 0"
      />
    </div>

    <div v-if="loading" class="text-center py-3">
      <ProgressBar mode="indeterminate" />
    </div>

    <div v-else-if="sources.length === 0" style="display: flex; flex-direction: row; gap: 0.5rem; padding: 1rem 0; color: var(--text-color-secondary)">
      <i class="pi pi-inbox" style="font-size: 1.5rem"></i>
      <p class="m-0">Chưa có nguồn dữ liệu nào được lưu</p>
    </div>

    <div v-else class="sources-list">
      <div 
        v-for="source in sources" 
        :key="source.id"
        class="source-item"
        :class="{ 'source-active': source.is_active }"
      >
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 1rem">
          <div style="display: flex; align-items: center; gap: 0.75rem; flex: 1; min-width: 0">
            <i 
              :class="source.source_type === 'file' ? 'pi pi-file-excel' : 'pi pi-link'"
              style="font-size: 1.25rem; color: var(--primary-color); flex-shrink: 0"
            ></i>
            <div style="flex: 1; min-width: 0">
              <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem">
                <strong style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis">{{ source.name }}</strong>
                <!-- <Tag v-if="source.is_active" severity="success" value="Active" style="flex-shrink: 0" /> -->
              </div>
              <small style="color: var(--text-color-secondary); font-size: 0.75rem">
                {{ formatDate(source.created_at) }}
              </small>
            </div>
          </div>
          
          <div style="display: flex; gap: 0.5rem; flex-shrink: 0">
            <Button 
              icon="pi pi-check" 
              size="small"
              outlined
              severity="primary"
              v-tooltip.top="'Sử dụng nguồn này'"
              @click="useSource(source)"
              
            />
            <Button 
              icon="pi pi-trash" 
              size="small"
              outlined
              severity="danger"
              v-tooltip.top="'Xóa'"
              @click="confirmDelete(source)"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'primevue/usetoast'
import { useConfirm } from 'primevue/useconfirm'
import axios from 'axios'

interface SavedSource {
  id: number
  name: string
  source_type: 'file' | 'google_sheets'
  file_path: string | null
  sheets_url: string | null
  is_active: boolean
  created_at: string
}

const emit = defineEmits<{
  (e: 'source-selected', sourceId: number): void
}>()

const toast = useToast()
const confirm = useConfirm()
const sources = ref<SavedSource[]>([])
const loading = ref(false)

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

onMounted(() => {
  loadSources()
})

async function loadSources() {
  loading.value = true
  try {
    const response = await axios.get(`${API_URL}/sources`)
    sources.value = response.data.sources || []
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải danh sách nguồn dữ liệu',
      life: 3000
    })
  } finally {
    loading.value = false
  }
}

async function useSource(source: SavedSource) {
  try {
    await axios.post(`${API_URL}/sources/${source.id}/activate`)
    
    toast.add({
      severity: 'success',
      summary: 'Thành công',
      detail: `Đã kích hoạt nguồn: ${source.name}`,
      life: 3000
    })
    
    emit('source-selected', source.id)
    await loadSources()
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể kích hoạt nguồn dữ liệu',
      life: 3000
    })
  }
}

function confirmDelete(source: SavedSource) {
  confirm.require({
    message: `Bạn có chắc muốn xóa nguồn "${source.name}"?`,
    header: 'Xác nhận xóa',
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: 'Xóa',
    rejectLabel: 'Hủy',
    accept: () => deleteSource(source.id)
  })
}

async function deleteSource(sourceId: number) {
  try {
    await axios.delete(`${API_URL}/sources/${sourceId}`)
    
    toast.add({
      severity: 'success',
      summary: 'Đã xóa',
      detail: 'Nguồn dữ liệu đã được xóa',
      life: 3000
    })
    
    await loadSources()
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể xóa nguồn dữ liệu',
      life: 3000
    })
  }
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('vi-VN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}
</script>

<style scoped>
.source-item {
  padding: 0.875rem;
  margin-bottom: 0.5rem;
  background: var(--surface-50);
  border: 2px solid var(--surface-200);
  border-radius: 8px;
  transition: all 0.2s;
}

.source-item:hover {
  background: var(--surface-100);
  border-color: var(--primary-color);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.source-active {
  border-color: var(--green-500);
  background: var(--green-50);
}

.source-active:hover {
  border-color: var(--green-600);
}
</style>
