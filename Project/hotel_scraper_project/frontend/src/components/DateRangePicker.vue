<template>
  <div class="date-range-picker">
    <!-- Add Date Range Section -->
    <div class="add-section">
      <div class="grid">
        <div class="col">
          <label>Ngày nhận phòng (Check-in)</label>
          <Calendar 
            v-model="newCheckin" 
            dateFormat="yy-mm-dd"
            :minDate="today"
            showIcon
            class="w-full"
          />
        </div>
        <div class="col">
          <label>Ngày trả phòng (Check-out)</label>
          <Calendar 
            v-model="newCheckout" 
            dateFormat="yy-mm-dd"
            :minDate="minCheckout"
            showIcon
            class="w-full"
          />
        </div>
        <div class="col-auto align-end">
          <Button 
            label="➕ Thêm" 
            @click="addDateRange"
            :disabled="!canAdd"
          />
        </div>
      </div>
    </div>

    <!-- Date Ranges List -->
    <div v-if="dateRanges.length > 0" class="ranges-list mt-4">
      <h4>📅 Danh sách các cặp ngày đã chọn ({{ dateRanges.length }})</h4>
      
      <div v-for="(range, index) in dateRanges" :key="index" class="range-item">
        <div class="range-info">
          <i class="pi pi-calendar"></i>
          <span class="range-text">
            {{ formatDate(range.checkin) }} → {{ formatDate(range.checkout) }}
            <small class="nights">({{ calculateNights(range) }} đêm)</small>
          </span>
        </div>
        <Button 
          icon="pi pi-trash" 
          class="p-button-danger p-button-sm"
          @click="removeDateRange(index)"
        />
      </div>

      <Button 
        label="🗑️ Xóa tất cả" 
        class="p-button-danger p-button-outlined mt-3"
        @click="clearAll"
      />
    </div>

    <div v-else class="empty-state mt-4">
      <i class="pi pi-calendar-plus" style="font-size: 2rem; color: #999;"></i>
      <p class="text-muted">Chưa có cặp ngày nào. Thêm ngày check-in và check-out ở trên.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useToast } from 'primevue/usetoast'
import type { DateRange } from '@/types'

const emit = defineEmits<{
  (e: 'date-ranges-updated', ranges: DateRange[]): void
}>()

const toast = useToast()
const today = ref(new Date())
const newCheckin = ref<Date>(new Date())
const newCheckout = ref<Date>(getDefaultCheckout())
const dateRanges = ref<DateRange[]>([])

const minCheckout = computed(() => {
  if (!newCheckin.value) return today.value
  const min = new Date(newCheckin.value)
  min.setDate(min.getDate() + 1)
  return min
})

const canAdd = computed(() => {
  return newCheckin.value && newCheckout.value && newCheckout.value > newCheckin.value
})

watch(newCheckin, (val) => {
  if (val && newCheckout.value && newCheckout.value <= val) {
    const nextDay = new Date(val)
    nextDay.setDate(nextDay.getDate() + 1)
    newCheckout.value = nextDay
  }
})

watch(dateRanges, (val) => {
  emit('date-ranges-updated', val)
}, { deep: true })

function getDefaultCheckout(): Date {
  const tomorrow = new Date()
  tomorrow.setDate(tomorrow.getDate() + 1)
  return tomorrow
}

function addDateRange() {
  if (!canAdd.value) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Ngày check-out phải sau ngày check-in',
      life: 3000
    })
    return
  }

  const newRange: DateRange = {
    checkin: formatDateToString(newCheckin.value),
    checkout: formatDateToString(newCheckout.value)
  }

  // Check duplicate
  const isDuplicate = dateRanges.value.some(
    r => r.checkin === newRange.checkin && r.checkout === newRange.checkout
  )

  if (isDuplicate) {
    toast.add({
      severity: 'warn',
      summary: 'Cảnh báo',
      detail: 'Cặp ngày này đã tồn tại',
      life: 3000
    })
    return
  }

  dateRanges.value.push(newRange)
  
  toast.add({
    severity: 'success',
    summary: 'Thành công',
    detail: 'Đã thêm cặp ngày',
    life: 2000
  })
}

function removeDateRange(index: number) {
  dateRanges.value.splice(index, 1)
  toast.add({
    severity: 'info',
    summary: 'Đã xóa',
    detail: 'Đã xóa cặp ngày',
    life: 2000
  })
}

function clearAll() {
  dateRanges.value = []
  toast.add({
    severity: 'info',
    summary: 'Đã xóa',
    detail: 'Đã xóa tất cả các cặp ngày',
    life: 2000
  })
}

function formatDateToString(date: Date): string {
  if (!date) return ''
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const [year, month, day] = dateStr.split('-')
  return `${day}/${month}/${year}`
}

function calculateNights(range: DateRange): number {
  const checkin = new Date(range.checkin)
  const checkout = new Date(range.checkout)
  const diff = checkout.getTime() - checkin.getTime()
  return Math.ceil(diff / (1000 * 60 * 60 * 24))
}
</script>

<style scoped>
.date-range-picker {
  padding: 1rem;
}

.grid {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 1rem;
  align-items: end;
}

.col {
  display: flex;
  flex-direction: column;
}

.col label {
  margin-bottom: 0.5rem;
  font-weight: 500;
}

.col-auto {
  display: flex;
}

.align-end {
  align-items: flex-end;
}

.w-full {
  width: 100%;
}

.ranges-list {
  border-top: 1px solid #dee2e6;
  padding-top: 1rem;
}

.ranges-list h4 {
  margin-bottom: 1rem;
  color: #495057;
}

.range-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: #f8f9fa;
  border-radius: 4px;
  margin-bottom: 0.5rem;
}

.range-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.range-info i {
  color: #667eea;
}

.range-text {
  font-weight: 500;
}

.nights {
  color: #6c757d;
  margin-left: 0.5rem;
}

.empty-state {
  text-align: center;
  padding: 2rem;
  background: #f8f9fa;
  border-radius: 4px;
}

.text-muted {
  color: #6c757d;
  margin-top: 0.5rem;
}

.mt-3 {
  margin-top: 1rem;
}

.mt-4 {
  margin-top: 1.5rem;
}

@media (max-width: 768px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
