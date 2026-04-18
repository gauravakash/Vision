import asyncio
from backend.notifier import notifier

async def main():
    await notifier.initialize()
    
    await notifier.send_spike_alert(
        topic_tag="AI Agents",
        spike_percent=420.5,
        volume="1.2M",
        context="AI Agents are taking over software engineering.",
        desk_name="Tech Desk",
        desk_id=1
    )
    print("Spike alert sent.")
    
    await notifier.send_drafts_ready(
        desk_name="Tech Desk",
        draft_count=5,
        top_topic="AI Agents",
        run_id="test-1234",
        draft_previews=[
            {"account_handle": "elonmusk", "text": "Something about AI agents..."}
        ]
    )
    print("Drafts ready sent.")

if __name__ == "__main__":
    asyncio.run(main())
