ROUTING = [
    ("what's eighteen percent of two hundred and forty", "calculate"),
    ("add three thousand two hundred and nineteen", "calculate"),
    ("remember I prefer window seats", "remember"),
    ("what have I asked you to remember", "recall"),
    ("forget what I said about the gym", "forget"),
    ("what's the weather in Austin", "get_weather"),
    ("is it raining in Noida right now", "get_weather"),
    ("what does my resume say about my experience", "ask"),
    ("what did we decide on the launch call", "ask"),
    ("summarize the document I uploaded", "summarize"),
    ("go through my call recording and pull out the action items", "recording_insights"),
    ("give me the decisions from that meeting recording", "recording_insights"),
    ("what documents do I have", "list_documents"),
    ("research the best SDE internships and make me a deck", "deep_research"),
    ("compare the best electric cars under twenty lakhs and write it up", "deep_research"),
    ("put together a report on the Indian EV market", "deep_research"),
    ("switch to my work assistant", "switch_assistant"),
    ("what files are in my folder", None),
    ("who won the cricket match last night", None),
]

SPEECH = [
    ("plain sentence", "It's twenty-six degrees and clear in Austin.", True),
    ("bold markers", "Here's an overview: **Dhruv Diwakirti** works in Noida.", False),
    ("bullet list", "- Location: Noida\n- Contact: 9302551161", False),
    ("markdown link", "See [LinkedIn](linkedin.com/in/dhruvdiwa) for more.", False),
    ("bare url", "It's at https://example.com/report.", False),
    ("heading", "## Summary\nHe is a student.", False),
    ("arithmetic stays", "That's 2 * 3, which is 6.", True),
    ("filename stays", "Dhruv_Diwakirti_Resume.pdf is two pages long.", True),
]

MESSY_DEBRIEF = {
    "title": "**Launch** date moved",
    "spoken": "The call was about the launch.\n  See https://example.com/notes for more.",
    "summary": ["- The team met to lock the **launch** date."],
    "decisions": [{"owner": "Ana", "call": "ship Friday"}],
    "action_items": ["*Ana* writes the rollback plan by `Wednesday`."],
    "follow_ups": [None, "", "Legal has not signed off."],
}
