// Recover from a stale cached build after a new deploy.
//
// Vite dispatches `vite:preloadError` on window when a lazy chunk (or its CSS)
// fails to load. In production the usual cause is a browser still executing a
// cached entry bundle whose hashed chunk files were replaced by a newer deploy;
// before this guard, that left the console stuck on a full-screen loading
// overlay with no way out except a manual hard refresh.
//
// Reloading once picks up the fresh index.html. The reload is one-shot per
// entry bundle: if the very same (still stale) bundle comes back, we stop, so a
// permanently dead chunk cannot put the tab into a reload loop.
const RECOVERED_ENTRY_KEY = 'pf:chunk-recovery-entry'

export function installChunkRecovery(entryUrl = import.meta.url) {
  if (typeof window === 'undefined') return

  window.addEventListener('vite:preloadError', () => {
    let recovered = null
    try {
      recovered = window.sessionStorage.getItem(RECOVERED_ENTRY_KEY)
    } catch (error) {
      // Storage can be unavailable in hardened/private contexts. Recovery is
      // best effort and must never throw from inside an error path.
    }
    if (recovered === entryUrl) return
    try {
      window.sessionStorage.setItem(RECOVERED_ENTRY_KEY, entryUrl)
    } catch (error) {
      // ignore: reloading is still better than staying broken
    }
    window.location.reload()
  })
}
