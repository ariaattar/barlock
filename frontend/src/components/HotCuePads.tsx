import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Cue } from "@/lib/api"

const PAD_COUNT = 8
const PAD_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"]

const DEFAULT_COLORS: Array<[number, number, number]> = [
  [40, 226, 20], [80, 180, 255], [255, 100, 0], [255, 64, 64],
  [180, 100, 255], [255, 230, 0], [255, 0, 200], [0, 230, 200],
]

type Props = {
  cues: Cue[]
  onSetCue: (slot: number) => void
  onJumpCue: (slot: number) => void
  onClearCue: (slot: number) => void
}

function rgbToCss([r, g, b]: [number, number, number], alpha = 1) {
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export function HotCuePads({ cues, onSetCue, onJumpCue, onClearCue }: Props) {
  return (
    <div className="grid grid-cols-4 gap-2">
      {Array.from({ length: PAD_COUNT }, (_, slot) => {
        const cue = cues.find((c) => c.slot === slot)
        const color = cue?.color ?? DEFAULT_COLORS[slot]
        const filled = !!cue
        return (
          <div key={slot} className="flex flex-col gap-1">
            <button
              onClick={() => (filled ? onJumpCue(slot) : onSetCue(slot))}
              onContextMenu={(e) => {
                e.preventDefault()
                if (filled) onClearCue(slot)
              }}
              className={cn(
                "h-14 w-full rounded-md border-2 text-sm font-bold transition-all",
                filled
                  ? "shadow-md hover:scale-[1.02]"
                  : "border-dashed border-border bg-card/30 text-muted-foreground hover:bg-card/60",
              )}
              style={
                filled
                  ? {
                      backgroundColor: rgbToCss(color, 0.7),
                      borderColor: rgbToCss(color, 1),
                      color: "#0a0a0a",
                    }
                  : undefined
              }
              title={
                filled
                  ? `${cue?.name || "Cue"} @ ${cue?.position_sec.toFixed(2)}s — right-click to clear`
                  : `Set hot cue ${PAD_LABELS[slot]}`
              }
            >
              <div className="text-lg leading-none">{PAD_LABELS[slot]}</div>
              {filled && (
                <div className="mt-0.5 truncate px-1 text-[10px] font-medium leading-none">
                  {cue?.position_sec.toFixed(2)}s
                </div>
              )}
            </button>
          </div>
        )
      })}
      <div className="col-span-4 flex justify-end">
        <Button
          size="sm"
          variant="ghost"
          className="text-xs text-muted-foreground"
        >
          Click pad: set / jump · Right-click: clear
        </Button>
      </div>
    </div>
  )
}
