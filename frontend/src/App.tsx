import { useEffect, useState } from "react"
import { Download, Disc3 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Deck } from "@/components/Deck"
import { Sidebar } from "@/components/Sidebar"
import { exportRekordbox, listTracks, type TrackSummary } from "@/lib/api"

function App() {
  const [tracks, setTracks] = useState<TrackSummary[]>([])
  const [exporting, setExporting] = useState(false)

  const refresh = async () => {
    try {
      setTracks(await listTracks())
    } catch {
      // ignore — backend may not be up yet
    }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 4000)
    return () => clearInterval(id)
  }, [])

  const onExport = async () => {
    if (!tracks.length) return
    setExporting(true)
    try {
      const blob = await exportRekordbox(tracks.map((t) => t.track_id))
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = "rekordbox.xml"
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex h-screen w-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-border/60 bg-background/80 px-6 py-3 backdrop-blur">
        <div className="flex items-center gap-2">
          <Disc3 className="size-5 text-primary" />
          <h1 className="text-lg font-semibold tracking-tight">Mixer</h1>
          <span className="text-xs text-muted-foreground">
            DJ analysis · bar-accurate loops · Rekordbox hot cues
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            {tracks.length} analyzed
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!tracks.length || exporting}
            onClick={onExport}
          >
            <Download className="size-4" />
            {exporting ? "Exporting…" : "Export Rekordbox XML"}
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
          <Deck label="A" />
          <Deck label="B" />
        </main>
      </div>
    </div>
  )
}

export default App
