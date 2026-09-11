import { useState } from "react"
import { Activity, ArrowRight, Disc3, Download, Plus, Search, Settings, ShieldCheck } from "lucide-react"
import type { ViewId } from "../types"
import { useDialogFocus } from "./useDialogFocus"

const actions = [
  { label: "Start a new import", detail: "Paste a SoundCloud URL", icon: Plus, view: "import" },
  { label: "Open downloads", detail: "Browse your prepared tracks", icon: Download, view: "downloads" },
  { label: "View activity", detail: "Follow imports and resume unfinished jobs", icon: Activity, view: "activity" },
  { label: "Open Rekordbox playlists", detail: "Explore playlists and edit tracks", icon: Disc3, view: "rekordbox" },
  { label: "Run Rekordbox Doctor", detail: "Diagnostics and repairs", icon: ShieldCheck, view: "doctor" },
  { label: "Open settings", detail: "Storage, defaults, and appearance", icon: Settings, view: "settings" },
] as const

export function CommandPalette({ onClose, onNavigate }: { onClose: () => void; onNavigate: (view: ViewId) => void }) {
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState(0)
  const ref = useDialogFocus(onClose)
  const visible = actions.filter((action) => `${action.label} ${action.detail}`.toLowerCase().includes(query.toLowerCase()))
  const navigate = (view: ViewId) => { onNavigate(view); onClose() }
  return <div className="palette-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <div className="palette" role="dialog" aria-modal="true" aria-label="Quick navigation" ref={ref}>
      <label><Search size={18} /><input aria-label="Search commands" role="combobox" aria-expanded="true" aria-controls="navigation-options" aria-activedescendant={visible[selected] ? `command-${selected}` : undefined} value={query} onChange={(event) => { setQuery(event.target.value); setSelected(0) }} placeholder="Where would you like to go?" onKeyDown={(event) => {
        if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setSelected((value) => visible.length ? (value + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length : 0) }
        if (event.key === "Enter" && visible[selected]) { event.preventDefault(); navigate(visible[selected].view) }
      }} /><kbd>ESC</kbd></label>
      <div className="palette-section" id="navigation-options" role="listbox" aria-label="Destinations"><span>Go to</span>{visible.map((action, index) => {
        const Icon = action.icon
        return <button id={`command-${index}`} role="option" aria-selected={selected === index} className={selected === index ? "selected" : ""} key={action.view} onMouseMove={() => setSelected(index)} onClick={() => navigate(action.view)}><span><Icon size={18} /></span><span><strong>{action.label}</strong><small>{action.detail}</small></span><ArrowRight size={15} className="row-arrow" /></button>
      })}{!visible.length ? <div className="palette-empty">No matching destinations. Try “downloads” or “settings”.</div> : null}</div>
      <div className="palette-hint"><span>↑ ↓ to navigate</span><span>↵ to open</span><span>esc to close</span></div>
    </div>
  </div>
}
