# Agent8088 development memory

Use TencentDB Agent Memory only as optional context for development in this repository.

- Shared Memory Proxy: `http://192.168.3.69:8096`
- Team ID: `team-4enrilfomp`
- Shared agent: `Agent8088 Developer` (`agt-4gzez9mlz6`)
- Use the shared Wiki and CodeGraph to find project architecture, decisions, conventions, and related code before making changes.
- Treat the current checkout, tests, and explicit user requirements as the source of truth. Verify retrieved memory against the code; stale or conflicting memory must not override it.
- Use a personal team key in local client configuration. Never put a key, token, password, `.env` value, or other credential in this repository.
- Development-only scope: do not send production data, customer data, personal data, secrets, or runtime application traffic to the memory server.
- Do not add a runtime dependency on the memory server or change production/deployment behavior to use it.
- Keep private or personal memories out of shared project context; use only team-shared development assets.
- If the LAN server is unavailable, continue locally without blocking development and mention that shared context was unavailable.

