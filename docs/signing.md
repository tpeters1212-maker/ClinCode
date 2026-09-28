# Removing the macOS "cannot check it for malicious software" warning

Not active. The Mac build is unsigned, so each Mac shows a one-time warning
(bypass: System Settings, Privacy & Security, Open Anyway).

To remove the warning later, join the Apple Developer Program ($99/year), then
add these repository secrets in GitHub (Settings, Secrets and variables, Actions).
The build signs and notarizes automatically once they exist; nothing else changes.

- `MACOS_CERT_P12`: the "Developer ID Application" certificate exported from
  Keychain Access as a .p12 file, base64-encoded
- `MACOS_CERT_PASSWORD`: the password chosen when exporting the .p12
- `APPLE_ID`: the Apple ID email used for the developer account
- `APPLE_APP_PASSWORD`: an app-specific password from account.apple.com
- `APPLE_TEAM_ID`: the 10-character Team ID from developer.apple.com, Membership

Windows shows a similar one-time SmartScreen prompt ("More info", "Run anyway").
Removing it needs a Windows code-signing certificate, which is a separate purchase.
