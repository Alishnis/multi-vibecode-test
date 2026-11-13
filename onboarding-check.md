# Onboarding Check: Joining the Project and Getting the Latest Code

This explains how a new teammate joins the project and keeps up with the team's code.

- **Repo:** https://github.com/Alishnis/multi-vibecode-test (shared branch: `main`)
- **Rule:** nobody pushes to `main` directly. Work happens on a task branch, goes up as a pull request, and a person reviews and merges it on GitHub.

## 1. Prerequisites

- `git`, `python3`, and the GitHub CLI (`gh`)
- Claude Code
- Access to the GitHub repo (ask the owner to add you as a collaborator)
- Run `gh auth login` once to connect your GitHub account

## 2. Join the project

1. Clone the repo:
   ```bash
   git clone https://github.com/Alishnis/multi-vibecode-test.git
   cd multi-vibecode-test
   ```
2. Set up your identity. Copy the example config and fill in your name:
   ```bash
   cp coord/me.example.json coord/me.json
   ```
   ```json
   { "name": "YourName", "room": "demo", "server": "http://127.0.0.1:8000" }
   ```
   Use the room name and server URL the team gave you. Those values are a placeholder example.
3. Open the folder in Claude Code. `.mcp.json` loads the `team-contract` tool automatically.
4. Read the team contract. It is shown to your agent on every prompt. It lists decisions the team has already made, such as the stack, ports and API shapes. Follow them.

## 3. Get the latest code

Ask Claude in plain words, or run the matching command:

| You say to Claude | Terminal equivalent |
|---|---|
| "Get the latest code" | `python3 coord/git_sync.py status` (fetches `main` and reports where you stand) |
| "Where does my branch stand?" | `python3 coord/git_sync.py status` |

You can also use plain git:
```bash
git checkout main
git pull origin main
```

To get merged work automatically, run the watcher. It notifies you about approved PRs and syncs after a merge:
```bash
python3 coord/git_sync.py watch
```

## 4. Start your own task

1. Ask Claude "start a new task called `<yourname>/<topic>`", or run:
   ```bash
   python3 coord/git_sync.py start yourname/topic
   ```
   This creates your branch from the latest `main`.
2. Before your first edit, Claude claims the files you plan to change. Teammates' agents see the claim. If it reports an **overlap**, talk to that teammate first.
3. Make your changes and commit them.
4. Ask Claude "share my work", or run:
   ```bash
   python3 coord/git_sync.py publish
   ```
   This pushes your branch and opens a pull request.
5. A teammate reviews and merges the PR on GitHub. Then get the latest code again (section 3).

## 5. Quick checklist

- [ ] Repo cloned and `gh auth login` done
- [ ] `coord/me.json` filled in with your name
- [ ] Claude Code opened in the project and the contract is visible
- [ ] Latest `main` pulled
- [ ] Task branch started with `start`
- [ ] Work published as a PR, not pushed to `main`
