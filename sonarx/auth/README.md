# Authentication and access policy

RBAC policy and runtime guards currently live in `sonarx/guards.py` and are enforced by `sonarx/bot.py`. This package is the planned separation point for future authentication and per-resource authorization modules. Secret encryption remains in `sonarx/security.py`.
