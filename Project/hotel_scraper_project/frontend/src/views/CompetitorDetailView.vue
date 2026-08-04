<template>
  <div class="competitor-detail-view">
    <Card>
      <template #title>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <Button icon="pi pi-arrow-left" text @click="router.back()" class="mr-2" />
            <span>Chi tiết Competitor</span>
          </div>
          <div style="display: flex; gap: 1rem;">
            <Button label="Chỉnh sửa" icon="pi pi-pencil" @click="goToEdit" severity="primary" />
            <Button label="Xóa" icon="pi pi-trash" @click="confirmDelete" severity="danger" />
          </div>
        </div>
      </template>
      <template #content>
        <div v-if="loading" class="text-center py-5">
          <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
        </div>
        <div v-else-if="competitor" class="competitor-details">
          <div class="grid">
            <div class="col-12 md:col-4">
              <div class="field">
                <label class="font-bold">Tên khách sạn</label>
                <div class="field-value">{{ competitor.hotel_name || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Market</label>
                <div class="field-value">{{ getConfigLabel(marketOptions, competitor.market) || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Cluster</label>
                <div class="field-value">{{ getConfigLabel(clusterOptions, competitor.cluster) || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-2">
              <div class="field">
                <label class="font-bold">Link khách sạn</label>
                <div class="field-value" style="text-align: center;">
                  <a v-if="competitor.hotel_link" :href="competitor.hotel_link" target="_blank" class="link-icon">
                    <i class="pi pi-external-link" style="font-size: 1.2rem; color: #3b82f6;"></i>
                  </a>
                  <span v-else>-</span>
                </div>
              </div>
            </div>
            <div class="col-12 md:col-4">
              <div class="field">
                <label class="font-bold">Tên hạng phòng</label>
                <div class="field-value">{{ competitor.room_type || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-2">
              <div class="field">
                <label class="font-bold">Số người</label>
                <div class="field-value">{{ competitor.num_people || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Giường</label>
                <div class="field-value">{{ competitor.bed_info || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Diện tích phòng</label>
                <div class="field-value">{{ competitor.room_area || '-' }}</div>
              </div>
            </div>
            <div class="col-12">
              <div class="field">
                <label class="font-bold">Các lựa chọn</label>
                <div class="field-value">{{ competitor.room_choices || '-' }}</div>
              </div>
            </div>
            <div class="col-12">
              <div class="field">
                <label class="font-bold">Các tiện nghi được ưa chuộng nhất</label>
                <div class="field-value">{{ competitor.popular_facilities || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Level đối thủ</label>
                <div class="field-value">{{ getConfigLabel(competitorLevelOptions, competitor.competitor_level) || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Bữa sáng</label>
                <div class="field-value">{{ getConfigLabel(breakfastOptions, competitor.breakfast_included) || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Nhóm hạng phòng</label>
                <div class="field-value">{{ getConfigLabel(roomGroupOptions, competitor.room_group) || '-' }}</div>
              </div>
            </div>
            <div class="col-12 md:col-3">
              <div class="field">
                <label class="font-bold">Level</label>
                <div class="field-value">{{ getConfigLabel(levelOptions, competitor.level) || '-' }}</div>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="text-center py-5">
          <p>Không tìm thấy thông tin competitor</p>
        </div>
      </template>
    </Card>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'primevue/usetoast'
import { useConfirm } from 'primevue/useconfirm'
import axios from 'axios'
import Card from 'primevue/card'
import Button from 'primevue/button'
import Toast from 'primevue/toast'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const router = useRouter()
const route = useRoute()
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

const competitor = ref<CompetitorData | null>(null)
const loading = ref(false)
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

async function loadCompetitor() {
  loading.value = true
  try {
    const id = route.params.id
    const response = await axios.get(`${API_BASE_URL}/api/competitors/${id}`)
    competitor.value = response.data
  } catch (error: any) {
    toast.add({
      severity: 'error',
      summary: 'Lỗi',
      detail: 'Không thể tải thông tin competitor',
      life: 3000
    })
  } finally {
    loading.value = false
  }
}

function goToEdit() {
  router.push({ name: 'competitor-edit', params: { id: route.params.id } })
}

function confirmDelete() {
  confirm.require({
    message: `Bạn có chắc chắn muốn xóa competitor "${competitor.value?.hotel_name}"?`,
    header: 'Xác nhận xóa',
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: 'Xóa',
    rejectLabel: 'Hủy',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await axios.delete(`${API_BASE_URL}/api/competitors/${route.params.id}`)
        toast.add({
          severity: 'success',
          summary: 'Thành công',
          detail: 'Đã xóa competitor',
          life: 3000
        })
        router.push({ name: 'competitor-list' })
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

onMounted(async () => {
  await loadConfigOptions()
  await loadCompetitor()
})
</script>

<style scoped>
.competitor-detail-view {
  padding: 1.5rem 0;
}

:deep(.p-card) {
  border-radius: 12px;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
}

:deep(.p-card-title) {
  font-size: 1.5rem;
  font-weight: 600;
  padding: 1.5rem 1.5rem 0 1.5rem;
}

:deep(.p-card-content) {
  padding: 1.5rem;
}

.competitor-details {
  background: white;
}

.field {
  margin-bottom: 1.25rem;
}

.field label {
  display: block;
  margin-bottom: 0.5rem;
  color: #64748b;
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.field-value {
  padding: 0.875rem 1rem;
  background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  min-height: 45px;
  font-size: 0.95rem;
  color: #1e293b;
  transition: all 0.2s;
  display: flex;
  align-items: center;
}

.field-value:hover {
  border-color: #cbd5e1;
  box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
}

.link-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.5rem;
  border-radius: 6px;
  transition: all 0.2s;
  background: rgba(59, 130, 246, 0.1);
}

.link-icon:hover {
  background: rgba(59, 130, 246, 0.2);
  transform: scale(1.1);
}

.link-icon i {
  transition: color 0.2s;
}

.link-icon:hover i {
  color: #2563eb !important;
}

:deep(.p-button) {
  border-radius: 6px;
  font-weight: 500;
  transition: all 0.2s;
}

:deep(.p-button:hover) {
  transform: translateY(-1px);
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

:deep(.grid) {
  margin: 0 -0.5rem;
}

:deep(.grid > div) {
  padding: 0 0.5rem;
}
</style>
