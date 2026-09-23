<template>
  <div v-if="!dismissed" :class="inline ? 'tab-fallback' : 'overlay-loading panel'" @click.self="dismissed = true">
    <div class="ale">
      <Icon name="alert-triangle" :size="26" />
      <p class="ale-title">界面资源加载失败</p>
      <p class="ale-hint">页面缓存可能已过期，重新加载即可恢复。</p>
      <button class="btn btn-amber" @click="reload"><Icon name="refresh" :size="14" /> 重新加载</button>
      <p class="ale-dismiss">点击空白处可关闭</p>
    </div>
  </div>
</template>

<script>
import Icon from './Icon.vue'

// Shown by `defineAsyncComponent` when a lazy chunk fails to load. Without an
// error component Vue keeps rendering the loading fallback forever, which is
// what used to leave the console blocked on a full-screen spinner.
export default {
  name: 'AsyncLoadError',
  components: { Icon },
  props: {
    // Inline variant for lazy tabs inside ProjectWorkspace.
    inline: { type: Boolean, default: false },
  },
  data() { return { dismissed: false } },
  methods: {
    reload() { window.location.reload() },
  },
}
</script>

<style scoped>
.ale { display: flex; flex-direction: column; align-items: center; gap: 9px; text-align: center; padding: 26px 30px; max-width: 380px; color: var(--ink-2); }
.ale-title { font-size: 14px; font-weight: 600; color: var(--ink); }
.ale-hint { font-size: 12.5px; color: var(--ink-3); }
.ale-dismiss { font-size: 11px; color: var(--ink-3); opacity: 0.75; }
.ale .btn { margin-top: 5px; }
</style>
