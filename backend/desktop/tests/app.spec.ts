import { expect, test } from "@playwright/test"

test.beforeEach(async ({ page }) => {
  await page.goto("/")
  await page.evaluate(() => localStorage.clear())
  await page.reload()
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

  await page.getByRole("row", { name: /Dirty Dancin/ }).click()
  await expect(page.getByText("Local preview")).toBeVisible()
})

test("reads authoritative Rekordbox waveform and exact cue timing", async ({ page }) => {
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await expect(page.getByRole("heading", { name: "Rekordbox playlists" })).toBeVisible()

  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await expect(page.getByText("Tony Dark Eyes")).toBeVisible()
  await page.getByRole("button", { name: /Dirty Dancin/ }).click()

  await expect(page.getByText("Rekordbox analysis")).toBeVisible()
  await expect(page.getByText("PWV4 overview · PWV5 detail")).toBeVisible()
  await expect(page.getByLabel(/Rekordbox color overview waveform/)).toBeVisible()
  await expect(page.getByLabel(/Rekordbox color detail waveform/)).toBeVisible()
  const paintedPixels = await page.getByLabel(/Rekordbox color overview waveform/).evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data ?? []
    let painted = 0
    for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0) painted += 1
    return painted
  })
  expect(paintedPixels).toBeGreaterThan(500)
  await expect(page.getByText("Authoritative Rekordbox data")).toBeVisible()
  await expect(page.getByText("Exact timing")).toBeVisible()

  await page.screenshot({ path: ".artifacts/rekordbox-waveform.png", fullPage: true })
})

test("keeps destructive cue removal behind a confirmation", async ({ page }) => {
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.getByLabel("Remove generated hot cues from Set 20").click()
  await expect(page.getByRole("heading", { name: "Remove generated hot cues?" })).toBeVisible()
  await expect(page.getByText(/Manual and unrecognized cues remain untouched/)).toBeVisible()
  await page.getByRole("button", { name: "Cancel" }).click()
  await expect(page.getByRole("heading", { name: "Remove generated hot cues?" })).not.toBeVisible()
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
  await expect(page.getByText(/Only verified SoundCloud DL loops are changed/)).toBeVisible()
})

test("remains usable at the minimum desktop width", async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 700 })
  await page.getByRole("button", { name: "Rekordbox" }).first().click()
  await page.locator(".playlist-main").filter({ hasText: "Set 20" }).click()
  await page.getByRole("button", { name: /Dirty Dancin/ }).click()
  await expect(page.getByText("PWV4 overview · PWV5 detail")).toBeVisible()
  await page.screenshot({ path: ".artifacts/rekordbox-waveform-compact.png", fullPage: true })
})
