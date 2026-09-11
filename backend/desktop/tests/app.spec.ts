import { expect, test } from "@playwright/test"

test.beforeEach(async ({ page }) => {
  await page.goto("/")
})

test("resolves and completes a delta-aware import", async ({ page }) => {
  await page.getByLabel("SoundCloud URL").fill("https://soundcloud.com/ariaattar/sets/set")
  await page.getByRole("button", { name: "Resolve" }).click()

  await expect(page.getByText("Resolved source")).toBeVisible()
  await expect(page.getByText("+6", { exact: true })).toBeVisible()
  await expect(page.getByText("36", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: /Import 6 tracks/ }).click()
  await expect(page.getByText("Import complete")).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText("6 of 6 tracks complete")).toBeVisible()
  await expect(page.getByLabel("Download fallback results").getByText("1 recovered by KlickAud", { exact: true })).toBeVisible()
  await expect(page.getByText("Ready via KlickAud", { exact: true })).toBeVisible()

  await page.getByRole("row", { name: /All The Time/ }).click()
  await expect(page.getByText("Recovered by KlickAud", { exact: true })).toBeVisible()
  await expect(page.getByText("Primary stream unavailable", { exact: true })).toBeVisible()
  await expect(page.getByText("Local preview")).toBeVisible()
})

test("reads authoritative Rekordbox waveform and exact cue timing", async ({ page }) => {
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await expect(page.getByRole("heading", { name: "Rekordbox playlists" })).toBeVisible()

  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await expect(page.getByText("Tony Dark Eyes")).toBeVisible()
  await page.getByRole("button", { name: /Dirty Dancin/ }).click()

  await expect(page.getByText("Rekordbox analysis")).toBeVisible()
  await expect(page.getByText(/PWV6 · Native 3-band overview · PWV7 · Native 3-band · 150 Hz detail/)).toBeVisible()
  await expect(page.getByLabel(/native Rekordbox overview waveform/)).toBeVisible()
  await expect(page.getByLabel(/native Rekordbox detail waveform/)).toBeVisible()
  const paintedPixels = await page.getByLabel(/native Rekordbox overview waveform/).evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data ?? []
    let painted = 0
    for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0) painted += 1
    return painted
  })
  expect(paintedPixels).toBeGreaterThan(500)
  const threeBandPixels = await page.getByLabel(/native Rekordbox detail waveform/).evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data ?? []
    let low = 0
    let mid = 0
    let high = 0
    for (let index = 0; index < pixels.length; index += 4) {
      const red = pixels[index]
      const green = pixels[index + 1]
      const blue = pixels[index + 2]
      const alpha = pixels[index + 3]
      if (!alpha) continue
      if (blue > 150 && red < 100) low += 1
      if (red > 150 && green > 80 && blue < 120) mid += 1
      if (red > 220 && green > 220 && blue > 220) high += 1
    }
    return { low, mid, high }
  })
  expect(threeBandPixels.low).toBeGreaterThan(100)
  expect(threeBandPixels.mid).toBeGreaterThan(100)
  expect(threeBandPixels.high).toBeGreaterThan(100)
  await expect(page.getByText("Authoritative Rekordbox data")).toBeVisible()
  await expect(page.getByText("Exact timing")).toBeVisible()

  await page.screenshot({ path: ".artifacts/rekordbox-waveform.png", fullPage: true })
})

