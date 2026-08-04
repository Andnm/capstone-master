<template>
  <div class="home-view">
    <Card>
      <template #title>
        <i class="pi pi-download"></i> Cào dữ liệu khách sạn
      </template>
      <template #content>
        <BookingTab />
      </template>
    </Card>
  </div>
</template>

<script setup lang="ts">
import BookingTab from '@/components/BookingTab.vue'
</script>