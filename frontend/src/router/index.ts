import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/dashboard',
    },
    {
      path: '/auth/token',
      component: () => import('@/views/AuthCallback.vue'),
    },
    {
      path: '/login',
      component: () => import('@/views/Login.vue'),
    },
    {
      path: '/dashboard',
      component: () => import('@/views/Dashboard.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/properties',
      component: () => import('@/views/Properties.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/properties/:propertyId/meters',
      component: () => import('@/views/Meters.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/properties/:propertyId/meters/:meterId',
      component: () => import('@/views/MeterDetail.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/properties/:propertyId/oil-deliveries',
      component: () => import('@/views/OilDeliveries.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/admin',
      component: () => import('@/views/Admin.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/dashboard',
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // On a page load the stored session (token + role from /auth/me) must be known before guarding.
  await auth.tryRestoreSession()
  if (to.meta.requiresAuth && !auth.isLoggedIn) {
    return '/login'
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return '/dashboard'
  }
})

export default router
