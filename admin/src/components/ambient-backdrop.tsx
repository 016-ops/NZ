/** 共享背景景深：仅写transform变量，不逐帧触发React渲染；减少动画/失焦时完全停止。 */
import { useEffect } from 'react'

export function AmbientBackdrop() {
  useEffect(() => {
    const root = document.documentElement
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const fine = window.matchMedia('(pointer: fine)')
    let frame = 0
    let active = true
    function sync() {
      active = !media.matches && !document.hidden && document.hasFocus()
      root.dataset.motion = active ? 'active' : 'paused'
      if (!active) { cancelAnimationFrame(frame); frame = 0; root.style.setProperty('--scene-x', '0px'); root.style.setProperty('--scene-y', '0px') }
    }
    function move(event: PointerEvent) {
      if (!active || !fine.matches || frame) return
      const x = (event.clientX / window.innerWidth - .5) * 12
      const y = (event.clientY / window.innerHeight - .5) * 8
      frame = requestAnimationFrame(() => { root.style.setProperty('--scene-x', `${x}px`); root.style.setProperty('--scene-y', `${y}px`); frame = 0 })
    }
    sync(); media.addEventListener('change', sync); document.addEventListener('visibilitychange', sync)
    window.addEventListener('focus', sync); window.addEventListener('blur', sync); window.addEventListener('pointermove', move, { passive: true })
    return () => { cancelAnimationFrame(frame); media.removeEventListener('change', sync); document.removeEventListener('visibilitychange', sync); window.removeEventListener('focus', sync); window.removeEventListener('blur', sync); window.removeEventListener('pointermove', move); delete root.dataset.motion }
  }, [])
  return <div className="nz-ambient" aria-hidden="true"><i /><i /><i /><div className="nz-ambient-grain" /></div>
}
