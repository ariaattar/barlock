import { Activity, Command, Disc3, Download, Heart, PanelRightClose, PanelRightOpen, Plus, Settings, ShieldCheck, type LucideIcon } from "lucide-react"
import { isDesktopRuntime } from "../lib/bridge"
import type { ActivityJob, AppConfig, ViewId } from "../types"
interface NavItem { id: ViewId; label: string; icon: LucideIcon; badge?: number }

function AppLogo() {
  return (
    <div className="brand-lockup">
      <div className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M7 10 16 5l9 5v13l-9 5-9-5V10Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="m7 10 9 5 9-5M16 15v13M11.5 7.5l9 5v6" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/></svg></div>
      <div>
        <strong>Crate<span className="brand-period">.</span></strong>
        <span>Your next set starts here</span>
      </div>
    </div>
  )
}

export function Sidebar({ active, onChange, onLikes, jobs, config }: { active: ViewId; onChange: (id: ViewId) => void; onLikes: () => void; jobs: ActivityJob[]; config: AppConfig | null }) {
  const activeJobs = jobs.filter((job) => job.state === "running" || job.state === "blocked").length
  const nav: NavItem[] = [
    { id: "import", label: "New import", icon: Plus },
    { id: "activity", label: "Activity", icon: Activity, badge: activeJobs || undefined },
    { id: "downloads", label: "Downloads", icon: Download },
    { id: "rekordbox", label: "Rekordbox", icon: Disc3 },
    { id: "doctor", label: "Doctor", icon: ShieldCheck },
  ]
  return (
    <aside className="sidebar">
      <div className="sidebar-drag" data-tauri-drag-region />
      <AppLogo />
      <nav className="sidebar-nav" aria-label="Primary">
        <div className="nav-section-label">Workspace</div>
        {nav.map((item) => {
          const Icon = item.icon
          return (
            <button key={item.id} aria-current={active === item.id ? "page" : undefined} className={`nav-item ${active === item.id ? "active" : ""}`} onClick={() => onChange(item.id)}>
              <Icon size={16} aria-hidden="true" />
              <span>{item.label}</span>
              {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
            </button>
          )
        })}
      </nav>
      <div className="sidebar-spacer" />
      {config?.likes_url ? (
        <button className="saved-source" onClick={onLikes}>
          <span className="saved-source-icon"><Heart size={14} /></span>
          <span><strong>@{config.soundcloud_username}</strong><small>Likes source</small></span>
        </button>
      ) : null}
      <button className={`nav-item settings-link ${active === "settings" ? "active" : ""}`} onClick={() => onChange("settings")}>
        <Settings size={16} aria-hidden="true" />
        <span>Settings</span>
      </button>
      <div className="user-block">
        <div className="user-avatar"><Disc3 size={17} /></div>
        <div><strong>Local library</strong><span>{isDesktopRuntime ? "On this Mac" : "Preview mode"}</span></div>
      </div>
    </aside>
  )
}

export function Topbar({
  view,
  inspectorOpen,
  onToggleInspector,
  onOpenPalette,
  hasSelection,
}: {
  hasSelection: boolean
  view: ViewId
  inspectorOpen: boolean
  onToggleInspector: () => void
  onOpenPalette: () => void
}) {
  const titles: Record<ViewId, string> = {
    import: "New import",
    activity: "Activity",
    downloads: "Downloads",
    rekordbox: "Rekordbox",
    doctor: "Doctor",
    settings: "Settings",
  }
  const current = titles[view]
  return (
    <header className="topbar" data-tauri-drag-region>
      <div className="window-controls-space" data-tauri-drag-region />
      <div className="view-heading" data-tauri-drag-region>
        <strong>{current}</strong>
      </div>
      <div className="topbar-spacer" data-tauri-drag-region />
      <button className="command-trigger" onClick={onOpenPalette}>
        <Command size={14} />
        <span>Quick navigation</span>
        <kbd><Command size={11} />K</kbd>
      </button>
      {hasSelection ? <button className="icon-button" onClick={onToggleInspector} aria-label={inspectorOpen ? "Close inspector" : "Open inspector"} title={inspectorOpen ? "Close inspector" : "Open inspector"}>
        {inspectorOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
      </button> : null}
    </header>
  )
}
