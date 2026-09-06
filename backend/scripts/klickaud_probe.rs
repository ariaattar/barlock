#!/usr/bin/env -S cargo +nightly -Zscript
---cargo
[package]
edition = "2021"

[dependencies]
anyhow = "1"
reqwest = { version = "0.12", default-features = false, features = ["blocking", "cookies", "json", "rustls-tls"] }
scraper = "0.24"
serde = { version = "1", features = ["derive"] }
url = "2"
---

use anyhow::{bail, Context, Result};
use reqwest::blocking::Client;
use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, ORIGIN, REFERER};
use scraper::{Html, Selector};
use serde::Deserialize;
use std::collections::BTreeSet;
use std::env;
use std::time::Duration;
use url::Url;

const HOME_URL: &str = "https://www.klickaud.org/en17/";
const CSRF_URL: &str = "https://www.klickaud.org/csrf-token-endpoint.php";
const DOWNLOAD_URL: &str = "https://www.klickaud.org/download.php";

#[derive(Deserialize)]
struct CsrfResponse {
    csrf_token: String,
}

fn main() -> Result<()> {
    let track_url = env::args()
        .skip(1)
        .find(|argument| argument != "--")
        .context("usage: klickaud_probe.rs <soundcloud-track-url>")?;
    validate_soundcloud_url(&track_url)?;

    let client = Client::builder()
        .cookie_store(true)
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(45))
        .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36")
        .build()
        .context("could not create the HTTP client")?;

    // KlickAud's page fetches this token immediately before submitting. The
    // response also sets a matching HttpOnly cookie, which Client retains.
    let csrf = client
        .get(CSRF_URL)
        .header(ACCEPT, "application/json")
        .header(ACCEPT_LANGUAGE, "en-US,en;q=0.9")
        .header(REFERER, HOME_URL)
        .send()
        .context("could not request KlickAud's CSRF token")?
        .error_for_status()
        .context("KlickAud rejected the CSRF token request")?
        .json::<CsrfResponse>()
        .context("KlickAud returned an invalid CSRF response")?;

    let response = client
        .post(DOWNLOAD_URL)
        .header(ACCEPT, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        .header(ACCEPT_LANGUAGE, "en-US,en;q=0.9")
        .header(ORIGIN, "https://www.klickaud.org")
        .header(REFERER, HOME_URL)
        .form(&[
            ("value", track_url.as_str()),
            ("csrf_token", csrf.csrf_token.as_str()),
        ])
        .send()
        .context("could not submit the SoundCloud URL to KlickAud")?;

    let status = response.status();
    let final_url = response.url().clone();
    let body = response
        .text()
        .context("could not read KlickAud's response body")?;

    println!("status: {status}");
    println!("final URL: {final_url}");
    println!("response bytes: {}", body.len());

    if !status.is_success() {
        bail!("KlickAud returned a non-success response");
    }

    summarize_html(&body, &final_url)?;
    Ok(())
}

fn validate_soundcloud_url(value: &str) -> Result<()> {
    let url = Url::parse(value).context("the argument is not a valid URL")?;
    if url.scheme() != "https" {
        bail!("the SoundCloud URL must use HTTPS");
    }

    let host = url.host_str().unwrap_or_default();
    let is_soundcloud = host == "soundcloud.com"
        || host.ends_with(".soundcloud.com")
        || host == "on.soundcloud.com";
    if !is_soundcloud {
        bail!("expected a soundcloud.com or on.soundcloud.com URL");
    }
    Ok(())
}

fn summarize_html(body: &str, base_url: &Url) -> Result<()> {
    let document = Html::parse_document(body);
    let title_selector = Selector::parse("title").expect("valid title selector");
    let link_selector = Selector::parse("a[href]").expect("valid link selector");

    if let Some(title) = document.select(&title_selector).next() {
        let text = title.text().collect::<String>();
        println!("page title: {}", text.trim());
    }

    let mode = javascript_string(body, "downloadMode");
    let file_name = javascript_string(body, "defaultFileName");
    let direct_url = javascript_string(body, "directDownloadUrl");

    if let Some(mode) = &mode {
        println!("download mode: {mode}");
    }
    if let Some(file_name) = &file_name {
        println!("file name: {file_name}");
    }

    let mut candidates = BTreeSet::new();
    if let Some(direct_url) = direct_url.filter(|value| !value.is_empty()) {
        candidates.insert(direct_url);
    }

    for anchor in document.select(&link_selector) {
        let href = anchor.value().attr("href").unwrap_or_default();
        let href_lower = href.to_ascii_lowercase();
        if href_lower.contains(".mp3")
            || href_lower.contains("sndcdn.com")
            || href_lower.contains("dl.klickaud.org")
        {
            if let Ok(resolved) = base_url.join(href) {
                candidates.insert(resolved.to_string());
            }
        }
    }

    if candidates.is_empty() {
        if mode.as_deref() == Some("worker") {
            println!("download candidates: worker conversion requires KlickAud's SSE flow");
        } else {
            println!("download candidates: none found");
        }
    } else {
        println!("download candidates:");
        for candidate in candidates {
            println!("  {candidate}");
        }
    }

    Ok(())
}

fn javascript_string(body: &str, name: &str) -> Option<String> {
    let marker = format!("const {name} =");
    let value = body.split_once(&marker)?.1.trim_start();
    let mut deserializer = serde_json::Deserializer::from_str(value);
    String::deserialize(&mut deserializer).ok()
}