test("edits and auditions an autosaved Rekordbox draft", async ({ page }) => {
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await page.getByRole("button", { name: /Dirty Dancin/ }).click()
  await page.getByRole("button", { name: "Open editor" }).click()

  await expect(page.getByRole("heading", { name: "Dirty Dancin'" })).toBeVisible()
  await expect(page.getByLabel("Editable Rekordbox waveform")).toBeVisible()
  await expect(page.getByText("Authoritative ANLZ")).toBeVisible()
  await expect(page.getByRole("spinbutton", { name: "BPM" })).toHaveValue("130")

  await page.getByRole("spinbutton", { name: "BPM" }).fill("128")
  await page.getByLabel("Camelot key").selectOption("9A")
  await expect(page.getByText("Em", { exact: true })).toBeVisible()
  await expect(page.getByText(/Draft saved · r/)).toBeVisible()
  const savedRevision = await page.locator(".draft-state").innerText()
  await page.waitForTimeout(900)
  await expect(page.locator(".draft-state")).toHaveText(savedRevision)

  await page.locator(".loop-targets button").filter({ hasText: "Exit Loop" }).click()
  await page.getByRole("button", { name: /Through/ }).click()
  await expect(page.getByRole("button", { name: /Through/ })).toHaveClass(/active/)
  const loopAudition = page.locator(".ab-controls button").filter({ hasText: "Loop" })
  await loopAudition.click()
  await expect(loopAudition).toHaveClass(/active/)
  await page.waitForTimeout(1_100)
  await expect(loopAudition).toHaveClass(/active/)
  await loopAudition.click()
  await expect(loopAudition).not.toHaveClass(/active/)

  const paintedPixels = await page.getByLabel("Editable Rekordbox waveform").evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data ?? []
    let painted = 0
    let redMarkers = 0
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index + 3] > 0) painted += 1
      if (pixels[index] > 220 && pixels[index + 1] < 70 && pixels[index + 2] < 80 && pixels[index + 3] > 0) redMarkers += 1
    }
    return { painted, redMarkers }
  })
  expect(paintedPixels.painted).toBeGreaterThan(1000)
  expect(paintedPixels.redMarkers).toBeGreaterThan(30)

  await page.screenshot({ path: ".artifacts/track-editor.png", fullPage: true })
})

test("keeps the waveform editor usable at the minimum window size", async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 700 })
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await page.getByRole("button", { name: /Dirty Dancin/ }).click()
  await page.getByRole("button", { name: "Open editor" }).click()

  await expect(page.getByLabel("Editable Rekordbox waveform")).toBeVisible()
  await expect(page.getByRole("button", { name: "Review and apply" })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: ".artifacts/track-editor-compact.png", fullPage: true })
})

test("keeps destructive cue removal behind a confirmation", async ({ page }) => {
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.getByLabel("Remove generated hot cues from Set 20").click()
  await expect(page.getByRole("heading", { name: "Remove generated hot cues?" })).toBeVisible()
  await expect(page.getByText(/Manual and unrecognized cues remain untouched/)).toBeVisible()
  await page.getByRole("button", { name: "Cancel" }).click()
  await expect(page.getByRole("heading", { name: "Remove generated hot cues?" })).not.toBeVisible()
})

test("explains protected tracks before accepting a licensed replacement", async ({ page }) => {
  await page.evaluate(() => {
    localStorage.setItem("soundcloud-dl.desktop.jobs", JSON.stringify([{
      id: "protected-job",
      title: "ATTAR (Likes)",
      sourceUrl: "https://soundcloud.com/ariaattar/likes",
      state: "partial",
      stage: "Needs source files",
      progress: 100,
      completed: 0,
      total: 1,
      startedAt: new Date().toISOString(),
      destination: "rekordbox",
      analyze: true,
      cueMode: "off",
      playlistName: "ATTAR (Likes)",
      outputDir: "/Users/ariaattar/Music/SoundCloud/aria-likes",
      summary: "1 protected track needs a licensed local audio file.",
      tracks: [{
        id: "2376567626",
        title: "Love Forever (feat. Kuuda)",
        artist: "SoundCloud",
        stage: "failed",
        progress: 100,
        cueStatus: "off",
        error: "ERROR: [soundcloud] 2376567626: This video is DRM protected",
      }],
    }]))
  })
  await page.reload()
  await page.getByRole("button", { name: "Activity" }).click()
  await page.locator(".job-row").filter({ hasText: "ATTAR (Likes)" }).click()
  await expect(page.getByText("Source files needed")).toBeVisible()
  await page.getByRole("row", { name: /Love Forever/ }).click()
  await expect(page.getByText("Encrypted playback only")).toBeVisible()
  await page.getByRole("button", { name: "Choose licensed audio" }).click()
  await expect(page.getByRole("heading", { name: "Attach a licensed source?" })).toBeVisible()
  await expect(page.getByText(/Non-commercial use by itself is not permission/)).toBeVisible()
  await page.screenshot({ path: ".artifacts/protected-track-resolver.png", fullPage: true })
})

