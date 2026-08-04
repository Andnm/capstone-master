import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'scraper',
      component: HomeView
    },
    {
      path: '/competitor-list',
      name: 'competitor-list',
      component: () => import('../views/CompetitorListView.vue')
    },
    {
      path: '/competitor/:id/view',
      name: 'competitor-view',
      component: () => import('../views/CompetitorDetailView.vue')
    },
    {
      path: '/competitor/:id/edit',
      name: 'competitor-edit',
      component: () => import('../views/CompetitorEditView.vue')
    },
    {
      path: '/config',
      name: 'config',
      component: () => import('../views/ConfigView.vue')
    },
    {
      path: '/history',
      name: 'history',
      component: () => import('../views/HistoryView.vue')
    },
    {
      path: '/history/:id',
      name: 'history-detail',
      component: () => import('../views/HistoryDetailView.vue')
    },
    {
      path: '/tracking',
      name: 'tracking',
      component: () => import('../views/TrackingView.vue')
    }
  ]
})

export default router