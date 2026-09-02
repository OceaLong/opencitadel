[English](README.md) · [简体中文](README.zh-CN.md)

# Repository scripts

- `quickstart.sh` creates a local `.env`, generates independent secrets, builds the sandbox image, and starts the core stack.
- `quickstart.sh --reset-data` explicitly removes the stack's containers and named volumes before starting from an empty database.
- `check-docs.sh` checks required bilingual documents and rejects retired product names from executable deployment surfaces.

Neither script stages or commits Git changes.
