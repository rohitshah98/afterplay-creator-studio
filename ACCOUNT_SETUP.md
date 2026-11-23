# Connect your YouTube and TikTok accounts

The app is running at **http://localhost:8787**. Open **Publishing → Your publishing accounts**. Your Google and TikTok passwords are never entered into Afterplay.

## Google / YouTube Shorts

1. In your existing Google Cloud project, enable **YouTube Data API v3** and **YouTube Analytics API**.
2. Configure the OAuth consent screen and add your Google account as a test user if the app is in testing.
3. Use an OAuth **Web application** client. Register this exact redirect URI:
   `http://localhost:8787/api/accounts/youtube/callback`
4. In Afterplay, click the YouTube **App setup** button. Enter the client ID and client secret locally, then **Save encrypted app credentials**. Do not send secrets in chat.
5. Click **Connect YouTube** and select the Google account/channel you intend to use. Review the official consent screen yourself. After the callback, Afterplay shows the channel name returned by YouTube.
6. The integration requests upload and read/analytics scopes. Unverified projects and Google OAuth testing policies can limit uploads, visibility or token lifetime. Resolve these through Google’s official review/configuration process, never by bypassing warnings.

YouTube Shorts use the ordinary official `videos.insert` upload API. The platform decides Shorts classification; this app exports vertical videos and does not claim a dedicated Shorts upload endpoint.

## TikTok

1. In your existing TikTok developer app, configure Login Kit / the approved scopes and the Content Posting API where permitted.
2. Register the callback shown by Afterplay:
   `http://localhost:8787/api/accounts/tiktok/callback`
   TikTok may require an approved HTTPS callback and may not accept localhost for your app. **Do not bypass that restriction.** If your existing app already has an HTTPS callback on a domain you control, enter that exact URI in **Registered callback override** during App setup. After completing official OAuth, use **Complete an approved external callback locally** and paste the final authorized callback URL into the local masked field. Afterplay verifies the exact registered redirect, the original short-lived OAuth state and the one-time code, then exchanges it with the same redirect URI. Never paste that URL in chat. Your own callback must leave the authorization code available for this handoff rather than consuming it in a different application. Alternatively use an approved authenticated HTTPS deployment. This build deliberately does not expose your local server to the public internet.
3. In Afterplay, click TikTok **App setup**. Enter the TikTok client key and client secret locally and save them encrypted.
4. Click **Connect TikTok** and complete the official authorization yourself. The requested permissions include `video.publish` plus supported account/video analytics scopes. Your app must actually have those scopes approved.

### Direct Post eligibility

Registering a developer app does not mean TikTok has approved its intended use. TikTok’s current Content Sharing Guidelines exclude a private utility solely for uploading to accounts you or your team manage. This local personal studio defaults to the **official TikTok manual uploader** for that reason. The real Direct Post adapter is included for a developer application whose audience and approved use permit it.

Only for an app whose approved use permits Direct Post, set these environment variables before starting the server:

```dotenv
TIKTOK_DIRECT_POST_ENABLED=true
# True only if TikTok has actually audited your application:
TIKTOK_APP_AUDITED=false
# True only if delayed sends are permitted for your approved application/use:
TIKTOK_SCHEDULING_APPROVED=false
```

These flags do not grant permission or remove any platform restriction. TikTok still enforces approved scopes, privacy rules, creator caps and app review. An unaudited client is restricted to `SELF_ONLY`, and the app filters returned privacy options accordingly. The creator’s nickname, available privacy options, maximum duration and interaction restrictions are fetched from `creator_info/query`; privacy has no preselected value and interactions are off by default. Commercial and AI-content disclosures are explicit user choices. The user must review the applicable TikTok music/branded-content confirmation before Direct Post.

When Direct Post is not available, a TikTok job becomes **manual required**. Download its immutable finished MP4, copy its prepared caption and open TikTok Studio’s official upload page. Complete publication there. Afterplay does not claim or fabricate confirmation for a manual post.

## Credential storage

Client secrets and OAuth tokens are Fernet-encrypted in the local database. A stable, owner-readable `data/oauth.key` is generated with mode `0600`; alternatively supply `TOKEN_ENCRYPTION_KEY`. Do not lose or rotate that key without reconnecting accounts. Credentials are never returned by configuration/status endpoints. OAuth callback query strings are redacted from access logging, and the launcher disables access logging. No account passwords are stored.

Tokens refresh through official token endpoints; rotating refresh tokens are persisted encrypted. Refreshes are serialized in this local single-process server. Upload jobs are bound to the channel/creator ID that authorized them, so connecting another account does not silently redirect a queued post.

## Before your first upload

Use a video you own and a test visibility you explicitly choose. Review titles, descriptions, hashtags, tags, audience, privacy and any schedule before checking the upload authorization box. The button **🚀 PUBLISH TO BOTH PLATFORMS** creates two independent jobs. This development session does not upload a real video or accept OAuth scopes on your behalf.

Official references:

- [Google OAuth for web applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [YouTube resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [YouTube video scheduling fields](https://developers.google.com/youtube/v3/docs/videos#status.publishAt)
- [TikTok Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)
- [TikTok Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [TikTok creator information](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info)
