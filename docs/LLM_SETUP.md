# Optional model investigation

Live investigations work without a model and are labeled **Deterministic fallback**. No model credentials were present during this repair, so external inference has **not** been verified.

To enable a compatible chat-completions endpoint, set these server-only variables:

```text
LLM_BASE_URL=https://YOUR_PROVIDER/v1
LLM_MODEL=YOUR_TOOL_CAPABLE_MODEL
LLM_API_KEY=YOUR_SERVER_SIDE_KEY
```

The API sends `POST {LLM_BASE_URL}/chat/completions` with OpenAI-compatible `tools`, `tool_choice: auto`, and JSON instructions. The model may call only bounded read tools scoped to the authenticated workspace. Tool arguments are validated, source IDs must be authorized, source payloads and customer references are excluded from model facts, requests have a ten-second timeout, and a failed or invalid response falls back to deterministic findings. Saved traces include tool names, result IDs, model name, elapsed milliseconds, validation status, and token usage when the provider reports it. Secrets are not persisted in traces.

With the variables set, run an inventory mismatch investigation and inspect **Agent Traces**. A real verification requires a saved `model_completion` trace followed by `model_validation: passed` and an evidence-linked `ai_enabled` finding. A stubbed response or deterministic fallback does not satisfy this check.

For the financial contrast, import a payment, an earlier refund, and a settlement whose net amount includes that refund. Before importing the linked bank credit, the finding should identify the missing bank observation as unresolved. After importing a matching credit, reinvestigate the saved timing incident: the records should reconcile exactly and no recovery proposal should be returned.

The Compose service now forwards these variables from the deployment environment. Keep the key in a secret store or untracked `.env` file. A public demo does not need model credentials.
