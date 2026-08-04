<template>
  <div class="history-view">
    <Card>
      <template #title>
        <i class="pi pi-history"></i> Lịch sử cào dữ liệu
      </template>
      <template #content>
        <HistoryList />
      </template>
    </Card>
  </div>
</template>

<script setup lang="ts">
import HistoryList from '@/components/HistoryList.vue'
</script>