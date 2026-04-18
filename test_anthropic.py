import os

import anthropic

client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

if not client.api_key:
    raise ValueError("Set ANTHROPIC_API_KEY in your environment before running this script.")
try:
    message = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=1024,
        messages=[
            {"role": "user", "content": "Hello, Claude"}
        ]
    )
    print("Success:")
    print(message.content)
except Exception as e:
    print("Error:")
    print(e)
