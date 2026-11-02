---
name: connector-secrets
description: >-
  Configure a FortiSOAR connector without the secret ever entering the chat.
  Use when a connector needs an API key, password or token.
---

# Configuring connectors without seeing secrets

- **Never ask the user for a key.** Ask which environment variable (`FSR_SECRET_<CONNECTOR>__<FIELD>`)
  or file under `FSR_SECRETS_DIR` holds it, and pass that reference.
- Use `setup_connector` with `secrets={"api_key": {"env": "FSR_SECRET_..."}}`. Do not use
  `create_/update_/upsert_connector_configuration` for credentials: they take raw values.
- Run it with `check_only=true` first: it resolves the references and checks field
  names without writing.
- If the connector is missing, say what you will install, then repeat the call with
  `install=true, confirm_install=true`.
- `partial` is not done: the configuration saved but its healthcheck failed. Report the
  healthcheck message; the key never has to be retyped.
- If a key was pasted into the chat anyway, tell the user to rotate it.