test("stops stale analysis indicators after an engine crash", async ({ page }) => {
  await page.evaluate(() => localStorage.setItem("soundcloud-dl.desktop.jobs", JSON.stringify([{
    id: "crashed", title: "Crashed import", sourceUrl: "https://soundcloud.com/demo/sets/crash",
    state: "failed", stage: "Failed", progress: 58, completed: 0, total: 1,
    startedAt: new Date().toISOString(), outputDir: "/tmp/audio", destination: "download",
    summary: "error: This video is DRM protected",
    tracks: [{ id: "123", title: "Recovered track", artist: "SoundCloud", stage: "analyzing",
      progress: 58, cueStatus: "off", downloadMethod: "klickaud", fallbackAttempted: true }],
  }])))
  await page.reload()
  await page.getByRole("button", { name: "Activity" }).click()
  await page.locator(".job-row").filter({ hasText: "Crashed import" }).click()
  const row = page.getByRole("row", { name: /Recovered track/ })
  await expect(row.getByText("Interrupted", { exact: true })).toBeVisible()
  await expect(row.locator(".spin")).toHaveCount(0)
  const job = await page.evaluate(() => JSON.parse(localStorage.getItem("soundcloud-dl.desktop.jobs")!)[0])
  expect(job.tracks[0].downloadMethod).toBe("klickaud")
  expect(job.summary).toContain("reuses audio already saved")
})

test("retry rebuilds an empty failed job from the refreshed download delta", async ({ page }) => {
  await page.evaluate(() => localStorage.setItem("soundcloud-dl.desktop.jobs", JSON.stringify([{
    id: "deleted-folder", title: "Deleted folder", sourceUrl: "https://soundcloud.com/ariaattar/sets/set",
    state: "failed", stage: "Failed", progress: 3, completed: 0, total: 1,
    startedAt: new Date().toISOString(), outputDir: "/tmp/deleted-audio", destination: "download",
    analyze: false, cueMode: "off", summary: "no tracks to push", tracks: [],
  }])))
  await page.reload()
  await page.getByRole("button", { name: "Activity" }).click()
  await page.locator(".job-row").filter({ hasText: "Deleted folder" }).click()
  await page.getByRole("button", { name: "Retry sync" }).click()
  await expect(page.getByText("Import complete")).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText("6 of 6 tracks complete")).toBeVisible()
  const job = await page.evaluate(() => JSON.parse(localStorage.getItem("soundcloud-dl.desktop.jobs")!)[0])
  expect(job.tracks).toHaveLength(6)
  expect(job.tracks.every((track: { stage: string }) => track.stage === "ready")).toBe(true)
})

test("keeps failures from both download methods visible", async ({ page }) => {
  await page.evaluate(() => {
    localStorage.setItem("soundcloud-dl.desktop.jobs", JSON.stringify([{
      id: "failed-job",
      title: "Failed downloads",
      sourceUrl: "https://soundcloud.com/demo/sets/failures",
      state: "partial",
      stage: "Download failures",
      progress: 100,
      completed: 0,
      total: 1,
      startedAt: new Date().toISOString(),
      destination: "download",
      analyze: true,
      cueMode: "off",
      outputDir: "/Users/ariaattar/Downloads/SoundCloud",
      summary: "1 failed after both download methods.",
      tracks: [{
        id: "failed-123",
        title: "Unavailable Track",
        artist: "SoundCloud",
        stage: "failed",
        progress: 100,
        cueStatus: "off",
        fallbackAttempted: true,
        primaryDownloadError: "Primary stream unavailable",
        error: "yt-dlp: Primary stream unavailable; KlickAud fallback: worker unavailable",
      }],
    }]))
  })
  await page.reload()
  await page.getByRole("button", { name: "Activity" }).click()
  await page.locator(".job-row").filter({ hasText: "Failed downloads" }).click()

  await expect(page.getByText("1 automatic download failed", { exact: true })).toBeVisible()
  await page.getByRole("row", { name: /Unavailable Track/ }).click()
  await expect(page.getByText("Both download methods failed", { exact: true })).toBeVisible()
  await expect(page.locator(".inspector").getByText(/KlickAud fallback: worker unavailable/)).toBeVisible()
  await expect(page.getByText("Primary stream unavailable", { exact: true })).toBeVisible()
})

