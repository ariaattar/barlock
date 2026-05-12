import { useEffect, useMemo, useState } from "react"
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  Music2,
  Disc,
  ListMusic,
  Loader2,
  RefreshCw,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  libraryBrowse,
  libraryFolders,
  rbPlaylists,
  rbPlaylistSongs,
  type LibraryEntry,
  type LibraryRoot,
  type RbPlaylistNode,
  type RbSong,
} from "@/lib/api"
import { deckRegistry } from "@/lib/decks"
import { cn } from "@/lib/utils"

type LoadingState = { path: string; label: "A" | "B" } | null

export function Sidebar() {
  const [roots, setRoots] = useState<LibraryRoot[]>([])
  const [rbAvailable, setRbAvailable] = useState(false)
  const [playlists, setPlaylists] = useState<RbPlaylistNode[]>([])
  const [loading, setLoading] = useState<LoadingState>(null)
  const [refreshing, setRefreshing] = useState(false)

  const refresh = async () => {
    setRefreshing(true)
    try {
      const f = await libraryFolders()
      setRoots(f.roots)
      setRbAvailable(f.rekordbox_available)
      if (f.rekordbox_available) {
        const p = await rbPlaylists()
        setPlaylists(p.playlists)
      } else {
        setPlaylists([])
      }
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const loadOn = async (path: string, label: "A" | "B") => {
    setLoading({ path, label })
    try {
      await deckRegistry[label].loadPath?.(path)
    } finally {
      setLoading(null)
    }
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border/60 bg-card/30">
      <div className="flex items-center justify-between border-b border-border/60 p-2">
        <div className="flex items-center gap-2 px-1 text-sm font-semibold">
          <ListMusic className="size-4 text-primary" />
          Library
        </div>
        <Button
          size="icon"
          variant="ghost"
          className="size-7"
          onClick={refresh}
          title="Refresh"
        >
          {refreshing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-1 text-sm">
        {rbAvailable && (
          <Section icon={<Disc className="size-3.5" />} label="Rekordbox">
            {playlists.map((p) => (
              <PlaylistNode
                key={p.id}
                node={p}
                onLoad={loadOn}
                loading={loading}
              />
            ))}
          </Section>
        )}
        <Section icon={<Folder className="size-3.5" />} label="Folders">
          {roots.map((r) => (
            <FolderNode
              key={r.path}
              name={r.name}
              path={r.path}
              onLoad={loadOn}
              loading={loading}
            />
          ))}
        </Section>
      </div>
    </aside>
  )
}

function Section({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode
  label: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:bg-accent"
      >
        {open ? (
          <ChevronDown className="size-3" />
        ) : (
          <ChevronRight className="size-3" />
        )}
        {icon}
        {label}
      </button>
      {open && <div className="ml-1.5 mt-0.5">{children}</div>}
    </div>
  )
}

function FolderNode({
  name,
  path,
  depth = 0,
  onLoad,
  loading,
}: {
  name: string
  path: string
  depth?: number
  onLoad: (path: string, label: "A" | "B") => void
  loading: LoadingState
}) {
  const [open, setOpen] = useState(false)
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null)
  const [busy, setBusy] = useState(false)

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (next && !entries) {
      setBusy(true)
      try {
        const r = await libraryBrowse(path)
        setEntries(r.entries)
      } finally {
        setBusy(false)
      }
    }
  }

  return (
    <div>
      <Row depth={depth} onClick={toggle}>
        {open ? (
          <ChevronDown className="size-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3 text-muted-foreground" />
        )}
        {open ? (
          <FolderOpen className="size-3.5 text-amber-400" />
        ) : (
          <Folder className="size-3.5 text-amber-400/80" />
        )}
        <span className="truncate">{name}</span>
        {busy && (
          <Loader2 className="ml-auto size-3 animate-spin text-muted-foreground" />
        )}
      </Row>
      {open && entries && (
        <div>
          {entries.map((e) =>
            e.type === "dir" ? (
              <FolderNode
                key={e.path}
                name={e.name}
                path={e.path}
                depth={depth + 1}
                onLoad={onLoad}
                loading={loading}
              />
            ) : (
              <FileRow
                key={e.path}
                name={e.name}
                path={e.path}
                depth={depth + 1}
                onLoad={onLoad}
                loading={loading}
              />
            ),
          )}
          {entries.length === 0 && (
            <Row depth={depth + 1}>
              <span className="text-xs text-muted-foreground italic">
                empty
              </span>
            </Row>
          )}
        </div>
      )}
    </div>
  )
}

function FileRow({
  name,
  path,
  depth,
  onLoad,
  loading,
  rightLabel,
}: {
  name: string
  path: string
  depth: number
  onLoad: (path: string, label: "A" | "B") => void
  loading: LoadingState
  rightLabel?: string
}) {
  const isLoading = loading?.path === path
  return (
    <Row depth={depth}>
      <Music2 className="size-3.5 text-muted-foreground" />
      <span className="truncate" title={path}>
        {name}
      </span>
      {rightLabel && (
        <span className="ml-auto pl-2 text-[10px] text-muted-foreground tabular-nums">
          {rightLabel}
        </span>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-1">
        {isLoading ? (
          <Loader2 className="size-3 animate-spin text-muted-foreground" />
        ) : (
          <>
            <LoadBadge
              label="A"
              onClick={() => onLoad(path, "A")}
              disabled={!!loading}
            />
            <LoadBadge
              label="B"
              onClick={() => onLoad(path, "B")}
              disabled={!!loading}
            />
          </>
        )}
      </div>
    </Row>
  )
}

function LoadBadge({
  label,
  onClick,
  disabled,
}: {
  label: "A" | "B"
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      disabled={disabled}
      className={cn(
        "rounded border border-border/60 px-1.5 py-0.5 text-[10px] font-semibold leading-none transition-colors",
        disabled
          ? "opacity-40"
          : "hover:border-primary hover:text-primary",
      )}
      title={`Load to Deck ${label}`}
    >
      {label}
    </button>
  )
}

function PlaylistNode({
  node,
  depth = 0,
  onLoad,
  loading,
}: {
  node: RbPlaylistNode
  depth?: number
  onLoad: (path: string, label: "A" | "B") => void
  loading: LoadingState
}) {
  const [open, setOpen] = useState(false)
  const [songs, setSongs] = useState<RbSong[] | null>(null)
  const [busy, setBusy] = useState(false)

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (next && !songs && !node.is_folder) {
      setBusy(true)
      try {
        const r = await rbPlaylistSongs(node.id)
        setSongs(r.songs)
      } finally {
        setBusy(false)
      }
    }
  }

  return (
    <div>
      <Row depth={depth} onClick={toggle}>
        {open ? (
          <ChevronDown className="size-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3 text-muted-foreground" />
        )}
        {node.is_folder ? (
          <Folder className="size-3.5 text-blue-400/80" />
        ) : (
          <ListMusic className="size-3.5 text-blue-400/80" />
        )}
        <span className="truncate">{node.name}</span>
        {!node.is_folder && node.song_count != null && (
          <span className="ml-auto pl-2 text-[10px] text-muted-foreground tabular-nums">
            {node.song_count}
          </span>
        )}
        {busy && (
          <Loader2 className="ml-2 size-3 animate-spin text-muted-foreground" />
        )}
      </Row>
      {open && node.is_folder && node.children && (
        <div>
          {node.children.map((c) => (
            <PlaylistNode
              key={c.id}
              node={c}
              depth={depth + 1}
              onLoad={onLoad}
              loading={loading}
            />
          ))}
        </div>
      )}
      {open && !node.is_folder && songs && (
        <div>
          {songs.length === 0 && (
            <Row depth={depth + 1}>
              <span className="text-xs italic text-muted-foreground">
                empty
              </span>
            </Row>
          )}
          {songs.map((s) => (
            <FileRow
              key={s.rekordbox_id}
              name={`${s.title}${s.artist ? ` — ${s.artist}` : ""}`}
              path={s.path}
              depth={depth + 1}
              onLoad={onLoad}
              loading={loading}
              rightLabel={s.bpm ? `${s.bpm.toFixed(1)}` : undefined}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function Row({
  depth = 0,
  onClick,
  children,
}: {
  depth?: number
  onClick?: () => void
  children: React.ReactNode
}) {
  const padding = useMemo(() => 4 + depth * 12, [depth])
  return (
    <div
      onClick={onClick}
      className={cn(
        "group flex items-center gap-1.5 rounded py-0.5 pr-1 text-xs",
        onClick && "cursor-pointer hover:bg-accent",
      )}
      style={{ paddingLeft: padding }}
    >
      {children}
    </div>
  )
}
