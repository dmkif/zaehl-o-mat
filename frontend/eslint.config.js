// Security-only lint pass (Constitution Principle X — Verifiable Security).
// Deliberately scoped to eslint-plugin-security's rules alone — general
// style/type-strictness linting is a separate, unrelated concern (and would
// surface hundreds of pre-existing, non-security findings if enabled here).
// Type-checking already runs separately via `vue-tsc` in CI.
import pluginSecurity from 'eslint-plugin-security'
import tsParser from '@typescript-eslint/parser'
import vueParser from 'vue-eslint-parser'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'public/**'],
  },
  {
    files: ['**/*.{ts,vue}'],
    plugins: {
      security: pluginSecurity,
    },
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tsParser,
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      ...pluginSecurity.configs.recommended.rules,
    },
  },
]