test("persists a blocked handoff and resumes after a normal Rekordbox close", async ({ page }) => {
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await page.getByRole("button", { name: "Open Rekordbox" }).click()
  await expect(page.getByText("Rekordbox opened")).toBeVisible()

  await page.getByRole("button", { name: "New import" }).click()
  await page.getByLabel("SoundCloud URL").fill("https://soundcloud.com/ariaattar/sets/set")
  await page.getByRole("button", { name: "Resolve" }).click()
  await page.getByRole("button", { name: /Import 6 tracks/ }).click()

  await expect(page.getByText("Action required")).toBeVisible()
  await expect(page.getByRole("paragraph").filter({ hasText: "Rekordbox must close before" })).toBeVisible()
  await page.getByRole("button", { name: "Finish later" }).click()
  await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible()
  await page.locator(".job-row").filter({ hasText: "Waiting for Rekordbox" }).click()
  await expect(page.getByText("Action required")).toBeVisible()
  await page.getByRole("button", { name: "Save and close Rekordbox" }).click()
  await expect(page.getByText("Import complete")).toBeVisible({ timeout: 15_000 })
})

test("previews Doctor repairs before applying them", async ({ page }) => {
  await page.getByRole("button", { name: "Doctor" }).click()
  await expect(page.getByRole("heading", { name: "Rekordbox Doctor" })).toBeVisible()
  await expect(page.getByText("2 generated loops can be aligned")).toBeVisible()
  await page.getByRole("button", { name: "Review" }).click()
  await expect(page.getByRole("heading", { name: "Align generated loops to the beat grid?" })).toBeVisible()
  await expect(page.getByText(/Only verified app-generated loops are changed/)).toBeVisible()
})

test("remains usable at the minimum desktop width", async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 700 })
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await page.getByRole("button", { name: /Dirty Dancin/ }).click()
  await expect(page.getByText(/PWV6 · Native 3-band overview · PWV7 · Native 3-band · 150 Hz detail/)).toBeVisible()
  await page.screenshot({ path: ".artifacts/rekordbox-waveform-compact.png", fullPage: true })
})

test("keeps a resolved import intact when browsing other screens", async ({ page }) => {
  await expect(page.locator('.inspector')).toHaveCount(0)
  await page.getByLabel("SoundCloud URL").fill("https://soundcloud.com/ariaattar/sets/set")
  await page.getByRole("button", { name: "Resolve", exact: true }).click()
  await expect(page.getByText("Resolved source")).toBeVisible()
  await page.getByRole("button", { name: "Downloads", exact: true }).click()
  await page.getByRole("button", { name: "New import", exact: true }).first().click()
  await expect(page.getByText("Resolved source")).toBeVisible()
  await expect(page.getByRole("button", { name: "Import 6 tracks" })).toBeVisible()
})

test("supports keyboard navigation and restores focus after the palette closes", async ({ page }) => {
  const trigger = page.getByRole("button", { name: /Quick navigation/ })
  await trigger.click()
  await page.getByRole("combobox", { name: "Search commands" }).fill("downloads")
  await page.keyboard.press("Enter")
  await expect(page.getByRole("heading", { name: "Downloads", exact: true })).toBeVisible()
  await trigger.click()
  await page.getByRole("combobox", { name: "Search commands" }).fill("nonexistent")
  await expect(page.getByText(/No matching destinations/)).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(trigger).toBeFocused()
})

