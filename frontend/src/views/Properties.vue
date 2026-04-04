<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold">{{ $t('properties.title') }}</h1>
      <button
        v-if="authStore.isAdmin"
        @click="showCreate = true"
        class="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-sm font-medium transition"
      >
        + {{ $t('properties.add') }}
      </button>
    </div>

    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="p in properties"
        :key="p.id"
        class="relative block p-4 rounded-xl border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm hover:shadow transition"
      >
        <RouterLink :to="`/properties/${p.id}/meters`" class="block">
          <div class="font-semibold text-lg">{{ p.name }}</div>
          <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ p.address }}</div>
          <div class="mt-2 inline-block text-xs px-2 py-0.5 rounded-full bg-brand-100 dark:bg-brand-700 text-brand-700 dark:text-brand-100">
            {{ $t(`properties.type_${p.property_type}`) || p.property_type }}
          </div>
        </RouterLink>
        <div v-if="authStore.isAdmin" class="absolute top-3 right-3 flex gap-1">
          <button
            class="text-gray-400 hover:text-yellow-500 transition-colors p-1"
            :title="$t('properties.edit')"
            @click.prevent="startEditProperty(p)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </button>
          <button
            class="text-gray-400 hover:text-red-500 transition-colors p-1"
            :title="$t('properties.delete')"
            @click.prevent="confirmDeleteProperty(p)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Create modal (minimal) -->
    <Teleport to="body">
      <div v-if="showCreate" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-4">{{ $t('properties.add') }}</h2>
          <form @submit.prevent="createProperty" class="flex flex-col gap-3">
            <input v-model="newProp.name" :placeholder="$t('properties.name')" required
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="newProp.address" :placeholder="$t('properties.address')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <select v-model="newProp.property_type"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="residential">{{ $t('properties.type_residential') }}</option>
              <option value="commercial">{{ $t('properties.type_commercial') }}</option>
              <option value="mixed">{{ $t('properties.type_mixed') }}</option>
            </select>
            <div class="flex gap-2 justify-end mt-2">
              <button type="button" @click="showCreate = false" class="px-4 py-2 rounded-lg border dark:border-gray-600">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" class="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium">
                {{ $t('common.save') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- Edit property modal -->
    <Teleport to="body">
      <div v-if="editTarget" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="editTarget = null">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-4">{{ $t('properties.edit_title') }}</h2>
          <form @submit.prevent="saveEditProperty" class="flex flex-col gap-3">
            <input v-model="editForm.name" :placeholder="$t('properties.name')" required
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="editForm.address" :placeholder="$t('properties.address')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <select v-model="editForm.property_type"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="residential">{{ $t('properties.type_residential') }}</option>
              <option value="commercial">{{ $t('properties.type_commercial') }}</option>
              <option value="mixed">{{ $t('properties.type_mixed') }}</option>
            </select>
            <div class="flex gap-2 justify-end mt-2">
              <button type="button" @click="editTarget = null" class="px-4 py-2 rounded-lg border dark:border-gray-600">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" :disabled="editSaving" class="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium">
                {{ editSaving ? $t('common.saving') : $t('common.save') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- Delete confirmation modal -->
    <Teleport to="body">
      <div v-if="deleteTarget" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="deleteTarget = null">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-2">{{ $t('properties.delete_confirm_title') }}</h2>
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">{{ $t('properties.delete_confirm_msg') }}</p>
          <div class="flex gap-2 justify-end">
            <button @click="deleteTarget = null" class="px-4 py-2 rounded-lg border dark:border-gray-600">
              {{ $t('common.cancel') }}
            </button>
            <button :disabled="deleting" @click="doDeleteProperty" class="px-4 py-2 bg-red-600 text-white rounded-lg font-medium">
              {{ deleting ? '…' : $t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'

const authStore = useAuthStore()
const { t: _t } = useI18n()
const loading = ref(true)
const properties = ref<any[]>([])
const showCreate = ref(false)
const newProp = ref({ name: '', address: '', property_type: 'residential' })

const editTarget = ref<any>(null)
const editForm = ref({ name: '', address: '', property_type: 'residential' })
const editSaving = ref(false)
const deleteTarget = ref<any>(null)
const deleting = ref(false)

onMounted(fetchProperties)

async function fetchProperties() {
  loading.value = true
  const res = await apiFetch('/api/properties', {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) properties.value = await res.json()
  loading.value = false
}

async function createProperty() {
  const res = await apiFetch('/api/properties', {
    method: 'POST',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(newProp.value),
  })
  if (res.ok) {
    showCreate.value = false
    newProp.value = { name: '', address: '', property_type: 'residential' }
    fetchProperties()
  }
}

function startEditProperty(p: any) {
  editTarget.value = p
  editForm.value = { name: p.name, address: p.address ?? '', property_type: p.property_type ?? 'residential' }
}

async function saveEditProperty() {
  if (!editTarget.value) return
  editSaving.value = true
  const res = await apiFetch(`/api/properties/${editTarget.value.id}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(editForm.value),
  })
  editSaving.value = false
  if (res.ok) { editTarget.value = null; await fetchProperties() }
}

function confirmDeleteProperty(p: any) {
  deleteTarget.value = p
}

async function doDeleteProperty() {
  if (!deleteTarget.value) return
  deleting.value = true
  const res = await apiFetch(`/api/properties/${deleteTarget.value.id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  deleting.value = false
  if (res.ok) { deleteTarget.value = null; await fetchProperties() }
}
</script>
