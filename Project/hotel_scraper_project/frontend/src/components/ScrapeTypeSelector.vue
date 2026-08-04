<template>
  <div class="scrape-type-selector mt-4">
    <label class="selector-label">Chọn chế độ cào dữ liệu</label>
    
    <div style="display: flex; gap: 0.75rem; margin-top: 0.75rem">
      <div 
        class="mode-card"
        :class="{ 'mode-selected': modelValue === 'info' }"
        @click="selectMode('info')"
      >
        <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem">
          <i class="pi pi-info-circle" style="font-size: 1.25rem; color: #3b82f6"></i>
          <strong style="flex: 1">Cào thông tin</strong>
          <i v-if="modelValue === 'info'" class="pi pi-check-circle" style="color: #16a34a; font-size: 1.25rem"></i>
        </div>
        <p class="mode-description">
          Lấy đầy đủ thông tin chi tiết về khách sạn và phòng
        </p>
       
      </div>

      <div 
        class="mode-card"
        :class="{ 'mode-selected': modelValue === 'price' }"
        @click="selectMode('price')"
      >
        <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem">
          <i class="pi pi-dollar" style="font-size: 1.25rem; color: #16a34a"></i>
          <strong style="flex: 1">Cào giá</strong>
          <i v-if="modelValue === 'price'" class="pi pi-check-circle" style="color: #16a34a; font-size: 1.25rem"></i>
        </div>
        <p class="mode-description">
          Chỉ lấy thông tin về giá và khuyến mãi
        </p>
        
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  modelValue: 'info' | 'price' | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: 'info' | 'price'): void
}>()

function selectMode(mode: 'info' | 'price') {
  emit('update:modelValue', mode)
}
</script>

<style scoped>
.selector-label {
  font-weight: 600;
  color: var(--text-color);
  display: block;
}

.mode-card {
  flex: 1;
  padding: 1rem;
  border: 2px solid #e2e8f0;
  background: #ffffff;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.25s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.mode-card:hover {
  border-color: #94a3b8;
  background: #f8fafc;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.mode-selected {
  border-color: #3b82f6;
  border-width: 3px;
  background: linear-gradient(135deg, #dbeafe 0%, #e0f2fe 100%);
  box-shadow: 0 4px 16px rgba(59, 130, 246, 0.3), 0 0 0 4px rgba(59, 130, 246, 0.1);
  transform: translateY(-1px);
}

.mode-selected strong {
  color: #1e40af;
  font-weight: 700;
}

.mode-selected .mode-description {
  color: #475569;
  font-weight: 500;
}

.mode-card:not(.mode-selected) strong {
  color: #64748b;
  font-weight: 600;
}

.mode-description {
  font-size: 0.875rem;
  color: #94a3b8;
  margin: 0 0 0.5rem 0;
  line-height: 1.4;
}

.mode-includes {
  font-size: 0.75rem;
  color: var(--text-color-secondary);
  line-height: 1.4;
}
</style>