test("keeps downloads after clearing completed history and deduplicates repeat imports", async ({ page }) => {
  await page.evaluate(() => {
    const track = { id: '123', title: 'One track', artist: 'Artist', stage: 'ready', progress: 100, cueStatus: 'off', path: '/tmp/music/track.mp3' }
    const job = { id: 'one', title: 'One import', sourceUrl: 'https://soundcloud.com/demo/likes', state: 'complete', stage: 'Complete', progress: 100, completed: 1, total: 1, startedAt: new Date().toISOString(), outputDir: '/tmp/music', tracks: [track] }
    localStorage.setItem('soundcloud-dl.desktop.jobs', JSON.stringify([job, {...job, id: 'two'}]))
  })
  await page.reload()
  await page.getByRole('button', { name: 'Downloads', exact: true }).click()
  await expect(page.getByRole('row', { name: /One track/ })).toHaveCount(1)
  await expect(page.locator('.tiny-progress')).toHaveCount(0)
  await page.getByRole('button', { name: 'Activity', exact: true }).click()
  await page.getByRole('button', { name: 'Clear completed' }).click()
  await page.getByRole('button', { name: 'Downloads', exact: true }).click()
  await expect(page.getByRole('row', { name: /One track/ })).toHaveCount(1)
  await page.getByPlaceholder('Search tracks, artists, keys...').fill('no match')
  await expect(page.getByText('No matching tracks')).toBeVisible()
})

test("renders light and compact layouts without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 700 })
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('button', { name: 'Light', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.getByRole('switch', { name: 'Write ID3 tags' })).toBeVisible()
  await page.screenshot({ path: '.artifacts/crate-settings-light.png' })
  await page.getByRole('button', { name: 'New import', exact: true }).first().click()
  const dimensions = await page.locator('.import-workspace').evaluate((element) => ({ scroll: element.scrollWidth, client: element.clientWidth }))
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client)
  await page.screenshot({ path: '.artifacts/crate-import-compact.png' })
})

for (const viewport of [{ width: 1040, height: 680 }, { width: 1440, height: 900 }, { width: 2048, height: 1024 }]) {
  test(`import actions never cover settings at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.getByLabel('SoundCloud URL').fill('https://soundcloud.com/ariaattar/sets/set')
    await page.getByRole('button', { name: 'Resolve', exact: true }).click()
    await expect(page.getByText('Resolved source')).toBeVisible()
    const workspace = page.locator('.import-workspace')
    const footer = page.getByRole('contentinfo', { name: 'Import actions' })
    for (const fraction of [0, .5, 1]) {
      await workspace.evaluate((element, fraction) => { element.scrollTop = fraction * element.scrollHeight }, fraction)
      const contentBox = await workspace.boundingBox()
      const footerBox = await footer.boundingBox()
      expect(contentBox!.y + contentBox!.height).toBeLessThanOrEqual(footerBox!.y + 1)
      await expect(footer.getByRole('button', { name: 'Import 6 tracks' })).toBeInViewport()
    }
    await page.getByRole('button', { name: 'Fill empty slots', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Fill empty slots', exact: true })).toHaveAttribute('aria-pressed', 'true')
    const cueBox = await page.locator('.cue-policy-row').boundingBox()
    const footerBox = await footer.boundingBox()
    expect(cueBox!.y + cueBox!.height).toBeLessThanOrEqual(footerBox!.y)
    await page.getByRole('switch', { name: 'Analyze and tag' }).click()
    await expect(page.getByRole('switch', { name: 'Analyze and tag' })).toHaveAttribute('aria-checked', 'false')
    await page.getByRole('switch', { name: 'Analyze and tag' }).click()
    await workspace.evaluate((element) => { element.scrollTop = element.scrollHeight })
    await page.screenshot({ path: `.artifacts/crate-import-settings-${viewport.width}.png` })
  })
}
