import { useEffect, useRef } from "react"

/** Keep keyboard focus inside a dialog and return it to its opener on dismissal. */
export function useDialogFocus(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const dialog = ref.current
    if (!dialog) return
    const focusable = () => [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, a[href], [tabindex="0"]')].filter((item) => item.getClientRects().length)
    focusable()[0]?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); return }
      if (event.key !== "Tab") return
      const nodes = focusable()
      const first = nodes[0], last = nodes[nodes.length - 1]
      if (!first) { event.preventDefault(); dialog.focus(); return }
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    dialog.addEventListener("keydown", handleKey)
    return () => { dialog.removeEventListener("keydown", handleKey); if (opener?.isConnected) opener.focus() }
  }, [])
  return ref
}
