# Bible Service Roadmap

## Phase 1: Launch (Current)
**Goal:** Provide stable, high-performance access to public domain Bible translations.

### Features
- Support for 4 core free translations:
  - King James Version (KJV)
  - American Standard Version (ASV)
  - Revised Version 1885 (RV)
  - World English Bible (WEB)
- High-speed lookup via JSDelivr CDN.
- Multi-verse range support (e.g., John 3:16-18).
- Caching of version manifest and verse text.

### Implementation Details
- Content sourced from `wldeh/bible-api` repository.
- Synchronous routing via `ScriptureService`.
- GraphQL interface with tier-specific descriptions.

---

## Phase 2: Pro Features (Future)
**Goal:** Unlock premium, copyrighted, and international translations via external API providers.

### Proposed Features
- **Premium Translations:** NIV, ESV, NLT, NASB.
- **Provider Integration:** Re-activate `APIBibleService` (American Bible Society).
- **International Support:** Expanded list of 200+ languages from the full CDN manifest.

### Technical Tasks
- [ ] Implement `ENABLE_PREMIUM_BIBLE_VERSIONS` feature flag logic.
- [ ] Set up secure API key management for `API_BIBLE_KEY`.
- [ ] Add `tier` field to `BibleVersion` GraphQL type.
- [ ] Implement usage tracking for premium API calls.
- [ ] Standardize book ID mapping across all providers.

### Estimates
- Feature Flag & Basic Premium Routing: 1-2 days
- Full Provider Integration & Testing: 3-5 days
- Compliance & Analytics: 2 days

---

## Limitations & Future Improvements
- **Offline Mode:** Initial version requires internet access. Future versions could cache common books locally.
- **Search:** Full-text search across multiple versions is currently out of scope for the backend (delegated to providers or dedicated search index).
- **Audio/Visual:** Integration with Audio Bible APIs (e.g., Bible Brain).
