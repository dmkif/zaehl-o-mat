import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import VueApexCharts from 'vue3-apexcharts'

import App from './App.vue'
import router from './router'
import de from './locales/de'
import en from './locales/en'
import './style.css'

const i18n = createI18n({
  legacy: false,
  locale: 'de',
  fallbackLocale: 'en',
  messages: { de, en },
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
app.use(VueApexCharts)
app.mount('#app')
