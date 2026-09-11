import { Channel, invoke } from "@tauri-apps/api/core"
import { open } from "@tauri-apps/plugin-dialog"
import { runMockBridge } from "./mock"
import type { BridgeMessage, BridgeResult } from "../types"

export const isDesktopRuntime = Boolean(window.__TAURI_INTERNALS__)

export async function runBridge(
  command: string,
  args: string[] = [],
  payload?: Record<string, unknown>,
  onMessage?: (message: BridgeMessage) => void,
): Promise<BridgeResult> {
  if (!isDesktopRuntime) {
    return runMockBridge(command, args, payload, onMessage)
  }

  const channel = new Channel<BridgeMessage>()
  channel.onmessage = (message) => onMessage?.(message)
  return invoke<BridgeResult>("run_bridge", {
    command,
    args,
    payload: payload ?? null,
    onMessage: channel,
  })
}

export function finalMessage(result: BridgeResult): BridgeMessage {
  const error = [...result.messages].reverse().find((message) => message.event === "error")
  const done = [...result.messages].reverse().find((message) => message.event === "done" || message.ok)
  if (result.code !== 0 || error) {
    throw new Error(String(error?.message ?? result.stderr.trim() ?? `Command exited with code ${result.code}`))
  }
  if (!done) {
    throw new Error("The engine returned no result.")
  }
  return done
}

export interface AudioAsset {
  token: string
  url: string
}

export async function registerAudio(path: string): Promise<AudioAsset> {
  if (!isDesktopRuntime) {
    return { token: "preview", url: path.startsWith("data:audio/") ? path : "" }
  }
  return invoke<AudioAsset>("register_audio", { path })
}

export async function releaseAudio(token: string): Promise<void> {
  if (!isDesktopRuntime || token === "preview") return
  await invoke("release_audio", { token })
}

export async function selectReplacementAudio(): Promise<string | null> {
  if (!isDesktopRuntime) return null
  const selected = await open({
    multiple: false,
    directory: false,
    title: "Choose a licensed audio file",
    filters: [{
      name: "Audio",
      extensions: ["mp3", "wav", "aiff", "aif", "flac", "m4a", "aac", "ogg", "opus"],
    }],
  })
  return typeof selected === "string" ? selected : null
}
