<template>
  <Card v-if="scraperStore.isScraing" class="progress-card">
    <template #title>
      <div class="progress-header">
        <i class="pi pi-spin pi-spinner"></i>
        <span>Đang cào dữ liệu...</span>
      </div>
    </template>
    <template #content>
      <!-- Overall Progress -->
      <div class="progress-section">
        <div class="progress-label">
          <span>Tiến độ tổng</span>
          <span class="progress-percentage">{{ scraperStore.progressPercentage }}%</span>
        </div>
        <ProgressBar :value="scraperStore.progressPercentage" />
        <div class="progress-stats">
          <span>{{ scraperStore.currentProgress }}/{{ scraperStore.totalProgress }} links</span>
        </div>
      </div>

      <!-- Current Status -->
      <div class="status-section mt-4">
        <div class="status-item">
          <div class="status-label">
            <i class="pi pi-building"></i>
            <span>Đang cào</span>
          </div>
          <div class="status-value" :title="scraperStore.currentHotelName">{{ truncateText(scraperStore.currentHotelName || 'Đang khởi động...', 50) }}</div>
        </div>

        <div class="status-item">
          <div class="status-label">
            <i class="pi pi-info-circle"></i>
            <span>Trạng thái</span>
          </div>
          <div class="status-value">{{ scraperStore.currentMessage }}</div>
        </div>

        <div class="status-item" v-if="scraperStore.currentRow > 0">
          <div class="status-label">
            <i class="pi pi-list"></i>
            <span>Hàng hiện tại</span>
          </div>
          <div class="status-value">Hàng #{{ scraperStore.currentRow }}</div>
        </div>
      </div>

      <!-- Stats -->
      <div class="stats-section mt-4">
        <div class="stat-card success">
          <div class="stat-icon">
            <i class="pi pi-check-circle"></i>
          </div>
          <div class="stat-info">
            <div class="stat-label">Thành công</div>
            <div class="stat-value">{{ scraperStore.successCount }}</div>
          </div>
        </div>

        <div class="stat-card error">
          <div class="stat-icon">
            <i class="pi pi-times-circle"></i>
          </div>
          <div class="stat-info">
            <div class="stat-label">Lỗi</div>
            <div class="stat-value">{{ scraperStore.errorCount }}</div>
          </div>
        </div>
      </div>
    </template>
  </Card>

  <!-- Completion Message -->
  <Card v-if="!scraperStore.isScraing && scraperStore.currentProgress > 0" class="completion-card">
    <template #content>
      <div class="completion-message">
        <i class="pi pi-check-circle success-icon"></i>
        <h3>Hoàn thành cào dữ liệu!</h3>
        <p>
          Đã cào xong {{ scraperStore.successCount }} khách sạn
          <span v-if="scraperStore.errorCount > 0"> ({{ scraperStore.errorCount }} lỗi)</span>
        </p>
      </div>
    </template>
  </Card>
</template>

<script setup lang="ts">
import { useScraperStore } from '@/stores/scraper'

const scraperStore = useScraperStore()

const truncateText = (text: string, maxLength: number) => {
  if (!text) return text
  return text.length > maxLength ? text.substring(0, maxLength) + '...' : text
}
</script>

<style scoped>
.progress-card {
  border-left: 4px solid #667eea;
}

.progress-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: #667eea;
}

.progress-section {
  margin-bottom: 1.5rem;
}

.progress-label {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
  font-weight: 500;
}

.progress-percentage {
  color: #667eea;
  font-size: 1.25rem;
  font-weight: 600;
}

.progress-stats {
  margin-top: 0.5rem;
  color: #6c757d;
  font-size: 0.875rem;
  text-align: right;
}

.status-section {
  background: #f8f9fa;
  padding: 1rem;
  border-radius: 4px;
}

.status-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.5rem 0;
  border-bottom: 1px solid #dee2e6;
}

.status-item:last-child {
  border-bottom: none;
}

.status-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #6c757d;
  font-size: 0.875rem;
}

.status-value {
  font-weight: 500;
  text-align: right;
  max-width: 60%;
}

.stats-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  border-radius: 4px;
  background: #f8f9fa;
}

.stat-card.success {
  border-left: 4px solid #10b981;
}

.stat-card.error {
  border-left: 4px solid #ef4444;
}

.stat-icon {
  font-size: 2rem;
}

.stat-card.success .stat-icon {
  color: #10b981;
}

.stat-card.error .stat-icon {
  color: #ef4444;
}

.stat-info {
  flex: 1;
}

.stat-label {
  font-size: 0.875rem;
  color: #6c757d;
  margin-bottom: 0.25rem;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 600;
}

.completion-card {
  border: 2px solid #10b981;
}

.completion-message {
  text-align: center;
  padding: 2rem;
}

.success-icon {
  font-size: 4rem;
  color: #10b981;
  margin-bottom: 1rem;
}

.completion-message h3 {
  color: #10b981;
  margin-bottom: 0.5rem;
}

.completion-message p {
  color: #6c757d;
}

.mt-4 {
  margin-top: 1.5rem;
}
</style>
