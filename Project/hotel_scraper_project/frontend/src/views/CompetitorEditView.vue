<template>
  <div class="competitor-edit-view">
    <Card>
      <template #title>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <Button icon="pi pi-arrow-left" text @click="router.back()" class="mr-2" />
            <span>Chỉnh sửa Competitor</span>
          </div>
        </div>
      </template>
      <template #content>
        <div v-if="loading" class="text-center py-5">
          <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
        </div>
        <form v-else-if="formData" @submit.prevent="saveCompetitor" class="p-fluid">
          <div class="grid">
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="market">Market</label>
                <Dropdown v-model="formData.market" :options="marketOptions" optionLabel="label" optionValue="value" placeholder="Chọn market" />
              </div>
            </div>
            <div class="col-12">
              <div class="field">
                <label for="hotel_name">Tên khách sạn *</label>
                <InputText v-model="formData.hotel_name" required />
              </div>
            </div>
            <div class="col-12">
              <div class="field">
                <label for="hotel_link">Link khách sạn</label>
                <InputText v-model="formData.hotel_link" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="room_type">Tên hạng phòng</label>
                <InputText v-model="formData.room_type" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="num_people">Số lượng người</label>
                <InputNumber v-model="formData.num_people" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="bed_info">Giường</label>
                <InputText v-model="formData.bed_info" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="room_area">Diện tích phòng</label>
                <InputText v-model="formData.room_area" />
              </div>
            </div>
            <div class="col-12">
              <div class="field">
                <label for="room_choices">Các lựa chọn</label>
                <Textarea v-model="formData.room_choices" rows="2" />
              </div>
            </div>
            <div class="col-12">
              <div class="field">
                <label for="popular_facilities">Các tiện nghi được ưa chuộng nhất</label>
                <Textarea v-model="formData.popular_facilities" rows="2" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="cluster">Cluster</label>
                <Dropdown v-model="formData.cluster" :options="clusterOptions" optionLabel="label" optionValue="value" placeholder="Chọn cluster" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="competitor_level">Level đối thủ</label>
                <Dropdown v-model="formData.competitor_level" :options="competitorLevelOptions" optionLabel="label" optionValue="value" placeholder="Chọn level" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="breakfast_included">Giá bao gồm bữa sáng</label>
                <Dropdown v-model="formData.breakfast_included" :options="breakfastOptions" optionLabel="label" optionValue="value" placeholder="Chọn" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="room_group">Nhóm hạng phòng</label>
                <Dropdown v-model="formData.room_group" :options="roomGroupOptions" optionLabel="label" optionValue="value" placeholder="Chọn nhóm" />
              </div>
            </div>
            <div class="col-12 md:col-6">
              <div class="field">
                <label for="level">Level</label>
                <Dropdown v-model="formData.level" :options="levelOptions" optionLabel="label" optionValue="value" placeholder="Chọn level" />
              </div>
            </div>
          </div>
          <div class="flex justify-content-end gap-2 mt-4 form-actions">
            <Button label="Hủy" icon="pi pi-times" @click="router.back()" severity="secondary" text />
            <Button label="Lưu" icon="pi pi-check" type="submit" :loading="saving" severity="primary" />
          </div>
        </form>
      </template>
    </Card>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'primevue/usetoast'
import axios from 'axios'
import Card from 'primevue/card'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import InputNumber from 'primevue/inputnumber'
import Textarea from 'primevue/textarea'
import Toast from 'primevue/toast'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const router = useRouter()
const route = useRoute()
const toast = useToast()

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

const loading = ref(false)
const saving = ref(false)
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

async function loadCompetitor() {
  loading.value = true
  try {
    const id = route.params.id
    const response = await axios.get(`${API_BASE_URL}/api/competitors/${id}`)
    formData.value = { ...response.data }
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

async function saveCompetitor() {
  if (!formData.value.hotel_name) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Vui lòng nhập tên khách sạn',
      life: 3000
    })
    return
  }

  saving.value = true
  try {
    await axios.put(`${API_BASE_URL}/api/competitors/${route.params.id}`, formData.value)
    toast.add({
      severity: 'success',
      summary: 'Thành công',
      detail: 'Đã cập nhật competitor',
      life: 3000
    })
    router.push({ name: 'competitor-view', params: { id: route.params.id } })
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

onMounted(async () => {
  await loadConfigOptions()
  await loadCompetitor()
})
</script>

<style scoped>
.competitor-edit-view {
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

.field {
  margin-bottom: 1.25rem;
}

.field label {
  display: block;
  margin-bottom: 0.5rem;
  color: #475569;
  font-size: 0.875rem;
  font-weight: 600;
}

:deep(.p-inputtext),
:deep(.p-dropdown),
:deep(.p-inputnumber input),
:deep(.p-inputtextarea) {
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  padding: 0.75rem 1rem;
  transition: all 0.2s;
}

:deep(.p-inputtext:hover),
:deep(.p-dropdown:hover),
:deep(.p-inputnumber input:hover),
:deep(.p-inputtextarea:hover) {
  border-color: #94a3b8;
}

:deep(.p-inputtext:focus),
:deep(.p-dropdown:focus),
:deep(.p-inputnumber input:focus),
:deep(.p-inputtextarea:focus) {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

:deep(.p-button) {
  border-radius: 6px;
  font-weight: 500;
  padding: 0.75rem 1.5rem;
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

.form-actions {
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid #e2e8f0;
}
</style>
