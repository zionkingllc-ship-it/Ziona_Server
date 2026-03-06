class APIBibleService:
    """
    Premium Bible scripture service using API.Bible.

    Supports: ESV, NIV, NASB (and 50+ more versions)
    Cost: Free tier 1000 requests/day, then $49/month

    TODO: Implement when premium versions are needed
    - Sign up at https://scripture.api.bible
    - Get API key
    - Store in environment variable: API_BIBLE_KEY
    """

    @staticmethod
    def fetch_verse(book, chapter, verse_start, verse_end, version):
        raise NotImplementedError(
            "Premium Bible API not yet implemented. " "Use free versions (KJV, ASV, WEB) for MVP."
        )
