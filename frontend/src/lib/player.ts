/**
 * WebAudioPlayer — single-deck playback engine for sample-accurate looping.
 *
 * Decodes the audio file once into an AudioBuffer, then plays it through a
 * single AudioBufferSourceNode per "play session". When a loop region is
 * active, we set source.loop = true with loopStart / loopEnd — the browser's
 * audio hardware handles the wraparound with zero gap, matching the
 * seamlessness of Rekordbox.
 *
 * Why a custom player instead of wavesurfer's playback: wavesurfer v7 plays
 * through an HTMLAudioElement whose `currentTime =` setter causes a brief
 * decode flush on every loop wrap (audible as a click). AudioBufferSourceNode
 * with native loop has no such flush.
 */
export class WebAudioPlayer {
  private ctx: AudioContext
  private gain: GainNode
  private buffer: AudioBuffer | null = null
  private source: AudioBufferSourceNode | null = null

  /** Absolute audioContext time at which the current `source` started. */
  private sourceStartedAt = 0
  /** Buffer offset (sec) at which the current `source` started playing. */
  private sourceOffset = 0
  private playing = false

  private loopStart: number | null = null
  private loopEnd: number | null = null

  private listeners = new Set<() => void>()
  private rate = 1

  constructor() {
    this.ctx = new AudioContext()
    this.gain = this.ctx.createGain()
    this.gain.connect(this.ctx.destination)
  }

  async load(url: string): Promise<void> {
    const res = await fetch(url)
    const ab = await res.arrayBuffer()
    this.buffer = await this.ctx.decodeAudioData(ab)
    this.sourceOffset = 0
    this.notify()
  }

  get duration(): number {
    return this.buffer?.duration ?? 0
  }

  get isPlaying(): boolean {
    return this.playing
  }

  get playbackRate(): number {
    return this.rate
  }

  setPlaybackRate(rate: number): void {
    this.rate = Math.max(0.25, Math.min(4, rate))
    if (this.source) {
      this.source.playbackRate.value = this.rate
    }
    // If we're playing, the source's playback rate already affects playback
    // from this moment on. Update our timing anchors so currentTime stays
    // continuous across the rate change.
    if (this.playing && this.buffer) {
      const t = this.currentTime
      this.sourceStartedAt = this.ctx.currentTime
      this.sourceOffset = t
    }
    this.notify()
  }

  /** Current playhead position in seconds. */
  get currentTime(): number {
    if (!this.buffer) return 0
    if (!this.playing) return this.sourceOffset
    const elapsed = (this.ctx.currentTime - this.sourceStartedAt) * this.rate
    let t = this.sourceOffset + elapsed
    // If looping, the AudioBufferSourceNode wraps natively; mirror that here.
    if (
      this.loopStart != null &&
      this.loopEnd != null &&
      this.loopEnd > this.loopStart
    ) {
      const len = this.loopEnd - this.loopStart
      if (t >= this.loopEnd) {
        t = this.loopStart + ((t - this.loopStart) % len)
      }
    } else if (t >= this.buffer.duration) {
      t = this.buffer.duration
    }
    return t
  }

  play(): void {
    if (!this.buffer || this.playing) return
    this.ctx.resume()
    this.startSource(this.sourceOffset)
  }

  pause(): void {
    if (!this.playing) return
    const t = this.currentTime
    this.stopSource()
    this.sourceOffset = t
    this.playing = false
    this.notify()
  }

  /** Seek to `t` (seconds). Continues playing if currently playing. */
  setTime(t: number): void {
    if (!this.buffer) return
    const clamped = Math.max(0, Math.min(this.buffer.duration, t))
    if (this.playing) {
      this.stopSource()
      this.startSource(clamped)
    } else {
      this.sourceOffset = clamped
      this.notify()
    }
  }

  /** Set the loop region. Call with null to clear. Apply takes effect
   *  on the *currently playing* source so an in-flight loop wraps immediately
   *  at the new bounds (Rekordbox does the same). */
  setLoop(region: { start: number; end: number } | null): void {
    if (region == null) {
      this.loopStart = null
      this.loopEnd = null
      if (this.source) {
        this.source.loop = false
      }
      return
    }
    this.loopStart = region.start
    this.loopEnd = region.end
    if (this.source) {
      this.source.loopStart = region.start
      this.source.loopEnd = region.end
      this.source.loop = true
    } else if (this.playing) {
      // No source but marked playing? Restart at loop start.
      this.startSource(region.start)
    }
  }

  setVolume(v: number): void {
    this.gain.gain.value = v
  }

  onChange(fn: () => void): () => void {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  destroy(): void {
    this.stopSource()
    this.listeners.clear()
    this.ctx.close()
  }

  private startSource(offset: number): void {
    if (!this.buffer) return
    const src = this.ctx.createBufferSource()
    src.buffer = this.buffer
    src.playbackRate.value = this.rate
    src.connect(this.gain)
    if (this.loopStart != null && this.loopEnd != null) {
      src.loop = true
      src.loopStart = this.loopStart
      src.loopEnd = this.loopEnd
    }
    src.onended = () => {
      if (src === this.source && !src.loop) {
        this.playing = false
        this.sourceOffset = this.buffer ? this.buffer.duration : 0
        this.source = null
        this.notify()
      }
    }
    src.start(0, offset)
    this.source = src
    this.sourceStartedAt = this.ctx.currentTime
    this.sourceOffset = offset
    this.playing = true
    this.notify()
  }

  private stopSource(): void {
    if (this.source) {
      try {
        this.source.onended = null
        this.source.stop()
      } catch {
        // already stopped
      }
      this.source.disconnect()
      this.source = null
    }
  }

  private notify(): void {
    this.listeners.forEach((fn) => fn())
  }
}
