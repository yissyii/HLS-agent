# Source map

The framework's active Python source is currently organized by responsibility:

- `../serve/`: inference transport and runtime configuration.
- `../agent/`: generation/validation/repair controller, context, policy and candidate selection.
- `../evaluation/`: manifest handling, C simulation, HLS synthesis and compatibility entry points.
- `../local_eval/`: removable development session management and whole-round network-failure restarts.
- `../rag/`: standalone manual ingestion, embeddings and retrieval; not connected to the default Agent flow.
- `../tools/`: standalone diagnostics.

Keep this map until source is deliberately moved into a single package; do not duplicate files here.
