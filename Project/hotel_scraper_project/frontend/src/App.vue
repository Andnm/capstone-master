<template>
  <div id="app">
    <header class="app-header">
      <div class="container">
        <nav class="nav-left">
          <router-link to="/" class="nav-link">
            <i class="pi pi-home"></i> Scraper
          </router-link>
          <router-link to="/competitor-list" class="nav-link">
            <i class="pi pi-users"></i> Competitor List
          </router-link>
          <router-link to="/config" class="nav-link">
            <i class="pi pi-cog"></i> Config
          </router-link>
          <router-link to="/history" class="nav-link">
            <i class="pi pi-history"></i> Lịch sử
          </router-link>
          <router-link to="/tracking" class="nav-link">
            <i class="pi pi-chart-line"></i> Tracking
          </router-link>
        </nav>
        <div class="logo-right">
          <img src="https://id.ezcloud.vn/images/ezCloud3.svg" alt="ezCloud Logo" class="logo-img" />
        </div>
      </div>
    </header>

    <main class="app-main">
      <div class="container">
        <router-view />
      </div>
    </main>

    <Toast />
    <ConfirmDialog />
  </div>
</template>

<script setup lang="ts">
// App entry point
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
  background-color: #f5f5f5;
}

#app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  background: linear-gradient(135deg, #1e88e5 0%, #1565c0 100%);
  color: white;
  padding: 1.5rem 0;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.app-header .container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.logo-right {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: white;
  padding: 0.5rem 1rem;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.logo-img {
  height: 40px;
  width: auto;
}

.logo-right h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0;
}

.nav-left {
  display: flex;
  gap: 1rem;
}

.nav-link {
  color: white;
  text-decoration: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  transition: background-color 0.3s;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.nav-link:hover {
  background-color: rgba(255, 255, 255, 0.2);
}

.nav-link.router-link-active {
  background-color: rgba(255, 255, 255, 0.3);
  font-weight: 600;
}

.app-main {
  flex: 1;
  padding: 2rem 0;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 1rem;
}

.p-card {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  margin-bottom: 1.5rem;
}

.p-button {
  font-weight: 500;
}

/* Override PrimeVue primary color to blue */
:root {
  --p-primary-50: #e3f2fd;
  --p-primary-100: #bbdefb;
  --p-primary-200: #90caf9;
  --p-primary-300: #64b5f6;
  --p-primary-400: #42a5f5;
  --p-primary-500: #1e88e5;
  --p-primary-600: #1976d2;
  --p-primary-700: #1565c0;
  --p-primary-800: #0d47a1;
  --p-primary-900: #0a3a82;
  --p-primary-950: #052a5f;
}

/* Force blue color for all primary buttons */
.p-button.p-button-primary,
.p-button:not(.p-button-secondary):not(.p-button-text):not(.p-button-outlined):not(.p-button-danger):not(.p-button-warning):not(.p-button-info):not(.p-button-success):not(.p-button-help):not(.p-button-contrast) {
  background: linear-gradient(135deg, #1e88e5 0%, #1565c0 100%) !important;
  border-color: #1e88e5 !important;
  color: white !important;
}

.p-button.p-button-primary:hover,
.p-button:not(.p-button-secondary):not(.p-button-text):not(.p-button-outlined):not(.p-button-danger):not(.p-button-warning):not(.p-button-info):not(.p-button-success):not(.p-button-help):not(.p-button-contrast):hover:not(:disabled) {
  background: linear-gradient(135deg, #1565c0 0%, #0d47a1 100%) !important;
  border-color: #1565c0 !important;
}

.p-button.p-button-primary:active,
.p-button:not(.p-button-secondary):not(.p-button-text):not(.p-button-outlined):not(.p-button-danger):not(.p-button-warning):not(.p-button-info):not(.p-button-success):not(.p-button-help):not(.p-button-contrast):active {
  background: linear-gradient(135deg, #0d47a1 0%, #0a3a82 100%) !important;
  border-color: #0d47a1 !important;
}

/* Outlined primary buttons */
.p-button.p-button-outlined.p-button-primary,
.p-button.p-button-outlined:not(.p-button-secondary):not(.p-button-danger) {
  border-color: #1e88e5 !important;
  color: #1e88e5 !important;
  background: transparent !important;
}

.p-button.p-button-outlined.p-button-primary:hover,
.p-button.p-button-outlined:not(.p-button-secondary):not(.p-button-danger):hover:not(:disabled) {
  background: rgba(30, 136, 229, 0.1) !important;
  border-color: #1565c0 !important;
  color: #1565c0 !important;
}
</style>
