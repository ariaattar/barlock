import type { Analysis } from "./api"
import type { WebAudioPlayer } from "./player"

export type DeckState = {
  analysis: Analysis | null
  player: WebAudioPlayer | null
  /** Set by the Deck so the sidebar can request load-by-path. */
  loadPath?: (path: string) => Promise<void>
}

/**
 * Shared registry so Deck A and Deck B can read each other's player + analysis
 * for sync. Kept as a plain object that Decks mutate via register/unregister;
 * sync is a one-shot action, no need for fine-grained reactivity here.
 */
export const deckRegistry: Record<"A" | "B", DeckState> = {
  A: { analysis: null, player: null },
  B: { analysis: null, player: null },
}
