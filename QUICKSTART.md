# Quickstart

1. Open the repo in your agent and say **“Set up Vibe Editing for me.”**
2. Follow the short [onboarding](ONBOARDING.md): tools, the same `/edit` connections, brands and delivery routes.
3. Give it a video and say **“Make shorts from this.”**

AssemblyAI is the primary transcriber. The agent walks you through getting your own keys and
saves them privately. [Full connection inventory](docs/CONNECTIONS.md).

Finished clips stay in the project's `20_DELIVER/`. To deliver a selected batch, say
**“Push these clips to Frame and Monday.”**

Technical check: `python3 plugins/vibe-editing/doctor.py`. Connection/progress check:
`python3 plugins/vibe-editing/onboard.py status`. A local doctor pass is not an end-to-end test.
