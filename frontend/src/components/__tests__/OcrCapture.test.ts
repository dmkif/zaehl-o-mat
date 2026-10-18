import { describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de'

vi.mock('@/utils/api', () => ({ apiFetch: vi.fn() }))
vi.mock('vue-advanced-cropper/dist/style.css', () => ({}))
// A cropper that stays "loading" until the test says ready (the real one fires `ready` after decoding the image).
const instances: Array<{ emit: (e: string) => void }> = []
vi.mock('vue-advanced-cropper', () => ({
  Cropper: defineComponent({
    props: ['src'],
    emits: ['ready', 'error'],
    setup(_p, { emit, expose }) {
      instances.push({ emit: (e: string) => emit(e as 'ready') })
      expose({ reset() {}, getResult: () => ({ canvas: null }) })
      return () => h('div', { class: 'fake-cropper' })
    },
  }),
}))

import OcrCapture from '../OcrCapture.vue'

async function mountToCrop() {
  instances.length = 0
  URL.createObjectURL = () => 'blob:fake'
  URL.revokeObjectURL = () => {}
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(OcrCapture, { meterId: 'm', propertyId: 'p' })
  app.use(createPinia())
  app.use(createI18n({ legacy: false, locale: 'de', messages: { de } }))
  app.mount(el)
  const input = el.querySelector('input[type=file]') as HTMLInputElement
  Object.defineProperty(input, 'files', { value: [new File(['x'], 'a.jpg', { type: 'image/jpeg' })] })
  input.dispatchEvent(new Event('change'))
  await nextTick()
  return el
}

const weiter = (el: HTMLElement) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === de.ocr.scan) as HTMLButtonElement

describe('OcrCapture crop step guard (spec 014)', () => {
  it('"Weiter" is disabled with a loading hint until the cropper fired ready', async () => {
    const el = await mountToCrop()
    expect(weiter(el).disabled).toBe(true)
    expect(el.querySelector('[data-testid=crop-loading]')).not.toBeNull()
    instances[0].emit('ready')
    await nextTick()
    expect(weiter(el).disabled).toBe(false)
    expect(el.querySelector('[data-testid=crop-loading]')).toBeNull()
  })

  it('a failed image load keeps the button disabled and shows an error, not the spinner', async () => {
    const el = await mountToCrop()
    instances[0].emit('error')
    await nextTick()
    expect(weiter(el).disabled).toBe(true)
    expect(el.querySelector('[data-testid=crop-loading]')).toBeNull()
    expect(el.textContent).toContain(de.ocr.crop_error)
  })
})
