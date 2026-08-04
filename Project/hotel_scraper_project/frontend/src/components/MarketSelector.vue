<template>
  <div class="market-selector">
    <label for="market" class="selector-label">Chọn Market</label>
    <Dropdown
      id="market"
      v-model="selectedMarket"
      :options="marketOptions"
      optionLabel="label"
      optionValue="value"
      placeholder="Chọn market"
      class="w-full"
      style="margin-top: 0.75rem"
      @change="handleChange"
    >
      <template #value="slotProps">
        <div v-if="slotProps.value" style="display: flex; align-items: center; gap: 0.5rem">
          <i class="pi pi-globe"></i>
          <strong>{{ getMarketLabel(slotProps.value) }}</strong>
        </div>
        <span v-else>{{ slotProps.placeholder }}</span>
      </template>
      <template #option="slotProps">
        <div style="display: flex; align-items: center; justify-content: space-between; width: 100%">
          <div style="display: flex; align-items: center; gap: 0.5rem">
            <i :class="slotProps.option.value === 'all' ? 'pi pi-list' : 'pi pi-tag'"></i>
            <span>{{ slotProps.option.label }}</span>
          </div>
          <Tag 
            v-if="slotProps.option.count" 
            :value="`${slotProps.option.count} links`" 
            severity="info"
            style="font-size: 0.75rem"
          />
        </div>
      </template>
    </Dropdown>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'

interface MarketOption {
  label: string
  value: string
  count?: number
}

const props = defineProps<{
  markets: string[]
  linksByMarket?: Record<string, number>
  modelValue: string | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
}>()

const selectedMarket = ref<string | null>(props.modelValue || 'all')

const marketOptions = computed<MarketOption[]>(() => {
  const options: MarketOption[] = [
    { 
      label: 'Tất cả markets', 
      value: 'all',
      count: Object.values(props.linksByMarket || {}).reduce((sum, count) => sum + count, 0)
    }
  ]
  
  props.markets.forEach(market => {
    options.push({
      label: market,
      value: market,
      count: props.linksByMarket?.[market]
    })
  })
  
  return options
})

function getMarketLabel(value: string): string {
  const option = marketOptions.value.find(opt => opt.value === value)
  return option?.label || value
}

function handleChange() {
  emit('update:modelValue', selectedMarket.value)
}

watch(() => props.modelValue, (newVal) => {
  selectedMarket.value = newVal || 'all'
}, { immediate: true })
</script>

<style scoped>
.selector-label {
  font-weight: 600;
  color: var(--text-color);
  display: block;
}

/* Enhanced styling for market selector */
:deep(.p-dropdown) {
  border-width: 2px;
}

:deep(.p-dropdown:not(.p-disabled):hover) {
  border-color: var(--primary-color);
}

:deep(.p-dropdown-panel) {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}

/* Style for selected option in dropdown list */
:deep(.p-dropdown-item.p-highlight) {
  background-color: var(--primary-color) !important;
  color: white !important;
  font-weight: 600;
}

:deep(.p-dropdown-item.p-highlight i) {
  color: white !important;
}

:deep(.p-dropdown-item.p-highlight .p-tag) {
  background-color: rgba(255, 255, 255, 0.25);
  color: white;
  font-weight: 600;
}

/* Hover effect for non-selected items */
:deep(.p-dropdown-item:not(.p-highlight):hover) {
  background-color: rgba(59, 130, 246, 0.1);
}

/* Style for the selected value display */
:deep(.p-dropdown-label) {
  font-weight: 500;
}
</style>

