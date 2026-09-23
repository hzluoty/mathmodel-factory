import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router.js'
import { installChunkRecovery } from './lib/chunkRecovery.js'

// Must be installed before the first lazy chunk is requested.
installChunkRecovery()

createApp(App).use(router).mount('#app')
