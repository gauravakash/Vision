"""
X Intent URL generator.

Generates URLs that open the X compose window with pre-filled text.
No API access or login required -- works via browser redirect.

Usage:
    from backend.intent_url import IntentURL

    url = IntentURL.tweet("Hello world")
    # -> "https://x.com/intent/tweet?text=Hello%20world"

Module-level class with static methods (no instantiation needed).
"""

import urllib.parse


class IntentURL:
    """Generates X intent URLs for composing tweets, replies, and quotes."""

    BASE = "https://x.com/intent/tweet"

    @staticmethod
    def tweet(text: str) -> str:
        """
        New tweet intent URL.

        Opens X compose with text pre-filled.
        """
        encoded = urllib.parse.quote(text, safe="")
        return f"{IntentURL.BASE}?text={encoded}"

    @staticmethod
    def reply(text: str, tweet_id: str) -> str:
        """
        Reply intent URL.

        Opens X compose as a reply to the given tweet.
        """
        encoded = urllib.parse.quote(text, safe="")
        return f"{IntentURL.BASE}?in_reply_to={tweet_id}&text={encoded}"

    @staticmethod
    def quote(text: str, tweet_url: str) -> str:
        """
        Quote tweet intent URL.

        Opens X compose with text + quoted tweet URL.
        """
        encoded_text = urllib.parse.quote(text, safe="")
        encoded_url = urllib.parse.quote(tweet_url, safe="")
        return f"{IntentURL.BASE}?text={encoded_text}%20{encoded_url}"

    @staticmethod
    def extract_tweet_id(url: str) -> str | None:
        """Extract tweet ID from a URL like https://x.com/user/status/1234567."""
        import re
        match = re.search(r"/status/(\d+)", url)
        return match.group(1) if match else None
